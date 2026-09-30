"""PDF builders for the image-collection E2E tests (``tests/e2e/test_images.py``).

Ports of the routing review's image probes (``p11b`` same xref placed three times,
``p22`` backgrounds and a small chart, ``p24`` an image listed through a Form XObject,
``p29`` tiles, ``p15`` a long scan, ``p21`` a rotated page), made at test time with
PyMuPDF and Pillow only. Pictures carry their text as pixels (Pillow's built-in font),
so only OCR can read them. Nothing from ``doc2mark`` is imported.
"""

import io
import random
from pathlib import Path
from typing import Sequence, Tuple

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from tests.e2e import pdfgen
from tests.e2e.builders_route import A4, MARGIN, SLIDE, _png, _save, insert_lines, picture_png, text_page


def _picture(lines: Sequence[str], size: Tuple[int, int], font_px: int, background="white") -> bytes:
    return picture_png(lines, size, font_px=font_px, background=background)


def repeated_picture_pdf(path: Path, body: Sequence[str], label: Sequence[str], form_label: Sequence[str]) -> Path:
    """Page 1: a text page showing ONE image XObject (a picture of ``label``) at three places.
    Page 2: a text page showing a picture of ``form_label`` once, drawn through a Form XObject while the
    page's own resources list the same image too, so ``get_images`` lists it twice (as the real deck's p4)."""
    doc = pymupdf.open()
    first = text_page(doc, body)
    picture = _picture(label, (900, 220), font_px=80)
    xref = first.insert_image(pymupdf.Rect(MARGIN, 300, MARGIN + 300, 374), stream=picture)
    for top in (450, 600):
        first.insert_image(pymupdf.Rect(MARGIN, top, MARGIN + 300, top + 74), xref=xref)

    second = text_page(doc, body)
    source = pymupdf.open()
    holder = source.new_page(width=300, height=74)
    holder.insert_image(holder.rect, stream=_picture(form_label, (900, 220), font_px=80))
    second.show_pdf_page(pymupdf.Rect(MARGIN, 400, MARGIN + 300, 474), source, 0)
    source.close()
    image = [info[0] for info in second.get_images(full=True)][0]
    resources = doc.xref_get_key(second.xref, "Resources")
    if resources[0] == "xref":
        target = int(resources[1].split()[0])
        doc.xref_set_key(target, "XObject/ImDup", f"{image} 0 R")
    else:
        doc.xref_set_key(second.xref, "Resources/XObject/ImDup", f"{image} 0 R")
    return _save(doc, path)


def small_chart_slide_pdf(path: Path, body: Sequence[str], chart: Sequence[str]) -> Path:
    """A 1440 x 810 pt slide with a text layer and a 130 x 75 pt chart picture with printed numbers (under
    10 % of the slide in both directions: the old "decorative" size)."""
    doc = pymupdf.open()
    page = doc.new_page(width=1440, height=810)
    insert_lines(page, body, top=80, fontsize=20, left=60)
    page.insert_image(pymupdf.Rect(900, 600, 1030, 675), stream=_picture(chart, (390, 225), font_px=40))
    return _save(doc, path)


def tiled_figure_pdf(path: Path, body: Sequence[str], figure: Sequence[str], grid: Tuple[int, int] = (8, 6)) -> Path:
    """An A4 text page with a figure made of ``grid`` (columns, rows) separate image tiles, each smaller
    than 10 % of the page in both directions, that together show one picture of ``figure``."""
    doc = pymupdf.open()
    page = text_page(doc, body)
    picture = Image.open(io.BytesIO(_picture(figure, (1500, 1000), font_px=90)))
    columns, rows = grid
    area = pymupdf.Rect(MARGIN, 330, MARGIN + 440, 330 + 293)
    tile_w, tile_h = picture.width / columns, picture.height / rows
    for row in range(rows):
        for col in range(columns):
            box = (round(col * tile_w), round(row * tile_h), round((col + 1) * tile_w), round((row + 1) * tile_h))
            rect = pymupdf.Rect(area.x0 + col * area.width / columns, area.y0 + row * area.height / rows,
                                area.x0 + (col + 1) * area.width / columns, area.y0 + (row + 1) * area.height / rows)
            page.insert_image(rect, stream=_png(picture.crop(box)), keep_proportion=False)
    return _save(doc, path)


def rotated_page_picture_pdf(path: Path, body: Sequence[str], picture: Sequence[str]) -> Path:
    """An A4 text page shown rotated by 90 degrees with an 80 x 55 pt picture of ``picture``: under 10 % of
    the page only when its unrotated size is compared with the rotated page."""
    doc = pymupdf.open()
    page = text_page(doc, body)
    page.insert_image(pymupdf.Rect(MARGIN, 400, MARGIN + 80, 455), stream=_picture(picture, (480, 330), font_px=64),
                      keep_proportion=False)
    page.set_rotation(90)
    return _save(doc, path)


