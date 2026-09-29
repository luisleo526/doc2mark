"""Document-level OCR token-usage aggregation.

Every LLM OCR call carries per-image token usage in ``OCRResult.metadata``
(``token_usage`` — a LangChain ``usage_metadata`` dict with ``input_tokens`` /
``output_tokens`` / ``total_tokens``). Individual results are consumed deep in
the format pipelines (only ``.text`` survives), so the counts never reach the
final :class:`~doc2mark.core.base.ProcessedDocument`.

:class:`UsageAggregatingOCR` is a thin :class:`~doc2mark.ocr.base.BaseOCR`
wrapper (same shape as :class:`~doc2mark.ocr.cache.CachedOCR`) that the loader
puts around the shared OCR instance handed to *every* processor. It sums the
usage of each result flowing back during one ``load()`` — across every provider
and source family — into a single dict the loader stamps onto
``ProcessedDocument.metadata.extra["token_usage"]``. Zero usage (OCR off, or a
usage-less provider like Tesseract) leaves the running total empty so the loader
can leave ``extra`` untouched.

The same wrapper also keeps the per-load OCR *issues* (:meth:`UsageAggregatingOCR.
pop_document_issues`): images whose answer was only a refusal (emitted empty),
images that failed, images whose withheld values the router firewall could not
recover, and an engine that could not run at all (``OCREngineError``) -- which the
loader re-raises instead of returning a placeholder-only document.
"""

import threading
from typing import Any, Dict, Iterable, List, Optional

from doc2mark.ocr.base import BaseOCR, OCREngineError, OCRResult
from doc2mark.ocr.cache import FROM_CACHE_METADATA_KEY

# Distinct error messages kept per document in ``ocr_issues["errors"]``.
_MAX_ISSUE_ERRORS = 5

# Read-side key aliases: the LLM providers emit LangChain's ``usage_metadata``
# (input_tokens/output_tokens/total_tokens), but be defensive about the common
# OpenAI-style (prompt/completion) and camelCase spellings so a provider tweak
# never silently drops the count. The WRITE side is always the canonical
# input_tokens/output_tokens/total_tokens triple the backend consumes.
_INPUT_TOKEN_KEYS = ("input_tokens", "prompt_tokens", "inputTokens", "promptTokens")
_OUTPUT_TOKEN_KEYS = ("output_tokens", "completion_tokens", "outputTokens", "completionTokens")
_TOTAL_TOKEN_KEYS = ("total_tokens", "totalTokens")


