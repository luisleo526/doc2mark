"""Main UnifiedDocumentLoader implementation."""

import base64
import datetime
import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, List, Optional, Union

from doc2mark.core.base import (
    BaseProcessor,
    DocumentMetadata,
    DocumentFormat,
    OutputFormat,
    ProcessedDocument,
    ProcessingError,
    UnsupportedFormatError
)
from doc2mark.core.strategy import ROUTING_VERSION
from doc2mark.core.structure import tables_and_sections
from doc2mark.core.table import TableStyle
from doc2mark.ocr.base import BaseOCR, OCRConfig, OCRFactory, OCRProvider, Task
from doc2mark.ocr.cache import CachedOCR, OCRCache, ocr_settings_identity
from doc2mark.ocr.prompts import PromptTemplate
from doc2mark.ocr.usage import UsageAggregatingOCR
from doc2mark.utils.output_paths import plan_output_names

logger = logging.getLogger(__name__)

# Schema of the document cache (cache_dir): part of every entry's file name and stored in the
# entry. v2: the key holds the OCR provider's answer-changing settings (model, task, language,
# structured mode, detail, prompts, ...: doc2mark.ocr.cache.ocr_settings_identity) instead of
# its class name only, so v1 entries, which may hold OCR text made with other settings, are
# never read.
DOCUMENT_CACHE_SCHEMA = "doc2mark-document-cache-v2"

# The API's own defaults of the loader's sampling settings: a value equal to its default is
# not sent (some models, such as OpenAI's reasoning models, may reject the parameters).
_SAMPLING_DEFAULTS = {"top_p": 1.0, "frequency_penalty": 0.0, "presence_penalty": 0.0}

# Arguments _create_ocr_provider passes to a provider by name: an older provider's model_kwargs
# (kept by set_ocr_provider) must not pass them a second time.
_NAMED_PROVIDER_ARGUMENTS = frozenset({
    "api_key", "config", "model", "temperature", "max_tokens", "max_workers", "prompt_template", "timeout",
    "max_retries", "default_prompt", "base_url", "project", "location",
})


