"""E2E tests for the pdftable lane: faithful PDF tables and safe table rendering.

Every test builds its input at test time (``builders_pdftable``), runs the real
``doc2mark`` CLI and parses the tables it wrote (HTML, pipe table or markdown
grid) back into a grid, so the assertions are about cell contents and positions.
No OCR is involved (every input has a text layer), so nothing is mocked.
"""

import html
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

import pymupdf
import pytest

from tests.e2e import builders_pdftable as B

C0_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


# --- reading tables back from the CLI output ----------------------------------------------------------------

@dataclass
class Grid:
    """A parsed table. ``rows[r][c]`` is the text at grid position (r, c), or None where a span covers it."""

    kind: str
    rows: List[List[Optional[str]]]
    spans: Dict[Tuple[int, int], Tuple[int, int]] = field(default_factory=dict)
    header_rows: int = 1

    def texts(self) -> List[str]:
        return [text for row in self.rows for text in row if text]

    def row_starting(self, first: str) -> List[Optional[str]]:
        matches = [row for row in self.rows if row and row[0] == first]
        assert len(matches) == 1, f"expected one row starting with {first!r} in {self.rows}"
        return matches[0]


class _HtmlTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: List[List[Tuple[str, str, int, int]]] = []
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            attrs = dict(attrs)
            self._cell = [tag, "", int(attrs.get("rowspan", 1)), int(attrs.get("colspan", 1))]
        elif tag == "br" and self._cell is not None:
            self._cell[1] += "\n"
        else:
            assert self._cell is None, f"markup <{tag}> inside a table cell"

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self.rows[-1].append(tuple(self._cell))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[1] += data


def _parse_html_table(text: str) -> Grid:
    parser = _HtmlTableParser()
    parser.feed(text)
    occupied = {}
    spans = {}
    header_rows = 0
    for r, cells in enumerate(parser.rows):
        c = 0
        if cells and all(tag == "th" for tag, *_ in cells):
            header_rows = r + 1
        for tag, cell_text, rowspan, colspan in cells:
            while (r, c) in occupied:
                c += 1
            for rr in range(r, r + rowspan):
                for cc in range(c, c + colspan):
                    occupied[(rr, cc)] = None
            occupied[(r, c)] = cell_text.strip()
            if rowspan > 1 or colspan > 1:
                spans[(r, c)] = (rowspan, colspan)
            c += colspan
    n_rows = max(r for r, _ in occupied) + 1
    n_cols = max(c for _, c in occupied) + 1
    rows = [[occupied.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)]
    return Grid("html", rows, spans, header_rows)


def _gfm_cell(raw: str) -> str:
    """One pipe-table cell as a GFM renderer shows it (cmark-gfm / markdown-it): the backslash of an
    escaped pipe is dropped, then backslash-escaped ASCII punctuation loses its backslash, entities are
    decoded and ``<br>`` is a line break."""
    text = raw.replace("\\|", "|")
    text = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    return html.unescape(text).strip()


def _split_pipe_row(line: str) -> List[str]:
    """Split a table row the way GFM does: a pipe directly after a backslash belongs to the cell."""
    line = line.strip()
    assert line.startswith("|") and line.endswith("|") and not line.endswith("\\|"), f"not a table row: {line!r}"
    cells, buf, body = [], "", line[1:-1]
    for i, char in enumerate(body):
        if char == "|" and (i == 0 or body[i - 1] != "\\"):
            cells.append(buf)
            buf = ""
        else:
            buf += char
    cells.append(buf)
    return cells


def _parse_pipe_table(text: str) -> Grid:
    lines = [line for line in text.split("\n") if line.strip()]
    kind = "markdown"
    spans = {}
    if lines and lines[0].startswith("<!-- Merged:"):
        kind = "grid"
        for r, c, rs, cs in re.findall(r"R(\d+)C(\d+):(\d+)x(\d+)", lines[0]):
            spans[(int(r) - 1, int(c) - 1)] = (int(rs), int(cs))
        lines = lines[1:]
    rows = [_split_pipe_row(line) for line in lines]
    assert len(rows) >= 2 and all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in rows[1]), f"no separator: {text!r}"
    del rows[1]
    widths = {len(row) for row in rows}
    assert len(widths) == 1, f"rows have different cell counts {sorted(widths)}: {text!r}"
    grid = [[_gfm_cell(cell) for cell in row] for row in rows]
    if kind == "grid":
        for r, row in enumerate(grid):
            for c, cell in enumerate(row):
                if cell in ("→", "↓"):
                    row[c] = None
                elif cell.endswith("⊕"):
                    assert (r, c) in spans, f"merge marker at ({r},{c}) missing from the Merged comment"
                    row[c] = cell[:-1].strip()
    return Grid(kind, grid, spans)


