"""Input builders for the pdftable lane's E2E tests (``tests/e2e/test_pdftable.py``).

The PDF fixtures are ports of the table-review probes (``d2m-rv-tables-probes``):
ruled tables drawn cell by cell the way Word/Excel export them, flattened forms,
overprinted text, watermarks, merged cells, split and nested tables, logo shapes
and borderless tables. The Office fixtures carry cell text the PDF text layer
cannot (CR/LF, soft line breaks). Everything is generated at test time with
PyMuPDF, openpyxl and python-pptx; nothing from ``doc2mark`` is imported.
"""

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pymupdf

A4 = (595, 842)
SLIDE = (1440, 810)

Cells = Dict[Tuple[int, int], str]
Merge = Tuple[int, int, int, int]  # (row, col, rowspan, colspan)


def grid(rows: Sequence[Sequence[Optional[str]]]) -> Cells:
    """``[["a", "b"], ["", "d"]]`` -> ``{(0, 0): "a", (0, 1): "b", (1, 1): "d"}`` (blank cells have no text)."""
    return {(r, c): v for r, row in enumerate(rows) for c, v in enumerate(row) if v}


def ruled_table(page, x0: float, y0: float, col_w: Sequence[float], row_h: Sequence[float], cells: Cells,
                merges: Iterable[Merge] = (), *, fontname: str = "helv", fontsize: float = 9,
                bold_rows: Iterable[int] = (), draw: bool = True) -> pymupdf.Rect:
    """Draw a ruled table and its text; return the table rectangle.

    Every cell, and every merged region as a whole, is drawn as its own outlined
    rectangle, so a merged region has no internal lines (how Word and Excel export
    merged cells). Text starts 3 pt from the cell's left edge on its first line;
    rows listed in ``bold_rows`` use Helvetica-Bold.
    """
    xs = [x0]
    for w in col_w:
        xs.append(xs[-1] + w)
    ys = [y0]
    for h in row_h:
        ys.append(ys[-1] + h)
    covered = {}
    for r, c, rs, cs in merges:
        for rr in range(r, r + rs):
            for cc in range(c, c + cs):
                covered[(rr, cc)] = (r, c, rs, cs)
    bold_rows = set(bold_rows)
    done = set()
    for r in range(len(row_h)):
        for c in range(len(col_w)):
            rr, cc, rs, cs = covered.get((r, c), (r, c, 1, 1))
            if (rr, cc) in done:
                continue
            done.add((rr, cc))
            rect = pymupdf.Rect(xs[cc], ys[rr], xs[cc + cs], ys[rr + rs])
            if draw:
                page.draw_rect(rect, color=(0, 0, 0), width=0.6)
            text = cells.get((rr, cc))
            if text:
                font = "hebo" if rr in bold_rows and fontname == "helv" else fontname
                page.insert_text((rect.x0 + 3, rect.y0 + fontsize + 2), text, fontname=font, fontsize=fontsize)
    return pymupdf.Rect(xs[0], ys[0], xs[-1], ys[-1])


def table_pdf(path: Path, rows: Sequence[Sequence[Optional[str]]], *, merges: Iterable[Merge] = (),
              col_w: Optional[Sequence[float]] = None, row_h: float = 18, fontname: str = "helv",
              fontsize: float = 9, origin: Tuple[float, float] = (50, 50), after: Optional[str] = None) -> Path:
    """One A4 page with one ruled table (``rows`` of text, ``""``/None = blank cell) and an optional
    paragraph ``after`` the table."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    col_w = col_w or [80] * max(len(r) for r in rows)
    rect = ruled_table(page, origin[0], origin[1], col_w, [row_h] * len(rows), grid(rows), merges,
                       fontname=fontname, fontsize=fontsize)
    if after:
        page.insert_text((origin[0], rect.y1 + 30), after, fontsize=10)
    doc.save(str(path))
    doc.close()
    return Path(path)


def landscape_table_pdf(path: Path, rows: Sequence[Sequence[Optional[str]]], *, rotation: int,
                        merges: Iterable[Merge] = (), col_w: Optional[Sequence[float]] = None) -> Path:
    """A landscape page stored the way word processors and scanners store one: a portrait page whose
    content is drawn sideways, with ``/Rotate rotation`` so that it displays upright. The content is
    one ruled table, as in :func:`table_pdf`."""
    upright = pymupdf.open()
    page = upright.new_page(width=A4[1], height=A4[0])
    col_w = col_w or [80] * max(len(r) for r in rows)
    ruled_table(page, 50, 50, col_w, [18] * len(rows), grid(rows), merges)
    doc = pymupdf.open()
    stored = doc.new_page(width=A4[0], height=A4[1])
    stored.show_pdf_page(stored.rect, upright, 0, rotate=rotation)
    stored.set_rotation(rotation)
    doc.save(str(path))
    doc.close()
    upright.close()
    return Path(path)


# --- T3: text drawn over text ---------------------------------------------------------------------------

def baked_form_pdf(path: Path) -> Path:
    """A real fillable form (AcroForm text fields and a checkbox inside a ruled table, CJK labels), filled
    in and flattened with ``doc.bake()`` -- what "flatten" / "print to PDF" produce."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    ruled_table(page, 50, 100, [150, 300], [28] * 4,
                grid([["欄位 Field", "填寫 Entry"], ["申請人 Applicant", ""], ["統一編號 Tax ID", ""], ["同意 Consent", ""]]),
                fontname="china-t", fontsize=10)
    page.insert_text((205, 146), "Name: ______________________", fontsize=10)
    page.insert_text((205, 174), "No.: ______________", fontsize=10)
    page.insert_text((205, 202), "[   ] Yes     [   ] No", fontsize=10)

    def text_field(name, rect, value):
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = name
        widget.rect = pymupdf.Rect(*rect)
        widget.field_value = value
        widget.text_fontsize = 10
        widget.border_width = 0
        page.add_widget(widget)

    text_field("name", (240, 134, 400, 149), "Chen Mei-Ling")
    text_field("taxid", (228, 162, 330, 177), "24536871")
    checkbox = pymupdf.Widget()
    checkbox.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    checkbox.field_name = "yes"
    checkbox.rect = pymupdf.Rect(207, 191, 219, 203)
    checkbox.field_value = True
    checkbox.border_width = 0
    page.add_widget(checkbox)
    doc.bake()
    doc.save(str(path))
    doc.close()
    return Path(path)


