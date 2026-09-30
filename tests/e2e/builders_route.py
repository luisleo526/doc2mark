"""PDF builders for the OCR-routing E2E tests (``tests/e2e/test_route.py``).

Ports of the routing review's probe fixtures (``p11``, ``p11b``, ``p12``, ``p12b``,
``p13``) and of the round-1 review of the routing change (``p1*``, ``p2``, ``p3``, ``p8``,
``p9``), made at test time with PyMuPDF and Pillow only: text uses PyMuPDF's built-in
fonts (Helvetica, the Nimbus Roman clone ``tiro``, Droid Sans Fallback as ``china-t``)
and pictures Pillow's built-in font, so no system font is needed. Nothing from
``doc2mark`` is imported.

A "broken" text layer is made the way real designed/print PDFs break it: the
glyphs render correctly, but the embedded font's ToUnicode map is removed or
rewritten, so text extraction yields U+FFFD, private-use or mojibake characters
while OCR of the page render reads the real words.
"""

import binascii
import io
import re
import zlib
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Tuple

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from tests.e2e import pdfgen

A4 = pdfgen.A4_POINTS
SLIDE = (960, 540)
MARGIN = pdfgen.MARGIN_POINTS

#: Resource name of the embedded font whose text layer ``garble`` corrupts.
BROKEN_FONT = "F9"


# --------------------------------------------------------------------------- images


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def solid_png(size: Tuple[int, int], color) -> bytes:
    """A one-colour PNG (backgrounds, "photos" without text)."""
    return _png(Image.new("RGB", size, color))


def picture_png(lines: Sequence[str], size: Tuple[int, int], *, font_px: int, background="white") -> bytes:
    """A PNG of ``lines`` drawn in black on ``background``: text baked into a picture, readable only by OCR."""
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=font_px)
    y = font_px
    for line in lines:
        right, bottom = draw.textbbox((font_px, y), line, font=font)[2:]
        if right > size[0] or bottom > size[1]:
            raise ValueError(f"text does not fit the picture at font_px={font_px}: {line!r}")
        draw.text((font_px, y), line, fill="black", font=font)
        y = bottom + font_px // 2
    return _png(image)


# ------------------------------------------------------------------- text layers


def insert_lines(page, lines: Sequence[str], *, top: float, fontsize: float, fontname: str = "helv",
                 left: float = MARGIN, leading: float = 1.5, render_mode: int = 0, color=(0, 0, 0)) -> float:
    """Write one text line per item starting at ``top``; return the y below the last line.

    A line wider than the page raises ``ValueError`` instead of being clipped silently.
    """
    y = top
    for line in lines:
        if fontname != BROKEN_FONT and pymupdf.get_text_length(line, fontname=fontname, fontsize=fontsize) > (
                page.rect.width - left - MARGIN / 2):
            raise ValueError(f"line is wider than the page at fontsize={fontsize}: {line!r}")
        page.insert_text((left, y), line, fontname=fontname, fontsize=fontsize, render_mode=render_mode, color=color)
        y += fontsize * leading
    return y


def add_broken_font(page) -> None:
    """Embed the font that :func:`garble` later corrupts, as resource ``BROKEN_FONT``."""
    page.insert_font(fontname=BROKEN_FONT, fontbuffer=pymupdf.Font("tiro").buffer)


def _broken_font_xrefs(doc) -> set:
    return {
        font[0]
        for page in doc
        for font in page.get_fonts(full=True)
        if font[4] == BROKEN_FONT
    }


def _cmap_pairs(cmap: str):
    """(source code hex, destination char) for every mapping in a ToUnicode CMap."""
    for block in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            yield src, chr(int(dst[:4], 16))
    for block in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
        for lo, hi, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            for code in range(int(lo, 16), int(hi, 16) + 1):
                yield f"{code:04X}", chr(int(dst[:4], 16) + code - int(lo, 16))


def _mojibake(char: str) -> str:
    """UTF-8 read as Latin-1 for a few vowels, as when a CMap was built from the wrong encoding."""
    return {"e": "é", "a": "ä", "o": "ö"}.get(char, char).encode("utf-8").decode("latin-1") if char in "aeo" else char


