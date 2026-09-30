"""Faithful tables from PyMuPDF's ``page.find_tables()`` grids.

``find_tables()`` finds grids drawn on a page. This module turns each grid into a
:class:`~doc2mark.core.table.TableData` without losing, duplicating or inventing text:

* **Cell text.** The page's characters are read once, from the text page that
  ``find_tables()`` built (the characters and coordinates ``Table.extract()`` uses,
  also on rotated pages), and each character goes to exactly one cell: the smallest
  cell, over all tables on the page, that contains its centre. A nested table keeps
  its own text and overlapping cells never share a character. Within a cell a text
  run is dropped only when it redraws another run's text over it at the same size
  and baseline (fake-bold overprint, a duplicated text layer), or when it is an
  invisible OCR layer over visible text; *different* text drawn over text (a value
  typed over ``____``, a tick over a checkbox, a watermark) is kept and read word by
  word from left to right. Otherwise the text is assembled the
  way ``Table.extract()`` does it: words split at spaces and gaps wider than 3 pt,
  lines kept apart with ``\\n``.
* **Merged cells.** ``rowspan``/``colspan`` are measured from each drawn cell box
  against the grid's column and row boundaries, never guessed from blank cells.
* **Plausibility.** A grid is kept as a table when it has text in at least two rows
  and two columns, or when its cell borders are drawn as lines (strokes or hairline
  fills). A grid without drawn borders that has overlapping cells or covers most of
  the page is page decoration, not a table. A table never claims a region whose text
  it does not emit (:func:`claims_only_its_text`).
* **Headers.** A header row that PyMuPDF finds just above the ruled cells (drawn
  without borders) becomes the table's header row; see :func:`continue_table`
  for tables that run on across a page break.
"""

import bisect
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pymupdf

from doc2mark.core.table import TableData

logger = logging.getLogger(__name__)

try:  # the flags find_tables() builds its text page with
    from pymupdf.table import FLAGS as _TEXTPAGE_FLAGS
except Exception:  # pragma: no cover - older PyMuPDF
    _TEXTPAGE_FLAGS = pymupdf.TEXTFLAGS_TEXT

Rect = Tuple[float, float, float, float]

X_TOLERANCE = 3.0  # word gap, as in Table.extract()
Y_TOLERANCE = 3.0  # line clustering, as in Table.extract()
SPAN_EPSILON = 1.0  # a cell box must reach this far past a grid line to span it
EDGE_TOLERANCE = 2.0  # drawn rule to cell border distance
MARGIN_BAND = 0.08  # top/bottom share of the page treated as running header/footer area
_LIGATURES = {"ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬁ": "fi", "ﬂ": "fl", "ﬆ": "st", "ﬅ": "st"}


# --- characters -------------------------------------------------------------------------------------------

class Char:
    """One character with the geometry ``Table.extract()`` uses (small glyph heights)."""

    __slots__ = ("text", "x0", "top", "x1", "bottom", "cx", "cy", "size", "run", "upright", "bold", "visible")

    def __init__(self, text, x0, top, x1, bottom, size, run, upright, bold, visible=True):
        self.text = text
        self.x0, self.top, self.x1, self.bottom = x0, top, x1, bottom
        self.cx = (x0 + x1) / 2
        self.cy = (top + bottom) / 2
        self.size = size
        self.run = run  # MuPDF line number: characters drawn as one text run share it
        self.upright = upright
        self.bold = bold
        self.visible = visible  # False for invisible text (an OCR layer: render mode 3, alpha 0)


def read_chars(textpage, matrix=None) -> List[Char]:
    """All characters of ``textpage``, optionally mapped through ``matrix``.

    Glyph boxes are read with PyMuPDF's small glyph heights, as ``find_tables()``
    does, so a character's centre lies on its baseline band and not in the next row.
    """
    small = bool(pymupdf.TOOLS.set_small_glyph_heights())
    pymupdf.TOOLS.set_small_glyph_heights(True)
    try:
        blocks = textpage.extractRAWDICT()["blocks"]
    finally:
        pymupdf.TOOLS.set_small_glyph_heights(small)
    chars = []
    run = 0
    for block in blocks:
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", ()):
            run += 1
            dx, dy = line.get("dir", (1.0, 0.0))
            if matrix is not None:
                dx, dy = dx * matrix.a + dy * matrix.c, dx * matrix.b + dy * matrix.d
            upright = abs(dy) < 1e-3 and dx > 0
            for span in line.get("spans", ()):
                size = span.get("size", 0.0)
                bold = bool(span.get("flags", 0) & 16 or span.get("char_flags", 0) & 8)
                # invisible text: fully transparent, or neither filled nor stroked (render mode 3)
                visible = span.get("alpha", 255) != 0 and ("char_flags" not in span or bool(span["char_flags"] & 48))
                for char in span.get("chars", ()):
                    x0, y0, x1, y1 = char["bbox"]
                    if matrix is not None:
                        x0, y0, x1, y1 = pymupdf.Rect(x0, y0, x1, y1) * matrix
                    chars.append(Char(char["c"], x0, y0, x1, y1, size, run, upright, bold, visible))
    return chars


def page_chars(page) -> List[Char]:
    """The characters of ``page`` in the coordinates ``find_tables()`` reports (the
    rotated page, as displayed)."""
    textpage = page.get_textpage(flags=_TEXTPAGE_FLAGS)
    return read_chars(textpage, page.rotation_matrix if page.rotation else None)


def table_chars(table) -> List[Char]:
    """The characters of the page a found table is on, in the table's coordinates."""
    textpage = getattr(table, "textpage", None)
    if textpage is not None:
        return read_chars(textpage)
    return page_chars(table.page)


# --- cell text --------------------------------------------------------------------------------------------

@dataclass
class _Run:
    order: int
    ink: str
    chars: List[Char]
    length: int
    x0: float
    x1: float
    mid: float
    size: float


