"""OpenAI GPT-4V OCR implementation."""

import base64
import io
import logging
import os
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from doc2mark.core.base import OCRError
from doc2mark.ocr.base import BaseOCR, OCRConfig, OCRProvider, OCRResult, OCRFactory

# LangChain imports for efficient batch processing
try:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.runnables import RunnableLambda
    from langchain_openai import ChatOpenAI

    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

from doc2mark.ocr.prompts import (
    DEFAULT_OCR_PROMPT,
    PROMPTS,
    PromptTemplate,
    build_prompt,
    list_available_prompts
)

logger = logging.getLogger(__name__)

# Supported image formats for OpenAI Vision API
SUPPORTED_IMAGE_FORMATS = {'png', 'jpeg', 'jpg', 'gif', 'webp'}


def detect_image_format(image_data: bytes) -> str:
    """Detect image format from binary data using magic bytes.
    
    Args:
        image_data: Raw image bytes
        
    Returns:
        Image format string (e.g., 'png', 'jpeg', 'gif', 'webp', 'tiff', 'bmp', 'unknown')
    """
    # Check magic bytes
    if image_data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    elif image_data[:2] == b'\xff\xd8':
        return 'jpeg'
    elif image_data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
        return 'webp'
    elif image_data[:4] in (b'II*\x00', b'MM\x00*'):
        return 'tiff'
    elif image_data[:2] == b'BM':
        return 'bmp'
    elif image_data[:4] == b'\x00\x00\x01\x00':
        return 'ico'
    else:
        return 'unknown'


def convert_image_to_supported_format(image_data: bytes) -> Tuple[bytes, str]:
    """Convert image to a format supported by OpenAI Vision API.
    
    If the image is already in a supported format, returns it as-is.
    Otherwise, converts to PNG.
    
    Args:
        image_data: Raw image bytes
        
    Returns:
        Tuple of (converted_image_bytes, mime_type)
    """
    try:
        from PIL import Image
        HAS_PIL = True
    except ImportError:
        HAS_PIL = False
    
    # Detect current format
    current_format = detect_image_format(image_data)
    
    # Map format to MIME type
    format_to_mime = {
        'png': 'image/png',
        'jpeg': 'image/jpeg',
        'jpg': 'image/jpeg',
        'gif': 'image/gif',
        'webp': 'image/webp',
    }
    
    # If already supported, return as-is with correct MIME type
    if current_format in SUPPORTED_IMAGE_FORMATS:
        mime_type = format_to_mime.get(current_format, 'image/png')
        logger.debug(f"Image format '{current_format}' is supported, using MIME type: {mime_type}")
        return image_data, mime_type
    
    # Need to convert - requires PIL
    if not HAS_PIL:
        logger.warning(
            f"Image format '{current_format}' is not supported by OpenAI Vision API. "
            f"PIL/Pillow is required for conversion. Install with: pip install Pillow"
        )
        # Return as PNG anyway (will likely fail at OpenAI)
        return image_data, 'image/png'
    
    # Convert to PNG using PIL
    logger.info(f"Converting image from '{current_format}' to PNG for OpenAI Vision API")
    try:
        img = Image.open(io.BytesIO(image_data))
        
        # Convert to RGB if necessary (for formats like CMYK)
        if img.mode in ('CMYK', 'P', 'LA', 'PA'):
            img = img.convert('RGBA')
        elif img.mode not in ('RGB', 'RGBA', 'L'):
            img = img.convert('RGB')
        
        # Save as PNG
        output = io.BytesIO()
        img.save(output, format='PNG')
        output.seek(0)
        
        converted_data = output.read()
        logger.debug(f"Image converted successfully: {len(image_data)} bytes -> {len(converted_data)} bytes")
        return converted_data, 'image/png'
        
    except Exception as e:
        logger.error(f"Failed to convert image from '{current_format}' to PNG: {e}")
        # Return original data (will likely fail at OpenAI)
        return image_data, 'image/png'


