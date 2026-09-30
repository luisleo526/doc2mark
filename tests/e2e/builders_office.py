"""Office fixture builders for tests/e2e/test_office.py, made at test time.

Documents are written with python-docx, openpyxl and python-pptx, plus raw OOXML
where those libraries cannot express a construct (content controls, tracked
changes, simple fields, ``w:gridBefore``, background picture fills, cached
formula values). Nothing from ``doc2mark`` is imported. Every builder saves to
``path`` and returns it as a ``Path`` unless its docstring says otherwise.
"""

import datetime
import io
import random
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import openpyxl
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import parse_xml
from docx.oxml.ns import qn as wqn
from docx.shared import Inches
from lxml import etree
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.oxml.ns import qn as pqn
from pptx.util import Emu

W_NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

# 16:9 slides (10 in x 5.625 in) and a matching picture size.
SLIDE_W, SLIDE_H = Emu(9144000), Emu(5143500)
SLIDE_PIXELS = (1600, 900)


def text_picture(text: str, size: Tuple[int, int] = SLIDE_PIXELS, font_size: int = 96,
                 background: str = "white") -> bytes:
    """PNG with ``text`` drawn large (black on ``background``) so Tesseract reads it reliably."""
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=font_size)
    y = font_size // 2
    for line in text.split("\n"):
        right, bottom = draw.textbbox((60, y), line, font=font)[2:]
        if right > size[0] - 30 or bottom > size[1] - 20:
            raise ValueError(f"text does not fit the picture at font_size={font_size}: {line!r}")
        draw.text((60, y), line, fill="black", font=font)
        y = bottom + font_size // 2
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def plain_picture(size: Tuple[int, int] = (480, 270), color: str = "lightsteelblue") -> bytes:
    """A flat-colour PNG with no text on it."""
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- XLSX


def random_sparse_workbook(path: Path, blank_rate: float, sheets: int, seed: int) -> List[List[List[str]]]:
    """Workbook of ``sheets`` sheets, 12 rows x 6 columns, with NO merged ranges.

    Row 1 is a header (``Account``, ``FY2020``..``FY2024``) and column A a label; every other
    cell is a random integer, blank with probability ``blank_rate``. Saves to ``path`` and
    returns the expected grid of every sheet (strings, ``""`` for a blank cell).
    """
    rng = random.Random(seed)
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    grids = []
    for index in range(sheets):
        sheet = workbook.create_sheet(f"S{index + 1:03d}")
        header = ["Account"] + [f"FY{year}" for year in range(2020, 2025)]
        sheet.append(header)
        grid = [header]
        for row_index in range(11):
            row = [f"acct-{row_index:02d}"]
            for _ in range(5):
                row.append(None if rng.random() < blank_rate else rng.randint(100, 99999))
            sheet.append(row)
            grid.append(["" if value is None else str(value) for value in row])
        grids.append(grid)
    workbook.save(str(path))
    return grids


def workbook(path: Path, sheets: Sequence[dict]) -> Path:
    """Workbook with one sheet per spec.

    A spec is a dict with ``title`` and ``rows`` (lists of cell values, ``None`` = blank) and,
    optionally, ``merges`` (``"A1:C1"`` ranges), ``formats`` (``{"B2": "0%"}``) and ``images``
    (``{"B3": png_bytes}``, each anchored at that cell with its top-left corner).
    """
    from openpyxl.drawing.image import Image as XLImage

    book = openpyxl.Workbook()
    book.remove(book.active)
    for spec in sheets:
        sheet = book.create_sheet(spec["title"])
        for row_index, row in enumerate(spec["rows"], start=1):
            for col_index, value in enumerate(row, start=1):
                if value is not None:
                    sheet.cell(row_index, col_index, value)
        for cell_range in spec.get("merges", ()):
            sheet.merge_cells(cell_range)
        for coordinate, number_format in spec.get("formats", {}).items():
            sheet[coordinate].number_format = number_format
        for coordinate, png in spec.get("images", {}).items():
            picture = XLImage(io.BytesIO(png))
            picture.anchor = coordinate
            sheet.add_image(picture)
    book.save(str(path))
    return Path(path)


