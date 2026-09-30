"""OCR-lane E2E tests: OCR table cleanup, output sanitisation, refusals, the router firewall
and Tesseract language / failure handling.

Every test runs the real ``doc2mark`` CLI and asserts on what it writes (Markdown, JSON,
exit code, stderr).

The LLM tests mock ONLY the third-party model. A real model cannot be made to refuse, emit a
malicious or ragged table, or violate the router firewall on demand, so ``--ocr openai`` is
pointed (``OPENAI_BASE_URL``) at a local fake OpenAI-compatible server started by the test
(``tests/e2e/fake_openai.py``). It returns the payloads from the table/XSS review probes; the
CLI, LangChain and the ``openai`` SDK run unmodified. The Tesseract tests run real Tesseract.
"""

import re

import pytest

from tests.e2e import builders_ocr as build
from tests.e2e import fake_openai as fake
from tests.e2e import pdfgen
from tests.e2e.fake_openai import FakeOpenAI

REFUSAL = "I'm sorry, but I can't assist with that request."


@pytest.fixture
def fake_llm():
    with FakeOpenAI() as server:
        yield server


@pytest.fixture
def scan(e2e_dir):
    """A one-page scanned PDF: it goes to whole-page OCR, one structured request per run."""
    return build.scan_pdf(e2e_dir / "scan.pdf")


def run_llm(run_cli, pdf, server, *args, **kwargs):
    return run_cli(pdf, "--ocr", "openai", "--ocr-images", *args, env=server.env, **kwargs)


def ocr_issues(result) -> dict:
    return ((result.json or {}).get("metadata") or {}).get("extra", {}).get("ocr_issues") or {}


# --------------------------------------------------------------------------- #
# T8: line breaks inside OCR table cells                                      #
# --------------------------------------------------------------------------- #


def test_t8_line_breaks_inside_table_cells_keep_words_and_numbers_apart(run_cli, fake_llm, scan):
    html = (
        "<table><tr><th>Net<br>income</th><th><p>Fiscal year</p><p>2024</p></th><th>Units</th></tr>"
        "<tr><td>1,200<br/>(+5%)</td><td><ul><li>alpha</li><li>beta</li></ul></td>"
        "<td><div>100</div><div>120</div></td></tr></table>"
    )
    fake_llm.script(structured=[fake.page("Segment results", tables=[fake.table(html)])])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    assert build.html_tables(result.markdown) == [
        [["Net income", "Fiscal year 2024", "Units"], ["1,200 (+5%)", "alpha beta", "100 120"]]
    ], result.describe()


# --------------------------------------------------------------------------- #
# T9: model-emitted spans are bounded                                         #
# --------------------------------------------------------------------------- #


def test_t9_huge_rowspan_and_colspan_do_not_stall_the_conversion(run_cli, fake_llm, scan):
    html = "<table><tr><td rowspan='5000' colspan='5000'>x</td><td>1</td></tr><tr><td>2</td></tr></table>"
    fake_llm.script(structured=[fake.page("Span attack", tables=[fake.table(html)])])

    result = run_llm(run_cli, scan, fake_llm, timeout=60)

    assert result.exit_code == 0, result.describe()
    [grid] = build.html_tables(result.markdown)
    assert len(grid) == 2 and max(len(row) for row in grid) <= 3, grid
    assert {"x", "1", "2"} <= {cell for row in grid for cell in row}
    assert len(result.markdown) < 5_000


def test_t9_huge_colspan_does_not_bloat_the_output(run_cli, fake_llm, scan):
    rows = "".join(f"<tr><td>row {i}</td></tr>" for i in range(20))
    html = f"<table><tr><th colspan='50000'>Title</th></tr>{rows}</table>"
    fake_llm.script(structured=[fake.page("Wide span", tables=[fake.table(html)])])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    assert len(result.markdown) < 5_000, f"{len(result.markdown)} chars of Markdown"
    [grid] = build.html_tables(result.markdown)
    assert grid == [["Title"]] + [[f"row {i}"] for i in range(20)]


def _implied_grid_cells(markdown: str) -> int:
    """How many grid slots the emitted tables claim (sum of rowspan x colspan)."""
    total = 0
    for cell in re.finditer(r"<t[dh]\b([^>]*)>", markdown):
        spans = dict(re.findall(r'(rowspan|colspan)="(\d+)"', cell.group(1)))
        total += int(spans.get("rowspan", 1)) * int(spans.get("colspan", 1))
    return total


def test_t9_many_wide_and_tall_spans_do_not_stall_the_conversion(run_cli, fake_llm, scan):
    """700 cells of colspan=700 rowspan=3000 over 3000 rows: each span alone is within the
    caps, together they claim a 490,000 x 3,000 grid."""
    html = "<table><tr>" + "<td colspan='700' rowspan='3000'>x</td>" * 700 + "</tr>" + "<tr></tr>" * 2999 + "</table>"
    fake_llm.script(structured=[fake.page("Span wall", tables=[fake.table(html)])])

    result = run_llm(run_cli, scan, fake_llm, timeout=30)

    assert result.exit_code == 0, result.describe()
    assert result.markdown.count(">x<") == 700, "every cell's text must survive"
    assert _implied_grid_cells(result.markdown) <= 100_000, "the emitted table must not be a table bomb"


