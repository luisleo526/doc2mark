"""Per-page OCR-routing signals of a PDF page, measured with PyMuPDF.

What the signals mean, the thresholds and the decisions live in
:mod:`doc2mark.core.strategy`; this module only measures one page:

- raster image coverage: the union of the image rectangles clipped to the visible
  page (CropBox), inline (BI/ID/EI) images included;
- the painted text, and the invisible (render mode 3, fully transparent) text, which
  is either the text of what the page shows (a scanner's OCR layer, a transparent copy
  of text baked into artwork or drawn as outlines), a copy of painted text, or hidden
  text;
- on pages without a usable text layer, what the text route cannot capture: pictures
  it does not OCR one by one (inline images, picture tiles) and other ink (vector
  outlines), line art (rules, frames, table grids) left out.

:func:`text_source` hands the text and table extractors the page without the
invisible text that must not become content.
"""
import logging
import math
import os
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pymupdf

from doc2mark.core.strategy import (
    GLYPH_INK_SHARE,
    GLYPH_SPREAD,
    GLYPH_TRANSITIONS,
    IMAGE_PAGE_COVERAGE,
    INK_CONTRAST,
    LAYER_BLANK_CONTRAST,
    LAYER_BLANK_SHARE,
    LAYER_DPI,
    MIN_LAYER_INK,
    MIN_UNCAPTURED_INK,
    NO_TEXT_LIMIT,
    PageSignals,
    legible_lines,
    text_layer_stats,
)

logger = logging.getLogger(__name__)

#: Text-extraction flags of the pipeline: unmapped glyphs come out as U+FFFD.
TEXT_FLAGS = pymupdf.TEXT_PRESERVE_LIGATURES

_FILLED = 16    # FZ_STEXT_FILLED in span["char_flags"] (PyMuPDF >= 1.25.2)
_STROKED = 32   # FZ_STEXT_STROKED
_INK_DPI = 72
_MAX_EXACT_UNION = 256   # beyond this many rectangles the union is measured on a raster

Bbox = Tuple[float, float, float, float]
Row = Tuple[str, float, Bbox, str]   # (span text, font size, bbox, font name)

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


def _array(bboxes: Iterable[Sequence[float]]) -> np.ndarray:
    """Rectangles as an (n, 4) array of x0, y0, x1, y1."""
    return np.array([tuple(bbox)[:4] for bbox in bboxes], dtype=float).reshape(-1, 4)


def _centres_in(bboxes: np.ndarray, rects: np.ndarray) -> np.ndarray:
    """Whether the centre of each of ``bboxes`` lies in one of ``rects``."""
    if not len(bboxes) or not len(rects):
        return np.zeros(len(bboxes), dtype=bool)
    x = (bboxes[:, 0:1] + bboxes[:, 2:3]) / 2
    y = (bboxes[:, 1:2] + bboxes[:, 3:4]) / 2
    return ((rects[:, 0] <= x) & (x <= rects[:, 2]) & (rects[:, 1] <= y) & (y <= rects[:, 3])).any(axis=1)


