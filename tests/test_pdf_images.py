"""Unit tests for the picture rules of the text route (doc2mark.pipelines.pdf_images), the
streaming OCR pass (PDFLoader._ocr_document) and the OCR cache's final-answer rule.

The E2E suite (tests/e2e/test_images.py) covers these through the CLI with Tesseract; these
tests pin the edge cases of the thresholds that no realistic fixture reaches cheaply.
"""
import io

import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from doc2mark.ocr.base import BaseOCR, OCRResult
from doc2mark.ocr.cache import CachedOCR, MemoryOCRCache
from doc2mark.pipelines import pdf_images
from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _grey(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.int16)


def _text_image(text="INVOICE 4471", size=(400, 120)) -> Image.Image:
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).text((10, 20), text, fill="black", font=ImageFont.load_default(size=48))
    return image


# --- content classes ----------------------------------------------------------------


def test_plain_pictures_carry_nothing_to_read():
    solid = Image.new("RGB", (300, 200), "navy")
    gradient = Image.linear_gradient("L").resize((300, 200))
    frame = Image.new("RGB", (300, 200), "white")
    ImageDraw.Draw(frame).rectangle((5, 5, 294, 194), outline="black", width=3)
    ImageDraw.Draw(frame).line((5, 100, 294, 100), fill="black", width=2)
    for image in (solid, gradient, frame):
        assert pdf_images.picture_content(_grey(image)) == pdf_images.PLAIN


def test_text_and_icons_are_told_apart():
    assert pdf_images.picture_content(_grey(_text_image())) == pdf_images.TEXT
    disc = Image.new("RGB", (120, 120), "white")
    ImageDraw.Draw(disc).ellipse((10, 10, 110, 110), fill="steelblue")
    ring = Image.new("RGB", (120, 120), "white")
    ImageDraw.Draw(ring).ellipse((10, 10, 110, 110), outline="black", width=8)
    assert pdf_images.picture_content(_grey(disc)) == pdf_images.SHAPES
    assert pdf_images.picture_content(_grey(ring)) == pdf_images.SHAPES


def test_small_shapes_are_left_out_but_large_ones_and_any_text_are_kept():
    doc = pymupdf.open()
    page = doc.new_page(width=1440, height=810)
    small = pdf_images.Picture(key=1, rects=[pymupdf.Rect(0, 0, 130, 75)], xref=1)
    large = pdf_images.Picture(key=2, rects=[pymupdf.Rect(0, 0, 400, 300)], xref=2)
    assert not pdf_images.ocr_worthy(page, small, pdf_images.SHAPES)
    assert pdf_images.ocr_worthy(page, small, pdf_images.TEXT)
    assert pdf_images.ocr_worthy(page, large, pdf_images.SHAPES)
    assert not pdf_images.ocr_worthy(page, large, pdf_images.PLAIN)


def test_small_is_judged_in_the_unrotated_frame():
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.set_rotation(90)
    # 80 x 55 pt: under 10 % of the page only when compared with the rotated page (842 x 595).
    assert not pdf_images.is_small(pymupdf.Rect(0, 0, 80, 55), page)


# --- placements and tiles -------------------------------------------------------------


def test_placements_are_listed_once_and_off_page_ones_are_not_shown():
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    xref = page.insert_image(pymupdf.Rect(50, 50, 250, 110), stream=_png(_text_image()))
    page.insert_image(pymupdf.Rect(50, 200, 250, 260), xref=xref)
    page.insert_image(pymupdf.Rect(50, -300, 250, -100), stream=_png(_text_image("OFF")), keep_proportion=False)
    page.insert_image(pymupdf.Rect(50, 836, 250, 1036), stream=_png(_text_image("SLIVER")), keep_proportion=False)
    shown = [placement for placement in pdf_images.placements(page) if placement.shown]
    assert [placement.xref for placement in shown] == [xref, xref]
    pictures, skipped = pdf_images.page_pictures(page)
    assert [(picture.key, len(picture.rects)) for picture in pictures] == [(xref, 2)]
    assert skipped == {"not_shown": 2}


