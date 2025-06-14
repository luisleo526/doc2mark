"""Basic tests for doc2mark package."""

import pytest
from pathlib import Path

from doc2mark import UnifiedDocumentLoader
from doc2mark.core.base import (
    DocumentFormat,
    OutputFormat,
    ProcessedDocument,
    DocumentMetadata
)
from doc2mark.ocr.base import OCRProvider


def test_import():
    """Test that the package can be imported."""
    assert UnifiedDocumentLoader is not None


def test_loader_initialization():
    """Test UnifiedDocumentLoader initialization."""
    # Test with tesseract (no API key needed)
    loader_tesseract = UnifiedDocumentLoader(ocr_provider='tesseract')
    assert loader_tesseract is not None
    assert hasattr(loader_tesseract, 'ocr')

    # Test default initialization only if API key is available
    import os
    if os.environ.get("OPENAI_API_KEY"):
        loader = UnifiedDocumentLoader()
        assert loader is not None
        assert hasattr(loader, 'ocr')


def test_supported_formats():
    """Test that all expected formats are supported."""
    loader = UnifiedDocumentLoader(ocr_provider='tesseract')
    supported = loader.supported_formats

    expected_formats = [
        'docx', 'xlsx', 'pptx',  # Modern Office
        'doc', 'xls', 'ppt', 'rtf', 'pps',  # Legacy Office
        'pdf',  # PDF
        'txt', 'csv', 'tsv', 'json', 'jsonl',  # Text/Data
        'html', 'xml', 'md'  # Markup
    ]

    for fmt in expected_formats:
        assert fmt in supported, f"Format {fmt} should be supported"


def test_output_formats():
    """Test OutputFormat enum values."""
    assert OutputFormat.MARKDOWN.value == 'markdown'
    assert OutputFormat.JSON.value == 'json'
    assert OutputFormat.TEXT.value == 'text'


def test_document_formats():
    """Test DocumentFormat enum values."""
    # Test some common formats
    assert DocumentFormat.DOCX.value == 'docx'
    assert DocumentFormat.PDF.value == 'pdf'
    assert DocumentFormat.XLSX.value == 'xlsx'
    assert DocumentFormat.TXT.value == 'txt'


def test_ocr_providers():
    """Test OCRProvider enum values."""
    assert OCRProvider.OPENAI.value == 'openai'
    assert OCRProvider.TESSERACT.value == 'tesseract'


def test_processed_document_structure():
    """Test ProcessedDocument dataclass."""
    metadata = DocumentMetadata(
        filename="test.txt",
        format=DocumentFormat.TXT,
        size_bytes=100
    )

    doc = ProcessedDocument(
        content="Test content",
        metadata=metadata
    )

    assert doc.content == "Test content"
    assert doc.metadata.filename == "test.txt"
    assert doc.metadata.format == DocumentFormat.TXT
    assert doc.metadata.size_bytes == 100
    assert doc.images is None
    assert doc.tables is None


def test_loader_with_different_parameters():
    """Test loader initialization with various parameters."""
    # Test with basic tesseract provider
    loader = UnifiedDocumentLoader(
        ocr_provider='tesseract'
    )
    assert loader is not None

    # Test with OpenAI parameters only if API key is available
    import os
    if os.environ.get("OPENAI_API_KEY"):
        loader_openai = UnifiedDocumentLoader(
            ocr_provider='openai',
            model='gpt-4.1',
            temperature=0.1,
            max_tokens=2048
        )
        assert loader_openai is not None


def test_file_not_found_error():
    """Test that FileNotFoundError is raised for non-existent files."""
    loader = UnifiedDocumentLoader(ocr_provider='tesseract')

    with pytest.raises(FileNotFoundError):
        loader.load('non_existent_file.txt')


def test_invalid_format_detection():
    """Test format detection for unknown extensions."""
    from doc2mark.core.base import UnsupportedFormatError

    loader = UnifiedDocumentLoader(ocr_provider='tesseract')

    # Create a temporary file with unknown extension
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.xyz', delete=False) as tmp:
        tmp.write(b"test content")
        tmp_path = tmp.name

    try:
        with pytest.raises(UnsupportedFormatError):
            loader.load(tmp_path)
    finally:
        Path(tmp_path).unlink()


@pytest.mark.skipif(not Path('sample_documents').exists(), reason="Sample documents not found")
def test_basic_text_loading():
    """Test loading a simple text file."""
    sample_txt = Path('sample_documents/sample_text.txt')
    if not sample_txt.exists():
        pytest.skip("sample_text.txt not found")

    loader = UnifiedDocumentLoader(ocr_provider='tesseract')
    result = loader.load(sample_txt)

    assert result is not None
    assert isinstance(result, ProcessedDocument)
    assert result.content is not None
    assert len(result.content) > 0
    assert result.metadata.format == DocumentFormat.TXT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