def overdrawn_cells_pdf(path: Path) -> Path:
    """A 5x2 ruled table whose cells carry text drawn over other text (probes D-a/D-b/D-m/D-n).

    Row 1: a typed-in value over a ``____`` placeholder, Latin and CJK (flattened form).
    Row 2: a tick drawn over the first of two checkboxes, Latin and CJK.
    Row 3: fake-bold overprint (the same string twice, 0.3 pt apart) | a normal cell.
    Row 4: legitimately repeated words drawn as separate, non-overlapping runs.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    x, y, cw, rh = 50, 50, 170, 40
    ruled_table(page, x, y, [cw, cw], [rh] * 5, {})
    page.insert_text((x + 3, y + 11), "Field", fontsize=9)
    page.insert_text((x + cw + 3, y + 11), "Value", fontsize=9)
    y1 = y + rh
    page.insert_text((x + 3, y1 + 16), "Name: ____________________", fontsize=10)
    page.insert_text((x + 40, y1 + 15), "John Smith", fontsize=10)
    page.insert_text((x + cw + 3, y1 + 16), "姓名：＿＿＿＿＿＿＿", fontname="china-t", fontsize=10)
    page.insert_text((x + cw + 33, y1 + 15), "王小明", fontname="china-t", fontsize=10)
    y2 = y + 2 * rh
    page.insert_text((x + 3, y2 + 16), "[ ] Yes   [ ] No", fontsize=10)
    page.insert_text((x + 4.5, y2 + 16), "X", fontsize=10)
    page.insert_text((x + cw + 3, y2 + 16), "□ 同意  □ 不同意", fontname="china-t", fontsize=10)
    page.insert_text((x + cw + 5.5, y2 + 16), "V", fontsize=10)
    y3 = y + 3 * rh
    for dx in (0, 0.3):
        page.insert_text((x + 3 + dx, y3 + 14), "Total 1 234", fontsize=10)
    page.insert_text((x + cw + 3, y3 + 14), "ok", fontsize=10)
    y4 = y + 4 * rh
    page.insert_text((x + 3, y4 + 14), "No", fontsize=10)
    page.insert_text((x + 25, y4 + 14), "No", fontsize=10)
    page.insert_text((x + cw + 3, y4 + 14), "10", fontsize=10)
    page.insert_text((x + cw + 20, y4 + 14), "10", fontsize=10)
    doc.save(str(path))
    doc.close()
    return Path(path)


WATERMARK_ROWS = [["Account", "Amount"]] + [[f"Acct{i:02d}", f"{2000 + 53 * i:,}"] for i in range(1, 12)]


def watermark_table_pdf(path: Path, baseline: float, fontsize: float) -> Path:
    """The probe_watermark_sweep page: a 12x2 ruled table (y = 100..340) crossed by one horizontal line of
    light-grey "INTERNAL USE ONLY" whose baseline is at ``baseline``."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    ruled_table(page, 60, 100, [160, 160], [20] * len(WATERMARK_ROWS), grid(WATERMARK_ROWS), fontsize=10)
    page.insert_text((70, baseline), "INTERNAL USE ONLY", fontsize=fontsize, color=(0.9, 0.9, 0.9))
    doc.save(str(path))
    doc.close()
    return Path(path)


# --- T11: headers ------------------------------------------------------------------------------------------

SPLIT_HEADER = ["Name", "Dept", "Salary"]


def split_table_pdf(path: Path, *, repeat_header: bool, bold_header: bool = False) -> Path:
    """One 3-column table continued from the bottom of page 1 (header + Emp1-Emp7) to the top of page 2
    (Emp8-Emp13); ``repeat_header`` repeats the header row on page 2."""
    doc = pymupdf.open()
    for page_no, (first, last) in enumerate([(1, 8), (8, 14)]):
        page = doc.new_page(width=A4[0], height=A4[1])
        with_header = page_no == 0 or repeat_header
        rows = [[f"Emp{i}", f"D{i % 3}", f"{50 + i}k"] for i in range(first, last)]
        if with_header:
            rows.insert(0, SPLIT_HEADER)
        if page_no == 0:
            page.insert_text((50, 620), "Staff list", fontsize=10)
        ruled_table(page, 50, 60 if page_no else 650, [100, 80, 80], [18] * len(rows), grid(rows),
                    bold_rows=[0] if (with_header and bold_header) else [])
    doc.save(str(path))
    doc.close()
    return Path(path)


