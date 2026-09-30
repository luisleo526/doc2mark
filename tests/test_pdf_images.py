"""Unit tests for the picture rules of the text route (doc2mark.pipelines.pdf_images), the
streaming OCR pass (PDFLoader._ocr_document) and the OCR cache's final-answer rule.

The E2E suite (tests/e2e/test_images.py) covers these through the CLI with Tesseract; these
tests pin the edge cases of the thresholds that no realistic fixture reaches cheaply.
"""
import io
import logging

import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from doc2mark.ocr.base import BaseOCR, OCRResult
from doc2mark.ocr.cache import CachedOCR, MemoryOCRCache
from doc2mark.pipelines import pdf_images, pymupdf_compat
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
    assert len(pdf_images.placements(page)) == 4
    pictures, skipped = pdf_images.page_pictures(page)
    assert [(picture.key, len(picture.rects)) for picture in pictures] == [(xref, 2)]
    assert skipped == {"not_shown": 2, "no_content": 0}


def test_thin_bands_and_thin_lines_are_pictures():
    """Printer drivers store a figure as bands a few points tall, and a line of text stored as an image can be
    10 pt tall: neither is a sliver. Bands are joined first; the whole figure shows."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for band in range(25):
        rect = pymupdf.Rect(72, 300 + band * 8, 522, 308 + band * 8)
        page.insert_image(rect, stream=_png(_text_image(f"B{band}", (1500, 27))), keep_proportion=False)
    line = page.insert_image(pymupdf.Rect(72, 600, 402, 610), stream=_png(_text_image("LINE", (1375, 42))),
                             keep_proportion=False)
    pictures, skipped = pdf_images.page_pictures(page)
    assert [picture.region for picture in pictures if not picture.xref] == [pymupdf.Rect(72, 300, 522, 500)]
    assert [picture.key for picture in pictures if picture.xref] == [line]
    assert skipped == {"not_shown": 0, "no_content": 0}


def test_a_poster_tile_cropped_from_a_large_picture_shows():
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(0, 0, 4 * 595, 4 * 842), stream=_png(_text_image("POSTER", (800, 600))),
                      keep_proportion=False)   # 1/16 of it (6 %) on the page, filling the page
    pictures, _ = pdf_images.page_pictures(page)
    assert [picture.rects for picture in pictures] == [[pymupdf.Rect(0, 0, 595, 842)]]


def test_the_content_check_leaves_the_image_intact():
    """The check samples a copy: the image MuPDF caches (and extraction reads) keeps its full size."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    xref = page.insert_image(pymupdf.Rect(72, 72, 522, 297), stream=_png(_text_image("WIDE", (3000, 1500))))
    assert max(pdf_images.image_grey(doc, xref).shape) <= pdf_images.CLASSIFY_SIDE
    assert (doc.extract_image(xref)["width"], pymupdf.Pixmap(doc, xref).width) == (3000, 3000)


def test_stencil_masks_are_judged_by_their_ink():
    """A stencil mask (/ImageMask) decodes to alpha only (coverage): ink where opaque, paper elsewhere."""
    import zlib
    doc = pymupdf.open()
    doc.new_page()
    xref = doc.get_new_xref()
    doc.update_object(xref, "<</Type/XObject/Subtype/Image/Width 64/Height 64/ImageMask true"
                            "/BitsPerComponent 1/Filter/FlateDecode>>")
    doc.update_stream(xref, zlib.compress(bytes([0x00] * 4 + [0xFF] * 4) * 64), compress=False)
    coverage = np.frombuffer(pymupdf.Pixmap(doc, xref).samples, dtype=np.uint8).reshape(64, 64)
    assert (pdf_images.image_grey(doc, xref) == 255 - coverage.astype(np.int16)).all()


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


# --- the OCR cache keeps answers, retries failures --------------------------------------------------


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


@pytest.mark.parametrize("answer", [
    OCRResult(text=""),
    OCRResult(text="   "),
    OCRResult(text="", metadata={"ocr_refusal": True, "non_content": "pattern"}),
], ids=["empty", "blank", "no-readable-text"])
def test_answers_without_text_are_cached(answer):
    """Review round 1 (B1): a picture with nothing to read gets an answer with no text, every time; replaying it
    saves the provider call (and, for the LLM providers, the free-form recovery call behind it)."""
    provider = _ScriptedOCR([answer, OCRResult(text="never asked")])
    cached = CachedOCR(provider, MemoryOCRCache())
    assert cached.batch_process_images([b"image"])[0].text == answer.text
    assert cached.batch_process_images([b"image"])[0].text == answer.text
    assert provider.calls == 1