def _shift(char: str) -> str:
    """Every letter replaced by the one three places later: valid Unicode, but nonsense words."""
    for first in ("a", "A"):
        if ord(first) <= ord(char) <= ord(first) + 25:
            return chr((ord(char) - ord(first) + 3) % 26 + ord(first))
    return char


def _glyph_offset(char: str) -> str:
    """Letters 29 code points lower, as a subset font whose glyph IDs were taken for Unicode
    ("Sample" -> "6DPSOH"): valid printable ASCII, but no words."""
    return chr(ord(char) - 29) if char.isascii() and char.isalpha() else char


GARBLERS: Dict[str, Optional[Callable[[str], str]]] = {
    "fffd": None,  # no ToUnicode at all: every glyph extracts as U+FFFD
    "pua": lambda ch: ch if ch.isspace() else chr(0xE000 + ord(ch) % 200),
    "mojibake": _mojibake,
    "shifted": _shift,
    "glyph_offset": _glyph_offset,
}


def garble(doc, mode: str) -> None:
    """Corrupt the text layer of every ``BROKEN_FONT`` span; the rendered glyphs stay intact."""
    rewrite = GARBLERS[mode]
    xrefs = _broken_font_xrefs(doc)
    if not xrefs:
        raise ValueError("no BROKEN_FONT text in the document")
    for xref in xrefs:
        kind, value = doc.xref_get_key(xref, "ToUnicode")
        if kind != "xref":
            raise ValueError(f"font {xref} has no ToUnicode stream to corrupt")
        if rewrite is None:
            doc.xref_set_key(xref, "ToUnicode", "null")
            continue
        cmap_xref = int(value.split()[0])
        cmap = doc.xref_stream(cmap_xref).decode("latin-1")
        entries = [
            f"<{src}> <{''.join(f'{ord(c):04X}' for c in rewrite(char))}>"
            for src, char in _cmap_pairs(cmap)
        ]
        doc.update_stream(cmap_xref, (
            "/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /X def "
            "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
            f"{len(entries)} beginbfchar\n" + "\n".join(entries) + "\nendbfchar\n"
            "endcmap CMapName currentdict /CMap defineresource pop end end"
        ).encode())


def _save(doc, path: Path) -> Path:
    doc.save(str(path))
    doc.close()
    return Path(path)


# ------------------------------------------------------------------- page makers


def scan_page(doc, text: str, *, font_px: int = 110):
    """A4 page that is one full-page picture of ``text`` (no text layer)."""
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_image(page.rect, stream=pdfgen.text_png(text, font_size=font_px))
    return page


def text_page(doc, lines: Sequence[str], *, fontsize: float = 11):
    """A4 page with a real, legible Helvetica text layer."""
    page = doc.new_page(width=A4[0], height=A4[1])
    insert_lines(page, lines, top=MARGIN + fontsize, fontsize=fontsize)
    return page


def inline_scan_page(doc, text: str, *, height_share: float = 1.0, heading: str = ""):
    """A4 page showing a picture of ``text`` as an INLINE image (BI/ID/EI) only: no image XObject.

    The picture spans the full width and the top ``height_share`` of the page (cropped to that
    part of the scan). ``heading`` (optional) is a line of real text below it.
    """
    page = doc.new_page(width=A4[0], height=A4[1])
    gray = Image.open(io.BytesIO(pdfgen.text_png(text))).convert("L")
    gray = gray.crop((0, 0, gray.width, round(gray.height * height_share)))
    data = binascii.hexlify(zlib.compress(gray.tobytes(), 9)).decode()
    width, height = gray.size
    shown = A4[1] * height_share
    content = (f"q {A4[0]} 0 0 {shown:.2f} 0 {A4[1] - shown:.2f} cm\n"
               f"BI /W {width} /H {height} /BPC 8 /CS /G /F [/AHx /Fl] ID\n{data}>\nEI Q\n")
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content.encode())
    doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
    if heading:
        insert_lines(page, [heading], top=shown + 30, fontsize=12)
    return page


