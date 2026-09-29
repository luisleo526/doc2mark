"""Shared LibreOffice (soffice) conversion helper.

Used by both the :class:`LegacyProcessor` (.doc/.xls/.ppt -> modern OOXML) and the
Office image-dominance route (.docx/.pptx -> .pdf). Degrades gracefully when
LibreOffice is not installed: :func:`find_libreoffice` returns ``None`` and callers
fall back to native extraction rather than failing hard.

Every conversion runs with its own throwaway LibreOffice user profile
(``-env:UserInstallation``). Instances that share a profile hand their work to
whichever one started first and exit, so concurrent conversions from
``batch_process(max_workers>1)`` or ``doc2mark --parallel`` used to fail with a
missing output file; separate profiles make them independent.
"""
import logging
import os
import shutil
import signal
import subprocess
import tempfile
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

# One retry absorbs a transient failure (a crashed start-up, a busy disk) without
# doubling the wait on a document that genuinely cannot be converted.
_ATTEMPTS = 2
# Seconds to wait for a timed-out soffice to exit after its process group was killed.
_KILL_WAIT = 10


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

    Each attempt uses a fresh LibreOffice user profile, so any number of conversions
    can run at the same time; a failed attempt is retried once.

    Args:
        input_path: source document.
        target_format: LibreOffice output filter / extension.
        output_dir: directory the converted file is written to.
        timeout: seconds before an attempt is aborted (large image decks need
            more than the 60s default). A timed-out attempt is not retried.
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
    logger.info(f"Converting {input_path.name} -> {target_format} (timeout={timeout}s)")
    error: Optional[ConversionError] = None
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            return _convert_once(soffice, input_path, target_format, Path(output_dir), timeout)
        except _Timeout as exc:
            raise ConversionError(str(exc)) from None
        except ConversionError as exc:
            error = exc
            if attempt < _ATTEMPTS:
                logger.warning(f"LibreOffice conversion of {input_path.name} failed ({exc}); retrying once")
    raise error


class _Timeout(Exception):
    pass


def _convert_once(soffice: str, input_path: Path, target_format: str, output_dir: Path, timeout: int) -> Path:
    profile = tempfile.mkdtemp(prefix="doc2mark-lo-profile-")
    try:
        cmd = [soffice, f"-env:UserInstallation={Path(profile).as_uri()}", "--headless", "--norestore",
               "--convert-to", target_format, "--outdir", str(output_dir), str(input_path)]
        # A process group of its own: on Linux ``soffice`` is a wrapper script around
        # ``soffice.bin``, and a timeout must stop both.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            _reap(proc)
            raise _Timeout(f"LibreOffice conversion timed out after {timeout}s")
        if proc.returncode != 0:
            raise ConversionError(f"LibreOffice conversion failed: {stderr or stdout or 'unknown error'}")
    finally:
        _remove_profile(profile)
    expected = output_dir / (input_path.stem + "." + target_format)
    if expected.exists():
        return expected
    # LibreOffice occasionally names the output differently; accept a single candidate only.
    matches = list(output_dir.glob(f"*.{target_format}"))
    if len(matches) == 1:
        return matches[0]
    raise ConversionError(f"Converted file not found: {expected.name}")


def _reap(proc: subprocess.Popen) -> None:
    """Wait a bounded time for a killed soffice to exit. One stuck in the kernel is left
    behind (with its pipes closed) rather than blocking the caller forever."""
    try:
        proc.communicate(timeout=_KILL_WAIT)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        logger.warning(f"LibreOffice process {proc.pid} was still running {_KILL_WAIT}s after it was killed")


def _remove_profile(path: str) -> None:
    """Delete a conversion's throwaway profile; a failure is logged, never raised over the
    conversion's own result or error."""
    try:
        shutil.rmtree(path)
    except OSError as exc:
        logger.warning(f"Could not remove LibreOffice profile {path}: {exc}")


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