def prepare_prompt(data: Dict[str, str]) -> "ChatPromptTemplate":
    """Prepare prompt for LangChain batch processing."""
    
    if not LANGCHAIN_AVAILABLE:
        raise ImportError("LangChain is required for prepare_prompt function")

    prompt_text = data.get('prompt', DEFAULT_OCR_PROMPT)

    # Log prompt details for debugging
    logger.debug(f"📝 VisionAgent using prompt (length: {len(prompt_text)} chars)")

    # Check if language instruction is included in the prompt
    if "CRITICAL LANGUAGE INSTRUCTION" in prompt_text:
        logger.debug("✅ Language instruction detected in VisionAgent prompt")
        # Extract language info for debugging
        if "You MUST respond ENTIRELY in" in prompt_text:
            # Extract the specific language
            import re
            lang_match = re.search(r"You MUST respond ENTIRELY in ([^\n]*)", prompt_text)
            if lang_match:
                logger.debug(f"🌍 VisionAgent language setting: {lang_match.group(1).strip()}")
        elif "AUTOMATICALLY DETECT the primary language" in prompt_text:
            logger.debug("🌍 VisionAgent language setting: Auto-detection mode")
    else:
        logger.warning("⚠️  No language instruction found in VisionAgent prompt")

    # Show first 200 chars of prompt for verification
    prompt_preview = prompt_text[:200].replace('\n', ' ')
    logger.debug(f"📄 VisionAgent prompt preview: {prompt_preview}...")

    # Get the image data and determine correct MIME type
    image_base64 = data['image_data']
    mime_type = data.get('mime_type', 'image/png')  # Default to png, should be set by caller
    
    return ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=prompt_text),
            HumanMessage(
                content=[
                    # deprecated
                    # {
                    #     "type": "image_url",
                    #     "image_url": {
                    #         "url": f"data:{mime_type};base64,{image_base64}"
                    #     }
                    # }
                    {
                        "type": "image",
                        "base64": image_base64,
                        "mime_type": mime_type,
                    }
                ]
            )
        ]
    )


