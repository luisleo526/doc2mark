"""Reading order of the items of a PDF page: columns, sidebars and rotated pages.

The PDF pipeline turns a page into items (text blocks, tables, pictures, running headers and
footers) and orders them here. The default is the order the pipeline always used: top to bottom
(for a ``/Rotate`` page, top to bottom as the page is displayed). A page is read column by column
only on clear evidence of columns:

* a gutter: a vertical strip of white space that separates items standing side by side;
* items that cross the gutter (a title, an abstract, a figure or a caption across the columns, a
  page number) cross it *between* the column text, never beside it; they cut the page into bands
  and keep their place between the bands;
* in a band, at least one side is running text (three or more lines ten or more font sizes wide)
  and the other side has text too;
* the two sides are not a table or a form: they do not start their items at the same heights
  (labels next to their values, a grid of text boxes, parallel texts).

A band that passes is read left side, then right side (each side can hold further columns); a
narrow side beside a much wider one (a sidebar, a pull-quote, margin notes) is read after it as a
whole. Anything else keeps the top-to-bottom order. Running headers and footers and footnotes are
never part of a column: they are placed by their height, so a header opens the page and a footnote
or page number closes it. Pieces of one text block always stay together and in order.

Everything here is deterministic geometry on item boxes; the result is always a permutation of the
items, so reordering can neither drop nor duplicate text.
"""

from dataclasses import dataclass, field
from typing import Hashable, List, Optional, Sequence, Tuple

Box = Tuple[float, float, float, float]

# A text line counts as running text from this many font sizes wide; a side of a band is running
# text from this many such lines.
_FLOW_LINE_EMS = 10.0
_FLOW_MIN_LINES = 3
# Items of the two sides that start within this share of a font size of each other (at least
# _ALIGN_MIN points) start on the same row; from this share of a side's items (its first one aside)
# the band is a table, a form or parallel text.
_ALIGN_EMS = 0.35
_ALIGN_MIN = 2.0
_ALIGNED_SHARE = 0.5
# A side whose lines are narrower than this share of the other side's is secondary (a sidebar).
_SECONDARY_WIDTH = 0.6
_MAX_DEPTH = 6
_MAX_UNITS = 400   # pages with more items (maps, charts) keep the top-to-bottom order


@dataclass
class Region:
    """What the reading order knows about one item of a page.

    ``box`` is the item's box as the page is displayed (``/Rotate`` applied), ``None`` when
    unknown. ``kind`` is ``text``, ``table`` or ``image``. ``lines`` holds ``(width, font size)`` of
    each line of a text item. Items with the same ``group`` (the pieces of one text block) are read
    together. ``anchored`` items (running headers and footers, footnotes, items without a box) are
    never put in a column; they are placed by their height."""

    box: Optional[Box]
    kind: str = "text"
    lines: Tuple[Tuple[float, float], ...] = ()
    group: Optional[Hashable] = None
    anchored: bool = False


@dataclass(eq=False)
class _Unit:
    members: List[int]
    box: Box
    kind: str
    lines: List[Tuple[float, float]] = field(default_factory=list)

    @property
    def top(self) -> float:
        return self.box[1]

    @property
    def middle(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]


def reading_order(regions: Sequence[Region]) -> List[int]:
    """The reading order of ``regions``, given in top-to-bottom order: a permutation of their
    indexes. It is ``0, 1, 2, …`` unless the page shows columns (see the module docstring)."""
    identity = list(range(len(regions)))
    units, anchored = _units(regions)
    if len(units) < 2 or len(units) > _MAX_UNITS:
        return identity
    ordered = _order(units, 0)
    body = [index for unit in ordered for index in unit.members]
    if body == [index for unit in units for index in unit.members]:
        return identity
    result = _place_anchored(ordered, anchored, regions)
    return result if sorted(result) == identity else identity


