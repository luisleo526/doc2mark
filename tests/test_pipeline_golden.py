"""Golden / structural characterization tests for the office and PDF pipelines.

These tests run real conversions against the sample documents shipped in
``sample_documents/`` and assert *structural* properties of the current output
(headings, table pipes, HTML table tags, page/slide/sheet markers, specific
cell text).  They are intentionally **not** whole-string equality checks --
they pin stable substrings and counts so that future refactors cannot silently
regress the output without a test failure.

No network access and no API keys are required -- the loader is created with
``ocr_provider=None``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

import pytest

from doc2mark import UnifiedDocumentLoader
from doc2mark.core.base import DocumentFormat, ProcessedDocument

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_documents"
COMPLEX_DIR = SAMPLE_DIR / "complex-tables"


@pytest.fixture(scope="module")
def loader() -> UnifiedDocumentLoader:
    """A shared loader with no OCR (pure text extraction)."""
    return UnifiedDocumentLoader(ocr_provider=None)


# Helpers -------------------------------------------------------------------

def _heading_lines(content: str) -> List[str]:
    """Return all lines that start with one or more '#' characters."""
    return [line for line in content.splitlines() if line.startswith("#")]


def _pipe_lines(content: str) -> List[str]:
    """Return all lines that contain markdown table pipes."""
    return [line for line in content.splitlines() if "|" in line]


def _page_markers(content: str) -> List[str]:
    """Return all HTML comment page/slide/sheet markers."""
    return re.findall(r"<!-- (?:page|slide|sheet) \d+ -->", content)


# ===================================================================
# sample_document.docx -- rich DOCX with headings, structured table,
# and an HTML table with merged cells.
# ===================================================================

class TestSampleDocumentDocx:
    """Characterization tests for sample_documents/sample_document.docx."""

    @pytest.fixture(scope="class")
    def result(self, loader: UnifiedDocumentLoader) -> ProcessedDocument:
        path = SAMPLE_DIR / "sample_document.docx"
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        return loader.load(path, output_format="markdown")

    # -- basic metadata --------------------------------------------------

    def test_format_is_docx(self, result: ProcessedDocument):
        assert result.metadata.format == DocumentFormat.DOCX

    def test_content_is_nonempty(self, result: ProcessedDocument):
        assert len(result.content) > 100

    # -- page markers ----------------------------------------------------

    def test_has_page_marker(self, result: ProcessedDocument):
        markers = _page_markers(result.content)
        assert len(markers) >= 1
        assert "<!-- page 1 -->" in markers

    # -- heading structure -----------------------------------------------

    def test_has_title_heading(self, result: ProcessedDocument):
        assert "# Sample DOCX Document" in result.content

    def test_has_introduction_heading(self, result: ProcessedDocument):
        headings = _heading_lines(result.content)
        intro_headings = [h for h in headings if "Introduction" in h]
        assert len(intro_headings) >= 1

    def test_has_structured_table_heading(self, result: ProcessedDocument):
        assert "## Structured Table" in result.content

    def test_has_unstructured_table_heading(self, result: ProcessedDocument):
        assert "## Unstructured Table (Merged Cells)" in result.content

    def test_has_additional_text_heading(self, result: ProcessedDocument):
        assert "## Additional Text Content" in result.content

    def test_heading_count(self, result: ProcessedDocument):
        """The DOCX currently produces at least 5 heading lines."""
        headings = _heading_lines(result.content)
        assert len(headings) >= 5

    # -- structured markdown table (pipes) --------------------------------

    def test_structured_table_has_pipes(self, result: ProcessedDocument):
        pipes = _pipe_lines(result.content)
        # Header + separator + 4 data rows = at least 6 pipe lines
        assert len(pipes) >= 6

    def test_structured_table_header_row(self, result: ProcessedDocument):
        assert "| Product | Category | Price | Stock |" in result.content

    def test_structured_table_separator(self, result: ProcessedDocument):
        assert "| --- | --- | --- | --- |" in result.content

    def test_structured_table_cell_laptop(self, result: ProcessedDocument):
        assert "Laptop" in result.content
        assert "$999.99" in result.content

    def test_structured_table_cell_smartphone(self, result: ProcessedDocument):
        assert "Smartphone" in result.content
        assert "$699.99" in result.content

    def test_structured_table_cell_desk_chair(self, result: ProcessedDocument):
        assert "Desk Chair" in result.content
        assert "$199.99" in result.content

    def test_structured_table_cell_coffee_mug(self, result: ProcessedDocument):
        assert "Coffee Mug" in result.content
        assert "$12.99" in result.content

    # -- HTML table with merged cells (unstructured) ----------------------

    def test_has_html_table(self, result: ProcessedDocument):
        assert "<table>" in result.content
        assert "</table>" in result.content

    def test_html_table_count(self, result: ProcessedDocument):
        assert result.content.count("<table>") == 1

    def test_html_table_has_department_header(self, result: ProcessedDocument):
        assert "<th>Department</th>" in result.content

    def test_html_table_has_quarter_headers(self, result: ProcessedDocument):
        assert "<th>Q1</th>" in result.content
        assert "<th>Q2</th>" in result.content
        assert "<th>Q3</th>" in result.content

    def test_html_table_sales_row(self, result: ProcessedDocument):
        assert "<td>Sales</td>" in result.content
        assert "$10,000" in result.content
        assert "$12,000" in result.content
        assert "$15,000" in result.content

    def test_html_table_marketing_row(self, result: ProcessedDocument):
        assert "<td>Marketing</td>" in result.content
        assert "$5,000" in result.content

    def test_html_table_grand_total_colspan(self, result: ProcessedDocument):
        assert 'colspan="3"' in result.content
        assert "Grand Total (All Quarters)" in result.content
        assert "$55,000" in result.content

    # -- prose / body text -----------------------------------------------

    def test_intro_paragraph(self, result: ProcessedDocument):
        assert "comprehensive sample DOCX document" in result.content

    def test_key_features_list(self, result: ProcessedDocument):
        assert "Rich text formatting" in result.content
        assert "Image embedding" in result.content
        assert "Structured data tables" in result.content
        assert "Complex merged cell layouts" in result.content


# ===================================================================
# sample_pdf.pdf -- the same content rendered as PDF.
# ===================================================================

class TestSamplePdf:
    """Characterization tests for sample_documents/sample_pdf.pdf."""

    @pytest.fixture(scope="class")
    def result(self, loader: UnifiedDocumentLoader) -> ProcessedDocument:
        path = SAMPLE_DIR / "sample_pdf.pdf"
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        return loader.load(path, output_format="markdown")

    # -- metadata --------------------------------------------------------

    def test_format_is_pdf(self, result: ProcessedDocument):
        assert result.metadata.format == DocumentFormat.PDF

    def test_page_count(self, result: ProcessedDocument):
        assert result.metadata.page_count == 2

    def test_content_is_nonempty(self, result: ProcessedDocument):
        assert len(result.content) > 100

    # -- page markers ----------------------------------------------------

    def test_has_page_2_marker(self, result: ProcessedDocument):
        """The PDF currently emits a page 2 marker."""
        assert "<!-- page 2 -->" in result.content

    # -- heading structure -----------------------------------------------

    def test_has_title_heading(self, result: ProcessedDocument):
        assert "# Sample DOCX Document" in result.content

    def test_has_introduction_heading(self, result: ProcessedDocument):
        # PDF headings may include bold markers
        assert "Introduction" in result.content

    # -- structured markdown table (pipes) --------------------------------

    def test_structured_table_has_pipes(self, result: ProcessedDocument):
        pipes = _pipe_lines(result.content)
        assert len(pipes) >= 6

    def test_structured_table_header_row(self, result: ProcessedDocument):
        assert "| Product | Category | Price | Stock |" in result.content

    def test_structured_table_separator(self, result: ProcessedDocument):
        assert "| --- | --- | --- | --- |" in result.content

    def test_structured_table_cell_laptop(self, result: ProcessedDocument):
        assert "Laptop" in result.content
        assert "$999.99" in result.content

    def test_structured_table_cell_coffee_mug(self, result: ProcessedDocument):
        assert "Coffee Mug" in result.content
        assert "$12.99" in result.content

    # -- HTML table with merged cells -------------------------------------

    def test_has_html_table(self, result: ProcessedDocument):
        assert "<table>" in result.content
        assert "</table>" in result.content

    def test_html_table_count(self, result: ProcessedDocument):
        assert result.content.count("<table>") == 1

    def test_html_table_has_department_header(self, result: ProcessedDocument):
        assert "<th>Department</th>" in result.content

    def test_html_table_sales_row(self, result: ProcessedDocument):
        assert "<td>Sales</td>" in result.content
        assert "$10,000" in result.content

    def test_html_table_grand_total_colspan(self, result: ProcessedDocument):
        assert 'colspan="3"' in result.content
        assert "Grand Total (All Quarters)" in result.content
        assert "$55,000" in result.content

    # -- prose on page 2 -------------------------------------------------

    def test_additional_text_on_page_2(self, result: ProcessedDocument):
        assert "Additional Text Content" in result.content
        assert "comprehensive example for testing document processing" in result.content

    def test_bullet_list_items(self, result: ProcessedDocument):
        assert "Rich text formatting" in result.content
        assert "Structured data tables" in result.content


# ===================================================================
# complex_table_test.docx -- DOCX with complex merged-cell table
# ===================================================================

class TestComplexTableDocx:
    """Characterization tests for complex_table_test.docx."""

    @pytest.fixture(scope="class")
    def result(self, loader: UnifiedDocumentLoader) -> ProcessedDocument:
        path = COMPLEX_DIR / "complex_table_test.docx"
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        return loader.load(path, output_format="markdown")

    # -- metadata --------------------------------------------------------

    def test_format_is_docx(self, result: ProcessedDocument):
        assert result.metadata.format == DocumentFormat.DOCX

    def test_content_is_nonempty(self, result: ProcessedDocument):
        assert len(result.content) > 200

    # -- page marker -----------------------------------------------------

    def test_has_page_marker(self, result: ProcessedDocument):
        assert "<!-- page 1 -->" in result.content

    # -- headings --------------------------------------------------------

    def test_has_title_heading(self, result: ProcessedDocument):
        assert "# Complex Table Test Document" in result.content

    # -- HTML table structure (complex merged cells) ----------------------

    def test_has_html_table(self, result: ProcessedDocument):
        assert "<table>" in result.content
        assert "</table>" in result.content

    def test_html_table_count(self, result: ProcessedDocument):
        assert result.content.count("<table>") == 1

    def test_no_markdown_pipe_table(self, result: ProcessedDocument):
        """Complex table is rendered as HTML, not as pipe-delimited markdown."""
        pipes = _pipe_lines(result.content)
        assert len(pipes) == 0

    # -- colspan / rowspan in the merged-cell table -----------------------

    def test_company_overview_colspan(self, result: ProcessedDocument):
        assert 'colspan="3"' in result.content
        assert "Company Overview" in result.content

    def test_first_half_colspan(self, result: ProcessedDocument):
        assert "First Half" in result.content

    def test_second_half_colspan(self, result: ProcessedDocument):
        assert "Second Half" in result.content

    def test_division_rowspan(self, result: ProcessedDocument):
        assert 'rowspan="2"' in result.content
        assert "Division" in result.content

    def test_technology_rowspan(self, result: ProcessedDocument):
        assert 'rowspan="3"' in result.content
        assert "Technology" in result.content

    # -- specific cell data -----------------------------------------------

    def test_widget_a(self, result: ProcessedDocument):
        assert "Widget A" in result.content
        assert "$10K" in result.content

    def test_widget_b(self, result: ProcessedDocument):
        assert "Widget B" in result.content
        assert "$20K" in result.content

    def test_combined_products(self, result: ProcessedDocument):
        assert "Combined Products" in result.content

    def test_all_regions_total(self, result: ProcessedDocument):
        assert "All Regions Total" in result.content
        assert "$50K" in result.content

    def test_subtotal(self, result: ProcessedDocument):
        assert "Subtotal (All Divisions)" in result.content
        assert "$80K" in result.content
        assert "$114K" in result.content

    def test_grand_total(self, result: ProcessedDocument):
        assert "Grand Total (All Quarters)" in result.content
        assert "Annual Total: $382K" in result.content

    # -- explanatory prose -----------------------------------------------

    def test_demonstrates_list(self, result: ProcessedDocument):
        assert "Column spans" in result.content
        assert "Row spans" in result.content


# ===================================================================
# complex_table_test.xlsx -- spreadsheet with complex merged cells
# ===================================================================

class TestComplexTableXlsx:
    """Characterization tests for complex_table_test.xlsx."""

    @pytest.fixture(scope="class")
    def result(self, loader: UnifiedDocumentLoader) -> ProcessedDocument:
        path = COMPLEX_DIR / "complex_table_test.xlsx"
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        return loader.load(path, output_format="markdown")

    # -- metadata --------------------------------------------------------

    def test_format_is_xlsx(self, result: ProcessedDocument):
        assert result.metadata.format == DocumentFormat.XLSX

    def test_content_is_nonempty(self, result: ProcessedDocument):
        assert len(result.content) > 200

    # -- sheet marker ----------------------------------------------------

    def test_has_sheet_marker(self, result: ProcessedDocument):
        assert "<!-- sheet 1 -->" in result.content

    # -- headings --------------------------------------------------------

    def test_has_sheet_heading(self, result: ProcessedDocument):
        assert "# Sheet: Complex Table" in result.content

    # -- HTML table structure ---------------------------------------------

    def test_has_html_table(self, result: ProcessedDocument):
        assert "<table>" in result.content
        assert "</table>" in result.content

    def test_html_table_count(self, result: ProcessedDocument):
        assert result.content.count("<table>") == 1

    def test_no_markdown_pipe_table(self, result: ProcessedDocument):
        pipes = _pipe_lines(result.content)
        assert len(pipes) == 0

    # -- colspan / rowspan ------------------------------------------------

    def test_company_overview_colspan(self, result: ProcessedDocument):
        assert 'colspan="3"' in result.content
        assert "Company Overview" in result.content

    def test_division_rowspan(self, result: ProcessedDocument):
        assert 'rowspan="2"' in result.content
        assert "Division" in result.content

    def test_technology_rowspan(self, result: ProcessedDocument):
        assert 'rowspan="3"' in result.content
        assert "Technology" in result.content

    # -- cell data -------------------------------------------------------

    def test_spreadsheet_title(self, result: ProcessedDocument):
        assert "Complex Table Test Spreadsheet" in result.content

    def test_quarter_headers(self, result: ProcessedDocument):
        assert "Q1" in result.content
        assert "Q2" in result.content
        assert "Q3" in result.content
        assert "Q4" in result.content

    def test_widget_data(self, result: ProcessedDocument):
        assert "Widget A" in result.content
        assert "Widget B" in result.content

    def test_grand_total(self, result: ProcessedDocument):
        assert "Annual Total: $382K" in result.content


# ===================================================================
# complex_table_test.pptx -- presentation with complex table on slide 2
# ===================================================================

class TestComplexTablePptx:
    """Characterization tests for complex_table_test.pptx."""

    @pytest.fixture(scope="class")
    def result(self, loader: UnifiedDocumentLoader) -> ProcessedDocument:
        path = COMPLEX_DIR / "complex_table_test.pptx"
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        return loader.load(path, output_format="markdown")

    # -- metadata --------------------------------------------------------

    def test_format_is_pptx(self, result: ProcessedDocument):
        assert result.metadata.format == DocumentFormat.PPTX

    def test_page_count(self, result: ProcessedDocument):
        assert result.metadata.page_count == 2

    def test_content_is_nonempty(self, result: ProcessedDocument):
        assert len(result.content) > 200

    # -- slide markers ---------------------------------------------------

    def test_has_slide_markers(self, result: ProcessedDocument):
        markers = _page_markers(result.content)
        assert "<!-- slide 1 -->" in markers
        assert "<!-- slide 2 -->" in markers
        assert len(markers) == 2

    # -- headings --------------------------------------------------------

    def test_has_slide_1_heading(self, result: ProcessedDocument):
        assert "# Complex Table Test" in result.content

    def test_has_slide_2_heading(self, result: ProcessedDocument):
        assert "# Complex Table Structure" in result.content

    # -- HTML table structure ---------------------------------------------

    def test_has_html_table(self, result: ProcessedDocument):
        assert "<table>" in result.content
        assert "</table>" in result.content

    def test_html_table_count(self, result: ProcessedDocument):
        assert result.content.count("<table>") == 1

    def test_no_markdown_pipe_table(self, result: ProcessedDocument):
        pipes = _pipe_lines(result.content)
        assert len(pipes) == 0

    # -- colspan / rowspan ------------------------------------------------

    def test_company_overview_colspan(self, result: ProcessedDocument):
        assert 'colspan="3"' in result.content
        assert "Company Overview" in result.content

    def test_division_rowspan(self, result: ProcessedDocument):
        assert 'rowspan="2"' in result.content
        assert "Division" in result.content

    def test_technology_rowspan(self, result: ProcessedDocument):
        assert 'rowspan="3"' in result.content
        assert "Technology" in result.content

    # -- cell data -------------------------------------------------------

    def test_widget_data(self, result: ProcessedDocument):
        assert "Widget A" in result.content
        assert "Widget B" in result.content

    def test_regions(self, result: ProcessedDocument):
        assert "North" in result.content
        assert "South" in result.content
        assert "East" in result.content

    def test_financial_values(self, result: ProcessedDocument):
        assert "$10K" in result.content
        assert "$12K" in result.content
        assert "$20K" in result.content

    def test_grand_total(self, result: ProcessedDocument):
        assert "Annual Total: $382K" in result.content

    def test_subtotal(self, result: ProcessedDocument):
        assert "Subtotal (All Divisions)" in result.content


# ===================================================================
# Parametrized cross-format structural checks
# ===================================================================

# All five sample files that we test
SAMPLE_FILES = [
    pytest.param(SAMPLE_DIR / "sample_document.docx", id="sample_document.docx"),
    pytest.param(SAMPLE_DIR / "sample_pdf.pdf", id="sample_pdf.pdf"),
    pytest.param(COMPLEX_DIR / "complex_table_test.docx", id="complex_table_test.docx"),
    pytest.param(COMPLEX_DIR / "complex_table_test.xlsx", id="complex_table_test.xlsx"),
    pytest.param(COMPLEX_DIR / "complex_table_test.pptx", id="complex_table_test.pptx"),
]


class TestCrossFormatStructure:
    """Parametrized structural tests across all five sample files."""

    @pytest.fixture(scope="class")
    def loader_instance(self) -> UnifiedDocumentLoader:
        return UnifiedDocumentLoader(ocr_provider=None)

    @pytest.mark.parametrize("path", SAMPLE_FILES)
    def test_produces_nonempty_content(self, loader_instance, path: Path):
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        result = loader_instance.load(path, output_format="markdown")
        assert len(result.content) > 100, f"Content too short for {path.name}"

    @pytest.mark.parametrize("path", SAMPLE_FILES)
    def test_has_at_least_one_heading(self, loader_instance, path: Path):
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        result = loader_instance.load(path, output_format="markdown")
        headings = _heading_lines(result.content)
        assert len(headings) >= 1, f"No headings found for {path.name}"

    @pytest.mark.parametrize("path", SAMPLE_FILES)
    def test_has_at_least_one_table(self, loader_instance, path: Path):
        """Every sample file should produce either a markdown pipe table or an HTML table."""
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        result = loader_instance.load(path, output_format="markdown")
        has_pipe_table = len(_pipe_lines(result.content)) >= 3  # header + sep + 1 row
        has_html_table = "<table>" in result.content
        assert has_pipe_table or has_html_table, f"No table found for {path.name}"

    @pytest.mark.parametrize("path", SAMPLE_FILES)
    def test_has_page_or_slide_marker(self, loader_instance, path: Path):
        """Every sample should produce at least one page/slide/sheet marker."""
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        result = loader_instance.load(path, output_format="markdown")
        markers = _page_markers(result.content)
        assert len(markers) >= 1, f"No page/slide/sheet marker for {path.name}"

    @pytest.mark.parametrize("path", SAMPLE_FILES)
    def test_returns_processed_document(self, loader_instance, path: Path):
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        result = loader_instance.load(path, output_format="markdown")
        assert isinstance(result, ProcessedDocument)

    @pytest.mark.parametrize("path", SAMPLE_FILES)
    def test_metadata_format_matches_extension(self, loader_instance, path: Path):
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        result = loader_instance.load(path, output_format="markdown")
        ext = path.suffix.lower().lstrip(".")
        assert result.metadata.format.value == ext

    # -- financial data appears in all complex-table variants ----------

    COMPLEX_FILES = [
        pytest.param(COMPLEX_DIR / "complex_table_test.docx", id="ct_docx"),
        pytest.param(COMPLEX_DIR / "complex_table_test.xlsx", id="ct_xlsx"),
        pytest.param(COMPLEX_DIR / "complex_table_test.pptx", id="ct_pptx"),
    ]

    @pytest.mark.parametrize("path", COMPLEX_FILES)
    def test_complex_table_annual_total(self, loader_instance, path: Path):
        """All three complex-table formats should report the same annual total."""
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        result = loader_instance.load(path, output_format="markdown")
        assert "Annual Total: $382K" in result.content

    @pytest.mark.parametrize("path", COMPLEX_FILES)
    def test_complex_table_has_rowspan(self, loader_instance, path: Path):
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        result = loader_instance.load(path, output_format="markdown")
        assert "rowspan=" in result.content

    @pytest.mark.parametrize("path", COMPLEX_FILES)
    def test_complex_table_has_colspan(self, loader_instance, path: Path):
        if not path.exists():
            pytest.skip(f"Sample file not found: {path}")
        result = loader_instance.load(path, output_format="markdown")
        assert "colspan=" in result.content
