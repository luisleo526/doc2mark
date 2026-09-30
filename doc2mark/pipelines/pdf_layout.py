"""Reading order of the items of a PDF page: columns, sidebars and rotated pages.

The PDF pipeline turns a page into items (text blocks, tables, pictures, running headers and
footers) and orders them here. The default is the order the pipeline always used: top to bottom
(for a ``/Rotate`` page, top to bottom as the page is displayed). A page is read column by column
only on clear evidence of columns:

* a gutter: a vertical strip of white space that separates items standing side by side;
* items across the gutter (a title, an abstract, a figure or a caption across the columns, a
  page number) sit *between* the column text, never beside it; they cut the page into bands and
  keep their place between the bands. An item reaches across the gutter when it passes both of
  its edges or is centred in it; a line of ragged text that reaches a little into the gutter
  stays in its column. Pictures and drawings across the columns cut the page into bands too,
  also when they are not part of the output (``figures``);
* in a band, one side is running text (three or more lines ten or more font sizes wide) standing
  beside text on the other side;
* the two sides are not a table or a form: their text items do not start on the same rows
  (labels next to their values, terms centred on their definitions, a grid of text boxes,
  parallel texts).

A band that passes is read left side, then right side (each side can hold further columns); a
narrow side beside a much wider one (a sidebar, a pull-quote, margin notes) is read after it as a
whole. Anything else keeps the top-to-bottom order. Running headers and footers and footnotes are
never part of a column: a header opens the page, a footnote or page number closes it. A picture
or drawing with text over it (a background, a boxed sidebar's fill, a chart with its labels) is not
a column item either: an extracted picture is placed by its height in the column it stands in.
Pieces of one text block always stay together and in order.

Everything here is deterministic geometry on item boxes; the result is always a permutation of the
items, so reordering can neither drop nor duplicate text.
"""

from dataclasses import dataclass, field
from typing import Callable, Hashable, List, Optional, Sequence, Tuple

Box = Tuple[float, float, float, float]

# A text line counts as running text from this many font sizes wide; a side of a band is running
# text from this many such lines.
_FLOW_LINE_EMS = 10.0
_FLOW_MIN_LINES = 3
# Text items of the two sides that start (or are centred) within this share of a font size of each
# other (at least _ALIGN_MIN points) are on the same row. A side of short items (labels, terms) is in
# rows from _ROWS_SHORT of its items (its first one aside); two sides of running text only from
# _ROWS_RUNNING on both sides and at least two rows each: paragraphs set on the same line grid in two
# columns start level by chance.
_ALIGN_EMS = 0.35
_ALIGN_MIN = 2.0
_ROWS_SHORT = 0.5
_ROWS_RUNNING = 0.75
# A side whose lines are narrower than this share of the other side's is secondary (a sidebar).
_SECONDARY_WIDTH = 0.6
_MAX_DEPTH = 6
_MAX_UNITS = 400   # pages with more items (maps, charts) keep the top-to-bottom order


@dataclass
class Region:
    """What the reading order knows about one item of a page.

    ``box`` is the item's box as the page is displayed (``/Rotate`` applied), ``None`` when
    unknown. ``kind`` is ``text``, ``table`` or ``image``. ``lines`` holds ``(width, font size,
    top, bottom)`` of each line of a text item, as displayed. Items with the same ``group`` (the
    pieces of one text block) are read together. ``anchored`` items are never put in a column:
    ``edge`` ones (running headers and footers, footnotes) open or close the page, by the half of
    the page they are in; the others are placed by their height in the column they stand in."""

    box: Optional[Box]
    kind: str = "text"
    lines: Tuple[Tuple[float, float, float, float], ...] = ()
    group: Optional[Hashable] = None
    anchored: bool = False
    edge: bool = False


@dataclass(eq=False)
class _Unit:
    members: List[int]
    box: Box
    kind: str                 # text, table, image, or figure (a picture or drawing that is not an item)
    lines: List[Tuple[float, float, float, float]] = field(default_factory=list)
    rank: float = 0.0         # place in the top-to-bottom order

    @property
    def top(self) -> float:
        return self.box[1]

    @property
    def middle(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def centre(self) -> float:
        return (self.box[0] + self.box[2]) / 2

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]


