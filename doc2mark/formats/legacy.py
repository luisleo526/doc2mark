"""Legacy format processors (DOC, XLS, PPT, RTF, PPS) using LibreOffice conversion."""

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Union

from doc2mark.core.base import (
    BaseProcessor,
    ConversionError,
    DocumentFormat,
    ProcessedDocument,
    ProcessingError
)
from doc2mark.ocr.base import BaseOCR

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
                if not result.metadata.extra:
                    result.metadata.extra = {}
                result.metadata.extra['converted_from'] = extension
                result.metadata.extra['converted_to'] = target_format

                return result

        except Exception as e:
            logger.error(f"Failed to process legacy format {extension}: {e}")
            raise ProcessingError(f"Legacy format processing failed: {str(e)}")

    def _find_libreoffice(self) -> Optional[str]:
        """Find LibreOffice installation."""
        # Common paths for LibreOffice
        possible_paths = [
            # macOS
            '/Applications/LibreOffice.app/Contents/MacOS/soffice',
            # Linux
            '/usr/bin/libreoffice',
            '/usr/bin/soffice',
            '/usr/local/bin/libreoffice',
            '/usr/local/bin/soffice',
            # Windows
            'C:\\Program Files\\LibreOffice\\program\\soffice.exe',
            'C:\\Program Files (x86)\\LibreOffice\\program\\soffice.exe',
        ]

        # Check each path
        for path in possible_paths:
            if os.path.exists(path):
                logger.info(f"Found LibreOffice at: {path}")
                return path

        # Try to find in PATH
        try:
            result = subprocess.run(
                ['which', 'libreoffice'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip()
                logger.info(f"Found LibreOffice in PATH: {path}")
                return path
        except Exception:
            pass

        # Try soffice
        try:
            result = subprocess.run(
                ['which', 'soffice'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip()
                logger.info(f"Found soffice in PATH: {path}")
                return path
        except Exception:
            pass

        logger.warning("LibreOffice not found")
        return None

    def _convert_with_libreoffice(
            self,
            input_path: Path,
            target_format: str,
            output_dir: str
    ) -> Path:
        """Convert file using LibreOffice.
        
        Args:
            input_path: Path to input file
            target_format: Target format (e.g., 'docx')
            output_dir: Directory for output file
            
        Returns:
            Path to converted file
            
        Raises:
            ConversionError: If conversion fails
        """
        # Build conversion command
        cmd = [
            self._libreoffice_path,
            '--headless',
            '--convert-to', target_format,
            '--outdir', output_dir,
            str(input_path)
        ]

        logger.info(f"Converting {input_path.name} to {target_format}")

        try:
            # Run conversion
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60  # 60 second timeout
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "Unknown error"
                raise ConversionError(
                    f"LibreOffice conversion failed: {error_msg}"
                )

            # Find converted file
            expected_name = input_path.stem + '.' + target_format
            converted_path = Path(output_dir) / expected_name

            if not converted_path.exists():
                # Sometimes LibreOffice uses different naming
                # Look for any file with the target extension
                files = list(Path(output_dir).glob(f'*.{target_format}'))
                if files:
                    converted_path = files[0]
                else:
                    raise ConversionError(
                        f"Converted file not found: {expected_name}"
                    )

            logger.info(f"Conversion successful: {converted_path}")
            return converted_path

        except subprocess.TimeoutExpired:
            raise ConversionError("LibreOffice conversion timed out")
        except Exception as e:
            raise ConversionError(f"Conversion failed: {str(e)}")

    def check_libreoffice_installed(self) -> bool:
        """Check if LibreOffice is installed and accessible.
        
        Returns:
            True if LibreOffice is available
        """
        return self._libreoffice_path is not None