def _is_subsequence(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(char in it for char in needle)


def _redraws(b: _Run, a: _Run) -> bool:
    """Is run ``b`` a second rendering of (part of) run ``a``'s text over ``a``?"""
    size = max(a.size, b.size)
    if size <= 0 or min(a.size, b.size) < 0.8 * size or abs(a.mid - b.mid) > 0.35 * size:
        return False
    if len(b.ink) <= 2:
        # a short run must sit exactly on the same characters (a bold redraw)
        ink = [char for char in a.chars if not char.text.isspace()]
        tolerance = 0.2 * size
        for k in range(len(ink) - len(b.ink) + 1):
            if (a.ink[k:k + len(b.ink)] == b.ink and abs(ink[k].x0 - b.x0) <= tolerance
                    and abs(ink[k + len(b.ink) - 1].x1 - b.x1) <= tolerance):
                return True
        return False
    tolerance = 0.6 * size
    return (a.x0 - tolerance <= b.x0 and b.x1 <= a.x1 + tolerance
            and 2 * len(b.ink) >= len(a.ink) and _is_subsequence(b.ink, a.ink))


def _without_hidden_copies(chars: Sequence[Char]) -> Sequence[Char]:
    """Drop invisible text (an OCR layer) drawn over visible text: the visible glyphs are the text,
    the hidden layer repeats them, often with recognition errors. Invisible text with nothing
    visible under it (a scanned cell) is kept."""
    hidden = [char for char in chars if not char.visible and not char.text.isspace()]
    if not hidden:
        return chars
    shown = sorted((char for char in chars if char.visible and not char.text.isspace()), key=lambda char: char.cy)
    if not shown:
        return chars
    mids = [char.cy for char in shown]
    covered: Dict[int, List[int]] = {}
    for char in hidden:
        lo = bisect.bisect_left(mids, char.cy - 0.5 * char.size)
        hi = bisect.bisect_right(mids, char.cy + 0.5 * char.size)
        over = any(min(char.x1, other.x1) > max(char.x0, other.x0) for other in shown[lo:hi])
        counts = covered.setdefault(char.run, [0, 0])
        counts[0] += 1
        counts[1] += over
    dropped = {run for run, (total, over) in covered.items() if 2 * over >= total}
    return [char for char in chars if char.visible or char.run not in dropped]


def _without_redrawn_runs(chars: Sequence[Char]) -> Sequence[Char]:
    """Drop the text runs that only redraw another run's text over it (fake bold, a second
    text layer). Runs with any other text stay, whatever they overlap."""
    by_run: Dict[int, List[Char]] = {}
    for char in chars:
        by_run.setdefault(char.run, []).append(char)
    if len(by_run) < 2:
        return chars
    runs = []
    for order, (run_id, run_chars) in enumerate(by_run.items()):
        ink = [char for char in run_chars if not char.text.isspace()]
        if not ink:
            continue
        mids = sorted(char.cy for char in ink)
        runs.append((run_id, _Run(order, "".join(char.text for char in ink), run_chars, len(run_chars),
                                  min(char.x0 for char in ink), max(char.x1 for char in ink),
                                  mids[len(mids) // 2], max(char.size for char in ink))))
    runs.sort(key=lambda item: (-len(item[1].ink), -item[1].length, item[1].order))
    kept: List[_Run] = []
    dropped = set()
    for run_id, run in runs:
        if any(_redraws(run, other) for other in kept):
            dropped.add(run_id)
        else:
            kept.append(run)
    if not dropped:
        return chars
    return [char for char in chars if char.run not in dropped]


def _without_covered_spaces(chars: Sequence[Char]) -> Sequence[Char]:
    """A space drawn on top of another run's glyph is positioning, not a word break
    (e.g. a value right-aligned with leading spaces over a label)."""
    if len({char.run for char in chars}) < 2:
        return chars
    ink = sorted((char for char in chars if not char.text.isspace()), key=lambda char: char.top)
    tops = [char.top for char in ink]
    kept = []
    for char in chars:
        if char.text.isspace():
            lo = bisect.bisect_left(tops, char.top - Y_TOLERANCE)
            hi = bisect.bisect_right(tops, char.top + Y_TOLERANCE)
            width = max(char.x1 - char.x0, 0.01)
            if any(other.run != char.run and min(char.x1, other.x1) - max(char.x0, other.x0) > width / 2
                   for other in ink[lo:hi]):
                continue
        kept.append(char)
    return kept


def _clusters(items, key: Callable, tolerance: float) -> List[list]:
    """pdfplumber/PyMuPDF ``cluster_objects``: chain-cluster ``items`` by ``key`` (values more
    than ``tolerance`` apart start a new cluster); items keep their order within a cluster."""
    values = sorted({key(item) for item in items})
    cluster_of = {}
    index = 0
    for i, value in enumerate(values):
        if i and value > values[i - 1] + tolerance:
            index += 1
        cluster_of[value] = index
    groups: Dict[int, list] = {}
    for item in items:
        groups.setdefault(cluster_of[key(item)], []).append(item)
    return [groups[i] for i in sorted(groups)]


@dataclass
class _Word:
    text: str
    x0: float
    x1: float
    top: float


def _words(ordered: Iterable[Char]) -> List[_Word]:
    """``Table.extract()``'s word split: at whitespace, at a gap wider than 3 pt, when the
    next character lies left of the previous one or 3 pt lower. Characters are ordered by
    their centres, so the zero-width second half of a ligature ("ff", "ti") stays in place."""
    words, current = [], []

    def flush():
        if current:
            words.append(_Word("".join(_LIGATURES.get(char.text, char.text) for char in current),
                               min(char.x0 for char in current), max(char.x1 for char in current),
                               min(char.top for char in current)))

    for char in ordered:
        if char.text.isspace():
            flush()
            current = []
        elif current and (char.cx < current[-1].cx or char.x0 > current[-1].x1 + X_TOLERANCE
                          or char.top > current[-1].top + Y_TOLERANCE):
            flush()
            current = [char]
        else:
            current.append(char)
    flush()
    return words


def _runs_overlap(chars: Sequence[Char]) -> bool:
    """Is a run with two or more glyphs drawn over another run on this line (overlapping
    more than half of the narrower one, spaces included)? Reading such a line character
    by character would interleave the two texts. A single glyph dropped onto text (a tick
    in a box) reads fine in place."""
    extents: Dict[int, List[float]] = {}
    for char in chars:
        extent = extents.setdefault(char.run, [char.x0, char.x1, 0])
        extent[0] = min(extent[0], char.x0)
        extent[1] = max(extent[1], char.x1)
        extent[2] += 0 if char.text.isspace() else 1
    boxes = sorted(extents.values())
    for i, (a0, a1, a_count) in enumerate(boxes):
        for b0, b1, b_count in boxes[i + 1:]:
            if b0 >= a1:
                break  # sorted by left edge: no later run reaches back into this one
            narrower = (a1 - a0, a_count) if a1 - a0 <= b1 - b0 else (b1 - b0, b_count)
            if narrower[1] >= 2 and min(a1, b1) - max(a0, b0) > 0.5 * narrower[0]:
                return True
    return False


def _upright_text(chars: Sequence[Char]) -> str:
    words: List[_Word] = []
    for line in _clusters(chars, lambda char: char.top, Y_TOLERANCE):
        if _runs_overlap(line):
            # text drawn over other text: split each run into words on its own, then read
            # all words left to right (a typed value stays whole next to its placeholder)
            by_run: Dict[int, List[Char]] = {}
            for char in line:
                by_run.setdefault(char.run, []).append(char)
            line_words = [word for run in by_run.values() for word in _words(sorted(run, key=lambda c: c.cx))]
            words.extend(sorted(line_words, key=lambda word: word.x0))
        else:
            words.extend(_words(sorted(_without_covered_spaces(line), key=lambda char: char.cx)))
    lines = _clusters(words, lambda word: word.top, Y_TOLERANCE)
    return "\n".join(" ".join(word.text for word in line) for line in lines)


def _rotated_text(chars: Sequence[Char]) -> str:
    """Text that does not run left to right: each run in writing order, runs in reading order."""
    runs: Dict[int, List[Char]] = {}
    for char in chars:
        runs.setdefault(char.run, []).append(char)
    texts = ["".join(_LIGATURES.get(char.text, char.text) for char in run) for run in runs.values()]
    return " ".join(" ".join(text.split()) for text in texts if text.strip())


def cell_text(chars: Sequence[Char]) -> str:
    """The text of one cell (or any region) from its characters in stream order."""
    if not chars:
        return ""
    chars = _without_redrawn_runs(_without_hidden_copies(chars))
    upright = [char for char in chars if char.upright]
    rotated = [char for char in chars if not char.upright]
    parts = [_upright_text(upright) if upright else "", _rotated_text(rotated) if rotated else ""]
    return "\n".join(part for part in parts if part).strip()


# --- grids ------------------------------------------------------------------------------------------------

@dataclass
class GridCell:
    row: int
    col: int
    bbox: Rect
    rowspan: int = 1
    colspan: int = 1
    drawn: bool = True  # False: a grid position no drawn cell covers (e.g. an open corner)
    chars: List[Char] = field(default_factory=list)
    _text: Optional[str] = None

    @property
    def area(self) -> float:
        return max(self.bbox[2] - self.bbox[0], 0.0) * max(self.bbox[3] - self.bbox[1], 0.0)

    def contains(self, x: float, y: float) -> bool:
        return self.bbox[0] <= x < self.bbox[2] and self.bbox[1] <= y < self.bbox[3]

    @property
    def text(self) -> str:
        if self._text is None:
            self._text = cell_text(self.chars)
        return self._text


class TableGrid:
    """A found table's cells on PyMuPDF's grid, with spans measured from the cell boxes.

    Grid column ``j`` starts at the ``j``-th distinct cell left edge and row ``i`` at the
    top of row ``i`` of ``table.rows`` (PyMuPDF's own grid). A drawn cell spans every
    column and row line it crosses; a span that would cover another drawn cell is
    shrunk (width first) so every drawn cell keeps its own position.
    """

    def __init__(self, table):
        self.table = table
        rows = table.rows
        boxes = [tuple(box) for row in rows for box in row.cells if box is not None]
        self.n_rows = len(rows)
        self.n_cols = max(len(row.cells) for row in rows)
        self.bbox: Rect = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                           max(b[2] for b in boxes), max(b[3] for b in boxes))
        self.col_edges = sorted({b[0] for b in boxes})[:self.n_cols] + [self.bbox[2]]
        self.row_edges = [min(box[1] for box in row.cells if box is not None) for row in rows] + [self.bbox[3]]

        drawn: Dict[Tuple[int, int], GridCell] = {}
        for r, row in enumerate(rows):
            for c, box in enumerate(row.cells):
                if box is None:
                    continue
                colspan = bisect.bisect_left(self.col_edges, box[2] - SPAN_EPSILON, c, self.n_cols) - c
                rowspan = bisect.bisect_left(self.row_edges, box[3] - SPAN_EPSILON, r, self.n_rows) - r
                drawn[(r, c)] = GridCell(r, c, tuple(box), max(1, rowspan), max(1, colspan))
        covered = set()
        for (r, c), cell in sorted(drawn.items()):
            free = lambda rr, cc: (rr, cc) not in drawn and (rr, cc) not in covered  # noqa: E731
            width = 1
            while width < cell.colspan and free(r, c + width):
                width += 1
            height = 1
            while height < cell.rowspan and all(free(r + height, c + k) for k in range(width)):
                height += 1
            cell.rowspan, cell.colspan = height, width
            covered.update((r + dr, c + dc) for dr in range(height) for dc in range(width) if dr or dc)
        self.cells: List[GridCell] = list(drawn.values())
        self.holes: Dict[Tuple[int, int], GridCell] = {}
        for r in range(self.n_rows):
            for c in range(self.n_cols):
                if (r, c) not in drawn and (r, c) not in covered:
                    self.holes[(r, c)] = GridCell(r, c, (self.col_edges[c], self.row_edges[r], self.col_edges[c + 1],
                                                         self.row_edges[r + 1]), drawn=False)
        # cells by row band, for the character lookup
        self._bands: List[List[GridCell]] = [[] for _ in range(self.n_rows)]
        for cell in self.cells:
            first = max(0, bisect.bisect_right(self.row_edges, cell.bbox[1]) - 1)
            last = bisect.bisect_left(self.row_edges, cell.bbox[3], first, self.n_rows + 1)
            for r in range(first, min(last, self.n_rows)):
                self._bands[r].append(cell)

    # -- geometry --
    def contains(self, x: float, y: float) -> bool:
        return self.bbox[0] <= x < self.bbox[2] and self.bbox[1] <= y < self.bbox[3]

    def drawn_cell_at(self, x: float, y: float) -> Optional[GridCell]:
        """The smallest drawn cell containing (x, y)."""
        r = bisect.bisect_right(self.row_edges, y) - 1
        if not 0 <= r < self.n_rows:
            return None
        best = None
        for cell in self._bands[r]:
            if cell.contains(x, y) and (best is None or cell.area < best.area):
                best = cell
        return best

    def hole_at(self, x: float, y: float) -> Optional[GridCell]:
        r = bisect.bisect_right(self.row_edges, y) - 1
        c = bisect.bisect_right(self.col_edges, x) - 1
        return self.holes.get((r, c))

    def all_cells(self) -> List[GridCell]:
        return self.cells + list(self.holes.values())

    def has_overlapping_cells(self) -> bool:
        for band in self._bands:
            band = sorted(band, key=lambda cell: cell.bbox[0])
            for i, first in enumerate(band):
                a = first.bbox
                for second in band[i + 1:]:
                    b = second.bbox
                    if b[0] >= a[2]:
                        break  # sorted by left edge: no later cell reaches back into this one
                    overlap = (min(a[2], b[2]) - b[0]) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
                    if overlap > 0.25 * min(first.area, second.area):
                        return True
        return False

    # -- content --
    def text_rows(self) -> List[List[Optional[str]]]:
        """Cell text on the grid; None where a merged cell covers the position."""
        rows: List[List[Optional[str]]] = [[None] * self.n_cols for _ in range(self.n_rows)]
        for cell in self.all_cells():
            rows[cell.row][cell.col] = cell.text
        return rows

    def spans(self) -> Dict[Tuple[int, int], Tuple[int, int]]:
        return {(cell.row, cell.col): (cell.rowspan, cell.colspan) for cell in self.cells
                if cell.rowspan > 1 or cell.colspan > 1}

    def bold_rows(self) -> List[Optional[bool]]:
        """Per row: True when every glyph is bold, False when some is not, None without text."""
        bold: Dict[int, bool] = {}
        for cell in self.all_cells():
            for char in cell.chars:
                if not char.text.isspace():
                    bold[cell.row] = bold.get(cell.row, True) and char.bold
        return [bold.get(r) for r in range(self.n_rows)]


def assign_chars(chars: Sequence[Char], grids: Sequence[TableGrid]) -> List[Char]:
    """Give every character to the smallest drawn cell containing its centre, over all grids
    (so a nested table keeps its text and overlapping cells never share it); a character in
    no drawn cell goes to the grid position it lies in, if any. Returns the characters that
    belong to no table."""
    for grid in grids:
        for cell in grid.all_cells():
            cell.chars = []
            cell._text = None
    outside = []
    for char in chars:
        best = hole = None
        for grid in grids:
            if not grid.contains(char.cx, char.cy):
                continue
            cell = grid.drawn_cell_at(char.cx, char.cy)
            if cell is not None:
                if best is None or cell.area < best.area:
                    best = cell
            elif hole is None:
                hole = grid.hole_at(char.cx, char.cy)
        target = best or hole
        if target is None:
            outside.append(char)
        else:
            target.chars.append(char)
    return outside


# --- plausibility (T5) -----------------------------------------------------------------------------------

def rule_segments(page) -> Tuple[List[Tuple[float, float, float]], List[Tuple[float, float, float]]]:
    """Line-like vector graphics on ``page`` in the coordinates ``find_tables()`` uses:
    horizontal ``(y, x0, x1)`` and vertical ``(x, y0, y1)`` segments from stroked lines and
    rectangle outlines, and from filled rectangles at most 2 pt thick (hairline rules).
    Large filled areas (backgrounds, logo shapes) are not rules."""
    matrix = page.rotation_matrix if page.rotation else None
    horizontal, vertical = [], []

    def add_rect(rect, stroked):
        if matrix is not None:
            rect = rect * matrix
        x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
        if stroked:
            horizontal.extend([(y0, x0, x1), (y1, x0, x1)])
            vertical.extend([(x0, y0, y1), (x1, y0, y1)])
        elif y1 - y0 <= 2.0 and x1 - x0 > 2.0:
            horizontal.append(((y0 + y1) / 2, x0, x1))
        elif x1 - x0 <= 2.0 and y1 - y0 > 2.0:
            vertical.append(((x0 + x1) / 2, y0, y1))

    try:
        drawings = page.get_drawings()
    except Exception as e:  # pragma: no cover - damaged content streams
        logger.debug(f"get_drawings failed: {e}")
        return horizontal, vertical
    for path in drawings:
        stroked = path.get("type") in ("s", "fs") and path.get("color") is not None
        for item in path.get("items", ()):
            kind = item[0]
            if kind == "l" and stroked:
                p1, p2 = item[1], item[2]
                if matrix is not None:
                    p1, p2 = p1 * matrix, p2 * matrix
                if abs(p1.y - p2.y) <= 1.0:
                    horizontal.append(((p1.y + p2.y) / 2, min(p1.x, p2.x), max(p1.x, p2.x)))
                elif abs(p1.x - p2.x) <= 1.0:
                    vertical.append(((p1.x + p2.x) / 2, min(p1.y, p2.y), max(p1.y, p2.y)))
            elif kind == "re":
                add_rect(pymupdf.Rect(item[1]), stroked)
            elif kind == "qu":
                add_rect(item[1].rect, stroked)
    return horizontal, vertical


def _by_position(segments) -> Dict[int, list]:
    buckets: Dict[int, list] = {}
    for segment in segments:
        buckets.setdefault(int(segment[0] // EDGE_TOLERANCE), []).append(segment)
    return buckets


def _edge_drawn(position: float, start: float, end: float, buckets: Dict[int, list]) -> bool:
    """Do segments on this line cover at least 60% of the edge from ``start`` to ``end``?"""
    length = end - start
    if length <= 0:
        return False
    key = int(position // EDGE_TOLERANCE)
    nearby = [segment for k in (key - 1, key, key + 1) for segment in buckets.get(k, ())]
    pieces = sorted((max(s0, start), min(s1, end)) for p, s0, s1 in nearby
                    if abs(p - position) <= EDGE_TOLERANCE and s1 > start and s0 < end)
    covered, reach = 0.0, start
    for s0, s1 in pieces:
        if s1 > reach:
            covered += s1 - max(s0, reach)
            reach = s1
    return covered >= 0.6 * length


def is_ruled(grid: TableGrid, segments) -> bool:
    """Are at least two of the grid's cell borders drawn as lines?"""
    horizontal, vertical = (_by_position(segments[0]), _by_position(segments[1]))
    edges = set()
    for cell in grid.cells:
        x0, y0, x1, y1 = cell.bbox
        edges.update({("h", y0, x0, x1), ("h", y1, x0, x1), ("v", x0, y0, y1), ("v", x1, y0, y1)})
    drawn = 0
    for kind, position, start, end in edges:
        if _edge_drawn(position, start, end, horizontal if kind == "h" else vertical):
            drawn += 1
            if drawn >= 2:
                return True
    return False


def is_plausible(grid: TableGrid, page_area: float, segments: Callable[[], tuple]) -> bool:
    """Should this grid be emitted as a table? (T5)

    Kept: text in at least two rows and two columns, or cell borders drawn as lines.
    Not kept: a single cell; a grid with no text; a grid without drawn borders that has
    text in only one row or column (a logo made of filled shapes), has overlapping cells
    (frames and panels), or covers most of the page (a slide background).
    ``segments`` returns :func:`rule_segments` of the page (computed only when needed).
    """
    if grid.n_rows * grid.n_cols < 2:
        return False
    filled = [(cell.row, cell.col) for cell in grid.all_cells() if cell.text.strip()]
    if not filled:
        return False
    text_grid = len({r for r, _ in filled}) >= 2 and len({c for _, c in filled}) >= 2
    x0, y0, x1, y1 = grid.bbox
    page_sized = page_area > 0 and (x1 - x0) * (y1 - y0) >= 0.85 * page_area
    if text_grid and not page_sized and not grid.has_overlapping_cells():
        return True
    return is_ruled(grid, segments())


# --- page tables -----------------------------------------------------------------------------------------

HeaderRow = List[Tuple[str, int]]  # (text, colspan) per header cell


@dataclass
class PageTable:
    """A validated table of one page, ready to render.

    ``cells[r][c]`` is the text at grid position (r, c), None where a merged cell covers it.
    """

    cells: List[List[Optional[str]]]
    spans: Dict[Tuple[int, int], Tuple[int, int]]
    grid_bbox: Rect
    col_edges: List[float]
    bold_rows: List[Optional[bool]]  # per row: every glyph bold (True), not (False), no text (None)
    header: Optional[HeaderRow] = None  # a header row placed above the grid rows
    header_bbox: Optional[Rect] = None  # where that header was drawn, when it is on this page
    header_known: bool = False  # ``header`` is known to be a header row (not a data row)
    carried_header: bool = False  # ``header`` comes from the previous page's table

    @classmethod
    def from_grid(cls, grid: TableGrid) -> "PageTable":
        return cls(grid.text_rows(), grid.spans(), grid.bbox, list(grid.col_edges), grid.bold_rows())

    @property
    def n_cols(self) -> int:
        return len(self.col_edges) - 1

    @property
    def bbox(self) -> Rect:
        return _union(self.grid_bbox, self.header_bbox) if self.header_bbox else self.grid_bbox

    def rows(self) -> Tuple[List[List[str]], Dict[Tuple[int, int], Tuple[int, int]]]:
        rows = [[text or "" for text in row] for row in self.cells]
        spans = dict(self.spans)
        if self.header:
            head = [""] * self.n_cols
            head_spans = {}
            c = 0
            for text, colspan in self.header:
                if c >= self.n_cols:
                    break
                head[c] = text
                if colspan > 1:
                    head_spans[(0, c)] = (1, min(colspan, self.n_cols - c))
                c += max(1, colspan)
            rows.insert(0, head)
            spans = {(r + 1, c): span for (r, c), span in spans.items()}
            spans.update(head_spans)
        return rows, spans

    def table_data(self) -> TableData:
        rows, spans = self.rows()
        return TableData.from_raw(rows, {"is_complex": bool(spans), "cell_spans": spans})

    def header_row(self) -> HeaderRow:
        """The row rendered as this table's header."""
        if self.header:
            return list(self.header)
        return [(text or "", self.spans.get((0, c), (1, 1))[1]) for c, text in enumerate(self.cells[0])
                if text is not None]

    def first_row_texts(self) -> List[str]:
        return [text or "" for text in self.cells[0]]


def _external_header(table, n_cols: int) -> Tuple[Optional[HeaderRow], Optional[Rect]]:
    """PyMuPDF's header found above the ruled cells (a header row drawn without borders),
    when it names at least two of the columns."""
    header = getattr(table, "header", None)
    if header is None or not getattr(header, "external", False):
        return None, None
    names = [" ".join((name or "").split()) for name in (header.names or [])]
    if len(names) != n_cols or sum(1 for name in names if name) < 2:
        return None, None
    return [(name, 1) for name in names], tuple(header.bbox)


def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _touches(a: Rect, b: Rect) -> bool:
    """Overlap as the text output tests it: boxes that merely touch count."""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _union(a: Rect, b: Rect) -> Rect:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def extract_page_tables(page, found_tables: Sequence, textpage=None, text_tables: bool = True
                        ) -> Tuple[List[PageTable], Optional[List[Char]]]:
    """Turn ``page.find_tables()`` results into validated :class:`PageTable` objects, and
    (``text_tables``) add the borderless tables :func:`find_text_tables` finds elsewhere on
    the page. ``textpage`` is the text page ``find_tables()`` built (``TableFinder.textpage``),
    reused so the page text is not extracted twice.

    Returns the tables in page order and the page characters that are in none of them
    (body text, used by :func:`continue_table`), or None when the page has no table and its
    characters were never needed.
    """
    grids = []
    for table in found_tables:
        try:
            if table.rows:
                grids.append(TableGrid(table))
        except Exception as e:
            logger.debug(f"Skipping a table PyMuPDF could not lay out: {e}")
    if textpage is None and grids:
        textpage = getattr(grids[0].table, "textpage", None)
    chars = None
    if grids:
        chars = read_chars(textpage) if textpage is not None else table_chars(grids[0].table)
    page_area = abs(page.rect.width * page.rect.height)
    segment_cache: List[tuple] = []

    def segments():
        if not segment_cache:
            segment_cache.append(rule_segments(page))
        return segment_cache[0]

    tables: List[PageTable] = []
    outside = chars
    if grids:
        # validate, then re-assign the characters among the kept grids only
        outside = assign_chars(chars, grids)
        kept = [grid for grid in grids if is_plausible(grid, page_area, segments)]
        if len(kept) != len(grids):
            logger.debug(f"Page {page.number + 1}: {len(grids) - len(kept)} table candidate(s) are not tabular")
            outside = assign_chars(chars, kept)
        blocks = None
        for grid in kept:
            table = PageTable.from_grid(grid)
            header, header_bbox = _external_header(grid.table, grid.n_cols)
            if header_bbox is not None:
                # the header's box joins the table's only if no text block there reaches outside both
                if blocks is None:
                    blocks = text_blocks(page)
                near = [block for block in blocks if _touches(block[0], header_bbox)]
                if not claims_only_its_text(_union(grid.bbox, header_bbox), near):
                    header, header_bbox = None, None
            table.header, table.header_bbox = header, header_bbox
            table.header_known = header is not None
            tables.append(table)
        header_boxes = [t.header_bbox for t in tables if t.header_bbox]
        if header_boxes:
            outside = [char for char in outside
                       if not any(b[0] <= char.cx < b[2] and b[1] <= char.cy < b[3] for b in header_boxes)]
    if text_tables:
        found_text, outside = find_text_tables(page, outside, [t.bbox for t in tables], segments, textpage)
        for table in found_text:
            tables.append(table)
            x0, y0, x1, y1 = table.grid_bbox
            outside = [char for char in outside if not (x0 <= char.cx < x1 and y0 <= char.cy < y1)]
    tables.sort(key=lambda t: (t.bbox[1], t.bbox[0]))
    return tables, outside


# --- borderless tables (T12) -------------------------------------------------------------------------------

_NUMERIC = re.compile(r"[(\[]?[-+−–]?\s?[$€£¥]?\s?\d[\d\s,.'’]*%?[)\]]?|[-–—]")
_GAP = 8.0  # a gap at least this wide (and at least one font size) separates two columns


def _looks_tabular(words: Sequence[tuple]) -> bool:
    """Cheap gate before the text-strategy search. ``words`` are ``(x0, y0, x1, y1, text, ...)``.

    At least three text lines must break into three or more pieces at wide gaps, and some
    column of pieces (pieces after the first on their line that share a left or a right
    edge within 3 pt) must have at least three pieces, 60% of them numbers. A directory or
    a multi-column layout has the gaps but no such column.
    """
    lines = _clusters(list(words), lambda word: round((word[1] + word[3]) / 2, 1), Y_TOLERANCE)
    columnar = 0
    columns: Dict[Tuple[str, int], List[int]] = {}
    for line in lines:
        line = sorted(line, key=lambda word: word[0])
        pieces = [[line[0]]]
        for a, b in zip(line, line[1:]):
            if b[0] - a[2] >= max(_GAP, 0.8 * (a[3] - a[1])):
                pieces.append([b])
            else:
                pieces[-1].append(b)
        if len(pieces) < 3:
            continue
        columnar += 1
        for piece in pieces[1:]:
            number = bool(_NUMERIC.fullmatch(" ".join(word[4] for word in piece)))
            for key in (("left", round(piece[0][0] / 3)), ("right", round(piece[-1][2] / 3))):
                counts = columns.setdefault(key, [0, 0])
                counts[0] += 1
                counts[1] += number
    return columnar >= 3 and any(numbers >= 3 and numbers >= 0.6 * total for total, numbers in columns.values())


def _ink_extent(chars: Sequence[Char]) -> Optional[Tuple[float, float, float]]:
    ink = [char for char in chars if not char.text.isspace()]
    if not ink:
        return None
    return min(char.x0 for char in ink), max(char.x1 for char in ink), max(char.size for char in ink)


def _text_table(grid: TableGrid, chars: Sequence[Char], words: Sequence[tuple], ruled_rows: bool
                ) -> Optional[PageTable]:
    """Validate one ``find_tables(strategy="text")`` candidate; see :func:`find_text_tables`."""
    assign_chars(chars, [grid])
    cells = {(cell.row, cell.col): cell for cell in grid.all_cells()}
    rows = [r for r in range(grid.n_rows) if any(cells[(r, c)].text for c in range(grid.n_cols) if (r, c) in cells)]

    def pieces(r):
        """Non-empty cells of row r as (col, ink x0, ink x1, size)."""
        out = []
        for c in range(grid.n_cols):
            cell = cells.get((r, c))
            extent = _ink_extent(cell.chars) if cell else None
            if extent:
                out.append((c, *extent))
        return out

    def flow_edges(r):
        """Column edges that the text of row r runs across with ordinary word spacing."""
        crossed = set()
        row = pieces(r)
        for (c1, _, a1, size_a), (c2, b0, _, size_b) in zip(row, row[1:]):
            if b0 - a1 < 0.6 * max(size_a, size_b):
                crossed.update(range(c1 + 1, c2 + 1))
        return crossed

    # a column edge that running text crosses in most rows splits a label ("Line item | 1"):
    # merge the two columns
    flows = {r: flow_edges(r) for r in rows}
    merged = {edge for edge in range(1, grid.n_cols) if 2 * sum(1 for r in rows if edge in flows[r]) >= len(rows)}
    kept_edges = [edge for edge in range(1, grid.n_cols) if edge not in merged]
    groups = [list(range(a, b)) for a, b in zip([0] + kept_edges, kept_edges + [grid.n_cols])]

    def filled_groups(r):
        return {g for g, group in enumerate(groups) if any((r, c) in cells and cells[(r, c)].text for c in group)}

    def is_body(r):
        return len(filled_groups(r)) >= 2 and not (flows[r] - merged)

    def group_chars(r, g):
        return [char for c in groups[g] if (r, c) in cells for char in cells[(r, c)].chars]

    def row_size(r):
        return max((char.size for g in range(len(groups)) for char in group_chars(r, g)), default=0.0)

    # captions and headings above the aligned rows are not table rows
    while rows and not is_body(rows[0]):
        rows.pop(0)
    # a wrapped line of a cell (under the row, closer to it than rows are to each other,
    # no numbers, only in columns the row fills) belongs to that row
    gaps = sorted(grid.row_edges[b] - grid.row_edges[a + 1] for a, b in zip(rows, rows[1:]))
    typical_gap = gaps[len(gaps) // 2] if gaps else 0.0
    records: List[List[int]] = []
    for r in rows:
        if records:
            previous = records[-1]
            gap = grid.row_edges[r] - grid.row_edges[previous[-1] + 1]
            here = filled_groups(r)
            above = set().union(*(filled_groups(q) for q in previous))
            if (here and here <= above and gap <= 0.5 * row_size(r) and gap < 0.7 * typical_gap
                    and not (flows[r] - merged)
                    and not any(_NUMERIC.fullmatch(cell_text(group_chars(r, g)).strip()) for g in here)):
                previous.append(r)
                continue
        records.append([r])
    # notes below the aligned rows are not table rows either
    while records and not is_body(records[-1][0]):
        records.pop()
    if len(records) < 3 or any(flows[r] - merged for record in records for r in record):
        return None  # too short, or text runs across a column boundary in some rows only
    table = []
    for record in records:
        table.append([cell_text(sorted((char for r in record for char in group_chars(r, g)),
                                       key=lambda char: (char.run, char.x0)))
                      for g in range(len(groups))])
    rows = [record[0] for record in records]
    last_row = records[-1][-1]
    used = [g for g in range(len(groups)) if any(row[g] for row in table)]
    table = [[row[g] for g in used] for row in table]
    groups = [groups[g] for g in used]
    n_cols = len(groups)
    filled = [text for row in table for text in row if text]
    single = sum(1 for row in table if sum(1 for text in row if text) < 2)
    numeric_columns = 0
    for g in range(1, n_cols):
        texts = [row[g].strip() for row in table if row[g]]
        numbers = sum(1 for text in texts if _NUMERIC.fullmatch(text))
        if numbers >= max(2, 0.6 * len(texts)):
            numeric_columns += 1
    if (n_cols < 3 and not (ruled_rows and n_cols >= 2)) or numeric_columns == 0:
        return None
    if len(filled) < 0.5 * len(table) * n_cols or 4 * single > len(table):
        return None
    if sum(1 for text in filled if len(text) <= 40) < 0.7 * len(filled):
        return None
    col_edges = [grid.col_edges[group[0]] for group in groups] + [grid.col_edges[groups[-1][-1] + 1]]
    top, bottom = grid.row_edges[rows[0]], grid.row_edges[last_row + 1]
    x0, x1 = col_edges[0], col_edges[-1]
    # no word may straddle a column boundary, nor the table's left or right edge (text running on
    # past the last column would be cut off)
    for w in words:
        if top <= (w[1] + w[3]) / 2 < bottom and w[2] > x0 and w[0] < x1:
            if any(w[0] < edge - 1 and w[2] > edge + 1 for edge in col_edges):
                return None
    if _is_table_of_contents(table):
        return None
    bold = []
    for record in records:
        ink = [char for r in record for c in range(grid.n_cols) if (r, c) in cells
               for char in cells[(r, c)].chars if not char.text.isspace()]
        bold.append(all(char.bold for char in ink) if ink else None)
    return PageTable(table, {}, (x0, top, x1, bottom), col_edges, bold)


_LEADER = re.compile(r"\.{4,}|(?:\. ){3,}|…{2,}|·{3,}")
_PAGE_NUMBER = re.compile(r"\d{1,3}")


def _is_table_of_contents(table: List[List[str]]) -> bool:
    """Dot leaders, or entries whose only number is a last-column page number that never goes down
    (``Chapter 2 | Market overview | p. 5 | 5``): a table of contents, which reads better as text."""
    if any(_LEADER.search(text) for row in table for text in row):
        return True
    last = [row[-1].strip() for row in table if row[-1].strip()]
    others = [text.strip() for row in table for text in row[1:-1] if text.strip()]
    if len(last) < 3 or not all(_PAGE_NUMBER.fullmatch(text) for text in last):
        return False
    pages = [int(text) for text in last]
    if any(b < a for a, b in zip(pages, pages[1:])):
        return False
    return not any(_NUMERIC.fullmatch(text) for text in others)


def _ruled_rows(bbox: Rect, horizontal) -> bool:
    """Is the region bounded by horizontal rules spanning most of its width (booktabs)?"""
    x0, y0, x1, y1 = bbox
    spans = [(y, s0, s1) for y, s0, s1 in horizontal if min(s1, x1) - max(s0, x0) >= 0.8 * (x1 - x0)]
    return (any(y0 - 12 <= y <= y0 + 6 for y, _, _ in spans) and any(y1 - 6 <= y <= y1 + 12 for y, _, _ in spans))


def text_blocks(page) -> List[Tuple[Rect, List[Tuple[Rect, float]]]]:
    """The text blocks the text output reads (``get_text("dict")`` with its flags), each as its box
    and the boxes and sizes of its non-blank spans, in the coordinates ``find_tables()`` uses."""
    matrix = page.rotation_matrix if page.rotation else None

    def box(bbox) -> Rect:
        return tuple(pymupdf.Rect(bbox) * matrix) if matrix is not None else tuple(bbox)

    blocks = []
    for block in page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_LIGATURES)["blocks"]:
        if block.get("type") != 0:
            continue
        spans = [(box(span["bbox"]), span["size"]) for line in block["lines"] for span in line["spans"]
                 if span["text"].strip()]
        blocks.append((box(block["bbox"]), spans))
    return blocks


def claims_only_its_text(bbox: Rect, blocks: Sequence[Tuple[Rect, List[Tuple[Rect, float]]]]) -> bool:
    """Does every text block that touches ``bbox`` lie inside it?

    The text output skips every block that touches a table's box, so a box that touches a block
    with text outside it (a caption or note set at the rows' own leading, a sidebar, the rest of a
    comment) would silently drop that text.
    """
    x0, y0, x1, y1 = bbox
    for block_bbox, spans in blocks:
        if not _touches(block_bbox, bbox):
            continue
        for (sx0, sy0, sx1, sy1), size in spans:
            slack = 0.35 * size  # the text output measures full glyph heights, the table small ones
            if sx0 < x0 - 1 or sx1 > x1 + 1 or sy0 < y0 - slack or sy1 > y1 + slack:
                return False
    return True


def find_text_tables(page, chars: Optional[Sequence[Char]], exclude: Sequence[Rect],
                     segments: Callable[[], tuple], textpage=None) -> Tuple[List[PageTable], Optional[List[Char]]]:
    """Tables drawn without vertical rules (booktabs, borderless), found with PyMuPDF's text
    strategy on pages where several lines break into three or more pieces at wide gaps.

    A candidate is kept only when, after dropping caption/note lines above and below it:
    it has at least three rows and three columns (two with booktabs rules above and below),
    at least one column besides the first is mostly numbers, no row's text runs across a
    column boundary with ordinary word spacing (a boundary that splits a label in most rows
    is merged instead), no word straddles a boundary, at least half the cells have text, at
    most a quarter of the rows have a single cell, 70% of the cells are short (at most 40
    characters), it is not a table of contents, no word runs over its left or right edge, and
    every text block touching it lies inside it (:func:`claims_only_its_text`). Prose, two-column
    layouts, key/value blocks, slide text boxes and tables of contents fail these checks, and so
    does a table whose caption, notes or neighbouring text share a text block with its rows; their
    text stays with the text path.

    ``chars`` are the page characters outside ``exclude`` (the tables already found), or None
    when they have not been read yet; the second value returned is that list, read if needed.
    """
    if textpage is not None:
        words = textpage.extractWORDS()
    else:
        words = page.get_text("words")
        if page.rotation:
            matrix = page.rotation_matrix
            words = [tuple(pymupdf.Rect(w[:4]) * matrix) + tuple(w[4:]) for w in words]
    words = [w for w in words if not any(b[0] <= (w[0] + w[2]) / 2 < b[2] and b[1] <= (w[1] + w[3]) / 2 < b[3]
                                         for b in exclude)]
    if not _looks_tabular(words):
        return [], chars
    if chars is None:
        chars = read_chars(textpage) if textpage is not None else page_chars(page)
    chars = [char for char in chars if not any(b[0] <= char.cx < b[2] and b[1] <= char.cy < b[3] for b in exclude)]
    try:
        finder = page.find_tables(strategy="text")
        candidates = list(getattr(finder, "tables", None) or [])
    except Exception as e:
        logger.debug(f"Text-strategy table search failed: {e}")
        return [], chars
    tables: List[PageTable] = []
    blocks = None
    for candidate in candidates:
        try:
            grid = TableGrid(candidate)
        except Exception:
            continue
        if any(_overlaps(grid.bbox, box) for box in list(exclude) + [t.grid_bbox for t in tables]):
            continue
        table = _text_table(grid, chars, words, _ruled_rows(grid.bbox, segments()[0]))
        if table is not None:
            if blocks is None:
                blocks = text_blocks(page)
            if not claims_only_its_text(table.grid_bbox, blocks):
                continue
            tables.append(table)
            x0, y0, x1, y1 = table.grid_bbox
            chars = [char for char in chars if not (x0 <= char.cx < x1 and y0 <= char.cy < y1)]
    return tables, chars


# --- tables continued across a page break (T11) ----------------------------------------------------------

@dataclass
class TableCarry:
    """The last table of a page, when nothing but the page's bottom 8% follows it."""

    page_num: int
    col_edges: List[float]
    header: HeaderRow
    header_is_known: bool  # the header row is known to be a header (styled, or drawn above the cells)
    header_lines: Set[str] = field(default_factory=set)  # text lines in that page's top band


def _has_text(chars: Sequence[Char], top: float, bottom: float) -> bool:
    return any(top < char.cy < bottom and not char.text.isspace() for char in chars)


def _lines(chars: Sequence[Char], top: float, bottom: float) -> Set[str]:
    """The text lines with their middle between ``top`` and ``bottom``, digits masked (so running
    headers and footers with page numbers compare equal from page to page)."""
    runs: Dict[int, List[Char]] = {}
    for char in chars:
        runs.setdefault(char.run, []).append(char)
    lines = set()
    for run in runs.values():
        ink = [char for char in run if not char.text.isspace()]
        if ink and top < sorted(char.cy for char in ink)[len(ink) // 2] < bottom:
            lines.add(re.sub(r"\d+", "#", " ".join("".join(char.text for char in run).split())))
    return lines


def _header_is_known(table: PageTable) -> bool:
    if table.header is not None:
        return table.header_known
    bold = table.bold_rows
    return len(bold) > 1 and bold[0] is True and bold[1] is False


def continue_table(tables: List[PageTable], outside: Sequence[Char], page_num: int, page_height: float,
                   carry: Optional[TableCarry]) -> Optional[TableCarry]:
    """Keep the first data row of a table continued from the previous page out of the header.

    The first table on this page continues the previous page's last table when the pages
    are consecutive, the columns line up (same left and right edges, and each column edge of
    this table is one of the previous table's), nothing but the bottom 8% of the previous page
    follows the previous table, and the only text above this table is in the top 8% of this
    page and repeats a line from the previous page's top 8% (a running header; page numbers
    masked). A heading over a new table is not a running header. If its first row repeats
    the previous header, or is styled as a header (bold over a non-bold row), it keeps that
    header. Otherwise its first row is data: it gets the previous page's header row when that
    row is known to be a header (bold over non-bold, or drawn above the ruled cells) and the
    columns are the same, and an empty header row otherwise -- a plain first row may just as
    well be the first key/value pair of a form, and repeating it would duplicate data.

    Returns the carry for the next page.
    """
    band = MARGIN_BAND * page_height
    if tables and carry is not None and carry.page_num == page_num - 1:
        first = tables[0]
        edges, previous = first.col_edges, carry.col_edges
        same_columns = len(edges) == len(previous) and all(
            abs(a - b) <= EDGE_TOLERANCE for a, b in zip(edges, previous))
        lined_up = (abs(edges[0] - previous[0]) <= EDGE_TOLERANCE and abs(edges[-1] - previous[-1]) <= EDGE_TOLERANCE
                    and all(any(abs(a - b) <= EDGE_TOLERANCE for b in previous) for a in edges))
        only_running_header = (not _has_text(outside, band, first.bbox[1] - 1)
                               and _lines(outside, -1.0, first.bbox[1] - 1) <= carry.header_lines)
        if first.header is None and lined_up and only_running_header:
            repeated = [text for text, _ in carry.header if text]
            own = [text for text in first.first_row_texts() if text]
            if own != repeated and not _header_is_known(first):
                if same_columns and carry.header_is_known:
                    first.header, first.header_known = list(carry.header), True
                else:
                    first.header, first.header_known = [("", 1)] * first.n_cols, False
                first.carried_header = True
    if not tables:
        return None
    last = max(tables, key=lambda t: t.bbox[3])
    if _has_text(outside, last.bbox[3] + 1, page_height - band):
        return None
    return TableCarry(page_num, list(last.col_edges), last.header_row(), _header_is_known(last),
                      _lines(outside, -1.0, band))
