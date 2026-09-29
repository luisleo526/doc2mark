"""E2E tests for Word/Excel/PowerPoint conversion (office lane): nothing is silently lost.

Every test builds its document at test time (tests/e2e/builders_office.py), runs the real
``doc2mark`` CLI on it and asserts on the Markdown/JSON the CLI writes. OCR runs use the real
Tesseract; the Office image route uses the real LibreOffice.
"""

import html
import json
import re
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

import pytest

from tests.e2e import builders_office as office

# --------------------------------------------------------------------------- output parsing


class Table:
    """One table from the output: ``grid[r][c]`` is the cell text (``<br>`` as ``\\n``), or
    None where a rowspan/colspan from another cell covers the position."""

    def __init__(self, grid: List[List[Optional[str]]], spans: Dict[Tuple[int, int], Tuple[int, int]], start: int):
        self.grid = grid
        self.spans = spans
        self.start = start  # offset of the table in the Markdown

    def __repr__(self):
        return f"Table(grid={self.grid!r}, spans={self.spans!r})"


class _HtmlTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: List[List[Tuple[str, int, int]]] = []
        self._cell: Optional[List[str]] = None
        self._span = (1, 1)

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            attributes = dict(attrs)
            self._cell = []
            self._span = (int(attributes.get("rowspan", 1)), int(attributes.get("colspan", 1)))
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self.rows[-1].append(("".join(self._cell).strip(), *self._span))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _expand(rows: List[List[Tuple[str, int, int]]]) -> Tuple[List[List[Optional[str]]], dict]:
    grid: Dict[Tuple[int, int], Optional[str]] = {}
    spans = {}
    for r, row in enumerate(rows):
        c = 0
        for text, rowspan, colspan in row:
            while (r, c) in grid:
                c += 1
            grid[(r, c)] = text
            if rowspan > 1 or colspan > 1:
                spans[(r, c)] = (rowspan, colspan)
            for dr in range(rowspan):
                for dc in range(colspan):
                    if dr or dc:
                        grid[(r + dr, c + dc)] = None
            c += colspan
    if not grid:
        return [], spans
    height = max(r for r, _ in grid) + 1
    width = max(c for _, c in grid) + 1
    return [[grid.get((r, c), "") for c in range(width)] for r in range(height)], spans


def _markdown_cell(text: str) -> str:
    text = text.strip().replace("\\|", "|")
    return html.unescape(re.sub(r"<br\s*/?>", "\n", text))


_SEPARATOR = re.compile(r"^\s*\|(\s*:?-{3,}:?\s*\|)+\s*$")


