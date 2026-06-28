"""Legacy format processors (DOC, XLS, PPT, RTF, PPS) using LibreOffice conversion."""

import logging
import tempfile
from pathlib import Path
from typing import Optional, Union

from doc2mark.core.base import (
    BaseProcessor,
    DocumentFormat,
    ProcessedDocument,
    ProcessingError
)
from doc2mark.ocr.base import BaseOCR
from doc2mark.utils.libreoffice import find_libreoffice, convert_office_to

logger = logging.getLogger(__name__)


class LegacyProcessor(BaseProcessor):
    """Processor for legacy Office formats using LibreOffice conversion."""

    def __init__(self, ocr: Optional[BaseOCR] = None):
        """Initialize legacy processor.
        
        Args:
            ocr: OCR provider for image extraction
        """
        self.ocr = ocr
        self._office_processor = None
        self._libreoffice_path = self._find_libreoffice()

    @property
    def office_processor(self):
        """Lazy load office processor for converted files."""
        if self._office_processor is None:
            from doc2mark.formats.office import OfficeProcessor
            self._office_processor = OfficeProcessor(ocr=self.ocr)
        return self._office_processor

    def can_process(self, file_path: Union[str, Path]) -> bool:
        """Check if this processor can handle the file."""
        file_path = Path(file_path)
        extension = file_path.suffix.lower().lstrip('.')
        return extension in ['doc', 'xls', 'ppt', 'rtf', 'pps']

    def process(
            self,
            file_path: Union[str, Path],
            **kwargs
    ) -> ProcessedDocument:
        """Process legacy document by converting to modern format."""
        file_path = Path(file_path)
        extension = file_path.suffix.lower().lstrip('.')

        # Check if LibreOffice is available
        if not self._libreoffice_path:
            raise ProcessingError(
                "LibreOffice is required to process legacy formats. "
                "Please install LibreOffice from https://www.libreoffice.org/"
            )

        # Get file size before conversion
        file_size = file_path.stat().st_size

        # Determine target format
        format_mapping = {
            'doc': ('docx', DocumentFormat.DOC),
            'xls': ('xlsx', DocumentFormat.XLS),
            'ppt': ('pptx', DocumentFormat.PPT),
            'pps': ('pptx', DocumentFormat.PPS),
            'rtf': ('docx', DocumentFormat.RTF)
        }

        if extension not in format_mapping:
            raise ProcessingError(f"Unsupported legacy format: {extension}")

        target_format, doc_format = format_mapping[extension]

        # Convert file
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                # Convert using LibreOffice
                converted_path = self._convert_with_libreoffice(
                    file_path,
                    target_format,
                    temp_dir
                )

                # Process converted file
                result = self.office_processor.process(converted_path, **kwargs)

                # Update metadata to reflect original format
                result.metadata.format = doc_format
                result.metadata.filename = file_path.name
                result.metadata.size_bytes = file_size

                # Add conversion note
                if result.metadata.extra is None:
                    result.metadata.extra = {}
                result.metadata.extra['converted_from'] = extension
                result.metadata.extra['converted_to'] = target_format

                return result

        except Exception as e:
            logger.error(f"Failed to process legacy format {extension}: {e}")
            raise ProcessingError(f"Legacy format processing failed: {str(e)}")

    def _find_libreoffice(self) -> Optional[str]:
        """Find the LibreOffice/soffice binary (delegates to the shared helper)."""
        return find_libreoffice()

    def _convert_with_libreoffice(
            self,
            input_path: Path,
            target_format: str,
            output_dir: str
    ) -> Path:
        """Convert a file with LibreOffice (delegates to the shared helper)."""
        return convert_office_to(
            input_path, target_format, output_dir, soffice_path=self._libreoffice_path
        )

    def check_libreoffice_installed(self) -> bool:
        """Check if LibreOffice is installed and accessible.
        
        Returns:
            True if LibreOffice is available
        """
        return self._libreoffice_path is not None