def test_t9_padding_does_not_blow_small_tables_up(run_cli, fake_llm, scan):
    """A 32-cell x colspan-32 header over 243 one-cell rows: padding every row to the
    1024-column header would turn 5 KB of HTML into 2 MB, twelve times over."""
    one = "<table><tr>" + "<td colspan='32'>h</td>" * 32 + "</tr>" + "<tr><td>1</td></tr>" * 243 + "</table>"
    fake_llm.script(structured=[fake.page("Padding", tables=[fake.table(one * 12)])])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    assert len(result.markdown) < 500_000, f"{len(result.markdown)} chars of Markdown"
    assert result.markdown.count("<td>1</td>") == 12 * 243, "every cell's text must survive"


# --------------------------------------------------------------------------- #
# T10: every OCR text field that reaches Markdown is sanitised                #
# --------------------------------------------------------------------------- #


def test_t10_model_text_fields_cannot_inject_live_html(run_cli, fake_llm, scan):
    payload = fake.page(
        "Invoice <img src=x onerror=alert('raw.text')>\nTotal due <b onmouseover=alert(1)>100</b>",
        tables=[
            fake.table(
                "<table><tr><td>f<!-- <script>alert(1)</script> --></td>"
                "<td><!--[if gte IE 4]><script>alert(2)</script><![endif]-->g</td></tr></table>"
            ),
            fake.table(markdown="| a |\n|---|\n| <img src=x onerror=alert('table.markdown')> |"),
            fake.table(headers=["Plan <svg onload=alert('headers')>"], rows=[["<iframe src=javascript:alert(1)>"]]),
        ],
        interpretation=fake.interpretation(
            page_title="<script>alert('title')</script>Invoice",
            figures=[{"kind": "bar", "title": "<img src=y onerror=alert('figure')>",
                      "meaning": "<script>alert('meaning')</script>"}],
        ),
    )
    fake_llm.script(structured=[payload])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    assert build.active_html(result.markdown) == [], result.describe()
    visible = build.visible_text(result.markdown)
    # Verbatim first: the text is neutralised, not dropped.
    assert "Invoice <img src=x onerror=alert('raw.text')>" in visible, visible
    assert "Plan <svg onload=alert('headers')>" in visible, visible


def test_t10_page_markdown_cannot_inject_live_html(run_cli, fake_llm, scan):
    page_markdown = (
        "## Quarterly report\n\n<img src=x onerror=alert('page_markdown')>\n\n"
        "Revenue 1,200 in Q1\n\n<!-- hidden <script>alert(3)</script> -->"
    )
    fake_llm.script(structured=[fake.page(
        "Quarterly report\nRevenue 1,200 in Q1",
        interpretation=fake.interpretation(page_markdown=page_markdown),
    )])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    assert build.active_html(result.markdown) == [], result.describe()
    assert result.markdown.startswith("## Quarterly report"), result.describe()
    assert "Revenue 1,200 in Q1" in result.markdown


def test_t10_text_before_a_sanitized_table_cannot_complete_a_tag(run_cli, fake_llm, scan):
    """A "<" right before table-like markup the sanitizer unwraps to plain text
    (<table-x> is not a table) must not join that text into a live tag."""
    page_markdown = "## Notes\n\nHello <<table-x><caption>img src=x onerror=alert(1)//</caption></table>>"
    fake_llm.script(structured=[fake.page(
        "Notes\nHello", interpretation=fake.interpretation(page_markdown=page_markdown))])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    assert build.active_html(result.markdown) == [], result.describe()
    assert "Hello" in result.markdown, result.describe()


def test_t8_long_blank_run_in_a_cell_does_not_stall_the_conversion(run_cli, fake_llm, scan):
    """400,000 blanks inside one cell: the line-break cleanup used to take minutes on them."""
    html = "<table><tr><td>Net" + " " * 400_000 + "income</td><td>1,200</td></tr></table>"
    fake_llm.script(structured=[fake.page("Income", tables=[fake.table(html)])])

    result = run_llm(run_cli, scan, fake_llm, timeout=30)

    assert result.exit_code == 0, result.describe()
    assert re.search(r"Net\s+income", result.markdown) and "1,200" in result.markdown


_LINK_INJECTIONS = (
    "![x](javascript:alert(1)) [click](javascript:alert(2)) ![](https://attacker.example/pixel.png) "
    "[y](java&#115;cript:alert(3))"
)


def _links_and_images(markdown):
    rendered = build.render(markdown)
    hrefs = [a.get("href", "") for a in rendered.find_all("a")]
    return rendered.find_all("img") + [h for h in hrefs if not h.lower().startswith(("http:", "https:", "mailto:"))]


@pytest.mark.parametrize("where", ["raw.text", "page_markdown", "table.markdown", "free-form"])
def test_t10_ocr_text_cannot_create_images_or_script_links(run_cli, fake_llm, scan, where):
    if where == "raw.text":
        fake_llm.script(structured=[fake.page("Links " + _LINK_INJECTIONS + "\n\n[a]: javascript:alert(4)\n\n[a]")])
    elif where == "page_markdown":
        fake_llm.script(structured=[fake.page("Links", interpretation=fake.interpretation(
            page_markdown="## Links\n\n" + _LINK_INJECTIONS + "\n\n[a]: javascript:alert(4)\n\n[a]"))])
    elif where == "table.markdown":
        fake_llm.script(structured=[fake.page("Links", tables=[fake.table(
            markdown="| a |\n|---|\n| " + _LINK_INJECTIONS + " |")])])
    else:
        fake_llm.script(free_form=[fake.text("Links " + _LINK_INJECTIONS)])
    args = ("--no-structured",) if where == "free-form" else ()

    result = run_llm(run_cli, scan, fake_llm, *args)

    assert result.exit_code == 0, result.describe()
    assert _links_and_images(result.markdown) == [], result.describe()
    assert build.active_html(result.markdown) == [], result.describe()
    assert "click" in build.visible_text(result.markdown), "the text is kept, only neutralized"


