"""Tiny PDF builders for E2E tests, made at test time with PyMuPDF and Pillow.

Nothing from ``doc2mark`` is imported here. Every builder saves to ``path`` and
returns it as a ``Path``. ``pages`` is one string (one page) or a list of strings
(one page each); a ``\\n`` in a string starts a new line, there is no wrapping.

Lane-specific builders belong in your own module (for example
``tests/e2e/pdfgen_<lane>.py``), not here, so parallel lanes do not conflict.
"""

import io
from pathlib import Path
from typing import List, Optional, Sequence, Union

import pymupdf
from PIL import Image, ImageDraw, ImageFont

Pages = Union[str, Sequence[str]]

A4_POINTS = (595, 842)
A4_PIXELS_200DPI = (1654, 2339)
MARGIN_PIXELS = 100

# CJK font for ``font_path`` (installed by ``fonts-noto-cjk`` in tests/e2e/Dockerfile).
NOTO_CJK_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def _as_pages(pages: Pages) -> List[str]:
    return [pages] if isinstance(pages, str) else list(pages)


def text_pdf(path: Path, pages: Pages, *, cjk: bool = False, fontsize: int = 14) -> Path:
    """PDF with a real text layer (extractable without OCR), one A4 page per item.

    ``cjk=True`` writes the text with PyMuPDF's built-in Traditional Chinese font
    (``china-t``); the default is Helvetica.
    """
    fontname = "china-t" if cjk else "helv"
    doc = pymupdf.open()
    for text in _as_pages(pages):
        page = doc.new_page(width=A4_POINTS[0], height=A4_POINTS[1])
        page.insert_text((72, 72), text, fontname=fontname, fontsize=fontsize)
    doc.save(str(path))
    doc.close()
    return Path(path)


def text_png(text: str, *, font_size: int = 110, font_path: Optional[str] = None) -> bytes:
    """PNG (A4 at 200 dpi, black on white) with ``text`` drawn large enough for Tesseract to read reliably.

    The default font is Pillow's built-in Latin one; pass ``font_path`` for other scripts.
    """
    width, height = A4_PIXELS_200DPI
    font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default(size=font_size)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    y = MARGIN_PIXELS
    for line in text.split("\n"):
        right, bottom = draw.textbbox((MARGIN_PIXELS, y), line, font=font)[2:]
        if right > width - MARGIN_PIXELS or bottom > height - MARGIN_PIXELS:
            raise ValueError(f"text does not fit the page at font_size={font_size}: {line!r}")
        draw.text((MARGIN_PIXELS, y), line, fill="black", font=font)
        y = bottom + font_size // 2
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", dpi=(200, 200))
    return buffer.getvalue()


def image_pdf(path: Path, pages: Pages, *, font_size: int = 110, font_path: Optional[str] = None) -> Path:
    """PDF whose pages are each one full-page picture of the text and have no text layer, so only OCR can read them."""
    doc = pymupdf.open()
    for text in _as_pages(pages):
        page = doc.new_page(width=A4_POINTS[0], height=A4_POINTS[1])
        page.insert_image(page.rect, stream=text_png(text, font_size=font_size, font_path=font_path))
    doc.save(str(path))
    doc.close()
    return Path(path)