def unruled_header_pdf(path: Path) -> Path:
    """A header row drawn without cell borders (bold text and one rule under it) above a fully ruled body."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    for x, name in zip([53, 153, 233], SPLIT_HEADER):
        page.insert_text((x, 72), name, fontname="hebo", fontsize=9)
    page.draw_line((50, 76), (310, 76), width=1.2)
    rows = [[f"Emp{i}", f"D{i % 3}", f"{50 + i}k"] for i in range(1, 6)]
    ruled_table(page, 50, 78, [100, 80, 80], [18] * len(rows), grid(rows))
    doc.save(str(path))
    doc.close()
    return Path(path)


# --- T5: shapes that look like tables ---------------------------------------------------------------------

def logo_deck_pdf(path: Path, titles: Sequence[str], brand: str = "by ACME Analytics") -> Path:
    """Slides (1440x810) with the corporate-deck header of the real deck in the table review: a logo made
    of two abutting filled rectangles (a square mark with white "AI", a band with the white brand line),
    the slide title on the same line 40 pt right of the logo, and a body line. Slide 2 also carries a
    real ruled table (the control)."""
    doc = pymupdf.open()
    for index, title in enumerate(titles):
        page = doc.new_page(width=SLIDE[0], height=SLIDE[1])
        page.draw_rect(pymupdf.Rect(52, 33, 92, 74), color=None, fill=(0.05, 0.1, 0.3))
        page.draw_rect(pymupdf.Rect(92, 33, 194, 69), color=None, fill=(0.05, 0.1, 0.3))
        page.insert_text((62, 58), "AI", fontsize=14, color=(1, 1, 1))
        page.insert_text((98, 56), brand, fontsize=9, color=(1, 1, 1))
        page.insert_text((234, 62), title, fontsize=28)
        page.insert_text((60, 160), f"Slide {index + 1} body: {title.lower()} overview for the board.", fontsize=18)
        if index == 1:
            ruled_table(page, 60, 220, [220, 160, 160], [26] * 3,
                        grid([["Module", "Users", "Uptime"], ["Meetings", "1,204", "99.9%"],
                              ["Voice", "860", "99.7%"]]),
                        fontsize=14)
    doc.save(str(path))
    doc.close()
    return Path(path)


def ruled_single_row_pdf(path: Path) -> Path:
    """A ruled one-row, two-cell table (a form line). It has no second row, but its borders are drawn
    lines, so it is a table."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    ruled_table(page, 50, 60, [200, 200], [24], grid([["Signature: J. Doe", "Date: 2026-03-31"]]), fontsize=10)
    doc.save(str(path))
    doc.close()
    return Path(path)


# --- T15: nested and overlapping cells -----------------------------------------------------------------

def nested_table_pdf(path: Path) -> Path:
    """Outer 2x2 ruled table whose cell (1,1) holds an inner 3x2 ruled table (probe L5)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    ruled_table(page, 50, 50, [120, 200], [18, 80], grid([["Outer A", "Outer B"], ["Left cell", ""]]))
    ruled_table(page, 180, 75, [90, 90], [18, 18, 18], grid([["in-h1", "in-h2"], ["in-1", "in-2"], ["in-3", "in-4"]]))
    doc.save(str(path))
    doc.close()
    return Path(path)


def l_shape_pdf(path: Path) -> Path:
    """3x3 ruled grid whose cells (1,0), (2,0) and (2,1) form one L-shaped region without inner lines
    (probe P6)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    x0, y0, w, h = 50, 50, 70, 18
    xs = [x0 + i * w for i in range(4)]
    ys = [y0 + i * h for i in range(4)]
    page.draw_rect(pymupdf.Rect(xs[0], ys[0], xs[3], ys[3]), width=0.6)
    for c in range(3):
        page.draw_rect(pymupdf.Rect(xs[c], ys[0], xs[c + 1], ys[1]), width=0.6)
    page.draw_rect(pymupdf.Rect(xs[1], ys[1], xs[2], ys[2]), width=0.6)
    page.draw_rect(pymupdf.Rect(xs[2], ys[1], xs[3], ys[2]), width=0.6)
    page.draw_rect(pymupdf.Rect(xs[2], ys[2], xs[3], ys[3]), width=0.6)
    labels = [(0, 0, "H1"), (0, 1, "H2"), (0, 2, "H3"), (1, 0, "L-shape"), (1, 1, "a"), (1, 2, "b"), (2, 2, "c")]
    for r, c, text in labels:
        page.insert_text((xs[c] + 3, ys[r] + 11), text, fontsize=9)
    doc.save(str(path))
    doc.close()
    return Path(path)


# --- T12: tables without vertical rules -------------------------------------------------------------------

SEGMENT_ROWS = [["Segment", "2023", "2024", "Growth"], ["Cloud", "1,200", "1,560", "30%"],
                ["Devices", "800", "760", "-5%"], ["Services", "430", "520", "21%"]]


