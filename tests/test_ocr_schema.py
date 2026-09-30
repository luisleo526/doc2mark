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
    sanitize_table_html,
    normalize_table_html,
)


def _row_grid_widths(html):
    """Return (per-row occupied column counts, grid width) for an HTML table,
    honouring colspan AND rowspan carry-over — the real rendered grid."""
    from lxml import html as H
    frag = H.fragment_fromstring(html, create_parent="div")
    rows = frag.findall(".//tr")
    grid = {}
    for r, tr in enumerate(rows):
        c = 0
        for cell in (e for e in tr if e.tag in ("td", "th")):
            while (r, c) in grid:
                c += 1
            cs = int(cell.get("colspan", 1) or 1)
            rs = int(cell.get("rowspan", 1) or 1)
            for dr in range(rs):
                for dc in range(cs):
                    grid[(r + dr, c + dc)] = True
            c += cs
    width = max((col for (_, col) in grid), default=-1) + 1
    return [sum(1 for cc in range(width) if (r, cc) in grid) for r in range(len(rows))], width


class TestTableHtmlNormalization:
    """Every OCR table must be a rectangular grid: all rows occupy the same number
    of columns. Models emitting ragged HTML (a sparse, header-less unit column makes
    them switch column counts mid-table) are repaired deterministically."""

    def test_pads_ragged_rows_to_uniform_width(self):
        ragged = ("<table><tr><td>a</td><td>b</td><td>c</td></tr>"
                  "<tr><td>x</td></tr></table>")
        widths, w = _row_grid_widths(normalize_table_html(ragged))
        assert w == 3 and widths == [3, 3]

    def test_noop_on_already_uniform_table(self):
        uniform = "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"
        widths, w = _row_grid_widths(normalize_table_html(uniform))
        assert w == 2 and widths == [2, 2]

    def test_colspan_counted_when_normalizing(self):
        # row 1 is 3 wide via colspan (row 2 cell by cell); row 3 has a single cell -> pad to 3.
        ragged = ('<table><tr><th colspan="3">H</th></tr>'
                  "<tr><td>a</td><td>b</td><td>c</td></tr>"
                  "<tr><td>only</td></tr></table>")
        widths, w = _row_grid_widths(normalize_table_html(ragged))
        assert w == 3 and widths == [3, 3, 3]

    def test_colspan_is_capped_at_the_widest_row(self):
        # no row has more than one cell, so a colspan of 3 cannot widen the grid.
        out = normalize_table_html('<table><tr><th colspan="3">H</th></tr><tr><td>only</td></tr></table>')
        widths, w = _row_grid_widths(out)
        assert w == 1 and widths == [1, 1]
        assert "colspan" not in out and "H" in out and "only" in out

    def test_rowspan_carryover_not_overpadded(self):
        # 'a' spans into row 2, so row 2 already occupies 2 cols (a + c) -> no padding.
        html = ('<table><tr><td rowspan="2">a</td><td>b</td></tr>'
                "<tr><td>c</td></tr></table>")
        out = normalize_table_html(html)
        widths, w = _row_grid_widths(out)
        assert w == 2 and widths == [2, 2]
        # row 2 must still carry exactly ONE explicit <td> (c), not a spurious pad.
        from lxml import html as H
        rows = H.fragment_fromstring(out, create_parent="div").findall(".//tr")
        assert len([e for e in rows[1] if e.tag in ("td", "th")]) == 1

    def test_table_model_normalizes_html_field(self):
        t = Table(html="<table><tr><td>a</td><td>b</td><td>c</td></tr><tr><td>x</td></tr></table>")
        widths, w = _row_grid_widths(t.html)
        assert w == 3 and widths == [3, 3]

    def test_empty_input_safe(self):
        assert normalize_table_html("") == ""


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

    def test_prefers_table_html_with_spans(self):
        html = '<table><tr><th colspan="2">H</th></tr><tr><td>a</td><td>b</td></tr></table>'
        # html wins over both markdown and headers/rows
        page = OCRPage(raw=RawExtraction(tables=[
            Table(html=html, markdown="| ignored |", headers=["x"], rows=[["y"]])
        ]))
        out = page.to_markdown()
        assert 'colspan="2"' in out and "<table>" in out
        assert "ignored" not in out

    def test_renders_headers_and_rows(self):
        page = OCRPage(raw=RawExtraction(tables=[Table(headers=["A", "B"], rows=[["1", "2"]])]))
        md = page.to_markdown()
        assert "| A | B |" in md
        assert "| 1 | 2 |" in md

    def test_empty_page(self):
        assert OCRPage().to_markdown() == ""


