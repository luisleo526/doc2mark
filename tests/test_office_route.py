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
