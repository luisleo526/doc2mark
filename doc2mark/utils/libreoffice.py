"""Shared LibreOffice (soffice) conversion helper.

Used by both the :class:`LegacyProcessor` (.doc/.xls/.ppt -> modern OOXML) and the
Office image-dominance route (.docx/.pptx -> .pdf). Degrades gracefully when
LibreOffice is not installed: :func:`find_libreoffice` returns ``None`` and callers
fall back to native extraction rather than failing hard.
"""
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional, Union

from doc2mark.core.base import ConversionError

logger = logging.getLogger(__name__)

# Well-known install locations, checked before falling back to PATH lookup.
_CANDIDATE_PATHS = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",   # macOS app bundle
    "/opt/homebrew/bin/soffice",                              # macOS homebrew
    "/usr/bin/libreoffice", "/usr/bin/soffice",               # Linux
    "/usr/local/bin/libreoffice", "/usr/local/bin/soffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",       # Windows
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)


def find_libreoffice() -> Optional[str]:
    """Locate the LibreOffice/soffice binary, or ``None`` if it is not installed."""
    for path in _CANDIDATE_PATHS:
        if os.path.exists(path):
            logger.info(f"Found LibreOffice at: {path}")
            return path
    for name in ("libreoffice", "soffice"):
        try:
            r = subprocess.run(["which", name], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                path = r.stdout.strip()
                logger.info(f"Found {name} in PATH: {path}")
                return path
        except Exception:
            pass
    logger.warning("LibreOffice not found")
    return None


def convert_office_to(
    input_path: Union[str, Path],
    target_format: str,
    output_dir: Union[str, Path],
    timeout: int = 60,
    soffice_path: Optional[str] = None,
) -> Path:
    """Convert ``input_path`` to ``target_format`` (e.g. ``"pdf"``, ``"docx"``) with
    LibreOffice and return the converted file path.

    Args:
        input_path: source document.
        target_format: LibreOffice output filter / extension.
        output_dir: directory the converted file is written to.
        timeout: seconds before the conversion is aborted (large image decks need
            more than the 60s default).
        soffice_path: optional pre-resolved binary path (skips the lookup).

    Raises:
        ConversionError: missing binary, non-zero exit, timeout, or missing output.
    """
    input_path = Path(input_path)
    soffice = soffice_path or find_libreoffice()
    if not soffice:
        raise ConversionError(
            "LibreOffice is required for this conversion but was not found. "
            "Install it from https://www.libreoffice.org/"
        )
    cmd = [soffice, "--headless", "--convert-to", target_format,
           "--outdir", str(output_dir), str(input_path)]
    logger.info(f"Converting {input_path.name} -> {target_format} (timeout={timeout}s)")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ConversionError(f"LibreOffice conversion timed out after {timeout}s")
    if result.returncode != 0:
        raise ConversionError(
            f"LibreOffice conversion failed: {result.stderr or result.stdout or 'unknown error'}"
        )
    expected = Path(output_dir) / (input_path.stem + "." + target_format)
    if expected.exists():
        return expected
    # LibreOffice occasionally names the output differently — take any match.
    matches = list(Path(output_dir).glob(f"*.{target_format}"))
    if matches:
        return matches[0]
    raise ConversionError(f"Converted file not found: {expected.name}")
