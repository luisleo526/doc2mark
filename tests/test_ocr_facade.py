"""Tests for the user-facing :class:`doc2mark.ocr.OCR` facade (Wave D)."""

import pytest

from doc2mark.ocr import OCR
from doc2mark.ocr.base import OCRResult, Task
from doc2mark.ocr.schema import Interpretation, OCRPage, RawExtraction


class _FakeVisionAgent:
    """Stand-in for the LangChain VisionAgent used by OpenAIOCR.

    Records the input dicts it is given and returns structured payloads in the
    ``{"parsed", "parsing_error", "raw", "usage"}`` shape that the structured
    path of ``_results_from_batch`` expects.
    """

    last_instance = None

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.structured = kwargs.get("structured", False)
        self.batch_calls = []
        type(self).last_instance = self

    def batch_invoke(self, input_dicts):
        self.batch_calls.append(input_dicts)
        out = []
        for _ in input_dicts:
            page = OCRPage(
                raw=RawExtraction(text="hello world", detected_language="en"),
                interpretation=Interpretation(
                    document_type="document",
                    summary="A greeting.",
                    self_confidence=0.9,
                ),
            )
            out.append({
                "parsed": page,
                "parsing_error": None,
                "raw": None,
                "usage": {},
            })
        return out


def test_build_with_api_key():
    ocr = OCR("openai", api_key="x")
    assert ocr.config.task is Task.AUTO
    # provider should be the registered OpenAI provider
    assert ocr._provider.__class__.__name__ == "OpenAIOCR"


def test_string_task_coerced_to_enum():
    ocr = OCR("openai", task="receipt", api_key="x")
    assert ocr.config.task is Task.RECEIPT


def test_enum_task_accepted():
    ocr = OCR("openai", task=Task.TABLE, api_key="x")
    assert ocr.config.task is Task.TABLE


def test_unknown_task_raises_value_error():
    with pytest.raises(ValueError) as excinfo:
        OCR("openai", task="not-a-task", api_key="x")
    assert "not-a-task" in str(excinfo.value)


def test_bad_detail_raises_value_error():
    with pytest.raises(ValueError):
        OCR("openai", detail="medium", api_key="x")


def test_provider_accepts_enum():
    from doc2mark.ocr.base import OCRProvider

    ocr = OCR(OCRProvider.OPENAI, api_key="x")
    assert ocr._provider.__class__.__name__ == "OpenAIOCR"


def test_read_delegates_to_batch_process_images(monkeypatch):
    import doc2mark.ocr.openai as openai_mod

    monkeypatch.setattr(openai_mod, "VisionAgent", _FakeVisionAgent)
    monkeypatch.setattr(openai_mod, "LANGCHAIN_AVAILABLE", True)

    ocr = OCR("openai", api_key="x")
    results = ocr.read([b"fake-image-bytes"])

    assert isinstance(results, list)
    assert len(results) == 1
    result = results[0]
    assert isinstance(result, OCRResult)
    # The structured page must flow through to .document
    assert isinstance(result.document, OCRPage)
    assert result.document.raw.text == "hello world"
    assert result.document.interpretation.summary == "A greeting."
    # back-compat .text rendered from the page
    assert "hello world" in result.text
    assert result.language == "en"


def test_read_one_returns_single_result(monkeypatch):
    import doc2mark.ocr.openai as openai_mod

    monkeypatch.setattr(openai_mod, "VisionAgent", _FakeVisionAgent)
    monkeypatch.setattr(openai_mod, "LANGCHAIN_AVAILABLE", True)

    ocr = OCR("openai", api_key="x")
    result = ocr.read_one(b"fake-image-bytes")

    assert isinstance(result, OCRResult)
    assert isinstance(result.document, OCRPage)
    assert result.document.raw.text == "hello world"


def test_top_level_public_imports():
    from doc2mark import OCR as TopOCR
    from doc2mark import OCRPage as TopOCRPage
    from doc2mark import Task as TopTask

    assert TopOCR is OCR
    assert TopTask is Task
    assert TopOCRPage is OCRPage
