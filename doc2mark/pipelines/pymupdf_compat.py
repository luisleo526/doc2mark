"""PyMuPDF capabilities newer than the declared floor (``pymupdf>=1.25.3``) that the PDF pipeline uses
when this PyMuPDF has them.

Without one, the pipeline falls back to a weaker measure and says so, once per process, through
:func:`missing`: a warning when the output can differ, an INFO record when only speed does. Every
capability here arrived in PyMuPDF 1.27.1:

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
    ``find_tables()`` runs in a process; stdout is where ``doc2mark`` writes a document (Markdown or
    JSON) when no output file is given. Switch that print off."""
    no_recommend_layout = getattr(pymupdf, "no_recommend_layout", None)
    if callable(no_recommend_layout):
        no_recommend_layout()