@pytest.mark.parametrize("failure", [
    OCRResult(text="", metadata={"failed": True, "error": "timeout"}),
    OCRResult(text="partial", metadata={"failed": True}),
], ids=["failed-empty", "failed-partial"])
def test_failed_answers_are_retried(failure):
    provider = _ScriptedOCR([failure, OCRResult(text="INVOICE 8812")])
    cached = CachedOCR(provider, MemoryOCRCache())
    assert cached.batch_process_images([b"image"])[0].text == failure.text
    assert cached.batch_process_images([b"image"])[0].text == "INVOICE 8812"
    assert cached.batch_process_images([b"image"])[0].text == "INVOICE 8812"
    assert provider.calls == 2


def test_a_failed_entry_already_in_the_cache_is_not_replayed():
    """A failed answer cached by an older doc2mark (or another writer) is a miss, not an answer."""
    from doc2mark.ocr.cache import build_ocr_cache_key

    provider = _ScriptedOCR([OCRResult(text="INVOICE 8812")])
    cache = MemoryOCRCache()
    cached = CachedOCR(provider, cache)
    key = build_ocr_cache_key(provider, b"image", kwargs={})
    cache.set(key, OCRResult(text="", metadata={"failed": True}))
    assert cached.batch_process_images([b"image"])[0].text == "INVOICE 8812"
    assert provider.calls == 1
    assert cache.get(key).text == "INVOICE 8812"   # the same entry, now holding the answer


# --- review round 1 (PR #21): clip paths, routing, failures, textless pages, repeated pictures ------------------


def _clipped_page(doc, image_rect, clip):
    """A page drawing one picture at ``image_rect`` through the clip rectangle ``clip`` (page coordinates)."""
    page = doc.new_page(width=960, height=540)
    xref = page.insert_image(pymupdf.Rect(0, 0, 1, 1), stream=_png(_text_image("Revenue 48,210", (1920, 1080))))
    name = [info for info in page.get_images(full=True) if info[0] == xref][0][7]
    contents = page.get_contents()[-1]
    kept = [line for line in doc.xref_stream(contents).split(b"\n")
            if not (f"/{name} Do".encode() in line or line.strip().endswith(b" cm"))]
    x0, y0, x1, y1 = image_rect
    c0, d0, c1, d1 = clip
    draw = f"q {c0} {540 - d1} {c1 - c0} {d1 - d0} re W n {x1 - x0} 0 0 {y1 - y0} {x0} {540 - y1} cm /{name} Do Q\n"
    doc.update_stream(contents, b"\n".join(kept) + b"\n" + draw.encode())
    return page


def test_what_a_picture_shows_is_measured_with_its_clip_path():
    doc = pymupdf.open()
    page = _clipped_page(doc, (700, 300, 1900, 975), (700, 300, 950, 530))
    [placement] = pdf_images.placements(page)
    assert placement.bbox == pymupdf.Rect(700, 300, 1900, 975)
    assert placement.visible == pymupdf.Rect(700, 300, 950, 530)
    pictures, skipped = pdf_images.page_pictures(page)
    assert [(picture.xref, picture.region) for picture in pictures] == [(0, pymupdf.Rect(700, 300, 950, 530))]
    assert skipped == {"not_shown": 0, "no_content": 0}
    assert pdf_images.single_rects(page) == []   # rendered as the page shows it, not read one by one


def test_without_text_clip_pictures_are_measured_unclipped_and_the_run_says_so(monkeypatch, caplog):
    """PyMuPDF before 1.27.1 has no TEXT_CLIP: the part of a picture its clip path shows cannot be measured, so
    the picture is taken whole. That used to happen silently; the run now warns, once per process."""
    monkeypatch.delattr(pymupdf, "TEXT_CLIP", raising=False)
    monkeypatch.setattr(pymupdf_compat, "_warned", set())
    doc = pymupdf.open()
    page = _clipped_page(doc, (700, 300, 1900, 975), (700, 300, 950, 530))
    with caplog.at_level(logging.WARNING, logger=pymupdf_compat.__name__):
        [placement] = pdf_images.placements(page)
        pdf_images.shown_boxes(page)
    assert placement.visible == placement.bbox & pdf_images.page_area(page)
    warnings = [record.getMessage() for record in caplog.records if record.name == pymupdf_compat.__name__]
    assert len(warnings) == 1 and "TEXT_CLIP" in warnings[0] and "1.27.1" in warnings[0]


def test_a_picture_clipped_away_entirely_is_not_shown():
    doc = pymupdf.open()
    page = _clipped_page(doc, (100, 100, 500, 325), (10, 10, 30, 30))
    assert pdf_images.page_pictures(page) == ([], {"not_shown": 1, "no_content": 0})