def tiled_scan_page(doc, text: str, *, grid: int = 12):
    """A4 page showing a picture of ``text`` cut into ``grid`` x ``grid`` separate image tiles."""
    page = doc.new_page(width=A4[0], height=A4[1])
    picture = Image.open(io.BytesIO(pdfgen.text_png(text)))
    tile_w, tile_h = picture.width / grid, picture.height / grid
    for row in range(grid):
        for col in range(grid):
            box = (round(col * tile_w), round(row * tile_h), round((col + 1) * tile_w), round((row + 1) * tile_h))
            rect = pymupdf.Rect(col * A4[0] / grid, row * A4[1] / grid,
                                (col + 1) * A4[0] / grid, (row + 1) * A4[1] / grid)
            page.insert_image(rect, stream=_png(picture.crop(box)))
    return page


def vector_page(doc, lines: Sequence[str], *, fontsize: float = 36, color=(0, 0, 0)):
    """A4 page with ``lines`` drawn as glyph OUTLINES (vector paths): no text layer, no raster image."""
    source = pymupdf.open()
    page = source.new_page(width=A4[0], height=A4[1])
    insert_lines(page, lines, top=MARGIN + fontsize, fontsize=fontsize, color=color)
    svg = page.get_svg_image(text_as_path=True)
    source.close()
    outlined = pymupdf.open("pdf", pymupdf.open(stream=svg.encode(), filetype="svg").convert_to_pdf())
    doc.insert_pdf(outlined)
    outlined.close()
    return doc[-1]


PAGE_MAKERS = {
    "text": lambda doc, content: text_page(doc, content.split("\n")),
    "scan": scan_page,
    "inline_scan": inline_scan_page,
    "tiled_scan": tiled_scan_page,
    "vector": lambda doc, content: vector_page(doc, content.split("\n")),
}


def mixed_pdf(path: Path, pages: Sequence[Tuple[str, str]]) -> Path:
    """One page per ``(kind, text)``; ``kind`` is a key of ``PAGE_MAKERS``."""
    doc = pymupdf.open()
    for kind, content in pages:
        PAGE_MAKERS[kind](doc, content)
    return _save(doc, path)


# ------------------------------------------------------------------- documents


def searchable_scan_pdf(path: Path, pages: Sequence[Tuple[str, str]]) -> Path:
    """Searchable scan (Acrobat / ocrmypdf style): per ``(scan_text, layer_text)``, a full-page picture of
    ``scan_text`` under an INVISIBLE (render mode 3) text layer holding ``layer_text``, line by line where the
    picture shows it."""
    doc = pymupdf.open()
    for scan_text, layer_text in pages:
        page = scan_page(doc, scan_text, font_px=56)
        insert_lines(page, layer_text.split("\n"), top=MARGIN / 2 + 20, fontsize=20, left=MARGIN / 2,
                     leading=1.6, render_mode=3)
    return _save(doc, path)


def hidden_text_pdf(path: Path, visible: Sequence[str], hidden: str) -> Path:
    """A normal text page (no pictures) that also carries one INVISIBLE (render mode 3) line."""
    doc = pymupdf.open()
    page = text_page(doc, visible)
    insert_lines(page, [hidden], top=A4[1] / 2, fontsize=11, render_mode=3)
    return _save(doc, path)


