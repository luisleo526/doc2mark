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


def test_a_damaged_pdf_written_to_stdout_is_only_the_document(run_cli, e2e_dir):
    """Review of #25 (m3): MuPDF writes its errors about a damaged file (here a content stream that does not
    inflate) to stdout, where the CLI writes the document: the Markdown carried "MuPDF error" lines and
    ``--format json`` was not JSON. The CLI sends them to its log on stderr (warnings, shown with -v too)."""
    memo = "Quarterly memo: pump station 12 passed its test."
    pdf = pdfgen.damaged_stream_pdf(e2e_dir / "damaged.pdf", [memo, "Survey notes for station 14."], damaged=[1])

    as_json = run_cli(pdf, "--ocr", "none", "--format", "json", raw=True)
    verbose = run_cli(pdf, "--ocr", "none", "-v", raw=True)

    assert as_json.exit_code == 0 and verbose.exit_code == 0, as_json.describe() + verbose.describe()
    assert memo in json.loads(as_json.stdout)["content"], as_json.describe()
    assert memo in verbose.stdout and "MuPDF" not in as_json.stdout + verbose.stdout, verbose.describe()
    assert "MuPDF error" in as_json.stderr and "MuPDF error" in verbose.stderr, as_json.describe()
