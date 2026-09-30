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


def banded_figure_pdf(path: Path, body: Sequence[str], figure: Sequence[str], line: Sequence[str]) -> Path:
    """An A4 text page with a figure of ``figure`` stored as 25 horizontal bands 8 pt tall (as printer drivers
    write images), and ``line`` stored as one image 11 pt tall (a line of text kept as a picture)."""
    doc = pymupdf.open()
    page = text_page(doc, body)
    picture = Image.open(io.BytesIO(_picture(figure, (1500, 667), font_px=90)))
    bands, area = 25, pymupdf.Rect(MARGIN, 300, MARGIN + 450, 500)
    band_px = picture.height / bands
    for band in range(bands):
        box = (0, round(band * band_px), picture.width, round((band + 1) * band_px))
        rect = pymupdf.Rect(area.x0, area.y0 + band * 8, area.x1, area.y0 + (band + 1) * 8)
        page.insert_image(rect, stream=_png(picture.crop(box)), keep_proportion=False)
    strip = Image.new("RGB", (1500, 50), "white")
    ImageDraw.Draw(strip).text((10, 4), " ".join(line), fill="black", font=ImageFont.load_default(size=40))
    page.insert_image(pymupdf.Rect(MARGIN, 560, MARGIN + 330, 571), stream=_png(strip), keep_proportion=False)
    return _save(doc, path)


def screenshot_pdf(path: Path, body: Sequence[str], lines: Sequence[str]) -> Path:
    """An A4 text page with a 3000 x 1500 px PNG screenshot of ``lines`` in small type (26 px), shown 450 pt
    wide: legible at its own resolution, not at a quarter of it."""
    image = Image.new("RGB", (3000, 1500), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=26)
    y = 60
    for line in lines:
        draw.text((60, y), line, fill="black", font=font)
        y += 40
    doc = pymupdf.open()
    page = text_page(doc, body)
    page.insert_image(pymupdf.Rect(MARGIN, 330, MARGIN + 450, 555), stream=_png(image))
    return _save(doc, path)


# ------------------------------------------------------------------- review round 1 (PR #21)


def scan_with_blank_sheet_pdf(path: Path, sheets: Sequence[str], blank_after: int, photo_page: Sequence[str]) -> Path:
    """Scanned sheets of ``sheets`` (one per page, no text layer) with one blank sheet (the back of a duplex scan:
    paper grey, nothing printed) after sheet ``blank_after``, then a text page ``photo_page`` with a photo that
    carries no text."""
    doc = pymupdf.open()
    for index, text in enumerate(sheets):
        page = doc.new_page(width=A4[0], height=A4[1])
        page.insert_image(page.rect, stream=pdfgen.text_png(text, font_size=110))
        if index + 1 == blank_after:
            blank = doc.new_page(width=A4[0], height=A4[1])
            blank.insert_image(blank.rect, stream=_png(Image.new("L", (827, 1170), 246)))
    page = text_page(doc, photo_page)
    rng = random.Random(3)
    photo = Image.new("RGB", (600, 400))
    pixels = photo.load()
    for y in range(400):
        for x in range(600):
            shade = 90 + 50 * ((x // 40 + y // 40) % 2) + rng.randrange(-20, 20)
            pixels[x, y] = (shade, shade + 15, int(shade * 0.8))
    page.insert_image(pymupdf.Rect(MARGIN, 380, MARGIN + 360, 620), stream=_png(photo))
    return _save(doc, path)


def thumbnail_sheet_pdf(path: Path, labels: Sequence[Tuple[str, str]], text_pages: Sequence[Sequence[str]]) -> Path:
    """Page 1 has no text layer: a catalogue sheet of small thumbnails (58 x 84 pt, under 10 % of the page), each a
    photo area over a two-line printed label ``labels[i]``. Then one text page per item of ``text_pages``."""
    doc = pymupdf.open()
    sheet = doc.new_page(width=A4[0], height=A4[1])
    font = ImageFont.load_default(size=48)
    for index, (first, second) in enumerate(labels):
        image = Image.new("RGB", (290, 420), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 280, 270), fill=(80 + 7 * index, 110, 150))
        draw.text((12, 282), first, fill="black", font=font)
        draw.text((12, 348), second, fill="black", font=font)
        x0, y0 = MARGIN + (index % 5) * 70, MARGIN + (index // 5) * 100
        sheet.insert_image(pymupdf.Rect(x0, y0, x0 + 58, y0 + 84), stream=_png(image), keep_proportion=False)
    for lines in text_pages:
        text_page(doc, lines)
    return _save(doc, path)


def clipped_screenshot_pdf(path: Path, slide_lines: Sequence[str], shown: Sequence[str], hidden: str) -> Path:
    """A 960 x 540 pt slide with a text layer and a 1920 x 1080 px screenshot drawn at 1200 x 675 pt from
    (700, 300), cropped by a clip path to its top-left 250 x 230 pt, which shows ``shown``. ``hidden`` is printed
    in the screenshot outside the crop: the slide does not show it."""
    image = Image.new("RGB", (1920, 1080), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=40)
    for index, line in enumerate(shown):
        draw.text((24, 24 + 60 * index), line, fill="black", font=font)
    draw.text((460, 520), hidden, fill="black", font=font)
    draw.rectangle((900, 700, 1800, 1000), fill=(200, 220, 240))
    doc = pymupdf.open()
    page = doc.new_page(width=SLIDE[0], height=SLIDE[1])
    insert_lines(page, slide_lines, top=60, fontsize=20, left=60)
    xref = page.insert_image(pymupdf.Rect(0, 0, 1, 1), stream=_png(image))
    name = [info for info in page.get_images(full=True) if info[0] == xref][0][7]
    contents = page.get_contents()[-1]
    kept = [line for line in doc.xref_stream(contents).split(b"\n")
            if not (f"/{name} Do".encode() in line or line.strip().endswith(b" cm"))]
    height = SLIDE[1]
    draw_image = (f"q 700 {height - 530} 250 230 re W n 1200 0 0 675 700 {height - 975} cm /{name} Do Q\n").encode()
    doc.update_stream(contents, b"\n".join(kept) + b"\n" + draw_image)
    return _save(doc, path)


def repeated_logo_pdf(path: Path, pages: Sequence[Sequence[str]], logo: str, stamp: str, stamp_pages: Sequence[int]) -> Path:
    """One text page per item of ``pages``, each showing the same lettered logo ``logo`` at the same place (top
    right, as letterheads and slide templates do), and a stamp picture ``stamp`` on the 1-based ``stamp_pages``
    only, at a different place on each."""
    font = ImageFont.load_default(size=80)

    def lettered(text: str, size: Tuple[int, int]) -> bytes:
        image = Image.new("RGB", size, "white")
        ImageDraw.Draw(image).text((20, 30), text, fill="black", font=font)
        return _png(image)

    doc = pymupdf.open()
    logo_xref = stamp_xref = 0
    for number, lines in enumerate(pages, 1):
        page = text_page(doc, lines)
        rect = pymupdf.Rect(A4[0] - MARGIN - 180, 24, A4[0] - MARGIN, 60)
        if logo_xref:
            page.insert_image(rect, xref=logo_xref)
        else:
            logo_xref = page.insert_image(rect, stream=lettered(logo, (900, 180)))
        if number in stamp_pages:
            spot = pymupdf.Rect(MARGIN, 500 + 40 * number, MARGIN + 200, 540 + 40 * number)
            if stamp_xref:
                page.insert_image(spot, xref=stamp_xref)
            else:
                stamp_xref = page.insert_image(spot, stream=lettered(stamp, (1000, 200)))
    return _save(doc, path)
