"""Tests for the structured OCR schema and the extended OCR base contract."""

import pytest

from doc2mark.ocr.base import (
    OCRConfig,
    OCRResult,
    OCRProvider,
    Task,
    TASK_PROMPTS,
)
from doc2mark.ocr.schema import (
    OCRPage,
    RawExtraction,
    Interpretation,
    Table,
    KeyValue,
)


class TestSchemaShape:
    def test_ocrpage_top_level_properties(self):
        props = OCRPage.model_json_schema()["properties"]
        assert set(props) == {"raw", "interpretation"}

    def test_every_task_has_a_prompt(self):
        assert set(TASK_PROMPTS) == set(Task)
        assert all(isinstance(v, str) and v for v in TASK_PROMPTS.values())

    def test_models_default_construct(self):
        # All nested models must be constructible with no args (defaults) so
        # strict json_schema mode (all-required) is satisfiable.
        page = OCRPage()
        assert page.raw.text == ""
        assert page.raw.tables == []
        assert page.interpretation is None

    def test_self_confidence_bounds(self):
        with pytest.raises(ValueError):
            Interpretation(self_confidence=1.5)

    def test_roundtrip_model_dump_validate(self):
        page = OCRPage(
            raw=RawExtraction(text="hello", fields=[KeyValue(label="Total", value="$8.10")]),
            interpretation=Interpretation(document_type="receipt", summary="A receipt"),
        )
        dumped = page.model_dump()
        rebuilt = OCRPage.model_validate(dumped)
        assert rebuilt.raw.text == "hello"
        assert rebuilt.raw.fields[0].value == "$8.10"
        assert rebuilt.interpretation.document_type == "receipt"


class TestToMarkdown:
    def test_text_only(self):
        assert OCRPage(raw=RawExtraction(text="  abc  ")).to_markdown() == "abc"

    def test_prefers_table_markdown(self):
        page = OCRPage(raw=RawExtraction(tables=[Table(markdown="| a |\n|---|")]))
        assert "| a |" in page.to_markdown()

    def test_renders_headers_and_rows(self):
        page = OCRPage(raw=RawExtraction(tables=[Table(headers=["A", "B"], rows=[["1", "2"]])]))
        md = page.to_markdown()
        assert "| A | B |" in md
        assert "| 1 | 2 |" in md

    def test_empty_page(self):
        assert OCRPage().to_markdown() == ""


class TestOCRConfigCompat:
    def test_structured_is_default(self):
        assert OCRConfig().structured is True
        assert OCRConfig().detail == "full"
        assert OCRConfig().task is Task.AUTO

    def test_legacy_keyword_construction_still_works(self):
        cfg = OCRConfig(language="en", detect_tables=True, timeout=30)
        assert cfg.language == "en"

    def test_deprecated_overrides_detected(self):
        cfg = OCRConfig(detect_tables=False, timeout=99, max_retries=5)
        flagged = cfg.deprecated_llm_overrides()
        assert set(flagged) == {"detect_tables", "timeout", "max_retries"}

    def test_no_deprecated_overrides_by_default(self):
        assert OCRConfig(language="en", model="gpt-4o").deprecated_llm_overrides() == []


class TestOCRResultDocument:
    def test_document_defaults_to_none(self):
        assert OCRResult(text="x").document is None

    def test_document_carries_page(self):
        page = OCRPage(raw=RawExtraction(text="x"))
        r = OCRResult(text="x", document=page)
        assert r.document is page


def test_gemini_alias_exists():
    assert OCRProvider.GEMINI.value == "gemini"
    assert OCRProvider("gemini") is OCRProvider.GEMINI