def booktabs_pdf(path: Path, *, rules: bool = True) -> Path:
    """The probe L7/L8 table: four aligned columns with a caption and a sentence after it; ``rules``
    draws the three booktabs rules (top, under the header, bottom), otherwise it is fully borderless."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((50, 40), "Table 2. Segment results (USD m)", fontsize=10)
    xs = [50, 190, 270, 350]
    y = 60
    if rules:
        page.draw_line((50, y), (420, y), width=1.0)
    for i, row in enumerate(SEGMENT_ROWS):
        y += 16
        for x, value in zip(xs, row):
            page.insert_text((x, y), value, fontsize=10)
        if i == 0 and rules:
            page.draw_line((50, y + 5), (420, y + 5), width=0.5)
    if rules:
        page.draw_line((50, y + 6), (420, y + 6), width=1.0)
    page.insert_text((50, y + 30), "Growth was driven by Cloud.", fontsize=10)
    doc.save(str(path))
    doc.close()
    return Path(path)


def prose_pages_pdf(path: Path) -> Path:
    """Pages a table detector must leave alone: a justified two-column article, a key/value letterhead
    block, a bulleted list with right-aligned page references, and prose between two horizontal rules."""
    doc = pymupdf.open()
    lorem = ("Quarterly revenue rose on strong demand for managed services while hardware margins narrowed "
             "as component prices increased across the supply chain during the second half of the year. ")
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((50, 50), "Market overview", fontname="hebo", fontsize=14)
    page.insert_textbox(pymupdf.Rect(50, 70, 290, 780), lorem * 9, fontsize=10, align=pymupdf.TEXT_ALIGN_JUSTIFY)
    page.insert_textbox(pymupdf.Rect(305, 70, 545, 780), lorem * 9, fontsize=10, align=pymupdf.TEXT_ALIGN_JUSTIFY)

    page = doc.new_page(width=A4[0], height=A4[1])
    y = 60
    for key, value in [("Invoice no.", "INV-2026-0042"), ("Date", "31 March 2026"), ("Customer", "ACME Trading Ltd."),
                       ("Address", "12 Harbour Road, Kowloon"), ("Contact", "Mei-Ling Chen")]:
        page.insert_text((50, y), key, fontsize=10)
        page.insert_text((160, y), value, fontsize=10)
        y += 16
    page.draw_line((50, y), (545, y), width=0.8)
    page.insert_textbox(pymupdf.Rect(50, y + 10, 545, y + 140), lorem * 3, fontsize=10)
    page.draw_line((50, y + 150), (545, y + 150), width=0.8)
    y += 190
    for index, item in enumerate(["Scope of the review", "Findings and ratings", "Management response",
                                  "Follow-up actions", "Appendix: sampling method"]):
        page.insert_text((50, y), f"- {item}", fontsize=10)
        page.insert_text((500, y), str(3 + index * 2), fontsize=10)
        y += 16
    doc.save(str(path))
    doc.close()
    return Path(path)


# --- T16/T18/T22: cell text the renderer must escape ------------------------------------------------------

SPECIAL_ROWS = [["Key", "Value"], ["pipe", "a | b"], ["path", "C:\\temp\\|x"], ["img", "<img src=x onerror=alert(1)>"],
                ["html", "<b>bold</b> & <script>x</script>"], ["math", "x < 5 & y > 2"], ["dash", "- leading dash"],
                ["hash", "# not a heading"], ["ctrl", "bell\x07here esc\x1bhere"]]


def special_chars_pdf(path: Path) -> Path:
    """A plain ruled 9x2 table (no merges, so the Markdown path renders it) with markup-like cell text and
    C0 control characters (a PDF text layer can carry them)."""
    return table_pdf(path, SPECIAL_ROWS, col_w=[60, 260])


def merged_special_pdf(path: Path) -> Path:
    """A ruled table with one real colspan (so it renders through the HTML / markdown_grid path) and cells
    holding a pipe, markup and two lines of text."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    rows = [["Region", "Q1", ""], ["North", "10 | 20", "30"], ["Notes", "line one\nline two", "<i>x</i>"]]
    ruled_table(page, 50, 50, [80, 120, 80], [18, 18, 30], grid(rows), merges=[(0, 1, 1, 2)])
    doc.save(str(path))
    doc.close()
    return Path(path)


BACKSLASH_ROWS = [["Key", "Value"], ["glob", "C:\\Users\\*.txt"], ["unc", "\\\\server\\share"],
                  ["trailing", "ends with \\"], ["escaped", "a \\| b \\_c\\_"], ["two lines", "C:\\temp\\\nD:\\data"]]


def backslash_table_pdf(path: Path, *, merged: bool) -> Path:
    """A ruled table of Windows paths and escape-like text (``BACKSLASH_ROWS``): backslashes before
    ASCII punctuation, at the end of a cell and at the end of a cell's first line. ``merged`` adds a
    ``Paths`` title row spanning both columns, so the table renders through the HTML / grid path."""
    rows = ([["Paths", ""]] if merged else []) + BACKSLASH_ROWS
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    ruled_table(page, 50, 50, [70, 220], [18] * (len(rows) - 1) + [30], grid(rows),
                merges=[(0, 0, 1, 2)] if merged else [])
    doc.save(str(path))
    doc.close()
    return Path(path)