def _units(regions: Sequence[Region]) -> Tuple[List[_Unit], List[int]]:
    """Layout units (one per text block, table or picture) in top-to-bottom order, and the
    anchored items."""
    units: List[_Unit] = []
    by_group = {}
    anchored: List[int] = []
    for index, region in enumerate(regions):
        box = region.box
        if region.anchored or box is None or box[2] < box[0] or box[3] < box[1]:
            anchored.append(index)
            continue
        key = (region.kind, region.group) if region.group is not None else None
        unit = by_group.get(key) if key is not None else None
        if unit is None:
            unit = _Unit([index], tuple(box), region.kind, list(region.lines))
            units.append(unit)
            if key is not None:
                by_group[key] = unit
        else:
            unit.members.append(index)
            unit.box = (min(unit.box[0], box[0]), min(unit.box[1], box[1]),
                        max(unit.box[2], box[2]), max(unit.box[3], box[3]))
            unit.lines.extend(region.lines)
    return units, anchored


def _place_anchored(ordered: List[_Unit], anchored: List[int], regions: Sequence[Region]) -> List[int]:
    """The units' items with each anchored item before the first unit (in reading order) that
    starts at or below it, or at the end."""
    result = [index for unit in ordered for index in unit.members]
    if not anchored:
        return result
    tops = [(unit.top, unit.members[0]) for unit in ordered]
    slots = {}
    for index in anchored:
        box = regions[index].box
        top = box[1] if box is not None else None
        slot = None
        if top is not None:
            slot = next((first for unit_top, first in tops if unit_top >= top), None)
        slots.setdefault(slot, []).append(index)
    placed = []
    for index in result:
        placed.extend(slots.pop(index, []))
        placed.append(index)
    placed.extend(slots.pop(None, []))
    return placed


def _order(units: List[_Unit], depth: int) -> List[_Unit]:
    if depth > _MAX_DEPTH or len(units) < 2:
        return units
    segments = _column_split(units)
    if segments is None:
        return units
    ordered: List[_Unit] = []
    for kind, parts in segments:
        if kind == "columns":
            for side in parts:
                ordered.extend(_order(side, depth + 1))
        elif kind == "band":
            ordered.extend(_order(parts[0], depth + 1))
        else:
            ordered.extend(parts[0])
    return ordered


def _overlap(a: _Unit, b: _Unit) -> float:
    return min(a.box[3], b.box[3]) - max(a.box[1], b.box[1])


