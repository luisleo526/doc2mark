"""Per-page OCR-routing signals of a PDF page, measured with PyMuPDF.

What the signals mean, the thresholds and the decisions live in
:mod:`doc2mark.core.strategy`; this module only measures one page:

- raster image coverage: the union of the image rectangles clipped to the visible
  page (CropBox), inline (BI/ID/EI) images included;
- the painted text, and the invisible (render mode 3, fully transparent) text, which
  is either the text of what the page shows (the render has ink under it: a scanner's
  OCR layer, a transparent copy of text baked into artwork) or hidden text;
- on pages without a usable text layer, what the text route cannot capture: pictures
  it does not OCR one by one (inline images, picture tiles) and other ink (vector
  outlines).

:func:`text_source` hands the text and table extractors the page without the
invisible text that must not become content.
"""
import logging
import math
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple

import pymupdf

from doc2mark.core.strategy import (
    IMAGE_PAGE_COVERAGE,
    MIN_LAYER_INK,
    NO_TEXT_LIMIT,
    PageSignals,
    text_layer_stats,
)

logger = logging.getLogger(__name__)

#: Text-extraction flags of the pipeline: unmapped glyphs come out as U+FFFD.
TEXT_FLAGS = pymupdf.TEXT_PRESERVE_LIGATURES

_FILLED = 16    # FZ_STEXT_FILLED in span["char_flags"] (PyMuPDF >= 1.25.2)
_STROKED = 32   # FZ_STEXT_STROKED
_INK_DPI = 72
_INK_CONTRAST = 48       # grey levels a pixel must differ from its background to count as ink
_MAX_EXACT_UNION = 256   # beyond this many rectangles the union is measured on a raster

Bbox = Tuple[float, float, float, float]
Row = Tuple[str, float, Bbox]   # (span text, font size, bbox)

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