def garbled_text_pdf(path: Path, title: str, body: Sequence[str], mode: str) -> Path:
    """A text-only page whose whole text layer is garbled (``mode`` is a key of ``GARBLERS``)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    add_broken_font(page)
    y = insert_lines(page, [title], top=MARGIN + 24, fontsize=24, fontname=BROKEN_FONT)
    insert_lines(page, body, top=y + 12, fontsize=13, fontname=BROKEN_FONT)
    garble(doc, mode)
    return _save(doc, path)


def image_deck_pdf(path: Path, slides: Sequence[Sequence[str]], appendix: Sequence[str] = ()) -> Path:
    """Slide deck: each slide is one full-bleed picture with its text baked in (no text layer); an optional
    last ``appendix`` page carries a dense, real text layer and no picture."""
    doc = pymupdf.open()
    for lines in slides:
        page = doc.new_page(width=SLIDE[0], height=SLIDE[1])
        page.insert_image(page.rect, stream=picture_png(lines, (1600, 900), font_px=90))
    if appendix:
        page = doc.new_page(width=SLIDE[0], height=SLIDE[1])
        insert_lines(page, appendix, top=48, fontsize=10, left=48, leading=1.4)
    return _save(doc, path)


def cjk_deck_pdf(path: Path, slides: Sequence[Sequence[str]], *, baked: Sequence[Sequence[str]] = ()) -> Path:
    """Slide deck with a full-bleed light picture per slide and a live CJK text layer (``slides[i]`` lines).

    ``baked[i]`` (optional) is Latin text drawn INTO slide ``i``'s picture, the way real image decks carry
    most of their words in the artwork; without it the picture is a plain background.
    """
    doc = pymupdf.open()
    for index, lines in enumerate(slides):
        page = doc.new_page(width=SLIDE[0], height=SLIDE[1])
        artwork = baked[index] if index < len(baked) else ()
        background = picture_png(artwork, (1600, 900), font_px=80, background="lightsteelblue") if artwork \
            else solid_png((480, 270), "lightsteelblue")
        page.insert_image(page.rect, stream=background)
        top = SLIDE[1] - 40 * len(lines) - 20 if artwork else 70
        insert_lines(page, lines, top=top, fontsize=24, fontname="china-t", left=50, leading=1.45)
    return _save(doc, path)


def bleed_and_overlap_pdf(path: Path, letters: Sequence[Sequence[str]]) -> Path:
    """Two sparse text pages whose pictures mostly do NOT cover them (routing review p11 cases 1 and 2).

    Page 1: a picture twice the page size placed so only its top-left quarter is on the page (print bleed).
    Page 2: two pictures stacked on the same 30 % band. ``letters`` holds the two pages' text lines.
    """
    doc = pymupdf.open()
    width, height = A4
    first = text_page(doc, letters[0])
    first.insert_image(pymupdf.Rect(width * 0.5, height * 0.5, width * 2.5, height * 2.5),
                       stream=solid_png((400, 400), "darkseagreen"))
    second = text_page(doc, letters[1])
    band = pymupdf.Rect(0, height * 0.55, width, height * 0.85)
    second.insert_image(band, stream=solid_png((300, 150), "lightsteelblue"))
    second.insert_image(band, stream=solid_png((300, 150), "lightblue"))
    return _save(doc, path)


def _background(page, color="whitesmoke") -> None:
    page.insert_image(page.rect, stream=solid_png((300, 424), color))


def icon_glyph_pdf(path: Path, body: Sequence[str]) -> Path:
    """Full-bleed background page with a legible body and ONE big glyph that has no Unicode mapping
    (a decorative icon drawn with a font lacking ToUnicode), the largest text on the page."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    _background(page)
    add_broken_font(page)
    insert_lines(page, ["*"], top=MARGIN + 40, fontsize=48, fontname=BROKEN_FONT)
    insert_lines(page, body, top=MARGIN + 110, fontsize=11)
    garble(doc, "fffd")
    return _save(doc, path)


def brochure_pdf(path: Path, pages: Sequence[Tuple[str, Sequence[str]]], *, broken_titles: Sequence[int]) -> Path:
    """Brochure: every page a full-bleed background with a title and a legible body; the titles of the pages
    listed in ``broken_titles`` (0-based) are drawn in a font whose text layer extracts as U+FFFD."""
    doc = pymupdf.open()
    for index, (title, body) in enumerate(pages):
        page = doc.new_page(width=A4[0], height=A4[1])
        _background(page, "honeydew")
        font = "helv"
        if index in broken_titles:
            add_broken_font(page)
            font = BROKEN_FONT
        y = insert_lines(page, [title], top=MARGIN + 30, fontsize=30, fontname=font)
        insert_lines(page, body, top=y + 16, fontsize=11)
    garble(doc, "fffd")
    return _save(doc, path)


