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
from doc2mark.core.table import TableStyle, TableRenderer, Cell, TableData
from doc2mark.core.chunker import Chunk, ChunkingConfig, chunk_content

__all__ = [
    # Main loader
    'UnifiedDocumentLoader',

    # Enums
    'DocumentFormat',
    'OutputFormat',
    'TableStyle',

    # Data classes
    'DocumentMetadata',
    'ProcessedDocument',

    # Base classes
    'BaseProcessor',

    # Table rendering
    'TableRenderer',
    'Cell',
    'TableData',

    # Exceptions
    'ProcessingError',
    'UnsupportedFormatError',
    'OCRError',
    'ConversionError',

    # Chunking
    'Chunk',
    'ChunkingConfig',
    'chunk_content',

    # MIME Type Mapping
    'MimeTypeMapper',
    'get_default_mapper',
    'detect_format_from_mime',
    'detect_format_from_file',
    'check_mime_support',
]