def gradient_png(size: Tuple[int, int], top=(236, 242, 250), bottom=(200, 214, 235)) -> bytes:
    """A smooth vertical colour gradient (a themed slide background)."""
    width, height = size
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        share = y / max(1, height - 1)
        draw.line([(0, y), (width, y)], fill=tuple(round(a + (b - a) * share) for a, b in zip(top, bottom)))
    return _png(image)


def icon_png(size: Tuple[int, int] = (120, 120), color="steelblue") -> bytes:
    """A small icon: one filled disc, no text."""
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).ellipse((10, 10, size[0] - 10, size[1] - 10), fill=color)
    return _png(image)


def themed_deck_pdf(path: Path, slides: Sequence[Sequence[str]], *, logo: Sequence[str] = ()) -> Path:
    """A slide deck with a live text layer on every slide, a full-bleed background image shared by all slides
    (one image XObject) and a small icon beside the title. Without ``logo`` the background is a plain
    gradient; with it, the background also shows ``logo`` in its top-right corner."""
    doc = pymupdf.open()
    if logo:
        image = Image.open(io.BytesIO(gradient_png((1600, 900))))
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=56)
        y = 40
        for line in logo:
            draw.text((1000, y), line, fill="black", font=font)
            y += 72
        background = _png(image)
    else:
        background = gradient_png((1600, 900))
    icon = icon_png()
    background_xref = icon_xref = 0
    for lines in slides:
        page = doc.new_page(width=SLIDE[0], height=SLIDE[1])
        if background_xref:
            page.insert_image(page.rect, xref=background_xref)
            page.insert_image(pymupdf.Rect(40, 40, 80, 80), xref=icon_xref)
        else:
            background_xref = page.insert_image(page.rect, stream=background)
            icon_xref = page.insert_image(pymupdf.Rect(40, 40, 80, 80), stream=icon)
        insert_lines(page, lines, top=64, fontsize=20, left=100, leading=1.6)
    return _save(doc, path)


def offpage_pictures_pdf(path: Path, body: Sequence[str], offpage: Sequence[str], sliver: Sequence[str]) -> Path:
    """An A4 text page with two pictures it does not show: one placed entirely above the page, one placed so
    that only a 6 pt strip of it reaches onto the page's bottom edge."""
    doc = pymupdf.open()
    page = text_page(doc, body)
    page.insert_image(pymupdf.Rect(MARGIN, -300, MARGIN + 400, -100), stream=_picture(offpage, (1200, 600), font_px=90),
                      keep_proportion=False)
    page.insert_image(pymupdf.Rect(MARGIN, A4[1] - 6, MARGIN + 400, A4[1] + 194),
                      stream=_picture(sliver, (1200, 600), font_px=90), keep_proportion=False)
    return _save(doc, path)


def long_scan_pdf(path: Path, pages: int) -> Path:
    """A scanned document of ``pages`` A4 pages; page ``n`` shows ``SHEET <n>`` in large letters."""
    doc = pymupdf.open()
    for number in range(1, pages + 1):
        page = doc.new_page(width=A4[0], height=A4[1])
        page.insert_image(page.rect, stream=pdfgen.text_png(f"SHEET {number}", font_size=160))
    return _save(doc, path)


def noisy_scan_pdf(path: Path, pages: int, *, seed: int = 7) -> Path:
    """``pages`` A4 pages that each show the same full-page grainy picture (one image XObject, so the file
    stays small) under a small printed page label, so every page renders to a large, distinct PNG."""
    rng = random.Random(seed)
    width, height = 620, 877
    noise = Image.frombytes("L", (width, height), bytes(rng.randrange(256) for _ in range(width * height)))
    picture = _png(noise.convert("RGB"))
    doc = pymupdf.open()
    xref = 0
    for number in range(1, pages + 1):
        page = doc.new_page(width=A4[0], height=A4[1])
        if xref:
            page.insert_image(page.rect, xref=xref)
        else:
            xref = page.insert_image(page.rect, stream=picture)
        page.insert_text((MARGIN, A4[1] - 20), f"sheet {number}", fontsize=8)
    return _save(doc, path)


def transparent_picture_pdf(path: Path, body: Sequence[str], lines: Sequence[str]) -> Path:
    """An A4 text page with a transparent PNG picture of ``lines`` (black letters on a fully transparent
    background, as charts and logos are often exported): in the PDF the letters' shape is only in the
    image's soft mask, and its colour channels are black everywhere."""
    image = Image.new("RGBA", (1200, 300), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=90)
    y = 40
    for line in lines:
        draw.text((40, y), line, fill=(0, 0, 0, 255), font=font)
        y += 120
    doc = pymupdf.open()
    page = text_page(doc, body)
    page.insert_image(pymupdf.Rect(MARGIN, 400, MARGIN + 400, 500), stream=_png(image))
    return _save(doc, path)
