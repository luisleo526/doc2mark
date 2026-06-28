"""Structured-OCR empty-result recovery: when a model can read an image but
cannot fill the json_schema (returns an empty OCRPage), the provider must fall
back to free-form OCR so content is never silently lost."""

import pytest

from doc2mark.ocr.base import OCRResult
from doc2mark.ocr.schema import OCRPage, RawExtraction, Table, KeyValue
from doc2mark.ocr.openai import OpenAIOCR
from doc2mark.ocr.vertex_ai import VertexAIOCR

PROVIDERS = [OpenAIOCR, VertexAIOCR]


@pytest.mark.parametrize("cls", PROVIDERS)
class TestIsEmptyStructured:
    def test_blank_text_and_empty_page_is_empty(self, cls):
        assert cls._is_empty_structured(OCRResult(text="", document=OCRPage()))
        assert cls._is_empty_structured(OCRResult(text="   ", document=None))

    def test_text_present_is_not_empty(self, cls):
        assert not cls._is_empty_structured(OCRResult(text="hello"))

    def test_raw_text_present_is_not_empty(self, cls):
        r = OCRResult(text="", document=OCRPage(raw=RawExtraction(text="y")))
        assert not cls._is_empty_structured(r)

    def test_tables_or_fields_present_is_not_empty(self, cls):
        r_tbl = OCRResult(text="", document=OCRPage(
            raw=RawExtraction(tables=[Table(html="<table><tr><td>a</td></tr></table>")])))
        r_fld = OCRResult(text="", document=OCRPage(
            raw=RawExtraction(fields=[KeyValue(label="L", value="V")])))
        assert not cls._is_empty_structured(r_tbl)
        assert not cls._is_empty_structured(r_fld)


@pytest.mark.parametrize("cls", PROVIDERS)
class TestRecoverEmptyStructured:
    def _ocr(self, cls):
        return cls(api_key="test-key")

    def test_empty_results_recovered_from_freeform(self, cls, monkeypatch):
        ocr = self._ocr(cls)
        empty = OCRResult(text="", document=OCRPage(raw=RawExtraction(text="")))
        good = OCRResult(text="hello", document=OCRPage(raw=RawExtraction(text="hello")))

        monkeypatch.setattr(ocr, "_ensure_vision_agent", lambda *a, **k: None)
        monkeypatch.setattr(
            ocr, "_batch_process_with_vision_agent",
            lambda imgs, *a, **k: [OCRResult(text="recovered CJK text")],
        )

        out = ocr._recover_empty_structured([empty, good], [b"img0", b"img1"])
        # empty one filled from free-form, in both .text and .document.raw.text
        assert out[0].text == "recovered CJK text"
        assert out[0].document.raw.text == "recovered CJK text"
        assert out[0].metadata["structured_fallback"] == "free_form"
        # good one untouched
        assert out[1].text == "hello"
        assert "structured_fallback" not in (out[1].metadata or {})

    def test_no_empties_is_a_noop(self, cls, monkeypatch):
        ocr = self._ocr(cls)
        called = {"n": 0}

        def _boom(*a, **k):
            called["n"] += 1
            return []

        monkeypatch.setattr(ocr, "_batch_process_with_vision_agent", _boom)
        good = OCRResult(text="content", document=OCRPage(raw=RawExtraction(text="content")))
        out = ocr._recover_empty_structured([good], [b"img"])
        assert out[0].text == "content"
        assert called["n"] == 0  # no free-form retry when nothing is empty

    def test_freeform_also_empty_leaves_result(self, cls, monkeypatch):
        ocr = self._ocr(cls)
        empty = OCRResult(text="", document=OCRPage())
        monkeypatch.setattr(ocr, "_ensure_vision_agent", lambda *a, **k: None)
        monkeypatch.setattr(
            ocr, "_batch_process_with_vision_agent",
            lambda imgs, *a, **k: [OCRResult(text="")],
        )
        out = ocr._recover_empty_structured([empty], [b"img"])
        assert out[0].text == ""  # unchanged when free-form also yields nothing
