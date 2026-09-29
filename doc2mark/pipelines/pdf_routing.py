"""Per-page OCR-routing signals of a PDF page, measured with PyMuPDF.

What the signals mean, the thresholds and the decisions live in
:mod:`doc2mark.core.strategy`; this module only measures one page:

- raster image coverage: the union of the image rectangles clipped to the visible
  page (CropBox), inline (BI/ID/EI) images included;
- the painted and the invisible (render mode 3, fully transparent) text, span by span;
- on pages without a usable text layer, the ink that neither the text layer nor the
  pictures the text route OCRs account for (vector-outlined text, inline images).

:class:`VisibleTextPage` hides invisible text from the text extractors on pages where
that text is not the page's content.
"""
import logging
import math
from dataclasses import dataclass, replace
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple

import pymupdf

from doc2mark.core.strategy import (
    IMAGE_PAGE_COVERAGE,
    NO_TEXT_LIMIT,
    LegibilityJudge,
    PageSignals,
    judge_text_layer,
    text_layer_stats,
)

logger = logging.getLogger(__name__)

#: Text-extraction flags of the pipeline: unmapped glyphs come out as U+FFFD.
TEXT_FLAGS = pymupdf.TEXT_PRESERVE_LIGATURES

_FILLED = 16    # FZ_STEXT_FILLED in span["char_flags"] (PyMuPDF >= 1.25.2)
_STROKED = 32   # FZ_STEXT_STROKED
_INK_DPI = 36
_INK_LEVEL = 128  # a grey level darker than this is ink
_LIGHT_LEVELS = bytes(range(_INK_LEVEL))

_char_flags_mark_painting: Optional[bool] = None


def char_flags_mark_painting() -> bool:
    """Whether this PyMuPDF reports in ``span["char_flags"]`` if a span is filled or stroked (1.25.2+).

    Checked once on a scratch page; older versions fall back to the page's text trace.
    """
    global _char_flags_mark_painting
    if _char_flags_mark_painting is None:
        try:
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((72, 72), "probe")
            spans = [span for block in page.get_text("dict")["blocks"] for line in block.get("lines", [])
                     for span in line.get("spans", [])]
            doc.close()
            _char_flags_mark_painting = bool(spans) and all(
                span.get("char_flags", 0) & (_FILLED | _STROKED) for span in spans)
        except Exception as exc:
            logger.debug(f"char_flags probe failed: {exc}")
            _char_flags_mark_painting = False
    return _char_flags_mark_painting


def _origin_key(point) -> Tuple[float, float]:
    return round(float(point[0]), 1), round(float(point[1]), 1)


def invisible_trace_origins(page) -> Set[Tuple[float, float]]:
    """Origins of the glyphs the text trace reports as invisible (render mode 3 or opacity 0).

    Only needed on PyMuPDF versions whose spans carry no ``char_flags``.
    """
    origins: Set[Tuple[float, float]] = set()
    try:
        for span in page.get_texttrace():
            if span.get("type") == 3 or span.get("opacity", 1) == 0:
                origins.update(_origin_key(char[2]) for char in span.get("chars", ()))
    except Exception as exc:
        logger.debug(f"get_texttrace failed on page {page.number + 1}: {exc}")
    return origins


def span_is_invisible(span: dict, trace_origins: Optional[Set[Tuple[float, float]]] = None) -> bool:
    """True for a ``dict``/``rawdict`` span that is not painted: render mode 3 (or 7), or zero alpha."""
    if span.get("alpha", 255) == 0:
        return True
    if "char_flags" in span and char_flags_mark_painting():
        return not span["char_flags"] & (_FILLED | _STROKED)
    if trace_origins:
        return _origin_key(span.get("origin", (math.nan, math.nan))) in trace_origins
    return False


def _union_bbox(bboxes: Iterable[Sequence[float]]) -> Tuple[float, float, float, float]:
    rect = pymupdf.Rect()
    for bbox in bboxes:
        rect |= pymupdf.Rect(bbox)
    return tuple(rect)


