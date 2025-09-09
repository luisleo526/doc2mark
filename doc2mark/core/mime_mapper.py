"""MIME type to DocumentFormat mapping utilities."""

import mimetypes
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

from doc2mark.core.base import DocumentFormat


class MimeTypeMapper:
    """Maps MIME types to DocumentFormat and provides utilities for format detection."""
    
    # Standard MIME type mappings for each DocumentFormat
    _DEFAULT_MAPPINGS: Dict[str, DocumentFormat] = {
        # Office formats - Microsoft Office
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': DocumentFormat.DOCX,
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': DocumentFormat.XLSX,
        'application/vnd.openxmlformats-officedocument.presentationml.presentation': DocumentFormat.PPTX,
        
        # Office formats - Open Document
        'application/vnd.oasis.opendocument.text': DocumentFormat.DOCX,  # Map ODT to DOCX
        'application/vnd.oasis.opendocument.spreadsheet': DocumentFormat.XLSX,  # Map ODS to XLSX
        'application/vnd.oasis.opendocument.presentation': DocumentFormat.PPTX,  # Map ODP to PPTX
        
        # Legacy Office formats
        'application/msword': DocumentFormat.DOC,
        'application/vnd.ms-word': DocumentFormat.DOC,
        'application/vnd.ms-excel': DocumentFormat.XLS,
        'application/vnd.ms-powerpoint': DocumentFormat.PPT,
        'application/rtf': DocumentFormat.RTF,
        'text/rtf': DocumentFormat.RTF,
        'application/vnd.ms-powerpoint.slideshow': DocumentFormat.PPS,
        
        # PDF
        'application/pdf': DocumentFormat.PDF,
        'application/x-pdf': DocumentFormat.PDF,
        'application/acrobat': DocumentFormat.PDF,
        
        # Text/Data formats
        'text/plain': DocumentFormat.TXT,
        'text/csv': DocumentFormat.CSV,
        'application/csv': DocumentFormat.CSV,
        'text/tab-separated-values': DocumentFormat.TSV,
        'text/tsv': DocumentFormat.TSV,
        'application/json': DocumentFormat.JSON,
        'application/x-ndjson': DocumentFormat.JSONL,
        'application/jsonlines': DocumentFormat.JSONL,
        'application/json-lines': DocumentFormat.JSONL,
        
        # Markup formats
        'text/html': DocumentFormat.HTML,
        'application/xhtml+xml': DocumentFormat.HTML,
        'text/xml': DocumentFormat.XML,
        'application/xml': DocumentFormat.XML,
        'text/markdown': DocumentFormat.MARKDOWN,
        'text/x-markdown': DocumentFormat.MARKDOWN,
        
        # Image formats
        'image/png': DocumentFormat.PNG,
        'image/jpeg': DocumentFormat.JPG,
        'image/jpg': DocumentFormat.JPG,
        'image/webp': DocumentFormat.WEBP,
    }
    
    # Reverse mapping: DocumentFormat to primary MIME types
    _FORMAT_TO_MIME: Dict[DocumentFormat, str] = {
        DocumentFormat.DOCX: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        DocumentFormat.XLSX: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        DocumentFormat.PPTX: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        DocumentFormat.DOC: 'application/msword',
        DocumentFormat.XLS: 'application/vnd.ms-excel',
        DocumentFormat.PPT: 'application/vnd.ms-powerpoint',
        DocumentFormat.RTF: 'application/rtf',
        DocumentFormat.PPS: 'application/vnd.ms-powerpoint.slideshow',
        DocumentFormat.PDF: 'application/pdf',
        DocumentFormat.TXT: 'text/plain',
        DocumentFormat.CSV: 'text/csv',
        DocumentFormat.TSV: 'text/tab-separated-values',
        DocumentFormat.JSON: 'application/json',
        DocumentFormat.JSONL: 'application/x-ndjson',
        DocumentFormat.HTML: 'text/html',
        DocumentFormat.XML: 'text/xml',
        DocumentFormat.MARKDOWN: 'text/markdown',
        DocumentFormat.PNG: 'image/png',
        DocumentFormat.JPG: 'image/jpeg',
        DocumentFormat.JPEG: 'image/jpeg',
        DocumentFormat.WEBP: 'image/webp',
    }
    
    # Alternative MIME types for each format
    _ALTERNATIVE_MIMES: Dict[DocumentFormat, Set[str]] = {
        DocumentFormat.DOC: {'application/vnd.ms-word', 'application/msword'},
        DocumentFormat.PDF: {'application/x-pdf', 'application/acrobat'},
        DocumentFormat.CSV: {'application/csv', 'text/comma-separated-values'},
        DocumentFormat.TSV: {'text/tsv', 'application/tab-separated-values'},
        DocumentFormat.JSONL: {'application/jsonlines', 'application/json-lines', 'text/x-ndjson'},
        DocumentFormat.HTML: {'application/xhtml+xml'},
        DocumentFormat.XML: {'application/xml', 'text/xml'},
        DocumentFormat.MARKDOWN: {'text/x-markdown', 'text/plain'},
        DocumentFormat.JPG: {'image/jpg'},
        DocumentFormat.RTF: {'text/rtf'},
    }
    
    def __init__(self):
        """Initialize the MIME type mapper with default mappings."""
        # Create a copy of default mappings to allow runtime modifications
        self._mime_to_format: Dict[str, DocumentFormat] = self._DEFAULT_MAPPINGS.copy()
        self._format_to_mimes: Dict[DocumentFormat, Set[str]] = {}
        
        # Initialize reverse mappings
        self._build_reverse_mappings()
        
        # Initialize mimetypes module with additional types
        self._init_mimetypes()
    
    def _build_reverse_mappings(self):
        """Build reverse mappings from DocumentFormat to all possible MIME types."""
        for mime_type, doc_format in self._mime_to_format.items():
            if doc_format not in self._format_to_mimes:
                self._format_to_mimes[doc_format] = set()
            self._format_to_mimes[doc_format].add(mime_type)
        
        # Add alternative MIME types
        for doc_format, alt_mimes in self._ALTERNATIVE_MIMES.items():
            if doc_format not in self._format_to_mimes:
                self._format_to_mimes[doc_format] = set()
            self._format_to_mimes[doc_format].update(alt_mimes)
    
    def _init_mimetypes(self):
        """Initialize Python's mimetypes module with custom mappings."""
        # Add custom extensions
        custom_types = {
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            '.jsonl': 'application/x-ndjson',
            '.ndjson': 'application/x-ndjson',
            '.md': 'text/markdown',
            '.markdown': 'text/markdown',
            '.webp': 'image/webp',
            '.csv': 'text/csv',  # Explicitly set CSV to avoid Windows mimetypes issues
            '.tsv': 'text/tab-separated-values',
        }
        
        for ext, mime_type in custom_types.items():
            mimetypes.add_type(mime_type, ext)
    
    def get_format_from_mime(self, mime_type: str) -> Optional[DocumentFormat]:
        """Get DocumentFormat from MIME type.
        
        Args:
            mime_type: MIME type string (e.g., 'application/pdf')
            
        Returns:
            DocumentFormat if mapping exists, None otherwise
            
        Examples:
            >>> mapper = MimeTypeMapper()
            >>> mapper.get_format_from_mime('application/pdf')
            <DocumentFormat.PDF: 'pdf'>
            >>> mapper.get_format_from_mime('text/csv')
            <DocumentFormat.CSV: 'csv'>
        """
        # Normalize MIME type (lowercase, strip parameters)
        normalized = mime_type.lower().split(';')[0].strip()
        return self._mime_to_format.get(normalized)
    
    def get_mime_from_format(self, doc_format: DocumentFormat, 
                           primary_only: bool = True) -> Union[str, Set[str], None]:
        """Get MIME type(s) for a DocumentFormat.
        
        Args:
            doc_format: DocumentFormat enum value
            primary_only: If True, return only the primary MIME type.
                         If False, return all possible MIME types.
            
        Returns:
            - If primary_only=True: Primary MIME type string or None
            - If primary_only=False: Set of all MIME types or empty set
            
        Examples:
            >>> mapper = MimeTypeMapper()
            >>> mapper.get_mime_from_format(DocumentFormat.PDF)
            'application/pdf'
            >>> mapper.get_mime_from_format(DocumentFormat.PDF, primary_only=False)
            {'application/pdf', 'application/x-pdf', 'application/acrobat'}
        """
        if primary_only:
            return self._FORMAT_TO_MIME.get(doc_format)
        else:
            return self._format_to_mimes.get(doc_format, set())
    
    def detect_format_from_file(self, file_path: Union[str, Path],
                              use_content: bool = False) -> Optional[DocumentFormat]:
        """Detect DocumentFormat from a file using MIME type detection.
        
        Args:
            file_path: Path to the file
            use_content: If True, also examine file content for detection
            
        Returns:
            DocumentFormat if detected, None otherwise
            
        Examples:
            >>> mapper = MimeTypeMapper()
            >>> mapper.detect_format_from_file('document.pdf')
            <DocumentFormat.PDF: 'pdf'>
        """
        file_path = Path(file_path)
        
        # First try extension-based detection
        mime_type, _ = mimetypes.guess_type(str(file_path))
        
        if mime_type:
            doc_format = self.get_format_from_mime(mime_type)
            if doc_format:
                return doc_format
        
        # If content-based detection is requested and file exists
        if use_content and file_path.exists():
            try:
                import magic  # python-magic library
                mime = magic.from_file(str(file_path), mime=True)
                return self.get_format_from_mime(mime)
            except ImportError:
                # python-magic not installed, fall back to extension
                pass
            except Exception:
                # File reading error
                pass
        
        # Fall back to extension mapping
        extension = file_path.suffix.lower().lstrip('.')
        for fmt in DocumentFormat:
            if fmt.value == extension:
                return fmt
        
        return None
    
    def register_mime_type(self, mime_type: str, doc_format: DocumentFormat):
        """Register a custom MIME type mapping.
        
        Args:
            mime_type: MIME type to register
            doc_format: DocumentFormat to map to
            
        Examples:
            >>> mapper = MimeTypeMapper()
            >>> mapper.register_mime_type('application/x-custom', DocumentFormat.TXT)
            >>> mapper.get_format_from_mime('application/x-custom')
            <DocumentFormat.TXT: 'txt'>
        """
        normalized = mime_type.lower()
        self._mime_to_format[normalized] = doc_format
        
        # Update reverse mapping
        if doc_format not in self._format_to_mimes:
            self._format_to_mimes[doc_format] = set()
        self._format_to_mimes[doc_format].add(normalized)
    
    def unregister_mime_type(self, mime_type: str) -> bool:
        """Remove a MIME type mapping.
        
        Args:
            mime_type: MIME type to remove
            
        Returns:
            True if removed, False if not found
        """
        normalized = mime_type.lower()
        if normalized in self._mime_to_format:
            doc_format = self._mime_to_format[normalized]
            del self._mime_to_format[normalized]
            
            # Update reverse mapping
            if doc_format in self._format_to_mimes:
                self._format_to_mimes[doc_format].discard(normalized)
            
            return True
        return False
    
    def get_all_mime_types(self, doc_format: Optional[DocumentFormat] = None) -> Dict[str, DocumentFormat]:
        """Get all registered MIME type mappings.
        
        Args:
            doc_format: If specified, only return MIME types for this format
            
        Returns:
            Dictionary of MIME type to DocumentFormat mappings
            
        Examples:
            >>> mapper = MimeTypeMapper()
            >>> pdf_mimes = mapper.get_all_mime_types(DocumentFormat.PDF)
            >>> len(pdf_mimes) > 0
            True
        """
        if doc_format:
            mime_types = self._format_to_mimes.get(doc_format, set())
            return {mime: doc_format for mime in mime_types}
        else:
            return self._mime_to_format.copy()
    
    def get_supported_formats(self) -> List[DocumentFormat]:
        """Get list of all DocumentFormats that have MIME type mappings.
        
        Returns:
            List of DocumentFormat enums with registered MIME types
        """
        return list(self._format_to_mimes.keys())
    
    def is_format_supported(self, mime_type: str) -> bool:
        """Check if a MIME type is supported.
        
        Args:
            mime_type: MIME type to check
            
        Returns:
            True if MIME type has a mapping, False otherwise
        """
        normalized = mime_type.lower().split(';')[0].strip()
        return normalized in self._mime_to_format
    
    def check_support(self, mime_type: str) -> Tuple[bool, Optional[DocumentFormat]]:
        """Check if a MIME type is supported and return the format.
        
        Args:
            mime_type: MIME type to check
            
        Returns:
            Tuple of (is_supported, document_format)
            - is_supported: True if MIME type is supported
            - document_format: The DocumentFormat if supported, None otherwise
            
        Examples:
            >>> mapper = MimeTypeMapper()
            >>> supported, fmt = mapper.check_support('application/pdf')
            >>> print(f"Supported: {supported}, Format: {fmt}")
            Supported: True, Format: DocumentFormat.PDF
            >>> supported, fmt = mapper.check_support('application/unknown')
            >>> print(f"Supported: {supported}, Format: {fmt}")
            Supported: False, Format: None
        """
        normalized = mime_type.lower().split(';')[0].strip()
        doc_format = self._mime_to_format.get(normalized)
        return (doc_format is not None, doc_format)
    
    def get_extensions_for_mime(self, mime_type: str) -> List[str]:
        """Get file extensions associated with a MIME type.
        
        Args:
            mime_type: MIME type
            
        Returns:
            List of extensions (with dots, e.g., ['.pdf'])
        """
        extensions = mimetypes.guess_all_extensions(mime_type)
        
        # Also check our DocumentFormat mapping
        doc_format = self.get_format_from_mime(mime_type)
        if doc_format:
            ext = f".{doc_format.value}"
            if ext not in extensions:
                extensions.append(ext)
        
        return extensions
    
    def suggest_format(self, mime_type: str) -> Optional[DocumentFormat]:
        """Suggest a DocumentFormat for an unsupported MIME type.
        
        Args:
            mime_type: MIME type to analyze
            
        Returns:
            Suggested DocumentFormat based on MIME type category, or None
            
        Examples:
            >>> mapper = MimeTypeMapper()
            >>> mapper.suggest_format('text/x-custom-markup')
            <DocumentFormat.TXT: 'txt'>  # Suggests TXT for unknown text/* types
        """
        normalized = mime_type.lower().split(';')[0].strip()
        
        # Check if already supported
        if normalized in self._mime_to_format:
            return self._mime_to_format[normalized]
        
        # Analyze MIME type category
        if normalized.startswith('text/'):
            # Default text types to TXT
            return DocumentFormat.TXT
        elif normalized.startswith('image/'):
            # Try to map to a supported image format
            if 'png' in normalized:
                return DocumentFormat.PNG
            elif 'jpg' in normalized or 'jpeg' in normalized:
                return DocumentFormat.JPG
            elif 'webp' in normalized:
                return DocumentFormat.WEBP
            # Default to PNG for unknown images
            return DocumentFormat.PNG
        elif normalized.startswith('application/'):
            if 'json' in normalized:
                return DocumentFormat.JSON
            elif 'xml' in normalized:
                return DocumentFormat.XML
            elif 'pdf' in normalized:
                return DocumentFormat.PDF
        
        return None
    
    def get_mime_info(self, mime_type: str) -> Dict[str, any]:
        """Get detailed information about a MIME type.
        
        Args:
            mime_type: MIME type to analyze
            
        Returns:
            Dictionary with MIME type information
        """
        normalized = mime_type.lower().split(';')[0].strip()
        doc_format = self.get_format_from_mime(normalized)
        
        info = {
            'mime_type': normalized,
            'supported': doc_format is not None,
            'format': doc_format,
            'extensions': self.get_extensions_for_mime(normalized),
            'category': normalized.split('/')[0] if '/' in normalized else None,
            'subtype': normalized.split('/')[1] if '/' in normalized else None,
        }
        
        if doc_format:
            info['alternative_mimes'] = list(self.get_mime_from_format(doc_format, primary_only=False))
        
        return info