def set_cached_formula_value(path: Path, sheet_index: int, coordinate: str, cached: str) -> Path:
    """Give the formula at ``coordinate`` a cached value, as Excel does when it saves.

    openpyxl writes formulas without cached values (``<f>..</f><v></v>``); this rewrites the
    sheet XML in place so that cell carries ``<v>cached</v>``.
    """
    part = f"xl/worksheets/sheet{sheet_index}.xml"
    with zipfile.ZipFile(path) as source:
        members = {name: source.read(name) for name in source.namelist()}
    xml = members[part].decode("utf-8")
    pattern = re.compile(r'(<c r="%s"[^>]*>\s*<f>[^<]*</f>)\s*(?:<v\s*/>|<v>\s*</v>)?' % re.escape(coordinate))
    xml, count = pattern.subn(lambda m: f"{m.group(1)}<v>{cached}</v>", xml)
    if count != 1:
        raise ValueError(f"formula cell {coordinate} not found in {part}")
    members[part] = xml.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in members.items():
            target.writestr(name, data)
    return Path(path)


def requote_sheet_attribute(source: Path, path: Path, sheet_index: int, attribute: str) -> Path:
    """Copy ``source`` to ``path`` with ``attribute="..."`` written as ``attribute='...'`` in
    one worksheet part (both quote styles are legal XML)."""
    part = f"xl/worksheets/sheet{sheet_index}.xml"
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    xml = members[part].decode("utf-8")
    xml, count = re.subn(rf'(\s{attribute})="([^"]*)"', r"\1='\2'", xml)
    if not count:
        raise ValueError(f"no {attribute}= attribute in {part}")
    members[part] = xml.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in members.items():
            target.writestr(name, data)
    return Path(path)


def prefix_sheet_namespace(path: Path, sheet_index: int, prefix: str = "x") -> Path:
    """Rewrite one worksheet part so SpreadsheetML elements carry a namespace prefix
    (``<x:c r="C2"><x:f>A2+B2</x:f>``), as the Open XML SDK writes them, instead of openpyxl's
    default namespace. The workbook still means exactly the same thing."""
    part = f"xl/worksheets/sheet{sheet_index}.xml"
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(path) as source:
        members = {name: source.read(name) for name in source.namelist()}
    xml = members[part].decode("utf-8")
    if f'xmlns="{namespace}"' not in xml:
        raise ValueError(f"{part} does not use the SpreadsheetML default namespace")
    xml = xml.replace(f'xmlns="{namespace}"', f'xmlns:{prefix}="{namespace}"', 1)
    xml = re.sub(r"<(/?)([A-Za-z][\w.-]*)(?=[\s/>])", rf"<\1{prefix}:\2", xml)
    members[part] = xml.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in members.items():
            target.writestr(name, data)
    return Path(path)


# --------------------------------------------------------------------------- DOCX


def _append_xml(paragraph, xml: str) -> None:
    paragraph._p.append(parse_xml(xml))


