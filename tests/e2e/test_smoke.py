"""Smoke tests for the E2E harness: the real CLI and real Tesseract, no mocks."""

import json

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


def test_a_pdf_written_to_stdout_is_only_the_document(run_cli, e2e_dir):
    """PyMuPDF 1.26.7+ prints "Consider using the pymupdf_layout package ..." to stdout the first time its table
    finder runs in a process. Without ``-o`` the CLI writes the document to stdout, so the Markdown started with
    that line and ``--format json`` was not JSON."""
    pdf = pdfgen.text_pdf(e2e_dir / "memo.pdf", "Quarterly memo\nPump station 12 passed its test.")

    markdown = run_cli(pdf, "--ocr", "none", raw=True)
    as_json = run_cli(pdf, "--ocr", "none", "--format", "json", raw=True)

    assert markdown.exit_code == 0 and as_json.exit_code == 0, as_json.describe()
    assert "pymupdf_layout" not in markdown.stdout + as_json.stdout, markdown.describe()
    assert "Quarterly memo" in markdown.stdout.strip().splitlines()[0], markdown.describe()
    assert "Pump station 12 passed its test." in json.loads(as_json.stdout)["content"], as_json.describe()