def control_chars_xlsx(path: Path) -> Path:
    """Workbook whose cells hold CR/LF, a lone CR and a tab (the only control characters a worksheet
    accepts)."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "controls"
    for r, row in enumerate([["Item", "Desc"], ["A", "line1\r\nline2"], ["B", "cr\ronly"], ["C", "tab\there"],
                             ["D", "ok"]], start=1):
        for c, value in enumerate(row, start=1):
            ws.cell(r, c, value)
    wb.save(str(path))
    return Path(path)


def soft_break_pptx(path: Path) -> Path:
    """One slide with a 3x2 table: a cell holding a soft line break (``<a:br/>``, which python-pptx reads
    as ``\\x0b``) and a cell holding markup-like text."""
    from lxml import etree
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    table = slide.shapes.add_table(3, 2, Inches(0.5), Inches(0.5), Inches(8), Inches(2)).table
    table.cell(0, 0).text = "Key"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "softbreak"
    paragraph = table.cell(1, 1).text_frame.paragraphs[0]
    paragraph.text = "line one"
    run = paragraph.add_run()
    run.text = "tail"
    br = etree.SubElement(paragraph._p, "{http://schemas.openxmlformats.org/drawingml/2006/main}br")
    paragraph._p.remove(br)
    paragraph._p.insert(len(paragraph._p) - 1, br)
    table.cell(2, 0).text = "markup"
    table.cell(2, 1).text = "a | b <script>x</script> & c"
    prs.save(str(path))
    return Path(path)


def sparse_xlsx(path: Path, rows: Sequence[Sequence[object]]) -> Path:
    """A sheet with no merged ranges; ``None`` leaves a cell blank (the T1 trigger)."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "sparse"
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row, start=1):
            if value is not None:
                ws.cell(r, c, value)
    wb.save(str(path))
    return Path(path)


# --- T21: dense tables ------------------------------------------------------------------------------------

def dense_rows(page_no: int) -> List[List[str]]:
    header = ["Account", "2021", "2022", "2023", "2024", "2025", "Chg", "Note"]
    rows = [header]
    for i in range(1, 50):
        amounts = [f"{(i * 37 + j * 11) % 9999:,}" for j in range(5)]
        rows.append([f"Line {page_no}-{i}", *amounts, f"{i % 7}.{i % 9}%", "n"])
    return rows


def dense_tables_pdf(path: Path, pages: int, *, ruled: bool = True) -> Path:
    """``pages`` A4 pages each holding one 50x8 financial table in 7 pt text (the probe_perf page).
    ``ruled=False`` writes exactly the same text without any lines (no table to detect)."""
    doc = pymupdf.open()
    for page_no in range(pages):
        page = doc.new_page(width=A4[0], height=A4[1])
        rows = dense_rows(page_no)
        ruled_table(page, 30, 30, [140, 60, 60, 60, 60, 60, 50, 40], [15.5] * len(rows), grid(rows), fontsize=7,
                    draw=ruled)
    doc.save(str(path))
    doc.close()
    return Path(path)


STATEMENT_ROWS = [["", "2025", "2024"], ["Revenue", "12,345", "11,210"], ["Cost of sales", "(7,890)", "(7,120)"],
                  ["Gross profit", "4,455", "4,090"], ["Operating expenses", "", ""], ["Selling", "(1,200)", "(1,150)"],
                  ["Administrative", "(800)", "(760)"], ["Operating income", "2,455", "2,180"]]