def docx_cell_constructs(path: Path) -> Path:
    """One 2-column table (``Kind`` | ``Cell``) whose right-hand cells hold text inside
    constructs that are not direct ``w:r`` children of the cell paragraph:

    hyperlink (real relationship), inline content control, block content control,
    tracked insertion next to a tracked deletion (``DELETED-7731`` must not appear),
    simple field, smart tag, custom XML, and a complex field (control case).
    """
    document = Document()
    document.add_paragraph("Cell constructs")
    rows = ["hyperlink", "inline control", "block control", "tracked change", "simple field",
            "smart tag", "custom xml", "complex field"]
    table = document.add_table(rows=len(rows) + 1, cols=2)
    table.cell(0, 0).text = "Kind"
    table.cell(0, 1).text = "Cell"
    for index, kind in enumerate(rows, start=1):
        table.cell(index, 0).text = kind

    rid = document.part.relate_to("mailto:sales@example.com", RT.HYPERLINK, is_external=True)
    p = table.cell(1, 1).paragraphs[0]
    p.add_run("Email: ")
    _append_xml(p, f'<w:hyperlink {W_NS} r:id="{rid}"><w:r><w:t>sales@example.com</w:t></w:r></w:hyperlink>')

    p = table.cell(2, 1).paragraphs[0]
    p.add_run("Name: ")
    _append_xml(p, f'<w:sdt {W_NS}><w:sdtPr/><w:sdtContent><w:r><w:t>Wang Xiao-Ming</w:t></w:r></w:sdtContent></w:sdt>')

    tc = table.cell(3, 1)._tc
    tc.append(parse_xml(
        f'<w:sdt {W_NS}><w:sdtPr/><w:sdtContent><w:p><w:r><w:t>A123456789</w:t></w:r></w:p></w:sdtContent></w:sdt>'))

    p = table.cell(4, 1).paragraphs[0]
    p.add_run("Payment within ")
    _append_xml(p, f'<w:ins {W_NS} w:id="1" w:author="e2e" w:date="2026-01-01T00:00:00Z">'
                   f'<w:r><w:t>30</w:t></w:r></w:ins>')
    _append_xml(p, f'<w:del {W_NS} w:id="2" w:author="e2e" w:date="2026-01-01T00:00:00Z">'
                   f'<w:r><w:delText>DELETED-7731</w:delText></w:r></w:del>')
    p.add_run(" days")

    p = table.cell(5, 1).paragraphs[0]
    _append_xml(p, f'<w:fldSimple {W_NS} w:instr=" =SUM(ABOVE) "><w:r><w:t>1,250</w:t></w:r></w:fldSimple>')

    p = table.cell(6, 1).paragraphs[0]
    p.add_run("Due ")
    _append_xml(p, f'<w:smartTag {W_NS} w:uri="urn:schemas-microsoft-com:office:smarttags" w:element="date">'
                   f'<w:r><w:t>March 31, 2026</w:t></w:r></w:smartTag>')

    p = table.cell(7, 1).paragraphs[0]
    _append_xml(p, f'<w:customXml {W_NS} w:element="invoice"><w:r><w:t>INV-2026-0042</w:t></w:r></w:customXml>')

    p = table.cell(8, 1).paragraphs[0]
    for xml in ('<w:r {ns}><w:fldChar w:fldCharType="begin"/></w:r>',
                '<w:r {ns}><w:instrText> =SUM(ABOVE) </w:instrText></w:r>',
                '<w:r {ns}><w:fldChar w:fldCharType="separate"/></w:r>',
                '<w:r {ns}><w:t>9,999</w:t></w:r>',
                '<w:r {ns}><w:fldChar w:fldCharType="end"/></w:r>'):
        _append_xml(p, xml.format(ns=W_NS))
    document.save(str(path))
    return Path(path)


def docx_body_constructs(path: Path) -> Path:
    """Body paragraphs (no table) with the same constructs as :func:`docx_cell_constructs`,
    plus a block-level content control that wraps a whole paragraph."""
    document = Document()
    document.add_paragraph("Body constructs")
    p = document.add_paragraph("Pay within ")
    _append_xml(p, f'<w:ins {W_NS} w:id="1" w:author="e2e" w:date="2026-01-01T00:00:00Z">'
                   f'<w:r><w:t>45</w:t></w:r></w:ins>')
    _append_xml(p, f'<w:del {W_NS} w:id="2" w:author="e2e" w:date="2026-01-01T00:00:00Z">'
                   f'<w:r><w:delText>DELETED-5518</w:delText></w:r></w:del>')
    p.add_run(" days of invoice")
    p = document.add_paragraph("Applicant: ")
    _append_xml(p, f'<w:sdt {W_NS}><w:sdtPr/><w:sdtContent><w:r><w:t>Chen Mei-Ling</w:t></w:r></w:sdtContent></w:sdt>')
    p = document.add_paragraph("Total: ")
    _append_xml(p, f'<w:fldSimple {W_NS} w:instr=" =SUM(ABOVE) "><w:r><w:t>8,640</w:t></w:r></w:fldSimple>')
    p = document.add_paragraph("Signed on ")
    _append_xml(p, f'<w:smartTag {W_NS} w:element="date"><w:r><w:t>April 2, 2026</w:t></w:r></w:smartTag>')
    marker = document.add_paragraph("Closing paragraph")
    marker._p.addprevious(parse_xml(
        f'<w:sdt {W_NS}><w:sdtPr/><w:sdtContent><w:p><w:r><w:t>Block control paragraph 6120</w:t></w:r></w:p>'
        f'</w:sdtContent></w:sdt>'))
    document.save(str(path))
    return Path(path)


