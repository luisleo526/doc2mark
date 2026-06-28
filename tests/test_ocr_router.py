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


class TestRichSchema:
    """Rich OCRPage fields: raw verbatim indexes + interpretation anchors."""

    def test_metric_illustrative_firewalled_off_screenshot(self):
        from doc2mark.ocr.schema import Metric
        page = OCRPage(
            raw=RawExtraction(text="Revenue $1,000", metrics=[Metric(label="Revenue", value="$1,000", illustrative=True)]),
            interpretation=Interpretation(document_type="document"),  # not a screenshot
        )
        assert any("only 'screenshot' may withhold" in m for m in router_invariants(page))

    def test_primary_date_must_come_from_raw_dates(self):
        ok = OCRPage(raw=RawExtraction(dates=["Q3 2024"]), interpretation=Interpretation(primary_date="Q3 2024"))
        assert router_invariants(ok) == []
        bad = OCRPage(raw=RawExtraction(dates=["Q3 2024"]), interpretation=Interpretation(primary_date="Q4 2024"))
        assert any("primary_date not present in raw.dates" in m for m in router_invariants(bad))

    def test_to_markdown_renders_title_and_metrics_dedup_safe(self):
        from doc2mark.ocr.schema import Metric
        # title not already leading raw.text -> H1 prepended; real metric -> table; illustrative skipped
        page = OCRPage(
            raw=RawExtraction(text="Body line", metrics=[
                Metric(label="Uptime", value="99.9", unit="%"),
                Metric(label="Demo", value="123", illustrative=True),
            ]),
            interpretation=Interpretation(page_title="Results"),
        )
        md = page.to_markdown()
        assert md.startswith("# Results")
        assert "| Uptime | 99.9 % |" in md
        assert "Demo" not in md  # illustrative metric not rendered

    def test_to_markdown_title_not_duplicated_when_already_leading(self):
        page = OCRPage(
            raw=RawExtraction(text="Results\nbody"),
            interpretation=Interpretation(page_title="Results"),
        )
        assert page.to_markdown().count("Results") == 1  # no duplicate H1


class TestNestedSchema:
    """Nested figures / sections / entities / relations + their firewall checks."""

    def _imp(self):
        from doc2mark.ocr.schema import (Figure, DataPoint, DiagramNode, DiagramEdge,
                                         Section, Entity, Relation)
        return Figure, DataPoint, DiagramNode, DiagramEdge, Section, Entity, Relation

    def test_figure_verbatim_must_be_in_raw_text(self):
        Figure, DataPoint, *_ = self._imp()
        ok = OCRPage(raw=RawExtraction(text="Revenue 2024 $4.2B"),
                     interpretation=Interpretation(figures=[Figure(kind="bar", title="Revenue 2024",
                         data_points=[DataPoint(label="2024", value="$4.2B")])]))
        assert router_invariants(ok) == []
        bad = OCRPage(raw=RawExtraction(text="something else"),
                      interpretation=Interpretation(figures=[Figure(kind="bar", title="Ghost Title")]))
        assert any("not found in raw.text" in m for m in router_invariants(bad))

    def test_figure_datapoints_need_a_value(self):
        Figure, DataPoint, *_ = self._imp()
        page = OCRPage(raw=RawExtraction(text="Q1 Q2"),
                       interpretation=Interpretation(figures=[Figure(kind="line",
                           data_points=[DataPoint(label="Q1"), DataPoint(label="Q2")])]))
        assert any("no point with a printed value" in m for m in router_invariants(page))

    def test_edge_endpoint_must_match_a_node(self):
        Figure, _DP, DiagramNode, DiagramEdge, *_ = self._imp()
        page = OCRPage(raw=RawExtraction(text="A B"),
                       interpretation=Interpretation(figures=[Figure(kind="flowchart",
                           nodes=[DiagramNode(label="A")], edges=[DiagramEdge(from_label="A", to_label="B")])]))
        assert any("matches no DiagramNode.label" in m for m in router_invariants(page))

    def test_section_heading_must_be_in_raw_headings(self):
        *_, Section, _E, _R = self._imp()
        bad = OCRPage(raw=RawExtraction(text="Intro body", headings=["Intro"]),
                      interpretation=Interpretation(sections=[Section(heading="Conclusion", level=1)]))
        assert any("not present in raw.headings" in m for m in router_invariants(bad))

    def test_entity_and_relation_must_be_verbatim(self):
        *_, Entity, Relation = self._imp()
        bad = OCRPage(raw=RawExtraction(text="Acme grew"),
                      interpretation=Interpretation(
                          typed_entities=[Entity(name="Globex", type="org")],
                          relations=[Relation(subject="Acme", relation="acquired", object="Initech")]))
        v = router_invariants(bad)
        assert any("typed_entities name 'Globex'" in m for m in v)
        assert any("relations object 'Initech'" in m for m in v)

    def test_to_markdown_renders_figure_and_section_outline(self):
        Figure, _DP, DiagramNode, DiagramEdge, Section, *_ = self._imp()
        page = OCRPage(
            raw=RawExtraction(text="App Core", headings=["Arch"]),
            interpretation=Interpretation(
                figures=[Figure(kind="flowchart", meaning="Layered architecture",
                    nodes=[DiagramNode(label="App"), DiagramNode(label="Core")],
                    edges=[DiagramEdge(from_label="App", to_label="Core")])],
                sections=[Section(heading="Arch", level=1, summary="overview")]))
        md = page.to_markdown()
        assert "*Layered architecture*" in md
        assert "- App --> Core" in md
        assert "**Section outline**" in md


class TestPageMarkdownSynthesis:
    """page_markdown display-swap with the verbatim coverage guard (BM42-safe)."""

    def test_faithful_page_markdown_is_used(self):
        raw = RawExtraction(text="第一步\n第二步\n第三步\n第四步\n第五步")
        it = Interpretation(page_markdown="## 流程\n\n第一步 → 第二步 → 第三步 → 第四步 → 第五步")
        md = OCRPage(raw=raw, interpretation=it).to_markdown()
        assert md.startswith("## 流程")
        assert "第一步" in md and "第五步" in md

    def test_undercover_falls_back_to_raw(self):
        raw = RawExtraction(text="alpha one\nbravo two\ncharlie three\ndelta four\necho five")
        it = Interpretation(page_markdown="## Summary\n\nA short summary, nothing else.")
        md = OCRPage(raw=raw, interpretation=it).to_markdown()
        assert not md.startswith("## Summary")              # rejected
        assert all(t in md for t in ["alpha", "charlie", "echo"])  # raw verbatim preserved

    def test_high_cover_uses_md_and_tail_keeps_dropped_token(self):
        toks = [f"item{i}" for i in range(10)]
        raw = RawExtraction(text="\n".join(toks))
        it = Interpretation(page_markdown="## List\n\n" + " ".join(toks[:9]))  # drops item9 (90% cover)
        md = OCRPage(raw=raw, interpretation=it).to_markdown()
        assert md.startswith("## List")
        assert "item9" in md                                # carried in the verbatim tail
        assert "raw-verbatim-tail" in md

    def test_none_page_markdown_uses_standard_render(self):
        raw = RawExtraction(text="a line of ordinary body text here")
        md = OCRPage(raw=raw, interpretation=Interpretation()).to_markdown()
        assert md.startswith("a line of ordinary")
