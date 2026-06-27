"""Google Gemini OCR implementation using langchain-google-genai."""

import base64
import logging
import os
import warnings
from typing import Any, Dict, List, Optional, Tuple, Union

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
    add_language_instruction,
    build_prompt,
    list_available_prompts,
)

logger = logging.getLogger(__name__)

# Gemini supports these image formats natively
SUPPORTED_IMAGE_FORMATS = {"png", "jpeg", "jpg", "gif", "webp"}

# Appended to a structured task prompt when detail="raw" — instructs the model to
# skip the interpretation subtree entirely to save output tokens.
_RAW_DETAIL_NOTE = (
    " Leave every interpretation field empty/default — only fill the raw section."
)


def _prepare_prompt(data: Dict[str, str]) -> "ChatPromptTemplate":
    """Prepare prompt for LangChain batch processing with Gemini."""

    if not LANGCHAIN_GOOGLE_GENAI_AVAILABLE:
        raise ImportError("langchain-google-genai is required for _prepare_prompt")

    prompt_text = data.get("prompt", DEFAULT_OCR_PROMPT)
    image_base64 = data["image_data"]
    mime_type = data.get("mime_type", "image/png")
    context_pdf = data.get("context_pdf")  # raw base64, no data-uri prefix; or None

    content = [
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}},
    ]
    if context_pdf:
        content.append({
            "type": "text",
            "text": _CONTEXT_PDF_INSTRUCTION + "\n\n" + _ROUTER_CONFIDENCE_CLAUSE,
        })
        content.append({
            "type": "media",
            "mime_type": "application/pdf",
            "data": context_pdf,  # VERIFIED Gemini format; RAW base64
        })

    return ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=prompt_text),
            HumanMessage(content=content),
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
        max_tokens: int = 8192,
        max_concurrency: Optional[int] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        structured: bool = False,
        response_model: Optional[type] = None,
    ):
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_concurrency = max_concurrency
        self.timeout = timeout
        self.max_retries = max_retries
        # Default mode for batch_invoke when no per-call override is given.
        self.structured = structured
        self.response_model = response_model

        if not LANGCHAIN_GOOGLE_GENAI_AVAILABLE:
            logger.warning("langchain-google-genai not available")
            self._llm = None
            self._chain = None
            self._structured_chain = None
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

            # Forward resilience knobs when explicitly set
            if self.timeout is not None:
                llm_kwargs["timeout"] = self.timeout
            if self.max_retries is not None:
                llm_kwargs["max_retries"] = self.max_retries

            self._llm = ChatGoogleGenerativeAI(**llm_kwargs)
            # Legacy free-form chain — image input is independent of output format.
            self._chain = RunnableLambda(_prepare_prompt) | self._llm
            # Structured chain mirrors the OpenAI provider: swap only the chain's
            # final stage for a json_schema-constrained model. Built eagerly so a
            # per-call structured override works even when the default is legacy.
            model_cls = self.response_model or OCRPage
            structured_llm = self._llm.with_structured_output(
                model_cls, method="json_schema", include_raw=True,
            )
            self._structured_chain = RunnableLambda(_prepare_prompt) | structured_llm

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

    def batch_invoke(
        self,
        input_dicts: List[Dict[str, str]],
        structured: Optional[bool] = None,
    ) -> List[Any]:
        """Process multiple images using LangChain batch processing.

        Args:
            input_dicts: Per-image prompt/image payloads.
            structured: Per-call override of the agent default. When the resolved
                value is False the legacy free-form path runs and the return type
                is ``List[Tuple[str, Dict]]`` (processed text, token usage). When
                True the structured chain runs and each element is the
                ``{'raw', 'parsed', 'parsing_error'}`` payload from
                ``with_structured_output(include_raw=True)``.
        """
        # getattr keeps the agent usable when constructed via __new__ (e.g. the
        # concurrency tests build a bare agent and never set ``structured``).
        effective = getattr(self, "structured", False) if structured is None else structured
        chain = getattr(self, "_structured_chain", None) if effective else self._chain
        if not chain:
            raise RuntimeError("langchain-google-genai not available")

        logger.info(
            f"Starting Gemini batch processing of {len(input_dicts)} images "
            f"(max_concurrency={self.max_concurrency or 'default'}, structured={effective})"
        )

        _cfg = {"max_concurrency": self.max_concurrency} if self.max_concurrency else None
        # return_exceptions=True isolates a single image's failure (e.g. a dense page
        # truncated at max_tokens) so it does NOT abort the whole batch.
        results = chain.batch_as_completed(input_dicts, config=_cfg, return_exceptions=True)
        sorted_results = sorted(results, key=lambda x: x[0])

        logger.info("Gemini batch processing complete")

        if effective:
            # Pass the structured payload through; OCRResult assembly happens in
            # VertexAIOCR so it can honour detail/on_parse_error. A failed item
            # becomes a parse-error payload so it is recovered, not propagated.
            out = []
            for res in sorted_results:
                payload = res[1]
                if isinstance(payload, Exception):
                    out.append({"parsed": None, "parsing_error": str(payload), "raw": None})
                else:
                    out.append(payload)
            return out

        out = []
        for res in sorted_results:
            msg = res[1]
            if isinstance(msg, Exception):
                out.append(("", {}))
            else:
                out.append((self._extract_text(msg.content), self._extract_usage(msg)))
        return out


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
        max_tokens: int = 8192,
        default_prompt: Optional[str] = None,
        prompt_template: Optional[Union[str, PromptTemplate]] = None,
        timeout: int = 30,
        max_retries: int = 3,
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
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries for failed requests
            **kwargs: Additional model parameters
        """
        super().__init__(api_key, config)

        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.location = location
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.model_kwargs = kwargs

        self.config = config or OCRConfig()

        # Warn once if the caller set fields that are inert for LLM providers.
        deprecated = self.config.deprecated_llm_overrides()
        if deprecated:
            warnings.warn(
                f"OCRConfig fields {deprecated} have no effect for the "
                f"Vertex/Gemini provider and are deprecated; they will be removed "
                f"in a future release.",
                DeprecationWarning,
                stacklevel=2,
            )

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

        # Initialize vision agent lazily so non-OCR processing can still run
        # without Vertex AI extras or credentials.
        self._vision_agent = None

        logger.info(f"Initializing Vertex AI OCR:")
        logger.info(f"   - Model: {self.model}")
        logger.info(f"   - Project: {self.project}")
        logger.info(f"   - Location: {self.location}")
        logger.info(f"   - Prompt template: {self.prompt_template.value}")

        logger.info("Vertex AI VisionAgent will initialize on first OCR request")

    def validate_api_key(self) -> bool:
        """Vertex AI uses Google Cloud ADC, not an API key."""
        return LANGCHAIN_GOOGLE_GENAI_AVAILABLE

    def _ensure_vision_agent(self) -> VertexAIVisionAgent:
        """Initialize the Vertex AI vision agent only when OCR is requested."""
        if self._vision_agent:
            return self._vision_agent

        if not LANGCHAIN_GOOGLE_GENAI_AVAILABLE:
            raise ImportError(
                "langchain-google-genai is required for Vertex AI OCR. "
                "Install it with: pip install doc2mark[vertex_ai]"
            )

        try:
            self._vision_agent = VertexAIVisionAgent(
                project=self.project,
                location=self.location,
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                max_concurrency=resolve_max_concurrency(
                    self.config.max_concurrency if self.config else None
                ),
                timeout=self.timeout,
                max_retries=self.max_retries,
                structured=self.config.structured if self.config else True,
                response_model=self.config.response_model if self.config else None,
            )
        except Exception as e:
            logger.error(f"Failed to initialize Vertex AI VisionAgent: {e}")
            raise RuntimeError(f"Failed to initialize Vertex AI VisionAgent: {str(e)}") from e
        return self._vision_agent

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

        self._ensure_vision_agent()

        return self._batch_process_with_vision_agent(images, **kwargs)

    @staticmethod
    def _coerce_task(task: Any) -> Task:
        """Normalize a task value (Task enum or its string name) to a Task."""
        if isinstance(task, Task):
            return task
        if isinstance(task, str):
            try:
                return Task(task)
            except ValueError:
                return Task.AUTO
        return Task.AUTO

    def _resolve_structured(self, **kwargs) -> bool:
        """Resolve the effective structured flag (per-call override > config)."""
        override = kwargs.get("structured")
        if override is None:
            return self.config.structured if self.config else True
        return bool(override)

    def _resolve_detail(self, **kwargs) -> str:
        """Resolve the effective detail level (per-call override > config)."""
        return kwargs.get("detail") or (self.config.detail if self.config else "full")

    def _build_structured_prompts(self, images: List[bytes], **kwargs) -> List[str]:
        """Build one schema-aligned prompt per image.

        ``tasks`` (per-image) wins over ``task`` (per-call) which falls back to
        ``config.task``. ``language`` injection reuses the legacy
        ``add_language_instruction`` mechanism, exactly as the free-form path does.
        """
        n = len(images)
        tasks = kwargs.get("tasks")
        if tasks is not None:
            if len(tasks) != n:
                raise OCRError(
                    f"tasks length ({len(tasks)}) does not match images length ({n})"
                )
            task_list = [self._coerce_task(t) for t in tasks]
        else:
            task = kwargs.get("task")
            single = (
                self._coerce_task(task)
                if task is not None
                else (self.config.task if self.config else Task.AUTO)
            )
            task_list = [single] * n

        custom = kwargs.get("instructions")
        if custom:
            return [custom] * n

        language = kwargs.get("language") or (self.config.language if self.config else None)
        detail = self._resolve_detail(**kwargs)

        prompts: List[str] = []
        for t in task_list:
            base = TASK_PROMPTS.get(t, TASK_PROMPTS[Task.AUTO])
            base = add_language_instruction(base, language)
            if detail == "raw":
                base = base + _RAW_DETAIL_NOTE
            if kwargs.get("synthesis_markdown"):
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
        self, results: List[OCRResult], images: List[bytes], **kwargs
    ) -> List[OCRResult]:
        """Re-OCR empty structured results in free-form mode so content is never
        lost when a model can read the image but cannot fill the schema."""
        empty_idx = [i for i, r in enumerate(results) if self._is_empty_structured(r)]
        if not empty_idx:
            return results

        logger.warning(
            f"Structured OCR returned empty for {len(empty_idx)}/{len(results)} image(s); "
            f"recovering with free-form OCR"
        )
        # Drop per-image task selectors (sub-batch differs in size) and force legacy.
        fk = {
            k: v
            for k, v in kwargs.items()
            if k not in ("structured", "tasks", "task", "context_pdfs")
        }
        fk["structured"] = False
        cp = kwargs.get("context_pdfs")
        if cp is not None:
            fk["context_pdfs"] = [cp[i] for i in empty_idx]  # realign to the empty sub-batch
        recovered = self._batch_process_with_vision_agent(
            [images[i] for i in empty_idx], **fk
        )

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
        self, images: List[bytes], **kwargs
    ) -> List[OCRResult]:
        """Process images using Gemini VisionAgent."""
        structured = self._resolve_structured(**kwargs)
        try:
            if structured:
                prompts = self._build_structured_prompts(images, **kwargs)
            else:
                prompts = [self._build_prompt(**kwargs)] * len(images)

            context_pdfs = kwargs.get("context_pdfs")  # Optional[List[Optional[str]]], len == len(images)

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
                        "prompt": prompts[i],
                        "index": i,
                        "context_pdf": context_pdfs[i] if context_pdfs else None,
                    }
                )

            logger.info(
                f"Processing {len(input_dicts)} images with Gemini VisionAgent "
                f"(structured={structured})"
            )
            batch_results = self._vision_agent.batch_invoke(
                input_dicts, structured=structured
            )

            if structured:
                results = self._build_structured_results(batch_results, images, **kwargs)
                return self._recover_empty_structured(results, images, **kwargs)

            return self._build_legacy_results(batch_results, images, **kwargs)

        except OCRError:
            raise
        except Exception as e:
            logger.error(f"Vertex AI batch processing failed: {e}")
            raise OCRError(f"Failed to process images with Vertex AI: {str(e)}") from e

    def _build_legacy_results(
        self, batch_results: List[Tuple[str, Dict[str, Any]]], images: List[bytes], **kwargs
    ) -> List[OCRResult]:
        """Build OCRResults from the legacy free-form (text, usage) tuples."""
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
        logger.info(f"Vertex AI batch complete: {successful}/{len(images)} successful")
        return results

    def _build_structured_results(
        self, batch_results: List[Any], images: List[bytes], **kwargs
    ) -> List[OCRResult]:
        """Build OCRResults carrying ``document=OCRPage`` from structured payloads.

        Each payload is the ``{'raw', 'parsed', 'parsing_error'}`` dict produced by
        ``with_structured_output(include_raw=True)``. On a parse failure we either
        raise or fall back to a verbatim ``OCRPage`` per ``config.on_parse_error``.
        """
        detail = self._resolve_detail(**kwargs)
        on_parse_error = self.config.on_parse_error if self.config else "raw_text"

        results: List[OCRResult] = []
        for i, payload in enumerate(batch_results):
            if isinstance(payload, dict):
                page = payload.get("parsed")
                aimsg = payload.get("raw")
                parsing_error = payload.get("parsing_error")
            else:  # defensive: a bare AIMessage if include_raw was bypassed
                page, aimsg, parsing_error = None, payload, None

            usage = self._vision_agent._extract_usage(aimsg) if aimsg else {}

            if page is None:
                if on_parse_error == "raise":
                    raise OCRError(
                        f"Structured OCR parse failed for image {i}: {parsing_error}"
                    )
                text = VertexAIVisionAgent._extract_text(getattr(aimsg, "content", ""))
                page = OCRPage(raw=RawExtraction(text=text), interpretation=None)

            if isinstance(page, OCRPage):
                if detail == "raw":
                    page.interpretation = None
                # page_markdown is image-strategy-only; null it elsewhere so to_markdown
                # stays byte-identical for normal docs / embedded-figure OCR.
                if not kwargs.get("synthesis_markdown") and page.interpretation is not None:
                    page.interpretation.page_markdown = None
                text = page.to_markdown() or page.raw.text
                confidence = (
                    page.interpretation.self_confidence if page.interpretation else None
                )
                detected_language = page.raw.detected_language
            else:  # BYO response_model — keep the object, render a best-effort string
                text = str(page)
                confidence = None
                detected_language = None

            results.append(
                OCRResult(
                    text=text,
                    confidence=confidence,
                    language=detected_language
                    or kwargs.get("language")
                    or (self.config.language if self.config else None),
                    metadata={
                        "model": self.model,
                        "provider": "vertex_ai",
                        "project": self.project,
                        "location": self.location,
                        "temperature": self.temperature,
                        "max_tokens": self.max_tokens,
                        "structured": True,
                        "detail": detail,
                        "using_custom_instructions": "instructions" in kwargs,
                        "image_size_bytes": len(images[i]),
                        "batch_index": i,
                        "parse_error": str(parsing_error) if parsing_error else None,
                        "token_usage": usage,
                    },
                    document=page if isinstance(page, OCRPage) else None,
                )
            )

        successful = len([r for r in results if r.text])
        logger.info(f"Vertex AI batch complete: {successful}/{len(images)} successful")
        return results

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


# Register the provider under both VERTEX_AI and the GEMINI alias so that
# OCRProvider("gemini") / "gemini" resolves to the same implementation.
logger.debug("Registering Vertex AI OCR provider")
OCRFactory.register_provider(OCRProvider.VERTEX_AI, VertexAIOCR)
OCRFactory.register_provider(OCRProvider.GEMINI, VertexAIOCR)