def _size(units: Sequence[_Unit]) -> float:
    sizes = sorted(size for unit in units for _, size in unit.lines if size > 0)
    return sizes[len(sizes) // 2] if sizes else 10.0


def _gutters(units: List[_Unit]) -> List[Tuple[float, float]]:
    """Candidate gutters: the white space between items that stand side by side (overlapping
    vertically), grouped where those gaps share a common strip at least half a font size wide."""
    min_gap = max(6.0, 0.6 * _size(units))
    gaps = []
    for unit in units:
        left = [other for other in units
                if other is not unit and other.box[2] <= unit.box[0] and _overlap(other, unit) > 0]
        if left:
            edge = max(other.box[2] for other in left)
            if unit.box[0] - edge >= min_gap:
                gaps.append((edge, unit.box[0]))
    strips: List[Tuple[float, float]] = []
    for start, end in sorted(gaps):
        if strips and start < strips[-1][1] and end > strips[-1][0]:
            low, high = max(strips[-1][0], start), min(strips[-1][1], end)
            if high - low >= min_gap:
                strips[-1] = (low, high)
                continue
        strips.append((start, end))
    return [strip for strip in dict.fromkeys(strips) if strip[1] - strip[0] >= min_gap]


def _column_split(units: List[_Unit]):
    """A column reading of ``units`` as segments in reading order, or None.

    Every candidate gutter whose crossing items all sit between the items beside it is used at
    once: the crossing items (spanning titles, figures, captions, page numbers) cut the region
    into bands, and in each band the items fall into the slots between the gutters."""
    if sum(1 for unit in units if unit.kind == "text") < 2:
        return None
    gutters = sorted(gutter for gutter in _gutters(units) if _clean(units, gutter))
    if not gutters:
        return None
    crossing = [unit for unit in units if any(_crosses(unit, gutter) for gutter in gutters)]
    crossing_ids = set(map(id, crossing))
    separators = sorted(crossing, key=lambda unit: unit.middle)
    bands: List[List[_Unit]] = [[] for _ in range(len(separators) + 1)]
    for unit in units:
        if id(unit) not in crossing_ids:
            bands[sum(1 for separator in separators if separator.middle < unit.middle)].append(unit)
    segments, columns_found = [], False
    for number, band in enumerate(bands):
        slots: List[List[_Unit]] = [[] for _ in range(len(gutters) + 1)]
        for unit in band:
            slots[sum(1 for _, high in gutters if unit.box[0] >= high - 1.0)].append(unit)
        filled = [slot for slot in slots if slot]
        if len(filled) >= 2:
            if _is_columns(filled):
                if len(filled) == 2 and _secondary(filled[0], filled[1]):
                    filled = [filled[1], filled[0]]
                segments.append(("columns", filled))
                columns_found = True
            else:
                segments.append(("keep", [band]))
        elif band:
            segments.append(("band", [band]))
        if number < len(separators):
            segments.append(("keep", [[separators[number]]]))
    return segments if columns_found else None


def _crosses(unit: _Unit, gutter: Tuple[float, float]) -> bool:
    low, high = gutter
    return unit.box[2] > low + 1.0 and unit.box[0] < high - 1.0


def _clean(units: List[_Unit], gutter: Tuple[float, float]) -> bool:
    """Is ``gutter`` a column gutter: text on both sides, and every item across it sitting between
    the items beside it (above or below them, never next to them)?"""
    low, high = gutter
    left = [unit for unit in units if unit.box[2] <= low + 1.0]
    right = [unit for unit in units if unit.box[0] >= high - 1.0]
    if not any(unit.kind == "text" for unit in left) or not any(unit.kind == "text" for unit in right):
        return False
    beside = left + right
    for unit in units:
        if _crosses(unit, gutter) and any(
                _overlap(unit, other) > max(1.5, 0.25 * min(unit.height, other.height)) for other in beside):
            return False
    return True


def _is_columns(slots: List[List[_Unit]]) -> bool:
    """Do the filled slots of a band hold columns: running text in one of them, text in at least
    two, and no two neighbours starting their items on the same rows?"""
    if sum(1 for slot in slots if any(unit.kind == "text" for unit in slot)) < 2:
        return False
    if not any(_flowing(slot) for slot in slots):
        return False
    return not any(_row_aligned(first, second) for first, second in zip(slots, slots[1:]))


def _row_aligned(left: List[_Unit], right: List[_Unit]) -> bool:
    """Do the two sides start their items on the same rows (a table, a form, parallel texts)?
    Each side's first item is left out: columns start at the same height too."""
    tolerance = max(_ALIGN_MIN, _ALIGN_EMS * _size(left + right))

    def share(side: List[_Unit], other: List[_Unit]) -> float:
        rest = sorted(side, key=lambda unit: unit.top)[1:]
        if not rest:
            return 0.0
        hits = sum(1 for unit in rest if any(abs(unit.top - peer.top) <= tolerance for peer in other))
        return hits / len(rest)

    return share(left, right) >= _ALIGNED_SHARE or share(right, left) >= _ALIGNED_SHARE


def _flowing(side: List[_Unit]) -> bool:
    """Is ``side`` running text: at least three lines at least ten font sizes wide?"""
    return sum(1 for unit in side for width, size in unit.lines
               if size > 0 and width >= _FLOW_LINE_EMS * size) >= _FLOW_MIN_LINES


def _widths(side: List[_Unit]) -> List[float]:
    return sorted(width for unit in side for width, _ in unit.lines)


def _secondary(left: List[_Unit], right: List[_Unit]) -> bool:
    """Is the left side a sidebar or margin notes beside the right side: not running text next to
    running text, or with lines much narrower and fewer than the right side's?"""
    if not _flowing(left) and _flowing(right):
        return True
    left_widths, right_widths = _widths(left), _widths(right)
    if not left_widths or not right_widths:
        return False
    left_width = left_widths[len(left_widths) // 2]
    right_width = right_widths[len(right_widths) // 2]
    return left_width < _SECONDARY_WIDTH * right_width and len(left_widths) < len(right_widths)