def drop_invisible_text(text_dict: dict, trace_origins: Optional[Set[Tuple[float, float]]] = None) -> dict:
    """A copy of a ``get_text("dict" | "rawdict")`` result without its invisible spans.

    Lines and text blocks left empty are removed; the bounding boxes of the ones that
    lost spans are recomputed from what remains.
    """
    blocks = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            blocks.append(block)
            continue
        lines, changed = [], False
        for line in block.get("lines", []):
            spans = [span for span in line.get("spans", []) if not span_is_invisible(span, trace_origins)]
            if len(spans) == len(line.get("spans", [])):
                lines.append(line)
                continue
            changed = True
            if spans:
                lines.append({**line, "spans": spans, "bbox": _union_bbox(span["bbox"] for span in spans)})
        if not changed:
            blocks.append(block)
        elif lines:
            blocks.append({**block, "lines": lines, "bbox": _union_bbox(line["bbox"] for line in lines)})
    return {**text_dict, "blocks": blocks}


class VisibleTextPage:
    """A PyMuPDF page whose ``get_text("dict" | "rawdict")`` leaves out invisible text.

    The pipeline hands this wrapper to its text and table extractors on pages whose
    invisible text is not content (hidden text; the OCR layer of a scan that is OCR'd
    from its render), so that text never reaches the output. Everything else is
    delegated to the real page.
    """

    def __init__(self, page, trace_origins: Optional[Set[Tuple[float, float]]] = None):
        self._page = page
        self._trace_origins = trace_origins

    def __getattr__(self, name):
        return getattr(self._page, name)

    def get_text(self, option: str = "text", **kwargs):
        result = self._page.get_text(option, **kwargs)
        if option in ("dict", "rawdict") and isinstance(result, dict):
            return drop_invisible_text(result, self._trace_origins)
        return result


def page_area(page) -> pymupdf.Rect:
    """The visible page (CropBox) in the frame of image and text coordinates (unrotated, CropBox origin)."""
    return pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)


def union_area(rects: Iterable[pymupdf.Rect]) -> float:
    """Area covered by the union of axis-aligned rectangles (overlaps counted once)."""
    rects = [rect for rect in rects if not rect.is_empty]
    xs = sorted({rect.x0 for rect in rects} | {rect.x1 for rect in rects})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        spans = sorted((rect.y0, rect.y1) for rect in rects if rect.x0 <= left and rect.x1 >= right)
        covered, top, bottom = 0.0, None, None
        for y0, y1 in spans:
            if bottom is None or y0 > bottom:
                if bottom is not None:
                    covered += bottom - top
                top, bottom = y0, y1
            else:
                bottom = max(bottom, y1)
        if bottom is not None:
            covered += bottom - top
        area += covered * (right - left)
    return area


def image_rects(page) -> List[pymupdf.Rect]:
    """Visible part of every raster image placement (image XObjects, Form XObject contents, inline images)."""
    area = page_area(page)
    rects = []
    for info in page.get_image_info():
        rect = pymupdf.Rect(info["bbox"]) & area
        if not rect.is_empty:
            rects.append(rect)
    return rects


def image_coverage(page, rects: Optional[List[pymupdf.Rect]] = None) -> float:
    """Share of the visible page covered by raster images: union of the placements, clipped to the page."""
    area = page_area(page)
    page_size = abs(area.width * area.height) or 1.0
    return min(union_area(image_rects(page) if rects is None else rects) / page_size, 1.0)


def uncaptured_ink(page, blank: Iterable[pymupdf.Rect]) -> float:
    """Share of the page showing ink outside ``blank`` (areas the text route already captures), measured on a
    low-resolution grey render."""
    zoom = _INK_DPI / 72
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    to_pixels = page.rotation_matrix * pymupdf.Matrix(zoom, zoom)
    for rect in blank:
        box = (pymupdf.Rect(rect) * to_pixels).irect & pix.irect
        if not box.is_empty:
            pix.set_rect(box, (255,))
    samples = pix.samples
    if not samples:
        return 0.0
    return (len(samples) - len(samples.translate(None, _LIGHT_LEVELS))) / len(samples)