class TestTableHtmlSanitization:
    """Table.html is LLM-controlled and must be sanitized to a table allowlist."""

    def test_script_is_dropped_with_content(self):
        out = sanitize_table_html("<table><tr><td>ok<script>alert(1)</script></td></tr></table>")
        assert "script" not in out.lower() and "alert" not in out
        assert "ok" in out and "<td>" in out

    def test_event_handler_and_style_attrs_stripped(self):
        out = sanitize_table_html('<table><tr><td onclick="x()" style="color:red" class="c">a</td></tr></table>')
        assert "onclick" not in out.lower() and "style" not in out.lower() and "class" not in out.lower()
        assert "a" in out

    def test_spans_preserved(self):
        out = sanitize_table_html('<table><tr><th colspan="2" rowspan="3">H</th></tr></table>')
        assert 'colspan="2"' in out and 'rowspan="3"' in out

    def test_non_integer_span_dropped(self):
        out = sanitize_table_html('<table><tr><td colspan="x">a</td></tr></table>')
        assert "colspan" not in out.lower() and "a" in out

    def test_inline_tags_unwrapped_keeping_text(self):
        out = sanitize_table_html("<table><tr><td><b>bold</b> <a href='javascript:x'>link</a></td></tr></table>")
        assert "bold" in out and "link" in out
        assert "<b>" not in out and "<a" not in out and "javascript" not in out

    def test_non_table_wrapper_dropped(self):
        out = sanitize_table_html("<div onmouseover='x'><table><tr><td>1</td></tr></table></div>")
        assert "<div" not in out and "onmouseover" not in out and "1" in out

    def test_empty_and_unparseable_fail_closed(self):
        assert sanitize_table_html("") == ""
        assert sanitize_table_html("   ") == ""

    def test_code_fence_stripped(self):
        out = sanitize_table_html("```html\n<table><tr><td>x</td></tr></table>\n```")
        assert "```" not in out and "<td>" in out and "x" in out

    def test_validator_runs_on_construction(self):
        t = Table(html="<table><tr><td onclick='x'>v<script>bad</script></td></tr></table>")
        assert "script" not in t.html.lower() and "onclick" not in t.html.lower()
        assert "v" in t.html

    def test_to_markdown_emits_sanitized_html(self):
        page = OCRPage(raw=RawExtraction(tables=[
            Table(html="<table><tr><td>cell<script>alert(1)</script></td></tr></table>")
        ]))
        md = page.to_markdown()
        assert "script" not in md.lower() and "cell" in md


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


# --------------------------------------------------------------------------- #
# Markdown boundary (review round 1: M1, M2, M3, m4)                          #
# --------------------------------------------------------------------------- #
import time  # noqa: E402

import markdown as _python_markdown  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from doc2mark.ocr.schema import Figure, Metric, _escape_text_block, _sanitize_markdown  # noqa: E402


def _rendered(markdown_text):
    return BeautifulSoup(_python_markdown.markdown(markdown_text, extensions=["tables"]), "html.parser")


def _live_links(markdown_text):
    """Images and links a reader could follow that are not http(s)/mailto."""
    soup = _rendered(markdown_text)
    links = [a.get("href", "") for a in soup.find_all("a")]
    return soup.find_all("img") + [h for h in links if not h.lower().startswith(("http:", "https:", "mailto:"))]


