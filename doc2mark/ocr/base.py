"""Base OCR interface for doc2mark."""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Type, Union

if TYPE_CHECKING:  # avoid a runtime import cycle (schema has no deps on base)
    from pydantic import BaseModel
    from doc2mark.ocr.schema import OCRPage


def resolve_max_concurrency(config_value: Optional[int] = None) -> Optional[int]:
    """Resolve the LLM-OCR batch concurrency cap.

    Precedence: explicit ``config_value`` > ``OCR_MAX_CONCURRENCY`` env var > ``None``.
    ``None`` means "use the LangChain default" (a CPU-tied thread pool, typically ~12),
    preserving the pre-0.5.2 behaviour. A positive int caps how many image OCR calls run
    concurrently in ``batch_as_completed`` — raise it to keep large scanned documents
    within an SLA (e.g. 32 ≈ a few-thousand-page doc in minutes).
    """
    if config_value is not None:
        return config_value
    env = os.getenv("OCR_MAX_CONCURRENCY")
    if env:
        try:
            value = int(env)
            return value if value > 0 else None
        except ValueError:
            return None
    return None


class OCRProvider(Enum):
    """Available OCR providers."""
    OPENAI = "openai"
    VERTEX_AI = "vertex_ai"
    GEMINI = "gemini"  # alias for the Google Generative AI (Gemini) provider
    TESSERACT = "tesseract"


class Task(str, Enum):
    """OCR intent. Replaces the free-form ``PromptTemplate`` variants with a
    small set of intent names that select a schema-aligned instruction
    (see :data:`TASK_PROMPTS`). ``language`` is a config field, not a task, so
    the old ``MULTILINGUAL`` template is dropped."""
    AUTO = "auto"              # general-purpose (was PromptTemplate.DEFAULT)
    TABLE = "table"
    DOCUMENT = "document"
    FORM = "form"
    RECEIPT = "receipt"
    HANDWRITING = "handwriting"
    CODE = "code"


# Short, schema-aligned instructions per task. The structured schema enforces
# *shape*; these prompts enforce the raw-vs-interpretation discipline. Shared by
# the OpenAI and Vertex/Gemini providers.
_RAW_DISCIPLINE = (
    "Transcribe every visible character verbatim into raw.text, preserving the "
    "original language (do not translate). For each table, populate raw.tables: "
    "put a clean, valid HTML table in its `html` field using <table>/<tr>/<th>/<td>, "
    "and MERGE cells with colspan/rowspan exactly as they appear in the image (no "
    "CSS, classes, or inline styles); also fill headers/rows as a best-effort flat "
    "view. Put label/value pairs in raw.fields. Never mix commentary into raw.text — "
    "put analysis only in the interpretation fields."
)

# --- Self-routing AUTO prompt (image job-router) -----------------------------
# Classify-then-act: most images are transcribed verbatim; only a product UI
# mockup with illustrative data (triple-gated) may withhold its sample values,
# and chart/diagram/infographic keep all printed text while describing the
# trend/structure in interpretation. Embeds _RAW_DISCIPLINE as the VERBATIM body.
# (Phase 1: photo/logo/stamp/mixed ride VERBATIM; screenshot is the only route
#  that may withhold printed values.)
_ROUTER_PREAMBLE = (
    "First CLASSIFY this image into exactly ONE type, then APPLY that type's policy "
    "in this same response. Do not write your reasoning; just produce the result.\n\n"
    "MASTER RULE (overrides everything below): Transcribe EVERY legible printed "
    "character into raw.text verbatim, in the original language. Only ONE type — "
    "\"screenshot\" — may omit printed values, and ONLY when ALL THREE hold: "
    "(a) the image is a product/app/dashboard UI with toolbar/nav/tabs/buttons, AND "
    "(b) its data is clearly ILLUSTRATIVE (round or sequential names, evenly spaced "
    "dates, round/repeated amounts, identical repeated %/progress bars, \"Sample\"/\"Demo\"), "
    "AND (c) the neighbor-context indicates a product, marketing, or feature/module "
    "introduction. If any one is missing, transcribe verbatim instead. When unsure, "
    "ALWAYS transcribe verbatim — a dropped real table is unrecoverable.\n\n"
    "POLICIES:\n"
    "- VERBATIM (default — document, table, form, receipt, handwriting, code, photo, "
    "logo, stamp, mixed, other): " + _RAW_DISCIPLINE + "\n"
    "- SCREENSHOT (only when triple-gated above): write only stable text — module/screen "
    "name, section/nav/field/column LABELS, buttons, and the capability message. Leave "
    "raw.tables header-only (column labels + row_count, illustrative=true, no sample "
    "rows); put what the product DOES in interpretation. Set content_fidelity=\"described\".\n"
    "- DESCRIBE (chart, diagram, infographic): keep ALL printed text verbatim (titles, "
    "axis/legend/node/edge labels, printed numbers); never invent or pixel-estimate "
    "values; put the trend/structure/message in interpretation. content_fidelity=\"described\".\n"
    "- SKIP (blank): leave raw empty; content_fidelity=\"skipped\".\n\n"
    "Record the chosen type in interpretation.document_type. A type other than "
    "\"screenshot\" may NEVER omit printed text. A ruled grid of irregular, varied-precision, "
    "or internally-consistent (subtotals that sum) numbers is a REAL table → transcribe it "
    "(table), regardless of any surrounding app chrome. Monospace code/terminal is \"code\" "
    "→ transcribe, never \"screenshot\"."
)