def parse_table(text: str) -> Grid:
    text = text.strip()
    return _parse_html_table(text) if text.startswith("<") and "<table" in text else _parse_pipe_table(text)


def convert(run_cli, path, *args):
    result = run_cli(path, "--ocr", "none", *args, fmt="both")
    assert result.exit_code == 0, result.describe()
    assert result.json is not None, result.describe()
    return result


def tables(result) -> List[Grid]:
    return [parse_table(item["content"]) for item in result.json["json_content"] or [] if item["type"] == "table"]


def flat(text: str) -> str:
    """Markdown with emphasis markers dropped and whitespace collapsed, for text-presence checks."""
    return " ".join(text.replace("*", "").split())


def occurrences(result, needle: str) -> int:
    return flat(result.markdown).count(needle)


# --- T3: span de-duplication removes only true duplicates -----------------------------------------------

def test_t3_flattened_form_values_survive(run_cli, e2e_dir):
    """Typed-in values of a filled and flattened AcroForm are drawn over the ``____`` placeholders; they
    are the only record of what the applicant entered."""
    result = convert(run_cli, B.baked_form_pdf(e2e_dir / "form.pdf"))

    [table] = tables(result)
    name_cell = table.row_starting("申請人 Applicant")[1]
    tax_cell = table.row_starting("統一編號 Tax ID")[1]
    assert "Chen Mei-Ling" in name_cell and "Name:" in name_cell, table.rows
    assert "24536871" in tax_cell and "No.:" in tax_cell, table.rows
    assert "Yes" in table.row_starting("同意 Consent")[1], table.rows
    for value in ("Chen Mei-Ling", "24536871"):
        assert occurrences(result, value) == 1, result.markdown


def test_t3_text_drawn_over_text_is_kept_and_true_duplicates_are_merged(run_cli, e2e_dir):
    result = convert(run_cli, B.overdrawn_cells_pdf(e2e_dir / "overdrawn.pdf"))

    [table] = tables(result)
    assert table.rows[0] == ["Field", "Value"]
    latin_form, cjk_form = table.rows[1]
    assert "John Smith" in latin_form and "Name:" in latin_form and "____" in latin_form, table.rows
    assert "王小明" in cjk_form and "姓名：" in cjk_form, table.rows  # the value stays one contiguous word
    latin_tick, cjk_tick = table.rows[2]
    # the tick is kept, in front of the option it marks
    assert "X" in latin_tick and latin_tick.index("X") < latin_tick.index("Yes"), table.rows
    assert "V" in cjk_tick and cjk_tick.index("V") < cjk_tick.index("同意"), table.rows
    # fake-bold overprint (same string 0.3 pt apart) is one string; separate repeated words stay repeated
    assert table.rows[3] == ["Total 1 234", "ok"], table.rows
    assert table.rows[4] == ["No No", "10 10"], table.rows


@pytest.mark.parametrize("baseline,fontsize", [(115, 20), (131, 20), (152, 30), (176, 30), (213, 48), (298, 20)])
def test_t3_watermark_crossing_a_row_loses_nothing(run_cli, e2e_dir, baseline, fontsize):
    """A light-grey horizontal watermark line crossing the table must not wipe the row it crosses."""
    result = convert(run_cli, B.watermark_table_pdf(e2e_dir / "wm.pdf", baseline, fontsize))

    [table] = tables(result)
    for account, amount in B.WATERMARK_ROWS[1:]:
        matches = [row for row in table.rows if account in (row[0] or "")]
        assert len(matches) == 1 and amount in (matches[0][1] or ""), f"{account}/{amount} not in one row: {table.rows}"


# --- T4: merged cells come from the cell geometry -------------------------------------------------------

BLANK_CELL_TABLES = {
    "sparse": [["Name", "Q1", "Q2", "Q3"], ["A", "10", "", "30"], ["B", "11", "21", "31"], ["C", "", "22", "32"]],
    "corner": [["", "Revenue", "Cost"], ["2023", "100", ""], ["2024", "", "80"], ["2025", "120", "90"]],
    "empty_row_and_column": [["A", "B", "", "D"], ["1", "2", "", "4"], ["", "", "", ""], ["5", "6", "", "8"]],
}