def _centre_in(bbox: Sequence[float], rects: Sequence[pymupdf.Rect]) -> bool:
    centre = pymupdf.Point((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    return any(centre in rect for rect in rects)


def _union_bbox(bboxes: Iterable[Sequence[float]]) -> Bbox:
    rect = pymupdf.Rect()
    for bbox in bboxes:
        rect |= pymupdf.Rect(bbox)
    return tuple(rect)


def drop_invisible_text(text_dict: dict, keep_rects: Sequence[pymupdf.Rect] = (),
                        trace_origins: Optional[Set[Tuple[float, float]]] = None) -> dict:
    """A copy of a ``get_text("dict" | "rawdict")`` result without its invisible spans, except
    those centred in ``keep_rects``.

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
            spans = [span for span in line.get("spans", [])
                     if not span_is_invisible(span, trace_origins) or _centre_in(span["bbox"], keep_rects)]
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
    """A PyMuPDF page whose ``get_text("dict" | "rawdict")`` leaves out invisible text,
    except invisible text centred in ``keep_rects``. Everything else is delegated to
    the real page."""

    def __init__(self, page, keep_rects: Sequence[pymupdf.Rect] = (),
                 trace_origins: Optional[Set[Tuple[float, float]]] = None):
        self._page = page
        self._keep_rects = [pymupdf.Rect(rect) for rect in keep_rects]
        self._trace_origins = trace_origins

    def __getattr__(self, name):
        return getattr(self._page, name)

    def get_text(self, option: str = "text", **kwargs):
        result = self._page.get_text(option, **kwargs)
        if option in ("dict", "rawdict") and isinstance(result, dict):
            return drop_invisible_text(result, self._keep_rects, self._trace_origins)
        return result


def page_area(page) -> pymupdf.Rect:
    """The visible page (CropBox) in the frame of image and text coordinates (unrotated, CropBox origin)."""
    return pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)


def union_area(rects: Iterable[pymupdf.Rect], bounds: Optional[pymupdf.Rect] = None) -> float:
    """Area covered by the union of axis-aligned rectangles (overlaps counted once).

    Exact for up to ``_MAX_EXACT_UNION`` rectangles; above that (tiled scans) measured on
    a raster of ``bounds`` with at most 1000 pixels a side.
    """
    rects = [rect for rect in rects if not rect.is_empty]
    if len(rects) > _MAX_EXACT_UNION and bounds is not None and not bounds.is_empty:
        scale = min(1.0, 1000 / max(bounds.width, bounds.height))
        grid = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, math.ceil(bounds.width * scale),
                                                            math.ceil(bounds.height * scale)), False)
        grid.clear_with(255)
        to_grid = pymupdf.Matrix(1, 0, 0, 1, -bounds.x0, -bounds.y0) * pymupdf.Matrix(scale, scale)
        for rect in rects:
            box = (rect * to_grid).irect & grid.irect
            if not box.is_empty:
                grid.set_rect(box, (0,))
        samples = grid.samples
        return (len(samples) - len(samples.translate(None, b"\x00"))) / (scale * scale)
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
    return min(union_area(image_rects(page) if rects is None else rects, area) / page_size, 1.0)


def _same_rect(a: pymupdf.Rect, b: pymupdf.Rect, tolerance: float = 1.0) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def uncaptured_raster(page, rects: List[pymupdf.Rect], ocr_rects: Iterable[pymupdf.Rect]) -> float:
    """Share of the page covered by pictures the text route does not OCR one by one: inline
    images and placements left out of ``ocr_rects`` (decorative-size tiles)."""
    area = page_area(page)
    page_size = abs(area.width * area.height) or 1.0
    ocr_visible = [pymupdf.Rect(rect) & area for rect in ocr_rects]
    left_out = [rect for rect in rects if not any(_same_rect(rect, done) for done in ocr_visible)]
    return min(union_area(left_out, area) / page_size, 1.0)


def _grey_render(page) -> Tuple[pymupdf.Pixmap, pymupdf.Matrix]:
    zoom = _INK_DPI / 72
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    return pix, page.rotation_matrix * pymupdf.Matrix(zoom, zoom)


def _background(samples: bytes) -> int:
    """The most common grey level (the background)."""
    return Counter(samples[::7] or samples).most_common(1)[0][0]


def _ink_share(samples: bytes, background: int) -> float:
    """Share of pixels differing from ``background`` by more than ``_INK_CONTRAST`` levels (any colour)."""
    if not samples:
        return 0.0
    near = bytes(range(max(0, background - _INK_CONTRAST), min(255, background + _INK_CONTRAST) + 1))
    return len(samples.translate(None, near)) / len(samples)


def _paint(pix: pymupdf.Pixmap, rects: Iterable[Sequence[float]], to_pixels: pymupdf.Matrix, level: int) -> None:
    for rect in rects:
        box = (pymupdf.Rect(rect) * to_pixels).irect & pix.irect
        if not box.is_empty:
            pix.set_rect(box, (level,))


def uncaptured_ink(page, blank: Iterable[Sequence[float]]) -> float:
    """Share of the page showing ink (any colour) outside ``blank`` (areas the text route already
    captures), measured on a grey render."""
    pix, to_pixels = _grey_render(page)
    background = _background(pix.samples)
    _paint(pix, blank, to_pixels, background)
    return _ink_share(pix.samples, background)


def _box_samples(samples: bytes, stride: int, box: pymupdf.IRect) -> bytes:
    return b"".join(samples[y * stride + box.x0:y * stride + box.x1] for y in range(box.y0, box.y1))


def split_invisible(page, invisible: Sequence[Row], visible: Sequence[Row]) -> Tuple[List[Row], List[Row]]:
    """Split invisible spans into (text layer, hidden text): a span is the text of what the page
    shows when the render, painted text left out, has ink under it (``MIN_LAYER_INK``)."""
    if not invisible:
        return [], []
    pix, to_pixels = _grey_render(page)
    _paint(pix, [bbox for _, _, bbox in visible], to_pixels, _background(pix.samples))
    samples, stride = pix.samples, pix.stride
    layer, hidden = [], []
    for row in invisible:
        box = (pymupdf.Rect(row[2]) * to_pixels).irect & pix.irect
        part = _box_samples(samples, stride, box) if not box.is_empty else b""
        if part and _ink_share(part, _background(part)) >= MIN_LAYER_INK:
            layer.append(row)
        else:
            hidden.append(row)
    return layer, hidden


@dataclass(frozen=True)
class PageMeasure:
    """Routing signals of one page, plus what the text path needs to honour them."""

    signals: PageSignals
    layer_rects: Tuple[Bbox, ...] = ()     # invisible spans over ink: the text of what the page shows
    hidden_rects: Tuple[Bbox, ...] = ()    # invisible spans over nothing visible: hidden text
    visible_rects: Tuple[Bbox, ...] = ()   # painted spans (kept only on pages with invisible text)
    trace_origins: Optional[frozenset] = None  # invisible-glyph origins, only without char_flags support
    text: Optional[str] = None             # the page's text layer, for the optional legibility judge

    @property
    def has_invisible_text(self) -> bool:
        return bool(self.layer_rects or self.hidden_rects)


def _span_rows(text_dict: dict, trace_origins) -> Tuple[List[Row], List[Row], List[List[Row]]]:
    visible, invisible, lines = [], [], []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            rows = []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text:
                    continue
                row = (text, float(span.get("size", 0.0)), tuple(span.get("bbox", (0, 0, 0, 0))))
                (invisible if span_is_invisible(span, trace_origins) else visible).append(row)
                rows.append(row)
            lines.append(rows)
    return visible, invisible, lines


def measure_page(page, *, ocr_rects: Callable[[object], List[pymupdf.Rect]] = lambda page: [],
                 keep_text: bool = False) -> PageMeasure:
    """Measure the routing signals of ``page``.

    ``ocr_rects(page)`` returns the rectangles of the pictures the text route OCRs one by
    one. ``keep_text`` keeps the page's text layer (for the legibility judge).
    """
    trace_origins = None if char_flags_mark_painting() else frozenset(invisible_trace_origins(page))
    text_dict = page.get_text("dict", flags=TEXT_FLAGS)
    visible, invisible, lines = _span_rows(text_dict, trace_origins)
    invisible = [row for row in invisible if row[0].strip()]
    try:
        layer, hidden = split_invisible(page, invisible, visible)
    except Exception as exc:
        logger.debug(f"Invisible-text check failed on page {page.number + 1}: {exc}")
        layer, hidden = [], list(invisible)
    rects = image_rects(page)
    coverage = image_coverage(page, rects)
    visible_stats = text_layer_stats((text, size) for text, size, _ in visible)
    raster, ink = 0.0, None
    if visible_stats.weight < NO_TEXT_LIMIT:
        try:
            raster = uncaptured_raster(page, rects, ocr_rects(page))
            if coverage < IMAGE_PAGE_COVERAGE:
                ink = uncaptured_ink(page, [bbox for _, _, bbox in visible] + [tuple(rect) for rect in rects])
        except Exception as exc:
            logger.debug(f"Uncaptured-content measure failed on page {page.number + 1}: {exc}")
    signals = PageSignals(
        image_coverage=coverage,
        visible=visible_stats,
        invisible=text_layer_stats((text, size) for text, size, _ in layer),
        hidden_chars=sum(len("".join(text.split())) for text, _, _ in hidden),
        uncaptured_raster=raster,
        uncaptured_ink=ink,
    )
    text = None
    if keep_text:
        source = {id(row) for row in (layer if signals.searchable_scan else visible)}
        text = "\n".join(joined for joined in ("".join(row[0] for row in line if id(row) in source)
                                               for line in lines) if joined)
    return PageMeasure(
        signals=signals,
        layer_rects=tuple(bbox for _, _, bbox in layer),
        hidden_rects=tuple(bbox for _, _, bbox in hidden),
        visible_rects=tuple(bbox for _, _, bbox in visible) if invisible else (),
        trace_origins=trace_origins if invisible else None,
        text=text,
    )


def _redacted_copy(page, drop: Sequence[Bbox], guard: Sequence[Bbox]):
    """A one-page copy of ``page`` with the invisible text under ``drop`` removed, or None.

    With PyMuPDF's invisible-text redaction (1.27+) only invisible glyphs go; otherwise all
    glyphs under a rectangle go, so rectangles touching ``guard`` (visible text, text to
    keep) are left alone and only the span filter of :class:`VisibleTextPage` applies there.
    """
    invisible_only = getattr(pymupdf, "PDF_REDACT_TEXT_REMOVE_INVISIBLE", None)
    copy = pymupdf.open()
    try:
        copy.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
        target = copy[0]
        guard = [pymupdf.Rect(bbox) for bbox in guard]
        added = 0
        for bbox in drop:
            rect = pymupdf.Rect(bbox)
            if any(rect.intersects(other) for other in guard):
                continue
            target.add_redact_annot(rect, fill=False)
            added += 1
        if added:
            target.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=invisible_only if invisible_only is not None else pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
            return copy
    except Exception as exc:
        logger.debug(f"Could not strip invisible text from page {page.number + 1}: {exc}")
    copy.close()
    return None


def text_source(page, measure: PageMeasure, keep_layer: bool):
    """``(page, copy)``: the page as the text and table extractors must read it, and the one-page
    document to close afterwards (None when no copy was made).

    Hidden text never reaches them; the text layer of what the page shows only when
    ``keep_layer`` (its pictures are not OCR'd in this run). Table cells are read by
    PyMuPDF's table finder, so the invisible text is removed from a one-page copy; the
    returned page also filters ``get_text("dict" | "rawdict")`` in case a span could not
    be removed.
    """
    drop = list(measure.hidden_rects) + ([] if keep_layer else list(measure.layer_rects))
    if not drop:
        return page, None
    keep = list(measure.layer_rects) if keep_layer else []
    invisible_only = getattr(pymupdf, "PDF_REDACT_TEXT_REMOVE_INVISIBLE", None) is not None
    copy = _redacted_copy(page, drop, keep + ([] if invisible_only else list(measure.visible_rects)))
    source = copy[0] if copy is not None else page
    return VisibleTextPage(source, keep_rects=[pymupdf.Rect(bbox) for bbox in keep],
                           trace_origins=measure.trace_origins), copy


def describe_pages(page_numbers: Sequence[int], limit: int = 10) -> str:
    """``"pages 2, 5 and 9"`` for 0-based page indexes (at most ``limit`` listed)."""
    shown = [str(number + 1) for number in page_numbers[:limit]]
    more = len(page_numbers) - len(shown)
    if more > 0:
        shown.append(f"{more} more")
    if len(shown) == 1:
        return f"page {shown[0]}"
    return f"pages {', '.join(shown[:-1])} and {shown[-1]}"