# Appended (after _CONTEXT_PDF_INSTRUCTION) by the providers when a neighbor-page
# PDF context is attached: read neighbors only to judge host-document purpose, and
# gate every non-verbatim policy behind confidence + legibility.
_ROUTER_CONFIDENCE_CLAUSE = (
    "Use the attached neighbor pages ONLY to judge the host document's purpose "
    "(e.g. marketing/module-intro vs financial report) and for terminology; never "
    "transcribe them. The describe and screenshot policies may be used ONLY when your "
    "self_confidence >= 0.7 AND legibility is \"high\". Otherwise, and whenever context "
    "is absent or conflicting, use VERBATIM."
)

TASK_PROMPTS: Dict["Task", str] = {
    Task.AUTO: _ROUTER_PREAMBLE,
    Task.DOCUMENT: (
        "This is a text document. " + _RAW_DISCIPLINE +
        " Preserve headings, lists, and reading order."
    ),
    Task.TABLE: (
        "This image is dominated by tabular data. " + _RAW_DISCIPLINE +
        " Reproduce every table precisely in raw.tables[].html as clean HTML, "
        "preserving merged cells with colspan/rowspan and the exact cell text."
    ),
    Task.FORM: (
        "This is a form. " + _RAW_DISCIPLINE +
        " Extract each form label and its filled value into raw.fields."
    ),
    Task.RECEIPT: (
        "This is a receipt or invoice. " + _RAW_DISCIPLINE +
        " Put merchant, totals, tax, and line items in raw.fields and raw.tables; "
        "summarize the transaction in interpretation."
    ),
    Task.HANDWRITING: (
        "This image contains handwriting. " + _RAW_DISCIPLINE +
        " Transcribe as faithfully as possible and set raw.has_handwriting=true."
    ),
    Task.CODE: (
        "This image contains source code or a terminal. " + _RAW_DISCIPLINE +
        " Preserve indentation and symbols exactly in raw.text."
    ),
}


@dataclass
class OCRResult:
    """Result from OCR processing.

    ``text`` is always populated (rendered from ``document.raw`` when structured
    output is used) for backward compatibility. ``document`` carries the
    structured :class:`~doc2mark.ocr.schema.OCRPage` when available, or ``None``
    for legacy/free-form results and non-LLM providers.
    """
    text: str
    confidence: Optional[float] = None
    language: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    document: Optional["OCRPage"] = None


# Fields that are inert for the LLM providers (OpenAI/Vertex). They are read
# only by Tesseract or by nobody; setting them against an LLM provider does
# nothing and earns a DeprecationWarning. Kept for one minor cycle.
_DEPRECATED_LLM_FIELDS = (
    "enhance_image", "detect_tables", "detect_layout", "timeout", "max_retries", "extra",
)

# Strict instruction appended to the OCR prompt when a neighbor-page PDF is attached
# as context. The image stays the sole transcription target; the PDF is context-only.
_CONTEXT_PDF_INSTRUCTION = (
    "The IMAGE above is the PRIMARY and ONLY target to transcribe. "
    "The attached PDF contains the neighboring pages (previous/current/next) of the "
    "same document and is provided STRICTLY as CONTEXT for terminology, names, and "
    "language continuity. Do NOT transcribe, summarize, or quote the PDF. Do NOT infer "
    "the image's content from the PDF. Transcribe ONLY what is visibly present in the "
    "image, and respond in the document's own language."
)