def tables(markdown: str) -> List[Table]:
    """Every HTML (``<table>``) and pipe table in ``markdown``, in document order."""
    found = []
    for match in re.finditer(r"<table\b.*?</table>", markdown, re.S):
        parser = _HtmlTableParser()
        parser.feed(match.group(0))
        grid, spans = _expand(parser.rows)
        found.append(Table(grid, spans, match.start()))
    lines = markdown.split("\n")
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line) + 1
    i = 0
    while i < len(lines) - 1:
        if lines[i].lstrip().startswith("|") and _SEPARATOR.match(lines[i + 1]):
            block = [lines[i]]
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                block.append(lines[j])
                j += 1
            rows = [[_markdown_cell(cell) for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
                    for line in block]
            found.append(Table(rows, {}, offsets[i]))
            i = j
        else:
            i += 1
    return sorted(found, key=lambda table: table.start)


def sections(markdown: str, label: str) -> Dict[int, str]:
    """Split the output on ``<!-- <label> N -->`` markers: {N: text of that part}."""
    parts = re.split(rf"<!-- {label} (\d+) -->", markdown)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def only_table(text: str, describe) -> Table:
    found = tables(text)
    assert len(found) == 1, f"expected exactly one table, found {len(found)}\n{describe()}"
    return found[0]


def routed_via(result) -> Optional[str]:
    return ((result.json or {}).get("metadata", {}).get("extra") or {}).get("routed_via")


# --------------------------------------------------------------------------- XLSX (T1, T13, T14)


@pytest.mark.parametrize("blank_rate,seed", [(0.05, 105), (0.15, 115), (0.30, 130)])
def test_t1_xlsx_without_merges_keeps_every_value_in_its_cell(run_cli, e2e_dir, blank_rate, seed):
    """T1: a sheet with no merged range must never get rowspan/colspan inferred from blanks."""
    path = e2e_dir / f"sparse_{int(blank_rate * 100)}.xlsx"
    expected = office.random_sparse_workbook(path, blank_rate, sheets=40, seed=seed)

    result = run_cli(path, "--ocr", "none")

    assert result.exit_code == 0, result.describe()
    parts = sections(result.markdown, "sheet")
    assert sorted(parts) == list(range(1, len(expected) + 1)), result.describe()
    lost, wrong = [], []
    for number, grid in enumerate(expected, start=1):
        table = only_table(parts[number], result.describe)
        assert not table.spans, f"sheet {number}: spans invented from blank cells: {table.spans}"
        for r, row in enumerate(grid):
            for c, value in enumerate(row):
                got = table.grid[r][c] if r < len(table.grid) and c < len(table.grid[r]) else "<missing>"
                if got != value:
                    (lost if value and value not in parts[number] else wrong).append((number, r, c, value, got))
    assert not lost and not wrong, f"lost values: {lost[:10]}\nmisplaced values: {wrong[:10]}"


def test_t1_xlsx_explicit_merges_still_span_and_blanks_stay_blank(run_cli, e2e_dir):
    path = office.workbook(e2e_dir / "merged.xlsx", [{
        "title": "Regions",
        "rows": [["Region", "City", "Q1", "Q2"], ["North", "Taipei", 10, None], [None, "Hsinchu", None, 20],
                 ["South", "Kaohsiung", 30, 40]],
        "merges": ["A2:A3"],
    }])

    result = run_cli(path, "--ocr", "none")

    assert result.exit_code == 0, result.describe()
    table = only_table(result.markdown, result.describe)
    assert table.spans == {(1, 0): (2, 1)}, table
    assert table.grid == [["Region", "City", "Q1", "Q2"], ["North", "Taipei", "10", ""],
                          [None, "Hsinchu", "", "20"], ["South", "Kaohsiung", "30", "40"]], table


def test_t13_xlsx_header_only_single_cell_and_uncached_formula_sheets_are_kept(run_cli, e2e_dir):
    path = office.workbook(e2e_dir / "small.xlsx", [
        {"title": "Invoices", "rows": [["Invoice No", "Date", "Amount"]]},
        {"title": "Note", "rows": [["Totals verified by auditor on 2026-03-31"]]},
        {"title": "Calc", "rows": [["a", "b", "sum", "product"], [1, 2, "=A2+B2", "=A2*B2"]]},
    ])
    office.set_cached_formula_value(path, 3, "D2", "2")  # Excel-saved style cache for D2 only

    result = run_cli(path, "--ocr", "none")

    assert result.exit_code == 0, result.describe()
    parts = sections(result.markdown, "sheet")
    header_only = only_table(parts[1], result.describe)
    assert header_only.grid == [["Invoice No", "Date", "Amount"]], header_only
    assert "Totals verified by auditor on 2026-03-31" in parts[2], result.describe()
    calc = only_table(parts[3], result.describe)
    # C2 has no cached value: its formula is shown; D2 shows the cached value, not the formula.
    assert calc.grid == [["a", "b", "sum", "product"], ["1", "2", "=A2+B2", "2"]], calc


def test_t14_xlsx_values_keep_their_displayed_form(run_cli, e2e_dir):
    rows, formats, expected = office.xlsx_formats_rows()
    path = office.workbook(e2e_dir / "values.xlsx", [
        {"title": "Values", "rows": rows, "formats": formats},
        {"title": "Errors", "rows": [["metric", "value"], ["ratio", "#DIV/0!"]]},
    ])

    result = run_cli(path, "--ocr", "none")

    assert result.exit_code == 0, result.describe()
    parts = sections(result.markdown, "sheet")
    grid = only_table(parts[1], result.describe).grid
    mismatches = {pos: (grid[pos[0]][pos[1]], want) for pos, want in expected.items() if grid[pos[0]][pos[1]] != want}
    assert not mismatches, f"(row, col): (got, displayed) -> {mismatches}\n{result.describe()}"
    assert only_table(parts[2], result.describe).grid == [["metric", "value"], ["ratio", "#DIV/0!"]]


def test_t14_xlsx_title_rows_are_text_above_the_real_header(run_cli, e2e_dir):
    path = office.workbook(e2e_dir / "titled.xlsx", [{
        "title": "Report",
        "rows": [["ACME Corp - Sales Report FY2025"], [], ["Region", "Units", "Revenue"],
                 ["North", 10, 100], ["South", 20, 200]],
    }])

    result = run_cli(path, "--ocr", "none")

    assert result.exit_code == 0, result.describe()
    assert "Unnamed" not in result.markdown, result.describe()
    table = only_table(result.markdown, result.describe)
    assert table.grid == [["Region", "Units", "Revenue"], ["North", "10", "100"], ["South", "20", "200"]], table
    title_at = result.markdown.find("ACME Corp - Sales Report FY2025")
    assert 0 <= title_at < table.start, result.describe()


def test_t14_xlsx_merged_sheet_has_no_empty_rows(run_cli, e2e_dir):
    path = office.workbook(e2e_dir / "gaps.xlsx", [{
        "title": "Gaps",
        "rows": [["Summary report", None, None], [], ["Region", "Q1", "Q2"], ["North", 1, 2], [], [],
                 ["South", 3, 4]],
        "merges": ["A1:C1"],
    }])

    result = run_cli(path, "--ocr", "none")

    assert result.exit_code == 0, result.describe()
    assert not re.search(r"<tr>\s*</tr>", result.markdown), result.describe()
    table = only_table(result.markdown, result.describe)
    empty_rows = [row for row in table.grid if not any(cell for cell in row)]
    assert not empty_rows, table
    assert [row for row in table.grid if row[0] in ("North", "South")] == [["North", "1", "2"], ["South", "3", "4"]]


def test_t14_xlsx_picture_anchor_is_not_a_fake_value_error(run_cli, e2e_dir):
    picture = office.optional_png("LOGO")
    path = office.workbook(e2e_dir / "pictures.xlsx", [{
        "title": "Pictures",
        "rows": [["Label", "Picture"], ["logo", None], ["chart", None]],
        "images": {"B2": picture, "B3": picture},
    }])

    plain = run_cli(path, "--ocr", "none")
    extracted = run_cli(path, "--ocr", "none", "--extract-images")

    for result in (plain, extracted):
        assert result.exit_code == 0, result.describe()
        assert "#VALUE!" not in result.markdown, result.describe()
    grid = only_table(extracted.markdown, extracted.describe).grid
    assert [grid[1][1], grid[2][1]] == ["[Image]", "[Image]"], extracted.describe()


def test_t14_xlsx_picture_ocr_text_lands_in_its_cell_once(run_cli, require_tool, e2e_dir):
    require_tool("tesseract")
    path = office.workbook(e2e_dir / "scan.xlsx", [{
        "title": "Scan",
        "rows": [["Label", "Picture"], ["receipt", None], ["total", 42]],
        "images": {"B2": office.optional_png("CELL 5831")},
    }])

    result = run_cli(path, "--ocr", "tesseract", "--ocr-images")

    assert result.exit_code == 0, result.describe()
    assert "#VALUE!" not in result.markdown, result.describe()
    grid = only_table(result.markdown, result.describe).grid
    assert "5831" in grid[1][1] and grid[1][0] == "receipt", result.describe()
    assert grid[2] == ["total", "42"], result.describe()
    assert result.markdown.count("5831") == 1, result.describe()


# --------------------------------------------------------------------------- DOCX (T2, T7, H-F16)


def test_t2_docx_cell_keeps_text_inside_links_controls_revisions_and_fields(run_cli, e2e_dir):
    path = office.docx_cell_constructs(e2e_dir / "cells.docx")

    result = run_cli(path, "--ocr", "none")

    assert result.exit_code == 0, result.describe()
    cells = {row[0]: row[1] for row in only_table(result.markdown, result.describe).grid[1:]}
    assert cells == {
        "hyperlink": "Email: sales@example.com",
        "inline control": "Name: Wang Xiao-Ming",
        "block control": "A123456789",
        "tracked change": "Payment within 30 days",
        "simple field": "1,250",
        "smart tag": "Due March 31, 2026",
        "custom xml": "INV-2026-0042",
        "complex field": "9,999",
    }, result.describe()
    assert "DELETED-7731" not in result.markdown, result.describe()


def test_t2_docx_body_keeps_text_inside_controls_revisions_and_fields(run_cli, e2e_dir):
    """Same root cause as T2 (only direct ``w:r`` children were read), in body paragraphs."""
    path = office.docx_body_constructs(e2e_dir / "body.docx")

    result = run_cli(path, "--ocr", "none")

    assert result.exit_code == 0, result.describe()
    md = result.markdown
    for text in ("Pay within 45 days of invoice", "Applicant: Chen Mei-Ling", "Total: 8,640",
                 "Signed on April 2, 2026", "Block control paragraph 6120"):
        assert text in md, f"{text!r} missing\n{result.describe()}"
    assert md.find("Block control paragraph 6120") < md.find("Closing paragraph"), result.describe()
    assert "DELETED-5518" not in md, result.describe()


def test_t2_docx_nested_table_is_flattened_into_its_parent_cell(run_cli, e2e_dir):
    path = office.docx_nested_table(e2e_dir / "nested.docx")

    result = run_cli(path, "--ocr", "none")

    assert result.exit_code == 0, result.describe()
    table = only_table(result.markdown, result.describe)
    assert table.grid[0] == ["Outer A", "Outer B"] and table.grid[1][0] == "Left", table
    # One line per nested row, nested cells joined by " | ", in document order.
    assert table.grid[1][1].split("\n") == ["Terms:", "InnerKey | InnerVal", "Deadline | 2026-03-31", "After inner"], table
    md = result.markdown
    assert md.find("Before the table") < table.start < md.find("After the table"), result.describe()


@pytest.mark.parametrize("kind,row,expected_row", [
    ("before", 1, ["", "b", "c"]),
    ("after", 1, ["a", "b", ""]),
    ("before", 2, ["", "e", "f"]),
])
def test_t7_docx_rows_with_grid_before_or_after(run_cli, e2e_dir, kind, row, expected_row):
    path = office.docx_grid_skip(e2e_dir / f"grid_{kind}_{row}.docx", kind, row)

    result = run_cli(path, "--ocr", "none")

    assert result.exit_code == 0, result.describe()
    table = only_table(result.markdown, result.describe)
    expected = [["H1", "H2", "H3"], ["a", "b", "c"], ["d", "e", "f"]]
    expected[row] = expected_row
    assert table.grid == expected, table
    md = result.markdown
    assert md.find("Intro paragraph that must stay first.") < table.start < md.find(
        "Closing paragraph that must stay last."), result.describe()


def test_hf16_docx_heading_levels_and_list_markers(run_cli, e2e_dir):
    path = office.docx_headings_and_lists(e2e_dir / "outline.docx")

    result = run_cli(path, "--ocr", "none")

    assert result.exit_code == 0, result.describe()
    lines = [line.rstrip() for line in result.markdown.split("\n") if line.strip()]
    expected = [
        "# Contract",
        "## Services Agreement",
        "# Definitions",
        "## Scope",
        "### Details",
        "#### Level four",
        "##### Level five",
        "###### Level six",
        "###### Level nine",
        "1. Buy milk",
        "2. Buy eggs",
        "3. Buy bread",
        "A plain paragraph between the lists.",
        "- First bullet",
        "    - Nested bullet",
        "- Second bullet",
    ]
    positions = [lines.index(line) if line in lines else -1 for line in expected]
    missing = [line for line, at in zip(expected, positions) if at < 0]
    assert not missing, f"missing lines {missing}\n{result.describe()}"
    assert positions == sorted(positions), f"out of order: {list(zip(expected, positions))}"


# --------------------------------------------------------------------------- PPTX (T22)


def test_t22_pptx_soft_line_break_is_a_line_break_not_a_control_char(run_cli, e2e_dir):
    path = office.pptx_soft_breaks(e2e_dir / "breaks.pptx")

    result = run_cli(path, "--ocr", "none")

    assert result.exit_code == 0, result.describe()
    assert "\x0b" not in result.markdown, repr(result.markdown)
    table = only_table(result.markdown, result.describe)
    assert table.grid[1] == ["softbreak", "line one\ntail"], table
    text_box = re.sub(r"<table\b.*?</table>", "", result.markdown, flags=re.S)
    assert "line one\ntail" in text_box, result.describe()


# --------------------------------------------------------------------------- Office route (R-F5, R-F15, R-F11)


BACKDROPS = ["BACKDROP 7140", "BACKDROP 7241", "BACKDROP 7342"]


def test_rf5_pptx_background_picture_slides_are_ocrd(run_cli, require_tool, e2e_dir):
    require_tool("tesseract")
    require_tool("soffice")
    path = office.pptx_background_deck(e2e_dir / "backdrops.pptx", BACKDROPS)

    result = run_cli(path, "--ocr", "tesseract", "--ocr-images", fmt="both")

    assert result.exit_code == 0, result.describe()
    for phrase in BACKDROPS:
        assert phrase.split()[1] in result.markdown, f"{phrase!r} not read\n{result.describe()}"
    assert routed_via(result) == "pdf", result.json["metadata"]


def test_rf5_pptx_background_pictures_are_extracted_natively(run_cli, e2e_dir):
    """Without OCR there is no route: the native path itself must not drop slide backgrounds."""
    path = office.pptx_background_deck(e2e_dir / "backdrops.pptx", BACKDROPS)

    result = run_cli(path, "--ocr", "none", "--extract-images")

    assert result.exit_code == 0, result.describe()
    parts = sections(result.markdown, "slide")
    assert sorted(parts) == [1, 2, 3], result.describe()[:3000]
    for number, text in parts.items():
        assert text.count("data:image/png;base64,") == 1, f"slide {number}: background picture missing"


def test_rf15_pptx_table_text_over_picture_stays_native(run_cli, require_tool, e2e_dir):
    require_tool("tesseract")
    require_tool("soffice")
    path = office.pptx_table_text_over_picture(e2e_dir / "table_text.pptx")

    result = run_cli(path, "--ocr", "tesseract", "--ocr-images", fmt="both")

    assert result.exit_code == 0, result.describe()
    assert routed_via(result) != "pdf", result.json["metadata"]
    parts = sections(result.markdown, "slide")
    assert sorted(parts) == [1, 2, 3, 4], result.describe()
    grid = tables(parts[1])[0].grid
    assert grid[1][2] == "Region 1 metric 2: value 0114.5 EUR", grid


def test_rf15_pptx_grouped_text_over_picture_stays_native(run_cli, require_tool, e2e_dir):
    require_tool("tesseract")
    require_tool("soffice")
    path = office.pptx_group_text_over_picture(e2e_dir / "group_text.pptx")

    result = run_cli(path, "--ocr", "tesseract", "--ocr-images", fmt="both")

    assert result.exit_code == 0, result.describe()
    assert routed_via(result) != "pdf", result.json["metadata"]
    parts = sections(result.markdown, "slide")
    assert all(f"Grouped callout {n}:" in parts[n] for n in (1, 2, 3, 4)), result.describe()


@pytest.mark.parametrize("builder", ["pptx_group_picture_deck", "pptx_placeholder_picture_deck"])
def test_rf15_pptx_pictures_in_groups_and_placeholders_route_as_images(run_cli, require_tool, e2e_dir, builder):
    require_tool("tesseract")
    require_tool("soffice")
    phrases = ["SLIDE 6150", "SLIDE 6251"]
    path = getattr(office, builder)(e2e_dir / f"{builder}.pptx", phrases)

    result = run_cli(path, "--ocr", "tesseract", "--ocr-images", fmt="both")

    assert result.exit_code == 0, result.describe()
    assert routed_via(result) == "pdf", result.json["metadata"]
    for phrase in phrases:
        assert phrase.split()[1] in result.markdown, result.describe()


def test_rf15_docx_table_text_over_picture_stays_native(run_cli, require_tool, e2e_dir):
    require_tool("tesseract")
    require_tool("soffice")
    path = office.docx_table_text_over_picture(e2e_dir / "form.docx")

    result = run_cli(path, "--ocr", "tesseract", "--ocr-images", fmt="both")

    assert result.exit_code == 0, result.describe()
    assert routed_via(result) != "pdf", result.json["metadata"]
    grid = only_table(result.markdown, result.describe).grid
    assert grid[3][1] == "Line item 3.1: qty 4 x EUR 31.99", grid


def test_rf11_parallel_office_conversions_all_take_the_image_route(run_cli, require_tool, e2e_dir):
    require_tool("tesseract")
    require_tool("soffice")
    decks = e2e_dir / "decks"
    decks.mkdir()
    phrases = {}
    for i in range(4):
        phrases[f"deck_{i}"] = [f"PARALLEL 81{i}0", f"PARALLEL 81{i}1"]
        office.pptx_picture_deck(decks / f"deck_{i}.pptx", phrases[f"deck_{i}"])
    out = e2e_dir / "parallel_out"

    result = run_cli(decks, "-o", out, "--format", "json", "--ocr", "tesseract", "--ocr-images",
                     "--parallel", "4", "--progress", "none", raw=True, timeout=600)

    assert result.exit_code == 0, result.describe()
    for stem, expected in phrases.items():
        written = out / f"{stem}.json"
        assert written.exists(), f"{written} missing\n{result.describe()}"
        payload = json.loads(written.read_text(encoding="utf-8"))
        extra = payload["metadata"].get("extra") or {}
        assert extra.get("routed_via") == "pdf", f"{stem}: {extra}\n{result.describe()}"
        for phrase in expected:
            assert phrase.split()[1] in payload["content"], f"{stem}: {phrase!r} not read"
