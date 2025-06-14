"""Document format processors for doc2mark."""

from doc2mark.formats.legacy import LegacyProcessor
from doc2mark.formats.markup import MarkupProcessor
from doc2mark.formats.office import OfficeProcessor
from doc2mark.formats.pdf import PDFProcessor
from doc2mark.formats.text import TextProcessor

__all__ = [
    'OfficeProcessor',
    'PDFProcessor',
    'TextProcessor',
    'MarkupProcessor',
    'LegacyProcessor',
]