def test_abutting_tiles_are_one_picture_but_spaced_and_stacked_pictures_are_not():
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for row in range(3):
        for col in range(4):
            rect = pymupdf.Rect(100 + col * 50, 100 + row * 40, 150 + col * 50, 140 + row * 40)
            page.insert_image(rect, stream=_png(_text_image(f"T{row}{col}", (100, 80))), keep_proportion=False)
    page.insert_image(pymupdf.Rect(100, 400, 250, 500), stream=_png(_text_image("A")), keep_proportion=False)
    page.insert_image(pymupdf.Rect(270, 400, 420, 500), stream=_png(_text_image("B")), keep_proportion=False)
    page.insert_image(pymupdf.Rect(100, 600, 400, 800), stream=_png(_text_image("BG")), keep_proportion=False)
    page.insert_image(pymupdf.Rect(150, 650, 250, 700), stream=_png(_text_image("LOGO")), keep_proportion=False)
    pictures, _ = pdf_images.page_pictures(page)
    regions = [picture for picture in pictures if not picture.xref]
    assert len(regions) == 1 and regions[0].region == pymupdf.Rect(100, 100, 300, 220)
    assert len([picture for picture in pictures if picture.xref]) == 4


# --- the streaming OCR pass -------------------------------------------------------------


class _CountingOCR(BaseOCR):
    def __init__(self):
        super().__init__(api_key=None)
        self.batches = []

    def batch_process_images(self, images, **kwargs):
        self.batches.append(len(images))
        return [OCRResult(text=f"text of {len(image)} bytes") for image in images]

    def validate_api_key(self):
        return True


def _scan(tmp_path, pages: int):
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=_png(_text_image(f"SHEET {number}", (300, 400))))
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    return path


def test_renders_are_ocrd_in_bounded_batches_and_mapped_back_in_order(tmp_path):
    ocr = _CountingOCR()
    loader = PDFLoader(_scan(tmp_path, 70), ocr=ocr)
    result = loader.convert_to_json(extract_images=True, ocr_images=True, show_progress=False)
    assert ocr.batches == [32, 32, 6]
    stats = result["ocr_images"]
    assert (stats["ocr_requests"], stats["page_renders"], stats["batches"], stats["largest_batch"]) == (70, 70, 3, 32)
    assert [item["page"] for item in result["content"]] == list(range(1, 71))


def test_same_pixels_under_two_xrefs_are_one_request(tmp_path):
    picture = _png(_text_image("LABEL 5521"))
    docs = []
    for _ in range(2):   # two documents merged: the same pixels end up under two xrefs
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(pymupdf.Rect(72, 72, 523, 280), "A text page with enough words to route as text. " * 8,
                            fontsize=9)
        page.insert_image(pymupdf.Rect(72, 300, 372, 390), stream=picture)
        docs.append(doc)
    docs[0].insert_pdf(docs[1])
    path = tmp_path / "twice.pdf"
    docs[0].save(str(path))
    source = pymupdf.open(str(path))
    xrefs = {info[0] for page in source for info in page.get_images(full=True)}
    assert len(xrefs) == 2
    ocr = _CountingOCR()
    result = PDFLoader(path, ocr=ocr).convert_to_json(extract_images=True, ocr_images=True, show_progress=False)
    assert result["ocr_images"]["ocr_requests"] == 1, (xrefs, result["ocr_images"])
    assert [item["page"] for item in result["content"] if item["type"] == "text:image_description"] == [1, 2]


# --- the OCR cache keeps final answers only --------------------------------------------------


class _ScriptedOCR(BaseOCR):
    def __init__(self, answers):
        super().__init__(api_key=None)
        self.answers = list(answers)
        self.calls = 0

    def batch_process_images(self, images, **kwargs):
        self.calls += 1
        return [self.answers.pop(0) for _ in images]

    def validate_api_key(self):
        return True


@pytest.mark.parametrize("first", [OCRResult(text=""), OCRResult(text="   "),
                                   OCRResult(text="partial", metadata={"failed": True})])
def test_empty_or_failed_results_are_not_cached(first):
    provider = _ScriptedOCR([first, OCRResult(text="INVOICE 8812")])
    cached = CachedOCR(provider, MemoryOCRCache())
    assert cached.batch_process_images([b"image"])[0].text == first.text
    assert cached.batch_process_images([b"image"])[0].text == "INVOICE 8812"
    assert cached.batch_process_images([b"image"])[0].text == "INVOICE 8812"
    assert provider.calls == 2
