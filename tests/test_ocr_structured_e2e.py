"""End-to-end-ish tests of the structured OCR path with a fully mocked LLM.

These are CHARACTERIZATION tests: they pin the *current* behavior of the
structured OCR chain so future refactors can't silently regress it. No network
and no API keys are used — the LangChain chain (the only networked stage) is
replaced by a stub that returns the exact ``{"raw", "parsed", "parsing_error"}``
dict shape that ``with_structured_output(..., include_raw=True)`` yields.

The stub is wired in at the *chain* boundary of a real ``VisionAgent`` instance
(created via ``__new__`` to skip the ChatOpenAI constructor), so the real
``VisionAgent.batch_invoke`` reshaping logic and the real
``OpenAIOCR._results_from_batch`` conversion both run unmodified.
"""

import base64
from types import SimpleNamespace

import pytest

from doc2mark.core.base import OCRError
from doc2mark.ocr import (
    OCR,
    OpenAIOCR,
    OCRConfig,
    OCRResult,
    OCRPage,
    RawExtraction,
    Interpretation,
    Table,
    KeyValue,
    MemoryOCRCache,
    CachedOCR,
)
from doc2mark.ocr.openai import VisionAgent

# A minimal valid 1x1 PNG. ``detect_image_format`` recognizes the magic bytes
# and ``convert_image_to_supported_format`` passes PNG through untouched (no PIL
# needed), so this exercises the real image-prep path in the provider.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


# --------------------------------------------------------------------------- #
# Stub chain: mimics the structured LangChain chain's batch_as_completed.      #
# --------------------------------------------------------------------------- #
class _StubChain:
    """Stand-in for ``RunnableLambda(prepare_prompt) | structured_llm``.

    ``batch_as_completed`` returns ``(index, payload)`` pairs (intentionally in
    reverse order to prove the real ``batch_invoke`` re-sorts by index). Each
    payload is the ``{"raw", "parsed", "parsing_error"}`` dict the structured
    chain produces with ``include_raw=True``.
    """

    def __init__(self, payloads):
        self._payloads = payloads
        self.last_config = "unset"

    def batch_as_completed(self, input_dicts, config=None):
        self.last_config = config
        pairs = [(i, self._payloads[i]) for i in range(len(input_dicts))]
        return list(reversed(pairs))


def _raw_message(content="", usage=None):
    """A stand-in AIMessage: only ``.content`` / ``.usage_metadata`` are read."""
    return SimpleNamespace(content=content, usage_metadata=usage)


def _chain_payload(parsed=None, content="", parsing_error=None, usage=None):
    """The raw chain output shape (pre-reshape)."""
    return {
        "raw": _raw_message(content=content, usage=usage),
        "parsed": parsed,
        "parsing_error": parsing_error,
    }


def _stub_agent(payloads, *, structured=True):
    """Build a real ``VisionAgent`` with its networked chain replaced.

    Bypasses ``__init__`` (which would construct ChatOpenAI) so the real
    ``batch_invoke`` reshaping logic runs against the stub chain.
    """
    agent = VisionAgent.__new__(VisionAgent)
    agent.structured = structured
    agent.max_concurrency = None
    agent._chain = _StubChain(payloads)
    return agent


def _make_page(*, with_interpretation=True):
    """A realistic structured page: verbatim text + a table + analysis."""
    raw = RawExtraction(
        text="Invoice #42\nThanks for your business",
        tables=[
            Table(
                headers=["Item", "Qty", "Price"],
                rows=[["Widget", "2", "$10"], ["Gadget", "1", "$5"]],
            )
        ],
        fields=[KeyValue(label="Total", value="$25")],
        detected_language="en",
    )
    interp = None
    if with_interpretation:
        interp = Interpretation(
            document_type="receipt",
            summary="A small invoice for two line items.",
            key_findings=["Total is $25"],
            self_confidence=0.83,
            legibility="high",
        )
    return OCRPage(raw=raw, interpretation=interp)


def _facade_with_stub(payloads, *, structured=True, **config_kwargs):
    """Build an ``OCR('openai')`` facade with the stub agent injected."""
    ocr = OCR("openai", api_key="test-key", **config_kwargs)
    ocr._provider._vision_agent = _stub_agent(payloads, structured=structured)
    return ocr


# --------------------------------------------------------------------------- #
# 1. Structured happy path: document populated, text == page.to_markdown().    #
# --------------------------------------------------------------------------- #
def test_structured_read_populates_document_raw_and_interpretation():
    page = _make_page()
    ocr = _facade_with_stub([_chain_payload(parsed=page, usage={"total_tokens": 7})])

    results = ocr.read([_PNG_1x1])
    assert len(results) == 1
    res = results[0]

    # document is the structured OCRPage with both halves populated.
    assert isinstance(res.document, OCRPage)
    assert res.document.raw.text == "Invoice #42\nThanks for your business"
    assert res.document.raw.detected_language == "en"
    assert res.document.raw.tables and res.document.raw.tables[0].headers == [
        "Item", "Qty", "Price",
    ]
    assert isinstance(res.document.interpretation, Interpretation)
    assert res.document.interpretation.document_type == "receipt"

    # OCRResult.text is exactly the page's rendered markdown (back-compat view).
    assert res.text == res.document.to_markdown()
    assert "Invoice #42" in res.text
    assert "| Item | Qty | Price |" in res.text

    # confidence/language are pulled from the structured page.
    assert res.confidence == pytest.approx(0.83)
    assert res.language == "en"
    assert res.metadata["structured"] is True
    assert res.metadata["token_usage"] == {"total_tokens": 7}