def test_routing_counts_only_large_pictures_as_read_one_by_one():
    """M1: small pictures (icons, thumbnails) are read only when they look like text, so for routing they are
    not captured: a page without a text layer that shows them goes to its render."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(72, 72, 130, 156), stream=_png(_text_image("SKU", (290, 420))))
    big = page.insert_image(pymupdf.Rect(72, 300, 522, 525), stream=_png(_text_image("CHART", (900, 450))))
    assert big and pdf_images.single_rects(page) == [pymupdf.Rect(72, 300, 522, 525)]


class _TextOCR(BaseOCR):
    """Answers each image with a text naming its size; images of ``fail_size`` fail (flagged failed)."""

    def __init__(self, fail_size=None):
        super().__init__(api_key=None)
        self.fail_size = fail_size
        self.sent = []

    def batch_process_images(self, images, **kwargs):
        out = []
        for image in images:
            size = pymupdf.Pixmap(image).width
            self.sent.append(size)
            out.append(OCRResult(text="", metadata={"failed": True}) if size == self.fail_size
                       else OCRResult(text=f"picture {size} px"))
        return out

    def validate_api_key(self):
        return True


def _report_page(doc):
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(72, 72, 523, 280), "A text page with enough words to route as text. " * 8,
                        fontsize=9)
    return page


def test_a_failed_answer_leaves_a_placeholder_and_counts_as_failed(tmp_path):
    doc = pymupdf.open()
    page = _report_page(doc)
    page.insert_image(pymupdf.Rect(72, 300, 372, 390), stream=_png(_text_image("OK", (400, 120))))
    page.insert_image(pymupdf.Rect(72, 450, 372, 540), stream=_png(_text_image("FAILS", (500, 150))))
    path = tmp_path / "failed.pdf"
    doc.save(str(path))
    result = PDFLoader(path, ocr=_TextOCR(fail_size=500)).convert_to_json(
        extract_images=True, ocr_images=True, show_progress=False)
    pictures = [item["content"] for item in result["content"] if item["type"] == "text:image_description"]
    assert pictures == ["<image_ocr_result>picture 400 px</image_ocr_result>",
                        "<image_ocr_result>[image: OCR unavailable]</image_ocr_result>"]
    assert (result["ocr_images"]["failed"], result["ocr_images"]["empty"]) == (1, 0)


def test_a_page_without_a_text_layer_keeps_its_only_small_pictures(tmp_path):
    """M1: on a page with no usable text layer, pictures are the only content: a small one is read even when its
    pixels look like shapes (a photo over a short label), so the page never emits nothing."""
    doc = pymupdf.open()
    _report_page(doc)
    sheet = doc.new_page(width=595, height=842)
    thumb = Image.new("RGB", (290, 420), "white")
    ImageDraw.Draw(thumb).rectangle((10, 10, 280, 300), fill=(90, 110, 150))
    ImageDraw.Draw(thumb).text((12, 320), "SKU 4100", fill="black", font=ImageFont.load_default(size=40))
    sheet.insert_image(pymupdf.Rect(72, 72, 130, 156), stream=_png(thumb), keep_proportion=False)
    path = tmp_path / "thumb.pdf"
    doc.save(str(path))
    loader = PDFLoader(path, ocr=_TextOCR())
    result = loader.convert_to_json(extract_images=True, ocr_images=True, show_progress=False)
    assert [item["page"] for item in result["content"] if item["type"] == "text:image_description"] == [2]


def test_a_picture_repeated_at_the_same_place_keeps_its_first_copy(tmp_path):
    """m1: the same picture at the same place on most pages is page furniture: the first copy stays, the later
    ones are typed text:header / text:footer. One shown on two pages, or at moving places, is content."""
    doc = pymupdf.open()
    logo = _png(_text_image("NORTHWIND", (600, 150)))
    stamp = _png(_text_image("APPROVED", (700, 175)))
    logo_xref = stamp_xref = 0
    for number in range(1, 7):
        page = _report_page(doc)
        if logo_xref:
            page.insert_image(pymupdf.Rect(400, 20, 520, 50), xref=logo_xref)
        else:
            logo_xref = page.insert_image(pymupdf.Rect(400, 20, 520, 50), stream=logo)
        spot = pymupdf.Rect(72, 700 + 10 * number, 272, 750 + 10 * number)
        if stamp_xref:
            page.insert_image(spot, xref=stamp_xref)
        else:
            stamp_xref = page.insert_image(spot, stream=stamp)
    path = tmp_path / "logo.pdf"
    doc.save(str(path))
    result = PDFLoader(path, ocr=_TextOCR()).convert_to_json(extract_images=True, ocr_images=True, show_progress=False)
    logos = [(item["page"], item["type"]) for item in result["content"] if "600 px" in item["content"]]
    stamps = [(item["page"], item["type"]) for item in result["content"] if "700 px" in item["content"]]
    assert logos == [(1, "text:image_description")] + [(page, "text:header") for page in range(2, 7)]
    assert stamps == [(page, "text:image_description") for page in range(1, 7)]   # it moves: content
    assert all("_picture" not in item for item in result["content"])
