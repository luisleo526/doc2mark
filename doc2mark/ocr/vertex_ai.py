"""Google Gemini OCR implementation using langchain-google-genai."""

import base64
import logging
import os
from typing import Any, Dict, List, Optional, Tuple, Union

from doc2mark.core.base import OCRError
from doc2mark.ocr.base import BaseOCR, OCRConfig, OCRProvider, OCRResult, OCRFactory
from doc2mark.utils.image_utils import (
    detect_image_format as _shared_detect_image_format,
    convert_image_to_supported_format as _shared_convert_image_to_supported_format,
)

try:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.runnables import RunnableLambda
    from langchain_google_genai import ChatGoogleGenerativeAI

    LANGCHAIN_GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    LANGCHAIN_GOOGLE_GENAI_AVAILABLE = False

from doc2mark.ocr.prompts import (
    DEFAULT_OCR_PROMPT,
    PROMPTS,
    PromptTemplate,
    build_prompt,
    list_available_prompts,
)

logger = logging.getLogger(__name__)

# Gemini supports these image formats natively
SUPPORTED_IMAGE_FORMATS = {"png", "jpeg", "jpg", "gif", "webp"}


def _prepare_prompt(data: Dict[str, str]) -> "ChatPromptTemplate":
    """Prepare prompt for LangChain batch processing with Gemini."""

    if not LANGCHAIN_GOOGLE_GENAI_AVAILABLE:
        raise ImportError("langchain-google-genai is required for _prepare_prompt")

    prompt_text = data.get("prompt", DEFAULT_OCR_PROMPT)
    image_base64 = data["image_data"]
    mime_type = data.get("mime_type", "image/png")

    return ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=prompt_text),
            HumanMessage(
                content=[
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_base64}"
                        },
                    }
                ]
            ),
        ]
    )


class VertexAIVisionAgent:
    """LangChain-based vision agent for Google Gemini batch OCR processing."""

    def __init__(
        self,
        project: Optional[str] = None,
        location: str = "global",
        model: str = "gemini-3.1-flash-lite-preview",
        temperature: float = 0,
        max_tokens: int = 4096,
    ):
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

        if not LANGCHAIN_GOOGLE_GENAI_AVAILABLE:
            logger.warning("langchain-google-genai not available")
            self._llm = None
            self._chain = None
        else:
            logger.info(f"Initializing Google Gemini VisionAgent with {model}")

            llm_kwargs = {
                "model": model,
                "temperature": temperature,
                "max_output_tokens": max_tokens,
                "vertexai": True,
                "location": location,
            }
            if self.project:
                llm_kwargs["project"] = self.project

            self._llm = ChatGoogleGenerativeAI(**llm_kwargs)
            self._chain = RunnableLambda(_prepare_prompt) | self._llm

    @staticmethod
    def _extract_text(content) -> str:
        """Extract text from LangChain message content (may be str or list of parts)."""
        if not content:
            return ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                part if isinstance(part, str) else part.get("text", "")
                for part in content
            )
        else:
            text = str(content)
        return text.replace("```", "`")

    @staticmethod
    def _extract_usage(msg) -> Dict[str, Any]:
        """Extract token usage metadata from a LangChain AIMessage."""
        usage = getattr(msg, 'usage_metadata', None)
        return dict(usage) if usage else {}

    def invoke(self, input_dict: Dict[str, str]) -> Tuple[str, Dict[str, Any]]:
        """Process single image.

        Returns:
            Tuple of (processed text, token usage dict)
        """
        if not self._chain:
            raise RuntimeError("langchain-google-genai not available")

        result = self._chain.invoke(input_dict)
        return self._extract_text(result.content), self._extract_usage(result)

    def batch_invoke(self, input_dicts: List[Dict[str, str]]) -> List[Tuple[str, Dict[str, Any]]]:
        """Process multiple images using LangChain batch processing.

        Returns:
            List of (processed text, token usage dict) tuples
        """
        if not self._chain:
            raise RuntimeError("langchain-google-genai not available")

        logger.info(f"Starting Gemini batch processing of {len(input_dicts)} images")

        results = self._chain.batch_as_completed(input_dicts)
        sorted_results = sorted(results, key=lambda x: x[0])

        logger.info("Gemini batch processing complete")

        return [(self._extract_text(res[1].content), self._extract_usage(res[1])) for res in sorted_results]