class UnifiedDocumentLoader:
    """Main document loader with unified API for all formats and enhanced OCR configuration."""

    def __init__(
            self,
            ocr_provider: Optional[Union[str, OCRProvider, BaseOCR]] = 'openai',
            api_key: Optional[str] = None,
            ocr_config: Optional[OCRConfig] = None,
            cache_dir: Optional[str] = None,
            ocr_cache: Optional[OCRCache] = None,
            # Enhanced OCR configuration for OpenAI / Vertex AI
            model: str = "gpt-5.4-mini",
            temperature: float = 0,
            max_tokens: int = 8192,
            max_workers: Optional[int] = None,
            prompt_template: Union[str, PromptTemplate] = PromptTemplate.DEFAULT,
            timeout: int = 30,
            max_retries: int = 3,
            # Additional OpenAI parameters
            top_p: float = 1.0,
            frequency_penalty: float = 0.0,
            presence_penalty: float = 0.0,
            base_url: Optional[str] = None,
            # Vertex AI parameters
            project: Optional[str] = None,
            location: str = "global",
            # General OCR parameters
            default_prompt: Optional[str] = None,
            # Structured-OCR knobs (override the OCRConfig sent to LLM providers
            # when provided; None = leave the config / provider default in place)
            task: Optional[Union[str, Task]] = None,
            structured: Optional[bool] = None,
            detail: Optional[str] = None,
            # Table output configuration
            table_style: Optional[Union[str, TableStyle]] = None,
            # Optional text-layer legibility judge (PDF quality gate)
            legibility_judge: Optional[Callable[[str], Optional[float]]] = None,
            # Optional judge for repeated header/footer lines the rule keeps (PDF)
            boilerplate_judge: Optional[Callable[[str, Dict[str, Any]], Optional[float]]] = None,
            # Optional judge object answering all hooks ("typesafe", "none" or an object)
            judge: Any = None,
    ):
        """Initialize the document loader with enhanced OCR configuration.

        Args:
            ocr_provider: OCR provider name, enum, or instance
            api_key: API key for OCR provider (OpenAI defaults to OPENAI_API_KEY env var)
            ocr_config: Basic OCR configuration (from base class)
            cache_dir: Directory for caching processed documents
            ocr_cache: Optional request-scoped OCR cache handler

            # OCR model settings (OpenAI and Vertex AI / Gemini):
            model: Model to use (default: gpt-5.4-mini for OpenAI; for Vertex AI the
                ``ocr_config`` model, else gemini-3.1-flash-lite-preview)
            temperature: Temperature for response generation (0.0-2.0)
            max_tokens: Maximum tokens in response (1-8192)
            max_workers: Maximum number of OCR requests sent at once, when
                ``ocr_config.max_concurrency`` is not set (None: ``$OCR_MAX_CONCURRENCY``,
                else LangChain's default thread pool)
            prompt_template: Template name (see PromptTemplate enum for the full list, e.g.
                'default', 'table_focused', 'document_focused', 'multilingual',
                'form_focused', 'receipt_focused', 'handwriting_focused', 'code_focused')
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries for failed requests

            # Sampling settings, sent only when they differ from the API default:
            top_p: Nucleus sampling parameter (0.0-1.0)
            frequency_penalty: Reduce word repetition (-2.0 to 2.0)
            presence_penalty: Encourage new topics (-2.0 to 2.0)
            base_url: Optional base URL for OpenAI-compatible API endpoints

            # Vertex AI parameters:
            project: Google Cloud project ID (defaults to GOOGLE_CLOUD_PROJECT env var)
            location: Google Cloud region (default: global)

            # General OCR parameters:
            default_prompt: Prompt of free-form OCR requests (``structured=False`` and the
                free-form retry of an empty structured answer) instead of the
                ``prompt_template``'s; structured requests use the task prompts

            # Structured-OCR knobs (LLM providers):
            task: Override the OCRConfig task (Task enum or name, e.g. 'receipt').
                None leaves the supplied config / provider default untouched.
            structured: Override structured-output mode (True/False). None leaves
                the config / provider default (structured=True) untouched.
            detail: Override interpretation detail ('raw' or 'full'). None leaves
                the config / provider default untouched.

            # Table output configuration:
            table_style: Output style for complex tables with merged cells (a name in any case
                or a ``TableStyle``; anything else is a ``ValueError``), used for every format,
                legacy Office files included:
                - 'minimal_html': Clean HTML with only rowspan/colspan (default)
                - 'markdown_grid': Markdown with merge annotations
                - 'styled_html': Full HTML with inline styles (legacy)

            # Text-layer quality gate (PDF):
            legibility_judge: Optional ``judge(page_text) -> Optional[float]``
                returning the probability that a page's extracted text is legible, or
                None when it cannot judge. Consulted only when OCR is active
                (``ocr_images=True`` with an OCR provider) and only for text layers the
                deterministic garbage detector does not flag; see
                doc2mark.core.strategy.judge_text_layer for the full contract.
            boilerplate_judge: Optional ``judge(line_text, context) -> Optional[float]``
                returning the probability that a repeated top/bottom line of a PDF is page
                chrome; asked only about the lines the verbatim-first rule keeps (see
                doc2mark.pipelines.pymupdf_advanced_pipeline.PDFLoader).

            # All judge hooks at once:
            judge: ``"typesafe"`` (the optional ``doc2mark[typesafe]`` add-on, key in
                ``TYPESAFE_API_KEY``), ``"none"``, or an object with any of the attributes
                ``legibility_judge``, ``boilerplate_judge`` and ``non_content_judge`` (the
                last one screens OCR answers, see ``OCRConfig.non_content_judge``). None
                (default) reads ``$DOC2MARK_JUDGE``. An explicit ``legibility_judge`` /
                ``boilerplate_judge`` or ``OCRConfig.non_content_judge`` wins over the
                object's. A judge that cannot answer leaves every decision to the
                deterministic rules; see docs/judge.rst.
        """
        logger.info("🚀 Initializing UnifiedDocumentLoader with enhanced OCR configuration")

        # Checked first, so a misspelt style fails before anything is set up
        table_style = self._normalize_table_style(table_style)

        from doc2mark.judge import judge_hooks, resolve_judge
        self.judge = resolve_judge(judge)
        hooks = judge_hooks(self.judge)

        self.ocr_cache = None
        self.ocr = self._create_ocr_provider(
            ocr_provider=ocr_provider,
            api_key=api_key,
            ocr_config=ocr_config,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            max_workers=max_workers,
            prompt_template=prompt_template,
            timeout=timeout,
            max_retries=max_retries,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            base_url=base_url,
            project=project,
            location=location,
            default_prompt=default_prompt,
            task=task,
            structured=structured,
            detail=detail,
        )
        self._apply_ocr_cache(ocr_cache)
        self._attach_non_content_judge(hooks["non_content_judge"])

        # Cache directory
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"📁 Cache directory: {self.cache_dir}")

        # Table output style (default: minimal_html for cleaner output)
        self.table_style = table_style
        logger.info(f"📊 Table style: {self.table_style}")
        self.legibility_judge = legibility_judge if legibility_judge is not None else hooks["legibility_judge"]
        self.boilerplate_judge = boilerplate_judge if boilerplate_judge is not None else hooks["boilerplate_judge"]

        # Registry of format processors
        self._processors: Dict[DocumentFormat, BaseProcessor] = {}
        self._initialize_processors()

        logger.info("✅ UnifiedDocumentLoader initialized successfully")

    @staticmethod
    def _normalize_table_style(table_style: Optional[Union[str, TableStyle]]) -> str:
        """The name of a valid table style. None (or an empty string) is the default; a name is
        matched in any case; anything else is a ValueError naming the valid styles, instead of
        failing in the PDF reader and falling back to a basic converter in the Office one."""
        if not table_style:
            return TableStyle.default().value
        if isinstance(table_style, TableStyle):
            return table_style.value
        if isinstance(table_style, str):
            try:
                return TableStyle(table_style.strip().lower()).value
            except ValueError:
                pass
        valid = ", ".join(style.value for style in TableStyle)
        raise ValueError(f"Unknown table_style {table_style!r}. Expected one of: {valid}")

    @staticmethod
    def _is_ocr_provider(provider: Union[str, OCRProvider], target: OCRProvider) -> bool:
        if isinstance(provider, OCRProvider):
            return provider == target
        if isinstance(provider, str):
            return provider.lower() == target.value
        return False

    @staticmethod
    def _unwrap_ocr(ocr: Optional[BaseOCR]) -> Optional[BaseOCR]:
        if ocr is None:
            return None
        return ocr.wrapped if isinstance(ocr, CachedOCR) else ocr

    @staticmethod
    def _wrap_usage_aggregation(ocr: Optional[BaseOCR]) -> Optional[BaseOCR]:
        """Wrap the OCR instance so per-load token usage can be aggregated.

        Returns ``None`` unchanged when OCR is disabled, and never double-wraps.
        The wrapper is transparent, so callers treat it exactly like the provider.
        """
        if ocr is None or isinstance(ocr, UsageAggregatingOCR):
            return ocr
        return UsageAggregatingOCR(ocr)

    @staticmethod
    def _resolve_ocr_config(
            ocr_config: Optional[OCRConfig],
            task: Optional[Union[str, Task]] = None,
            structured: Optional[bool] = None,
            detail: Optional[str] = None,
    ) -> OCRConfig:
        """Resolve the OCRConfig handed to the OCR provider.

        Always returns a concrete OCRConfig (never None) so the structured
        defaults (structured=True) apply consistently even when the caller did
        not supply a config. Any of ``task``/``structured``/``detail`` that are
        not None override the matching config field; None leaves the config
        value (or the OCRConfig default) untouched. The caller's config object
        is never mutated.
        """
        config = ocr_config if ocr_config is not None else OCRConfig()
        overrides: Dict[str, Any] = {}
        if task is not None:
            overrides["task"] = task if isinstance(task, Task) else Task(str(task).lower())
        if structured is not None:
            overrides["structured"] = structured
        if detail is not None:
            overrides["detail"] = detail
        if overrides:
            config = replace(config, **overrides)
        return config

    def _create_ocr_provider(
            self,
            ocr_provider: Optional[Union[str, OCRProvider, BaseOCR]],
            api_key: Optional[str] = None,
            ocr_config: Optional[OCRConfig] = None,
            model: str = "gpt-5.4-mini",
            temperature: float = 0,
            max_tokens: int = 8192,
            max_workers: Optional[int] = None,
            prompt_template: Union[str, PromptTemplate] = PromptTemplate.DEFAULT,
            timeout: int = 30,
            max_retries: int = 3,
            top_p: float = 1.0,
            frequency_penalty: float = 0.0,
            presence_penalty: float = 0.0,
            base_url: Optional[str] = None,
            project: Optional[str] = None,
            location: str = "global",
            default_prompt: Optional[str] = None,
            task: Optional[Union[str, Task]] = None,
            structured: Optional[bool] = None,
            detail: Optional[str] = None,
            model_kwargs: Optional[Dict[str, Any]] = None,
    ) -> Optional[BaseOCR]:
        """Create an OCR provider using the same enhanced path everywhere."""
        if ocr_provider is None or (isinstance(ocr_provider, str) and ocr_provider.lower() in {"none", "disabled"}):
            logger.info("OCR provider disabled")
            return None

        if isinstance(ocr_provider, BaseOCR):
            logger.info(f"✓ Using provided OCR instance: {type(ocr_provider).__name__}")
            return ocr_provider

        logger.info(f"🤖 Initializing OCR provider: {ocr_provider}")
        # Resolve a concrete OCRConfig so the structured/task/detail knobs reach
        # the provider and the structured defaults apply even when no config was
        # supplied. This config is passed through to every provider branch below.
        ocr_config = self._resolve_ocr_config(ocr_config, task=task, structured=structured, detail=detail)
        extra_model_kwargs = {key: value for key, value in (model_kwargs or {}).items()
                              if key not in _NAMED_PROVIDER_ARGUMENTS}
        # Sampling settings are sent only when set to something other than the API's default.
        sampling = {"top_p": top_p, "frequency_penalty": frequency_penalty, "presence_penalty": presence_penalty}
        for name, value in sampling.items():
            if value is not None and value != _SAMPLING_DEFAULTS[name]:
                extra_model_kwargs.setdefault(name, value)

        if self._is_ocr_provider(ocr_provider, OCRProvider.OPENAI):
            logger.info("🔧 Using enhanced OpenAI OCR configuration")
            from doc2mark.ocr.openai import OpenAIOCR

            ocr = OpenAIOCR(
                api_key=api_key,
                config=ocr_config,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                max_workers=max_workers,
                prompt_template=prompt_template,
                timeout=timeout,
                max_retries=max_retries,
                default_prompt=default_prompt,
                base_url=base_url,
                **extra_model_kwargs,
            )
            self._log_ocr_configuration(ocr, title="📋 OCR Configuration Summary:")
            return ocr

        if (self._is_ocr_provider(ocr_provider, OCRProvider.VERTEX_AI)
                or self._is_ocr_provider(ocr_provider, OCRProvider.GEMINI)):
            logger.info("Using enhanced Vertex AI OCR configuration")
            from doc2mark.ocr.vertex_ai import VertexAIOCR

            vertex_kwargs: Dict[str, Any] = {}
            if model != "gpt-5.4-mini":
                # The loader's default is an OpenAI model: left at it, the Vertex AI provider
                # takes ocr_config.model, else its own default.
                vertex_kwargs["model"] = model
            ocr = VertexAIOCR(
                api_key=api_key,
                config=ocr_config,
                project=project,
                location=location,
                temperature=temperature,
                max_tokens=max_tokens,
                prompt_template=prompt_template,
                default_prompt=default_prompt,
                timeout=timeout,
                max_retries=max_retries,
                max_workers=max_workers,
                **vertex_kwargs,
                **extra_model_kwargs,
            )
            self._log_ocr_configuration(ocr, title="OCR Configuration Summary:")
            return ocr

        logger.info(f"Using standard OCR factory for provider: {ocr_provider}")
        return OCRFactory.create(
            provider=ocr_provider,
            api_key=api_key,
            config=ocr_config
        )

    def _attach_non_content_judge(self, judge: Optional[Callable[[str], Optional[float]]]) -> None:
        """Give the OCR provider the judge object's ``non_content_judge``, unless OCR is off
        or its config already names one (the caller's config object is not mutated)."""
        target = self._unwrap_ocr(self.ocr)
        config = getattr(target, "config", None)
        if judge is None or not isinstance(config, OCRConfig) or config.non_content_judge is not None:
            return
        target.config = replace(config, non_content_judge=judge)

    @staticmethod
    def _log_ocr_configuration(ocr: BaseOCR, title: str):
        if hasattr(ocr, 'get_configuration_summary'):
            config_summary = ocr.get_configuration_summary()
            logger.info(title)
            for key, value in config_summary.items():
                logger.info(f"   {key}: {value}")

    def _apply_ocr_cache(self, ocr_cache: Optional[OCRCache] = None):
        """Apply an explicit cache, preserve embedded cache, or reuse loader cache."""
        if self.ocr is None:
            if ocr_cache is not None:
                self.ocr_cache = ocr_cache
                logger.info(f"OCR cache configured for later use: {type(self.ocr_cache).__name__}")
            return

        if ocr_cache is not None:
            self.ocr_cache = ocr_cache
            if isinstance(self.ocr, CachedOCR):
                self.ocr.cache = ocr_cache
            else:
                self.ocr = CachedOCR(self.ocr, ocr_cache)
            logger.info(f"🧠 OCR cache enabled: {type(self.ocr_cache).__name__}")
            return

        if isinstance(self.ocr, CachedOCR):
            self.ocr_cache = self.ocr.cache
            logger.info(f"🧠 OCR cache enabled: {type(self.ocr_cache).__name__}")
            return

        if self.ocr_cache is not None:
            self.ocr = CachedOCR(self.ocr, self.ocr_cache)
            logger.info(f"🧠 OCR cache enabled: {type(self.ocr_cache).__name__}")

    def _current_ocr_constructor_options(self) -> Dict[str, Any]:
        """Read current provider settings so set_ocr_provider does not downgrade OCR config."""
        current = self._unwrap_ocr(self.ocr)
        if current is None:
            return {
                "api_key": None,
                "ocr_config": None,
                "model": "gpt-5.4-mini",
                "temperature": 0,
                "max_tokens": 8192,
                "max_workers": None,
                "prompt_template": PromptTemplate.DEFAULT,
                "timeout": 30,
                "max_retries": 3,
                "base_url": None,
                "project": None,
                "location": "global",
                "default_prompt": None,
                "model_kwargs": {},
            }
        return {
            "api_key": getattr(current, "api_key", None),
            "ocr_config": getattr(current, "config", None),
            "model": getattr(current, "model", "gpt-5.4-mini"),
            "temperature": getattr(current, "temperature", 0),
            "max_tokens": getattr(current, "max_tokens", 8192),
            "max_workers": getattr(current, "max_workers", None),
            "prompt_template": getattr(current, "prompt_template", PromptTemplate.DEFAULT),
            "timeout": getattr(current, "timeout", 30),
            "max_retries": getattr(current, "max_retries", 3),
            "base_url": getattr(current, "base_url", None),
            "project": getattr(current, "project", None),
            "location": getattr(current, "location", "global"),
            "default_prompt": getattr(current, "default_prompt", None),
            "model_kwargs": dict(getattr(current, "model_kwargs", {}) or {}),
        }

    def _initialize_processors(self):
        """Initialize all format processors."""
        # Import processors lazily to avoid circular imports
        try:
            # Import all processors
            from doc2mark.formats.office import OfficeProcessor
            from doc2mark.formats.pdf import PDFProcessor
            from doc2mark.formats.text import TextProcessor
            from doc2mark.formats.markup import MarkupProcessor
            from doc2mark.formats.legacy import LegacyProcessor
            from doc2mark.formats.image import ImageProcessor

            # Every OCR-capable processor shares ONE usage-aggregating wrapper around
            # the loader's OCR instance, so token usage from any OCR call (any format,
            # any provider) accumulates in one place for load() to read. self.ocr is
            # left untouched (raw provider / CachedOCR) for the public API and tests;
            # the wrapper is transparent and is None when OCR is disabled.
            ocr = self._usage_ocr = self._wrap_usage_aggregation(self.ocr)

            # Initialize processors with OCR support
            office_processor = OfficeProcessor(ocr=ocr, table_style=self.table_style)
            pdf_processor = PDFProcessor(ocr=ocr, table_style=self.table_style,
                                         legibility_judge=getattr(self, "legibility_judge", None),
                                         boilerplate_judge=getattr(self, "boilerplate_judge", None))
            text_processor = TextProcessor()
            markup_processor = MarkupProcessor()
            legacy_processor = LegacyProcessor(ocr=ocr, table_style=self.table_style)
            image_processor = ImageProcessor(ocr=ocr)

            # Register processors for each format
            # Office formats - use our new OfficeProcessor
            self._processors[DocumentFormat.DOCX] = office_processor
            self._processors[DocumentFormat.XLSX] = office_processor
            self._processors[DocumentFormat.PPTX] = office_processor

            # PDF
            self._processors[DocumentFormat.PDF] = pdf_processor

            # Text/Data formats
            for fmt in [DocumentFormat.TXT, DocumentFormat.CSV,
                        DocumentFormat.TSV, DocumentFormat.JSON,
                        DocumentFormat.JSONL]:
                self._processors[fmt] = text_processor

            # Markup formats
            for fmt in [DocumentFormat.HTML, DocumentFormat.XML,
                        DocumentFormat.MARKDOWN]:
                self._processors[fmt] = markup_processor

            # Legacy formats
            for fmt in [DocumentFormat.DOC, DocumentFormat.XLS,
                        DocumentFormat.PPT, DocumentFormat.RTF,
                        DocumentFormat.PPS]:
                self._processors[fmt] = legacy_processor
            
            # Image formats
            for fmt in [DocumentFormat.PNG, DocumentFormat.JPG,
                        DocumentFormat.JPEG, DocumentFormat.WEBP,
                        DocumentFormat.TIFF, DocumentFormat.TIF,
                        DocumentFormat.BMP, DocumentFormat.GIF,
                        DocumentFormat.HEIC, DocumentFormat.HEIF,
                        DocumentFormat.AVIF]:
                self._processors[fmt] = image_processor

            logger.info("Using individual format processors with enhanced image extraction")

        except ImportError as e:
            logger.error(f"Failed to import required processors: {e}")
            raise ImportError(f"Required format processors not available: {str(e)}") from e

        # Optional email (.eml) processor - register it only when the handler and
        # the matching DocumentFormat.EML member are both available, so the loader
        # keeps working even if email.py isn't present yet (see SHARED CONTRACT).
        try:
            from doc2mark.formats.email import EmailProcessor

            eml_format = getattr(DocumentFormat, 'EML', None)
            if eml_format is not None:
                self._processors[eml_format] = EmailProcessor(ocr=self._usage_ocr)
                logger.info("Registered EML (email) processor")
        except ImportError:
            logger.debug("Email processor not available; skipping .eml support")

    def _judge_identity(self, judge: Any = None) -> Optional[str]:
        """A stable name for a judge hook (for cache keys), by default the legibility judge.

        A judge can name its own configuration with a ``cache_key`` attribute; otherwise
        its qualified name (plus the arguments of a ``functools.partial``) is used.
        """
        if judge is None:
            judge = getattr(self, "legibility_judge", None)
        if judge is None:
            return None
        try:
            if getattr(judge, "available", True) is False:
                return None  # a judge that cannot answer at all leaves the output to the rules: keyed as none
            explicit = getattr(judge, "cache_key", None)
            if explicit is not None:
                return str(explicit)
            target = getattr(judge, "func", None) or getattr(judge, "__func__", None) or judge
            name = getattr(target, "__qualname__", None) or type(target).__qualname__
            identity = f"{getattr(target, '__module__', type(target).__module__)}.{name}"
            if getattr(judge, "func", None) is not None:  # functools.partial
                identity += repr((getattr(judge, "args", ()), getattr(judge, "keywords", {})))
            return identity
        except Exception:
            return type(judge).__qualname__

    def _begin_judge_document(self) -> Any:
        """Start counting the judge's questions for one document (judges with ``begin_document``)."""
        begin = getattr(getattr(self, "judge", None), "begin_document", None)
        if not callable(begin):
            return None
        try:
            return begin()
        except Exception as e:
            logger.debug(f"judge.begin_document failed: {e!r}")
            return None

    def _end_judge_document(self, start: Any, result: ProcessedDocument, file_path: Path) -> bool:
        """Stamp what the judge did on the document (``metadata.extra["judge"]``) and warn, once
        per document, when some of its questions got no answer (the rules decided them).
        Returns False when the result must not be cached: the judge failed on some question,
        or stopped answering during the document (its cache key named it as available)."""
        end = getattr(getattr(self, "judge", None), "end_document", None)
        if start is None or not callable(end):
            return True
        try:
            stats = dict(end(start))
        except Exception as e:
            logger.debug(f"judge.end_document failed: {e!r}")
            return False
        complete = not stats.get("failed") and stats.get("unavailable") == start.get("unavailable")
        if not (stats.get("asked") or stats.get("failed")):
            return complete
        if result.metadata.extra is None:
            result.metadata.extra = {}
        result.metadata.extra["judge"] = {
            "name": getattr(self.judge, "name", type(self.judge).__name__),
            "model": getattr(self.judge, "model", None),
            **{key: stats.get(key) for key in ("asked", "cached", "fresh", "failed", "input_tokens", "cost_usd")},
        }
        if stats.get("failed"):
            reason = stats.get("unavailable") or stats.get("last_error") or "no answer"
            logger.warning(f"{file_path.name}: {stats['failed']} judge question(s) got no answer ({reason}); "
                           f"the deterministic rules decided those cases")
        return complete

    @staticmethod
    def _fill_structure(result: ProcessedDocument) -> None:
        """Give a document its ``tables`` and ``sections`` (see ``doc2mark.core.structure``) when its
        converter produced content items (``json_content``) and did not list them itself. Formats
        without content items (text, data and markup files) keep both as None."""
        if result.json_content is None or (result.tables is not None and result.sections is not None):
            return
        tables, sections = tables_and_sections(result.json_content)
        if result.tables is None:
            result.tables = tables
        if result.sections is None:
            result.sections = sections

    @staticmethod
    def _normalize_output_format(output_format: Union[str, OutputFormat]) -> OutputFormat:
        """Normalize string output format names to OutputFormat enum values."""
        if isinstance(output_format, OutputFormat):
            return output_format
        if isinstance(output_format, str):
            try:
                return OutputFormat(output_format.lower())
            except ValueError as e:
                valid = ", ".join(fmt.value for fmt in OutputFormat)
                raise ValueError(f"Unsupported output format: {output_format}. Expected one of: {valid}") from e
        raise TypeError(f"Unsupported output format type: {type(output_format).__name__}")

    def load(
            self,
            file_path: Union[str, Path],
            output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
            extract_images: bool = False,
            ocr_images: bool = False,
            show_progress: bool = False,
            # Format-specific parameters
            encoding: str = 'utf-8',
            delimiter: Optional[str] = None
    ) -> ProcessedDocument:
        """Load and process a document.
        
        Args:
            file_path: Path to the document
            output_format: Desired output format (MARKDOWN, JSON, TEXT)
            extract_images: Whether to extract images as base64 (Office/PDF only)
            ocr_images: Whether to perform OCR on images (implies extract_images when an OCR provider is configured)
            show_progress: Whether to show progress messages during processing
            
            # Format-specific parameters:
            encoding: Text encoding for text/markup files (default: 'utf-8')
            delimiter: Delimiter for CSV files (auto-detect if None)
            
        Returns:
            ProcessedDocument with content and metadata
            
        Raises:
            UnsupportedFormatError: If format is not supported
            ProcessingError: If processing fails
            
        Note:
            - extract_images and ocr_images only work with Office and PDF formats
            - encoding and delimiter only apply to text-based formats
            
            For advanced OCR configuration, use the constructor parameters or
            update_ocr_configuration() method.
        """
        file_path = Path(file_path)
        output_format = self._normalize_output_format(output_format)

        # Validate file exists
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Determine format
        doc_format = self._detect_format(file_path)
        if doc_format not in self._processors:
            raise UnsupportedFormatError(
                f"Unsupported format: {doc_format.value}"
            )

        # OCR needs the images: ocr_images=True with the default extract_images=False
        # means "OCR my images", not "do nothing".
        if ocr_images and not extract_images and self.ocr is not None:
            logger.info("ocr_images=True implies extract_images=True (images are extracted for OCR)")
            extract_images = True

        # Check cache
        if self.cache_dir:
            cache_options = {
                "output_format": output_format.value,
                "extract_images": extract_images,
                "ocr_images": ocr_images,
                "encoding": encoding,
                "delimiter": delimiter,
                "table_style": self.table_style,
                # The OCR provider and every setting of it that changes its answers (model, task,
                # language, structured mode, detail, prompts, ...): another one must not be
                # answered with the OCR text of an older run.
                "ocr": ocr_settings_identity(self._unwrap_ocr(self.ocr), strict=False) if self.ocr else None,
                # Which PDF images carry neighbour pages as context (the OCR cache keys the
                # context a request carries; a converted document depends on the tier).
                "context_pages": getattr(getattr(self._unwrap_ocr(self.ocr), "config", None), "context_pages", None),
                # A different judge, or routing that changed what a page emits, must not
                # be answered from an older cached result.
                "legibility_judge": self._judge_identity(),
                "routing_version": ROUTING_VERSION,
            }
            # The other hooks change what a document emits too (keys only when set, so the
            # cache keys of loaders without them stay as they were).
            for name, hook in (("boilerplate_judge", getattr(self, "boilerplate_judge", None)),
                               ("non_content_judge", getattr(getattr(self._unwrap_ocr(self.ocr), "config", None),
                                                             "non_content_judge", None))):
                identity = self._judge_identity(hook) if hook is not None else None
                if identity is not None:
                    cache_options[name] = identity
            cached = self._get_cached(file_path, output_format, cache_options)
            if cached:
                logger.info(f"Using cached result for {file_path}")
                return cached
        else:
            cache_options = {}

        # Process document
        processor = self._processors[doc_format]

        try:
            # Fallback processors need parameter mapping
            processor_kwargs = {}

            # Map common parameters
            if processor.__class__.__name__ in ['OfficeProcessor', 'LegacyProcessor']:
                processor_kwargs['extract_images'] = extract_images
                processor_kwargs['ocr_images'] = ocr_images
            elif processor.__class__.__name__ == 'PDFProcessor':
                processor_kwargs['extract_images'] = extract_images
                processor_kwargs['use_ocr'] = ocr_images
                processor_kwargs['extract_tables'] = True
            elif processor.__class__.__name__ == 'ImageProcessor':
                processor_kwargs['extract_images'] = extract_images
                processor_kwargs['ocr_images'] = ocr_images
            elif processor.__class__.__name__ == 'TextProcessor':
                processor_kwargs['encoding'] = encoding
                if delimiter:
                    processor_kwargs['delimiter'] = delimiter
            elif processor.__class__.__name__ == 'MarkupProcessor':
                processor_kwargs['encoding'] = encoding

            # Start a fresh per-load OCR token-usage count, run the processor, then
            # fold the sum onto the document. Done BEFORE any output-format conversion
            # or caching so JSON output and cached copies carry the usage too.
            usage_ocr = getattr(self, "_usage_ocr", None)
            if usage_ocr is not None:
                usage_ocr.begin_document_usage()
            judge_start = self._begin_judge_document()

            # Process with mapped parameters
            result = processor.process(file_path, **processor_kwargs)
            self._fill_structure(result)
            judge_complete = self._end_judge_document(judge_start, result, file_path)

            if usage_ocr is not None:
                token_usage = usage_ocr.pop_document_usage()
                # Only stamp it when OCR actually reported tokens, so a no-OCR load
                # (or a usage-less provider like Tesseract) stays byte-identical.
                if token_usage:
                    if result.metadata.extra is None:
                        result.metadata.extra = {}
                    result.metadata.extra["token_usage"] = token_usage
                # Raises OCREngineError when the OCR engine could not run at all (the
                # pipelines degrade that to placeholders); otherwise stamp the issues.
                ocr_issues = usage_ocr.pop_document_issues()
                if ocr_issues:
                    if result.metadata.extra is None:
                        result.metadata.extra = {}
                    result.metadata.extra["ocr_issues"] = ocr_issues
                    logger.warning(f"OCR issues in {file_path.name}: {ocr_issues}")

            # Apply output format conversion if needed
            if output_format != OutputFormat.MARKDOWN:
                # Convert content to requested format
                if output_format == OutputFormat.JSON:
                    # Create JSON structure
                    json_data = result.to_dict()
                    result.content = json.dumps(json_data, indent=2, ensure_ascii=False)
                    if result.json_content is None:
                        result.json_content = [{"type": "text:normal", "content": json_data.get("content", "")}]
                elif output_format == OutputFormat.TEXT:
                    # Convert to plain text
                    result.content = result.text

            # Cache result, unless its OCR failed somewhere: a re-run with a healthy provider
            # must read what this one could not (see _ocr_incomplete).
            if self.cache_dir:
                incomplete = self._ocr_incomplete(result)
                if not judge_complete:
                    incomplete = "the judge could not answer every question"
                if incomplete:
                    logger.info(f"Not caching {file_path.name}: {incomplete}; the next run converts it again")
                else:
                    self._cache_result(file_path, output_format, result, cache_options)

            return result

        except Exception as e:
            logger.error(f"Failed to process {file_path}: {e}")
            raise ProcessingError(f"Processing failed: {str(e)}") from e

    def load_directory(
            self,
            directory: Union[str, Path],
            pattern: str = "*",
            recursive: bool = True,
            output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
            **kwargs
    ) -> List[ProcessedDocument]:
        """Load all documents from a directory.
        
        Args:
            directory: Directory path
            pattern: Glob pattern for files
            recursive: Whether to search recursively
            output_format: Desired output format
            **kwargs: Additional processor options
            
        Returns:
            List of processed documents
        """
        directory = Path(directory)
        output_format = self._normalize_output_format(output_format)
        if not directory.is_dir():
            raise ValueError(f"Not a directory: {directory}")

        # Find files
        if recursive:
            files = list(directory.rglob(pattern))
        else:
            files = list(directory.glob(pattern))

        # Process files
        results = []
        for file_path in files:
            if file_path.is_file():
                try:
                    result = self.load(
                        file_path,
                        output_format=output_format,
                        **kwargs
                    )
                    results.append(result)
                except (UnsupportedFormatError, ProcessingError) as e:
                    logger.warning(f"Skipping {file_path}: {e}")
                    continue

        return results

    def _execute_batch(
            self,
            items: List[tuple],
            total_files: int,
            process_one: Callable[[Path, Optional[Path]], Dict[str, Any]],
            max_workers: Optional[int],
            progress_callback: Optional[Callable[[int, int, str], None]],
            show_progress: bool,
            start_time: float,
    ) -> tuple:
        """Run per-file batch work either sequentially or across a thread pool.

        Args:
            items: List of (key, file_path, output_path) tuples in input order.
            total_files: Total number of files (== len(items)).
            process_one: Callable taking (file_path, output_path) and returning the
                per-file result dict. It must handle its own exceptions and return a
                ``{'status': 'failed', ...}`` dict on error so a failing file still
                records an entry.
            max_workers: When > 1 and more than one file is present, files are
                processed concurrently with a ThreadPoolExecutor. None / <= 1 keeps
                the default sequential behavior.
            progress_callback: Optional callable invoked as ``(done, total, key)``
                after each file completes.
            show_progress: Whether to log per-file progress.
            start_time: Batch start timestamp for ETA logging.

        Returns:
            Tuple of (results dict, processed_count, error_count). The results dict
            preserves the input order of ``items`` regardless of completion order.
        """
        results: Dict[str, Dict[str, Any]] = {}
        processed_count = 0
        error_count = 0

        use_parallel = bool(max_workers) and max_workers > 1 and total_files > 1

        if use_parallel:
            worker_count = min(max_workers, total_files)
            logger.info(f"⚙️  Processing {total_files} files with {worker_count} workers")
            results_by_key: Dict[str, Dict[str, Any]] = {}
            done = 0
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_to_key = {
                    executor.submit(process_one, file_path, output_path): key
                    for key, file_path, output_path in items
                }
                for future in as_completed(future_to_key):
                    key = future_to_key[future]
                    result = future.result()
                    results_by_key[key] = result
                    done += 1
                    if result.get('status') == 'success':
                        processed_count += 1
                    else:
                        error_count += 1
                    if show_progress:
                        logger.info(f"📄 Completed {done}/{total_files}: {Path(key).name}")
                    if progress_callback is not None:
                        progress_callback(done, total_files, key)
            # Rebuild in deterministic input order so results match the sequential path.
            for key, _file_path, _output_path in items:
                results[key] = results_by_key[key]
        else:
            done = 0
            for key, file_path, output_path in items:
                if show_progress:
                    logger.info(f"📄 Processing {done + 1}/{total_files}: {file_path.name}")
                result = process_one(file_path, output_path)
                results[key] = result
                done += 1
                if result.get('status') == 'success':
                    processed_count += 1
                else:
                    error_count += 1
                if progress_callback is not None:
                    progress_callback(done, total_files, key)
                if show_progress and done % 10 == 0:
                    elapsed = time.time() - start_time
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (total_files - done) / rate if rate > 0 else 0
                    logger.info(
                        f"📊 Progress: {done}/{total_files} ({done / total_files * 100:.1f}%) - ETA: {eta:.1f}s")

        return results, processed_count, error_count

    def batch_process(
            self,
            input_dir: Union[str, Path],
            output_dir: Optional[Union[str, Path]] = None,
            output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
            extract_images: bool = False,
            ocr_images: bool = False,
            recursive: bool = True,
            show_progress: bool = True,
            save_files: bool = True,
            encoding: str = 'utf-8',
            delimiter: Optional[str] = None,
            max_workers: Optional[int] = None,
            progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Batch process multiple documents in a directory with full result tracking.

        Args:
            input_dir: Directory containing documents
            output_dir: Optional output directory (default: same as input). The input tree is mirrored
                under it; files that would share an output name (``report.txt`` and ``report.md``) are
                written as ``report.txt.md`` and ``report.md.md``. Next to the inputs (the default) an
                output replaces the one an earlier run wrote.
            output_format: Output format (MARKDOWN, JSON, TEXT)
            extract_images: Whether to extract images from documents (Office/PDF only)
            ocr_images: Whether to perform OCR on images (implies extract_images when an OCR provider is configured)
            recursive: Whether to process subdirectories
            show_progress: Whether to show progress messages
            save_files: Whether to save output files
            encoding: Text encoding for text/markup files
            delimiter: CSV delimiter (auto-detect if None)
            max_workers: Optional number of worker threads. When set to a value > 1
                (and more than one file is found), documents are processed
                concurrently with a thread pool. Defaults to None, which preserves
                the original sequential behavior.
            progress_callback: Optional callable invoked as ``(done, total, path)``
                after each file finishes (whether it succeeded or failed). ``path``
                is the string key used in the returned results dict.

        Returns:
            Dictionary mapping input paths to processing results

        Examples:
            ::

                # Process with image extraction but no OCR
                loader.batch_process("docs/", extract_images=True, ocr_images=False)

                # Process with batch OCR
                loader.batch_process("docs/", extract_images=True, ocr_images=True)

                # Process concurrently with a progress bar
                loader.batch_process("docs/", max_workers=4,
                                     progress_callback=lambda d, t, p: print(f"{d}/{t}"))
        """
        input_dir = Path(input_dir)
        output_dir = Path(output_dir) if output_dir else input_dir
        output_format = self._normalize_output_format(output_format)

        if not input_dir.exists():
            raise FileNotFoundError(f"Directory not found: {input_dir}")

        logger.info(f"🗂️  Starting batch processing: {input_dir}")
        logger.info(f"📁 Output directory: {output_dir}")
        logger.info(f"📊 Recursive: {recursive}, Save files: {save_files}")
        logger.info(f"🖼️  Image processing: extract_images={extract_images}, ocr_images={ocr_images}")

        # Find all supported files: one walk that asks the same question load() does (the extension,
        # in any case), so report.PDF, page.htm and guide.markdown are found like their lower-case forms
        results = {}
        processed_count = 0
        error_count = 0
        start_time = time.time()

        files_by_format: Dict[DocumentFormat, List[Path]] = {}
        all_files = []

        for file_path in sorted(input_dir.rglob("*") if recursive else input_dir.glob("*")):
            if not file_path.is_file():
                continue
            try:
                doc_format = self._detect_format(file_path)
            except UnsupportedFormatError:
                continue
            if doc_format not in self._processors:
                continue
            files_by_format.setdefault(doc_format, []).append(file_path)
            all_files.append(file_path)

        total_files = len(all_files)

        if total_files == 0:
            logger.warning("No supported files found")
            return results

        logger.info(f"📄 Found {total_files} files to process")
        if show_progress:
            for fmt, files in files_by_format.items():
                logger.info(f"   {fmt.value.upper()}: {len(files)} files")

        # Into an output folder of its own, files that would share an output name (report.txt and report.md)
        # keep their whole file name (report.txt.md). Next to the inputs (the default) each output replaces the
        # one an earlier run wrote, so a run can be repeated.
        output_names = None
        suffixes = {OutputFormat.MARKDOWN: (".md",), OutputFormat.JSON: (".json",)}.get(output_format)
        if save_files and suffixes and output_dir.resolve() != input_dir.resolve():
            output_names = plan_output_names(
                all_files, {path: path.relative_to(input_dir) for path in all_files}, output_dir, suffixes)

        # Build the ordered work list and pre-create output directories on the main
        # thread (so concurrent workers never race on mkdir).
        items = []
        for file_path in all_files:
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(input_dir)
            if save_files:
                output_path = output_dir / (output_names[file_path] if output_names
                                            else rel_path.parent / file_path.stem)
                output_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                output_path = None
            items.append((str(file_path), file_path, output_path))

        total_files = len(items)

        def process_one(file_path: Path, output_path: Optional[Path]) -> Dict[str, Any]:
            try:
                start_file_time = time.time()
                result = self.load(
                    file_path=file_path,
                    output_format=output_format,
                    extract_images=extract_images,
                    ocr_images=ocr_images,
                    show_progress=show_progress,
                    encoding=encoding,
                    delimiter=delimiter
                )
                file_duration = time.time() - start_file_time

                output_files = []
                if save_files and output_path:
                    output_files = self._save_result(result, output_path, output_format)

                return {
                    'status': 'success',
                    'format': result.metadata.format.value,
                    'content_length': len(result.content) if result.content else 0,
                    'duration': file_duration,
                    'output_files': output_files,
                    'metadata': {
                        'images_extracted': len(result.images) if result.images else 0,
                        'tables_found': len(result.tables) if result.tables else 0,
                        'pages': result.metadata.page_count or 1
                    }
                }
            except Exception as e:
                logger.error(f"❌ Failed to process {file_path}: {e}")
                return {
                    'status': 'failed',
                    'error': str(e),
                    'format': file_path.suffix.lower()
                }

        results, processed_count, error_count = self._execute_batch(
            items=items,
            total_files=total_files,
            process_one=process_one,
            max_workers=max_workers,
            progress_callback=progress_callback,
            show_progress=show_progress,
            start_time=start_time,
        )

        # Final summary
        total_time = time.time() - start_time
        logger.info(f"🏁 Batch processing complete!")
        logger.info(f"📊 Results: {processed_count} succeeded, {error_count} failed")
        logger.info(f"⏱️  Total time: {total_time:.2f}s ({processed_count / total_time:.2f} files/sec)")

        return results

    def batch_process_files(
            self,
            file_paths: List[Union[str, Path]],
            output_dir: Optional[Union[str, Path]] = None,
            output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
            extract_images: bool = False,
            ocr_images: bool = False,
            show_progress: bool = True,
            save_files: bool = True,
            encoding: str = 'utf-8',
            delimiter: Optional[str] = None,
            max_workers: Optional[int] = None,
            progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Batch process a specific list of files.

        Args:
            file_paths: List of file paths to process
            output_dir: Optional output directory (flat; files that would share an output name, such as
                ``q1.pdf`` of two folders, are written as ``q1.pdf.md`` and ``q1.pdf-2.md``)
            output_format: Output format (MARKDOWN, JSON, TEXT)
            extract_images: Whether to extract images from documents (Office/PDF only)
            ocr_images: Whether to perform OCR on images (implies extract_images when an OCR provider is configured)
            show_progress: Whether to show progress messages
            save_files: Whether to save output files
            encoding: Text encoding for text/markup files
            delimiter: CSV delimiter (auto-detect if None)
            max_workers: Optional number of worker threads. When set to a value > 1
                (and more than one file is provided), files are processed
                concurrently with a thread pool. Defaults to None, which preserves
                the original sequential behavior.
            progress_callback: Optional callable invoked as ``(done, total, path)``
                after each file finishes (whether it succeeded or failed). ``path``
                is the string key used in the returned results dict.

        Returns:
            Dictionary mapping input paths to processing results

        Examples:
            ::

                # Process specific files with OCR
                files = ["doc1.pdf", "doc2.docx"]
                loader.batch_process_files(files, extract_images=True, ocr_images=True)

                # Process concurrently with a progress callback
                loader.batch_process_files(files, max_workers=4,
                                           progress_callback=lambda d, t, p: print(f"{d}/{t}"))
        """
        if not file_paths:
            return {}

        file_paths = [Path(p) for p in file_paths]
        output_format = self._normalize_output_format(output_format)
        total_files = len(file_paths)
        start_time = time.time()

        logger.info(f"📄 Starting batch processing of {total_files} files")
        logger.info(f"🖼️  Image processing: extract_images={extract_images}, ocr_images={ocr_images}")

        # Files that would share an output name (q1.pdf of two folders, report.txt and report.md) keep their whole
        # file name (q1.pdf.md, q1.pdf-2.md) instead of overwriting each other
        output_names = None
        suffixes = {OutputFormat.MARKDOWN: (".md",), OutputFormat.JSON: (".json",)}.get(output_format)
        if save_files and output_dir and suffixes:
            output_names = plan_output_names(
                file_paths, {path: PurePath(path.name) for path in file_paths}, Path(output_dir), suffixes)

        # Build the ordered work list and pre-create output directories on the main
        # thread (so concurrent workers never race on mkdir).
        items = []
        for file_path in file_paths:
            if save_files and output_dir:
                output_path = Path(output_dir) / (output_names[file_path] if output_names else file_path.stem)
                output_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                output_path = None
            items.append((str(file_path), file_path, output_path))

        def process_one(file_path: Path, output_path: Optional[Path]) -> Dict[str, Any]:
            try:
                start_file_time = time.time()
                result = self.load(
                    file_path=file_path,
                    output_format=output_format,
                    extract_images=extract_images,
                    ocr_images=ocr_images,
                    show_progress=show_progress,
                    encoding=encoding,
                    delimiter=delimiter
                )
                file_duration = time.time() - start_file_time

                output_files = []
                if save_files and output_path:
                    output_files = self._save_result(result, output_path, output_format)

                return {
                    'status': 'success',
                    'format': result.metadata.format.value,
                    'content_length': len(result.content) if result.content else 0,
                    'duration': file_duration,
                    'output_files': output_files,
                    'metadata': {
                        'images_extracted': len(result.images) if result.images else 0,
                        'tables_found': len(result.tables) if result.tables else 0
                    }
                }
            except Exception as e:
                logger.error(f"❌ Failed to process {file_path}: {e}")
                return {
                    'status': 'failed',
                    'error': str(e),
                    'format': file_path.suffix.lower()
                }

        results, processed_count, error_count = self._execute_batch(
            items=items,
            total_files=total_files,
            process_one=process_one,
            max_workers=max_workers,
            progress_callback=progress_callback,
            show_progress=show_progress,
            start_time=start_time,
        )

        # Final summary
        total_time = time.time() - start_time
        logger.info(f"🏁 Batch processing complete!")
        logger.info(f"📊 Results: {processed_count} succeeded, {error_count} failed")
        logger.info(f"⏱️  Total time: {total_time:.2f}s")

        return results

    def _save_result(
            self,
            result: ProcessedDocument,
            output_path: Path,
            output_format: OutputFormat
    ) -> List[str]:
        """Save processing result to file(s).
        
        Args:
            result: Processing result
            output_path: Base output path (without extension; a dot in the file name is part of it)
            output_format: Output format
            
        Returns:
            List of created file paths
        """
        output_files = []

        if output_format == OutputFormat.MARKDOWN:
            # Save markdown
            md_path = output_path.with_name(f"{output_path.name}.md")
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(result.content)
            output_files.append(str(md_path))

        elif output_format == OutputFormat.JSON:
            # Save JSON
            json_path = output_path.with_name(f"{output_path.name}.json")
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
            output_files.append(str(json_path))

        # Save images if extracted
        if result.images:
            images_dir = output_path.parent / f"{output_path.name}_images"
            images_dir.mkdir(exist_ok=True)

            for i, image_info in enumerate(result.images):
                image_path = images_dir / f"image_{i:03d}.png"
                
                # Handle different image data formats
                image_data = None
                if isinstance(image_info, dict):
                    # Check for different possible keys
                    if 'data' in image_info:
                        image_data = image_info['data']
                    elif 'content' in image_info:
                        # Base64 encoded data
                        import base64
                        image_data = base64.b64decode(image_info['content'])
                elif isinstance(image_info, bytes):
                    image_data = image_info
                elif isinstance(image_info, str):
                    # Assume it's base64 encoded
                    import base64
                    image_data = base64.b64decode(image_info)
                
                if image_data:
                    with open(image_path, 'wb') as f:
                        f.write(image_data)
                    output_files.append(str(image_path))

        return output_files

    def _detect_format(self, file_path: Path, use_mime: bool = False) -> DocumentFormat:
        """Detect document format from file extension or MIME type.
        
        Args:
            file_path: File path
            use_mime: Whether to use MIME type detection
            
        Returns:
            Document format enum
            
        Raises:
            UnsupportedFormatError: If format cannot be detected
        """
        # First try MIME type detection if enabled
        if use_mime:
            try:
                from doc2mark.core.mime_mapper import get_default_mapper
                mapper = get_default_mapper()
                doc_format = mapper.detect_format_from_file(file_path, use_content=False)
                if doc_format:
                    logger.debug(f"Detected format {doc_format} from MIME type for {file_path}")
                    return doc_format
            except Exception as e:
                logger.debug(f"MIME type detection failed: {e}, falling back to extension")
        
        # Fall back to extension-based detection
        extension = file_path.suffix.lower().lstrip('.')

        # Try to match extension to format
        for fmt in DocumentFormat:
            if fmt.value == extension:
                return fmt

        # Special cases
        if extension == 'markdown':
            return DocumentFormat.MARKDOWN
        elif extension == 'htm':
            return DocumentFormat.HTML

        raise UnsupportedFormatError(
            f"Cannot detect format for extension: {extension}"
        )

    @staticmethod
    def _ocr_incomplete(result: ProcessedDocument) -> Optional[str]:
        """Why the document's OCR is not a final answer, or None when it is: images whose OCR
        failed (flagged ``failed`` by the provider, or left without an answer by a failed batch;
        ``metadata.extra["ocr_images"]["failed"]`` and ``["ocr_issues"]["failed"]``), pages
        showing content whose render OCR returned nothing (``unread_pages``), or images the
        provider itself refused or blocked (``["ocr_issues"]["provider_refused"]``: that may not
        last, so the OCR cache keeps it only briefly and ``cache_dir``, which never expires, not
        at all). Such a document is not written to ``cache_dir``. An answer with no text, or a
        "no readable text" statement, is an answer: a blank page or a photo without words gets it
        again on every run."""
        extra = getattr(result.metadata, "extra", None) or {}
        images = extra.get("ocr_images") or {}
        issues = extra.get("ocr_issues") or {}
        failed = max(int(images.get("failed") or 0), int(issues.get("failed") or 0))
        if failed:
            return f"OCR failed on {failed} image(s)"
        if images.get("unread_pages"):
            return f"unread page(s) {images['unread_pages']}: they show content but their OCR returned nothing"
        if issues.get("provider_refused"):
            return f"the OCR provider refused or blocked {issues['provider_refused']} image(s), which may not last"
        return None

    def _get_cached(
            self,
            file_path: Path,
            output_format: OutputFormat,
            options: Optional[Dict[str, Any]] = None
    ) -> Optional[ProcessedDocument]:
        """Get cached result if available.
        
        Args:
            file_path: Original file path
            output_format: Output format
            
        Returns:
            Cached document or None
        """
        if not self.cache_dir:
            return None

        cache_file = self._cache_file_path(file_path, output_format, options or {})
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if payload.get("schema") != DOCUMENT_CACHE_SCHEMA:
                logger.debug(f"Ignoring cached {cache_file.name}: schema {payload.get('schema')!r}")
                return None
            document = self._document_from_cache_dict(payload["document"])
            return self._demote_cached_token_usage(document)
        except Exception as e:
            logger.warning(f"Failed to read cache for {file_path}: {e}")
            return None

    @staticmethod
    def _demote_cached_token_usage(document: ProcessedDocument) -> ProcessedDocument:
        """Strip a document-cache replay of its billable OCR token count.

        A document served from the on-disk ``cache_dir`` spent NO OCR tokens this
        ``load()`` — it is a replay of an earlier run. The earlier run already
        stamped ``metadata.extra['token_usage']`` and was billed for it once, so
        this method renames that key to ``'token_usage_cached'`` on the replay:
        a billing consumer (which reads only ``'token_usage'``) never re-bills a
        cache hit, while the original run's count stays visible for diagnostics.
        Idempotent; a no-op when no usage was stamped."""
        metadata = getattr(document, "metadata", None)
        extra = getattr(metadata, "extra", None)
        if isinstance(extra, dict) and "token_usage" in extra:
            extra["token_usage_cached"] = extra.pop("token_usage")
        return document

    def _cache_result(
            self,
            file_path: Path,
            output_format: OutputFormat,
            result: ProcessedDocument,
            options: Optional[Dict[str, Any]] = None
    ):
        """Cache processing result.
        
        Args:
            file_path: Original file path
            output_format: Output format
            result: Processing result
        """
        if not self.cache_dir:
            return

        cache_file = self._cache_file_path(file_path, output_format, options or {})
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": DOCUMENT_CACHE_SCHEMA,
            "source": str(file_path.resolve()),
            "output_format": output_format.value,
            "document": self._document_to_cache_dict(result),
        }
        tmp_file = cache_file.with_suffix(cache_file.suffix + ".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            tmp_file.replace(cache_file)
        except Exception as e:
            logger.warning(f"Failed to write cache for {file_path}: {e}")
            try:
                tmp_file.unlink(missing_ok=True)
            except OSError:
                pass

    def _cache_file_path(self, file_path: Path, output_format: OutputFormat, options: Dict[str, Any]) -> Path:
        stat = file_path.stat()
        key_payload = {
            "schema": DOCUMENT_CACHE_SCHEMA,
            "path": str(file_path.resolve()),
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "output_format": output_format.value,
            "options": self._json_cache_safe(options),
        }
        cache_key = hashlib.sha256(json.dumps(key_payload, sort_keys=True).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{cache_key}.json"

    @classmethod
    def _json_cache_safe(cls, value: Any) -> Any:
        if isinstance(value, bytes):
            return {"__bytes__": base64.b64encode(value).decode("ascii")}
        if isinstance(value, (DocumentFormat, OutputFormat, OCRProvider, PromptTemplate)):
            return value.value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (datetime.date, datetime.time)):  # e.g. dates in Markdown front matter
            return value.isoformat()
        if isinstance(value, (set, frozenset)):
            return [cls._json_cache_safe(item) for item in sorted(value, key=str)]
        if isinstance(value, dict):
            return {str(key): cls._json_cache_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_cache_safe(item) for item in value]
        return value

    @classmethod
    def _restore_cache_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            if set(value.keys()) == {"__bytes__"}:
                return base64.b64decode(value["__bytes__"].encode("ascii"))
            return {key: cls._restore_cache_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._restore_cache_value(item) for item in value]
        return value

    @classmethod
    def _document_to_cache_dict(cls, result: ProcessedDocument) -> Dict[str, Any]:
        return {
            "content": result.content,
            "metadata": cls._json_cache_safe(result.metadata.__dict__),
            "images": cls._json_cache_safe(result.images),
            "tables": cls._json_cache_safe(result.tables),
            "sections": cls._json_cache_safe(result.sections),
            "json_content": cls._json_cache_safe(result.json_content),
        }

    @classmethod
    def _document_from_cache_dict(cls, payload: Dict[str, Any]) -> ProcessedDocument:
        metadata_dict = cls._restore_cache_value(payload["metadata"])
        metadata_dict["format"] = DocumentFormat(metadata_dict["format"])
        metadata = DocumentMetadata(**metadata_dict)
        return ProcessedDocument(
            content=payload["content"],
            metadata=metadata,
            images=cls._restore_cache_value(payload.get("images")),
            tables=cls._restore_cache_value(payload.get("tables")),
            sections=cls._restore_cache_value(payload.get("sections")),
            json_content=cls._restore_cache_value(payload.get("json_content")),
        )

    @property
    def supported_formats(self) -> List[str]:
        """Get list of supported formats.
        
        Returns:
            List of format extensions
        """
        return [fmt.value for fmt in DocumentFormat]

    def validate_ocr(self) -> bool:
        """Validate OCR provider configuration.
        
        Returns:
            True if OCR is properly configured
        """
        if self.ocr is None:
            return False
        return self.ocr.validate_api_key()

    def set_ocr_provider(
            self,
            provider: Optional[Union[str, OCRProvider, BaseOCR]],
            api_key: Optional[str] = None,
            config: Optional[OCRConfig] = None,
            ocr_cache: Optional[OCRCache] = None
    ):
        """Change OCR provider.
        
        Args:
            provider: New OCR provider
            api_key: API key for provider
            config: OCR configuration
            ocr_cache: Optional OCR cache handler. Reuses existing handler when omitted.
        """
        preserved_options = self._current_ocr_constructor_options()
        if api_key is not None:
            preserved_options["api_key"] = api_key
        if config is not None:
            preserved_options["ocr_config"] = config

        if provider is None or (isinstance(provider, str) and provider.lower() in {"none", "disabled"}):
            self.ocr = None
        elif isinstance(provider, BaseOCR):
            self.ocr = provider
        else:
            self.ocr = self._create_ocr_provider(
                ocr_provider=provider,
                **preserved_options,
            )

        if not hasattr(self, "ocr_cache"):
            self.ocr_cache = None
        self._apply_ocr_cache(ocr_cache)
        from doc2mark.judge import judge_hooks
        self._attach_non_content_judge(judge_hooks(getattr(self, "judge", None))["non_content_judge"])

        # Reinitialize processors with new OCR
        self._initialize_processors()

    def get_ocr_configuration(self) -> Dict[str, Any]:
        """Get current OCR configuration summary.
        
        Returns:
            Dictionary with OCR configuration details
        """
        if self.ocr is None:
            return {
                "provider": None,
                "enabled": False,
                "api_key_configured": False,
                "config": None,
            }
        if hasattr(self.ocr, 'get_configuration_summary'):
            return self.ocr.get_configuration_summary()
        else:
            return {
                "provider": type(self.ocr).__name__,
                "api_key_configured": bool(self.ocr.api_key),
                "config": self.ocr.config.__dict__ if self.ocr.config else None
            }

    def update_ocr_configuration(self, **kwargs):
        """Update OCR configuration dynamically.
        
        Args:
            **kwargs: Configuration parameters to update
            
        Available for OpenAI OCR:
            - model: str
            - temperature: float
            - max_tokens: int
            - max_workers: int
            - prompt_template: str
            - enable_langchain: bool
            - timeout: int
            - max_retries: int
        """
        logger.info("🔧 Updating OCR configuration...")

        if self.ocr is None:
            logger.warning("OCR provider is disabled; no configuration was updated")
            return

        if hasattr(self.ocr, 'update_model_config'):
            # Extract model configuration parameters
            model_params = {}
            prompt_template = None

            for key, value in kwargs.items():
                if key == 'prompt_template':
                    prompt_template = value
                elif key in ['model', 'temperature', 'max_tokens', 'timeout', 'max_retries']:
                    model_params[key] = value
                elif key in ['max_workers', 'enable_langchain']:
                    # These are instance attributes, set directly
                    setattr(self.ocr, key, value)
                    if key == 'max_workers':
                        # The concurrency cap is fixed when the provider builds its client: build
                        # it again on the next request.
                        target = self._unwrap_ocr(self.ocr)
                        if hasattr(target, '_vision_agent'):
                            target._vision_agent = None
                    logger.info(f"✓ Updated {key}: {value}")
                else:
                    # Additional model parameters
                    model_params[key] = value

            # Update model configuration if there are any model parameters
            if model_params:
                self.ocr.update_model_config(**model_params)

            # Update prompt template if specified
            if prompt_template:
                self.ocr.update_prompt_template(prompt_template)

        else:
            logger.warning("⚠️  OCR provider doesn't support dynamic configuration updates")

        # Log updated configuration
        config = self.get_ocr_configuration()
        logger.info("📋 Updated OCR Configuration:")
        for key, value in config.items():
            logger.info(f"   {key}: {value}")

    def get_available_prompt_templates(self) -> Dict[str, str]:
        """Get available prompt templates for OCR.
        
        Returns:
            Dictionary of template names and descriptions
        """
        if self.ocr is None:
            return {}
        if hasattr(self.ocr, 'get_available_prompts'):
            return self.ocr.get_available_prompts()
        else:
            return {"default": "Standard OCR processing"}

    def validate_ocr_setup(self) -> Dict[str, Any]:
        """Validate OCR setup and return status information.
        
        Returns:
            Dictionary with validation results
        """
        logger.info("🔐 Validating OCR setup...")

        if self.ocr is None:
            return {
                "provider": None,
                "enabled": False,
                "api_key_configured": False,
                "api_key_valid": False,
                "configuration": self.get_ocr_configuration(),
                "available_templates": {},
                "errors": [],
            }

        validation_results = {
            "provider": type(self.ocr).__name__,
            "api_key_configured": bool(self.ocr.api_key),
            "api_key_valid": False,
            "configuration": self.get_ocr_configuration(),
            "available_templates": self.get_available_prompt_templates(),
            "errors": []
        }

        try:
            # Validate API key if provider requires it
            if self.ocr.requires_api_key:
                if not self.ocr.api_key:
                    validation_results["errors"].append("API key required but not provided")
                else:
                    validation_results["api_key_valid"] = self.ocr.validate_api_key()
                    if not validation_results["api_key_valid"]:
                        validation_results["errors"].append("API key validation failed")
            else:
                validation_results["api_key_valid"] = True

        except Exception as e:
            validation_results["errors"].append(f"Validation error: {str(e)}")
            logger.error(f"❌ OCR validation failed: {e}")

        # Log validation results
        if validation_results["errors"]:
            logger.warning(f"⚠️  OCR validation issues: {validation_results['errors']}")
        else:
            logger.info("✅ OCR setup validation successful")

        return validation_results