def reading_order(regions: Sequence[Region], page_height: Optional[float] = None,
                  figures: Optional[Callable[[], Sequence[Box]]] = None) -> List[int]:
    """The reading order of ``regions``, given in top-to-bottom order: a permutation of their
    indexes. It is ``0, 1, 2, …`` unless the page shows columns (see the module docstring).
    ``page_height`` (as displayed) tells the top half of the page from the bottom half.
    ``figures()`` returns the boxes (as displayed) of the pictures and drawings of the page that
    are not items: they are asked for only when the page has a column gutter."""
    count = len(regions)
    identity = list(range(count))
    units, anchored = _units(regions)
    units, backgrounds = _split_backgrounds(units)
    anchored = sorted(anchored + [index for unit in backgrounds for index in unit.members])
    if len(units) < 2 or len(units) > _MAX_UNITS:
        return identity
    if not any(_clean(units, gutter) is not None for gutter in _gutters(units)):
        return identity   # no column gutter: the top-to-bottom order
    everything = list(regions)
    if figures is not None:
        ghosts = []
        for box in figures() or ():
            if _valid(box) and not any(unit.kind == "image" and _same_box(unit.box, box) for unit in units):
                ghosts.append(_Unit([len(everything)], tuple(box), "figure"))
                everything.append(Region(tuple(box), kind="figure"))
        ghosts, _ = _split_backgrounds(ghosts, units)
        units = _with_ranks(units, ghosts)
    ordered = _order(units, 0)
    if [unit.rank for unit in ordered] == sorted(unit.rank for unit in ordered):
        return identity   # nothing was read in columns
    if page_height is None:
        page_height = max(unit.box[3] for unit in units) + min(unit.box[1] for unit in units)
    result = [index for index in _place_anchored(ordered, anchored, everything, page_height) if index < count]
    return result if sorted(result) == identity else identity


def _units(regions: Sequence[Region]) -> Tuple[List[_Unit], List[int]]:
    """Layout units (one per text block, table or picture) in top-to-bottom order, and the
    anchored items. An anchored piece of a text block (a footnote line) stays with the block's
    other pieces."""
    placed_groups = {(region.kind, region.group) for region in regions
                     if region.group is not None and not region.anchored and _valid(region.box)}
    units: List[_Unit] = []
    by_group = {}
    anchored: List[int] = []
    for index, region in enumerate(regions):
        box = region.box
        key = (region.kind, region.group) if region.group is not None else None
        if not _valid(box) or (region.anchored and key not in placed_groups):
            anchored.append(index)
            continue
        unit = by_group.get(key) if key is not None else None
        if unit is None:
            unit = _Unit([index], tuple(box), region.kind, list(region.lines), rank=float(index))
            units.append(unit)
            if key is not None:
                by_group[key] = unit
        else:
            unit.members.append(index)
            unit.box = (min(unit.box[0], box[0]), min(unit.box[1], box[1]),
                        max(unit.box[2], box[2]), max(unit.box[3], box[3]))
            unit.lines.extend(region.lines)
    return units, anchored


def _valid(box: Optional[Box]) -> bool:
    return box is not None and box[2] >= box[0] and box[3] >= box[1]


def _same_box(a: Box, b: Box) -> bool:
    """Do two boxes cover (nearly) the same area (intersection over union at least 0.8)?"""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return False
    both = width * height
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - both
    return union > 0 and both / union >= 0.8


def _inside(unit: _Unit, box: Box) -> bool:
    return box[0] <= unit.centre <= box[2] and box[1] <= unit.middle <= box[3]


def _split_backgrounds(candidates: List[_Unit], text_units: Optional[List[_Unit]] = None
                       ) -> Tuple[List[_Unit], List[_Unit]]:
    """``candidates`` without the pictures and drawings that have text drawn over them (a page
    background, a banner, a boxed sidebar's fill, a chart with its labels), and those backgrounds.
    ``text_units`` default to the candidates."""
    text = [unit for unit in (candidates if text_units is None else text_units) if unit.kind == "text"]
    kept, backgrounds = [], []
    for unit in candidates:
        if unit.kind in ("image", "figure") and any(_inside(other, unit.box) for other in text):
            backgrounds.append(unit)
        else:
            kept.append(unit)
    return kept, backgrounds


