"""Shared content-based OCR strategy decision — used by every format pipeline.

A document is processed by one of two strategies, decided from two per-document
signals (mean image coverage + mean selectable-text density):

- ``"image"``: the document is mostly pictures with no usable text layer (slide
  decks, scans). It is rendered page-by-page and OCR'd as whole images, with the
  ``page_markdown`` synthesis producing structured Markdown.
- ``"text"``:  the document has a usable text layer (or little image coverage).
  The deterministic rule-based layer (text + tables, verbatim for BM42 sparse
  retrieval) is authoritative; embedded figures are OCR'd individually.

Keeping the thresholds and the decision here is the single source of truth so the
PDF and Office routes never diverge.
"""
from typing import Literal

# A page is "image-like" when raster images cover at least this fraction of it AND
# it carries fewer than this many selectable-text characters (i.e. no real text
# layer). Text density is the decisive signal — coverage alone misclassifies a
# text document that happens to carry large figures.
IMAGE_PAGE_COVERAGE = 0.55
IMAGE_PAGE_TEXT_LIMIT = 200

# Text-layer QUALITY gate. A selectable-text layer can exist yet be untrustworthy:
# designed/print PDFs often draw prominent text (titles) with subset fonts that
# carry no ToUnicode map, so extraction yields replacement chars (U+FFFD) — the
# visible page is faithful but the text layer is not. When such an image-dominant
# page has a headline that is at least this fraction unmappable, the render is
# authoritative and we OCR it instead of trusting the broken text layer.
ILLEGIBLE_TEXT_RATIO = 0.3


def decide_doc_strategy(
    mean_image_coverage: float,
    mean_text_chars_per_page: float,
    text_illegibility: float = 0.0,
) -> Literal["image", "text"]:
    """Return the document-level OCR strategy from per-document signals.

    Routes to ``"image"`` when the document is image-dominant
    (``mean_image_coverage >= IMAGE_PAGE_COVERAGE``) AND *either*:

    - it has little selectable text
      (``mean_text_chars_per_page < IMAGE_PAGE_TEXT_LIMIT``), i.e. no real text
      layer; **or**
    - its text layer is low quality (``text_illegibility >= ILLEGIBLE_TEXT_RATIO``),
      i.e. the prominent text cannot be decoded (U+FFFD) and the render must be
      trusted instead.

    Otherwise ``"text"``. The quality gate is deliberately scoped to image-dominant
    pages: a low-coverage text document with the odd unmappable glyph stays on the
    (lossless) text path rather than being forced through whole-document OCR.
    """
    if mean_image_coverage >= IMAGE_PAGE_COVERAGE and (
        mean_text_chars_per_page < IMAGE_PAGE_TEXT_LIMIT
        or text_illegibility >= ILLEGIBLE_TEXT_RATIO
    ):
        return "image"
    return "text"
