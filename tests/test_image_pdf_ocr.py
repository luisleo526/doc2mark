"""Page-level OCR strategy for image-dominant PDFs (scanned pages / image decks).

Image-dominant pages are rendered and OCR'd once instead of per embedded image;
decorative thumbnails are skipped. These tests exercise the heuristics and the
page-render handling on a synthetic PDF, without any real OCR calls.
"""
import io

import pytest

fitz = pytest.importorskip("pymupdf")
Image = pytest.importorskip("PIL.Image")

from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader, _PAGE_RENDER_XREF


def _png(size, color):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _make_pdf(tmp_path):
    doc = fitz.open()
    # page 0: full-page image, no text -> image-dominant
    p0 = doc.new_page(width=600, height=800)
    p0.insert_image(p0.rect, stream=_png((1200, 1600), "navy"))
    # page 1: lots of native text -> NOT image-dominant
    p1 = doc.new_page(width=600, height=800)
    p1.insert_text((50, 100), "This is a normal text page. " * 40, fontsize=11)
    # page 2: text page with a tiny decorative image -> per-image, tiny skipped
    p2 = doc.new_page(width=600, height=800)
    p2.insert_text((50, 100), "Another text page with words. " * 40, fontsize=11)
    p2.insert_image(fitz.Rect(50, 50, 70, 70), stream=_png((20, 20), "red"))
    path = tmp_path / "synthetic.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


class _StubOCR:
    config = None


def test_image_dominant_detection(tmp_path):
    p = PDFLoader(_make_pdf(tmp_path), ocr=_StubOCR())
    assert p._is_image_dominant_page(p.doc.load_page(0)) is True
    assert p._is_image_dominant_page(p.doc.load_page(1)) is False


def test_decorative_image_filter(tmp_path):
    p = PDFLoader(_make_pdf(tmp_path), ocr=_StubOCR())
    page = p.doc.load_page(2)
    tiny = fitz.Rect(50, 50, 70, 70)   # 20pt on a 600pt-wide page
    big = fitz.Rect(0, 0, 400, 600)
    assert p._is_decorative_image(tiny, page) is True
    assert p._is_decorative_image(big, page) is False


def test_collect_renders_image_pages_and_skips_tiny(tmp_path):
    p = PDFLoader(_make_pdf(tmp_path), ocr=_StubOCR())
    work = p._collect_all_images()
    renders = {w["page_num"] for w in work if w.get("is_page_render")}
    assert 0 in renders                       # image-dominant page rendered once
    assert all(w["page_num"] != 2 for w in work)  # tiny decorative image skipped


def test_process_page_emits_render_transcription(tmp_path):
    p = PDFLoader(_make_pdf(tmp_path), ocr=_StubOCR())
    out = p._process_page(0, extract_images=True, ocr_images=True,
                          ocr_results_map={(0, _PAGE_RENDER_XREF): "Transcribed slide text"})
    assert len(out) == 1
    assert out[0]["type"] == "text:image_description"
    assert "Transcribed slide text" in out[0]["content"]


def test_process_page_empty_render_drops_page(tmp_path):
    p = PDFLoader(_make_pdf(tmp_path), ocr=_StubOCR())
    out = p._process_page(0, extract_images=True, ocr_images=True,
                          ocr_results_map={(0, _PAGE_RENDER_XREF): "   "})
    assert out == []
