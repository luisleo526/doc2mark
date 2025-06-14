#!/usr/bin/env python3
"""
Legacy Office Format Converter

Converts old-style Office documents (.doc, .ppt, .xls) to modern formats
(.docx, .pptx, .xlsx) using LibreOffice in headless mode.

Prerequisites:
    - LibreOffice must be installed on the system
    - Linux: sudo apt-get install libreoffice
    - macOS: brew install libreoffice
    - Windows: Download from https://www.libreoffice.org/
"""

import logging
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Union, Optional

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class LegacyOfficeConverter:
    """Convert legacy Office formats to modern formats using LibreOffice"""

    # Mapping of old extensions to new ones
    CONVERSION_MAP = {
        '.doc': 'docx',
        '.ppt': 'pptx',
        '.xls': 'xlsx',
        '.rtf': 'docx',  # Rich Text Format to DOCX
        '.pps': 'pptx',  # PowerPoint Show to PPTX
    }

    def __init__(self):
        self.libreoffice_path = self._find_libreoffice()
        if not self.libreoffice_path:
            raise RuntimeError("LibreOffice not found. Please install LibreOffice.")

    def _find_libreoffice(self) -> Optional[str]:
        """Find LibreOffice executable on the system"""
        system = platform.system()

        # Common paths for LibreOffice
        if system == "Darwin":  # macOS
            paths = [
                "/Applications/LibreOffice.app/Contents/MacOS/soffice",
                "/usr/local/bin/soffice",
                "soffice"
            ]
        elif system == "Windows":
            paths = [
                r"C:\Program Files\LibreOffice\program\soffice.exe",
                r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                "soffice.exe"
            ]
        else:  # Linux
            paths = [
                "/usr/bin/soffice",
                "/usr/bin/libreoffice",
                "soffice",
                "libreoffice"
            ]

        # Check each path
        for path in paths:
            if shutil.which(path):
                logger.info(f"Found LibreOffice at: {path}")
                return path

        return None

    def convert_file(self,
                     input_path: Union[str, Path],
                     output_path: Optional[Union[str, Path]] = None,
                     timeout: int = 60) -> Path:
        """
        Convert a legacy Office file to modern format
        
        Args:
            input_path: Path to the legacy file
            output_path: Optional output path (default: same dir, new extension)
            timeout: Conversion timeout in seconds
            
        Returns:
            Path to the converted file
        """
        input_path = Path(input_path)

        if not input_path.exists():
            raise FileNotFoundError(f"File not found: {input_path}")

        # Check if conversion is needed
        ext = input_path.suffix.lower()
        if ext not in self.CONVERSION_MAP:
            raise ValueError(f"Unsupported format: {ext}. Supported: {list(self.CONVERSION_MAP.keys())}")

        # Determine output format and path
        output_format = self.CONVERSION_MAP[ext]

        if output_path:
            output_path = Path(output_path)
        else:
            # Default: same directory, new extension
            output_path = input_path.parent / f"{input_path.stem}.{output_format}"

        # Build LibreOffice command
        cmd = [
            self.libreoffice_path,
            "--headless",
            "--convert-to", output_format,
            "--outdir", str(output_path.parent),
            str(input_path)
        ]

        logger.info(f"Converting {input_path.name} to {output_format}...")

        try:
            # Run conversion
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            if result.returncode != 0:
                raise RuntimeError(f"Conversion failed: {result.stderr}")

            # LibreOffice creates the file with the same stem
            expected_output = output_path.parent / f"{input_path.stem}.{output_format}"

            if not expected_output.exists():
                raise RuntimeError(f"Conversion succeeded but output file not found: {expected_output}")

            # Rename if different output path was specified
            if output_path != expected_output:
                expected_output.rename(output_path)

            logger.info(f"Successfully converted to: {output_path}")
            return output_path

        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Conversion timed out after {timeout} seconds")
        except Exception as e:
            logger.error(f"Conversion failed: {e}")
            raise

    def convert_directory(self,
                          input_dir: Union[str, Path],
                          output_dir: Optional[Union[str, Path]] = None,
                          recursive: bool = False) -> dict:
        """
        Convert all legacy Office files in a directory
        
        Args:
            input_dir: Directory containing legacy files
            output_dir: Optional output directory (default: same as input)
            recursive: Whether to process subdirectories
            
        Returns:
            Dict mapping input paths to output paths
        """
        input_dir = Path(input_dir)
        output_dir = Path(output_dir) if output_dir else input_dir

        if not input_dir.exists():
            raise FileNotFoundError(f"Directory not found: {input_dir}")

        # Find all legacy files
        legacy_extensions = list(self.CONVERSION_MAP.keys())
        pattern = "**/*" if recursive else "*"

        conversions = {}

        for ext in legacy_extensions:
            for file_path in input_dir.glob(f"{pattern}{ext}"):
                if file_path.is_file():
                    try:
                        # Calculate relative path for output
                        rel_path = file_path.relative_to(input_dir)
                        output_path = output_dir / rel_path.parent / f"{file_path.stem}.{self.CONVERSION_MAP[ext]}"

                        # Ensure output directory exists
                        output_path.parent.mkdir(parents=True, exist_ok=True)

                        # Convert file
                        converted_path = self.convert_file(file_path, output_path)
                        conversions[str(file_path)] = str(converted_path)

                    except Exception as e:
                        logger.error(f"Failed to convert {file_path}: {e}")
                        conversions[str(file_path)] = None

        return conversions