# Singleton instance for convenience
_default_mapper: Optional[MimeTypeMapper] = None


def get_default_mapper() -> MimeTypeMapper:
    """Get the default MimeTypeMapper instance.
    
    Returns:
        Singleton MimeTypeMapper instance
    """
    global _default_mapper
    if _default_mapper is None:
        _default_mapper = MimeTypeMapper()
    return _default_mapper


def detect_format_from_mime(mime_type: str) -> Optional[DocumentFormat]:
    """Convenience function to detect format from MIME type.
    
    Args:
        mime_type: MIME type string
        
    Returns:
        DocumentFormat if detected, None otherwise
    """
    return get_default_mapper().get_format_from_mime(mime_type)


def detect_format_from_file(file_path: Union[str, Path]) -> Optional[DocumentFormat]:
    """Convenience function to detect format from file.
    
    Args:
        file_path: Path to file
        
    Returns:
        DocumentFormat if detected, None otherwise
    """
    return get_default_mapper().detect_format_from_file(file_path)


def check_mime_support(mime_type: str) -> Tuple[bool, Optional[DocumentFormat]]:
    """Convenience function to check if a MIME type is supported.
    
    Args:
        mime_type: MIME type to check
        
    Returns:
        Tuple of (is_supported, document_format)
        
    Examples:
        >>> supported, fmt = check_mime_support('application/pdf')
        >>> print(f"PDF supported: {supported}")
        PDF supported: True
    """
    return get_default_mapper().check_support(mime_type)
