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


def decide_doc_strategy(
    mean_image_coverage: float,
    mean_text_chars_per_page: float,
) -> Literal["image", "text"]:
    """Return the document-level OCR strategy from two per-document means.

    ``"image"`` iff images cover the pages (``mean_image_coverage >=
    IMAGE_PAGE_COVERAGE``) AND there is little selectable text
    (``mean_text_chars_per_page < IMAGE_PAGE_TEXT_LIMIT``); otherwise ``"text"``.
    """
    if (mean_image_coverage >= IMAGE_PAGE_COVERAGE
            and mean_text_chars_per_page < IMAGE_PAGE_TEXT_LIMIT):
        return "image"
    return "text"
