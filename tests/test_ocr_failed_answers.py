"""A failed OCR answer is not an answer: the LLM providers flag a per-image failure ``metadata["failed"]``
(as Tesseract does), so the caches retry it, while an answer with no text is cached like any other.

PR #21 review round 1 (B1). The E2E suite covers the caches through the public API with Tesseract; the LLM
providers' per-image failures can only be reached with their chains scripted, here.
"""

import io
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image

from doc2mark.core.base import DocumentFormat, DocumentMetadata, ProcessedDocument
from doc2mark.core.loader import UnifiedDocumentLoader
from doc2mark.ocr.base import FAILURE_USAGE_KEY, OCRConfig
from doc2mark.ocr.openai import OpenAIOCR, VisionAgent
from doc2mark.ocr.schema import OCRPage, RawExtraction
from doc2mark.ocr.vertex_ai import VertexAIOCR, VertexAIVisionAgent


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _chain(replies):
    chain = MagicMock()
    chain.batch_as_completed.return_value = list(enumerate(replies))
    return chain


# --- the agents keep the failure --------------------------------------------------------------


def test_openai_agent_flags_a_failed_image():
    agent = VisionAgent.__new__(VisionAgent)
    agent.max_concurrency = None
    agent.structured = True
    ok = SimpleNamespace(content="", usage_metadata={}, response_metadata={}, additional_kwargs={})
    agent._chain = _chain([TimeoutError("read timed out"), {"parsed": OCRPage(), "raw": ok, "parsing_error": None}])

    failed, answered = agent.batch_invoke([{}, {}])

    assert failed["failed"] is True and failed["parsed"] is None and "timed out" in failed["parsing_error"]
    assert not answered.get("failed")


def test_openai_free_form_agent_flags_a_failed_image():
    agent = VisionAgent.__new__(VisionAgent)
    agent.max_concurrency = None
    agent.structured = False
    agent._chain = _chain([RuntimeError("429 rate limited")])

    [(text, usage)] = agent.batch_invoke([{}])

    assert text == "" and "429" in usage[FAILURE_USAGE_KEY]


def test_gemini_agent_flags_a_failed_image():
    agent = VertexAIVisionAgent.__new__(VertexAIVisionAgent)
    agent.structured = True
    agent.max_concurrency = None
    agent._structured_chain = _chain([ConnectionError("reset")])

    [payload] = agent.batch_invoke([{"image_data": "", "prompt": "p"}], structured=True)
    assert payload["failed"] is True and payload["parsed"] is None

    agent._chain = _chain([ConnectionError("reset")])
    [(text, usage)] = agent.batch_invoke([{"image_data": "", "prompt": "p"}], structured=False)
    assert text == "" and "reset" in usage[FAILURE_USAGE_KEY]


# --- the providers turn it into metadata["failed"], unless the recovery answers -----------------


class _ScriptedOpenAIAgent:
    """Stands in for the OpenAI vision agent of one mode; replies are scripted per mode."""

    def __init__(self, structured, replies):
        self.structured = structured
        self.replies = replies

    def batch_invoke(self, input_dicts):
        queue = self.replies["structured" if self.structured else "free_form"]
        return [queue.pop(0) for _ in input_dicts]


def _openai(monkeypatch, structured, free_form):
    replies = {"structured": list(structured), "free_form": list(free_form)}
    ocr = OpenAIOCR(api_key="test-key", config=OCRConfig())
    monkeypatch.setattr("doc2mark.ocr.openai.VisionAgent", lambda **kw: _ScriptedOpenAIAgent(kw["structured"], replies))
    ocr._vision_agent = _ScriptedOpenAIAgent(True, replies)
    return ocr


FAILED_STRUCTURED = {"parsed": None, "parsing_error": "read timed out", "raw": None, "usage": {}, "failed": True}


def test_openai_failed_image_whose_recovery_fails_too_is_flagged_failed(monkeypatch):
    ocr = _openai(monkeypatch, [FAILED_STRUCTURED], [("", {FAILURE_USAGE_KEY: "read timed out"})])

    [result] = ocr.batch_process_images([_png()])

    assert result.text == "" and result.metadata["failed"] is True


