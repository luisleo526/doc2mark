"""Image job-router: schema additions, the self-routing prompt wiring, and the
BM42 firewall validator (router_invariants)."""

from doc2mark.ocr.base import (
    Task,
    TASK_PROMPTS,
    _ROUTER_PREAMBLE,
    _ROUTER_CONFIDENCE_CLAUSE,
    _RAW_DISCIPLINE,
)
from doc2mark.ocr.schema import (
    OCRPage,
    RawExtraction,
    Interpretation,
    Table,
    KeyValue,
    router_invariants,
)


class TestSchemaAdditions:
    def test_document_type_has_16_values(self):
        vals = Interpretation.model_fields["document_type"].annotation.__args__
        assert set(vals) >= {"screenshot", "diagram", "infographic", "logo", "stamp"}
        assert len(vals) == 16

    def test_content_fidelity_default_verbatim(self):
        assert Interpretation().content_fidelity == "verbatim"

    def test_illustrative_defaults_false(self):
        assert Table().illustrative is False and Table().row_count is None
        assert KeyValue().illustrative is False

    def test_roundtrip_preserves_new_fields(self):
        page = OCRPage(
            raw=RawExtraction(tables=[Table(headers=["A"], illustrative=True, row_count=12)]),
            interpretation=Interpretation(document_type="screenshot", content_fidelity="described",
                                          summary="demo", self_confidence=0.9),
        )
        rebuilt = OCRPage.model_validate(page.model_dump())
        assert rebuilt.raw.tables[0].illustrative is True
        assert rebuilt.raw.tables[0].row_count == 12
        assert rebuilt.interpretation.content_fidelity == "described"


class TestRouterPromptWiring:
    def test_auto_is_the_router(self):
        auto = TASK_PROMPTS[Task.AUTO]
        assert auto is _ROUTER_PREAMBLE
        assert auto.startswith("First CLASSIFY")
        assert "ALL THREE hold" in auto              # triple-gate
        assert _RAW_DISCIPLINE[:40] in auto          # verbatim body embedded
        assert "REAL table" in auto                  # grid-precedence guard
        assert 'never \"screenshot\"' in auto        # code precedence guard

    def test_explicit_tasks_unchanged(self):
        # explicit tasks remain hard verbatim routes (no self-routing)
        assert TASK_PROMPTS[Task.DOCUMENT].startswith("This is a text document")
        assert TASK_PROMPTS[Task.TABLE].startswith("This image is dominated by tabular")

    def test_confidence_clause_gates_nonverbatim(self):
        assert "self_confidence >= 0.7" in _ROUTER_CONFIDENCE_CLAUSE
        assert 'legibility is \"high\"' in _ROUTER_CONFIDENCE_CLAUSE


class TestRouterInvariants:
    def _screenshot(self, conf=0.9, leg="high"):
        return OCRPage(
            raw=RawExtraction(text="Module: Projects", tables=[Table(headers=["Item", "Price"], illustrative=True, row_count=5)]),
            interpretation=Interpretation(document_type="screenshot", content_fidelity="described",
                                          summary="A project dashboard demo.", self_confidence=conf, legibility=leg),
        )

    def test_clean_verbatim_page_ok(self):
        page = OCRPage(raw=RawExtraction(text="hello"), interpretation=Interpretation(document_type="document"))
        assert router_invariants(page) == []

    def test_valid_screenshot_ok(self):
        assert router_invariants(self._screenshot()) == []

    def test_illustrative_on_nonscreenshot_violates(self):
        page = OCRPage(
            raw=RawExtraction(tables=[Table(headers=["A"], illustrative=True)]),
            interpretation=Interpretation(document_type="table", content_fidelity="verbatim", summary="x"),
        )
        v = router_invariants(page)
        assert any("only 'screenshot' may withhold" in m for m in v)

    def test_low_confidence_screenshot_violates(self):
        v = router_invariants(self._screenshot(conf=0.4))
        assert any("self_confidence<0.7" in m for m in v)
        v2 = router_invariants(self._screenshot(leg="low"))
        assert any("legibility" in m for m in v2)

    def test_described_without_summary_violates(self):
        page = OCRPage(raw=RawExtraction(text="t"),
                       interpretation=Interpretation(document_type="chart", content_fidelity="described", summary=""))
        assert any("interpretation.summary is empty" in m for m in router_invariants(page))

    def test_chart_describes_without_withholding_ok(self):
        # chart keeps all printed text (no illustrative), describes trend -> valid
        page = OCRPage(
            raw=RawExtraction(text="Revenue 2024\nQ1 85%"),
            interpretation=Interpretation(document_type="chart", content_fidelity="described",
                                          summary="Revenue rose through 2024."),
        )
        assert router_invariants(page) == []

    def test_skipped_with_content_violates(self):
        page = OCRPage(raw=RawExtraction(text="not blank"),
                       interpretation=Interpretation(document_type="blank", content_fidelity="skipped"))
        assert any("skipped" in m for m in router_invariants(page))

    def test_freeform_recovery_page_is_verbatim_ok(self):
        # the empty->free-form recovery builds OCRPage(raw=..., interpretation=None)
        page = OCRPage(raw=RawExtraction(text="recovered verbatim text"), interpretation=None)
        assert router_invariants(page) == []