def borderless_statement_pdf(path: Path) -> Path:
    """An income statement laid out with tab stops only: a bold title, a label column (sub-items
    indented), two right-aligned number columns and a note under it -- no rules at all."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((50, 50), "Statement of income (USD thousands)", fontname="hebo", fontsize=12)
    y = 80
    for label, *values in STATEMENT_ROWS:
        if label:
            indent = 10 if label in ("Selling", "Administrative") else 0
            page.insert_text((50 + indent, y), label, fontsize=10)
        for right, value in zip((330, 430), values):
            if value:
                page.insert_text((right - pymupdf.get_text_length(value, fontsize=10), y), value, fontsize=10)
        y += 16
    page.insert_text((50, y + 20), "The notes on pages 12-30 are part of these statements.", fontsize=9)
    doc.save(str(path))
    doc.close()
    return Path(path)


def aligned_non_tables_pdf(path: Path) -> Path:
    """Layouts with column-aligned text that are not tables: slide text boxes side by side (short
    items with numbers), a table of contents, a CV timeline, label/value form pairs and three
    justified newspaper columns."""
    doc = pymupdf.open()
    page = doc.new_page(width=SLIDE[0], height=SLIDE[1])
    page.insert_text((60, 60), "Quarterly highlights", fontsize=32)
    boxes = [["Revenue up 20%", "1,200 new users", "Churn 3.1%", "NPS 62"],
             ["Costs down 5%", "Headcount 118", "Opex 4.2M", "Capex 0.8M"],
             ["Cloud 45%", "Devices 30%", "Services 25%", "Other 0%"]]
    for i, items in enumerate(boxes):
        for j, item in enumerate(items):
            page.insert_text((80 + i * 440, 200 + j * 40), f"- {item}", fontsize=22)
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((50, 50), "Contents", fontsize=16)
    contents = [("1", "Introduction", "3"), ("1.1", "Scope", "4"), ("2", "Findings", "7"), ("2.1", "Revenue", "8"),
                ("3", "Outlook", "12")]
    for i, (number, title, page_no) in enumerate(contents):
        page.insert_text((50, 90 + i * 18), number, fontsize=11)
        page.insert_text((90, 90 + i * 18), title, fontsize=11)
        page.insert_text((500, 90 + i * 18), page_no, fontsize=11)
    page = doc.new_page(width=A4[0], height=A4[1])
    jobs = [("2019 - 2024", "Senior Analyst", "ACME Corp"), ("2016 - 2019", "Analyst", "Globex"),
            ("2014 - 2016", "Intern", "Initech")]
    for i, (dates, role, company) in enumerate(jobs):
        page.insert_text((50, 80 + i * 40), dates, fontsize=11)
        page.insert_text((180, 80 + i * 40), role, fontsize=11)
        page.insert_text((380, 80 + i * 40), company, fontsize=11)
        page.insert_text((180, 94 + i * 40), "Built forecasting models and reporting pipelines.", fontsize=9)
    page = doc.new_page(width=A4[0], height=A4[1])
    pairs = [("Name:", "Chen Mei-Ling", "Date:", "2026-03-31"), ("ID:", "A123456789", "Phone:", "0912-345-678"),
             ("Dept:", "Finance", "Ext:", "4421")]
    for i, pair in enumerate(pairs):
        for x, value in zip([50, 110, 320, 380], pair):
            page.insert_text((x, 80 + i * 20), value, fontsize=11)
    page = doc.new_page(width=A4[0], height=A4[1])
    news = "In 2025 the council approved 12 new projects worth 4.5 million while 3 were deferred to 2026. "
    for i in range(3):
        page.insert_textbox(pymupdf.Rect(40 + i * 180, 60, 200 + i * 180, 780), news * 12, fontsize=9,
                            align=pymupdf.TEXT_ALIGN_JUSTIFY)
    doc.save(str(path))
    doc.close()
    return Path(path)


KEY_VALUE_PAGES = [[["Plan name", "Image assistant"], ["Price", "5,000 per 6 months"], ["Industry", "Services"],
                    ["Users", "9 to 50 staff"], ["Channel", "Web and LINE"], ["Launch", "2026-01"],
                    ["Owner", "Chen Mei-Ling"]],
                   [["Support", "Email and phone"], ["Training", "2 workshops"], ["Contract", "12 months"]]]


def key_value_split_pdf(path: Path) -> Path:
    """A plain two-column key/value form table (no header row) running from the bottom of page 1
    to the top of page 2."""
    doc = pymupdf.open()
    for page_no, rows in enumerate(KEY_VALUE_PAGES):
        page = doc.new_page(width=A4[0], height=A4[1])
        top = 60 if page_no else 842 - 48 - 18 * len(rows)
        if page_no == 0:
            page.insert_text((50, top - 20), "Application details", fontsize=10)
        ruled_table(page, 50, top, [140, 240], [18] * len(rows), grid(rows))
    doc.save(str(path))
    doc.close()
    return Path(path)


# --- Review round 1: the reviewer's adversarial pages (d2m-review-pdftable/gen_adv*.py), normal leading ---

def _right_aligned(page, right: float, y: float, text: str, fontsize: float = 10) -> None:
    page.insert_text((right - pymupdf.get_text_length(text, fontsize=fontsize), y), text, fontsize=fontsize)


PROJECT_ROWS = [["Project", "Budget", "Spent", "Comment"], ["Apollo", "1,200", "1,050", "On track"],
                ["Borealis", "800", "910", "Over budget due to vendor delay"], ["Cygnus", "430", "120", "Paused"],
                ["Draco", "95", "90", "Closing in May, final audit pending"]]


def free_text_column_pdf(path: Path) -> Path:
    """A borderless status table (16 pt row pitch) whose last column is free text of different lengths,
    under a title and above a closing sentence (reviewer page p12)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((50, 50), "Project status", fontsize=12)
    y = 80
    for project, budget, spent, comment in PROJECT_ROWS:
        page.insert_text((50, y), project, fontsize=10)
        _right_aligned(page, 210, y, budget)
        _right_aligned(page, 280, y, spent)
        page.insert_text((310, y), comment, fontsize=10)
        y += 16
    page.insert_text((50, y + 20), "Next review in June.", fontsize=10)
    doc.save(str(path))
    doc.close()
    return Path(path)


SIDEBAR_LINES = ["Key takeaways from the quarter:", "revenue beat guidance by 4%", "and margins expanded again",
                 "thanks to lower input costs", "and a better product mix."]
SIDEBAR_ROWS = SEGMENT_ROWS + [["Other", "95", "101", "6%"]]


def table_with_sidebar_pdf(path: Path) -> Path:
    """A 9 pt borderless table on the left and a sidebar sentence on the same lines to its right
    (reviewer page p6)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    y = 80
    for row, side in zip(SIDEBAR_ROWS, SIDEBAR_LINES):
        for x, value in zip([50, 130, 180, 230], row):
            page.insert_text((x, y), value, fontsize=9)
        page.insert_text((330, y), side, fontsize=9)
        y += 14
    doc.save(str(path))
    doc.close()
    return Path(path)


REGION_ROWS = [["Region", "Q1", "Q2", "Q3"], ["North", "120", "135", "150"], ["South", "98", "101", "110"],
               ["East", "77", "80", "95"], ["West", "60", "66", "71"]]
REGION_LEAD = "Our regional results improved across the board this year, as summarised here."
REGION_CLOSE = "All regions beat their targets except the West, which missed by 2%."


def borderless_in_paragraph_pdf(path: Path) -> Path:
    """A borderless table set inside a paragraph at the paragraph's own 13 pt leading: one sentence
    above, one below (reviewer page p10)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((50, 50), REGION_LEAD, fontsize=10)
    y = 63
    for row in REGION_ROWS:
        for x, value in zip([50, 200, 260, 320], row):
            page.insert_text((x, y), value, fontsize=10)
        y += 13
    page.insert_text((50, y), REGION_CLOSE, fontsize=10)
    doc.save(str(path))
    doc.close()
    return Path(path)