def test_batch_invoke_reshapes_and_resorts_by_index():
    """The stub returns pairs reversed; real batch_invoke must re-sort + reshape."""
    pages = [_make_page(), _make_page(with_interpretation=False)]
    payloads = [_chain_payload(parsed=p) for p in pages]
    ocr = _facade_with_stub(payloads)

    results = ocr.read([_PNG_1x1, _PNG_1x1])
    # Order is preserved (index 0 has interpretation, index 1 does not).
    assert results[0].document.interpretation is not None
    assert results[1].document.interpretation is None
    assert results[0].metadata["batch_index"] == 0
    assert results[1].metadata["batch_index"] == 1


# --------------------------------------------------------------------------- #
# 2. detail="raw": the (mocked) model returns a raw-only page.                 #
# --------------------------------------------------------------------------- #
def test_detail_raw_yields_interpretation_none():
    # detail="raw" instructs the model to skip interpretation; we mock the
    # model returning a raw-only page and characterize the pass-through:
    # interpretation is None and confidence is therefore None.
    page = _make_page(with_interpretation=False)
    ocr = _facade_with_stub([_chain_payload(parsed=page)], detail="raw")

    res = ocr.read_one(_PNG_1x1, detail="raw")
    assert isinstance(res.document, OCRPage)
    assert res.document.raw.text  # raw still populated
    assert res.document.interpretation is None
    assert res.confidence is None
    assert res.text == res.document.to_markdown()


# --------------------------------------------------------------------------- #
# 3. on_parse_error: "raw_text" degrades gracefully, "raise" raises OCRError.  #
# --------------------------------------------------------------------------- #
def test_on_parse_error_raw_text_builds_raw_only_page():
    # parsed=None (structured parse failed); raw content carries the free-form
    # text. on_parse_error="raw_text" (default) bridges it into a raw-only page.
    payload = _chain_payload(
        parsed=None,
        content="fallback text with ```fence```",
        parsing_error="boom",
    )
    ocr = _facade_with_stub([payload])  # on_parse_error defaults to "raw_text"

    res = ocr.read_one(_PNG_1x1)
    assert isinstance(res.document, OCRPage)
    # triple backticks are collapsed to single by the bridge.
    assert res.document.raw.text == "fallback text with `fence`"
    assert res.document.interpretation is None
    assert res.confidence is None
    assert res.text == res.document.to_markdown()


def test_on_parse_error_raise_raises_ocrerror():
    payload = _chain_payload(parsed=None, content="ignored", parsing_error="bad json")
    ocr = _facade_with_stub([payload], on_parse_error="raise")

    with pytest.raises(OCRError):
        ocr.read_one(_PNG_1x1)


# --------------------------------------------------------------------------- #
# 4. Per-image tasks length mismatch raises ValueError.                        #
# --------------------------------------------------------------------------- #
def test_tasks_length_mismatch_raises_value_error():
    # One image but two per-image tasks -> ValueError from _resolve_task_prompts,
    # surfaced unchanged (not wrapped in OCRError).
    ocr = _facade_with_stub([_chain_payload(parsed=_make_page())])
    with pytest.raises(ValueError):
        ocr.read([_PNG_1x1], tasks=["table", "form"])


def test_matching_tasks_length_is_accepted():
    pages = [_make_page(), _make_page()]
    ocr = _facade_with_stub([_chain_payload(parsed=p) for p in pages])
    results = ocr.read([_PNG_1x1, _PNG_1x1], tasks=["table", "document"])
    assert len(results) == 2
    assert all(isinstance(r.document, OCRPage) for r in results)


# --------------------------------------------------------------------------- #
# 5. Facade read_one flows a single document through.                         #
# --------------------------------------------------------------------------- #
def test_facade_read_one_flows_document_through():
    page = _make_page()
    ocr = _facade_with_stub([_chain_payload(parsed=page)])
    res = ocr.read_one(_PNG_1x1)
    assert isinstance(res, OCRResult)
    assert isinstance(res.document, OCRPage)
    assert res.document.raw.text == page.raw.text


# --------------------------------------------------------------------------- #
# 6. OCR cache round-trips the structured document via the public API.         #
# --------------------------------------------------------------------------- #
def test_memory_cache_round_trips_structured_document():
    provider = OpenAIOCR(api_key="test-key", config=OCRConfig())
    provider._vision_agent = _stub_agent([_chain_payload(parsed=_make_page())])

    cache = MemoryOCRCache()
    cached = CachedOCR(provider, cache)

    first = cached.batch_process_images([_PNG_1x1])
    second = cached.batch_process_images([_PNG_1x1])

    # Both calls yield an equivalent structured document...
    assert isinstance(first[0].document, OCRPage)
    assert isinstance(second[0].document, OCRPage)
    assert second[0].document.model_dump() == first[0].document.model_dump()
    assert second[0].text == first[0].text

    # ...but the cache returns deep copies, not shared references.
    assert second[0].document is not first[0].document

    # The second read was served from the cache (a hit).
    stats = cache.stats()
    assert stats["hits"] >= 1
    assert stats["sets"] >= 1


def test_cache_miss_then_hit_only_calls_provider_once():
    provider = OpenAIOCR(api_key="test-key", config=OCRConfig())
    chain = _StubChain([_chain_payload(parsed=_make_page())])
    agent = VisionAgent.__new__(VisionAgent)
    agent.structured = True
    agent.max_concurrency = None
    agent._chain = chain
    provider._vision_agent = agent

    cache = MemoryOCRCache()
    cached = CachedOCR(provider, cache)

    cached.batch_process_images([_PNG_1x1])
    # The chain ran on the first (miss) call: last_config moved off the sentinel
    # (max_concurrency is None, so the real config passed to the chain is None).
    assert chain.last_config is None

    chain.last_config = "unset"
    cached.batch_process_images([_PNG_1x1])
    # The second call was a cache hit, so the chain was NOT invoked again.
    assert chain.last_config == "unset"