def docx_nested_table(path: Path) -> Path:
    """A 2x2 table whose bottom-right cell holds a 2x2 nested table, between two paragraphs."""
    document = Document()
    document.add_paragraph("Before the table")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Outer A"
    table.cell(0, 1).text = "Outer B"
    table.cell(1, 0).text = "Left"
    cell = table.cell(1, 1)
    cell.paragraphs[0].add_run("Terms:")
    inner = cell.add_table(rows=2, cols=2)
    inner.cell(0, 0).text = "InnerKey"
    inner.cell(0, 1).text = "InnerVal"
    inner.cell(1, 0).text = "Deadline"
    inner.cell(1, 1).text = "2026-03-31"
    cell.add_paragraph("After inner")
    document.add_paragraph("After the table")
    document.save(str(path))
    return Path(path)


def docx_grid_skip(path: Path, kind: str, row: int) -> Path:
    """3x3 table (header ``H1 H2 H3``) between two paragraphs, where table row ``row``
    (1 or 2) skips one grid column: ``kind="before"`` drops its first cell and adds
    ``w:gridBefore``; ``kind="after"`` drops its last cell and adds ``w:gridAfter``."""
    document = Document()
    document.add_paragraph("Intro paragraph that must stay first.")
    table = document.add_table(rows=3, cols=3)
    for r, values in enumerate([["H1", "H2", "H3"], ["a", "b", "c"], ["d", "e", "f"]]):
        for c, value in enumerate(values):
            table.cell(r, c).text = value
    tr = table.rows[row]._tr
    cells = tr.findall(wqn("w:tc"))
    if kind == "before":
        tr.remove(cells[0])
        tr.get_or_add_trPr().append(parse_xml(f'<w:gridBefore {W_NS} w:val="1"/>'))
    elif kind == "after":
        tr.remove(cells[-1])
        tr.get_or_add_trPr().append(parse_xml(f'<w:gridAfter {W_NS} w:val="1"/>'))
    else:
        raise ValueError(kind)
    document.add_paragraph("Closing paragraph that must stay last.")
    document.save(str(path))
    return Path(path)


def docx_headings_and_lists(path: Path) -> Path:
    """Title, Subtitle, Heading 1..6 and 9, numbered and bulleted lists (with a nested
    level), and a caption, all through Word's built-in styles."""
    document = Document()
    document.add_paragraph("Contract", style="Title")
    document.add_paragraph("Services Agreement", style="Subtitle")
    document.add_heading("Definitions", level=1)
    document.add_heading("Scope", level=2)
    document.add_heading("Details", level=3)
    document.add_heading("Level four", level=4)
    document.add_heading("Level five", level=5)
    document.add_heading("Level six", level=6)
    document.add_heading("Level nine", level=9)
    for text in ("Buy milk", "Buy eggs", "Buy bread"):
        document.add_paragraph(text, style="List Number")
    document.add_paragraph("A plain paragraph between the lists.")
    document.add_paragraph("First bullet", style="List Bullet")
    document.add_paragraph("Nested bullet", style="List Bullet 2")
    document.add_paragraph("Second bullet", style="List Bullet")
    document.add_paragraph("Figure 1: Revenue by quarter", style="Caption")
    document.save(str(path))
    return Path(path)


