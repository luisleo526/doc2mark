"""Base OCR interface for doc2mark."""

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional, Type, Union

from doc2mark.core.base import OCRError

if TYPE_CHECKING:  # avoid a runtime import cycle (schema has no deps on base)
    from pydantic import BaseModel
    from doc2mark.ocr.schema import OCRPage

logger = logging.getLogger(__name__)


# Key under which the vision agents report a provider refusal or safety block of a
# free-form answer in its usage dict (the per-item channel of their public
# ``(text, usage)`` return shape); the providers turn it into ``ocr_refusal``.
REFUSAL_USAGE_KEY = "doc2mark_refusal"


class OCREngineError(OCRError):
    """The OCR engine itself cannot run: not installed, language data missing, a
    broken ``TESSDATA_PREFIX``. Unlike a failure on one image it affects every image,
    so the document loader re-raises it instead of emitting placeholder text."""


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
# Part of the router prompt on every auto-routed request, and appended again (after
# _CONTEXT_PDF_INSTRUCTION) when a neighbor-page PDF is attached: neighbors are read
# only to judge host-document purpose, every non-verbatim policy is gated behind
# confidence + legibility, and context absent -> VERBATIM.
_ROUTER_CONFIDENCE_CLAUSE = (
    "When neighbor pages are attached, use them ONLY to judge the host document's purpose "
    "(e.g. marketing/module-intro vs financial report) and for terminology; never "
    "transcribe them. The describe and screenshot policies may be used ONLY when your "
    "self_confidence >= 0.7 AND legibility is \"high\". Otherwise, and whenever context "
    "is absent or conflicting, use VERBATIM."
)

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
    "→ transcribe, never \"screenshot\".\n\n" + _ROUTER_CONFIDENCE_CLAUSE
)