@dataclass(frozen=True)
class PageMeasure:
    """Routing signals of one page, plus what the text path needs to honour them."""

    signals: PageSignals
    has_invisible_text: bool = False
    trace_origins: Optional[frozenset] = None  # invisible-glyph origins, only without char_flags support


def _span_rows(text_dict: dict, trace_origins) -> Tuple[List[Tuple[str, float, tuple]], List[Tuple[str, float, tuple]],
                                                        List[str], List[str]]:
    visible, invisible, visible_lines, invisible_lines = [], [], [], []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            shown, hidden = [], []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text:
                    continue
                row = (text, float(span.get("size", 0.0)), tuple(span.get("bbox", (0, 0, 0, 0))))
                if span_is_invisible(span, trace_origins):
                    invisible.append(row)
                    hidden.append(text)
                else:
                    visible.append(row)
                    shown.append(text)
            if shown:
                visible_lines.append("".join(shown))
            if hidden:
                invisible_lines.append("".join(hidden))
    return visible, invisible, visible_lines, invisible_lines


def _share_over(rows: Sequence[Tuple[str, float, tuple]], rects: Sequence[pymupdf.Rect]) -> float:
    """Share of the characters of ``rows`` whose span centre lies inside one of ``rects``."""
    total = inside = 0
    for text, _, bbox in rows:
        count = len(text.strip())
        centre = pymupdf.Rect(bbox)
        centre = pymupdf.Point((centre.x0 + centre.x1) / 2, (centre.y0 + centre.y1) / 2)
        total += count
        if any(centre in rect for rect in rects):
            inside += count
    return inside / total if total else 0.0


def measure_page(page, *, legibility_judge: Optional[LegibilityJudge] = None,
                 ocr_rects: Callable[[object], List[pymupdf.Rect]] = lambda page: []) -> PageMeasure:
    """Measure the routing signals of ``page``.

    ``ocr_rects(page)`` returns the rectangles of the pictures the text route would OCR
    one by one; the uncaptured-ink measure ignores them, as it ignores the text spans.
    """
    trace_origins = None if char_flags_mark_painting() else frozenset(invisible_trace_origins(page))
    text_dict = page.get_text("dict", flags=TEXT_FLAGS)
    visible, invisible, visible_lines, invisible_lines = _span_rows(text_dict, trace_origins)
    rects = image_rects(page)
    coverage = image_coverage(page, rects)
    visible_stats = text_layer_stats((text, size) for text, size, _ in visible)
    ink = None
    if visible_stats.weight < NO_TEXT_LIMIT and coverage < IMAGE_PAGE_COVERAGE:
        try:
            ink = uncaptured_ink(page, [pymupdf.Rect(bbox) for _, _, bbox in visible] + list(ocr_rects(page)))
        except Exception as exc:
            logger.debug(f"Ink measure failed on page {page.number + 1}: {exc}")
    signals = PageSignals(
        image_coverage=coverage,
        visible=visible_stats,
        invisible=text_layer_stats((text, size) for text, size, _ in invisible),
        invisible_over_images=_share_over(invisible, rects),
        uncaptured_ink=ink,
    )
    layer_text = "\n".join(invisible_lines if signals.searchable_scan else visible_lines)
    verdict = judge_text_layer(legibility_judge, signals.text_layer, layer_text)
    if verdict is not None:
        signals = replace(signals, judge_legibility=verdict)
    has_invisible = any(text.strip() for text, _, _ in invisible)
    return PageMeasure(signals=signals, has_invisible_text=has_invisible,
                       trace_origins=trace_origins if has_invisible else None)


def describe_pages(page_numbers: Sequence[int], limit: int = 10) -> str:
    """``"pages 2, 5 and 9"`` for 0-based page indexes (at most ``limit`` listed)."""
    shown = [str(number + 1) for number in page_numbers[:limit]]
    more = len(page_numbers) - len(shown)
    if more > 0:
        shown.append(f"{more} more")
    if len(shown) == 1:
        return f"page {shown[0]}"
    return f"pages {', '.join(shown[:-1])} and {shown[-1]}"