def docx_body_text_outline(path: Path) -> Path:
    """Paragraphs whose outline level says body text (``w:outlineLvl w:val="9"``) although
    their style is a heading: one set on the paragraph itself, one on a custom style based on
    Heading 2; plus an ordinary Heading 2 as a control."""
    document = Document()
    document.add_paragraph("Opening paragraph.")
    demoted = document.add_heading("Demoted by the paragraph", level=2)
    demoted._p.get_or_add_pPr().append(parse_xml(f'<w:outlineLvl {W_NS} w:val="9"/>'))
    style = document.styles.add_style("Body Heading", 1)  # 1 = WD_STYLE_TYPE.PARAGRAPH
    style.base_style = document.styles["Heading 2"]
    style.element.get_or_add_pPr().append(parse_xml(f'<w:outlineLvl {W_NS} w:val="9"/>'))
    document.add_paragraph("Demoted by its style", style="Body Heading")
    document.add_heading("Real section", level=2)
    document.save(str(path))
    return Path(path)


def docx_table_text_over_picture(path: Path) -> Path:
    """A form: a large picture (about 60% of a Letter page) and a table that holds all of the
    document's text (no body paragraph text)."""
    document = Document()
    document.add_picture(io.BytesIO(plain_picture((800, 600))), width=Inches(6.5), height=Inches(8.5))
    table = document.add_table(rows=8, cols=3)
    for r in range(8):
        for c in range(3):
            table.cell(r, c).text = f"Line item {r}.{c}: qty {r + c} x EUR {r * 10 + c}.99"
    document.save(str(path))
    return Path(path)


# --------------------------------------------------------------------------- PPTX


def _deck() -> Presentation:
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    return prs


def _blank_slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def pptx_soft_breaks(path: Path) -> Path:
    """One slide: a 2x2 table whose cell (1,1) is ``line one`` + soft line break (``a:br``)
    + ``tail``, and a text box with the same soft break."""
    prs = _deck()
    slide = _blank_slide(prs)
    table = slide.shapes.add_table(2, 2, Emu(400000), Emu(400000), Emu(8000000), Emu(1500000)).table
    table.cell(0, 0).text = "Key"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "softbreak"
    for frame in (table.cell(1, 1).text_frame,
                  slide.shapes.add_textbox(Emu(400000), Emu(2500000), Emu(8000000), Emu(1000000)).text_frame):
        frame.text = "line one"
        paragraph = frame.paragraphs[0]
        paragraph.add_run().text = "tail"
        paragraph._p.insert(len(paragraph._p) - 1, etree.Element(f"{{{A_NS}}}br"))
    prs.save(str(path))
    return Path(path)


def _set_background_picture(slide, png: bytes) -> None:
    _, rid = slide.part.get_or_add_image_part(io.BytesIO(png))
    c_sld = slide._element.find(pqn("p:cSld"))
    background = etree.fromstring(
        '<p:bg xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        f'xmlns:a="{A_NS}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<p:bgPr><a:blipFill dpi="0" rotWithShape="1"><a:blip r:embed="{rid}"/><a:srcRect/>'
        '<a:stretch><a:fillRect/></a:stretch></a:blipFill><a:effectLst/></p:bgPr></p:bg>')
    c_sld.insert(0, background)


def pptx_background_deck(path: Path, phrases: Sequence[str]) -> Path:
    """One slide per phrase; each slide's only content is a background picture fill
    (``p:bg/p:bgPr/a:blipFill``) showing that phrase."""
    prs = _deck()
    for phrase in phrases:
        _set_background_picture(_blank_slide(prs), text_picture(phrase))
    prs.save(str(path))
    return Path(path)


def pptx_picture_deck(path: Path, phrases: Sequence[str]) -> Path:
    """One slide per phrase; each slide is a single full-slide picture showing that phrase."""
    prs = _deck()
    for phrase in phrases:
        _blank_slide(prs).shapes.add_picture(io.BytesIO(text_picture(phrase)), 0, 0, SLIDE_W, SLIDE_H)
    prs.save(str(path))
    return Path(path)


def pptx_group_picture_deck(path: Path, phrases: Sequence[str]) -> Path:
    """Like :func:`pptx_picture_deck`, but each full-slide picture sits inside a group shape."""
    prs = _deck()
    for phrase in phrases:
        group = _blank_slide(prs).shapes.add_group_shape()
        group.shapes.add_picture(io.BytesIO(text_picture(phrase)), 0, 0, SLIDE_W, SLIDE_H)
    prs.save(str(path))
    return Path(path)