def test_t10_code_lists_and_entities_render_as_written(run_cli, fake_llm, scan):
    page_markdown = (
        "## Setup\n\nUse `<div>` and `List<String>` here.\n\n```html\n<div>code</div>\n```\n\n"
        "- Fast\n- Cheap\n\n&copy; 2026 Acme"
    )
    fake_llm.script(structured=[fake.page(
        "Setup\nUse div and List String here.\ncode\nFast\nCheap\n2026 Acme",
        interpretation=fake.interpretation(page_markdown=page_markdown))])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    rendered = build.render(result.markdown)
    codes = [code.get_text() for code in rendered.find_all("code")]
    assert "<div>" in codes and "List<String>" in codes, codes
    assert any("<div>code</div>" in code for code in codes), codes
    assert [li.get_text() for li in rendered.find_all("li")] == ["Fast", "Cheap"], result.describe()
    assert "© 2026 Acme" in build.visible_text(result.markdown), result.describe()
    assert build.active_html(result.markdown) == [], result.describe()


def test_t10_transcribed_list_stays_a_list(run_cli, fake_llm, scan):
    """raw.text is a verbatim transcription: its bullets and numbered items are real lists,
    so the Markdown keeps them (no backslashes in the RAG text)."""
    fake_llm.script(structured=[fake.page("Agenda\n- Fast\n- Cheap\n1. Budget\n2. Hiring")])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    assert "- Fast" in result.markdown and "1. Budget" in result.markdown, result.describe()
    rendered = build.render(result.markdown)
    assert [li.get_text() for li in rendered.find_all("li")] == ["Fast", "Cheap", "Budget", "Hiring"]


def test_t10_plain_ocr_text_does_not_turn_into_markdown_structure(run_cli, fake_llm, scan):
    """raw.text is a verbatim transcription: a line that happens to start with '#' or '>' is
    text on the page, not a heading or a quote, so the reader must see the characters."""
    fake_llm.script(structured=[fake.page(
        "Board memo\n# 3 approved motions\n> Chair: J. Lin\nTotal\n==\n[1]: https://example.com/minutes\nClosing remarks"
    )])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    rendered = build.render(result.markdown)
    assert rendered.find(["h1", "h2", "h3", "blockquote"]) is None, result.describe()
    visible = build.visible_text(result.markdown)
    for line in ("# 3 approved motions", "> Chair: J. Lin", "Total", "[1]: https://example.com/minutes"):
        assert line in visible, visible
    # CommonMark turns the paragraph above a bare "==" / "--" line into a heading.
    assert not re.search(r"^ {0,3}(=+|-+)[ \t]*$", result.markdown, re.M), result.describe()


def test_t10_text_removed_by_sanitizing_page_markdown_is_still_indexed(run_cli, fake_llm, scan):
    """The sanitizer drops an <svg> with its text; that text must still reach the output
    (the verbatim coverage check has to judge the sanitized rendering)."""
    page_markdown = "## Costs\n\n<table><tr><td>Unit cost</td><td><svg><text>800</text></svg></td></tr></table>"
    fake_llm.script(structured=[fake.page(
        "Costs\nUnit cost 800", interpretation=fake.interpretation(page_markdown=page_markdown))])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    assert "800" in result.markdown and "<svg" not in result.markdown, result.describe()


@pytest.mark.parametrize("sentence", [
    "The <table> element represents tabular data.",
    "The <table> element holds <tr> rows, which hold <td> cells.",
])
def test_t10_model_markdown_that_mentions_a_table_tag_keeps_its_structure(run_cli, fake_llm, scan, sentence):
    """A literal "<table>" in prose is not a truncated table: the Markdown after it stays."""
    answer = (sentence + "\n\n## Attributes\n\n"
              "- border: width of the frame\n- summary: deprecated\n\n1. Step one\n2. Step two")
    fake_llm.script(free_form=[fake.text(answer)])

    result = run_llm(run_cli, scan, fake_llm, "--no-structured")

    assert result.exit_code == 0, result.describe()
    rendered = build.render(result.markdown)
    assert [h.get_text() for h in rendered.find_all("h2")] == ["Attributes"], result.describe()
    assert len(rendered.find_all("li")) == 4, result.describe()
    assert sentence in build.visible_text(result.markdown)


def test_t10_prose_mention_before_a_real_table_keeps_the_table(run_cli, fake_llm, scan):
    answer = "Use the <table> element, for example:\n\n<table><tr><td>A</td><td>B</td></tr></table>"
    fake_llm.script(free_form=[fake.text(answer)])

    result = run_llm(run_cli, scan, fake_llm, "--no-structured")

    assert result.exit_code == 0, result.describe()
    assert build.html_tables(result.markdown) == [[["A", "B"]]], result.describe()
    assert "Use the <table> element, for example:" in build.visible_text(result.markdown)