@pytest.mark.parametrize("case", sorted(BLANK_CELL_TABLES))
def test_t4_blank_cells_are_blank_not_merges(run_cli, e2e_dir, case):
    rows = BLANK_CELL_TABLES[case]
    result = convert(run_cli, B.table_pdf(e2e_dir / f"{case}.pdf", rows))

    [table] = tables(result)
    assert table.spans == {}, f"blank cells rendered as merges: {table.spans}\n{result.markdown}"
    assert table.rows == rows, result.markdown


MERGED_TABLES = {
    "vertical_merge_over_most_of_a_column": dict(
        rows=[["Region", "City", "Sales"], ["North", "A", "1"], ["", "B", "2"], ["", "C", "3"], ["", "D", "4"],
              ["", "E", "5"], ["South", "F", "6"]],
        merges=[(1, 0, 5, 1)], spans={(1, 0): (5, 1)}, col_w=None),
    "block_2x2": dict(
        rows=[["Item", "A", "B", "C"], ["x", "Merged 2x2", "", "1"], ["y", "", "", "2"], ["z", "3", "4", "5"]],
        merges=[(1, 1, 2, 2)], spans={(1, 1): (2, 2)}, col_w=None),
    "two_level_header": dict(
        rows=[["Region", "2023", "", "2024", ""], ["", "H1", "H2", "H1", "H2"], ["North", "1", "2", "3", "4"],
              ["South", "5", "6", "7", "8"]],
        merges=[(0, 0, 2, 1), (0, 1, 1, 2), (0, 3, 1, 2)], spans={(0, 0): (2, 1), (0, 1): (1, 2), (0, 3): (1, 2)},
        col_w=[70, 50, 50, 50, 50]),
    "docs_worked_example": dict(
        rows=[["", "", "Revenue (USD)", "", ""], ["Region", "Country", "2023", "2024", "2025"],
              ["Americas", "USA", "$4.2B", "$4.8B", "$5.1B"], ["", "Canada", "$0.9B", "$1.0B", "$1.1B"],
              ["EMEA", "Germany", "$2.1B", "$2.3B", "$2.5B"]],
        merges=[(0, 2, 1, 3), (2, 0, 2, 1)], spans={(0, 2): (1, 3), (2, 0): (2, 1)}, col_w=[70, 70, 60, 60, 60]),
}


@pytest.mark.parametrize("case", sorted(MERGED_TABLES))
def test_t4_real_merges_follow_the_drawn_cells(run_cli, e2e_dir, case):
    spec = MERGED_TABLES[case]
    result = convert(run_cli, B.table_pdf(e2e_dir / f"{case}.pdf", spec["rows"], merges=spec["merges"],
                                          col_w=spec["col_w"]))

    [table] = tables(result)
    assert table.kind == "html", result.markdown
    assert table.spans == spec["spans"], result.markdown
    for (r, c), text in B.grid(spec["rows"]).items():
        assert table.rows[r][c] == text, f"({r},{c}) should be {text!r}: {table.rows}"
    covered = {(r + dr, c + dc) for (r, c), (rs, cs) in spec["spans"].items()
               for dr in range(rs) for dc in range(cs)} - set(spec["spans"])
    for r, row in enumerate(table.rows):
        for c, text in enumerate(row):
            assert (text is None) == ((r, c) in covered), f"({r},{c}) coverage wrong: {table.rows}"


def _find(grid: Grid, text: str) -> Tuple[int, int]:
    [position] = [(r, c) for r, row in enumerate(grid.rows) for c, cell in enumerate(row) if cell == text]
    return position


def test_t4_sample_spec_sheet_merges_and_overprint(run_cli, sample_documents_dir):
    """test-table.pdf (a real car spec sheet): section rows are drawn across all seven columns, shared
    values across several variant columns, and some values are overprinted by a second text layer."""
    result = convert(run_cli, sample_documents_dir / "test-table.pdf")

    [table] = tables(result)
    assert len(table.rows[0]) == 7, table.rows[0]
    for section in ("Transmission", "Outside dimensions", "Weights", "Liquids"):
        assert table.spans.get(_find(table, section)) == (1, 7), section
    assert table.spans.get(_find(table, "Front Wheel Drive")) == (1, 6)
    assert table.spans.get(_find(table, "1.0 TSI/85 kW")) == (1, 3)
    # the overprinted layers (two sizes, one missing spaces) come out as one clean value
    for value in ("385 / 491 / 1 405", "1 193-1 248", "1 245-1 284"):
        _find(table, value)
    assert "Displacement [cm3]" in table.texts(), table.texts()


# --- T5: shapes that are not tables are not tables ------------------------------------------------------

