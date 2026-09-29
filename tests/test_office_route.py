"""Office image-dominance route: probe classification + gating + graceful fallback."""
import io
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from doc2mark.formats.office import OfficeProcessor
from doc2mark.utils.libreoffice import find_libreoffice

SAMP = Path("sample_documents")


class _StubOCR:
    config = None


@pytest.fixture
def image_pptx(tmp_path):
    """A 1-slide pptx that is a single full-bleed image -> image-dominant."""
    pptx = pytest.importorskip("pptx")
    Image = pytest.importorskip("PIL.Image")
    from pptx.util import Emu
    prs = pptx.Presentation()
    prs.slide_width = Emu(9144000)
    prs.slide_height = Emu(6858000)
    s = prs.slides.add_slide(prs.slide_layouts[6])
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "navy").save(buf, format="PNG")
    s.shapes.add_picture(io.BytesIO(buf.getvalue()), 0, 0,
                         width=prs.slide_width, height=prs.slide_height)
    out = tmp_path / "img.pptx"
    prs.save(str(out))
    return out


def test_probe_image_dominant_vs_text(image_pptx):
    p = OfficeProcessor(ocr=_StubOCR())
    assert p._is_image_dominant(image_pptx) is True
    assert p._is_image_dominant(SAMP / "sample_document.docx") is False
    assert p._is_image_dominant(SAMP / "sample_presentation.pptx") is False


def test_xlsx_never_routes():
    p = OfficeProcessor(ocr=_StubOCR())
    assert p._maybe_route_image_dominant(
        SAMP / "sample_spreadsheet.xlsx", 100, ocr_images=True, extract_images=True) is None


def test_no_route_without_ocr(image_pptx):
    assert OfficeProcessor(ocr=None)._maybe_route_image_dominant(
        image_pptx, 100, ocr_images=True, extract_images=True) is None


def test_no_route_when_ocr_not_requested(image_pptx):
    p = OfficeProcessor(ocr=_StubOCR())
    assert p._maybe_route_image_dominant(
        image_pptx, 100, ocr_images=False, extract_images=True) is None
    assert p._maybe_route_image_dominant(
        image_pptx, 100, ocr_images=True, extract_images=False) is None


def test_text_doc_not_routed():
    p = OfficeProcessor(ocr=_StubOCR())
    assert p._maybe_route_image_dominant(
        SAMP / "sample_document.docx", 100, ocr_images=True, extract_images=True) is None


def test_route_falls_back_on_conversion_failure(image_pptx):
    """Image-dominant + OCR, but conversion raises -> None (native fallback), never raises."""
    p = OfficeProcessor(ocr=_StubOCR())
    with patch.object(OfficeProcessor, "_process_as_image_dominant",
                      side_effect=RuntimeError("no soffice")):
        assert p._maybe_route_image_dominant(
            image_pptx, 100, ocr_images=True, extract_images=True) is None


def _text_deck_over_picture(path):
    """Slides whose text sits in a table over a full-bleed picture: the converted PDF has
    a text layer on every page, so the PDF route decides ``text``."""
    pptx = pytest.importorskip("pptx")
    Image = pytest.importorskip("PIL.Image")
    from pptx.util import Emu
    prs = pptx.Presentation()
    prs.slide_width, prs.slide_height = Emu(9144000), Emu(5143500)
    buf = io.BytesIO()
    Image.new("RGB", (480, 270), "lightsteelblue").save(buf, format="PNG")
    for k in range(2):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        slide.shapes.add_picture(io.BytesIO(buf.getvalue()), 0, 0, prs.slide_width, prs.slide_height)
        table = slide.shapes.add_table(4, 3, Emu(400000), Emu(400000), Emu(8000000), Emu(3000000)).table
        for r in range(4):
            for c in range(3):
                table.cell(r, c).text = f"Region {r} metric {c}: value {k}{r * 100 + c * 7}.5 EUR"
    prs.save(str(path))
    return path


