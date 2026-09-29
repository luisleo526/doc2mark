"""PDF builders for the OCR-routing E2E tests (``tests/e2e/test_route.py``).

Ports of the routing review's probe fixtures (``p11``, ``p11b``, ``p12``, ``p12b``,
``p13``), made at test time with PyMuPDF and Pillow only: text uses PyMuPDF's
built-in fonts (Helvetica, the Nimbus Roman clone ``tiro``, Droid Sans Fallback as
``china-t``), so no system font is needed. Nothing from ``doc2mark`` is imported.

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
                 left: float = MARGIN, leading: float = 1.5, render_mode: int = 0) -> float:
    """Write one text line per item starting at ``top``; return the y below the last line.

    A line wider than the page raises ``ValueError`` instead of being clipped silently.
    """
    y = top
    for line in lines:
        if fontname != BROKEN_FONT and pymupdf.get_text_length(line, fontname=fontname, fontsize=fontsize) > (
                page.rect.width - left - MARGIN / 2):
            raise ValueError(f"line is wider than the page at fontsize={fontsize}: {line!r}")
        page.insert_text((left, y), line, fontname=fontname, fontsize=fontsize, render_mode=render_mode)
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


GARBLERS: Dict[str, Optional[Callable[[str], str]]] = {
    "fffd": None,  # no ToUnicode at all: every glyph extracts as U+FFFD
    "pua": lambda ch: ch if ch.isspace() else chr(0xE000 + ord(ch) % 200),
    "mojibake": _mojibake,
    "shifted": _shift,
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


def inline_scan_page(doc, text: str):
    """A4 page whose only content is an INLINE image (BI/ID/EI) of ``text``: no image XObject, no text layer."""
    page = doc.new_page(width=A4[0], height=A4[1])
    gray = Image.open(io.BytesIO(pdfgen.text_png(text))).convert("L")
    data = binascii.hexlify(zlib.compress(gray.tobytes(), 9)).decode()
    width, height = gray.size
    content = (f"q {A4[0]} 0 0 {A4[1]} 0 0 cm\n"
               f"BI /W {width} /H {height} /BPC 8 /CS /G /F [/AHx /Fl] ID\n{data}>\nEI Q\n")
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content.encode())
    doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
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


def vector_page(doc, lines: Sequence[str], *, fontsize: float = 36):
    """A4 page with ``lines`` drawn as glyph OUTLINES (vector paths): no text layer, no raster image."""
    source = pymupdf.open()
    page = source.new_page(width=A4[0], height=A4[1])
    insert_lines(page, lines, top=MARGIN + fontsize, fontsize=fontsize)
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
