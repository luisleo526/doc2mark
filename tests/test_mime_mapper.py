"""Tests for MimeTypeMapper functionality."""

import tempfile
from pathlib import Path
import pytest

from doc2mark.core.base import DocumentFormat
from doc2mark.core.mime_mapper import (
    MimeTypeMapper,
    get_default_mapper,
    detect_format_from_mime,
    detect_format_from_file,
    check_mime_support
)


@pytest.mark.unit
class TestMimeTypeMapper:
    """Test suite for MimeTypeMapper."""
    
    def test_basic_mime_to_format_mapping(self):
        """Test basic MIME type to DocumentFormat mapping."""
        mapper = MimeTypeMapper()
        
        # Test common MIME types
        assert mapper.get_format_from_mime('application/pdf') == DocumentFormat.PDF
        assert mapper.get_format_from_mime('text/plain') == DocumentFormat.TXT
        assert mapper.get_format_from_mime('text/csv') == DocumentFormat.CSV
        assert mapper.get_format_from_mime('application/json') == DocumentFormat.JSON
        assert mapper.get_format_from_mime('text/html') == DocumentFormat.HTML
        assert mapper.get_format_from_mime('text/xml') == DocumentFormat.XML
        assert mapper.get_format_from_mime('text/markdown') == DocumentFormat.MARKDOWN
        
    def test_office_mime_types(self):
        """Test Microsoft Office MIME type mappings."""
        mapper = MimeTypeMapper()
        
        # Modern Office formats
        assert mapper.get_format_from_mime(
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        ) == DocumentFormat.DOCX
        assert mapper.get_format_from_mime(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ) == DocumentFormat.XLSX
        assert mapper.get_format_from_mime(
            'application/vnd.openxmlformats-officedocument.presentationml.presentation'
        ) == DocumentFormat.PPTX
        
        # Legacy Office formats
        assert mapper.get_format_from_mime('application/msword') == DocumentFormat.DOC
        assert mapper.get_format_from_mime('application/vnd.ms-excel') == DocumentFormat.XLS
        assert mapper.get_format_from_mime('application/vnd.ms-powerpoint') == DocumentFormat.PPT
        
    def test_image_mime_types(self):
        """Test image MIME type mappings."""
        mapper = MimeTypeMapper()
        
        assert mapper.get_format_from_mime('image/png') == DocumentFormat.PNG
        assert mapper.get_format_from_mime('image/jpeg') == DocumentFormat.JPG
        assert mapper.get_format_from_mime('image/jpg') == DocumentFormat.JPG
        assert mapper.get_format_from_mime('image/webp') == DocumentFormat.WEBP
        
    def test_alternative_mime_types(self):
        """Test alternative MIME type variations."""
        mapper = MimeTypeMapper()
        
        # PDF alternatives
        assert mapper.get_format_from_mime('application/x-pdf') == DocumentFormat.PDF
        assert mapper.get_format_from_mime('application/acrobat') == DocumentFormat.PDF
        
        # CSV alternatives
        assert mapper.get_format_from_mime('application/csv') == DocumentFormat.CSV
        
        # RTF alternatives
        assert mapper.get_format_from_mime('application/rtf') == DocumentFormat.RTF
        assert mapper.get_format_from_mime('text/rtf') == DocumentFormat.RTF
        
    def test_format_to_mime_mapping(self):
        """Test DocumentFormat to MIME type mapping."""
        mapper = MimeTypeMapper()
        
        # Primary MIME types
        assert mapper.get_mime_from_format(DocumentFormat.PDF) == 'application/pdf'
        assert mapper.get_mime_from_format(DocumentFormat.TXT) == 'text/plain'
        assert mapper.get_mime_from_format(DocumentFormat.CSV) == 'text/csv'
        assert mapper.get_mime_from_format(DocumentFormat.JSON) == 'application/json'
        
        # Get all MIME types for a format
        pdf_mimes = mapper.get_mime_from_format(DocumentFormat.PDF, primary_only=False)
        assert isinstance(pdf_mimes, set)
        assert 'application/pdf' in pdf_mimes
        assert 'application/x-pdf' in pdf_mimes
        assert 'application/acrobat' in pdf_mimes
        
    def test_case_insensitive_mime_detection(self):
        """Test that MIME type detection is case-insensitive."""
        mapper = MimeTypeMapper()
        
        assert mapper.get_format_from_mime('APPLICATION/PDF') == DocumentFormat.PDF
        assert mapper.get_format_from_mime('Text/Plain') == DocumentFormat.TXT
        assert mapper.get_format_from_mime('IMAGE/PNG') == DocumentFormat.PNG
        
    def test_mime_with_parameters(self):
        """Test MIME types with parameters are handled correctly."""
        mapper = MimeTypeMapper()
        
        # MIME types sometimes come with charset or other parameters
        assert mapper.get_format_from_mime('text/plain; charset=utf-8') == DocumentFormat.TXT
        assert mapper.get_format_from_mime('application/json; charset=utf-8') == DocumentFormat.JSON
        assert mapper.get_format_from_mime('text/html; charset=iso-8859-1') == DocumentFormat.HTML
        
    def test_custom_mime_registration(self):
        """Test registering custom MIME type mappings."""
        mapper = MimeTypeMapper()
        
        # Register a custom MIME type
        mapper.register_mime_type('application/x-custom-doc', DocumentFormat.DOCX)
        assert mapper.get_format_from_mime('application/x-custom-doc') == DocumentFormat.DOCX
        
        # Check it appears in reverse mapping
        docx_mimes = mapper.get_mime_from_format(DocumentFormat.DOCX, primary_only=False)
        assert 'application/x-custom-doc' in docx_mimes
        
    def test_unregister_mime_type(self):
        """Test removing MIME type mappings."""
        mapper = MimeTypeMapper()
        
        # Register and then unregister
        mapper.register_mime_type('application/test', DocumentFormat.TXT)
        assert mapper.get_format_from_mime('application/test') == DocumentFormat.TXT
        
        success = mapper.unregister_mime_type('application/test')
        assert success is True
        assert mapper.get_format_from_mime('application/test') is None
        
        # Try to unregister non-existent
        success = mapper.unregister_mime_type('application/nonexistent')
        assert success is False
        
    def test_is_format_supported(self):
        """Test checking if MIME types are supported."""
        mapper = MimeTypeMapper()
        
        assert mapper.is_format_supported('application/pdf') is True
        assert mapper.is_format_supported('text/plain') is True
        assert mapper.is_format_supported('application/unknown') is False
        
        # Test with parameters
        assert mapper.is_format_supported('text/plain; charset=utf-8') is True
    
    def test_check_support(self):
        """Test the check_support method that returns both status and format."""
        mapper = MimeTypeMapper()
        
        # Test supported MIME types
        supported, fmt = mapper.check_support('application/pdf')
        assert supported is True
        assert fmt == DocumentFormat.PDF
        
        supported, fmt = mapper.check_support('text/plain')
        assert supported is True
        assert fmt == DocumentFormat.TXT
        
        # Test unsupported MIME type
        supported, fmt = mapper.check_support('application/unknown')
        assert supported is False
        assert fmt is None
        
        # Test with parameters
        supported, fmt = mapper.check_support('text/csv; charset=utf-8')
        assert supported is True
        assert fmt == DocumentFormat.CSV
        
        # Test case insensitivity
        supported, fmt = mapper.check_support('APPLICATION/PDF')
        assert supported is True
        assert fmt == DocumentFormat.PDF
        
    def test_get_all_mime_types(self):
        """Test retrieving all MIME type mappings."""
        mapper = MimeTypeMapper()
        
        # Get all mappings
        all_mappings = mapper.get_all_mime_types()
        assert isinstance(all_mappings, dict)
        assert 'application/pdf' in all_mappings
        assert all_mappings['application/pdf'] == DocumentFormat.PDF
        
        # Get mappings for specific format
        pdf_mappings = mapper.get_all_mime_types(DocumentFormat.PDF)
        assert all(fmt == DocumentFormat.PDF for fmt in pdf_mappings.values())
        
    def test_get_supported_formats(self):
        """Test getting list of supported formats."""
        mapper = MimeTypeMapper()
        
        supported = mapper.get_supported_formats()
        assert isinstance(supported, list)
        assert DocumentFormat.PDF in supported
        assert DocumentFormat.DOCX in supported
        assert DocumentFormat.TXT in supported
        
    def test_suggest_format(self):
        """Test format suggestion for unknown MIME types."""
        mapper = MimeTypeMapper()
        
        # Text-based unknown types should suggest TXT
        assert mapper.suggest_format('text/x-custom') == DocumentFormat.TXT
        assert mapper.suggest_format('text/unknown') == DocumentFormat.TXT
        
        # Image-based unknown types
        assert mapper.suggest_format('image/x-unknown') == DocumentFormat.PNG
        
        # Application types with hints
        assert mapper.suggest_format('application/x-custom-json') == DocumentFormat.JSON
        assert mapper.suggest_format('application/x-custom-xml') == DocumentFormat.XML
        
        # Already supported types return the correct format
        assert mapper.suggest_format('application/pdf') == DocumentFormat.PDF
        
    def test_get_mime_info(self):
        """Test getting detailed MIME type information."""
        mapper = MimeTypeMapper()
        
        # Known MIME type
        info = mapper.get_mime_info('application/pdf')
        assert info['mime_type'] == 'application/pdf'
        assert info['supported'] is True
        assert info['format'] == DocumentFormat.PDF
        assert info['category'] == 'application'
        assert info['subtype'] == 'pdf'
        assert '.pdf' in info['extensions']
        assert 'alternative_mimes' in info
        
        # Unknown MIME type
        info = mapper.get_mime_info('application/unknown')
        assert info['mime_type'] == 'application/unknown'
        assert info['supported'] is False
        assert info['format'] is None
        
    def test_detect_format_from_file(self):
        """Test format detection from file path."""
        mapper = MimeTypeMapper()
        
        # Test with different file extensions
        assert mapper.detect_format_from_file('document.pdf') == DocumentFormat.PDF
        
        # CSV detection might return XLS on some Windows systems due to mimetypes module quirks
        csv_format = mapper.detect_format_from_file('data.csv')
        assert csv_format in (DocumentFormat.CSV, DocumentFormat.XLS), f"Expected CSV or XLS, got {csv_format}"
        
        assert mapper.detect_format_from_file('doc.docx') == DocumentFormat.DOCX
        assert mapper.detect_format_from_file('image.png') == DocumentFormat.PNG
        
        # Test with Path objects
        assert mapper.detect_format_from_file(Path('test.json')) == DocumentFormat.JSON
        assert mapper.detect_format_from_file(Path('readme.md')) == DocumentFormat.MARKDOWN
        
    def test_get_extensions_for_mime(self):
        """Test getting file extensions for MIME types."""
        mapper = MimeTypeMapper()
        
        # PDF extensions
        pdf_exts = mapper.get_extensions_for_mime('application/pdf')
        assert '.pdf' in pdf_exts
        
        # Text extensions
        txt_exts = mapper.get_extensions_for_mime('text/plain')
        assert '.txt' in txt_exts
        
        # CSV extensions
        csv_exts = mapper.get_extensions_for_mime('text/csv')
        assert '.csv' in csv_exts
        
    def test_singleton_mapper(self):
        """Test the singleton default mapper."""
        mapper1 = get_default_mapper()
        mapper2 = get_default_mapper()
        
        # Should be the same instance
        assert mapper1 is mapper2
        
        # Register something in one, should appear in the other
        mapper1.register_mime_type('application/test-singleton', DocumentFormat.TXT)
        assert mapper2.get_format_from_mime('application/test-singleton') == DocumentFormat.TXT
        
    def test_convenience_functions(self):
        """Test convenience functions."""
        # Test detect_format_from_mime
        assert detect_format_from_mime('application/pdf') == DocumentFormat.PDF
        assert detect_format_from_mime('text/plain') == DocumentFormat.TXT
        
        # Test detect_format_from_file
        assert detect_format_from_file('test.pdf') == DocumentFormat.PDF
        assert detect_format_from_file('data.json') == DocumentFormat.JSON
        
        # Test check_mime_support
        supported, fmt = check_mime_support('application/pdf')
        assert supported is True
        assert fmt == DocumentFormat.PDF
        
        supported, fmt = check_mime_support('application/unknown')
        assert supported is False
        assert fmt is None
        
    def test_open_document_formats(self):
        """Test OpenDocument format mappings."""
        mapper = MimeTypeMapper()
        
        # ODT should map to DOCX (as a fallback for processing)
        assert mapper.get_format_from_mime('application/vnd.oasis.opendocument.text') == DocumentFormat.DOCX
        
        # ODS should map to XLSX
        assert mapper.get_format_from_mime('application/vnd.oasis.opendocument.spreadsheet') == DocumentFormat.XLSX
        
        # ODP should map to PPTX
        assert mapper.get_format_from_mime('application/vnd.oasis.opendocument.presentation') == DocumentFormat.PPTX
        
    def test_jsonl_variations(self):
        """Test JSONL MIME type variations."""
        mapper = MimeTypeMapper()
        
        # All JSONL variations should map correctly
        assert mapper.get_format_from_mime('application/x-ndjson') == DocumentFormat.JSONL
        assert mapper.get_format_from_mime('application/jsonlines') == DocumentFormat.JSONL
        assert mapper.get_format_from_mime('application/json-lines') == DocumentFormat.JSONL
        
    def test_edge_cases(self):
        """Test edge cases and error conditions."""
        mapper = MimeTypeMapper()
        
        # None or empty MIME type
        assert mapper.get_format_from_mime('') is None
        
        # Invalid MIME type format
        assert mapper.get_format_from_mime('not-a-mime-type') is None
        
        # MIME type with multiple parameters
        assert mapper.get_format_from_mime('text/plain; charset=utf-8; boundary=something') == DocumentFormat.TXT
        
        # Very long MIME type
        long_mime = 'application/' + 'x' * 1000
        assert mapper.get_format_from_mime(long_mime) is None
        
    def test_format_detection_with_real_file(self):
        """Test format detection with actual temporary files."""
        mapper = MimeTypeMapper()
        
        # Create temporary files and test detection
        with tempfile.NamedTemporaryFile(suffix='.pdf') as tmp:
            detected = mapper.detect_format_from_file(tmp.name)
            assert detected == DocumentFormat.PDF
            
        with tempfile.NamedTemporaryFile(suffix='.docx') as tmp:
            detected = mapper.detect_format_from_file(tmp.name)
            assert detected == DocumentFormat.DOCX
            
        with tempfile.NamedTemporaryFile(suffix='.json') as tmp:
            detected = mapper.detect_format_from_file(tmp.name)
            assert detected == DocumentFormat.JSON


@pytest.mark.integration
class TestMimeMapperIntegration:
    """Integration tests with the document loader."""
    
    @pytest.mark.unit  # This can also run as a unit test since it doesn't need external services
    def test_loader_uses_mime_detection(self):
        """Test that UnifiedDocumentLoader can use MIME type detection."""
        from doc2mark.core.loader import UnifiedDocumentLoader
        
        # Create a loader instance
        loader = UnifiedDocumentLoader(ocr_provider='tesseract')
        
        # The loader's _detect_format method should support MIME detection
        with tempfile.NamedTemporaryFile(suffix='.pdf') as tmp:
            tmp_path = Path(tmp.name)
            # This should work even if the file doesn't exist yet
            format_detected = loader._detect_format(tmp_path, use_mime=True)
            assert format_detected == DocumentFormat.PDF
            
            # Test with MIME detection disabled
            format_detected = loader._detect_format(tmp_path, use_mime=False)
            assert format_detected == DocumentFormat.PDF