def pptx_placeholder_picture_deck(path: Path, phrases: Sequence[str]) -> Path:
    """One "Picture with Caption" slide per phrase with the phrase picture inserted into the
    layout's picture placeholder (so the shape is a placeholder, not a plain picture)."""
    prs = _deck()
    layout = next(layout for layout in prs.slide_layouts if layout.name == "Picture with Caption")
    for phrase in phrases:
        slide = prs.slides.add_slide(layout)
        placeholder = next(p for p in slide.placeholders if "PICTURE" in str(p.placeholder_format.type))
        picture_idx = placeholder.placeholder_format.idx
        picture = placeholder.insert_picture(io.BytesIO(text_picture(phrase)))
        picture.left, picture.top, picture.width, picture.height = 0, 0, SLIDE_W, SLIDE_H
        picture.crop_left = picture.crop_right = picture.crop_top = picture.crop_bottom = 0.0
        for other in list(slide.placeholders):
            if other.placeholder_format.idx != picture_idx:
                other._element.getparent().remove(other._element)
    prs.save(str(path))
    return Path(path)


def pptx_table_text_over_picture(path: Path, slides: int = 4) -> Path:
    """Each slide: a full-bleed plain picture under a 4x3 table that holds all the slide's
    text (``Region r metric c: value ...``)."""
    prs = _deck()
    for k in range(slides):
        slide = _blank_slide(prs)
        slide.shapes.add_picture(io.BytesIO(plain_picture()), 0, 0, SLIDE_W, SLIDE_H)
        table = slide.shapes.add_table(4, 3, Emu(400000), Emu(400000), Emu(8000000), Emu(3000000)).table
        for r in range(4):
            for c in range(3):
                table.cell(r, c).text = f"Region {r} metric {c}: value {k}{r * 100 + c * 7}.5 EUR"
    prs.save(str(path))
    return Path(path)


def pptx_group_text_over_picture(path: Path, slides: int = 4) -> Path:
    """Each slide: a full-bleed plain picture under a grouped text box with ~300 characters."""
    prs = _deck()
    for k in range(slides):
        slide = _blank_slide(prs)
        slide.shapes.add_picture(io.BytesIO(plain_picture()), 0, 0, SLIDE_W, SLIDE_H)
        group = slide.shapes.add_group_shape()
        box = group.shapes.add_textbox(Emu(400000), Emu(400000), Emu(8000000), Emu(3000000))
        box.text_frame.word_wrap = True
        box.text_frame.text = f"Grouped callout {k + 1}: text that carries the slide's real message. " * 5
    prs.save(str(path))
    return Path(path)


TEXT_FREE_FILLER = ("Quarterly operations review: shipments grew in every region while "
                    "returns fell, and the team closed the open audit items before the deadline.")


def textless_pictures_workbook(path: Path) -> Path:
    """A table with a text-free picture anchored in cell B2 and another anchored at E9,
    outside the used range."""
    blank = plain_picture((600, 400))
    return workbook(path, [{
        "title": "Photos",
        "rows": [["Item", "Photo"], ["pump", None], ["valve", None]],
        "images": {"B2": blank, "E9": blank},
    }])


def textless_pictures_document(path: Path) -> Path:
    """Body paragraphs around a text-free inline picture (small, so the document stays on the
    native path)."""
    document = Document()
    document.add_paragraph("Before the picture. " + TEXT_FREE_FILLER)
    document.add_picture(io.BytesIO(plain_picture((600, 400))), width=Inches(2.0), height=Inches(1.3))
    document.add_paragraph("After the picture. " + TEXT_FREE_FILLER)
    document.save(str(path))
    return Path(path)