@dataclass
class OCRConfig:
    """Configuration for OCR processing.

    The live knobs for LLM providers are ``model``, ``task``, ``language``,
    ``max_concurrency``, and the structured-output controls
    (``structured``/``detail``/``response_model``/``on_parse_error``). The
    remaining fields are either Tesseract-only (``enhance_image``,
    ``detect_layout``) or deprecated no-ops kept for backward compatibility
    (see :data:`_DEPRECATED_LLM_FIELDS`).
    """
    # --- live for LLM providers ---
    model: Optional[str] = None                 # provider default when None
    task: "Task" = Task.AUTO
    language: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    base_url: Optional[str] = None              # OpenAI-compatible endpoints
    # Max concurrent image OCR calls for LLM providers (vertex_ai/openai) in
    # batch_as_completed. None = LangChain default (~CPU-tied). Falls back to the
    # OCR_MAX_CONCURRENCY env var when None. Raise for large scanned docs.
    max_concurrency: Optional[int] = None

    # --- structured-output controls ---
    structured: bool = True                                       # structured is the default
    detail: Literal["raw", "full"] = "full"                       # "raw" skips interpretation
    response_model: Optional[Type["BaseModel"]] = None            # BYO schema; None => OCRPage
    on_parse_error: Literal["raw_text", "raise"] = "raw_text"     # graceful degradation control

    # --- neighbor-page PDF context (PDF sources only; provider-gated) ---
    # The window is FIXED at {k-1, k, k+1} (clamped, <=3 pages). This int is a SCOPE
    # TIER, not a window size:
    #   0 = off (default; zero behavior change)
    #   1 = attach context to whole-page renders only  (one upload per image page)
    #   2 = renders + non-decorative embedded images    (opt-in; more uploads)
    # Only Gemini/Vertex consumes it today; other providers accept-and-ignore.
    context_pages: int = 0

    # --- Tesseract-only (inert for LLM providers) ---
    enhance_image: bool = True
    detect_tables: bool = True
    detect_layout: bool = True

    # --- deprecated no-ops kept for one cycle (see _DEPRECATED_LLM_FIELDS) ---
    max_retries: int = 3
    timeout: int = 30
    extra: Optional[Dict[str, Any]] = None

    def deprecated_llm_overrides(self) -> List[str]:
        """Return the names of deprecated/inert fields the user set to a
        non-default value. LLM providers use this to emit a single
        ``DeprecationWarning`` at construction time."""
        defaults = {
            "enhance_image": True, "detect_tables": True, "detect_layout": True,
            "timeout": 30, "max_retries": 3, "extra": None,
        }
        return [f for f in _DEPRECATED_LLM_FIELDS if getattr(self, f) != defaults[f]]


class BaseOCR(ABC):
    """Abstract base class for OCR providers."""

    def __init__(self, api_key: Optional[str] = None, config: Optional[OCRConfig] = None):
        """Initialize OCR provider.
        
        Args:
            api_key: API key for the provider (if required)
            config: OCR configuration options
        """
        self.api_key = api_key
        self.config = config or OCRConfig()

    @abstractmethod
    def batch_process_images(self, images: List[bytes], **kwargs) -> List[OCRResult]:
        """Process multiple images in batch using LangChain.
        
        This is the primary method for OCR processing. All implementations
        must use LangChain for efficient batch processing.
        
        Args:
            images: List of image data as bytes
            **kwargs: Additional provider-specific options
            
        Returns:
            List of OCRResult objects in the same order as input
        """
        pass

    def validate_api_key(self) -> bool:
        """Validate that the API key is set if required.
        
        Returns:
            True if valid, False otherwise
        """
        # Base implementation - providers can override
        return True

    def preprocess_image(self, image_data: bytes) -> bytes:
        """Preprocess image before OCR (optional).
        
        Args:
            image_data: Raw image data
            
        Returns:
            Preprocessed image data
        """
        # Base implementation - no preprocessing
        return image_data

    @property
    def provider_name(self) -> str:
        """Get the provider name."""
        return self.__class__.__name__.replace('OCR', '')

    @property
    def requires_api_key(self) -> bool:
        """Check if this provider requires an API key."""
        # Override in subclasses
        return True


class OCRFactory:
    """Factory for creating OCR providers."""

    _providers: Dict[OCRProvider, type] = {}

    @classmethod
    def register_provider(cls, provider: OCRProvider, provider_class: type):
        """Register an OCR provider.
        
        Args:
            provider: Provider enum value
            provider_class: Provider class type
        """
        cls._providers[provider] = provider_class

    @classmethod
    def create(
            cls,
            provider: Union[OCRProvider, str],
            api_key: Optional[str] = None,
            config: Optional[OCRConfig] = None
    ) -> BaseOCR:
        """Create an OCR provider instance.
        
        Args:
            provider: Provider type or string name
            api_key: API key for the provider
            config: OCR configuration
            
        Returns:
            OCR provider instance
            
        Raises:
            ValueError: If provider is not registered
        """
        if isinstance(provider, str):
            try:
                provider = OCRProvider(provider.lower())
            except ValueError:
                raise ValueError(f"Unknown OCR provider: {provider}")

        if provider not in cls._providers:
            raise ValueError(f"OCR provider {provider.value} is not registered")

        provider_class = cls._providers[provider]
        return provider_class(api_key=api_key, config=config)

    @classmethod
    def list_providers(cls) -> List[str]:
        """List available OCR providers.
        
        Returns:
            List of provider names
        """
        return [p.value for p in cls._providers.keys()]