def test_long_blank_run_in_a_cell_costs_linear_time():
    started = time.perf_counter()
    table = Table(html="<table><tr><td>a" + " " * 400_000 + "b</td></tr></table>")
    assert time.perf_counter() - started < 2.0
    assert table.html.startswith("<table><tr><td>a") and table.html.endswith("b</td></tr></table>")


@pytest.mark.parametrize("text", [
    "![x](javascript:alert(1))",
    "[click](javascript:alert(1))",
    "![](https://attacker.example/pixel.png)",
    "[a]: javascript:alert(1)\n\n[a]",
    "- [a]: javascript:alert(1)\n\n[a]",
    "[x](java&#115;cript:alert(1))",
    "[x](javascript&colon;alert(1))",
    "[x](javascript\\:alert(1))",
    "[x](data:text/html;base64,PHNjcmlwdD4=)",
    "<table-x><caption>[x]</caption></table>(javascript:alert(1))",
    "`a` <table><tr><td>![p](https://attacker.example/p.png)</td></tr></table>",
])
def test_ocr_markdown_creates_no_image_or_script_link(text):
    assert _live_links(_sanitize_markdown(text)) == []
    assert _live_links(_escape_text_block(text)) == []


def test_ocr_markdown_keeps_http_links():
    out = _sanitize_markdown("[docs](https://example.com/a) and [mail](mailto:a@example.com)")
    assert [a["href"] for a in _rendered(out).find_all("a")] == ["https://example.com/a", "mailto:a@example.com"]


def test_code_is_shown_as_written():
    out = _sanitize_markdown("Use `<div>` and `List<String>`:\n\n```html\n<div>code</div> &copy;\n```")
    codes = [code.get_text() for code in _rendered(out).find_all("code")]
    assert codes[:2] == ["<div>", "List<String>"]
    assert out.endswith("```html\n<div>code</div> &copy;\n```")  # the fenced block as written


@pytest.mark.parametrize("text", [
    "`<img src=x onerror=alert(1)>`",
    "<table><tr><td>1</td></tr></table>\n```\n<img src=x onerror=alert(1)>\n```",  # a table's HTML block holds the fence
    "`<script>`alert(1)`</script>`",
])
def test_code_that_is_not_code_after_all_stays_inert(text):
    """Markup left as written inside a code region must be inert even when a renderer does
    not treat the region as code."""
    soup = _rendered(_sanitize_markdown(text))
    assert soup.find_all(["img", "script"]) == []
    assert not [el for el in soup.find_all(True) if any(name.startswith("on") for name in el.attrs)]


def test_transcribed_lists_stay_lists_and_entities_stay():
    out = _escape_text_block("Agenda\n- Fast\n- Cheap\n1. Budget\n&copy; 2026")
    assert out == "Agenda\n- Fast\n- Cheap\n1. Budget\n&copy; 2026"
    assert [li.get_text() for li in _rendered(_escape_text_block("- Fast\n- Cheap")).find_all("li")] == ["Fast", "Cheap"]
    assert "©" in _rendered(out).get_text()


def test_withheld_fields_metrics_and_figures_leave_a_marker():
    page = OCRPage(
        raw=RawExtraction(text="Dashboard", fields=[KeyValue(label="Plan", value="Pro", illustrative=True)],
                          metrics=[Metric(label="MRR", value="$1,000", illustrative=True),
                                   Metric(label="Users", value="12", illustrative=True)]),
        interpretation=Interpretation(document_type="screenshot",
                                      figures=[Figure(kind="bar", title="Growth", meaning="Up", illustrative=True)]),
    )
    out = page.to_markdown()
    assert "[1 illustrative field not transcribed]" in out
    assert "[2 illustrative metrics not transcribed]" in out
    assert "[1 illustrative figure not transcribed]" in out
    assert "$1,000" not in out