def textless_pictures_deck(path: Path) -> Path:
    """Three text slides (enough text to stay on the native path) with text-free pictures:
    a small picture shape, the slide's own background picture fill, and a picture inserted
    into a picture placeholder."""
    prs = _deck()
    blank = plain_picture((600, 400))

    slide = _blank_slide(prs)
    slide.shapes.add_picture(io.BytesIO(blank), Emu(6000000), Emu(3500000), Emu(1800000), Emu(1200000))
    _add_text_box(slide, "Slide with a picture shape. " + TEXT_FREE_FILLER * 2)

    slide = _blank_slide(prs)
    _set_background_picture(slide, blank)
    _add_text_box(slide, "Slide with a background picture. " + TEXT_FREE_FILLER * 2)

    layout = next(layout for layout in prs.slide_layouts if layout.name == "Picture with Caption")
    slide = prs.slides.add_slide(layout)
    placeholder = next(p for p in slide.placeholders if "PICTURE" in str(p.placeholder_format.type))
    picture_idx = placeholder.placeholder_format.idx
    placeholder.insert_picture(io.BytesIO(blank))
    for other in list(slide.placeholders):
        if other.placeholder_format.idx != picture_idx:
            other._element.getparent().remove(other._element)
    _add_text_box(slide, "Slide with a picture placeholder. " + TEXT_FREE_FILLER * 2)

    prs.save(str(path))
    return Path(path)


def _add_text_box(slide, text: str) -> None:
    box = slide.shapes.add_textbox(Emu(400000), Emu(300000), Emu(8300000), Emu(1500000))
    box.text_frame.word_wrap = True
    box.text_frame.text = text


def xlsx_formats_rows() -> Tuple[List[list], Dict[str, str], Dict[Tuple[int, int], str]]:
    """Rows, number formats and the expected displayed text for the value-fidelity sheet.

    Returns ``(rows, formats, expected)`` where ``expected`` maps a 0-based (row, col) grid
    position to the text a spreadsheet application displays for that cell.
    """
    rows = [
        ["Item", "Qty", "Rate", "Share", "Due", "Stamp", "Price", "Units", "Ratio", "Flag"],
        ["alpha", 10, 0.25, 0.125, datetime.date(2026, 3, 31), datetime.datetime(2026, 3, 31, 14, 5),
         1234.5, 12345, 0.1 + 0.2, True],
        ["beta", None, 0.3, 0.5, datetime.date(2026, 4, 1), datetime.datetime(2026, 4, 1, 9, 30),
         -42.0, 7, 2.5, False],
        ["gamma", 7, 1, 0.0425, datetime.date(2026, 12, 5), datetime.datetime(2026, 12, 5, 23, 59),
         0, 1000000, 1e-7, None],
    ]
    formats = {}
    for r in (2, 3, 4):
        formats[f"C{r}"] = "0%"
        formats[f"D{r}"] = "0.00%"
        formats[f"E{r}"] = "yyyy-mm-dd"
        formats[f"F{r}"] = "yyyy-mm-dd hh:mm"
        formats[f"G{r}"] = '"$"#,##0.00'
        formats[f"H{r}"] = "#,##0"
    expected = {
        (1, 1): "10", (3, 1): "7",
        (1, 2): "25%", (2, 2): "30%", (3, 2): "100%",
        (1, 3): "12.50%", (2, 3): "50.00%", (3, 3): "4.25%",
        (1, 4): "2026-03-31", (2, 4): "2026-04-01", (3, 4): "2026-12-05",
        (1, 5): "2026-03-31 14:05", (2, 5): "2026-04-01 09:30", (3, 5): "2026-12-05 23:59",
        (1, 6): "$1,234.50", (2, 6): "-$42.00", (3, 6): "$0.00",
        (1, 7): "12,345", (2, 7): "7", (3, 7): "1,000,000",
        (1, 8): "0.3", (2, 8): "2.5", (3, 8): "1E-07",
        (1, 9): "TRUE", (2, 9): "FALSE",
    }
    return rows, formats, expected


def optional_png(text: Optional[str]) -> Optional[bytes]:
    """``text_picture(text)`` sized for a spreadsheet cell anchor, or None."""
    return None if text is None else text_picture(text, size=(900, 260), font_size=90)
