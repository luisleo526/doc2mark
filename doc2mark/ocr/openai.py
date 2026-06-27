"""OpenAI GPT-4V OCR implementation."""

import base64
import io
import logging
import os
import uuid
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type, Union

from doc2mark.core.base import OCRError
from doc2mark.ocr.base import (
    BaseOCR,
    OCRConfig,
    OCRProvider,
    OCRResult,
    OCRFactory,
    Task,
    TASK_PROMPTS,
    resolve_max_concurrency,
    _CONTEXT_PDF_INSTRUCTION,
    _ROUTER_CONFIDENCE_CLAUSE,
    _SYNTHESIS_MARKDOWN_INSTRUCTION,
)
from doc2mark.ocr.schema import OCRPage, RawExtraction

try:
    from pydantic import BaseModel
except Exception:  # pragma: no cover - pydantic ships with the schema models
    BaseModel = object  # type: ignore
from doc2mark.utils.image_utils import (
    detect_image_format as _shared_detect_image_format,
    convert_image_to_supported_format as _shared_convert_image_to_supported_format,
)

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
    add_language_instruction,
    build_prompt,
    list_available_prompts
)

# Sentinel for "constructor param not explicitly provided" so we can apply the
# precedence: explicit param > OCRConfig field > hard-coded default.
_UNSET = object()

# Appended to the per-image prompt when detail="raw": ask the model to fill only
# the verbatim raw.* fields and skip the (token-heavy) interpretation subtree.
_RAW_DETAIL_INSTRUCTION = (
    "\n\nRAW MODE: populate only the raw.* fields with the verbatim transcription. "
    "Leave the interpretation fields empty/null — do not analyze or summarize."
)

logger = logging.getLogger(__name__)

# Supported image formats for OpenAI Vision API
SUPPORTED_IMAGE_FORMATS = {'png', 'jpeg', 'jpg', 'gif', 'webp'}

# Model families that can accept a `file` (application/pdf) content part. Used to
# gate the neighbor-page PDF context attachment so non-PDF models never 400.
_PDF_CAPABLE_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-5", "o1")


def _model_supports_pdf(model: str) -> bool:
    """Whether ``model`` can accept a PDF (file) content part."""
    m = (model or "").lower()
    return any(m.startswith(p) for p in _PDF_CAPABLE_PREFIXES)


def detect_image_format(image_data: bytes) -> str:
    """Detect image format from binary data using magic bytes.

    Delegates to the shared utility in doc2mark.utils.image_utils.
    Kept here for backward compatibility.
    """
    return _shared_detect_image_format(image_data)