def test_openai_failed_image_that_the_recovery_reads_is_an_answer(monkeypatch):
    ocr = _openai(monkeypatch, [FAILED_STRUCTURED], [("Invoice 2026-0917", {})])

    [result] = ocr.batch_process_images([_png()])

    assert result.text == "Invoice 2026-0917" and not result.metadata.get("failed")


def test_openai_failed_image_whose_recovery_answers_nothing_is_an_empty_answer(monkeypatch):
    ocr = _openai(monkeypatch, [FAILED_STRUCTURED], [("", {})])

    [result] = ocr.batch_process_images([_png()])

    assert result.text == "" and not result.metadata.get("failed")


def test_openai_empty_answer_whose_recovery_fails_is_flagged_failed(monkeypatch):
    """The structured call answered nothing and the free-form recovery that would have read the image failed:
    the image was not read, so the next run must ask again."""
    empty = {"parsed": OCRPage(raw=RawExtraction(text="")), "raw": None, "parsing_error": None, "usage": {}}
    ocr = _openai(monkeypatch, [empty], [("", {FAILURE_USAGE_KEY: "503"})])

    [result] = ocr.batch_process_images([_png()])

    assert result.text == "" and result.metadata["failed"] is True


class _ScriptedGeminiAgent:
    def __init__(self, structured, free_form):
        self.structured, self.free_form = list(structured), list(free_form)

    def batch_invoke(self, input_dicts, structured=None):
        replies = self.structured if structured else self.free_form
        return [replies.pop(0) for _ in input_dicts]

    _extract_usage = staticmethod(VertexAIVisionAgent._extract_usage)


def test_gemini_failed_image_is_flagged_failed_unless_the_recovery_reads_it():
    failed = {"parsed": None, "parsing_error": "deadline exceeded", "raw": None, "failed": True}
    ocr = VertexAIOCR(api_key="test-key", config=OCRConfig())
    ocr._vision_agent = _ScriptedGeminiAgent([failed, dict(failed)],
                                             [("", {FAILURE_USAGE_KEY: "deadline exceeded"}), ("Board minutes", {})])

    first, second = ocr.batch_process_images([_png(), _png()])

    assert first.text == "" and first.metadata["failed"] is True
    assert second.text == "Board minutes" and not second.metadata.get("failed")


# --- cache_dir: only real failures keep a document out -------------------------------------------


def _document(extra):
    metadata = DocumentMetadata(filename="d.pdf", format=DocumentFormat.PDF, size_bytes=1)
    metadata.extra = extra
    return ProcessedDocument(content="x", metadata=metadata)


def test_documents_with_answers_without_text_or_refusals_are_cached():
    for extra in ({"ocr_images": {"empty": 3, "failed": 0}},
                  {"ocr_issues": {"refused": 2, "failed": 0, "withheld": 0}},
                  {}):
        assert UnifiedDocumentLoader._ocr_incomplete(_document(extra)) is None, extra


def test_documents_with_a_provider_refusal_are_not_cached():
    """A provider's own refusal or safety block can be transient: the OCR cache keeps it for minutes only, so
    ``cache_dir`` (no expiry) must not keep the document that holds it."""
    reason = UnifiedDocumentLoader._ocr_incomplete(
        _document({"ocr_issues": {"refused": 2, "provider_refused": 1, "failed": 0, "withheld": 0}}))
    assert reason is not None and "refused" in reason


def test_documents_with_failed_or_unread_ocr_are_not_cached():
    assert "failed" in UnifiedDocumentLoader._ocr_incomplete(_document({"ocr_images": {"failed": 1}}))
    assert "unread" in UnifiedDocumentLoader._ocr_incomplete(_document({"ocr_images": {"unread_pages": [2]}}))
    assert "failed" in UnifiedDocumentLoader._ocr_incomplete(_document({"ocr_issues": {"failed": 1, "refused": 0}}))


def test_documents_whose_ocr_request_raised_are_not_cached():
    """An OCR request that raised (no API key, an outage) is a failure even where no image was counted for it
    (``ocr_issues["errors"]`` only): the document was stored in ``cache_dir`` with its failure text."""
    reason = UnifiedDocumentLoader._ocr_incomplete(
        _document({"ocr_issues": {"refused": 0, "failed": 0, "errors": ["RuntimeError: OpenAI OCR requires an API key"]}}))
    assert reason is not None and "API key" in reason