def page_number_title_pdf(path: Path, number: str, title: str, body: Sequence[str]) -> Path:
    """Full-bleed background page: a big legible page number, an illegible title at 0.8x its size (U+FFFD
    text layer), and a legible body (routing review p13 case 5)."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    _background(page)
    add_broken_font(page)
    insert_lines(page, [number], top=MARGIN + 30, fontsize=30)
    insert_lines(page, [title], top=MARGIN + 80, fontsize=24, fontname=BROKEN_FONT)
    insert_lines(page, body, top=MARGIN + 130, fontsize=11)
    garble(doc, "fffd")
    return _save(doc, path)


def table_with_hidden_text_pdf(path: Path, intro: Sequence[str], rows: Sequence[Sequence[str]],
                               hidden_in_cell: str, hidden_below: str, *, render_mode: int = 3,
                               rotation: int = 0) -> Path:
    """A text page with a ruled table; one INVISIBLE word (``render_mode`` 3, or 7: clip only) sits
    inside the last column of the second row, and an invisible line sits below the table. The page is
    shown rotated by ``rotation`` degrees (a landscape table)."""
    doc = pymupdf.open()
    page = text_page(doc, intro)
    x0, y0, col_w, row_h = MARGIN, 300, 150, 30
    for r in range(len(rows) + 1):
        page.draw_line((x0, y0 + r * row_h), (x0 + len(rows[0]) * col_w, y0 + r * row_h))
    for c in range(len(rows[0]) + 1):
        page.draw_line((x0 + c * col_w, y0), (x0 + c * col_w, y0 + len(rows) * row_h))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            page.insert_text((x0 + c * col_w + 5, y0 + r * row_h + 20), value, fontsize=10)
    page.insert_text((x0 + (len(rows[0]) - 1) * col_w + 70, y0 + row_h + 20), hidden_in_cell, fontsize=5,
                     render_mode=render_mode)
    insert_lines(page, [hidden_below], top=y0 + len(rows) * row_h + 60, fontsize=10, render_mode=render_mode)
    page.set_rotation(rotation)
    return _save(doc, path)


def letterhead_pdf(path: Path, lines: Sequence[str], hidden: str) -> Path:
    """A short letter on a full-page letterhead picture (light, blank where the text goes), plus one
    INVISIBLE line over a blank part of the letterhead."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_image(page.rect, stream=picture_png(["ACME PUMPS"], (1240, 1754), font_px=60, background="ivory"))
    insert_lines(page, lines, top=250, fontsize=12)
    insert_lines(page, [hidden], top=600, fontsize=11, render_mode=3)
    return _save(doc, path)


