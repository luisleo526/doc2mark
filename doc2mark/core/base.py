"""Base classes and interfaces for doc2mark."""

from abc import ABC, abstractmethod
import base64
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class DocumentFormat(Enum):
    """Supported document formats."""
    # Office formats
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"

    # Legacy formats
    DOC = "doc"
    XLS = "xls"
    PPT = "ppt"
    RTF = "rtf"
    PPS = "pps"

    # PDF
    PDF = "pdf"

    # Text/Data formats
    TXT = "txt"
    CSV = "csv"
    TSV = "tsv"
    JSON = "json"
    JSONL = "jsonl"

    # Markup formats
    HTML = "html"
    XML = "xml"
    MARKDOWN = "md"
    
    # Email formats
    EML = "eml"

    # Image formats
    PNG = "png"
    JPG = "jpg"
    JPEG = "jpeg"
    WEBP = "webp"
    TIFF = "tiff"
    TIF = "tif"
    BMP = "bmp"
    GIF = "gif"
    HEIC = "heic"
    HEIF = "heif"
    AVIF = "avif"


class OutputFormat(Enum):
    """Supported output formats."""
    MARKDOWN = "markdown"
    JSON = "json"
    TEXT = "text"


@dataclass
class DocumentMetadata:
    """Metadata for a processed document."""
    filename: str
    format: DocumentFormat
    size_bytes: int
    page_count: Optional[int] = None
    word_count: Optional[int] = None
    language: Optional[str] = None
    creation_date: Optional[str] = None
    modification_date: Optional[str] = None
    author: Optional[str] = None
    title: Optional[str] = None
    # Additional fields for specific formats
    sheet_names: Optional[List[str]] = None  # For XLSX
    slide_count: Optional[int] = None  # For PPTX
    line_count: Optional[int] = None  # For text files
    header_count: Optional[int] = None  # For markdown
    link_count: Optional[int] = None  # For HTML/markdown
    image_count: Optional[int] = None  # For documents with images
    total_cells: Optional[int] = None  # For XLSX
    encoding: Optional[str] = None  # For text files
    delimiter: Optional[str] = None  # For CSV/TSV
    record_count: Optional[int] = None  # For JSONL
    row_count: Optional[int] = None  # For CSV
    column_count: Optional[int] = None  # For CSV
    element_count: Optional[int] = None  # For XML
    root_tag: Optional[str] = None  # For XML
    frontmatter: Optional[Dict[str, Any]] = None  # For markdown
    data_type: Optional[str] = None  # For JSON
    item_count: Optional[int] = None  # For JSON
    extra: Dict[str, Any] = field(default_factory=dict)  # For any additional metadata


def _json_safe(value: Any) -> Any:
    """Convert document values into JSON-serializable data."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


@dataclass
class ProcessedDocument:
    """Container for processed document data."""
    content: str
    metadata: DocumentMetadata
    images: Optional[List[Dict[str, Any]]] = None
    tables: Optional[List[Dict[str, Any]]] = None
    sections: Optional[List[Dict[str, Any]]] = None
    json_content: Optional[List[Dict[str, Any]]] = None  # For UnifiedMarkdownLoader compatibility

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation of the processed document."""
        metadata = asdict(self.metadata)
        metadata["format"] = self.metadata.format.value
        return {
            "content": self.content,
            "metadata": _json_safe(metadata),
            "images": _json_safe(self.images),
            "tables": _json_safe(self.tables),
            "sections": _json_safe(self.sections),
            "json_content": _json_safe(self.json_content),
        }

    def get_chunks(self, config=None):
        """Split document into section-aware chunks for RAG.

        Args:
            config: Optional ``ChunkingConfig``.  Uses defaults when ``None``.

        Returns:
            List of ``Chunk`` objects.
        """
        from doc2mark.core.chunker import chunk_content, ChunkingConfig, Chunk
        if not self.json_content:
            return [Chunk(content=self.content, chunk_index=0)]
        return chunk_content(self.json_content, config)

    @property
    def markdown(self) -> str:
        """Get content as markdown."""
        return self.content

    @property
    def text(self) -> str:
        """Get content as plain text."""
        # Simple conversion - could be enhanced
        import re
        text = self.content
        # Remove markdown formatting
        text = re.sub(r'#+ ', '', text)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        return text


class BaseProcessor(ABC):
    """Base class for document processors."""

    @abstractmethod
    def can_process(self, file_path: Union[str, Path]) -> bool:
        """Check if this processor can handle the given file."""
        pass

    @abstractmethod
    def process(
            self,
            file_path: Union[str, Path],
            **kwargs
    ) -> ProcessedDocument:
        """Process the document and return structured data."""
        pass


class ProcessingError(Exception):
    """Base exception for document processing errors."""
    pass


class UnsupportedFormatError(ProcessingError):
    """Raised when attempting to process an unsupported format."""
    pass


class OCRError(ProcessingError):
    """Raised when OCR processing fails."""
    pass


class ConversionError(ProcessingError):
    """Raised when document conversion fails."""
    pass
