"""Internal pipeline modules for doc2mark package."""

from .legacy_converter import LegacyOfficeConverter
# Make key modules easily importable
from .office_advanced_pipeline import office_to_json, office_to_markdown
from .pymupdf_advanced_pipeline import pdf_to_simple_json, pdf_to_markdown

__all__ = [
    'office_to_json',
    'office_to_markdown',
    'LegacyOfficeConverter',
    'pdf_to_simple_json',
    'pdf_to_markdown'
]