def receipt_with_ocr_layer_pdf(path: Path, report: Sequence[str], receipt: Sequence[str]) -> Path:
    """A born-digital report page carrying a scanned receipt (a picture of ``receipt`` over about a
    quarter of the page) with an INVISIBLE OCR layer of the receipt's lines placed over them, as
    ``ocrmypdf --redo-ocr`` leaves it."""
    doc = pymupdf.open()
    page = text_page(doc, report)
    size, font_px = (1200, 600), 56
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=font_px)
    boxes, y = [], font_px // 2
    for line in receipt:
        box = draw.textbbox((font_px // 2, y), line, font=font)
        draw.text((font_px // 2, y), line, fill="black", font=font)
        boxes.append(box)
        y = box[3] + font_px // 3
    frame = pymupdf.Rect(MARGIN, 450, MARGIN + 400, 650)
    page.insert_image(frame, stream=_png(image), keep_proportion=False)
    scale = frame.width / size[0], frame.height / size[1]
    for line, (left, top, right, bottom) in zip(receipt, boxes):
        height = (bottom - top) * scale[1]
        page.insert_text((frame.x0 + left * scale[0], frame.y0 + bottom * scale[1]), line,
                         fontsize=height * 1.1, render_mode=3)
    return _save(doc, path)


def report_with_exhibit_pdf(path: Path, report: Sequence[str], scan_text: str, heading: str) -> Path:
    """A text page, then a page showing a scan as an inline image over its top 60 % with only a short
    real ``heading`` below it."""
    doc = pymupdf.open()
    text_page(doc, report)
    inline_scan_page(doc, scan_text, height_share=0.6, heading=heading)
    return _save(doc, path)


def report_with_outlined_notice_pdf(path: Path, report: Sequence[str], notice: Sequence[str]) -> Path:
    """A text page, then a page whose only content is ``notice`` as small grey vector OUTLINES (12 pt)."""
    doc = pymupdf.open()
    text_page(doc, report)
    vector_page(doc, notice, fontsize=12, color=(0.45, 0.45, 0.45))
    return _save(doc, path)


def off_layer_and_hidden_text_pdf(path: Path, visible: Sequence[str], off_layer: str, hidden: str) -> Path:
    """A text page with one line in an optional-content layer that is OFF by default (not shown) and one
    INVISIBLE line."""
    doc = pymupdf.open()
    page = text_page(doc, visible)
    layer = doc.add_ocg("Draft notes", on=False)
    page.insert_text((MARGIN, 500), off_layer, fontsize=11, oc=layer)
    insert_lines(page, [hidden], top=600, fontsize=11, render_mode=3)
    return _save(doc, path)


def report_with_covered_text_scan_pdf(path: Path, report: Sequence[str], scan_text: str, covered: str) -> Path:
    """A text page, then a scanned page (one full-page picture, no text layer) under which a line of real
    text was painted first, so the picture hides it."""
    doc = pymupdf.open()
    text_page(doc, report)
    page = doc.new_page(width=A4[0], height=A4[1])
    insert_lines(page, [covered], top=A4[1] / 2, fontsize=11)
    page.insert_image(page.rect, stream=pdfgen.text_png(scan_text))
    return _save(doc, path)


# ------------------------------------------------------------ routing review, round 1


def layered_scan_pdf(path: Path, lines: Sequence[str], *, ink: int = 0, paper: int = 255,
                     watermark: Optional[str] = None) -> Path:
    """Searchable scan whose OCR layer sits exactly on the scanned lines (reviewer fixtures ``p1c_diag``,
    ``p1b_*``, ``p2``): one A4 page that is a full-page greyscale picture (150 DPI) of ``lines`` in grey
    ``ink`` on grey ``paper``, with an INVISIBLE (render mode 3) text line over each picture line.

    ``watermark`` paints real text over the scan, as document-management and scanner software stamp it:
    ``"diagonal"`` a big translucent grey "CONFIDENTIAL DRAFT" at 45 degrees across the page, ``"stamp"``
    a horizontal light-grey "CONFIDENTIAL" across the middle lines.
    """
    size, font_px = (1240, 1754), 28
    image = Image.new("L", size, paper)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=font_px)
    boxes, y = [], 120
    for line in lines:
        box = draw.textbbox((100, y), line, font=font)
        if box[2] > size[0] or box[3] > size[1]:
            raise ValueError(f"line does not fit the scan: {line!r}")
        draw.text((100, y), line, fill=ink, font=font)
        boxes.append(box)
        y = box[3] + font_px
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_image(page.rect, stream=_png(image))
    scale = A4[0] / size[0], A4[1] / size[1]
    for line, (left, top, _, bottom) in zip(lines, boxes):
        page.insert_text((left * scale[0], bottom * scale[1]), line, fontsize=(bottom - top) * scale[1] * 1.1,
                         render_mode=3)
    if watermark == "diagonal":
        origin = pymupdf.Point(90, 700)
        page.insert_text(origin, "CONFIDENTIAL DRAFT", fontsize=64, color=(0.8, 0.8, 0.8), fill_opacity=0.3,
                         morph=(origin, pymupdf.Matrix(45)))
    elif watermark == "stamp":
        page.insert_text((40, A4[1] / 2), "CONFIDENTIAL", fontsize=72, color=(0.85, 0.85, 0.85))
    elif watermark is not None:
        raise ValueError(f"unknown watermark {watermark!r}")
    return _save(doc, path)


def outlined_heading_report_pdf(path: Path, heading: str, body: Sequence[str], figure: Sequence[str]) -> Path:
    """A report page whose heading is drawn as glyph OUTLINES (vector paths) under an INVISIBLE live copy of
    the heading, as design tools export outlined titles, followed by a real text body and a figure (a
    picture of ``figure`` lines) that the text route OCRs (reviewer fixture ``p3_outlined``)."""
    source = pymupdf.open()
    outlined = source.new_page(width=A4[0], height=A4[1])
    insert_lines(outlined, [heading], top=MARGIN + 26, fontsize=26)
    svg = outlined.get_svg_image(text_as_path=True)
    source.close()
    vector = pymupdf.open("pdf", pymupdf.open(stream=svg.encode(), filetype="svg").convert_to_pdf())
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.show_pdf_page(page.rect, vector, 0)
    vector.close()
    insert_lines(page, [heading], top=MARGIN + 26, fontsize=26, render_mode=3)
    y = insert_lines(page, body, top=MARGIN + 80, fontsize=11)
    page.insert_image(pymupdf.Rect(MARGIN, y + 20, MARGIN + 400, y + 220),
                      stream=picture_png(figure, (1200, 600), font_px=64))
    return _save(doc, path)


def ruled_table_with_hidden_words_pdf(path: Path, intro: Sequence[str], rows: Sequence[Sequence[str]],
                                      hidden: Sequence[str]) -> Path:
    """A text page with a ruled table and four INVISIBLE words (reviewer fixture ``p8``): ``hidden[0]``
    straddles an inner vertical rule of the table, ``hidden[1]`` an inner horizontal rule, ``hidden[2]``
    overlaps the second intro line, and ``hidden[3]`` lies on a plain navy picture below the table."""
    doc = pymupdf.open()
    page = text_page(doc, intro)
    x0, y0, col_w, row_h = MARGIN, 300, 150, 30
    for r in range(len(rows) + 1):
        page.draw_line((x0, y0 + r * row_h), (x0 + len(rows[0]) * col_w, y0 + r * row_h))
    for c in range(len(rows[0]) + 1):
        page.draw_line((x0 + c * col_w, y0), (x0 + c * col_w, y0 + len(rows) * row_h))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            page.insert_text((x0 + c * col_w + 5, y0 + r * row_h + 20), value, fontsize=10)
    straddle = pymupdf.get_text_length(hidden[0], fontname="helv", fontsize=10) / 2
    page.insert_text((x0 + col_w - straddle, y0 + 2 * row_h - 8), hidden[0], fontsize=10, render_mode=3)
    page.insert_text((x0 + 2 * col_w + 30, y0 + row_h + 4), hidden[1], fontsize=10, render_mode=3)
    page.insert_text((MARGIN + 80, MARGIN + 11 + 16.5), hidden[2], fontsize=11, render_mode=3)
    picture = pymupdf.Rect(MARGIN, y0 + len(rows) * row_h + 60, MARGIN + 200, y0 + len(rows) * row_h + 160)
    page.insert_image(picture, stream=solid_png((200, 100), "navy"))
    page.insert_text((picture.x0 + 10, picture.y0 + 55), hidden[3], fontsize=10, render_mode=3, color=(1, 1, 1))
    return _save(doc, path)


def ruled_contract_pdf(path: Path, pages: Sequence[Sequence[str]]) -> Path:
    """A contract whose every page has a grey header rule, a grey footer rule and a page number; an empty
    ``pages[i]`` gives a page that only says "This page intentionally left blank" (reviewer fixture ``p9``)."""
    doc = pymupdf.open()
    for number, lines in enumerate(pages, 1):
        page = doc.new_page(width=A4[0], height=A4[1])
        page.draw_line((MARGIN, 50), (A4[0] - MARGIN, 50), color=(0.5, 0.5, 0.5), width=0.75)
        page.draw_line((MARGIN, A4[1] - 42), (A4[0] - MARGIN, A4[1] - 42), color=(0.5, 0.5, 0.5), width=0.75)
        page.insert_text((A4[0] / 2, A4[1] - 27), str(number), fontsize=9)
        if lines:
            insert_lines(page, lines, top=MARGIN + 10, fontsize=10)
        else:
            page.insert_text((200, A4[1] / 2), "This page intentionally left blank", fontsize=10)
    return _save(doc, path)


def epigraph_pdf(path: Path, line: str) -> Path:
    """A page holding one short line of French in an embedded Unicode font (Nimbus Roman), so accents,
    ellipsis and curly quotes extract exactly as written."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_font(fontname="F1", fontbuffer=pymupdf.Font("tiro").buffer)
    page.insert_text((MARGIN * 2, A4[1] / 3), line, fontname="F1", fontsize=14)
    return _save(doc, path)


def rating_cards_pdf(path: Path, cards: Sequence[Tuple[str, str]]) -> Path:
    """A product page: per ``(name, rating)`` card, the name, a row of five rating stars drawn with a font
    whose text layer maps them to private-use code points (as icon fonts do) and the rating line."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    add_broken_font(page)
    y = MARGIN + 18
    for name, rating in cards:
        y = insert_lines(page, [name], top=y, fontsize=18)
        y = insert_lines(page, ["*****"], top=y, fontsize=14, fontname=BROKEN_FONT)
        y = insert_lines(page, [rating], top=y, fontsize=11) + 20
    garble(doc, "pua")
    return _save(doc, path)


def garbled_title_grey_body_pdf(path: Path, title: str, body: Sequence[str]) -> Path:
    """A page whose big black title extracts as U+FFFD and whose body is real text in light grey
    (0.72), legible on the page but lighter than Tesseract's binarisation threshold."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    add_broken_font(page)
    y = insert_lines(page, [title], top=MARGIN + 24, fontsize=24, fontname=BROKEN_FONT)
    insert_lines(page, body, top=y + 12, fontsize=11, color=(0.72, 0.72, 0.72))
    garble(doc, "fffd")
    return _save(doc, path)


def paper_scan_png(lines: Sequence[str], *, font_px: int = 26, top_share: float = 0.25) -> bytes:
    """An A4 page scanned at about 100 dpi: ``lines`` in black from ``top_share`` of the way down, the paper
    above and below them blank."""
    size = (827, 1170)
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=font_px)
    y = round(size[1] * top_share)
    for line in lines:
        right, bottom = draw.textbbox((font_px * 3, y), line, font=font)[2:]
        if right > size[0] or bottom > size[1] - font_px * 4:
            raise ValueError(f"text does not fit the scan at font_px={font_px}: {line!r}")
        draw.text((font_px * 3, y), line, fill="black", font=font)
        y = bottom + font_px
    return _png(image)


def report_with_scanned_page_pdf(path: Path, reports: Sequence[Sequence[str]], scan_lines: Sequence[str], *,
                                 printed: Sequence[str] = (), running_header: str = "",
                                 page_numbers: bool = False, printed_font: str = "helv") -> Path:
    """A text report whose second page is a full-page scan (``paper_scan_png`` of ``scan_lines``) with real text
    printed over the blank paper above the scanned lines: ``printed`` lines, in ``printed_font`` (``china-t`` for
    CJK). ``reports`` are the text pages around it (the first before, the others after). Every page carries
    ``running_header`` at the top and ``Page N of M`` at the bottom when asked, the scan too: printed over its
    blank margins."""
    doc = pymupdf.open()
    pages = [reports[0], None, *reports[1:]]
    for number, lines in enumerate(pages, 1):
        page = doc.new_page(width=A4[0], height=A4[1])
        if lines is None:
            page.insert_image(page.rect, stream=paper_scan_png(scan_lines))
            insert_lines(page, printed, top=110, fontsize=12, fontname=printed_font)
        else:
            insert_lines(page, lines, top=120, fontsize=11)
        if running_header:
            insert_lines(page, [running_header], top=48, fontsize=9)
        if page_numbers:
            insert_lines(page, [f"Page {number} of {len(pages)}"], top=A4[1] - 40, fontsize=9)
    return _save(doc, path)
