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
]