def _with_ranks(units: List[_Unit], ghosts: List[_Unit]) -> List[_Unit]:
    """``units`` and ``ghosts`` in one top-to-bottom order: a ghost ranks just before the first unit
    that starts at or below it."""
    for ghost in ghosts:
        following = next((unit for unit in units if unit.top >= ghost.top), None)
        ghost.rank = (following.rank if following is not None else units[-1].rank + 1.0) - 0.5
    return sorted(units + ghosts, key=lambda unit: unit.rank)


def _place_anchored(ordered: List[_Unit], anchored: List[int], regions: Sequence[Region],
                    page_height: float) -> List[int]:
    """The units' items (in reading order) with the anchored items put back: ``edge`` items at
    the start (top half of the page) or the end (bottom half); the others before the first unit of
    their column (units overlapping them horizontally) that starts at or below them, else after the
    last unit of their column, else by height alone."""
    result = [index for unit in ordered for index in unit.members]
    if not anchored:
        return result
    start, end = [], []
    before, after = {}, {}
    for index in anchored:
        region = regions[index]
        box = region.box
        if box is None:
            end.append(index)
            continue
        if region.edge:
            (start if (box[1] + box[3]) / 2 < page_height / 2 else end).append(index)
            continue
        column = [unit for unit in ordered if min(unit.box[2], box[2]) > max(unit.box[0], box[0])]
        below = next((unit for unit in column if unit.top >= box[1]), None)
        if below is None and not column:
            below = next((unit for unit in ordered if unit.top >= box[1]), None)
        if below is not None:
            before.setdefault(below.members[0], []).append(index)
        elif column:
            after.setdefault(column[-1].members[-1], []).append(index)
        else:
            end.append(index)
    placed = list(start)
    for index in result:
        placed.extend(before.pop(index, []))
        placed.append(index)
        placed.extend(after.pop(index, []))
    return placed + end


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
    sizes = sorted(line[1] for unit in units for line in unit.lines if line[1] > 0)
    return sizes[len(sizes) // 2] if sizes else 10.0


def _gutters(units: List[_Unit]) -> List[Tuple[float, float]]:
    """Candidate gutters: the white space between items that stand side by side (overlapping
    vertically), grouped where those gaps share a common strip at least half a font size wide.
    Pictures and drawings that are not items (a rule in the gutter) do not make gutters."""
    units = [unit for unit in units if unit.kind != "figure"]
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
    into bands, and in each band the items fall into the slots between the gutters, by their
    centre. A pull-quote set across the gutter with the column text wrapped around it (an island)
    is read after the columns of its band. A picture or drawing across the gutter that the column
    text runs past (a rule under a pull-quote) stays in the band."""
    if sum(1 for unit in units if unit.kind == "text") < 2:
        return None
    gutters, islands = [], []
    for gutter in _gutters(units):
        found = _clean(units, gutter)
        if found is not None:
            gutters.append(gutter)
            islands.extend(unit for unit in found if unit not in islands)
    if not gutters:
        return None
    gutters.sort()
    island_ids = set(map(id, islands))
    crossing = [unit for unit in units if id(unit) not in island_ids
                and any(_crosses(unit, gutter) for gutter in gutters)]
    beside = [unit for unit in units if unit not in crossing and id(unit) not in island_ids]
    crossing = [unit for unit in crossing if unit.kind != "figure" or not any(
        other.box[1] < unit.middle < other.box[3] for other in beside if other.kind != "figure")]
    crossing_ids = set(map(id, crossing))
    separators = sorted(crossing, key=lambda unit: unit.middle)

    def band_of(unit: _Unit) -> int:
        return sum(1 for separator in separators if separator.middle < unit.middle)

    bands: List[List[_Unit]] = [[] for _ in range(len(separators) + 1)]
    floating: List[List[_Unit]] = [[] for _ in range(len(separators) + 1)]
    for unit in units:
        if id(unit) in island_ids:
            floating[band_of(unit)].append(unit)
        elif id(unit) not in crossing_ids:
            bands[band_of(unit)].append(unit)
    segments, columns_found = [], False
    for number, band in enumerate(bands):
        slots: List[List[_Unit]] = [[] for _ in range(len(gutters) + 1)]
        for unit in band:
            slots[sum(1 for low, high in gutters if unit.centre > (low + high) / 2)].append(unit)
        filled = [slot for slot in slots if slot]
        if len(filled) >= 2 and _is_columns(filled):
            segments.append(("columns", _reading_slots(filled)))
            if floating[number]:
                segments.append(("keep", [floating[number]]))
            columns_found = True
        elif band or floating[number]:
            rest = sorted(band + floating[number], key=lambda unit: unit.rank)
            segments.append(("keep" if len(filled) >= 2 or floating[number] else "band", [rest]))
        if number < len(separators):
            segments.append(("keep", [[separators[number]]]))
    return segments if columns_found else None


def _crosses(unit: _Unit, gutter: Tuple[float, float]) -> bool:
    """Does ``unit`` reach across the gutter: past both of its edges, or centred in it (a page
    number)? A picture or drawing that is not an item crosses only past both edges (a rule in the
    gutter does not)."""
    low, high = gutter
    if unit.box[0] < low - 1.0 and unit.box[2] > high + 1.0:
        return True
    return unit.kind != "figure" and low <= unit.centre <= high


def _clean(units: List[_Unit], gutter: Tuple[float, float]) -> Optional[List[_Unit]]:
    """The islands of ``gutter`` when it is a column gutter, None when it is not.

    A column gutter has text on both sides, and every item across it sits between the items
    beside it (above or below them, never next to them), except for at most two islands: text
    items across the whole gutter, clear of the outer quarter of the columns on both sides and
    lower than 40% of the text beside them (a pull-quote with the columns wrapped around it).
    Pictures and drawings that are not items never decide."""
    low, high = gutter
    middle = (low + high) / 2
    items = [unit for unit in units if unit.kind != "figure"]
    beside = [unit for unit in items if not _crosses(unit, gutter)]
    left = [unit for unit in beside if unit.centre < middle]
    right = [unit for unit in beside if unit.centre >= middle]
    if not any(unit.kind == "text" for unit in left) or not any(unit.kind == "text" for unit in right):
        return None
    outer_left = min(unit.box[0] for unit in left)
    outer_right = max(unit.box[2] for unit in right)
    extent = max(unit.box[3] for unit in beside) - min(unit.box[1] for unit in beside)
    islands = []
    for unit in items:
        if not _crosses(unit, gutter) or not any(
                _overlap(unit, other) > max(1.5, 0.25 * min(unit.height, other.height)) for other in beside):
            continue
        island = (unit.kind == "text" and unit.box[0] < low - 1.0 and unit.box[2] > high + 1.0
                  and unit.box[0] >= outer_left + 0.25 * (low - outer_left)
                  and unit.box[2] <= outer_right - 0.25 * (outer_right - high)
                  and unit.height <= 0.4 * extent)
        if not island or len(islands) == 2:
            return None
        islands.append(unit)
    return islands


def _is_columns(slots: List[List[_Unit]]) -> bool:
    """Do the filled slots of a band hold columns: across every gutter, running text on one side
    standing beside text on the other, and the two sides not starting their items on the same
    rows?"""
    for first, second in zip(slots, slots[1:]):
        if not any(unit.kind == "text" for unit in first) or not any(unit.kind == "text" for unit in second):
            return False
        if not (_flowing(first, beside=second) or _flowing(second, beside=first)):
            return False
        if _row_aligned(first, second):
            return False
    return True


def _row_aligned(left: List[_Unit], right: List[_Unit]) -> bool:
    """Are the text items of the two sides on the same rows (a form's labels and values, dates and
    their entries, terms and their definitions, a grid of text boxes, parallel texts)? Each side's
    first item is left out: columns start at the same height too. An item is on a row with an item
    of the other side when both start at the same height, when their middles are level (a label or
    a term centred on its value, whatever its height), or when it is short (one or two lines) and
    its middle is level with the other item. Rows pair items one to one, so the labels of a figure
    beside one paragraph are not rows. See _ROWS_SHORT and _ROWS_RUNNING for how many rows it takes."""
    left = [unit for unit in left if unit.kind == "text"]
    right = [unit for unit in right if unit.kind == "text"]
    tolerance = max(_ALIGN_MIN, _ALIGN_EMS * _size(left + right))

    def aligned(side: List[_Unit], other: List[_Unit]) -> Tuple[int, int]:
        rest = sorted(side, key=lambda unit: unit.top)[1:]
        used, hits = set(), 0
        for unit in rest:
            peer = next((peer for peer in other if id(peer) not in used and (
                abs(unit.top - peer.top) <= tolerance or abs(unit.middle - peer.middle) <= tolerance
                or (len(unit.lines) <= 2 and peer.box[1] <= unit.middle <= peer.box[3]))), None)
            if peer is not None:
                used.add(id(peer))
                hits += 1
        return hits, len(rest)

    left_hits, left_rest = aligned(left, right)
    right_hits, right_rest = aligned(right, left)
    if not _flowing(left) and left_rest and left_hits >= _ROWS_SHORT * left_rest:
        return True
    if not _flowing(right) and right_rest and right_hits >= _ROWS_SHORT * right_rest:
        return True
    return (left_rest > 0 and right_rest > 0 and min(left_hits, right_hits) >= 2
            and left_hits >= _ROWS_RUNNING * left_rest and right_hits >= _ROWS_RUNNING * right_rest)


def _flowing(side: List[_Unit], beside: Optional[List[_Unit]] = None) -> bool:
    """Is ``side`` running text: at least three lines at least ten font sizes wide, one under the
    other (pieces of one row, such as the column labels of a table header, count once), and next
    to the text of ``beside`` when given (sharing heights with its text)?"""
    spans = [(line[2], line[3]) for unit in beside or () for line in unit.lines]
    rows = sorted((top + bottom) / 2 for unit in side for width, size, top, bottom in unit.lines
                  if size > 0 and width >= _FLOW_LINE_EMS * size
                  and (beside is None or any(min(bottom, high) > max(top, low) for low, high in spans)))
    stacked, last = 0, None
    for middle in rows:
        if last is None or middle - last > _ALIGN_MIN:
            stacked += 1
            last = middle
    return stacked >= _FLOW_MIN_LINES


def _widths(side: List[_Unit]) -> List[float]:
    return sorted(line[0] for unit in side for line in unit.lines)


def _median_width(slot: List[_Unit]) -> float:
    widths = _widths(slot)
    return widths[len(widths) // 2] if widths else 0.0


def _reading_slots(slots: List[List[_Unit]]) -> List[List[_Unit]]:
    """The slots of a column band in reading order: left to right, except that a sidebar, a
    pull-quote column or margin notes (lines much narrower than the widest column's, and fewer of
    them) come after the columns they stand beside."""
    widest = max(slots, key=_median_width)
    minor = [slot for slot in slots if slot is not widest
             and _median_width(slot) < _SECONDARY_WIDTH * _median_width(widest)
             and len(_widths(slot)) < len(_widths(widest))]
    return [slot for slot in slots if not any(slot is other for other in minor)] + minor


def cluster_boxes(boxes: Sequence[Box], tolerance: float = 3.0, min_size: float = 24.0,
                  max_boxes: int = 3000) -> List[Box]:
    """Group the boxes of vector drawing operations into figures: boxes that overlap or come within
    ``tolerance`` points of each other form one figure, and a figure inside another one is part of
    it (the bars inside a chart's frame). Figures whose longer side is shorter than
    ``min_size`` (bullets, icons, underlines of a word) are left out. Pages with more than
    ``max_boxes`` boxes (maps, dense charts) give no figures."""
    boxes = [tuple(box) for box in boxes if _valid(box)]
    if not boxes or len(boxes) > max_boxes:
        return []
    boxes.sort()
    parent = list(range(len(boxes)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    active: List[int] = []
    for index, box in enumerate(boxes):
        active = [other for other in active if boxes[other][2] + tolerance >= box[0]]
        for other in active:
            near = boxes[other]
            if near[1] - tolerance <= box[3] and box[1] - tolerance <= near[3]:
                parent[root(other)] = root(index)
        active.append(index)
    merged = {}
    for index, box in enumerate(boxes):
        key = root(index)
        seen = merged.get(key)
        merged[key] = box if seen is None else (min(seen[0], box[0]), min(seen[1], box[1]),
                                                max(seen[2], box[2]), max(seen[3], box[3]))
    # a figure inside another one (the bars inside a chart's frame) is part of it
    figures = sorted(merged.values(), key=lambda box: (box[2] - box[0]) * (box[3] - box[1]), reverse=True)
    outer: List[Box] = []
    for box in figures:
        if not any(o[0] - tolerance <= box[0] and box[2] <= o[2] + tolerance
                   and o[1] - tolerance <= box[1] and box[3] <= o[3] + tolerance for o in outer):
            outer.append(box)
    return [box for box in outer if max(box[2] - box[0], box[3] - box[1]) >= min_size]