CASH_FLOW_TITLE = "Statement of cash flows (USD thousands)"
CASH_FLOW_ROWS = [["Operating activities", "2023", "2024"], ["Net income", "2,455", "2,180"],
                  ["Depreciation", "310", "295"], ["Working capital", "(120)", "85"],
                  ["Net cash from operations", "2,645", "2,560"]]
CASH_FLOW_NOTES = ["Figures for 2023 were restated after the merger with Globex.",
                   "See note 7 for the reconciliation of operating cash flow."]


def statement_with_notes_pdf(path: Path) -> Path:
    """A borderless statement whose title and two notes sit at the rows' own 12 pt pitch (reviewer
    page p2)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((50, 50), CASH_FLOW_TITLE, fontsize=10)
    y = 64
    for label, *values in CASH_FLOW_ROWS:
        page.insert_text((50, y), label, fontsize=10)
        for right, value in zip((330, 430), values):
            _right_aligned(page, right, y, value)
        y += 12
    for note in CASH_FLOW_NOTES:
        page.insert_text((50, y), note, fontsize=10)
        y += 12
    doc.save(str(path))
    doc.close()
    return Path(path)


BOOKTABS_TIGHT_LINES = ["Revenue grew in every segment during the year as shown below in detail.",
                        "Table 3. Segment revenue by year (USD m)",
                        "Source: company filings. Growth figures are rounded to whole percent.",
                        "Management expects Cloud to remain the main driver next year."]


def booktabs_tight_pdf(path: Path) -> Path:
    """A booktabs table at 13 pt row pitch with a paragraph and caption right above it and a source
    line and a paragraph right under it (reviewer page p1)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((50, 60), BOOKTABS_TIGHT_LINES[0], fontsize=10)
    page.insert_text((50, 74), BOOKTABS_TIGHT_LINES[1], fontsize=10)
    y = 80
    page.draw_line((50, y), (420, y), width=1.0)
    for i, row in enumerate(SIDEBAR_ROWS):
        y += 13
        for x, value in zip([50, 190, 270, 350], row):
            page.insert_text((x, y), value, fontsize=10)
        if i == 0:
            page.draw_line((50, y + 3), (420, y + 3), width=0.5)
    page.draw_line((50, y + 4), (420, y + 4), width=1.0)
    page.insert_text((50, y + 16), BOOKTABS_TIGHT_LINES[2], fontsize=10)
    page.insert_text((50, y + 30), BOOKTABS_TIGHT_LINES[3], fontsize=10)
    doc.save(str(path))
    doc.close()
    return Path(path)


LEDGER_ROWS = [["Account", "Amount"], ["Revenue from services", "1,250,000"], ["Cost of goods sold", "640,000"],
               ["Operating income", "610,000"]]
LEDGER_OCR = [["Acc0unt", "Arnount"], ["Revenue frorn services", "1,25O,OOO"], ["Cost of qoods sold", "640,0O0"],
              ["Operatinq income", "61O,000"]]


def ocr_layer_table_pdf(path: Path) -> Path:
    """A ruled table whose cells also carry an invisible OCR text layer (render mode 3) with typical
    recognition errors, drawn at the same positions (reviewer page p11)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    for r, (row, ocr_row) in enumerate(zip(LEDGER_ROWS, LEDGER_OCR)):
        for c, (text, ocr_text) in enumerate(zip(row, ocr_row)):
            x0, y0 = 50 + c * 200, 100 + r * 20
            page.draw_rect(pymupdf.Rect(x0, y0, x0 + 200, y0 + 20), color=(0, 0, 0), width=0.5)
            page.insert_text((x0 + 3, y0 + 14), text, fontsize=10)
            page.insert_text((x0 + 3, y0 + 14), ocr_text, fontsize=10, render_mode=3)
    doc.save(str(path))
    doc.close()
    return Path(path)


CONTENTS = [("1", "Introduction", "3"), ("2", "Market overview", "5"), ("3", "Financial results", "9"),
            ("4", "Risk factors", "14"), ("5", "Outlook", "18"), ("6", "Appendix", "21")]


def contents_pdf(path: Path) -> Path:
    """Two table-of-contents pages: number / title / right-aligned page, and ``Chapter N`` / title /
    ``p. N`` / page (reviewer page p3)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((50, 50), "Contents", fontsize=16)
    for i, (number, title, page_no) in enumerate(CONTENTS):
        page.insert_text((50, 90 + i * 18), number, fontsize=11)
        page.insert_text((80, 90 + i * 18), title, fontsize=11)
        _right_aligned(page, 520, 90 + i * 18, page_no, fontsize=11)
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((50, 50), "Contents", fontsize=16)
    for i, (number, title, page_no) in enumerate(CONTENTS):
        page.insert_text((50, 90 + i * 18), f"Chapter {number}", fontsize=11)
        page.insert_text((140, 90 + i * 18), title, fontsize=11)
        page.insert_text((400, 90 + i * 18), f"p. {page_no}", fontsize=11)
        page.insert_text((470, 90 + i * 18), page_no, fontsize=11)
    doc.save(str(path))
    doc.close()
    return Path(path)


DIRECTORY_WORDS = "alpha beta gamma delta 12 345 6,789 epsilon zeta eta theta 2024 iota kappa".split()