def test_converted_pdf_decides_and_text_goes_native(tmp_path):
    """When the OOXML pre-filter says image but the converted PDF routes text, the
    document is not processed by the PDF pipeline: it goes back to native extraction,
    and the decision is recorded."""
    if find_libreoffice() is None:  # same policy as tests/e2e require_tool
        if os.environ.get("D2M_E2E_STRICT") == "1":
            pytest.fail("LibreOffice is required (D2M_E2E_STRICT=1)", pytrace=False)
        pytest.skip("LibreOffice is not installed")
    deck = _text_deck_over_picture(tmp_path / "table_text.pptx")
    p = OfficeProcessor(ocr=_StubOCR())
    route_info = {}
    with patch.object(OfficeProcessor, "_is_image_dominant", return_value=True), \
            patch("doc2mark.formats.pdf.PDFProcessor.process") as pdf_process:
        routed = p._maybe_route_image_dominant(
            deck, 100, route_info=route_info, ocr_images=True, extract_images=True)
    assert routed is None
    pdf_process.assert_not_called()
    assert route_info == {"routed_via": "native", "route_reason": "converted PDF routes text"}


def test_route_failure_is_recorded(image_pptx):
    p = OfficeProcessor(ocr=_StubOCR())
    route_info = {}
    with patch("doc2mark.utils.libreoffice.convert_office_to", side_effect=RuntimeError("soffice crashed")):
        assert p._maybe_route_image_dominant(
            image_pptx, 100, route_info=route_info, ocr_images=True, extract_images=True) is None
    assert route_info == {"routed_via": "native", "route_error": "soffice crashed"}


def _text_and_image_pdfs(tmp_path):
    """A PDF page with a real text layer and a PDF page that is one full-page picture."""
    pymupdf = pytest.importorskip("pymupdf")
    Image = pytest.importorskip("PIL.Image")
    text_pdf, image_pdf = tmp_path / "text.pdf", tmp_path / "scan.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "\n".join(f"Line {i}: quarterly revenue grew in every region." for i in range(12)))
    doc.save(str(text_pdf))
    doc.close()
    buf = io.BytesIO()
    Image.new("RGB", (850, 1100), "white").save(buf, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(str(image_pdf))
    doc.close()
    return text_pdf, image_pdf


def test_pdf_route_adapter_contract(tmp_path):
    """The Office route takes the converted PDF's decision through ONE adapter over
    PDFLoader._document_image_strategy (owned by the PDF route). If that method changes
    shape, this test fails loudly; at runtime the adapter answers None and the document
    stays on native extraction."""
    from doc2mark.formats.office import _pdf_document_route
    from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader

    text_pdf, image_pdf = _text_and_image_pdfs(tmp_path)
    assert callable(getattr(PDFLoader, "_document_image_strategy", None))
    assert _pdf_document_route(text_pdf) == "text"
    assert _pdf_document_route(image_pdf) == "image"


def test_pdf_route_adapter_answers_none_when_the_pdf_side_changes_shape(tmp_path):
    from doc2mark.formats.office import _pdf_document_route
    from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader

    text_pdf, _ = _text_and_image_pdfs(tmp_path)
    with patch.object(PDFLoader, "_document_image_strategy", None):
        assert _pdf_document_route(text_pdf) is None
    with patch.object(PDFLoader, "_document_image_strategy", return_value="per-page"):
        assert _pdf_document_route(text_pdf) is None


def test_unanswered_pdf_route_goes_native(tmp_path, image_pptx):
    """Fail closed: when the converted PDF's route is unknown the document is not handed to
    the PDF pipeline (which would re-decide on its own) but stays native, and that is recorded."""
    converted = tmp_path / "converted.pdf"
    converted.write_bytes(b"%PDF-1.4\n")
    p = OfficeProcessor(ocr=_StubOCR())
    route_info = {}
    with patch("doc2mark.utils.libreoffice.convert_office_to", return_value=converted), \
            patch("doc2mark.formats.office._pdf_document_route", return_value=None), \
            patch("doc2mark.formats.pdf.PDFProcessor.process") as pdf_process:
        routed = p._maybe_route_image_dominant(
            image_pptx, 100, route_info=route_info, ocr_images=True, extract_images=True)
    assert routed is None
    pdf_process.assert_not_called()
    assert route_info == {"routed_via": "native", "route_reason": "converted PDF route unavailable"}
