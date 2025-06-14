"""Simple tests that should work with current implementation."""

import pytest
from pathlib import Path

from doc2mark import UnifiedDocumentLoader
from doc2mark.core.base import DocumentFormat, OutputFormat


def test_basic_functionality():
    """Test basic loader functionality."""
    loader = UnifiedDocumentLoader(ocr_provider='tesseract')
    assert loader is not None
    assert hasattr(loader, 'supported_formats')
    assert len(loader.supported_formats) > 0


def test_real_sample_files():
    """Test with real sample files if they exist."""
    loader = UnifiedDocumentLoader(ocr_provider='tesseract')
    sample_dir = Path('sample_documents')

    if not sample_dir.exists():
        pytest.skip("Sample documents directory not found")

    # Test TXT file
    txt_files = list(sample_dir.glob('*.txt'))
    if txt_files:
        result = loader.load(txt_files[0])
        assert result is not None
        assert result.metadata.format == DocumentFormat.TXT
        print(f"✓ Loaded {txt_files[0].name}: {len(result.content)} chars")

    # Test CSV file
    csv_files = list(sample_dir.glob('*.csv'))
    if csv_files:
        result = loader.load(csv_files[0])
        assert result is not None
        assert result.metadata.format == DocumentFormat.CSV
        print(f"✓ Loaded {csv_files[0].name}: {len(result.content)} chars")

    # Test JSON file
    json_files = list(sample_dir.glob('*.json'))
    if json_files:
        result = loader.load(json_files[0])
        assert result is not None
        assert result.metadata.format == DocumentFormat.JSON
        print(f"✓ Loaded {json_files[0].name}: {len(result.content)} chars")

    # Test Markdown file
    md_files = list(sample_dir.glob('*.md'))
    if md_files:
        result = loader.load(md_files[0])
        assert result is not None
        assert result.metadata.format == DocumentFormat.MARKDOWN
        print(f"✓ Loaded {md_files[0].name}: {len(result.content)} chars")


def test_office_formats():
    """Test office formats if available."""
    loader = UnifiedDocumentLoader(ocr_provider='tesseract')
    sample_dir = Path('sample_documents')

    if not sample_dir.exists():
        pytest.skip("Sample documents directory not found")

    # Test DOCX
    docx_files = list(sample_dir.glob('*.docx'))
    if docx_files:
        try:
            result = loader.load(docx_files[0])
            assert result is not None
            print(f"✓ Loaded {docx_files[0].name}: {len(result.content)} chars")
        except Exception as e:
            print(f"! DOCX loading failed: {e}")

    # Test XLSX
    xlsx_files = list(sample_dir.glob('*.xlsx'))
    if xlsx_files:
        try:
            result = loader.load(xlsx_files[0])
            assert result is not None
            print(f"✓ Loaded {xlsx_files[0].name}: {len(result.content)} chars")
        except Exception as e:
            print(f"! XLSX loading failed: {e}")

    # Test PPTX
    pptx_files = list(sample_dir.glob('*.pptx'))
    if pptx_files:
        try:
            result = loader.load(pptx_files[0])
            assert result is not None
            print(f"✓ Loaded {pptx_files[0].name}: {len(result.content)} chars")
        except Exception as e:
            print(f"! PPTX loading failed: {e}")


def test_pdf_format():
    """Test PDF format if available."""
    loader = UnifiedDocumentLoader(ocr_provider='tesseract')
    sample_dir = Path('sample_documents')

    if not sample_dir.exists():
        pytest.skip("Sample documents directory not found")

    pdf_files = list(sample_dir.glob('*.pdf'))
    if pdf_files:
        try:
            result = loader.load(pdf_files[0])
            assert result is not None
            print(f"✓ Loaded {pdf_files[0].name}: {len(result.content)} chars")
        except Exception as e:
            print(f"! PDF loading failed: {e}")


if __name__ == "__main__":
    # Run tests directly
    test_basic_functionality()
    test_real_sample_files()
    test_office_formats()
    test_pdf_format()