def columnar_directory_pdf(path: Path, pages: int, *, as_prose: bool = False) -> Path:
    """A dense directory: four columns of 110 short 6 pt entries (three words each) per page --
    column-aligned text with numbers but no table (reviewer's perf_4col_directory). ``as_prose`` writes
    the same entries as one paragraph per page instead, the no-columns baseline."""
    doc = pymupdf.open()
    index = 0
    for _ in range(pages):
        page = doc.new_page(width=A4[0], height=A4[1])
        entries = []
        for _ in range(4 * 110):
            entries.append(" ".join(DIRECTORY_WORDS[(index + k * 5) % len(DIRECTORY_WORDS)] for k in range(3)))
            index += 1
        if as_prose:
            page.insert_textbox(pymupdf.Rect(30, 30, 565, 812), " ".join(entries), fontsize=6)
        else:
            for column in range(4):
                for row in range(110):
                    page.insert_text((30 + column * 140, 30 + row * 7.2), entries[column * 110 + row], fontsize=6)
    doc.save(str(path))
    doc.close()
    return Path(path)


BONUS_ROWS = [["Region", "Pool", "Paid"], ["North", "40k", "38k"], ["South", "35k", "35k"], ["East", "20k", "18k"]]


def page_top_table_pdf(path: Path, *, heading: bool, running_header: bool) -> Path:
    """Page 1 ends with a ruled staff table whose header row is bold. Page 2 starts with a table with
    the same columns right under the top 8% of the page. With ``heading`` that is a new table
    (``BONUS_ROWS``) under a bold heading set inside the top 8%; without it, the staff table continues
    (Emp8-Emp10, no repeated header). ``running_header`` puts the same running header line at the very
    top of both pages."""
    doc = pymupdf.open()
    for page_no in range(2):
        page = doc.new_page(width=A4[0], height=A4[1])
        if running_header:
            page.insert_text((50, 24), "ACME Annual Report 2025", fontsize=8)
        if page_no == 0:
            rows = [SPLIT_HEADER] + [[f"Emp{i}", f"D{i % 3}", f"{50 + i}k"] for i in range(1, 8)]
            page.insert_text((50, 620), "Staff list", fontsize=10)
            ruled_table(page, 50, 650, [100, 80, 80], [18] * len(rows), grid(rows), bold_rows=[0])
        elif heading:
            page.insert_text((50, 44), "Bonus pool by region", fontname="hebo", fontsize=11)
            ruled_table(page, 50, 56, [100, 80, 80], [18] * len(BONUS_ROWS), grid(BONUS_ROWS))
        else:
            rows = [[f"Emp{i}", f"D{i % 3}", f"{50 + i}k"] for i in range(8, 11)]
            ruled_table(page, 50, 56, [100, 80, 80], [18] * len(rows), grid(rows))
    doc.save(str(path))
    doc.close()
    return Path(path)


def different_first_page_pdf(path: Path) -> Path:
    """Word's "different first page": page 1 has a letterhead and no running header, pages 2 and 3
    carry ``ACME Annual Report 2025 - page N`` at the very top. The staff table (bold header row)
    ends page 1 and continues right under page 2's running header (Emp8-Emp10, no repeated header);
    page 3 is text."""
    doc = pymupdf.open()
    for page_no in range(3):
        page = doc.new_page(width=A4[0], height=A4[1])
        if page_no == 0:
            page.insert_text((50, 40), "ACME Corporation", fontname="hebo", fontsize=16)
            page.insert_text((50, 56), "1 Harbour Road, Springfield", fontsize=9)
            rows = [SPLIT_HEADER] + [[f"Emp{i}", f"D{i % 3}", f"{50 + i}k"] for i in range(1, 8)]
            page.insert_text((50, 620), "Staff list", fontsize=10)
            ruled_table(page, 50, 650, [100, 80, 80], [18] * len(rows), grid(rows), bold_rows=[0])
            continue
        page.insert_text((50, 24), f"ACME Annual Report 2025 - page {page_no + 1}", fontsize=8)
        if page_no == 1:
            rows = [[f"Emp{i}", f"D{i % 3}", f"{50 + i}k"] for i in range(8, 11)]
            ruled_table(page, 50, 56, [100, 80, 80], [18] * len(rows), grid(rows))
        else:
            page.insert_text((50, 80), "Headcount figures are as of 31 December 2025.", fontsize=10)
    doc.save(str(path))
    doc.close()
    return Path(path)


QUARTER_ROWS = [["Region", "Q1", "Q2"], ["North", "120", "135"], ["South", "98", "101"]]
CENTRED_ROWS = [["Item", "Units", "Share"], ["Paper", "5", "2%"], ["Toner", "120", "31%"],
                ["Laptops", "1,234", "67%"]]
SMALL_LEAD = "Regional figures for the first half are summarised below."
SMALL_CLOSE = "All figures are unaudited."


def small_borderless_pdf(path: Path, *, centred: bool) -> Path:
    """Borderless tables at the validator's minimums, a paragraph away from a lead-in and a closing
    sentence: a header and two rows of right-aligned numbers (``QUARTER_ROWS``), or numbers centred
    in their columns the way Word centres cells (``CENTRED_ROWS``)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_text((50, 40), SMALL_LEAD, fontsize=10)
    y = 64
    for row in CENTRED_ROWS if centred else QUARTER_ROWS:
        page.insert_text((50, y), row[0], fontsize=10)
        for anchor, value in zip([220, 300], row[1:]):
            width = pymupdf.get_text_length(value, fontsize=10)
            page.insert_text((anchor - width / 2 if centred else anchor - width, y), value, fontsize=10)
        y += 14
    page.insert_text((50, y + 14), SMALL_CLOSE, fontsize=10)
    doc.save(str(path))
    doc.close()
    return Path(path)
