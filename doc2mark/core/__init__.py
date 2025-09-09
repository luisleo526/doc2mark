"""Core components for doc2mark."""

from doc2mark.core.base import (
    DocumentFormat,
    OutputFormat,
    DocumentMetadata,
    ProcessedDocument,
    BaseProcessor,
    ProcessingError,
    UnsupportedFormatError,
    OCRError,
    ConversionError
)
from doc2mark.core.loader import UnifiedDocumentLoader
from doc2mark.core.mime_mapper import (
    MimeTypeMapper,
    get_default_mapper,
    detect_format_from_mime,
    detect_format_from_file,
    check_mime_support
)

__all__ = [
    # Main loader
    'UnifiedDocumentLoader',

    # Enums
    'DocumentFormat',
    'OutputFormat',

    # Data classes
    'DocumentMetadata',
    'ProcessedDocument',

    # Base classes
    'BaseProcessor',

    # Exceptions
    'ProcessingError',
    'UnsupportedFormatError',
    'OCRError',
    'ConversionError',
    
    # MIME Type Mapping
    'MimeTypeMapper',
    'get_default_mapper',
    'detect_format_from_mime',
    'detect_format_from_file',
    'check_mime_support',
]