def test_t5_logo_made_of_filled_rectangles_is_not_a_table(run_cli, e2e_dir):
    titles = ["Company Overview", "Module Introduction", "Leadership Team"]
    result = convert(run_cli, B.logo_deck_pdf(e2e_dir / "deck.pdf", titles))

    grids = tables(result)
    assert [grid.rows for grid in grids] == [[["Module", "Users", "Uptime"], ["Meetings", "1,204", "99.9%"],
                                              ["Voice", "860", "99.7%"]]], result.markdown
    for title in titles:
        assert occurrences(result, title) == 1, result.markdown
    assert occurrences(result, "by ACME Analytics") == 3, result.markdown


def test_t5_ruled_single_row_table_is_kept(run_cli, e2e_dir):
    result = convert(run_cli, B.ruled_single_row_pdf(e2e_dir / "form_line.pdf"))

    assert [grid.rows for grid in tables(result)] == [[["Signature: J. Doe", "Date: 2026-03-31"]]], result.markdown


# --- T11: headers of split and unruled tables -----------------------------------------------------------

def _employee_rows(first, last):
    return [[f"Emp{i}", f"D{i % 3}", f"{50 + i}k"] for i in range(first, last)]


@pytest.mark.parametrize("bold_header", [False, True], ids=["plain_header", "bold_header"])
def test_t11_continued_table_does_not_promote_a_data_row(run_cli, e2e_dir, bold_header):
    result = convert(run_cli, B.split_table_pdf(e2e_dir / "split.pdf", repeat_header=False, bold_header=bold_header))

    first, second = tables(result)
    assert first.rows == [B.SPLIT_HEADER] + _employee_rows(1, 8), result.markdown
    assert second.rows[0] == B.SPLIT_HEADER, f"page-2 header should repeat the column names: {second.rows}"
    assert second.rows[1:] == _employee_rows(8, 14), result.markdown
    for i in range(1, 14):
        assert occurrences(result, f"Emp{i} ") == 1, result.markdown


def test_t11_repeated_header_is_not_doubled(run_cli, e2e_dir):
    result = convert(run_cli, B.split_table_pdf(e2e_dir / "split.pdf", repeat_header=True))

    first, second = tables(result)
    assert first.rows == [B.SPLIT_HEADER] + _employee_rows(1, 8), result.markdown
    assert second.rows == [B.SPLIT_HEADER] + _employee_rows(8, 14), result.markdown


def test_t11_header_row_without_borders_is_the_header(run_cli, e2e_dir):
    result = convert(run_cli, B.unruled_header_pdf(e2e_dir / "unruled.pdf"))

    [table] = tables(result)
    assert table.rows == [B.SPLIT_HEADER] + _employee_rows(1, 6), result.markdown
    for name in B.SPLIT_HEADER:
        assert occurrences(result, name) == 1, result.markdown


# --- T15: nested and overlapping cells ------------------------------------------------------------------

def test_t15_nested_table_text_appears_once(run_cli, e2e_dir):
    result = convert(run_cli, B.nested_table_pdf(e2e_dir / "nested.pdf"))

    grids = tables(result)
    inner = [["in-h1", "in-h2"], ["in-1", "in-2"], ["in-3", "in-4"]]
    assert inner in [grid.rows for grid in grids], result.markdown
    for text in ("Outer A", "Outer B", "Left cell", "in-h1", "in-h2", "in-1", "in-2", "in-3", "in-4"):
        assert occurrences(result, text) == 1, f"{text!r}:\n{result.markdown}"


def test_t15_overlapping_cells_keep_each_text_once(run_cli, e2e_dir):
    result = convert(run_cli, B.l_shape_pdf(e2e_dir / "lshape.pdf"))

    [table] = tables(result)
    words = [word for text in table.texts() for word in text.split()]
    assert sorted(words) == sorted(["H1", "H2", "H3", "L-shape", "a", "b", "c"]), table.rows
    assert table.rows[0] == ["H1", "H2", "H3"] and table.rows[1][1:] == ["a", "b"], table.rows


# --- T16 / T18 / T22: cell rendering --------------------------------------------------------------------

def _assert_no_raw_markup(text: str):
    without_comments = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    assert not re.search(r"<(?!/?(table|tr|td|th|br)\b)[A-Za-z/!?]", without_comments), f"raw markup in output:\n{text}"
    assert not C0_CONTROLS.search(text), f"control character in output: {C0_CONTROLS.findall(text)!r}"