@pytest.mark.parametrize("markup", [
    "<table><td>Revenue</td><td>1,200</td></table>",
    "<table><!-- q3 --><tr><td>Revenue</td><td>1,200</td></tr></table>",
])
def test_t10_free_form_table_without_the_usual_row_markup_is_kept(run_cli, fake_llm, scan, markup):
    fake_llm.script(free_form=[fake.text("Q3 results\n\n" + markup)])

    result = run_llm(run_cli, scan, fake_llm, "--no-structured")

    assert result.exit_code == 0, result.describe()
    assert build.html_tables(result.markdown) == [[["Revenue", "1,200"]]], result.describe()


@pytest.mark.parametrize("answer", [
    pytest.param("Report\n<table>" + "<!-- row -->" * 30, id="comments-after-a-table-tag"),
    pytest.param("<table><!--a-->" * 40_000 + " end", id="table-tags-each-with-a-comment"),
    pytest.param("<table><!--" * 40_000, id="comments-that-never-end"),
    pytest.param("<table" * 200_000, id="table-tags-that-never-close"),
])
def test_t10_repeated_comments_and_table_tags_do_not_stall_the_conversion(run_cli, fake_llm, scan, answer):
    """Markup a model can repeat until max_tokens. None of it opens a table, and finding
    that out must take neither exponential nor quadratic time."""
    fake_llm.script(free_form=[fake.text(answer)])

    result = run_llm(run_cli, scan, fake_llm, "--no-structured", timeout=30)

    assert result.exit_code == 0, result.describe()
    assert build.html_tables(result.markdown) == [], result.describe()
    assert result.markdown.count("&lt;table") == answer.count("<table"), "every mention stays as text"


def test_t10_truncated_free_form_table_keeps_its_structure(run_cli, fake_llm, scan):
    """A free-form answer cut off inside a table (max_tokens) still yields a sanitized table,
    not the table's markup as escaped text."""
    answer = "Invoice\n\n<table><tr><th>Item</th><th>Cost</th></tr><tr><td>Paper</td><td>12</td></tr><tr><td>Toner"
    fake_llm.script(free_form=[fake.text(answer)])

    result = run_llm(run_cli, scan, fake_llm, "--no-structured")

    assert result.exit_code == 0, result.describe()
    assert build.html_tables(result.markdown) == [[["Item", "Cost"], ["Paper", "12"], ["Toner", ""]]], result.describe()
    assert "&lt;t" not in result.markdown, result.describe()


# --------------------------------------------------------------------------- #
# T19: ragged, nested and decorated OCR tables                                #
# --------------------------------------------------------------------------- #


def _table_run(run_cli, fake_llm, scan, *html_fields):
    fake_llm.script(structured=[fake.page("", tables=[fake.table(html) for html in html_fields])])
    result = run_llm(run_cli, scan, fake_llm)
    assert result.exit_code == 0, result.describe()
    return result


def test_t19_row_with_an_omitted_cell_stays_under_its_columns(run_cli, fake_llm, scan):
    result = _table_run(run_cli, fake_llm, scan, (
        "<table><tr><th>Item</th><th>Unit</th><th>2024</th></tr>"
        "<tr><td>Sales</td><td>NT$</td><td>100</td></tr><tr><td>Cost</td><td>80</td></tr></table>"
    ))
    assert build.html_tables(result.markdown) == [
        [["Item", "Unit", "2024"], ["Sales", "NT$", "100"], ["Cost", "", "80"]]
    ], result.describe()


def test_t19_row_with_an_omitted_leading_label_stays_under_its_columns(run_cli, fake_llm, scan):
    result = _table_run(run_cli, fake_llm, scan, (
        "<table><tr><th>Region</th><th>Q1</th><th>Q2</th></tr>"
        "<tr><th>North</th><td>1,200</td><td>1,350</td></tr><tr><td>980</td><td>1,010</td></tr></table>"
    ))
    assert build.html_tables(result.markdown) == [
        [["Region", "Q1", "Q2"], ["North", "1,200", "1,350"], ["", "980", "1,010"]]
    ], result.describe()


def test_t19_double_counted_rowspan_does_not_add_a_column(run_cli, fake_llm, scan):
    result = _table_run(run_cli, fake_llm, scan, (
        "<table><tr><th>Region</th><th>City</th><th>Sales</th></tr>"
        "<tr><td rowspan='2'>North</td><td>A</td><td>1</td></tr><tr><td></td><td>B</td><td>2</td></tr></table>"
    ))
    assert build.html_tables(result.markdown) == [
        [["Region", "City", "Sales"], ["North", "A", "1"], [None, "B", "2"]]
    ], result.describe()


def test_t19_two_tables_in_one_field_keep_their_own_grids(run_cli, fake_llm, scan):
    result = _table_run(run_cli, fake_llm, scan, (
        "<table><tr><td>a</td><td>b</td><td>c</td></tr></table><table><tr><td>x</td></tr></table>"
    ))
    assert build.html_tables(result.markdown) == [[["a", "b", "c"]], [["x"]]], result.describe()


def test_t19_nested_table_is_kept_and_not_folded_into_the_outer_grid(run_cli, fake_llm, scan):
    result = _table_run(run_cli, fake_llm, scan, (
        "<table><tr><td>outer</td><td><table><tr><td>i1</td><td>i2</td><td>i3</td></tr></table></td></tr>"
        "<tr><td>o2</td><td>o3</td></tr></table>"
    ))
    outer, inner = build.html_tables(result.markdown, nested=True)
    assert outer == [["outer", "i1 i2 i3"], ["o2", "o3"]], result.describe()
    assert inner == [["i1", "i2", "i3"]], result.describe()