# Sent with every auto-routed request that carries NO neighbor-page PDF: context is
# absent, so the router's third gate cannot hold and nothing may be withheld. The
# providers also enforce this on the result (schema.withholding_violations).
_ROUTER_NO_CONTEXT_CLAUSE = (
    "No neighbor-page context is attached to this image, so context is absent: use "
    "VERBATIM. Transcribe every legible printed character, every table row and every "
    "value; never leave a table header-only and never mark anything illustrative. The "
    "describe policy may only add interpretation on top of a complete verbatim transcription."
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

# Appended (image-strategy whole-page renders only) so the model ALSO emits a clean,
# structured Markdown rendering of the page in interpretation.page_markdown — turning
# a flat OCR dump into a readable document, WITHOUT losing any verbatim text.
_SYNTHESIS_MARKDOWN_INSTRUCTION = (
    "\n\nALSO fill interpretation.page_markdown — a clean, well-structured Markdown "
    "rendering of THIS whole page (for a human reader and a RAG index):\n"
    "1. COVERAGE (critical): every word, number and character you put in raw.text MUST "
    "also appear in page_markdown — drop nothing, paraphrase nothing, translate nothing. "
    "You are RE-LAYING-OUT the same text into a document, not summarizing it. Include "
    "EVERY visible label: card sub-bullets, the small labels inside each diagram node, "
    "legend entries, axis ticks, footnotes — render them as nested bullets if needed. "
    "Completeness beats brevity.\n"
    "2. STRUCTURE: '## ' for the page/slide title; '### ' for sub-sections; numbered or "
    "bulleted lists for cards / bullet groups (keep each card's body text under its "
    "heading); render a process or flow diagram as an arrow chain 'A → B → C' and list "
    "each node's inner labels beneath it; follow natural reading order (top-to-bottom, "
    "left-to-right).\n"
    "3. TABLES: for any table/grid region write ONLY a short '[see table]' placeholder — "
    "the authoritative HTML table is already in raw.tables; do not re-transcribe it here.\n"
    "4. Leave page_markdown null ONLY for a blank page."
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

    # --- optional judge for refusal / "no readable text" answers (LLM providers) ---
    # ``judge(ocr_text) -> Optional[float]``: probability that a short OCR answer is
    # ONLY a refusal, apology, error or "no readable text" statement. Consulted when
    # the deterministic check does not fire; None (the default) or a None answer keeps
    # the text. Full contract: doc2mark.ocr.refusal.NonContentJudge.
    non_content_judge: Optional[Callable[[str], Optional[float]]] = None

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

    @staticmethod
    def _is_empty_structured(result: OCRResult) -> bool:
        """A structured result with no usable content (some models/images cannot
        fill the json_schema and return an empty OCRPage). Shared by all providers."""
        if result.text and result.text.strip():
            return False
        doc = result.document
        if doc is None:
            return True
        raw = doc.raw
        return not (raw.text.strip() or raw.tables or raw.fields)

    # --- refusals and "no readable text" answers (shared by the LLM providers) ---
    def _non_content_judge(self) -> Optional[Callable[[str], Optional[float]]]:
        return getattr(self.config, "non_content_judge", None) if self.config is not None else None

    @staticmethod
    def _without_content(result: OCRResult, **flags: Any) -> OCRResult:
        """``result`` with no text and an empty page, plus metadata ``flags``."""
        from doc2mark.ocr.schema import OCRPage
        meta = dict(result.metadata or {})
        meta.update(flags)
        return replace(result, text="", document=OCRPage(), metadata=meta)

    @staticmethod
    def _has_content_besides_text(page: Any) -> bool:
        """Whether a structured page carries anything besides ``raw.text``: tables,
        fields, headings, metrics, dates, figures, sections, entities, relations,
        definitions, findings or action items, or a title, summary, message, visual note
        or page Markdown that is not itself a refusal / "no readable text" statement.
        Such a page is content whatever its ``raw.text`` says."""
        from doc2mark.ocr.refusal import matches_non_content_pattern
        raw = page.raw
        if raw.tables or raw.fields or raw.headings or raw.metrics or raw.dates:
            return True
        interp = page.interpretation
        if interp is None:
            return False
        if (interp.figures or interp.sections or interp.typed_entities or interp.relations
                or interp.definitions or interp.key_findings or interp.action_items):
            return True
        texts = (interp.page_title, interp.summary, interp.primary_message, interp.visual_notes, interp.page_markdown)
        return any((text or "").strip() and not matches_non_content_pattern(text) for text in texts)

    def _screen_structured_answers(self, results: List[OCRResult]) -> None:
        """Empty (in place) every structured answer that is only a refusal or a "no
        readable text" statement, so the empty-result recovery re-reads that image.

        Provider-native refusals are emptied by the providers themselves; this is the
        deterministic multilingual check plus the optional ``non_content_judge`` (see
        :mod:`doc2mark.ocr.refusal`), on ``raw.text`` as the model wrote it. Only an
        otherwise empty page can be one: a page with anything else (see
        :meth:`_has_content_besides_text`) is content.
        """
        from doc2mark.ocr.refusal import non_content_reason
        from doc2mark.ocr.schema import OCRPage
        judge = self._non_content_judge()
        for index, result in enumerate(results):
            doc = result.document
            if isinstance(doc, OCRPage):
                if self._has_content_besides_text(doc):
                    continue
                answer = doc.raw.text
            else:  # a caller's own response model: its rendering is all there is
                answer = result.text
            reason = non_content_reason(answer or "", judge)
            if reason:
                results[index] = self._without_content(result, non_content=reason)

    def _free_form_answer(self, answer: Optional[str], *, refusal: Any = None,
                          recovery: bool = False) -> "tuple[str, Dict[str, Any]]":
        """The ``OCRResult.text`` and metadata flags of one free-form answer.

        A provider refusal or block (``refusal``) leaves no text. A recovery call's
        answer is returned as written (the recovery screens and sanitizes it). Any other
        answer that is only a refusal / "no readable text" statement -- judged on the
        answer as the model wrote it, not on its escaped rendering -- becomes empty text
        flagged ``ocr_refusal`` (a free-form answer has no recovery behind it); the rest
        is model Markdown, sanitized once here.
        """
        from doc2mark.ocr.refusal import non_content_reason
        from doc2mark.ocr.schema import _sanitize_markdown
        if refusal:
            return "", {"refusal": refusal, "non_content": "provider_refusal", "ocr_refusal": True}
        answer = answer or ""
        if recovery:
            return answer, {}
        reason = non_content_reason(answer, self._non_content_judge())
        if reason:
            return "", {"non_content": reason, "ocr_refusal": True}
        return _sanitize_markdown(answer), {}

    def _apply_recovered(
        self,
        results: List[OCRResult],
        empty_idx: List[int],
        recovered: List[OCRResult],
    ) -> List[OCRResult]:
        """Merge free-form-recovered text back into the empty structured results
        (in place), tagging them ``structured_fallback='free_form'``. Shared by the
        OpenAI and Vertex recovery paths; the provider-specific part is only the
        free-form re-OCR call that produces ``recovered``.

        ``recovered`` holds the raw free-form answers (the recovery call passes
        ``_recovery=True`` so they are not sanitized yet): the verbatim answer goes to
        ``document.raw.text`` and its sanitized Markdown to ``text``. A recovered
        answer that is itself only a refusal / "no readable text" statement is not
        applied. When either answer was one, the result stays empty and is flagged
        ``metadata["ocr_refusal"] = True``, so a refusal is never indexed as page
        content -- unless the page carries other content (headings, dates, ...), which
        is kept as it is.
        """
        from doc2mark.ocr.refusal import non_content_reason
        from doc2mark.ocr.schema import OCRPage, RawExtraction, _sanitize_markdown
        judge = self._non_content_judge()
        for j, i in enumerate(empty_idx):
            text = (recovered[j].text or "").strip()
            recovered_refused = bool((recovered[j].metadata or {}).get("ocr_refusal"))
            if text and not recovered_refused and non_content_reason(text, judge):
                recovered_refused, text = True, ""
            if not text:
                doc = results[i].document
                if isinstance(doc, OCRPage) and self._has_content_besides_text(doc):
                    continue
                if recovered_refused or (results[i].metadata or {}).get("non_content"):
                    results[i] = self._without_content(results[i], ocr_refusal=True)
                continue
            doc = results[i].document
            if doc is not None:
                doc.raw.text = text
            else:
                doc = OCRPage(raw=RawExtraction(text=text))
            meta = dict(results[i].metadata or {})
            meta["structured_fallback"] = "free_form"
            meta.pop("non_content", None)
            results[i] = OCRResult(
                text=_sanitize_markdown(text),
                confidence=results[i].confidence,
                language=results[i].language,
                metadata=meta,
                document=doc,
            )
        return results

    # --- runtime router firewall (shared by the LLM providers) ---
    def _enforce_router_firewall(
        self,
        results: List[OCRResult],
        redo_verbatim: Callable[[List[int]], List[OCRResult]],
    ) -> List[OCRResult]:
        """Runtime router firewall: redo, with an explicit verbatim task, every
        structured result that withholds printed values against the router policy.

        The result builders run the withholding subset of ``router_invariants``
        (:func:`doc2mark.ocr.schema.withholding_violations`, e.g. an illustrative
        table on a ``document_type="table"`` page, or any withholding while no
        neighbor-page context was attached) and record what they find in
        ``metadata["router_violations"]``. ``redo_verbatim(indices)`` re-OCRs those
        images and returns one result per index. A clean redo replaces the result
        (``metadata["router_fallback"] = "verbatim"``); otherwise the original stays,
        flagged ``"unresolved"``, and its Markdown carries the visible
        ``[N illustrative rows not transcribed]`` marker. Either way the violations
        stay in ``metadata["router_violations"]`` and the token usage of both calls
        is kept.
        """
        from doc2mark.ocr.schema import OCRPage
        indices = [i for i, r in enumerate(results) if (r.metadata or {}).get("router_violations")]
        if not indices:
            return results
        first = results[indices[0]].metadata["router_violations"]
        logger.warning(
            f"Router firewall: {len(indices)}/{len(results)} OCR result(s) withheld printed values "
            f"({'; '.join(first)}); redoing them verbatim"
        )
        try:
            redone: Optional[List[OCRResult]] = redo_verbatim(indices)
        except OCRError:
            if getattr(self.config, "on_parse_error", "raw_text") == "raise":
                raise  # the caller asked for parse failures to surface
            logger.warning("Router firewall: verbatim redo failed; keeping the flagged results")
            redone = None
        except Exception as exc:  # keep the original results, flagged
            logger.warning(f"Router firewall: verbatim redo failed: {exc}")
            redone = None
        for position, index in enumerate(indices):
            original = results[index]
            candidate = redone[position] if redone is not None and position < len(redone) else None
            candidate_meta = (candidate.metadata or {}) if candidate is not None else {}
            clean = (
                candidate is not None
                and isinstance(candidate.document, OCRPage)
                and not self._is_empty_structured(candidate)
                and not candidate_meta.get("ocr_refusal")
                and not candidate_meta.get("router_violations")
            )
            chosen = candidate if clean else original
            meta = dict(chosen.metadata or {})
            meta.update(
                router_violations=original.metadata["router_violations"],
                router_fallback="verbatim" if clean else "unresolved",
                token_usage=_sum_token_usage(original.metadata.get("token_usage"), candidate_meta.get("token_usage")),
            )
            if "batch_index" in original.metadata:
                meta["batch_index"] = original.metadata["batch_index"]
            results[index] = replace(chosen, metadata=meta)
        return results

    @property
    def provider_name(self) -> str:
        """Get the provider name."""
        return self.__class__.__name__.replace('OCR', '')

    @property
    def requires_api_key(self) -> bool:
        """Check if this provider requires an API key."""
        # Override in subclasses
        return True


def _sum_token_usage(first: Any, second: Any) -> Dict[str, Any]:
    """Two ``token_usage`` dicts added up key by key (numbers only; other values of
    ``first`` win), so a result built from two provider calls bills both."""
    if not isinstance(first, dict) or not first:
        return dict(second) if isinstance(second, dict) else {}
    total = dict(first)
    if isinstance(second, dict):
        for key, value in second.items():
            current = total.get(key)
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            if numeric and isinstance(current, (int, float)) and not isinstance(current, bool):
                total[key] = current + value
            elif key not in total:
                total[key] = value
    return total


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