def test_t16_markdown_table_cells_are_escaped(run_cli, e2e_dir):
    result = convert(run_cli, B.special_chars_pdf(e2e_dir / "special.pdf"))

    [table] = tables(result)
    assert table.kind == "markdown", result.markdown
    assert table.rows == [[key, C0_CONTROLS.sub("", value)] for key, value in B.SPECIAL_ROWS], result.markdown
    _assert_no_raw_markup(result.markdown)
    assert "| x < 5 & y > 2 |" in result.markdown  # comparison operators stay readable


def test_t16_t22_office_cell_line_breaks_and_controls(run_cli, e2e_dir):
    xlsx = convert(run_cli, B.control_chars_xlsx(e2e_dir / "controls.xlsx"))
    [sheet] = tables(xlsx)
    assert [row[0] for row in sheet.rows] == ["Item", "A", "B", "C", "D"], xlsx.markdown
    assert sheet.row_starting("A")[1].split() == ["line1", "line2"], xlsx.markdown
    assert sheet.row_starting("B")[1].split() == ["cr", "only"], xlsx.markdown
    assert sheet.row_starting("D")[1] == "ok", xlsx.markdown
    assert "\r" not in xlsx.markdown and "\r" not in xlsx.json["content"]

    pptx = convert(run_cli, B.soft_break_pptx(e2e_dir / "softbreak.pptx"))
    [slide] = tables(pptx)
    assert slide.row_starting("softbreak")[1].split() == ["line", "one", "tail"], pptx.markdown
    assert slide.row_starting("markup")[1] == "a | b <script>x</script> & c", pptx.markdown
    for text in (pptx.markdown, pptx.json["content"]):
        _assert_no_raw_markup(text)


def test_t18_markdown_grid_cells_are_escaped(run_cli, e2e_dir):
    result = convert(run_cli, B.merged_special_pdf(e2e_dir / "merged.pdf"), "--table-style", "markdown_grid")

    [table] = tables(result)
    assert table.kind == "grid", result.markdown
    assert table.rows == [["Region", "Q1", None], ["North", "10 | 20", "30"],
                          ["Notes", "line one line two", "<i>x</i>"]], result.markdown
    _assert_no_raw_markup(result.markdown)


# --- T1: a span never overwrites a value (safety net in TableData) ----------------------------------------

@pytest.mark.parametrize("rows", [
    [["Name", "Q1", "Q2"], ["A", 10, None], ["B", None, 20], ["C", 30, 40]],
    [["Account", "2023", "2024", "Note"], ["Revenue", 1000, 1200, None], ["Other income", None, 50, "new"],
     ["Expenses", 800, None, None], ["Tax", None, None, "exempt"], ["Net", 200, 1250, None]],
], ids=["sparse_quarters", "sparse_financials"])
def test_t1_sparse_sheet_keeps_every_value_in_its_row(run_cli, e2e_dir, rows):
    result = convert(run_cli, B.sparse_xlsx(e2e_dir / "sparse.xlsx", rows))

    [table] = tables(result)
    for row in rows[1:]:
        rendered = " ".join(text for text in table.row_starting(row[0]) if text)
        for value in row[1:]:
            if value is not None:
                assert re.search(rf"(?<![\d.]){value}(\.0)?(?![\d])", rendered), f"{value!r} missing from row {row[0]}: {table.rows}"


# --- T21: dense tables cost about what PyMuPDF's own extraction costs ------------------------------------

def test_t21_dense_tables_convert_near_pymupdf_speed(run_cli, e2e_dir):
    pdf = B.dense_tables_pdf(e2e_dir / "dense.pdf", 10)
    started = time.perf_counter()
    with pymupdf.open(pdf) as doc:
        for page in doc:
            for table in page.find_tables().tables:
                table.extract()
    pymupdf_seconds = time.perf_counter() - started

    small = B.table_pdf(e2e_dir / "small.pdf", [["a", "b"], ["1", "2"]])
    started = time.perf_counter()
    convert(run_cli, small)
    startup_seconds = time.perf_counter() - started
    started = time.perf_counter()
    result = convert(run_cli, pdf)
    dense_seconds = time.perf_counter() - started

    grids = tables(result)
    assert [grid.rows for grid in grids] == [B.dense_rows(page_no) for page_no in range(10)]
    overhead = dense_seconds - startup_seconds
    assert overhead < 3 * pymupdf_seconds + 1.0, (
        f"table extraction took {overhead:.2f}s over CLI start-up; PyMuPDF's own find_tables+extract "
        f"takes {pymupdf_seconds:.2f}s for the same pages")