class VisionAgent:
    """
    LangChain-based vision agent for efficient batch OCR processing.
    
    This replicates the functionality from src/components/agents/ocr_agent.py
    but integrates with doc2mark's OCR system.
    """

    def __init__(
            self,
            api_key: Optional[str] = None,
            model: str = "gpt-4.1",
            temperature: float = 0,
            max_tokens: int = 4096,
            base_url: Optional[str] = None
    ):
        """Initialize the vision agent.
        
        Args:
            api_key: OpenAI API key
            model: Model to use for OCR (default: gpt-4.1)
            temperature: Temperature for response generation
            max_tokens: Maximum tokens in response
            base_url: Optional base URL for OpenAI-compatible API endpoints
        """
        self.api_key = api_key or os.environ.get('OPENAI_API_KEY')
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = base_url or os.environ.get('OPENAI_BASE_URL')

        if not LANGCHAIN_AVAILABLE:
            logger.warning("⚠️  LangChain not available - falling back to basic OpenAI client")
            self._llm = None
            self._chain = None
        else:
            logger.info(f"🤖 Initializing LangChain VisionAgent with {model}")
            if self.base_url:
                logger.info(f"🌐 Using custom base URL: {self.base_url}")
            
            # Prepare kwargs for ChatOpenAI
            llm_kwargs = {
                "model": model,
                "api_key": self.api_key,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            
            # Add base_url if provided
            if self.base_url:
                llm_kwargs["base_url"] = self.base_url
            
            self._llm = ChatOpenAI(**llm_kwargs)
            self._chain = RunnableLambda(prepare_prompt) | self._llm

    def invoke(self, input_dict: Dict[str, str]) -> str:
        """Process single image using LangChain."""
        if not self._chain:
            raise RuntimeError("LangChain not available")

        result = self._chain.invoke(input_dict)
        # Replace triple backticks with single backtick in the output
        processed_content = result.content.replace('```', '`') if result.content else result.content
        return processed_content

    def batch_invoke(self, input_dicts: List[Dict[str, str]]) -> List[str]:
        """
        Process multiple images using LangChain's efficient batch processing.
        
        This uses the same approach as the original ocr_agent.py for optimal performance.
        """
        if not self._chain:
            raise RuntimeError("LangChain not available")

        logger.info(f"🚀 Starting LangChain batch processing of {len(input_dicts)} images")

        # Use LangChain's batch_as_completed for efficient processing
        results = self._chain.batch_as_completed(input_dicts)  # Returns (index, result) tuples
        sorted_results = sorted(results, key=lambda x: x[0])

        logger.info(f"✅ LangChain batch processing complete")

        # Replace triple backticks with single backtick in each output
        return [res[1].content.replace('```', '`') if res[1].content else res[1].content for res in sorted_results]


class OpenAIOCR(BaseOCR):
    """OpenAI GPT-4V based OCR implementation with comprehensive configuration options."""

    def __init__(
            self,
            api_key: Optional[str] = None,
            config: Optional[OCRConfig] = None,
            model: str = "gpt-4.1",
            temperature: float = 0,
            max_tokens: int = 4096,
            max_workers: int = 5,
            default_prompt: Optional[str] = None,
            prompt_template: Optional[Union[str, PromptTemplate]] = None,
            timeout: int = 30,
            max_retries: int = 3,
            base_url: Optional[str] = None,
            **kwargs
    ):
        """Initialize OpenAI OCR provider with comprehensive configuration.
        
        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            config: OCR configuration (from base class)
            model: OpenAI model to use (default: gpt-4.1)
            temperature: Temperature for response generation (0.0-2.0)
            max_tokens: Maximum tokens in response (1-4096)
            max_workers: Maximum concurrent workers for batch processing
            default_prompt: Custom default prompt to use instead of built-in
            prompt_template: Template name from PROMPTS dict ('default', 'table_focused', etc.)
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries for failed requests
            base_url: Optional base URL for OpenAI-compatible API endpoints
            **kwargs: Additional model parameters (passed to OpenAI API)
        """
        # Use provided API key or fall back to environment variable
        api_key = api_key or os.environ.get('OPENAI_API_KEY')
        super().__init__(api_key, config)

        # Model configuration
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_url = base_url or os.environ.get('OPENAI_BASE_URL')
        self.model_kwargs = kwargs

        self.config = config or OCRConfig()

        # Batch processing configuration
        self.max_workers = max_workers

        # Prompt configuration
        self.prompt_template = prompt_template or PromptTemplate.DEFAULT

        # Convert string to enum if needed
        if isinstance(self.prompt_template, str):
            try:
                self.prompt_template = PromptTemplate(self.prompt_template)
            except ValueError:
                available = [template.value for template in PromptTemplate]
                raise ValueError(f"Unknown prompt template: {self.prompt_template}. Available: {available}")

        if default_prompt:
            self.default_prompt = default_prompt
        elif self.prompt_template in PROMPTS:
            self.default_prompt = PROMPTS[self.prompt_template]
        else:
            self.default_prompt = DEFAULT_OCR_PROMPT

        # Initialize clients
        self._client = None
        self._vision_agent = None

        logger.info(f"🤖 Initializing OpenAI OCR with comprehensive configuration:")
        logger.info(f"   - Model: {self.model}")
        logger.info(f"   - Temperature: {self.temperature}")
        logger.info(f"   - Max tokens: {self.max_tokens}")
        logger.info(f"   - Max workers: {self.max_workers}")
        logger.info(f"   - Prompt template: {self.prompt_template.value}")
        logger.info(f"   - LangChain enabled: True (required)")

        if not api_key:
            logger.warning("⚠️  No OpenAI API key provided - OCR will fail unless key is set later")
        else:
            logger.debug(f"✓ OpenAI API key configured (length: {len(api_key)} chars)")

        # Initialize VisionAgent for efficient batch processing
        if not LANGCHAIN_AVAILABLE:
            logger.error("❌ LangChain is required but not available")
            raise ImportError(
                "LangChain is required for OpenAI OCR. "
                "Install it with: pip install langchain langchain-openai"
            )

        logger.info("🔗 Initializing LangChain VisionAgent for batch processing")
        if self.base_url:
            logger.info(f"🌐 Using custom base URL: {self.base_url}")
        try:
            self._vision_agent = VisionAgent(
                api_key=api_key,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                base_url=self.base_url
            )
        except Exception as e:
            logger.error(f"❌ Failed to initialize VisionAgent: {e}")
            raise RuntimeError(f"Failed to initialize LangChain VisionAgent: {str(e)}")

    @property
    def client(self):
        """Lazy load OpenAI client."""
        if self._client is None:
            logger.debug("🔌 Loading OpenAI client...")
            # OpenAI client is no longer needed as we use LangChain
            logger.warning("⚠️  Direct OpenAI client is deprecated - use LangChain instead")
            self._client = None
        return self._client

    def validate_api_key(self) -> bool:
        """Validate OpenAI API key."""
        if not self.api_key:
            logger.warning("⚠️  No API key to validate")
            return False

        logger.info("🔐 Validating OpenAI API key...")
        # We validate through LangChain instead
        if self._vision_agent:
            logger.info("✓ API key configured for LangChain")
            return True
        else:
            logger.error("❌ VisionAgent not initialized")
            return False

    def get_available_prompts(self) -> Dict[str, str]:
        """Get available prompt templates.
        
        Returns:
            Dictionary of prompt template names and descriptions
        """
        return list_available_prompts()

    def update_prompt_template(self, template_name: Union[str, PromptTemplate]):
        """Update the prompt template.
        
        Args:
            template_name: Name of the prompt template to use (string or PromptTemplate enum)
            
        Raises:
            ValueError: If template name is not available
        """
        # Convert string to enum if needed
        if isinstance(template_name, str):
            try:
                template_name = PromptTemplate(template_name)
            except ValueError:
                available = [template.value for template in PromptTemplate]
                raise ValueError(f"Unknown prompt template: {template_name}. Available: {available}")

        if template_name not in PROMPTS:
            available = [template.value for template in PromptTemplate]
            raise ValueError(f"Unknown prompt template: {template_name}. Available: {available}")

        self.prompt_template = template_name
        self.default_prompt = PROMPTS[template_name]
        logger.info(f"📝 Updated prompt template to: {template_name.value}")

    def update_model_config(
            self,
            model: Optional[str] = None,
            temperature: Optional[float] = None,
            max_tokens: Optional[int] = None,
            **kwargs
    ):
        """Update model configuration.
        
        Args:
            model: New model name
            temperature: New temperature value
            max_tokens: New max tokens value
            **kwargs: Additional model parameters
        """
        if model is not None:
            self.model = model
            logger.info(f"🤖 Updated model to: {model}")

        if temperature is not None:
            self.temperature = temperature
            logger.info(f"🌡️ Updated temperature to: {temperature}")

        if max_tokens is not None:
            self.max_tokens = max_tokens
            logger.info(f"📊 Updated max_tokens to: {max_tokens}")

        if kwargs:
            self.model_kwargs.update(kwargs)
            logger.info(f"⚙️ Updated model kwargs: {list(kwargs.keys())}")

        # Reinitialize vision agent
        logger.info("🔄 Reinitializing VisionAgent with new configuration...")
        if self.base_url:
            logger.info(f"🌐 Using custom base URL: {self.base_url}")
        try:
            self._vision_agent = VisionAgent(
                api_key=self.api_key,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                base_url=self.base_url
            )
        except Exception as e:
            logger.error(f"❌ Failed to reinitialize VisionAgent: {e}")
            raise RuntimeError(f"Failed to reinitialize LangChain VisionAgent: {str(e)}")

    def _save_image_locally(self, image_data: bytes, **kwargs) -> OCRResult:
        """Save image locally and return file:// URL.
        
        Args:
            image_data: Image data as bytes
            **kwargs: Additional options
            
        Returns:
            OCRResult with local file path
        """
        image_size = len(image_data)
        logger.info(f"💾 Saving image locally ({image_size} bytes)")

        try:
            # Get image directory
            image_dir_path = kwargs.get('local_image_dir', './images')
            image_dir = Path(image_dir_path)
            logger.debug(f"📁 Image directory: {image_dir}")

            # Create directory if it doesn't exist
            image_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(f"✓ Directory created/verified: {image_dir}")

            # Generate unique filename
            image_id = str(uuid.uuid4())
            image_path = image_dir / f"{image_id}.png"
            logger.debug(f"📸 Generated filename: {image_path.name}")

            # Save image
            logger.debug(f"💾 Writing image to: {image_path}")
            with open(image_path, 'wb') as f:
                f.write(image_data)

            # Verify file was written
            saved_size = image_path.stat().st_size
            if saved_size != image_size:
                logger.warning(f"⚠️  Size mismatch: original {image_size} vs saved {saved_size}")
            else:
                logger.debug(f"✓ Image saved successfully ({saved_size} bytes)")

            # Return file:// URL
            file_url = f"file://{image_path.absolute()}"
            logger.info(f"✅ Image saved locally: {file_url}")

            return OCRResult(
                text=f"![Image]({file_url})",
                confidence=1.0,
                metadata={
                    "local_file": str(image_path),
                    "file_url": file_url,
                    "saved_locally": True,
                    "image_size_bytes": image_size,
                    "saved_size_bytes": saved_size
                }
            )

        except Exception as e:
            logger.error(f"❌ Failed to save image locally: {e}")
            logger.error(f"   Target directory: {kwargs.get('local_image_dir', './images')}")
            logger.error(f"   Image size: {image_size} bytes")
            raise OCRError(f"Failed to save image locally: {str(e)}")

    def _build_prompt(self, **kwargs) -> str:
        """Build prompt for GPT-4V based on configuration and kwargs.
        
        Args:
            **kwargs: Additional options:
                - instructions: str - Custom instructions to override default
                - prompt_template: str - Prompt template to use for this request
                - language: str - Specify expected language (overrides config.language)
                - content_type: str - Hint about content type
            
        Returns:
            Prompt string
        """
        # Extract parameters for the build_prompt function
        template_name = kwargs.get('prompt_template', self.prompt_template)
        # Use language from kwargs, or fall back to config.language if available
        language = kwargs.get('language') or (self.config.language if self.config else None)
        content_type = kwargs.get('content_type')
        custom_instructions = kwargs.get('instructions')

        # Use the centralized build_prompt function
        prompt = build_prompt(
            template_name=template_name,
            language=language,
            content_type=content_type,
            custom_instructions=custom_instructions
        )

        # Log what we're using
        if custom_instructions:
            logger.debug("Using custom instructions for OCR prompt")
        else:
            template_display = template_name.value if isinstance(template_name, PromptTemplate) else template_name
            logger.debug(f"Using prompt template: {template_display}")
            if language:
                # Determine if language came from kwargs or config
                if 'language' in kwargs:
                    logger.debug(f"Added language instruction: Output in {language} (from request)")
                else:
                    logger.debug(f"Added language instruction: Output in {language} (from OCRConfig)")
            else:
                logger.debug("Added auto-detection: Output in same language as image content")
            if content_type:
                logger.debug(f"Added content type hint: {content_type}")

        return prompt

    def batch_process_images(
            self,
            images: List[bytes],
            max_workers: Optional[int] = None,
            **kwargs
    ) -> List[OCRResult]:
        """
        Process multiple images using LangChain for optimal performance.
        
        Args:
            images: List of image data
            max_workers: Not used - kept for compatibility
            **kwargs: Additional options
            
        Returns:
            List of OCR results in the same order as input
        """
        total_images = len(images)

        logger.info(f"🚀 Starting batch OCR processing of {total_images} images")
        logger.info(f"⚙️ Configuration: model={self.model}, langchain=True")

        if total_images == 0:
            return []

        # Check if we should save locally instead of OCR
        if kwargs.get('save_locally', False):
            logger.info("💾 Saving images locally instead of performing OCR")
            return self._batch_save_images_locally(images, **kwargs)

        # Always use VisionAgent with LangChain
        if not self._vision_agent:
            logger.error("❌ VisionAgent not initialized - cannot process images")
            raise RuntimeError("LangChain VisionAgent is required but not initialized")

        logger.info("🔗 Using LangChain VisionAgent for batch processing")
        return self._batch_process_with_vision_agent(images, **kwargs)

    def _batch_process_with_vision_agent(self, images: List[bytes], **kwargs) -> List[OCRResult]:
        """Process images using LangChain VisionAgent for optimal performance."""
        try:
            # Build prompt for batch processing
            prompt = self._build_prompt(**kwargs)

            # Prepare input data for VisionAgent
            input_dicts = []
            for i, image_data in enumerate(images):
                # Convert image to supported format if needed
                converted_data, mime_type = convert_image_to_supported_format(image_data)
                base64_image = base64.b64encode(converted_data).decode('utf-8')
                input_dicts.append({
                    'image_data': base64_image,
                    'mime_type': mime_type,
                    'prompt': prompt,
                    'index': i
                })

            # Use VisionAgent batch processing (same as original ocr_agent.py)
            logger.info(f"🚀 Processing {len(input_dicts)} images with VisionAgent")
            batch_results = self._vision_agent.batch_invoke(input_dicts)

            # Convert to OCRResult objects
            results = []
            for i, text_result in enumerate(batch_results):
                image_size = len(images[i])
                results.append(OCRResult(
                    text=text_result,
                    confidence=1.0,
                    language=kwargs.get('language') or (self.config.language if self.config else None),
                    metadata={
                        "model": self.model,
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                        "using_langchain": True,
                        "prompt_template": self.prompt_template.value,
                        "using_custom_instructions": 'instructions' in kwargs,
                        "image_size_bytes": image_size,
                        "batch_index": i,
                        "content_type": kwargs.get('content_type'),
                        "model_kwargs": self.model_kwargs
                    }
                ))

            successful = len([r for r in results if r.text])
            logger.info(f"✅ VisionAgent batch complete: {successful}/{len(images)} successful")

            return results

        except Exception as e:
            logger.error(f"❌ VisionAgent batch processing failed: {e}")
            raise OCRError(f"Failed to process images with LangChain: {str(e)}")

    def _batch_save_images_locally(self, images: List[bytes], **kwargs) -> List[OCRResult]:
        """Batch save images locally."""
        results = []
        for i, image_data in enumerate(images):
            try:
                result = self._save_image_locally(image_data, **kwargs)
                results.append(result)
            except Exception as e:
                logger.error(f"❌ Failed to save image {i + 1}: {e}")
                results.append(OCRResult(
                    text="",
                    metadata={"error": str(e), "image_index": i, "failed": True}
                ))

        successful = sum(1 for r in results if not r.metadata.get('failed'))
        logger.info(f"✅ Batch save complete: {successful}/{len(images)} successful")

        return results

    def get_configuration_summary(self) -> Dict[str, any]:
        """Get current configuration summary.
        
        Returns:
            Dictionary with current configuration
        """
        config = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "max_workers": self.max_workers,
            "prompt_template": self.prompt_template.value,
            "langchain_enabled": True,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "model_kwargs": self.model_kwargs,
            "api_key_configured": bool(self.api_key),
            "langchain_available": LANGCHAIN_AVAILABLE,
            "vision_agent_ready": bool(self._vision_agent)
        }
        
        # Add base_url if configured
        if self.base_url:
            config["base_url"] = self.base_url
            
        return config

    @property
    def requires_api_key(self) -> bool:
        """OpenAI requires an API key."""
        return True


# Register the provider
logger.debug("🔌 Registering OpenAI OCR provider")
OCRFactory.register_provider(OCRProvider.OPENAI, OpenAIOCR)
