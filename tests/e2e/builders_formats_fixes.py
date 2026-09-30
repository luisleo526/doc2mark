"""Fixture builders for the formats / CLI / loader-API fix tests (``test_formats_fixes.py``).

Nothing from ``doc2mark`` is imported here. Every builder writes to ``path`` and returns it as a ``Path``.
"""

import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict

from docx import Document

from tests.e2e import fake_openai


def write(path: Path, text: str, newline: str = "\n") -> Path:
    """Write ``text`` as UTF-8 to ``path`` (parent folders are created); every ``\\n`` becomes ``newline``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def report_docx(path: Path) -> Path:
    """A Word report: Heading 1, a paragraph, a ruled table whose header has a merged cell (so the table style
    shows), Heading 2 and a paragraph."""
    document = Document()
    document.add_heading("Quarterly report", level=1)
    document.add_paragraph("Revenue grew in every region this quarter.")
    table = document.add_table(rows=3, cols=3)
    table.style = "Table Grid"
    for r, row in enumerate([["Region", "Q1", "Q2"], ["North", "10", "12"], ["South", "7", "9"]]):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    table.cell(0, 1).merge(table.cell(0, 2)).text = "Quarters"
    document.add_heading("Details", level=2)
    document.add_paragraph("Regional notes follow.")
    document.save(str(path))
    return Path(path)


def to_legacy(source: Path, out_dir: Path, fmt: str = "doc", timeout: int = 180) -> Path:
    """Convert ``source`` (.docx) to a legacy ``fmt`` (``doc``) with LibreOffice headless (its own profile dir, so
    parallel runs cannot collide) and return the converted file."""
    soffice = shutil.which("soffice")
    if soffice is None:
        raise FileNotFoundError("soffice is not on PATH")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = out_dir / f"lo-profile-{Path(source).stem}"
    subprocess.run(
        [soffice, f"-env:UserInstallation={profile.resolve().as_uri()}", "--headless",
         "--convert-to", fmt, "--outdir", str(out_dir), str(source)],
        check=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
    )
    converted = out_dir / f"{Path(source).stem}.{fmt}"
    if not converted.exists():
        raise FileNotFoundError(f"LibreOffice did not write {converted}")
    return converted


class HangingOpenAI:
    """An OpenAI-compatible endpoint that accepts every request and never answers it, so a document that needs OCR
    takes as long as the client lets it. Use as a context manager; leaving it releases the waiting requests."""

    def __init__(self):
        release = self._release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                release.wait(timeout=600)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._server.block_on_close = False
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "HangingOpenAI":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._release.set()
        self._server.shutdown()
        self._server.server_close()

    @property
    def env(self) -> Dict[str, str]:
        """Environment for ``run_cli(..., env=...)`` that routes ``--ocr openai`` here."""
        host, port = self._server.server_address[:2]
        return {"OPENAI_BASE_URL": f"http://{host}:{port}/v1", "OPENAI_API_KEY": fake_openai.API_KEY}