def new_usage_sink() -> Dict[str, int]:
    """Return a fresh zeroed accumulator with the canonical output keys."""
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _coerce_int(value: Any) -> int:
    """Best-effort non-negative-safe int from a usage value (``bool`` excluded —
    it is an ``int`` subclass but never a real token count)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _first_present(usage: Dict[str, Any], keys: Iterable[str]) -> int:
    """First key present in ``usage`` (in ``keys`` order), coerced to int."""
    for key in keys:
        if key in usage:
            return _coerce_int(usage[key])
    return 0


def new_issue_sink() -> Dict[str, Any]:
    """A fresh per-document OCR issue record (see ``pop_document_issues``)."""
    return {"refused": 0, "failed": 0, "withheld": 0, "errors": [], "engine_error": None}


def merge_usage_into(sink: Dict[str, int], usage: Optional[Dict[str, Any]]) -> None:
    """Fold one ``token_usage`` dict into ``sink`` in place.

    Missing/None/empty usage is a no-op. ``total_tokens`` is taken as reported
    when present, else derived as ``input + output`` (some payloads report only a
    total, e.g. ``{"total_tokens": 7}``, and others only the split).
    """
    if not isinstance(usage, dict) or not usage:
        return
    inp = _first_present(usage, _INPUT_TOKEN_KEYS)
    out = _first_present(usage, _OUTPUT_TOKEN_KEYS)
    total = _first_present(usage, _TOTAL_TOKEN_KEYS) or (inp + out)
    sink["input_tokens"] += inp
    sink["output_tokens"] += out
    sink["total_tokens"] += total


class UsageAggregatingOCR(BaseOCR):
    """OCR wrapper that sums the token usage of every result it passes through.

    Delegates transparently to ``wrapped`` (mirrors :class:`CachedOCR`), so it is
    indistinguishable from the underlying provider to the pipelines. Accumulation
    is scoped per ``load()`` via a thread-local sink: :meth:`begin_document_usage`
    starts a fresh count, :meth:`pop_document_usage` returns and clears it. The
    thread-local keeps concurrent ``load()`` calls (loader batch processing runs
    them on a thread pool) from cross-contaminating each other.
    """

    def __init__(self, wrapped: BaseOCR):
        object.__setattr__(self, "wrapped", wrapped)
        object.__setattr__(self, "_usage_local", threading.local())

    # --- transparent delegation (same contract as CachedOCR) ----------------
    @property
    def api_key(self) -> Optional[str]:
        return getattr(self.wrapped, "api_key", None)

    @api_key.setter
    def api_key(self, value: Optional[str]) -> None:
        setattr(self.wrapped, "api_key", value)

    @property
    def config(self) -> Any:
        return getattr(self.wrapped, "config", None)

    @config.setter
    def config(self, value: Any) -> None:
        setattr(self.wrapped, "config", value)

    @property
    def provider_name(self) -> str:
        return getattr(self.wrapped, "provider_name", type(self.wrapped).__name__)

    @property
    def requires_api_key(self) -> bool:
        return getattr(self.wrapped, "requires_api_key", True)

    def __getattr__(self, name: str) -> Any:
        if name == "wrapped":
            raise AttributeError(name)
        wrapped = self.__dict__.get("wrapped")
        if wrapped is None:
            raise AttributeError(name)
        return getattr(wrapped, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "wrapped" or name.startswith("_"):
            object.__setattr__(self, name, value)
        elif hasattr(self.wrapped, name):
            setattr(self.wrapped, name, value)
        else:
            object.__setattr__(self, name, value)

    def validate_api_key(self) -> bool:
        return self.wrapped.validate_api_key()

    def get_configuration_summary(self) -> Dict[str, Any]:
        if hasattr(self.wrapped, "get_configuration_summary"):
            return self.wrapped.get_configuration_summary()
        return {
            "provider": type(self.wrapped).__name__,
            "api_key_configured": bool(getattr(self.wrapped, "api_key", None)),
            "config": getattr(self.wrapped, "config", None),
        }

    def preprocess_image(self, image_data: bytes) -> bytes:
        return self.wrapped.preprocess_image(image_data)

    # --- per-load accumulation ----------------------------------------------
    def begin_document_usage(self) -> None:
        """Start (or reset) the token-usage and OCR-issue accumulators for the current thread."""
        self._usage_local.sink = new_usage_sink()
        self._usage_local.issues = new_issue_sink()

    def pop_document_issues(self) -> Optional[Dict[str, Any]]:
        """Return this load's OCR issues and clear them, or ``None`` when there were none.

        The dict counts images whose answer was only a refusal / "no readable text"
        statement (``refused``, emitted as empty text), images that failed
        (``failed``) and images that still withhold values after the router
        firewall's verbatim redo (``withheld``), plus up to five distinct error
        messages (``errors``).

        Raises:
            OCREngineError: the OCR engine could not run at all during this load
                (e.g. Tesseract without its language data). The pipelines degrade
                such a failure to placeholders; the loader must not report that as
                a successful conversion.
        """
        issues = getattr(self._usage_local, "issues", None)
        self._usage_local.issues = None
        if not issues:
            return None
        if issues["engine_error"] is not None:
            raise issues["engine_error"]
        issues.pop("engine_error")
        if not (issues["refused"] or issues["failed"] or issues["withheld"] or issues["errors"]):
            return None
        return issues

    def _note_error(self, exc: BaseException) -> None:
        issues = getattr(self._usage_local, "issues", None)
        if issues is None:
            return
        if isinstance(exc, OCREngineError):
            if issues["engine_error"] is None:
                issues["engine_error"] = exc
            return
        message = f"{type(exc).__name__}: {exc}"
        if message not in issues["errors"] and len(issues["errors"]) < _MAX_ISSUE_ERRORS:
            issues["errors"].append(message)

    def pop_document_usage(self) -> Optional[Dict[str, int]]:
        """Return the accumulated usage and clear it, or ``None`` when no OCR
        result reported any tokens (OCR off, or a usage-less provider)."""
        sink = getattr(self._usage_local, "sink", None)
        self._usage_local.sink = None
        if not sink:
            return None
        if sink["input_tokens"] or sink["output_tokens"] or sink["total_tokens"]:
            return sink
        return None

    def _record(self, results: Iterable[Any]) -> None:
        """Fold the usage of each *fresh* result into the active sink, and count the
        OCR issues every result's metadata reports (``ocr_refusal``, ``failed``,
        ``router_fallback="unresolved"``) into the active issue record.

        Results that :class:`CachedOCR` served from a cache hit or an intra-batch
        dedup fan-out carry the ``FROM_CACHE_METADATA_KEY`` flag — they represent
        NO fresh provider spend this ``load()``, so they are skipped. Counting
        them would double-bill the consumer of
        ``ProcessedDocument.metadata.extra['token_usage']`` for tokens that were
        never spent (cache hit) or spent only once (dedup). No-op when no sink is
        active."""
        sink = getattr(self._usage_local, "sink", None)
        issues = getattr(self._usage_local, "issues", None)
        for result in results:
            metadata = getattr(result, "metadata", None)
            if not isinstance(metadata, dict):
                continue
            if issues is not None:
                if metadata.get("ocr_refusal"):
                    issues["refused"] += 1
                if metadata.get("failed"):
                    issues["failed"] += 1
                    error = metadata.get("error")
                    if error and error not in issues["errors"] and len(issues["errors"]) < _MAX_ISSUE_ERRORS:
                        issues["errors"].append(str(error))
                if metadata.get("router_fallback") == "unresolved":
                    issues["withheld"] += 1
            if sink is None or metadata.get(FROM_CACHE_METADATA_KEY):
                continue
            merge_usage_into(sink, metadata.get("token_usage"))

    # --- intercepted OCR calls ----------------------------------------------
    def batch_process_images(self, images: List[bytes], **kwargs) -> List[OCRResult]:
        try:
            results = self.wrapped.batch_process_images(images, **kwargs)
        except Exception as exc:
            self._note_error(exc)
            raise
        self._record(results)
        return results

    def process_image(self, image: bytes, **kwargs) -> OCRResult:
        # Prefer the wrapped provider's own single-image method when it has one
        # (CachedOCR, test doubles); otherwise route through batch_process_images
        # the way CachedOCR does, since the LLM providers expose only the batch API.
        wrapped_process = getattr(self.wrapped, "process_image", None)
        try:
            if callable(wrapped_process):
                result = wrapped_process(image, **kwargs)
                self._record([result] if result is not None else [])
                return result
            results = self.wrapped.batch_process_images([image], **kwargs)
        except Exception as exc:
            self._note_error(exc)
            raise
        self._record(results)
        return results[0] if results else OCRResult(text="")
