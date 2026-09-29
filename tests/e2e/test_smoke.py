"""Smoke tests for the E2E harness: the real CLI and real Tesseract, no mocks."""

from tests.e2e import pdfgen

OCR_PHRASE = "HARNESS SMOKE 4721"


def test_cli_extracts_text_layer_from_sample_pdf(run_cli, sample_documents_dir):
    result = run_cli(sample_documents_dir / "sample_pdf.pdf", "--ocr", "none", fmt="both")

    assert result.exit_code == 0, result.describe()
    assert "Sample DOCX Document" in result.markdown, result.describe()
    assert "Additional Text Content" in result.markdown, result.describe()
    assert result.json["metadata"]["filename"] == "sample_pdf.pdf"
    assert result.json["content"] == result.markdown


def test_tesseract_reads_text_from_image_only_pdf(run_cli, require_tool, e2e_dir):
    require_tool("tesseract")
    pdf = pdfgen.image_pdf(e2e_dir / "image_only.pdf", OCR_PHRASE)

    result = run_cli(pdf, "--ocr", "tesseract", "--ocr-images")

    assert result.exit_code == 0, result.describe()
    assert OCR_PHRASE in " ".join(result.markdown.split()), result.describe()