def _overlaps(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(len(a), len(b)) areas of the intersections of the rectangles ``a`` and ``b``."""
    width = np.minimum(a[:, None, 2], b[None, :, 2]) - np.maximum(a[:, None, 0], b[None, :, 0])
    height = np.minimum(a[:, None, 3], b[None, :, 3]) - np.maximum(a[:, None, 1], b[None, :, 1])
    return np.clip(width, 0, None) * np.clip(height, 0, None)


_PAIRS = 1_000_000   # rectangle pairs compared at a time (memory stays in the tens of MB)


def _any_overlap(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Whether each rectangle of ``a`` overlaps one of ``b``."""
    result = np.zeros(len(a), dtype=bool)
    if len(a) and len(b):
        low, high = a.min(axis=0), a.max(axis=0)
        b = b[(b[:, 0] < high[2]) & (b[:, 2] > low[0]) & (b[:, 1] < high[3]) & (b[:, 3] > low[1])]
        chunk = max(1, _PAIRS // max(1, len(b)))
        for start in range(0, len(a) if len(b) else 0, chunk):
            result[start:start + chunk] = (_overlaps(a[start:start + chunk], b) > 0).any(axis=1)
    return result


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
    keep = _array(keep_rects)
    blocks = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            blocks.append(block)
            continue
        lines, changed = [], False
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            invisible = [span_is_invisible(span, trace_origins) for span in spans]
            if not any(invisible):
                lines.append(line)
                continue
            kept = _centres_in(_array(span["bbox"] for span in spans), keep)
            spans = [span for span, hidden, keep_it in zip(spans, invisible, kept) if not hidden or keep_it]
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
    the real page.

    PyMuPDF's table finder reads the page's text itself: when a table it finds meets
    ``drop_rects``, the tables are found again on ``redact()`` (a copy of the page
    without that text), and the page reads from the copy from then on.
    """

    def __init__(self, page, keep_rects: Sequence[pymupdf.Rect] = (),
                 trace_origins: Optional[Set[Tuple[float, float]]] = None,
                 drop_rects: Sequence[pymupdf.Rect] = (), redact: Optional[Callable[[], object]] = None):
        self._page = page
        self._keep_rects = [pymupdf.Rect(rect) for rect in keep_rects]
        self._trace_origins = trace_origins
        self._drop_rects = [pymupdf.Rect(rect) for rect in drop_rects]
        self._redact = redact

    def __getattr__(self, name):
        return getattr(self._page, name)

    def get_text(self, option: str = "text", **kwargs):
        result = self._page.get_text(option, **kwargs)
        if option in ("dict", "rawdict") and isinstance(result, dict):
            return drop_invisible_text(result, self._keep_rects, self._trace_origins)
        return result

    def find_tables(self, *args, **kwargs):
        rotated = bool(self._page.rotation)
        tables = self._page.find_tables(*args, **kwargs)
        found = getattr(tables, "tables", ())
        # Table boxes come in the displayed frame: on a rotated page any table may hold the text to drop.
        meets = bool(found) if rotated else any(pymupdf.Rect(table.bbox).intersects(rect)
                                                for table in found for rect in self._drop_rects)
        if self._redact is not None and meets:
            copy, self._redact = self._redact(), None
            if copy is not None:
                self._page = copy
                tables = copy.find_tables(*args, **kwargs)
        return tables


class PageCopies:
    """Editable copies of a document's pages, all on one second handle of the same file.

    The handle is opened once per document (a new document would lose the catalog's
    optional-content state, so layers that are off would show). :meth:`copy` appends a
    full copy of a page; :meth:`discard` deletes the copies again.
    """

    def __init__(self, doc):
        self._source = doc
        self._doc = None

    def copy(self, number: int):
        """A full copy of page ``number``; raises when the copy does not match the page."""
        if self._doc is None:
            name = self._source.name
            self._doc = (pymupdf.open(name) if name and os.path.exists(name)
                         else pymupdf.open("pdf", self._source.tobytes()))
        # The copy lands under another page-tree node: what the page inherits from its own
        # node (resources, boxes, rotation) is written into the page first.
        flatten = getattr(getattr(pymupdf, "mupdf", None), "pdf_flatten_inheritable_page_items", None)
        if flatten is not None:
            flatten(pymupdf.mupdf.pdf_lookup_page_obj(pymupdf._as_pdf_document(self._doc), number))
        self._doc.fullcopy_page(number)
        copy, page = self._doc[-1], self._source[number]
        if (copy.rotation, tuple(copy.cropbox), len(copy.get_images()), len(copy.get_fonts())) != (
                page.rotation, tuple(page.cropbox), len(page.get_images()), len(page.get_fonts())):
            self.discard()
            raise RuntimeError(f"the copy of page {number + 1} lost what the page shows")
        return copy

    def discard(self) -> None:
        if self._doc is not None and len(self._doc) > len(self._source):
            self._doc.delete_pages(len(self._source), len(self._doc) - 1)

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None


def page_area(page) -> pymupdf.Rect:
    """The visible page (CropBox) in the frame of image and text coordinates (unrotated, CropBox origin)."""
    return pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)


def union_area(rects: Iterable[pymupdf.Rect], bounds: Optional[pymupdf.Rect] = None) -> float:
    """Area covered by the union of axis-aligned rectangles (overlaps counted once).

    Exact for up to ``_MAX_EXACT_UNION`` rectangles; above that (tiled scans) measured on
    a raster of ``bounds`` with at most 1000 cells a side.
    """
    rects = [rect for rect in rects if not rect.is_empty]
    if len(rects) > _MAX_EXACT_UNION and bounds is not None and not bounds.is_empty:
        scale = min(1.0, 1000 / max(bounds.width, bounds.height))
        grid = np.zeros((math.ceil(bounds.height * scale), math.ceil(bounds.width * scale)), dtype=bool)
        for rect in rects:
            x0, y0 = max(0, int((rect.x0 - bounds.x0) * scale)), max(0, int((rect.y0 - bounds.y0) * scale))
            x1, y1 = math.ceil((rect.x1 - bounds.x0) * scale), math.ceil((rect.y1 - bounds.y0) * scale)
            grid[y0:y1, x0:x1] = True
        return float(grid.sum()) / (scale * scale)
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


# --- Pixels ---------------------------------------------------------------------


def _grey(page, dpi: float) -> Tuple[np.ndarray, pymupdf.Matrix]:
    """The page rendered in grey at ``dpi`` (rows x columns), and the matrix from page to pixels."""
    zoom = dpi / 72
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    pixels = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    return pixels, page.rotation_matrix * pymupdf.Matrix(zoom, zoom)


def _region(pixels: np.ndarray, bbox: Sequence[float], to_pixels: pymupdf.Matrix) -> Optional[np.ndarray]:
    """The pixels of ``bbox`` (page coordinates), or None when it is off the render."""
    box = (pymupdf.Rect(bbox) * to_pixels).irect & pymupdf.IRect(0, 0, pixels.shape[1], pixels.shape[0])
    return None if box.is_empty else pixels[box.y0:box.y1, box.x0:box.x1]


def _deviation(region: np.ndarray) -> np.ndarray:
    """How far each pixel is from the region's background (its most common grey)."""
    return np.abs(region.astype(np.int16) - int(np.bincount(region.ravel(), minlength=256).argmax()))


def _blank(region: np.ndarray) -> bool:
    """Nothing shows: (almost) every pixel within LAYER_BLANK_CONTRAST levels of the background."""
    return float((_deviation(region) > LAYER_BLANK_CONTRAST).mean()) < LAYER_BLANK_SHARE


def _glyph_like(region: np.ndarray) -> bool:
    """Ink spread over the rows and the columns of the region and broken into strokes along its
    rows, as glyphs are (not a rule, the edge of a box or a chart line)."""
    ink = _deviation(region) > INK_CONTRAST
    rows = ink.any(axis=1)
    if (float(ink.mean()) < GLYPH_INK_SHARE or float(rows.mean()) < GLYPH_SPREAD
            or float(ink.any(axis=0).mean()) < GLYPH_SPREAD):
        return False
    return float(np.abs(np.diff(ink[rows].astype(np.int8), axis=1)).sum(axis=1).mean()) >= GLYPH_TRANSITIONS


def _ink_share(region: np.ndarray) -> float:
    return float((_deviation(region) > INK_CONTRAST).mean())


def _edges(x0: float, y0: float, x1: float, y1: float, pad: float) -> List[Bbox]:
    return [(x0 - pad, y0 - pad, x1 + pad, y0 + pad), (x0 - pad, y1 - pad, x1 + pad, y1 + pad),
            (x0 - pad, y0 - pad, x0 + pad, y1 + pad), (x1 - pad, y0 - pad, x1 + pad, y1 + pad)]


def _line_art(page) -> np.ndarray:
    """Where the page draws rules, frames and grids, as an (n, 4) array: horizontal and vertical
    strokes, rectangle and quad outlines, and fills thinner than 2 pt. Not content by themselves.

    Paths are streamed one by one where PyMuPDF allows it, so a drawing of hundreds of
    thousands of paths is never held in memory.
    """
    boxes: List[Bbox] = []

    def add(path: dict) -> None:
        kind = path.get("type") or ""
        x0, y0, x1, y1 = path.get("rect") or (0, 0, 0, 0)
        if "f" in kind:
            if min(x1 - x0, y1 - y0) <= 2:
                boxes.append((x0 - 1, y0 - 1, x1 + 1, y1 + 1))
            return
        pad = (path.get("width") or 1) / 2 + 1
        for item in path.get("items", ()):
            if item[0] == "l":
                (ax, ay), (bx, by) = item[1], item[2]
                if abs(ax - bx) <= 1 or abs(ay - by) <= 1:
                    boxes.append((min(ax, bx) - pad, min(ay, by) - pad, max(ax, bx) + pad, max(ay, by) + pad))
            elif item[0] == "re":
                boxes.extend(_edges(*item[1][:4], pad))
            elif item[0] == "qu":
                xs, ys = [point[0] for point in item[1]], [point[1] for point in item[1]]
                boxes.extend(_edges(min(xs), min(ys), max(xs), max(ys), pad))

    try:
        page.get_cdrawings(callback=add)
    except TypeError:   # a PyMuPDF without the callback
        for path in page.get_cdrawings():
            add(path)
    return _array(boxes)


def _pixel_boxes(bboxes: np.ndarray, to_pixels: pymupdf.Matrix, shape: Tuple[int, ...]) -> np.ndarray:
    """Integer pixel boxes (x0, y0, x1, y1) of ``bboxes`` on a render of ``shape``, clipped to it
    (empty where they are off it)."""
    xs, ys = bboxes[:, [0, 2, 0, 2]], bboxes[:, [1, 1, 3, 3]]
    px = to_pixels.a * xs + to_pixels.c * ys + to_pixels.e
    py = to_pixels.b * xs + to_pixels.d * ys + to_pixels.f
    boxes = np.stack([np.floor(px.min(axis=1)), np.floor(py.min(axis=1)),
                      np.ceil(px.max(axis=1)), np.ceil(py.max(axis=1))], axis=1)
    return np.clip(boxes, 0, [shape[1], shape[0], shape[1], shape[0]]).astype(int)


def uncaptured_ink(page, blank: Iterable[Sequence[float]]) -> float:
    """Share of the page showing ink (any colour) outside ``blank`` (areas the text route already
    captures) and outside line art, measured on a grey render. Line art is only looked at when
    the other ink reaches MIN_UNCAPTURED_INK."""
    pixels, to_pixels = _grey(page, _INK_DPI)
    background = int(np.bincount(pixels.ravel(), minlength=256).argmax())
    ink = np.abs(pixels.astype(np.int16) - background) > INK_CONTRAST
    for x0, y0, x1, y1 in _pixel_boxes(_array(blank), to_pixels, pixels.shape):
        ink[y0:y1, x0:x1] = False
    if float(ink.mean()) < MIN_UNCAPTURED_INK:
        return float(ink.mean())
    for x0, y0, x1, y1 in _pixel_boxes(_line_art(page), to_pixels, pixels.shape):
        ink[y0:y1, x0:x1] = False
    return float(ink.mean())


# --- Invisible text ---------------------------------------------------------------


def _norm(text: str) -> str:
    return "".join(text.split()).casefold()


def _same_text(row: Row, under: Sequence[Row]) -> bool:
    """Whether the painted spans ``under`` read, over the extent of the invisible span ``row``, the
    same text (each painted span's characters taken in proportion to the width it shares)."""
    text = _norm(row[0])
    if not text:
        return False
    x0, x1 = row[2][0], row[2][2]
    margin = 1 + len(text) // 4
    parts = []
    for painted, _, box, _ in sorted(under, key=lambda other: other[2][0]):
        width = box[2] - box[0]
        if width <= 0 or not painted:
            continue
        first = max(0, math.floor((x0 - box[0]) / width * len(painted)) - margin)
        last = min(len(painted), math.ceil((x1 - box[0]) / width * len(painted)) + margin)
        parts.append(painted[first:last])
    painted = _norm("".join(parts))
    return bool(painted) and (text in painted or (painted in text and len(painted) >= 0.9 * len(text)))


def _duplicates(invisible: Sequence[Row], visible: Sequence[Row], rows: np.ndarray, shown: np.ndarray) -> np.ndarray:
    """Whether each invisible span repeats the painted text it lies on (an invisible duplicate): painted
    spans on the same line (sharing half the taller one's height, so not a big stamp or a rotated
    watermark) that read the same text where it lies."""
    result = np.zeros(len(invisible), dtype=bool)
    if not len(shown):
        return result
    shown_height = shown[:, 3] - shown[:, 1]
    chunk = max(1, _PAIRS // len(shown))
    for start in range(0, len(invisible), chunk):
        part = rows[start:start + chunk]
        width = np.minimum(part[:, None, 2], shown[None, :, 2]) - np.maximum(part[:, None, 0], shown[None, :, 0])
        height = np.minimum(part[:, None, 3], shown[None, :, 3]) - np.maximum(part[:, None, 1], shown[None, :, 1])
        taller = np.maximum((part[:, 3] - part[:, 1])[:, None], shown_height[None, :])
        for index, same_line in enumerate((width > 0) & (height >= 0.5 * taller), start):
            if same_line.any():
                result[index] = _same_text(invisible[index], [visible[k] for k in np.flatnonzero(same_line)])
    return result


def _without_text(page, copies: "PageCopies"):
    """A copy of ``page`` with all its text removed (pictures and drawings kept)."""
    copy = copies.copy(page.number)
    copy.add_redact_annot(page_area(page), fill=False)
    copy.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    return copy


_PAINTING = ("fill-path", "stroke-path", "fill-image", "fill-imgmask", "fill-shade")


def _erase(region: np.ndarray, origin: Tuple[int, int], boxes: np.ndarray, background: int) -> np.ndarray:
    """``region`` (pixels from ``origin``) with the pixel ``boxes`` painted over in ``background``."""
    region = region.copy()
    left, top = origin
    for x0, y0, x1, y1 in boxes:
        region[max(0, y0 - top):max(0, y1 - top), max(0, x0 - left):max(0, x1 - left)] = background
    return region


def classify_invisible(page, invisible: Sequence[Row], visible: Sequence[Row], pictures: Sequence[pymupdf.Rect],
                       copies: "PageCopies") -> Tuple[List[Row], List[Row], List[Row]]:
    """Split invisible spans into (layer, hidden, duplicates).

    A duplicate repeats the painted text it lies on. The others are judged by what the
    page shows under them (:func:`_by_what_shows`); when the page cannot be checked,
    they are all kept as the page's text (fail open) and a warning is logged.
    """
    rows = _array(row[2] for row in invisible) + [-0.5, -0.5, 0.5, 0.5]
    shown = _array(row[2] for row in visible)
    duplicate = _duplicates(invisible, visible, rows, shown)
    duplicates = [row for row, flag in zip(invisible, duplicate) if flag]
    rest = np.flatnonzero(~duplicate)
    try:
        layer, hidden = _by_what_shows(page, [invisible[index] for index in rest], rows[rest], shown, pictures,
                                       copies)
    except Exception as exc:  # cannot tell what shows: keep the text (fail open)
        logger.warning(f"Page {page.number + 1}: could not check what its invisible text lies on ({exc}); "
                       f"keeping it as the page's text")
        layer, hidden = [invisible[index] for index in rest], []
    return layer, hidden, duplicates


def _by_what_shows(page, invisible: Sequence[Row], rows: np.ndarray, shown: np.ndarray,
                   pictures: Sequence[pymupdf.Rect], copies: "PageCopies") -> Tuple[List[Row], List[Row]]:
    """(layer, hidden) of invisible spans that copy no painted text (``rows``: their boxes).

    A span with nothing but text painted under it lies over nothing the page shows
    (hidden). The rest is judged on the page rendered without its text (a copy is
    rendered only where painted text overlaps them; invisible text never renders): over
    a picture a span is the picture's text (layer) unless the region under it is blank;
    elsewhere it is layer only over glyph-like ink (text drawn as outlines), rules and
    frames crossing it left out.
    """
    painted = _array(box for kind, box in page.get_bboxlog() if kind in _PAINTING) + [-1, -1, 1, 1]
    candidate = _any_overlap(rows, painted)
    hidden = [row for row, check in zip(invisible, candidate) if not check]
    if not candidate.any():
        return [], hidden
    overlapped = bool(_any_overlap(rows[candidate], shown).any())
    try:
        pixels, to_pixels = _grey(_without_text(page, copies) if overlapped else page, LAYER_DPI)
    finally:
        if overlapped:
            copies.discard()
    boxes = _pixel_boxes(rows, to_pixels, pixels.shape)
    over_picture = _centres_in(rows, _array(pictures))
    art = background = None
    layer = []
    for index in np.flatnonzero(candidate):
        row = invisible[index]
        x0, y0, x1, y1 = boxes[index]
        if x1 <= x0 or y1 <= y0:
            hidden.append(row)   # off the visible page
            continue
        region = pixels[y0:y1, x0:x1]
        if over_picture[index]:
            (hidden if _blank(region) else layer).append(row)
            continue
        if art is None:
            art = _line_art(page)
            background = int(np.bincount(pixels.ravel(), minlength=256).argmax())
        a0, b0, a1, b1 = row[2]
        meets = (art[:, 0] < a1) & (art[:, 2] > a0) & (art[:, 1] < b1) & (art[:, 3] > b0)
        inside = (art[:, 0] >= a0 - 1) & (art[:, 1] >= b0 - 1) & (art[:, 2] <= a1 + 1) & (art[:, 3] <= b1 + 1)
        crossing = art[meets & ~inside]
        if len(crossing):
            region = _erase(region, (x0, y0), _pixel_boxes(crossing, to_pixels, pixels.shape), background)
        (layer if _glyph_like(region) else hidden).append(row)
    return layer, hidden


@dataclass(frozen=True)
class PageMeasure:
    """Routing signals of one page, plus what the text path needs to honour them."""

    signals: PageSignals
    layer_rects: Tuple[Bbox, ...] = ()      # invisible spans over what the page shows: its text
    hidden_rects: Tuple[Bbox, ...] = ()     # invisible spans over nothing the page shows: hidden text
    duplicate_rects: Tuple[Bbox, ...] = ()  # invisible copies of the painted text they lie on
    visible_rects: Tuple[Bbox, ...] = ()    # painted spans (kept only on pages with invisible text)
    trace_origins: Optional[frozenset] = None  # invisible-glyph origins, only without char_flags support
    text: Optional[str] = None              # the page's text layer, for the optional legibility judge

    @property
    def has_invisible_text(self) -> bool:
        return bool(self.layer_rects or self.hidden_rects or self.duplicate_rects)


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
                row = (text, float(span.get("size", 0.0)), tuple(span.get("bbox", (0, 0, 0, 0))),
                       span.get("font", ""))
                (invisible if span_is_invisible(span, trace_origins) else visible).append(row)
                rows.append(row)
            lines.append(rows)
    return visible, invisible, lines


def measure_page(page, *, ocr_rects: Callable[[object], List[pymupdf.Rect]] = lambda page: [],
                 keep_text: bool = False, copies: Optional[PageCopies] = None) -> PageMeasure:
    """Measure the routing signals of ``page``.

    ``ocr_rects(page)`` returns the rectangles of the pictures the text route OCRs one by
    one. ``keep_text`` keeps the page's text layer (for the legibility judge). ``copies``
    provides editable page copies (a private one is used and closed when omitted).
    """
    trace_origins = None if char_flags_mark_painting() else frozenset(invisible_trace_origins(page))
    text_dict = page.get_text("dict", flags=TEXT_FLAGS)
    visible, invisible, lines = _span_rows(text_dict, trace_origins)
    invisible = [row for row in invisible if row[0].strip()]
    rects = image_rects(page)
    coverage = image_coverage(page, rects)
    layer, hidden, duplicates = [], [], []
    if invisible:
        own = copies is None
        copies = copies or PageCopies(page.parent)
        try:
            layer, hidden, duplicates = classify_invisible(page, invisible, visible, rects, copies)
        except Exception as exc:  # cannot tell what the spans are: keep the text (fail open)
            logger.warning(f"Page {page.number + 1}: could not classify its invisible text ({exc}); "
                           f"keeping it as the page's text")
            layer, hidden, duplicates = list(invisible), [], []
        finally:
            if own:
                copies.close()
    signals = PageSignals(
        image_coverage=coverage,
        visible=text_layer_stats((text, size, font) for text, size, _, font in visible),
        invisible=text_layer_stats((text, size, font) for text, size, _, font in layer),
        hidden_chars=sum(len("".join(text.split())) for text, _, _, _ in hidden),
    )
    # What the text route cannot capture matters only without a usable text layer (a
    # searchable scan has one: its OCR layer).
    if signals.visible.weight < NO_TEXT_LIMIT and not signals.searchable_scan:
        try:
            raster = uncaptured_raster(page, rects, ocr_rects(page))
            ink = (uncaptured_ink(page, [bbox for _, _, bbox, _ in visible] + [tuple(rect) for rect in rects])
                   if coverage < IMAGE_PAGE_COVERAGE else None)
            signals = replace(signals, uncaptured_raster=raster, uncaptured_ink=ink)
        except Exception as exc:
            logger.debug(f"Uncaptured-content measure failed on page {page.number + 1}: {exc}")
    text = None
    if keep_text:
        source = {id(row) for row in (layer if signals.searchable_scan else visible)}
        text = "\n".join(joined for joined in ("".join(row[0] for row in line if id(row) in source)
                                               for line in lines) if joined)
    return PageMeasure(
        signals=signals,
        layer_rects=tuple(row[2] for row in layer),
        hidden_rects=tuple(row[2] for row in hidden),
        duplicate_rects=tuple(row[2] for row in duplicates),
        visible_rects=tuple(row[2] for row in visible) if invisible else (),
        trace_origins=trace_origins if invisible else None,
        text=text,
    )


def _redacted_copy(page, drop: Sequence[Bbox], visible: Sequence[Bbox], keep: Sequence[Bbox],
                   copies: PageCopies):
    """A copy of ``page`` (from ``copies``) with the invisible text under ``drop`` removed, or None.

    Rectangles clear of visible text and of text to keep lose every glyph under them
    (this also removes render mode 7 text). Rectangles touching visible text lose only
    their invisible glyphs, which needs PyMuPDF's invisible-text redaction (1.27+);
    without it they are left to the span filter of :class:`VisibleTextPage`, and table
    cells may keep them.
    """
    invisible_only = getattr(pymupdf, "PDF_REDACT_TEXT_REMOVE_INVISIBLE", None)
    keep = [pymupdf.Rect(bbox) for bbox in keep]
    visible = [pymupdf.Rect(bbox) for bbox in visible]
    clear, touching = [], []
    for bbox in drop:
        rect = pymupdf.Rect(bbox)
        if any(rect.intersects(other) for other in keep):
            continue
        (touching if any(rect.intersects(other) for other in visible) else clear).append(rect)
    passes = [(clear, pymupdf.PDF_REDACT_TEXT_REMOVE)]
    if invisible_only is not None:
        passes.append((touching, invisible_only))
    if not any(rects for rects, _ in passes):
        return None
    try:
        copy = copies.copy(page.number)
        for rects, mode in passes:
            if rects:
                for rect in rects:
                    copy.add_redact_annot(rect, fill=False)
                copy.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                      graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=mode)
        return copy
    except Exception as exc:
        logger.debug(f"Could not strip invisible text from page {page.number + 1}: {exc}")
        copies.discard()
        return None


def text_source(page, measure: PageMeasure, copies: PageCopies, ocrd_rects: Sequence[pymupdf.Rect] = ()):
    """The page as the text and table extractors must read it.

    Hidden text and invisible copies of painted text never reach them, nor layer spans
    centred in ``ocrd_rects`` (pictures whose OCR returned text in this run: that text
    replaces them). Other layer spans are the page's text and stay. When a table meets
    text to drop, the table finder reads a copy of the page from ``copies`` without it
    (see :class:`VisibleTextPage`); discard the copies once the page is extracted.
    """
    replaced = _centres_in(_array(measure.layer_rects), _array(ocrd_rects))
    keep = [bbox for bbox, flag in zip(measure.layer_rects, replaced) if not flag]
    drop = (list(measure.hidden_rects) + list(measure.duplicate_rects)
            + [bbox for bbox, flag in zip(measure.layer_rects, replaced) if flag])
    if not drop:
        return page
    return VisibleTextPage(page, keep_rects=[pymupdf.Rect(bbox) for bbox in keep],
                           trace_origins=measure.trace_origins, drop_rects=[pymupdf.Rect(bbox) for bbox in drop],
                           redact=lambda: _redacted_copy(page, drop, measure.visible_rects, keep, copies))


# --- Verbatim tail ----------------------------------------------------------------

_CJK_RANGES = ((0x3040, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xAC00, 0xD7AF), (0xF900, 0xFAFF))
_CJK_CHAR = re.compile("[" + "".join(f"{chr(low)}-{chr(high)}" for low, high in _CJK_RANGES) + "]")


def _tokens(text: str) -> List[str]:
    """Words, case-folded, markup and punctuation dropped; CJK split per character (OCR engines
    space CJK text unpredictably)."""
    words = []
    for word in re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()):
        words.extend(_CJK_CHAR.findall(word) if _CJK_CHAR.search(word) else [word])
    return words


def _match(words: List[str], ocr_words: List[str]) -> Optional[range]:
    """Where the OCR words reproduce a line's ``words``: the first stretch of OCR words as long as the
    line holding at least 80 % of its words, in order (markup, punctuation, case and spacing
    ignored); None when there is none."""
    if not words:
        return range(0)
    size, needed = len(words), 0.8 * len(words)
    for start in range(max(1, len(ocr_words) - size + 1)):
        window = ocr_words[start:start + size]
        if sum(1 for a, b in zip(window, words) if a == b) >= needed:
            return range(start, start + len(window))
    return None


_COVERING = ("fill-image", "fill-imgmask", "fill-shade", "fill-path")


def _shown(bbox: pymupdf.Rect, log: list, pixels: np.ndarray, to_pixels: pymupdf.Matrix) -> bool:
    """Whether painted text in ``bbox`` shows on the page: not painted over by a later picture
    or shape, and drawn with enough contrast to its background."""
    painted = [index for index, (kind, box) in enumerate(log)
               if kind.endswith("-text") and pymupdf.Rect(box).intersects(bbox)]
    after = painted[-1] if painted else -1
    area = abs(bbox.width * bbox.height) or 1.0
    for kind, box in log[after + 1:]:
        if kind in _COVERING and abs((pymupdf.Rect(box) & bbox).get_area()) >= 0.9 * area:
            return False
    region = _region(pixels, bbox, to_pixels)
    return region is not None and region.size > 0 and _ink_share(region) >= MIN_LAYER_INK


def missing_painted_lines(page, measure: PageMeasure, ocr_text: str, *, garbled: bool = False) -> List[str]:
    """The page's painted, legible text lines that ``ocr_text`` does not reproduce and that the
    page visibly shows (text painted over by a picture, drawn in its background colour or
    garbled never reaches the OCR as such and is not kept).

    ``garbled``: the page was OCR'd because its text layer is garbled, so only the deterministic
    detector can vouch for a line. No line is kept when the legibility judge alone found the
    layer garbled, nor when the OCR text holds words no layer line accounts for, as many as half
    the words of the lines to keep: the OCR then read those lines differently, so their text
    layer is wrong, not missed.
    """
    if not measure.signals.visible.chars:
        return []
    if garbled and not measure.signals.text_layer.garbled:
        return []
    lines = []
    for block in page.get_text("dict", flags=TEXT_FLAGS).get("blocks", []):
        for line in block.get("lines", []) if block.get("type") == 0 else []:
            spans = [span for span in line.get("spans", [])
                     if span.get("text") and not span_is_invisible(span, measure.trace_origins)]
            if spans:
                lines.append(spans)
    legible = legible_lines([[(span["text"], span.get("size", 0.0), span.get("font", "")) for span in spans]
                             for spans in lines])
    ocr_words = _tokens(ocr_text)
    explained: Set[int] = set()
    candidates = []
    for spans, readable in zip(lines, legible):
        text = "".join(span["text"] for span in spans).strip()
        words = _tokens(text)
        window = _match(words, ocr_words)
        if window is not None:
            explained.update(window)
        elif readable:
            candidates.append((text, pymupdf.Rect(_union_bbox(span["bbox"] for span in spans)), len(words)))
    if not candidates:
        return []
    if garbled and len(ocr_words) - len(explained) >= 0.5 * sum(count for _, _, count in candidates):
        return []
    try:
        log = page.get_bboxlog()
        pixels, to_pixels = _grey(page, _INK_DPI)
        return [text for text, bbox, _ in candidates if _shown(bbox, log, pixels, to_pixels)]
    except Exception as exc:  # cannot tell what shows: keep the text (verbatim first)
        logger.debug(f"Visibility check failed on page {page.number + 1}: {exc}")
        return [text for text, _, _ in candidates]


def describe_pages(page_numbers: Sequence[int], limit: int = 10) -> str:
    """``"pages 2, 5 and 9"`` for 0-based page indexes (at most ``limit`` listed)."""
    shown = [str(number + 1) for number in page_numbers[:limit]]
    more = len(page_numbers) - len(shown)
    if more > 0:
        shown.append(f"{more} more")
    if len(shown) == 1:
        return f"page {shown[0]}"
    return f"pages {', '.join(shown[:-1])} and {shown[-1]}"
