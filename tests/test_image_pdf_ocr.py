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


def _make_image_doc(tmp_path):
    """A mostly-image document (every page a full-page picture) -> image strategy."""
    doc = fitz.open()
    for color in ("navy", "darkgreen", "maroon"):
        p = doc.new_page(width=600, height=800)
        p.insert_image(p.rect, stream=_png((1200, 1600), color))
    path = tmp_path / "image_doc.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


class _StubOCR:
    config = None


def test_document_strategy_route(tmp_path):
    # mostly-image doc -> "image"; mixed/mostly-text doc -> "text"
    assert PDFLoader(_make_image_doc(tmp_path), ocr=_StubOCR())._document_image_strategy() == "image"
    assert PDFLoader(_make_pdf(tmp_path), ocr=_StubOCR())._document_image_strategy() == "text"


def test_decorative_image_filter(tmp_path):
    p = PDFLoader(_make_pdf(tmp_path), ocr=_StubOCR())
    page = p.doc.load_page(2)
    tiny = fitz.Rect(50, 50, 70, 70)   # 20pt on a 600pt-wide page
    big = fitz.Rect(0, 0, 400, 600)
    assert p._is_decorative_image(tiny, page) is True
    assert p._is_decorative_image(big, page) is False


def _make_image_with_text_doc(tmp_path):
    """Full-page images AND a real selectable-text layer (>200 chars/page)."""
    doc = fitz.open()
    for _ in range(2):
        p = doc.new_page(width=600, height=800)
        p.insert_image(p.rect, stream=_png((1200, 1600), "gray"))
        # insert_textbox wraps -> a genuine multi-line text layer (>200 chars)
        p.insert_textbox(fitz.Rect(50, 100, 550, 700),
                         "Real selectable body text content. " * 40, fontsize=11)
    path = tmp_path / "img_text.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def test_high_coverage_but_text_rich_is_text(tmp_path):
    # The two-signal fix: high image coverage but a usable selectable-text layer
    # routes to "text", not "image" (coverage alone would misclassify this).
    p = PDFLoader(_make_image_with_text_doc(tmp_path), ocr=_StubOCR())
    assert p._document_image_strategy() == "text"


def test_image_doc_renders_every_page(tmp_path):
    # image-strategy document: every page rendered once as a whole image.
    p = PDFLoader(_make_image_doc(tmp_path), ocr=_StubOCR())
    work = p._collect_all_images()
    renders = {w["page_num"] for w in work if w.get("is_page_render")}
    assert renders == {0, 1, 2}


def test_text_doc_collects_embedded_skips_tiny(tmp_path):
    # text-strategy document (mixed/mostly-text): no whole-page renders; embedded
    # figures collected for per-image OCR, tiny decorative image skipped.
    p = PDFLoader(_make_pdf(tmp_path), ocr=_StubOCR())
    work = p._collect_all_images()
    assert not any(w.get("is_page_render") for w in work)   # no whole-doc OCR
    assert any(w["page_num"] == 0 for w in work)            # full image collected
    assert all(w["page_num"] != 2 for w in work)            # tiny decorative skipped


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


def test_ocr_failure_emits_placeholder_not_base64(tmp_path):
    """When OCR was requested but results are missing (batch failure), images
    become lightweight placeholders — NEVER a raw base64 dump into the text."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((50, 100), "Real text content. " * 30, fontsize=11)   # text layer -> not image-dominant
    page.insert_image(fitz.Rect(50, 300, 360, 600), stream=_png((400, 400), "green"))  # 310pt, non-decorative
    path = tmp_path / "ocr_fail.pdf"
    doc.save(str(path))
    doc.close()

    pl = PDFLoader(str(path), ocr=_StubOCR())
    # ocr_images requested but the results map is empty (simulates a failed batch)
    out = pl._process_page(0, extract_images=True, ocr_images=True, ocr_results_map={})

    assert all(c["type"] != "image" for c in out), "must not emit raw base64 on OCR failure"
    assert any(c["type"] == "text:image_description" and "OCR unavailable" in c["content"] for c in out)
    assert any("Real text content" in c.get("content", "") for c in out)  # rule-based text preserved