class VertexAIOCR(BaseOCR):
    """Google Gemini based OCR implementation via langchain-google-genai."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        config: Optional[OCRConfig] = None,
        project: Optional[str] = None,
        location: str = "global",
        model: str = "gemini-3.1-flash-lite-preview",
        temperature: float = 0,
        max_tokens: int = 4096,
        default_prompt: Optional[str] = None,
        prompt_template: Optional[Union[str, PromptTemplate]] = None,
        **kwargs,
    ):
        """Initialize Vertex AI OCR provider.

        Args:
            api_key: Google API key (optional, defaults to ADC via GOOGLE_APPLICATION_CREDENTIALS)
            config: OCR configuration
            project: Google Cloud project ID (defaults to GOOGLE_CLOUD_PROJECT env var)
            location: Google Cloud region (default: global)
            model: Gemini model name (default: gemini-3.1-flash-lite-preview)
            temperature: Temperature for response generation (0.0-2.0)
            max_tokens: Maximum tokens in response
            default_prompt: Custom default prompt to use
            prompt_template: Template name from PROMPTS dict
            **kwargs: Additional model parameters
        """
        super().__init__(api_key, config)

        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.model_kwargs = kwargs

        self.config = config or OCRConfig()

        # Prompt configuration
        self.prompt_template = prompt_template or PromptTemplate.DEFAULT
        if isinstance(self.prompt_template, str):
            try:
                self.prompt_template = PromptTemplate(self.prompt_template)
            except ValueError:
                available = [template.value for template in PromptTemplate]
                raise ValueError(
                    f"Unknown prompt template: {self.prompt_template}. Available: {available}"
                )

        if default_prompt:
            self.default_prompt = default_prompt
        elif self.prompt_template in PROMPTS:
            self.default_prompt = PROMPTS[self.prompt_template]
        else:
            self.default_prompt = DEFAULT_OCR_PROMPT

        # Initialize vision agent
        self._vision_agent = None

        logger.info(f"Initializing Vertex AI OCR:")
        logger.info(f"   - Model: {self.model}")
        logger.info(f"   - Project: {self.project}")
        logger.info(f"   - Location: {self.location}")
        logger.info(f"   - Prompt template: {self.prompt_template.value}")

        if not LANGCHAIN_GOOGLE_GENAI_AVAILABLE:
            raise ImportError(
                "langchain-google-genai is required for Vertex AI OCR. "
                "Install it with: pip install langchain-google-genai"
            )

        try:
            self._vision_agent = VertexAIVisionAgent(
                project=self.project,
                location=self.location,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as e:
            logger.error(f"Failed to initialize Vertex AI VisionAgent: {e}")
            raise RuntimeError(f"Failed to initialize Vertex AI VisionAgent: {str(e)}")

    def validate_api_key(self) -> bool:
        """Vertex AI uses Google Cloud ADC, not an API key."""
        if self._vision_agent:
            logger.info("Vertex AI configured via Application Default Credentials")
            return True
        return False

    def get_available_prompts(self) -> Dict[str, str]:
        """Get available prompt templates."""
        return list_available_prompts()

    def update_prompt_template(self, template_name: Union[str, PromptTemplate]):
        """Update the prompt template."""
        if isinstance(template_name, str):
            try:
                template_name = PromptTemplate(template_name)
            except ValueError:
                available = [template.value for template in PromptTemplate]
                raise ValueError(
                    f"Unknown prompt template: {template_name}. Available: {available}"
                )

        if template_name not in PROMPTS:
            available = [template.value for template in PromptTemplate]
            raise ValueError(
                f"Unknown prompt template: {template_name}. Available: {available}"
            )

        self.prompt_template = template_name
        self.default_prompt = PROMPTS[template_name]
        logger.info(f"Updated prompt template to: {template_name.value}")

    def _build_prompt(self, **kwargs) -> str:
        """Build prompt based on configuration and kwargs."""
        template_name = kwargs.get("prompt_template", self.prompt_template)
        language = kwargs.get("language") or (self.config.language if self.config else None)
        content_type = kwargs.get("content_type")
        custom_instructions = kwargs.get("instructions")

        return build_prompt(
            template_name=template_name,
            language=language,
            content_type=content_type,
            custom_instructions=custom_instructions,
        )

    def batch_process_images(
        self,
        images: List[bytes],
        max_workers: Optional[int] = None,
        **kwargs,
    ) -> List[OCRResult]:
        """Process multiple images using Gemini via LangChain.

        Args:
            images: List of image data
            max_workers: Not used - kept for compatibility
            **kwargs: Additional options

        Returns:
            List of OCR results in the same order as input
        """
        total_images = len(images)
        logger.info(f"Starting Vertex AI batch OCR processing of {total_images} images")

        if total_images == 0:
            return []

        if not self._vision_agent:
            raise RuntimeError("Vertex AI VisionAgent is required but not initialized")

        return self._batch_process_with_vision_agent(images, **kwargs)

    def _batch_process_with_vision_agent(
        self, images: List[bytes], **kwargs
    ) -> List[OCRResult]:
        """Process images using Gemini VisionAgent."""
        try:
            prompt = self._build_prompt(**kwargs)

            input_dicts = []
            for i, image_data in enumerate(images):
                converted_data, mime_type = _shared_convert_image_to_supported_format(
                    image_data, supported_formats=SUPPORTED_IMAGE_FORMATS
                )
                base64_image = base64.b64encode(converted_data).decode("utf-8")
                input_dicts.append(
                    {
                        "image_data": base64_image,
                        "mime_type": mime_type,
                        "prompt": prompt,
                        "index": i,
                    }
                )

            logger.info(f"Processing {len(input_dicts)} images with Gemini VisionAgent")
            batch_results = self._vision_agent.batch_invoke(input_dicts)

            results = []
            for i, (text_result, token_usage) in enumerate(batch_results):
                image_size = len(images[i])
                results.append(
                    OCRResult(
                        text=text_result,
                        confidence=1.0,
                        language=kwargs.get("language")
                        or (self.config.language if self.config else None),
                        metadata={
                            "model": self.model,
                            "provider": "vertex_ai",
                            "project": self.project,
                            "location": self.location,
                            "temperature": self.temperature,
                            "max_tokens": self.max_tokens,
                            "prompt_template": self.prompt_template.value,
                            "using_custom_instructions": "instructions" in kwargs,
                            "image_size_bytes": image_size,
                            "batch_index": i,
                            "content_type": kwargs.get("content_type"),
                            "token_usage": token_usage,
                        },
                    )
                )

            successful = len([r for r in results if r.text])
            logger.info(
                f"Vertex AI batch complete: {successful}/{len(images)} successful"
            )

            return results

        except Exception as e:
            logger.error(f"Vertex AI batch processing failed: {e}")
            raise OCRError(f"Failed to process images with Vertex AI: {str(e)}")

    def get_configuration_summary(self) -> Dict:
        """Get current configuration summary."""
        return {
            "provider": "vertex_ai",
            "model": self.model,
            "project": self.project,
            "location": self.location,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "prompt_template": self.prompt_template.value,
            "langchain_google_genai_available": LANGCHAIN_GOOGLE_GENAI_AVAILABLE,
            "vision_agent_ready": bool(self._vision_agent),
        }

    @property
    def requires_api_key(self) -> bool:
        """Vertex AI uses ADC, not an API key."""
        return False


# Register the provider
logger.debug("Registering Vertex AI OCR provider")
OCRFactory.register_provider(OCRProvider.VERTEX_AI, VertexAIOCR)