def test_t19_rowspan_zero_spans_to_the_end_of_its_row_group(run_cli, fake_llm, scan):
    result = _table_run(run_cli, fake_llm, scan, (
        "<table><tr><td rowspan='0'>x</td><td>1</td></tr><tr><td>2</td></tr><tr><td>3</td></tr></table>"
    ))
    assert build.html_tables(result.markdown) == [[["x", "1"], [None, "2"], [None, "3"]]], result.describe()


def test_t19_caption_and_unit_text_next_to_the_table_are_kept(run_cli, fake_llm, scan):
    result = _table_run(
        run_cli, fake_llm, scan,
        "Table 3: Revenue by region<table><tr><td>North</td><td>1</td></tr></table>",
        "<p>Unit: NT$ thousand</p><table><tr><td>South</td><td>2</td></tr></table>",
    )
    visible = build.visible_text(result.markdown)
    assert "Table 3: Revenue by region" in visible, result.describe()
    assert "Unit: NT$ thousand" in visible, result.describe()
    assert build.html_tables(result.markdown) == [[["North", "1"]], [["South", "2"]]], result.describe()


@pytest.mark.parametrize("html", [
    "<table>Totals<tr><td>North</td>(provisional)<td>1</td></tr><tr><td>South</td><td>2</td></tr>Unit: NT$</table>",
    "<table>Totals<td>North</td>(provisional)<td>1</td><tr><td>South</td><td>2</td></tr>Unit: NT$</table>",
])
def test_t19_text_between_rows_and_cells_keeps_its_reading_order(run_cli, fake_llm, scan, html):
    result = _table_run(run_cli, fake_llm, scan, html)
    visible = build.visible_text(result.markdown)
    assert re.search(r"Totals.*\(provisional\).*Unit: NT\$", visible), result.describe()
    assert build.html_tables(result.markdown) == [[["North", "1"], ["South", "2"]]], result.describe()


def test_t19_markdown_table_in_the_html_field_is_kept(run_cli, fake_llm, scan):
    result = _table_run(run_cli, fake_llm, scan, "| Region | Sales |\n|---|---|\n| North | 1,200 |")
    assert build.html_tables(result.markdown) == [[["Region", "Sales"], ["North", "1,200"]]], result.describe()


# --------------------------------------------------------------------------- #
# T20: the flat headers/rows fallback table                                   #
# --------------------------------------------------------------------------- #


def test_t20_flat_table_cells_with_pipes_and_newlines_stay_in_their_cells(run_cli, fake_llm, scan):
    flat = fake.table(headers=["Plan", "Price"], rows=[["Basic | Lite", "10"], ["Pro\nannual", "99"]])
    fake_llm.script(structured=[fake.page("", tables=[flat])])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    assert build.pipe_table_rows(result.markdown) == [
        ["Plan", "Price"], ["Basic | Lite", "10"], ["Pro annual", "99"]
    ], result.describe()


# --------------------------------------------------------------------------- #
# R-F14: runtime router firewall                                              #
# --------------------------------------------------------------------------- #

_WITHHELD_HEADER = "<table><tr><th>Account</th><th>Amount</th></tr></table>"
_VERBATIM_LEDGER = (
    "<table><tr><th>Account</th><th>Amount</th></tr><tr><td>Cash</td><td>1,250</td></tr>"
    "<tr><td>Receivables</td><td>3,475</td></tr></table>"
)


@pytest.mark.parametrize("document_type, fidelity", [("table", "verbatim"), ("screenshot", "described")])
def test_rf14_withholding_result_is_redone_verbatim(run_cli, fake_llm, scan, document_type, fidelity):
    """An illustrative (withheld) table on a non-screenshot page violates the firewall; so does
    any withholding while no neighbor-page context is attached (the CLI never attaches one)."""
    withheld = fake.page("Q3 ledger", tables=[fake.table(_WITHHELD_HEADER, illustrative=True, row_count=25)],
                         interpretation=fake.interpretation(document_type=document_type, content_fidelity=fidelity,
                                                            summary="A ledger screen.", self_confidence=0.95,
                                                            legibility="high"))
    verbatim = fake.page("Q3 ledger", tables=[fake.table(_VERBATIM_LEDGER)],
                         interpretation=fake.interpretation(document_type="table", summary="A ledger."))
    fake_llm.script(structured=[withheld, verbatim])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    assert build.html_tables(result.markdown) == [
        [["Account", "Amount"], ["Cash", "1,250"], ["Receivables", "3,475"]]
    ], result.describe()
    assert len(fake_llm.requests_of("structured")) == 2


