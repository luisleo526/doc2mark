"""PyMuPDF capabilities the PDF pipeline relies on that arrived in PyMuPDF 1.27.1, the declared floor
(``pymupdf>=1.27.1``).

An install that forces an older PyMuPDF still converts: without one of them the pipeline falls back
to a weaker measure and says so, once per process, through :func:`missing` (a warning when the output
can differ, an INFO record when only speed does):

- ``TEXT_CLIP`` (``pdf_images``): what part of a picture its clip paths let the page show;
- ``PDF_REDACT_TEXT_REMOVE_INVISIBLE`` (``pdf_routing``): removing only the invisible glyphs of an
  area that also holds visible text, for the page copy the table finder reads;
- ``TableFinder.textpage`` (the PDF pipeline): the characters ``find_tables()`` already read.
"""

import logging
import threading

import pymupdf

logger = logging.getLogger(__name__)

_warned = set()
_lock = threading.Lock()


def missing(capability: str, consequence: str, level: int = logging.WARNING) -> None:
    """Log, once per process, that this PyMuPDF lacks ``capability`` (PyMuPDF 1.27.1+) and what that
    costs (``consequence``)."""
    with _lock:
        if capability in _warned:
            return
        _warned.add(capability)
    logger.log(level, f"PyMuPDF {pymupdf.VersionBind} has no {capability} (PyMuPDF 1.27.1+): {consequence}. "
                      f"Upgrade PyMuPDF to remove this fallback.")


def quiet_layout_recommendation() -> None:
    """PyMuPDF 1.26.7+ prints a recommendation of its ``pymupdf_layout`` package to stdout the first time
    ``find_tables()`` runs in a process; stdout is where the ``doc2mark`` CLI writes a document (Markdown
    or JSON) when no output file is given. Switch that print off, for the whole process: only the CLI
    calls this, a library user's PyMuPDF is left as PyMuPDF sets it."""
    no_recommend_layout = getattr(pymupdf, "no_recommend_layout", None)
    if callable(no_recommend_layout):
        no_recommend_layout()


def messages_to_log() -> None:
    """PyMuPDF prints its messages to stdout, among them MuPDF's errors about a damaged file (``MuPDF error:
    library error: zlib error: ...``). Send them to the ``pymupdf`` logger as warnings instead, and PyMuPDF's
    debugging log there at DEBUG, for the whole process: only the CLI calls this (see
    :func:`quiet_layout_recommendation`)."""
    for setter, level in (("set_messages", logging.WARNING), ("set_log", logging.DEBUG)):
        route = getattr(pymupdf, setter, None)
        if callable(route):
            route(pylogging_logger=logging.getLogger("pymupdf"), pylogging_level=level)