def convert_image_to_supported_format(image_data: bytes) -> Tuple[bytes, str]:
    """Convert image to a format supported by OpenAI Vision API.

    Delegates to the shared utility in doc2mark.utils.image_utils.
    Kept here for backward compatibility.
    """
    return _shared_convert_image_to_supported_format(
        image_data, supported_formats=SUPPORTED_IMAGE_FORMATS,
    )


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

    # Optional neighbor-page PDF context (raw base64, no data-uri prefix; or None).
    # Attached as a context-only `file` part ONLY when both a context PDF is present
    # AND the model is PDF-capable (`context_pdf_enabled`). The image remains the sole
    # transcription target — the PDF anchors terminology/language continuity.
    context_pdf = data.get('context_pdf')

    content = [
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
    if context_pdf and data.get('context_pdf_enabled'):
        content.append({
            "type": "text",
            "text": _CONTEXT_PDF_INSTRUCTION + "\n\n" + _ROUTER_CONFIDENCE_CLAUSE,
        })
        content.append({
            "type": "file",
            "file": {
                "filename": "context.pdf",
                "file_data": f"data:application/pdf;base64,{context_pdf}",  # VERIFIED OpenAI format
            },
        })

    return ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=prompt_text),
            HumanMessage(content=content),
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
            model: str = "gpt-5.4-mini",
            temperature: float = 0,
            max_tokens: int = 8192,
            base_url: Optional[str] = None,
            max_concurrency: Optional[int] = None,
            timeout: Optional[int] = None,
            max_retries: Optional[int] = None,
            structured: bool = False,
            response_model: Optional[Type[BaseModel]] = None,
            detail: str = "full",
    ):
        """Initialize the vision agent.

        Args:
            api_key: OpenAI API key
            model: Model to use for OCR (default: gpt-5.4-mini)
            temperature: Temperature for response generation
            max_tokens: Maximum tokens in response
            base_url: Optional base URL for OpenAI-compatible API endpoints
            timeout: Request timeout in seconds (forwarded to ChatOpenAI)
            max_retries: Maximum number of retries for failed requests
            structured: When True, attach ``with_structured_output`` so the chain
                returns ``{"raw", "parsed", "parsing_error"}`` dicts. When False
                (default) the legacy free-form chain is used and the chain returns
                plain ``AIMessage`` objects.
            response_model: BYO pydantic schema for structured output; defaults to
                :class:`~doc2mark.ocr.schema.OCRPage` when ``None``.
            detail: ``"full"`` or ``"raw"`` — recorded for callers; the prompt-side
                instruction is added by the provider, not here.
        """
        self.api_key = api_key or os.environ.get('OPENAI_API_KEY')
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = base_url or os.environ.get('OPENAI_BASE_URL')
        self.max_concurrency = max_concurrency
        self.timeout = timeout
        self.max_retries = max_retries
        self.structured = structured
        self.response_model = response_model
        self.detail = detail

        # Neighbor-page PDF context: enabled when the model can ingest PDF. The
        # nested {"type":"file","file":{"filename","file_data":"data:...;base64"}}
        # block was spike-verified against gpt-5.4-mini.
        self._context_pdf_enabled = _model_supports_pdf(model)

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
                "max_tokens": max_tokens,
            }

            # Forward resilience knobs when explicitly set
            if self.timeout is not None:
                llm_kwargs["timeout"] = self.timeout
            if self.max_retries is not None:
                llm_kwargs["max_retries"] = self.max_retries

            # Add base_url if provided
            if self.base_url:
                llm_kwargs["base_url"] = self.base_url

            self._llm = ChatOpenAI(**llm_kwargs)
            # prepare_prompt (image input) is identical for both paths — only the
            # final stage of the chain changes for structured output.
            if self.structured:
                schema = self.response_model or OCRPage
                logger.info(f"🧱 Structured output enabled (schema: {schema.__name__})")
                structured_llm = self._llm.with_structured_output(
                    schema, method="json_schema", include_raw=True,
                )
                self._chain = RunnableLambda(prepare_prompt) | structured_llm
            else:
                self._chain = RunnableLambda(prepare_prompt) | self._llm

    @staticmethod
    def _extract_usage(msg) -> Dict[str, Any]:
        """Extract token usage metadata from a LangChain AIMessage."""
        usage = getattr(msg, 'usage_metadata', None)
        return dict(usage) if usage else {}

    def invoke(self, input_dict: Dict[str, str]) -> Tuple[str, Dict[str, Any]]:
        """Process single image using LangChain.

        Returns:
            Tuple of (processed text, token usage dict)
        """
        if not self._chain:
            raise RuntimeError("LangChain not available")

        result = self._chain.invoke(input_dict)
        processed_content = result.content.replace('```', '`') if result.content else result.content
        return processed_content, self._extract_usage(result)

    def batch_invoke(self, input_dicts: List[Dict[str, str]]) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Process multiple images using LangChain's efficient batch processing.

        Returns:
            List of (processed text, token usage dict) tuples
        """
        if not self._chain:
            raise RuntimeError("LangChain not available")

        logger.info(
            f"🚀 Starting LangChain batch processing of {len(input_dicts)} images "
            f"(max_concurrency={self.max_concurrency or 'default'})"
        )

        _cfg = {"max_concurrency": self.max_concurrency} if self.max_concurrency else None
        # return_exceptions=True isolates a single image's failure (e.g. a dense page
        # truncated at max_tokens) so it does NOT abort the whole batch.
        results = self._chain.batch_as_completed(input_dicts, config=_cfg, return_exceptions=True)
        sorted_results = sorted(results, key=lambda x: x[0])

        logger.info(f"✅ LangChain batch processing complete")

        if getattr(self, "structured", False):
            # Structured path: each element value is a dict
            # {"raw": AIMessage, "parsed": OCRPage|None, "parsing_error": ...}
            # (LangChain include_raw=True). Re-shape into a stable payload and
            # carry token usage extracted from the raw AIMessage.
            structured_output: List[Dict[str, Any]] = []
            for _idx, payload in sorted_results:
                if isinstance(payload, Exception):
                    structured_output.append({"parsed": None, "parsing_error": str(payload), "raw": None, "usage": {}})
                    continue
                raw_msg = payload.get("raw") if isinstance(payload, dict) else None
                structured_output.append({
                    "parsed": payload.get("parsed") if isinstance(payload, dict) else None,
                    "parsing_error": payload.get("parsing_error") if isinstance(payload, dict) else None,
                    "raw": raw_msg,
                    "usage": self._extract_usage(raw_msg) if raw_msg is not None else {},
                })
            return structured_output

        # Legacy free-form path: each element value is an AIMessage with .content.
        output = []
        for res in sorted_results:
            msg = res[1]
            if isinstance(msg, Exception):
                output.append(("", {}))
                continue
            text = msg.content.replace('```', '`') if msg.content else msg.content
            output.append((text, self._extract_usage(msg)))
        return output


class OpenAIOCR(BaseOCR):
    """OpenAI GPT-4V based OCR implementation with comprehensive configuration options."""

    def __init__(
            self,
            api_key: Optional[str] = None,
            config: Optional[OCRConfig] = None,
            model: Any = _UNSET,
            temperature: Any = _UNSET,
            max_tokens: Any = _UNSET,
            max_workers: int = 5,
            default_prompt: Optional[str] = None,
            prompt_template: Optional[Union[str, PromptTemplate]] = None,
            timeout: int = 30,
            max_retries: int = 3,
            base_url: Any = _UNSET,
            **kwargs
    ):
        """Initialize OpenAI OCR provider with comprehensive configuration.
        
        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            config: OCR configuration (from base class)
            model: OpenAI model to use (default: gpt-5.4-mini)
            temperature: Temperature for response generation (0.0-2.0)
            max_tokens: Maximum tokens in response (1-8192)
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

        cfg = config or OCRConfig()
        self.config = cfg

        # One-time deprecation notice for inert/no-op config fields. They are read
        # only by Tesseract (or by nobody) and do nothing for the LLM provider.
        deprecated = cfg.deprecated_llm_overrides()
        if deprecated:
            warnings.warn(
                "These OCRConfig fields are inert for the OpenAI provider and will "
                f"be removed in a future release: {', '.join(deprecated)}. "
                "Use the live knobs (model/task/language/structured/detail/...) instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        # Resolve model knobs with precedence: explicit param > config > default.
        def _resolve(param: Any, cfg_value: Any, default: Any) -> Any:
            if param is not _UNSET:
                return param
            if cfg_value is not None:
                return cfg_value
            return default

        # Model configuration
        self.model = _resolve(model, cfg.model, "gpt-5.4-mini")
        self.temperature = _resolve(temperature, cfg.temperature, 0)
        self.max_tokens = _resolve(max_tokens, cfg.max_tokens, 8192)
        self.timeout = timeout
        self.max_retries = max_retries
        resolved_base_url = base_url if base_url is not _UNSET else cfg.base_url
        self.base_url = resolved_base_url or os.environ.get('OPENAI_BASE_URL')
        self.model_kwargs = kwargs

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

        self._vision_agent = None

        logger.info(f"🤖 Initializing OpenAI OCR with comprehensive configuration:")
        logger.info(f"   - Model: {self.model}")
        logger.info(f"   - Temperature: {self.temperature}")
        logger.info(f"   - Max tokens: {self.max_tokens}")
        logger.info(f"   - Max workers: {self.max_workers}")
        logger.info(f"   - Prompt template: {self.prompt_template.value}")
        logger.info(f"   - LangChain enabled: True (required)")

        if not api_key:
            logger.debug("No OpenAI API key configured; OCR calls will fail unless a key is set later")
        else:
            logger.debug("OpenAI API key configured")

        # Initialize VisionAgent lazily. This keeps text-only processing usable
        # without OCR extras or OPENAI_API_KEY.
        logger.info("🔗 LangChain VisionAgent will initialize on first OCR request")

    def validate_api_key(self) -> bool:
        """Validate OpenAI API key."""
        if not self.api_key:
            logger.warning("⚠️  No API key to validate")
            return False

        logger.info("🔐 Validating OpenAI API key...")
        return True

    def _ensure_vision_agent(
            self,
            structured: bool = False,
            response_model: Optional[Type[BaseModel]] = None,
            detail: str = "full",
    ) -> VisionAgent:
        """Initialize the LangChain vision agent only when OCR is requested.

        The chain's final stage depends on ``structured``, so when a request
        toggles structured output relative to the cached agent we rebuild it.
        """
        if self._vision_agent is not None and getattr(self._vision_agent, "structured", False) == structured:
            return self._vision_agent

        if not LANGCHAIN_AVAILABLE:
            logger.error("❌ LangChain is required but not available")
            raise ImportError(
                "LangChain is required for OpenAI OCR. "
                "Install it with: pip install doc2mark[ocr]"
            )

        if not self.api_key:
            raise RuntimeError(
                "OpenAI OCR requires an API key. Set OPENAI_API_KEY, pass api_key, "
                "or disable OCR with ocr_provider=None / --ocr none."
            )

        logger.info("🔗 Initializing LangChain VisionAgent for batch processing")
        if self.base_url:
            logger.info(f"🌐 Using custom base URL: {self.base_url}")
        try:
            self._vision_agent = VisionAgent(
                api_key=self.api_key,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                base_url=self.base_url,
                max_concurrency=resolve_max_concurrency(
                    self.config.max_concurrency if self.config else None
                ),
                timeout=self.timeout,
                max_retries=self.max_retries,
                structured=structured,
                response_model=response_model,
                detail=detail,
            )
        except Exception as e:
            logger.error(f"❌ Failed to initialize VisionAgent: {e}")
            raise RuntimeError(f"Failed to initialize LangChain VisionAgent: {str(e)}") from e
        return self._vision_agent

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

        self._vision_agent = None
        logger.info("VisionAgent configuration updated; it will reinitialize on the next OCR request")

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
            raise OCRError(f"Failed to save image locally: {str(e)}") from e

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
            *,
            task: Optional[Union[str, Task]] = None,
            tasks: Optional[List[Union[str, Task]]] = None,
            language: Optional[str] = None,
            structured: Optional[bool] = None,
            detail: Optional[str] = None,
            **kwargs
    ) -> List[OCRResult]:
        """
        Process multiple images using LangChain for optimal performance.

        Args:
            images: List of image data
            task: Single OCR intent applied to every image (overrides config.task).
            tasks: Per-image OCR intents for mixed batches; must match len(images).
            language: Output language hint (overrides config.language).
            structured: Override config.structured for this call. When False the
                legacy free-form path runs (returns OCRResult.text, document=None).
            detail: "full" or "raw"; "raw" skips interpretation to save tokens.
            **kwargs: Additional options (e.g. save_locally, content_type,
                instructions, prompt_template for the legacy path).

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

        # Resolve the structured-output controls (per-call override > config).
        resolved_structured = self.config.structured if structured is None else structured
        resolved_detail = detail or self.config.detail
        response_model = self.config.response_model

        self._ensure_vision_agent(
            structured=resolved_structured,
            response_model=response_model,
            detail=resolved_detail,
        )

        logger.info(
            f"🔗 Using LangChain VisionAgent for batch processing "
            f"(structured={resolved_structured}, detail={resolved_detail})"
        )
        return self._batch_process_with_vision_agent(
            images,
            structured=resolved_structured,
            task=task,
            tasks=tasks,
            language=language,
            detail=resolved_detail,
            **kwargs,
        )

    def _coerce_task(self, task: Union[str, Task]) -> Task:
        """Coerce a string/enum into a :class:`Task`."""
        if isinstance(task, Task):
            return task
        try:
            return Task(task)
        except ValueError:
            available = [t.value for t in Task]
            raise ValueError(f"Unknown OCR task: {task}. Available: {available}")

    def _resolve_task_prompts(
            self,
            n_images: int,
            task: Optional[Union[str, Task]],
            tasks: Optional[List[Union[str, Task]]],
            language: Optional[str],
            detail: str,
            synthesis_markdown: bool = False,
    ) -> List[str]:
        """Build a per-image structured prompt list from TASK_PROMPTS.

        Per-image ``tasks`` win over the single ``task``/config.task. The
        existing language-instruction mechanism is appended unchanged, a
        raw-mode instruction is appended when ``detail == "raw"``, and the
        page-markdown synthesis instruction when ``synthesis_markdown`` is set.
        """
        if tasks is not None:
            if len(tasks) != n_images:
                raise ValueError(
                    f"tasks length ({len(tasks)}) must match number of images ({n_images})"
                )
            resolved_tasks = [self._coerce_task(t) for t in tasks]
        else:
            single = self._coerce_task(task) if task is not None else self.config.task
            resolved_tasks = [single] * n_images

        lang = language if language is not None else (self.config.language if self.config else None)
        prompts: List[str] = []
        for t in resolved_tasks:
            base = TASK_PROMPTS[t]
            base = add_language_instruction(base, lang)
            if detail == "raw":
                base = base + _RAW_DETAIL_INSTRUCTION
            if synthesis_markdown:
                base = base + _SYNTHESIS_MARKDOWN_INSTRUCTION
            prompts.append(base)
        return prompts

    @staticmethod
    def _is_empty_structured(result: OCRResult) -> bool:
        """A structured result with no usable content (some models/images cannot
        fill the json_schema and return an empty OCRPage)."""
        if result.text and result.text.strip():
            return False
        doc = result.document
        if doc is None:
            return True
        raw = doc.raw
        return not (raw.text.strip() or raw.tables or raw.fields)

    def _recover_empty_structured(
            self,
            results: List[OCRResult],
            images: List[bytes],
            *,
            language: Optional[str] = None,
            **kwargs,
    ) -> List[OCRResult]:
        """Re-OCR empty structured results in free-form mode so content is never
        lost when a model can read the image but cannot fill the schema."""
        empty_idx = [i for i, r in enumerate(results) if self._is_empty_structured(r)]
        if not empty_idx:
            return results

        logger.warning(
            f"⚠️  Structured OCR returned empty for {len(empty_idx)}/{len(results)} "
            f"image(s); recovering with free-form OCR"
        )
        # Realign per-image context to the recovery sub-batch: the provider only
        # re-OCRs images at empty_idx, so context_pdfs must be sliced to match.
        cp = kwargs.pop("context_pdfs", None)
        sub_kwargs = dict(kwargs)
        if cp is not None:
            sub_kwargs["context_pdfs"] = [cp[i] for i in empty_idx]

        self._ensure_vision_agent(structured=False)
        try:
            recovered = self._batch_process_with_vision_agent(
                [images[i] for i in empty_idx], structured=False, language=language, **sub_kwargs
            )
        finally:
            self._ensure_vision_agent(structured=True)

        for j, i in enumerate(empty_idx):
            text = (recovered[j].text or "").strip()
            if not text:
                continue
            doc = results[i].document
            if doc is not None:
                doc.raw.text = text
            else:
                doc = OCRPage(raw=RawExtraction(text=text))
            meta = dict(results[i].metadata or {})
            meta["structured_fallback"] = "free_form"
            results[i] = OCRResult(
                text=text,
                confidence=results[i].confidence,
                language=results[i].language,
                metadata=meta,
                document=doc,
            )
        return results

    def _batch_process_with_vision_agent(
            self,
            images: List[bytes],
            *,
            structured: bool = False,
            task: Optional[Union[str, Task]] = None,
            tasks: Optional[List[Union[str, Task]]] = None,
            language: Optional[str] = None,
            detail: str = "full",
            **kwargs,
    ) -> List[OCRResult]:
        """Process images using LangChain VisionAgent for optimal performance."""
        try:
            # Build the per-image prompts. Structured output selects schema-aligned
            # TASK_PROMPTS; the legacy path keeps the verbose template builder.
            synthesis_markdown = bool(kwargs.get('synthesis_markdown', False))
            if structured:
                prompts = self._resolve_task_prompts(
                    len(images), task, tasks, language, detail, synthesis_markdown=synthesis_markdown)
            else:
                legacy_kwargs = dict(kwargs)
                if language is not None:
                    legacy_kwargs['language'] = language
                prompts = [self._build_prompt(**legacy_kwargs)] * len(images)

            # Optional per-image neighbor-page PDF context (len == len(images)).
            # Absent when the feature is off -> off-by-default byte-identical path.
            context_pdfs = kwargs.get('context_pdfs')

            # Prepare input data for VisionAgent
            input_dicts = []
            for i, image_data in enumerate(images):
                # Convert image to supported format if needed
                converted_data, mime_type = convert_image_to_supported_format(image_data)
                base64_image = base64.b64encode(converted_data).decode('utf-8')
                input_dicts.append({
                    'image_data': base64_image,
                    'mime_type': mime_type,
                    'prompt': prompts[i],
                    'index': i,
                    'context_pdf': context_pdfs[i] if context_pdfs else None,
                    'context_pdf_enabled': getattr(self._vision_agent, '_context_pdf_enabled', False),
                })

            # Use VisionAgent batch processing (same as original ocr_agent.py)
            logger.info(f"🚀 Processing {len(input_dicts)} images with VisionAgent")
            batch_results = self._vision_agent.batch_invoke(input_dicts)

            results = self._results_from_batch(images, batch_results, language, kwargs)
            if structured:
                results = self._recover_empty_structured(results, images, language=language, **kwargs)

            successful = len([r for r in results if r.text])
            logger.info(f"✅ VisionAgent batch complete: {successful}/{len(images)} successful")

            return results

        except (OCRError, ValueError):
            # OCRError (e.g. on_parse_error="raise") and ValueError (e.g. a
            # tasks/images length mismatch) are intentional — surface them as-is.
            raise
        except Exception as e:
            logger.error(f"❌ VisionAgent batch processing failed: {e}")
            raise OCRError(f"Failed to process images with LangChain: {str(e)}") from e

    def _results_from_batch(
            self,
            images: List[bytes],
            batch_results: List[Any],
            language: Optional[str],
            kwargs: Dict[str, Any],
    ) -> List[OCRResult]:
        """Convert VisionAgent batch output into OCRResult objects.

        Shape-tolerant: structured runs yield ``{"parsed", "raw", ...}`` dicts
        (``document`` populated), while the legacy free-form path yields
        ``(text, token_usage)`` tuples (``document=None``).
        """
        on_parse_error = self.config.on_parse_error if self.config else "raw_text"
        # page_markdown is an image-strategy-only synthesis; null it everywhere else so
        # to_markdown() stays byte-identical for normal docs / embedded-figure OCR.
        synthesis_markdown = bool(kwargs.get('synthesis_markdown', False))
        results: List[OCRResult] = []

        for i, item in enumerate(batch_results):
            image_size = len(images[i]) if i < len(images) else None

            if isinstance(item, dict):
                # --- Structured path ---
                page = item.get("parsed")
                raw_msg = item.get("raw")
                token_usage = item.get("usage") or {}

                if page is None:
                    if on_parse_error == "raise":
                        raise OCRError(
                            f"Structured OCR parse failed: {item.get('parsing_error')}"
                        )
                    # render bridge: free-form content into raw.text
                    content = getattr(raw_msg, "content", "") or ""
                    if isinstance(content, str):
                        content = content.replace('```', '`')
                    page = OCRPage(raw=RawExtraction(text=content), interpretation=None)

                if not synthesis_markdown and page.interpretation is not None:
                    page.interpretation.page_markdown = None
                results.append(OCRResult(
                    text=page.to_markdown(),
                    confidence=(page.interpretation.self_confidence if page.interpretation else None),
                    language=page.raw.detected_language,
                    metadata={
                        "model": self.model,
                        "token_usage": token_usage,
                        "structured": True,
                        "image_size_bytes": image_size,
                        "batch_index": i,
                    },
                    document=page,
                ))
            else:
                # --- Legacy free-form path ---
                text_result, token_usage = item
                results.append(OCRResult(
                    text=text_result,
                    confidence=1.0,
                    language=language or (self.config.language if self.config else None),
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
                        "model_kwargs": self.model_kwargs,
                        "token_usage": token_usage,
                        "structured": False,
                    }
                ))

        return results

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