def test_rf14_rows_still_withheld_after_the_retry_leave_a_visible_marker(run_cli, fake_llm, scan):
    withheld = fake.page("Q3 ledger", tables=[fake.table(_WITHHELD_HEADER, illustrative=True, row_count=25)],
                         interpretation=fake.interpretation(document_type="table", summary="A ledger."))
    fake_llm.script(structured=[withheld])

    result = run_llm(run_cli, scan, fake_llm, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert re.search(r"\b25\b[^\n]*\brows\b[^\n]*not transcribed", result.markdown), result.describe()
    assert ocr_issues(result).get("withheld") == 1, result.json


def test_rf14_withheld_metrics_and_figures_leave_a_visible_marker(run_cli, fake_llm, scan):
    withheld = fake.page(
        "Sales dashboard",
        metrics=[{"label": "MRR", "value": "$12,000", "unit": "", "illustrative": True},
                 {"label": "Churn", "value": "2%", "unit": "", "illustrative": True}],
        interpretation=fake.interpretation(
            document_type="chart", summary="A sales dashboard.",
            figures=[{"kind": "line", "title": "Signups", "meaning": "Signups grow", "illustrative": True}]),
    )
    fake_llm.script(structured=[withheld])

    result = run_llm(run_cli, scan, fake_llm, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert "[2 illustrative metrics not transcribed]" in result.markdown, result.describe()
    assert "[1 illustrative figure not transcribed]" in result.markdown, result.describe()
    assert ocr_issues(result).get("withheld") == 1, result.json


def test_rf14_router_is_told_context_is_absent_when_no_context_pdf_is_attached(run_cli, fake_llm, scan):
    fake_llm.script(structured=[fake.page("Plain page text")])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    [request] = fake_llm.requests_of("structured")
    prompt = FakeOpenAI.prompt_text(request)
    assert re.search(r"context is absent", prompt, re.I) and "VERBATIM" in prompt, prompt


# --------------------------------------------------------------------------- #
# Refusals and "no readable text" answers                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("structured_reply", [
    pytest.param(fake.refusal(REFUSAL), id="native-refusal-field"),
    pytest.param(fake.text(REFUSAL), id="schema-ignored-refusal-text"),
    pytest.param(fake.page(REFUSAL), id="refusal-as-transcription"),
])
def test_refusal_is_not_indexed_as_page_content(run_cli, fake_llm, scan, structured_reply):
    fake_llm.script(structured=[structured_reply], free_form=[fake.text(REFUSAL)])

    result = run_llm(run_cli, scan, fake_llm, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert "sorry" not in result.markdown.lower(), result.describe()
    assert ocr_issues(result).get("refused") == 1, result.json
    assert len(fake_llm.requests_of("free_form")) == 1, "the free-form recovery must be tried first"


@pytest.mark.parametrize("answer", [
    "抱歉，我無法辨識這張圖片中的文字。",
    "圖片中沒有可辨識的文字。",
    "申し訳ありませんが、この画像の文字を読み取ることができません。",
    "Leider kann ich den Text in diesem Bild nicht erkennen.",
    "No text detected.",
])
def test_multilingual_no_text_answers_are_not_indexed(run_cli, fake_llm, scan, answer):
    fake_llm.script(structured=[fake.page(answer)], free_form=[fake.text(answer)])

    result = run_llm(run_cli, scan, fake_llm, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert build.squash(answer) not in build.squash(result.markdown), result.describe()
    assert ocr_issues(result).get("refused") == 1, result.json


@pytest.mark.parametrize("answer", [
    "No text detected in image",
    "The image does not contain any text.",
    "I apologize, but I cannot provide the text from this image as it appears to contain copyrighted material.",
    "I'm sorry, but I can't assist with that. If you have any other questions, feel free to ask!",
    "I'm unable to read the text in this image. It appears to be blurry or low resolution.\n"
    "If you could provide a clearer image, I'd be happy to help!",
])
def test_whole_answer_refusal_with_a_reason_or_a_courtesy_tail_is_not_indexed(run_cli, fake_llm, scan, answer):
    fake_llm.script(structured=[fake.page(answer)], free_form=[fake.text(answer)])

    result = run_llm(run_cli, scan, fake_llm, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert build.squash(answer[:24]) not in build.squash(result.markdown), result.describe()
    assert ocr_issues(result).get("refused") == 1, result.json


@pytest.mark.parametrize("answer", [
    "There is no readable text in this image. It seems to be a decorative background graphic.",
    "Unable to process the image. Please provide a clearer scan of the page.",
])
def test_answer_the_patterns_cannot_decide_is_left_to_the_judge(run_cli, fake_llm, scan, answer):
    """A description of the image, or a notice without a first person, reads the same as
    page text: it is left to OCRConfig.non_content_judge; the CLI has no judge, so the
    answer is kept (verbatim first)."""
    fake_llm.script(structured=[fake.page(answer)], free_form=[fake.text("unused")])

    result = run_llm(run_cli, scan, fake_llm, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert build.normalize(answer) in build.normalize(result.markdown), result.describe()
    assert not ocr_issues(result), result.json
    assert fake_llm.requests_of("free_form") == [], "kept content must not be re-OCR'd"


# Short answers that open with a refusal or "no text" clause and go on with content (a
# partial transcription, a description, a quote): the content must stay.
_CLAUSE_THEN_CONTENT = [
    ("There is no text in this image. It shows a bar chart with revenue rising from 120 to 171 across Q1-Q4.",
     "120 to 171"),
    ("I can't transcribe the handwritten text, but the printed part reads:\nInvoice No. 2026-0917\nTotal due: $450",
     "2026-0917"),
    ("Invoice No. 2026-0917\nTotal due: $450\n(The signature at the bottom is illegible, so I can't transcribe it.)",
     "2026-0917"),
    ("The image is mostly blank except for a small caption: 'Figure 3 - Plant layout'.", "Plant layout"),
    ("I don't see any text in the image, but it shows a red octagonal stop sign on a pole.", "stop sign"),
    ("As an AI, I can't identify the person in the photo, but the name badge reads 'Dr. Lee, Chief Engineer'.",
     "Chief Engineer"),
    ("죄송하지만 이 이미지의 손글씨는 인식할 수 없습니다. 인쇄된 부분: 청구서 번호 2026-0917 합계 45,000원", "45,000"),
    ("Leider kann ich den handschriftlichen Text im Bild nicht erkennen. Gedruckt: Rechnung Nr. 4711, Betrag 450 EUR",
     "4711"),
]


@pytest.mark.parametrize("answer, content", _CLAUSE_THEN_CONTENT)
def test_answer_that_goes_on_with_content_after_a_refusal_clause_is_kept(run_cli, fake_llm, scan, answer, content):
    fake_llm.script(structured=[fake.page(answer)], free_form=[fake.text("unused")])

    result = run_llm(run_cli, scan, fake_llm, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert content in result.markdown, result.describe()
    assert not ocr_issues(result), result.json
    assert fake_llm.requests_of("free_form") == [], "content must not be re-OCR'd"


def test_free_form_answer_that_goes_on_with_content_is_kept(run_cli, fake_llm, scan):
    answer, content = _CLAUSE_THEN_CONTENT[0]
    fake_llm.script(free_form=[fake.text(answer)])

    result = run_llm(run_cli, scan, fake_llm, "--no-structured", fmt="both")

    assert result.exit_code == 0, result.describe()
    assert content in result.markdown, result.describe()
    assert not ocr_issues(result), result.json


def test_no_text_answer_does_not_wipe_the_figures_and_metrics_of_its_page(run_cli, fake_llm, scan):
    page = fake.page(
        "There is no text in this image.",
        metrics=[{"label": "Revenue Q4", "value": "171", "unit": "", "illustrative": False}],
        interpretation=fake.interpretation(
            document_type="chart",
            figures=[{"kind": "bar", "title": "Quarterly revenue", "meaning": "Revenue grows every quarter"}]),
    )
    fake_llm.script(structured=[page], free_form=[fake.text("unused")])

    result = run_llm(run_cli, scan, fake_llm, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert "Quarterly revenue" in result.markdown and "Revenue Q4" in result.markdown, result.describe()
    assert not ocr_issues(result), result.json
    assert fake_llm.requests_of("free_form") == [], "a page with content must not be re-OCR'd"


def test_refused_page_leaves_a_marker_and_its_location(run_cli, fake_llm, scan):
    fake_llm.script(structured=[fake.page(REFUSAL)], free_form=[fake.text(REFUSAL)])

    result = run_llm(run_cli, scan, fake_llm, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert "[page 1: OCR returned no content]" in build.visible_text(result.markdown), result.describe()
    issues = ocr_issues(result)
    assert issues.get("refused") == 1, result.json
    assert issues.get("locations") == [{"issue": "refused", "image": 1, "page": 1}], result.json


def test_free_form_refusal_is_reported(run_cli, fake_llm, scan):
    fake_llm.script(free_form=[fake.refusal(REFUSAL)])

    result = run_llm(run_cli, scan, fake_llm, "--no-structured", fmt="both")

    assert result.exit_code == 0, result.describe()
    assert "sorry" not in result.markdown.lower(), result.describe()
    assert ocr_issues(result).get("refused") == 1, result.json


def test_refused_structured_answer_is_recovered_by_free_form_ocr(run_cli, fake_llm, scan):
    fake_llm.script(structured=[fake.page(REFUSAL)], free_form=[fake.text("Board minutes 2026\nBudget approved")])

    result = run_llm(run_cli, scan, fake_llm, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert "Board minutes 2026" in result.markdown and "sorry" not in result.markdown.lower(), result.describe()
    assert not ocr_issues(result), result.json


@pytest.mark.parametrize("content", [
    "Sorry we missed you!\nWe tried to deliver your parcel today. Scan the QR code to reschedule delivery.",
    "No text? No problem.\nOur OCR engine reads handwriting, stamps and faded scans.",
    "This page intentionally left blank.",
    "Dear Mr. Chen,\nI am sorry to inform you that your application was not successful.",
    "We're sorry, we are unable to process your payment at this time.",
    "Unable to read the file.\nError code 0x80070570\nContact your administrator",
    "無法讀取檔案內容，請稍後再試。",
    "As an AI-first company, we ship faster.",
    "This page is intentionally left blank.",
    "無法處理的問題，請參考下圖。",
    "Hi team, I can't read the text in the attachment - can you resend it?",
    "Sorry, I cannot read the document you sent.",
    "Unable to process the uploaded image. Please upload a JPG or PNG under 10 MB.",
    "The photo is blurry.\nHold the camera steady and retake it.",
    "As an AI assistant, Aria answers customer questions 24/7.",
    "I can't read the picture, it's too blurry. Can you retake it?",
    "There is no visible content in this folder.",
    "Leider kann ich Ihre Bildungsnachweise nicht verarbeiten, da die Unterschrift fehlt.",
    "I can't read the scans until Dr. Lee signs off.",
    "Sorry, I can't read the scans until Dr. Lee signs off.",
    "很抱歉，我無法處理這批照片，下週一再處理。",
    "Sorry, I can't help with reading the contract until Monday.",
    "I'm sorry, I can't provide a transcription of the hearing until the judge approves it.",
    "Sorry, I can't read the scans. It seems Dr. Lee has them.",
    "I can't help. It is too late to change the order. Ref #4411",
    "Sorry, I can't help with that. However, I can ask Mark tomorrow.",
    "I can't read the scan. It seems to have been corrupted. Total: $500",
    "I'm sorry Dave, I'm afraid I can't do that.",
    "Leider kann ich das Foto nicht lesen, kannst du es nochmal schicken?",
    "No pude leer la foto, ¿me la mandas otra vez?",
])
def test_real_content_that_mentions_apologies_is_kept(run_cli, fake_llm, scan, content):
    fake_llm.script(structured=[fake.page(content)], free_form=[fake.text("unused")])

    result = run_llm(run_cli, scan, fake_llm)

    assert result.exit_code == 0, result.describe()
    assert build.normalize(content) in build.normalize(result.markdown), result.describe()
    assert fake_llm.requests_of("free_form") == [], "real content must not be re-OCR'd"


# --------------------------------------------------------------------------- #
# Tesseract: --ocr-lang                                                       #
# --------------------------------------------------------------------------- #

# Lines Tesseract's chi_tra / chi_sim models read exactly at this size (checked on the E2E image).
_TRADITIONAL = ["台北市政府公告", "客戶服務中心"]
_SIMPLIFIED = ["北京市人民政府", "今天天气很好"]


@pytest.mark.parametrize("ocr_lang, script, lines", [
    ("chi_tra", "TC", _TRADITIONAL),
    ("chi_sim", "SC", _SIMPLIFIED),
    ("chinese_traditional", "TC", _TRADITIONAL),
])
def test_tesseract_reads_cjk_with_the_requested_language(run_cli, require_tool, e2e_dir, ocr_lang, script, lines):
    require_tool("tesseract")
    pdf = build.text_scan_pdf(e2e_dir / "cjk.pdf", lines, build.cjk_font(script))

    result = run_cli(pdf, "--ocr", "tesseract", "--ocr-images", "--ocr-lang", ocr_lang)

    assert result.exit_code == 0, result.describe()
    for line in lines:
        assert line in build.squash(result.markdown), result.describe()


def test_tesseract_combined_language_codes_read_both_scripts(run_cli, require_tool, e2e_dir):
    require_tool("tesseract")
    font = build.cjk_font("TC")
    pdf = build.text_scan_pdf(e2e_dir / "mixed.pdf", ["INVOICE 2026", "台北市政府公告"], font)

    result = run_cli(pdf, "--ocr", "tesseract", "--ocr-images", "--ocr-lang", "eng+chi_tra")

    assert result.exit_code == 0, result.describe()
    assert "INVOICE2026" in build.squash(result.markdown), result.describe()
    assert "台北市政府公告" in build.squash(result.markdown), result.describe()


def test_tesseract_unknown_language_fails_loudly(run_cli, require_tool, e2e_dir):
    require_tool("tesseract")
    pdf = pdfgen.image_pdf(e2e_dir / "scan.pdf", "HELLO 2026")

    result = run_cli(pdf, "--ocr", "tesseract", "--ocr-images", "--ocr-lang", "klingon")

    assert result.exit_code != 0, result.describe()
    assert "klingon" in result.stderr, result.describe()
    assert result.markdown is None, "no output may be written for a failed OCR run"


# --------------------------------------------------------------------------- #
# Tesseract: engine failures are visible                                      #
# --------------------------------------------------------------------------- #


def test_tesseract_engine_failure_exits_non_zero(run_cli, require_tool, e2e_dir):
    require_tool("tesseract")
    pdf = pdfgen.image_pdf(e2e_dir / "scan.pdf", "HELLO 2026")

    result = run_cli(pdf, "--ocr", "tesseract", "--ocr-images",
                     env={"TESSDATA_PREFIX": str(e2e_dir / "no-tessdata")})

    assert result.exit_code != 0, result.describe()
    assert "TESSDATA_PREFIX" in result.stderr or "tessdata" in result.stderr, result.describe()
    assert result.markdown is None, "a placeholder-only document must not be written as a success"


def test_tesseract_engine_failure_in_a_batch_is_reported_and_skipped(run_cli, require_tool, e2e_dir):
    require_tool("tesseract")
    docs = e2e_dir / "docs"
    docs.mkdir()
    pdfgen.text_pdf(docs / "notes.pdf", "Meeting notes with a real text layer")
    pdfgen.image_pdf(docs / "scan.pdf", "HELLO 2026")
    out = e2e_dir / "out"

    result = run_cli(docs, "-o", out, "--ocr", "tesseract", "--ocr-images", "--skip-errors", "--progress", "none",
                     raw=True, env={"TESSDATA_PREFIX": str(e2e_dir / "no-tessdata")})

    assert (out / "notes.md").exists(), result.describe()
    assert "Meeting notes with a real text layer" in (out / "notes.md").read_text(encoding="utf-8")
    assert not (out / "scan.md").exists(), result.describe()
    assert "scan.pdf" in result.stderr and "tessdata" in result.stderr.lower(), result.describe()
    assert "Failed: 1" in result.stdout, result.describe()


# --------------------------------------------------------------------------- #
# Real OCR path regression                                                    #
# --------------------------------------------------------------------------- #


def test_tesseract_real_ocr_path_keeps_every_line(run_cli, require_tool, e2e_dir):
    """Real OCR, no mocks: the sanitised Tesseract path still delivers every line."""
    require_tool("tesseract")
    pdf = pdfgen.image_pdf(e2e_dir / "report.pdf", "QUARTERLY REVIEW 2026\n1. Budget approved\nRevenue up 12%")

    result = run_cli(pdf, "--ocr", "tesseract", "--ocr-images", fmt="both")

    assert result.exit_code == 0, result.describe()
    text = build.visible_text(result.markdown)
    for expected in ("QUARTERLY REVIEW 2026", "1. Budget approved", "Revenue up 12%"):
        assert expected in text, result.describe()
    assert not ocr_issues(result), result.json
