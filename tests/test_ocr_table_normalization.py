"""OCR table grid normalization properties the CLI E2E tests cannot sweep: every
normalized table is rectangular, normalizing again changes nothing, and spans that
claim a huge grid cannot make normalization slow."""

import random
import time

from lxml import html as lxml_html

from doc2mark.ocr.schema import Table, normalize_table_html, sanitize_table_html


def _row_widths(html: str):
    """Occupied columns per row of the first top-level table, laid out per the HTML
    table model (rowspan limited to its row group)."""
    table = lxml_html.fragment_fromstring(html, create_parent="div").find("table")
    rows = [row for row in table if row.tag == "tr"]
    covered, widths = set(), []
    for r, row in enumerate(rows):
        col = 0
        for cell in (c for c in row if c.tag in ("td", "th")):
            while (r, col) in covered:
                col += 1
            rowspan = min(int(cell.get("rowspan", 1)), len(rows) - r)
            colspan = int(cell.get("colspan", 1))
            for dr in range(rowspan):
                for dc in range(colspan):
                    covered.add((r + dr, col + dc))
            col += colspan
        widths.append(sum(1 for (rr, _) in covered if rr == r))
    return widths


def _random_table(rng: random.Random) -> str:
    rows = []
    for _ in range(rng.randint(2, 6)):
        cells = []
        for _ in range(rng.randint(0, 5)):
            spans = ""
            if rng.random() < 0.25:
                spans += f' rowspan="{rng.randint(0, 4)}"'
            if rng.random() < 0.25:
                spans += f' colspan="{rng.randint(0, 4)}"'
            tag = "th" if rng.random() < 0.2 else "td"
            text = rng.choice(["", "", "12", "3.5%", "North", "Q1", "NT$"])
            cells.append(f"<{tag}{spans}>{text}</{tag}>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return "<table>" + "".join(rows) + "</table>"


def test_a_short_row_padded_mid_row_does_not_shift_a_rowspan():
    html = '<table><tr><td></td><td rowspan="2">Rev</td></tr><tr><td colspan="2"></td><td>x</td></tr></table>'
    widths = _row_widths(Table(html=html).html)
    assert len(set(widths)) == 1, widths


def test_normalized_tables_are_rectangular_and_stable():
    rng = random.Random(20260930)
    for _ in range(2000):
        source = _random_table(rng)
        once = Table(html=source).html
        widths = _row_widths(once)
        assert len(set(widths)) == 1, (source, once, widths)
        assert Table(html=once).html == once, (source, once)


def test_spans_that_claim_a_huge_grid_are_dropped_quickly():
    html = ("<table><tr>" + "<td colspan='300' rowspan='800'>x</td>" * 300 + "</tr>"
            + "<tr></tr>" * 799 + "</table>")
    started = time.perf_counter()
    out = normalize_table_html(sanitize_table_html(html))
    assert time.perf_counter() - started < 2.0
    assert out.count(">x<") == 300 and "rowspan" not in out and "colspan" not in out
