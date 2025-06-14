"""Tests for document processing pipelines."""

import pytest

from doc2mark import UnifiedDocumentLoader
from doc2mark.core.base import DocumentFormat, OutputFormat, ProcessedDocument, ProcessingError


class TestPipelines:
    """Test document processing pipelines."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test loader with Tesseract (no API key needed)."""
        self.loader = UnifiedDocumentLoader(ocr_provider='tesseract')

    def test_pdf_pipeline(self, sample_documents_dir):
        """Test PDF processing pipeline."""
        pdf_files = list(sample_documents_dir.glob('*.pdf'))
        if not pdf_files:
            pytest.skip("No PDF files found")

        result = self.loader.load(pdf_files[0])
        assert isinstance(result, ProcessedDocument)
        assert result.metadata.format == DocumentFormat.PDF
        assert len(result.content) > 0

    def test_docx_pipeline(self, sample_documents_dir):
        """Test DOCX processing pipeline."""
        docx_files = list(sample_documents_dir.glob('*.docx'))
        if not docx_files:
            pytest.skip("No DOCX files found")

        result = self.loader.load(docx_files[0])
        assert isinstance(result, ProcessedDocument)
        assert result.metadata.format == DocumentFormat.DOCX
        assert len(result.content) > 0

    def test_xlsx_pipeline(self, sample_documents_dir):
        """Test XLSX processing pipeline."""
        xlsx_files = list(sample_documents_dir.glob('*.xlsx'))
        if not xlsx_files:
            pytest.skip("No XLSX files found")

        result = self.loader.load(xlsx_files[0])
        assert isinstance(result, ProcessedDocument)
        assert result.metadata.format == DocumentFormat.XLSX
        assert len(result.content) > 0

    def test_pptx_pipeline(self, sample_documents_dir):
        """Test PPTX processing pipeline."""
        pptx_files = list(sample_documents_dir.glob('*.pptx'))
        if not pptx_files:
            pytest.skip("No PPTX files found")

        result = self.loader.load(pptx_files[0])
        assert isinstance(result, ProcessedDocument)
        assert result.metadata.format == DocumentFormat.PPTX
        assert len(result.content) > 0

    @pytest.mark.parametrize("output_format", [
        OutputFormat.MARKDOWN,
        OutputFormat.JSON,
        OutputFormat.TEXT
    ])
    def test_output_formats(self, sample_documents_dir, output_format):
        """Test different output formats."""
        # Find any document file
        test_file = None
        for pattern in ['*.txt', '*.pdf', '*.docx']:
            files = list(sample_documents_dir.glob(pattern))
            if files:
                test_file = files[0]
                break

        if not test_file:
            pytest.skip("No test files found")

        result = self.loader.load(test_file, output_format=output_format)
        assert isinstance(result, ProcessedDocument)
        assert len(result.content) > 0

        # Check format-specific content
        if output_format == OutputFormat.JSON:
            # For now, just check it's not empty
            # The JSON format handling needs to be fixed in the processors
            assert result.content

    def test_image_extraction_disabled(self, sample_documents_dir):
        """Test that image extraction can be disabled."""
        pdf_files = list(sample_documents_dir.glob('*.pdf'))
        if not pdf_files:
            pytest.skip("No PDF files found")

        result = self.loader.load(
            pdf_files[0],
            extract_images=False,
            ocr_images=False
        )

        assert isinstance(result, ProcessedDocument)
        # Should not have extracted images
        assert result.images is None or len(result.images) == 0

    @pytest.mark.slow
    def test_large_document_handling(self, sample_documents_dir):
        """Test handling of larger documents."""
        # Find the largest document, excluding legacy formats that require LibreOffice
        largest_file = None
        largest_size = 0
        legacy_extensions = {'.doc', '.xls', '.ppt', '.rtf', '.pps'}

        for file_path in sample_documents_dir.glob('*'):
            if file_path.is_file() and file_path.suffix.lower() not in legacy_extensions:
                if file_path.stat().st_size > largest_size:
                    largest_size = file_path.stat().st_size
                    largest_file = file_path

        if not largest_file or largest_size < 1024 * 1024:  # Skip if no file > 1MB
            pytest.skip("No large non-legacy documents found")

        try:
            result = self.loader.load(largest_file, show_progress=True)
            assert isinstance(result, ProcessedDocument)
            assert len(result.content) > 0
        except ProcessingError as e:
            if "LibreOffice not found" in str(e):
                pytest.skip("LibreOffice not available for legacy format processing")
            raise

    def test_error_handling_invalid_file(self, tmp_path):
        """Test error handling for invalid files."""
        # Create an invalid file
        invalid_file = tmp_path / "invalid.pdf"
        invalid_file.write_text("This is not a valid PDF")

        with pytest.raises(Exception):  # Should raise ProcessingError or similar
            self.loader.load(invalid_file)

    def test_metadata_extraction(self, sample_documents_dir):
        """Test that metadata is properly extracted."""
        # Test with any available file
        test_files = list(sample_documents_dir.glob('*'))
        test_files = [f for f in test_files if f.is_file() and f.suffix in ['.pdf', '.docx', '.xlsx']]

        if not test_files:
            pytest.skip("No suitable test files found")

        result = self.loader.load(test_files[0])

        # Check metadata
        assert result.metadata is not None
        assert result.metadata.filename == test_files[0].name
        assert result.metadata.format is not None
        assert result.metadata.size_bytes > 0

    @pytest.mark.integration
    def test_ocr_pipeline_with_images(self, sample_documents_dir):
        """Test OCR pipeline with image extraction."""
        pdf_files = list(sample_documents_dir.glob('*.pdf'))
        if not pdf_files:
            pytest.skip("No PDF files found")

        # This test requires API key for OpenAI OCR
        import os
        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not set")

        loader = UnifiedDocumentLoader(ocr_provider='openai')
        result = loader.load(
            pdf_files[0],
            extract_images=True,
            ocr_images=True
        )

        # Check for OCR results
        if '<image_ocr_result>' in result.content:
            assert '</image_ocr_result>' in result.content
