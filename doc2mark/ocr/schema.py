"""Structured OCR output schema for doc2mark.

The redesigned OCR layer returns a *structured* result instead of a single
free-form markdown blob. Each image becomes an :class:`OCRPage` with a hard
boundary between two concerns:

- ``raw``: what is literally on the page (verbatim transcription, tables,
  label/value fields) — no inference, no commentary.
- ``interpretation``: the model's reading of the page (document type, summary,
  key findings) — omitted for ``detail="raw"`` and for non-LLM providers
  (e.g. Tesseract) that cannot infer.

These models are emitted by the LLM providers via LangChain's
``with_structured_output(method="json_schema")``. Every field is defaulted so
that OpenAI strict mode (which requires all properties to be present) is
satisfiable, and Optional fields serialize as ``anyOf: [T, null]``.
"""

import html as _html
import re
from collections import Counter
from typing import Dict, List, Optional, Literal, Tuple

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Table HTML sanitization                                                     #
# --------------------------------------------------------------------------- #
# Table.html is produced by a vision model reading a (possibly adversarial)
# document image and flows into rendered output via OCRPage.to_markdown(). To
# avoid an HTML-injection / XSS sink, it is sanitized to a strict allowlist of
# table-structural tags + span attributes (plus the inert <br>); everything else
# is dropped. Every other model-supplied string is escaped when rendered (see
# "Markdown boundary" below).
_ALLOWED_TABLE_TAGS = frozenset({
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "col", "colgroup",
})
_ALLOWED_TABLE_ATTRS = frozenset({"colspan", "rowspan", "scope"})
_SCOPE_VALUES = frozenset({"row", "col", "rowgroup", "colgroup"})
_DANGEROUS_TAGS = (
    "script", "style", "iframe", "object", "embed", "link", "meta", "base",
    "form", "input", "button", "noscript", "template", "svg", "math",
)
# Block elements a model uses for line structure inside a cell (or around a table).
# They are unwrapped like every other non-table tag, but each becomes a line break
# first, so "<p>Q1</p><p>2024</p>" reads "Q1<br>2024", never "Q12024".
_BLOCK_TAGS = frozenset({
    "p", "div", "li", "ul", "ol", "dl", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "section", "article", "header", "footer", "address", "figure",
    "figcaption", "hr", "center", "main", "nav", "aside",
})
_ROW_GROUP_TAGS = ("thead", "tbody", "tfoot")
_TABLE_MARKUP_RE = re.compile(r"<\s*/?\s*(?:table|thead|tbody|tfoot|tr|td|th|caption)\b", re.I)
_PIPE_DELIMITER_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
_BR_TAG_RE = re.compile(r"<br\s*/?>", re.I)
_C0_CONTROL_RE = re.compile(r"[\x00-\x08\x0e-\x1f]")
_LINE_BREAK_RE = re.compile(r"[ \t]*\n\s*")
_ASCII_DIGITS_RE = re.compile(r"[0-9]+")  # str.isdigit() also accepts "²", which int() rejects

# Bounds on model-supplied spans. A colspan never exceeds the widest row's cell count
# (nor HTML's own cap of 1000) and a rowspan never runs past its row group, so a
# model cannot make one table cost seconds or megabytes. Past _MAX_GRID_CELLS the
# grid is left unpadded rather than materialized.
_MAX_COLSPAN = 1000
_MAX_GRID_CELLS = 250_000
# Column slots a table layout may visit; spans that claim more are dropped.
_MAX_LAYOUT_WORK = 500_000
# Padding may add at most this many empty cells per cell the model wrote (plus a small
# allowance), so a small table cannot be padded into megabytes.
_MAX_PADS_PER_CELL = 8
_PAD_ALLOWANCE = 64
# The pad-alignment search: rows longer than this, or past this many column slots per
# table, are padded at the end.
_MAX_ALIGNED_ROW_CELLS = 256
_MAX_ALIGNMENT_WORK = 200_000


def _clean_controls(text: str) -> str:
    """Normalize line separators to ``\\n`` and drop the other C0 control characters
    (``\\t`` and ``\\n`` are kept). ``\\r``, vertical tab and form feed are line breaks
    in the sources, so they become ``\\n`` instead of gluing words together."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n")
    return _C0_CONTROL_RE.sub("", text)


def _tag(element) -> str:
    return element.tag.lower() if isinstance(element.tag, str) else ""


def _strip_code_fence(text: str) -> str:
    """Strip a leading ```/```html ... ``` code fence a model might wrap the table in."""
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text[: text.rfind("```")]
    return text.strip()


def _split_pipe_row(line: str) -> List[str]:
    """Cells of one GFM pipe-table row (a ``|`` right after a backslash is cell text)."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells, current, last = [], "", ""
    for ch in body:
        if ch == "|" and last != "\\":
            cells.append(current)
            current = ""
        elif ch == "|":
            current = current[:-1] + "|"
        else:
            current += ch
        last = ch
    cells.append(current)
    return [cell.strip() for cell in cells]


def _escape_html_keep_breaks(text: str) -> str:
    return "<br>".join(_html.escape(part, quote=False) for part in _BR_TAG_RE.split(text))


def _pipe_tables_to_html(text: str) -> Optional[str]:
    """Convert the GFM pipe tables in a markup-free ``html`` field into HTML tables,
    keeping every other line as text; ``None`` when the field holds no pipe table."""
    lines = text.split("\n")
    out: List[str] = []
    found = False
    i = 0
    while i < len(lines):
        if ("|" in lines[i] and i + 1 < len(lines) and "|" in lines[i + 1]
                and _PIPE_DELIMITER_RE.match(lines[i + 1])):
            rows = [_split_pipe_row(lines[i])]
            i += 2
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_pipe_row(lines[i]))
                i += 1
            html = ["<table>"]
            for r, row in enumerate(rows):
                tag = "th" if r == 0 else "td"
                html.append("<tr>" + "".join(
                    f"<{tag}>{_escape_html_keep_breaks(cell)}</{tag}>" for cell in row) + "</tr>")
            html.append("</table>")
            out.append("".join(html))
            found = True
        else:
            if lines[i].strip():
                out.append(_escape_html_keep_breaks(lines[i].strip()))
            i += 1
    return "\n".join(out) if found else None


def _remove_keep_tail(element) -> None:
    """Remove ``element`` (and its content) but keep the text that follows it."""
    parent = element.getparent()
    if element.tail:
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + element.tail
        else:
            parent.text = (parent.text or "") + element.tail
    parent.remove(element)


def _break_around(element) -> None:
    """Line breaks on both sides of a block element (redundant ones are tidied later)."""
    element.addprevious(element.makeelement("br", {}))
    after = element.makeelement("br", {})
    after.tail, element.tail = element.tail, None
    element.addnext(after)


def _text_lines(text: Optional[str]) -> List[str]:
    """Non-blank lines of ``text``, whitespace-collapsed."""
    return [" ".join(line.split()) for line in (text or "").split("\n") if line.strip()]


def _wrap_stray_rows(frag) -> None:
    """Give rows/cells/row groups that sit outside any table a table of their own."""
    run: List = []

    def flush() -> None:
        if not run:
            return
        table = frag.makeelement("table", {})
        run[0].addprevious(table)
        row = None
        for element in run:
            if _tag(element) in ("td", "th"):
                if row is None:
                    row = table.makeelement("tr", {})
                    table.append(row)
                row.append(element)
            else:
                row = None
                table.append(element)
        run.clear()

    for child in list(frag):
        if _tag(child) in ("tr", "td", "th") + _ROW_GROUP_TAGS:
            run.append(child)
        else:
            flush()
    flush()


def _add_lines(parent, lines: List[str], *, at_start: bool = False) -> None:
    """Add ``lines`` to ``parent`` separated by <br> (at the start or the end of it)."""
    if not lines:
        return
    if at_start:
        old_text, existing = parent.text, list(parent)
        parent.text = lines[0]
        children = []
        for line in lines[1:]:
            br = parent.makeelement("br", {})
            br.tail = line
            children.append(br)
        if (old_text and old_text.strip()) or existing:
            br = parent.makeelement("br", {})
            br.tail = old_text
            children.append(br)
        parent[:] = children + existing  # one pass, not one insert per line
        return
    for line in lines:
        if len(parent) == 0 and not (parent.text or "").strip():
            parent.text = line
            continue
        br = parent.makeelement("br", {})
        br.tail = line
        parent.append(br)


def _caption_of(table):
    for child in table:
        if _tag(child) == "caption":
            return child
    caption = table.makeelement("caption", {})
    table.insert(0, caption)
    return caption


def _split_newlines_into_breaks(cell) -> None:
    """Newlines inside cell text become <br> (the shared HTML-cell rule); one pass."""
    def split(text: Optional[str]) -> Tuple[Optional[str], list]:
        pieces = _LINE_BREAK_RE.split(text or "")
        if len(pieces) == 1:
            return text, []
        breaks = []
        for piece in pieces[1:]:
            br = cell.makeelement("br", {})
            br.tail = piece
            breaks.append(br)
        return pieces[0], breaks

    cell.text, children = split(cell.text)
    added = bool(children)
    for child in list(cell):
        child.tail, breaks = split(child.tail)
        children.append(child)
        children.extend(breaks)
        added = added or bool(breaks)
    if added:
        cell[:] = children


def _tidy_breaks(cell) -> None:
    """Keep a <br> only between two pieces of content: drop leading, trailing and
    repeated ones (one pass; their text is kept)."""
    content_since_break = bool((cell.text or "").strip())
    last_kept = None
    for kid in list(cell):
        has_tail_text = bool((kid.tail or "").strip())
        if _tag(kid) == "br":
            if content_since_break:
                last_kept, content_since_break = kid, False
            else:
                _remove_keep_tail(kid)
        else:
            content_since_break = True
        if has_tail_text:
            content_since_break = True
    if last_kept is not None and not content_since_break:
        _remove_keep_tail(last_kept)


def _tidy_table(table) -> None:
    """Clean one table's own structure: no text or <br> between rows/cells (it moves to
    the caption, verbatim), no pretty-printing whitespace, <br> for in-cell newlines."""
    stray: List[str] = []
    containers = [table] + [child for child in table if _tag(child) in _ROW_GROUP_TAGS]
    rows = [row for container in containers for row in container if _tag(row) == "tr"]
    for node in containers + rows:
        stray.extend(_text_lines(node.text))
        node.text = None
        for child in list(node):
            stray.extend(_text_lines(child.tail))
            child.tail = None
            if _tag(child) == "br":
                node.remove(child)
    for row in rows:
        for cell in row:
            if _tag(cell) in ("td", "th"):
                _split_newlines_into_breaks(cell)
                _tidy_breaks(cell)
    if stray:
        _add_lines(_caption_of(table), stray)
    for child in table:
        if _tag(child) == "caption":
            _split_newlines_into_breaks(child)
            _tidy_breaks(child)


def _restructure(frag) -> None:
    """Give every table a clean structure and keep the text around the tables: text
    before a table becomes (the start of) its <caption>; text after the last table
    stays after it, one line per <br>."""
    _wrap_stray_rows(frag)
    pending = _text_lines(frag.text)
    frag.text = None
    last_table = None
    for child in list(frag):
        tail = child.tail
        child.tail = None
        tag = _tag(child)
        if tag == "table":
            if pending:
                _add_lines(_caption_of(child), pending, at_start=True)
                pending = []
            last_table = child
        else:  # a stray caption/br/col: keep its text for the next table
            pending.extend(_text_lines(child.text_content()) if tag == "caption" else [])
            frag.remove(child)
        pending.extend(_text_lines(tail))
    if pending:
        if last_table is None:
            _add_lines(frag, pending)
        else:
            last_table.tail = pending[0]
            anchor = last_table
            for line in pending[1:]:
                br = frag.makeelement("br", {})
                br.tail = line
                anchor.addnext(br)
                anchor = br
    for table in frag.iter("table"):
        _tidy_table(table)


def _serialize(frag) -> str:
    from lxml import etree
    head = _html.escape(frag.text, quote=False) if frag.text else ""
    return (head + "".join(etree.tostring(child, encoding="unicode", method="html") for child in frag)).strip()


def sanitize_table_html(html: str) -> str:
    """Sanitize model-produced table HTML to a strict table-only allowlist.

    Keeps only table-structural tags, ``<br>`` and the ``colspan``/``rowspan``/``scope``
    attributes (integer spans, the four ``scope`` keywords); drops scripts, styles, event handlers, URLs,
    comments, processing instructions and every other tag/attribute, keeping their
    text. Line structure inside a cell (``<br>``, ``<p>``, ``<li>``, ``<div>`` or a raw
    newline) becomes ``<br>``, so words and numbers never run together. Text outside
    any table is kept: before a table it becomes that table's ``<caption>`` (a title or
    a unit line such as ``Unit: NT$ thousand``), after the last table it follows it.
    A Markdown pipe table put in the field is converted to an HTML table. Fails
    **closed**: returns ``""`` when the input is empty or cannot be parsed, so
    unsanitized HTML is never emitted.
    """
    if not html or not html.strip():
        return ""
    text = _strip_code_fence(_clean_controls(html).strip())
    if not text:
        return ""
    if not _TABLE_MARKUP_RE.search(text):
        converted = _pipe_tables_to_html(text)
        if converted is not None:
            text = converted
    try:
        from lxml import etree, html as lxml_html
        frag = lxml_html.fragment_fromstring(text, create_parent="div")
    except Exception:
        return ""  # fail closed — never emit unparsed LLM HTML
    # 1. Comments and processing instructions carry hidden text/markup: drop them.
    etree.strip_elements(frag, etree.Comment, etree.ProcessingInstruction, with_tail=False)
    # 2. Remove dangerous elements together with their text content.
    etree.strip_elements(frag, *_DANGEROUS_TAGS, with_tail=False)
    # 3. Block elements separate lines: mark their boundaries before unwrapping.
    for element in list(frag.iter()):
        if element is not frag and _tag(element) in _BLOCK_TAGS:
            _break_around(element)
    # 4. Unwrap every remaining non-allowlisted element (keeps inner text).
    #    strip_tags preserves the root wrapper, so nested <div>/<span>/<a>/... go.
    present = {e.tag for e in frag.iter() if isinstance(e.tag, str)}
    unwrap = tuple(t for t in present if t.lower() not in _ALLOWED_TABLE_TAGS and t.lower() != "br")
    if unwrap:
        etree.strip_tags(frag, *unwrap)
    # 5. Drop every attribute outside the allowlist; require integer spans.
    for element in frag.iter():
        if not isinstance(element.tag, str) or element is frag:
            continue
        for attr in list(element.attrib):
            name = attr.lower()
            value = element.attrib[attr].strip()
            if _tag(element) == "br" or name not in _ALLOWED_TABLE_ATTRS:
                del element.attrib[attr]
            elif name in ("colspan", "rowspan"):
                if _ASCII_DIGITS_RE.fullmatch(value):
                    element.attrib[attr] = str(int(value))
                else:
                    del element.attrib[attr]
            elif value.lower() not in _SCOPE_VALUES:
                del element.attrib[attr]
    # 6. Structure: keep the text around tables, tidy rows, cells and line breaks.
    _restructure(frag)
    return _serialize(frag)


# --------------------------------------------------------------------------- #
# Table grid normalization                                                    #
# --------------------------------------------------------------------------- #
_NUMERIC_CELL_RE = re.compile(
    r"^[(\[]?[+\-\u2212\u2013]?\s*(?:[A-Z]{1,3}\$|[$\u20ac\u00a3\u00a5\u20a9\u20b9])?\s*\d[\d,.\s]*"
    r"(?:%|\u2030|[kKmMbB]|\u842c|\u4e07|\u5104|\u4ebf)?\s*[)\]]?$"
)
_NULL_CELL_TEXT = frozenset({"-", "\u2013", "\u2014", "n/a", "na", "nil", "none"})


def _row_groups(table) -> List[list]:
    """The table's own rows, grouped by row group (thead/tbody/tfoot, or runs of rows
    placed directly in the table). Rows of nested tables are not included."""
    groups: List[list] = []
    loose: List = []
    for child in table:
        tag = _tag(child)
        if tag in _ROW_GROUP_TAGS:
            if loose:
                groups.append(loose)
                loose = []
            rows = [row for row in child if _tag(row) == "tr"]
            if rows:
                groups.append(rows)
        elif tag == "tr":
            loose.append(child)
    if loose:
        groups.append(loose)
    return groups


def _span_value(value: Optional[str]) -> int:
    value = (value or "1").strip()
    return int(value) if _ASCII_DIGITS_RE.fullmatch(value) else 1


def _write_span(cell, name: str, value: int) -> bool:
    current = cell.get(name)
    if value == 1:
        if current is not None and current.strip() != "1":
            del cell.attrib[name]
            return True
        return False
    if current is None or current.strip() != str(value):
        cell.set(name, str(value))
        return True
    return False


def _is_blank_cell(cell) -> bool:
    return not cell.text_content().strip() and all(_tag(child) == "br" for child in cell)


def _cell_kind(cell) -> Optional[str]:
    """"num" for a number-like cell (amounts, percents, years), "text" otherwise,
    ``None`` for an empty or placeholder cell."""
    text = " ".join(cell.text_content().split())
    if not text or text.lower() in _NULL_CELL_TEXT:
        return None
    return "num" if _NUMERIC_CELL_RE.match(text) else "text"


def _dominant(votes: Counter) -> Optional[str]:
    total = sum(votes.values())
    if not total:
        return None
    value, count = votes.most_common(1)[0]
    return value if count / total >= 0.6 else None


def _place(cells: list, carried: set, spans: dict) -> List[Tuple[object, int]]:
    """HTML table layout of one row: each cell starts at the first column not covered
    by a rowspan from above."""
    placed, col = [], 0
    for cell in cells:
        while col in carried:
            col += 1
        placed.append((cell, col))
        col += spans[cell][1]
    return placed


def _occupancy(carried: set, placed: list, spans: dict) -> int:
    columns = set(carried)
    for cell, start in placed:
        columns.update(range(start, start + spans[cell][1]))
    return len(columns)


def _pad_position(cells: list, carried: set, spans: dict, deficit: int,
                  kinds: List[Optional[str]], tags: List[Optional[str]], budget: List[int]) -> int:
    """Where a short row lost its cell(s): the insertion point for ``deficit`` empty
    cells that best lines the row's cells up with their columns (numbers under number
    columns, labels under label columns, <td> under <td>). Pads never go before a cell
    that spans rows (moving it would change the rows below). Ties keep the pads at the
    end of the row, the historical behaviour; header rows are always padded at the end,
    and so is every row once the table's search ``budget`` (column slots) is spent."""
    first = max((index + 1 for index, cell in enumerate(cells) if spans[cell][0] > 1), default=0)
    cost = (len(cells) - first + 1) * (len(cells) + deficit + len(carried))
    if (first >= len(cells) or len(cells) > _MAX_ALIGNED_ROW_CELLS or cost > budget[0]
            or all(_tag(cell) == "th" for cell in cells)):
        return len(cells)
    budget[0] -= cost
    cell_kinds = [_cell_kind(cell) for cell in cells]
    best, best_score = len(cells), -1
    for position in range(len(cells), first - 1, -1):
        sequence = list(range(position)) + [None] * deficit + list(range(position, len(cells)))
        col, score = 0, 0
        for index in sequence:
            while col in carried:
                col += 1
            if index is None:
                col += 1
                continue
            cell = cells[index]
            colspan = spans[cell][1]
            if colspan == 1 and col < len(kinds):
                if tags[col] is not None and tags[col] == _tag(cell):
                    score += 1
                if kinds[col] is not None and kinds[col] == cell_kinds[index]:
                    score += 1
            col += colspan
        if score > best_score:
            best, best_score = position, score
    return best


def _lay_out(groups: List[list], cells: dict, spans: dict, visit) -> Dict[object, Tuple[int, list]]:
    """HTML table layout, row group by row group, with rowspan carry-over.
    ``visit(row, carried)`` may change ``cells[row]`` before the row is placed.
    Returns ``{row: (occupied columns, [(cell, start column)])}``."""
    layout = {}
    for group in groups:
        carry: Dict[int, int] = {}
        for row in group:
            carried = {col for col, left in carry.items() if left > 0}
            visit(row, carried)
            placed = _place(cells[row], carried, spans)
            layout[row] = _occupancy(carried, placed, spans), placed
            carry = {col: left - 1 for col, left in carry.items() if left > 1}
            for cell, start in placed:
                rowspan, colspan = spans[cell]
                if rowspan > 1:
                    for col in range(start, start + colspan):
                        carry[col] = max(carry.get(col, 0), rowspan - 1)
    return layout


def _layout_work(groups: List[list], cells: dict, spans: dict) -> int:
    """Upper bound of the column slots a layout visits: per row, its own spans plus
    every span reaching down into it from the rows above (counted without laying out)."""
    work = 0
    for group in groups:
        delta = [0] * (len(group) + 1)
        reaching = 0
        for r, row in enumerate(group):
            reaching += delta[r]
            work += reaching + sum(spans[cell][1] for cell in cells[row])
            for cell in cells[row]:
                rowspan, colspan = spans[cell]
                if rowspan > 1:
                    delta[r + 1] += colspan
                    delta[r + rowspan] -= colspan
    return work


def _normalize_table(table, pad_budget: List[int]) -> bool:
    """Make one table a rectangular grid; return whether it changed. See
    :func:`normalize_table_html`. ``pad_budget`` holds the empty cells the whole field
    may still add; padding is skipped (the table stays ragged) when it would exceed
    that or :data:`_MAX_PADS_PER_CELL` per written cell."""
    groups = _row_groups(table)
    rows = [row for group in groups for row in group]
    if not rows:
        return False
    cells = {row: [c for c in row if _tag(c) in ("td", "th")] for row in rows}
    unit_width = max(len(row_cells) for row_cells in cells.values())
    if unit_width == 0:
        return False
    changed = False

    # 1. Bound the spans: colspan <= the widest row's cell count, rowspan <= the rows
    #    left in its row group; rowspan="0" means "to the end of the row group".
    colspan_cap = min(unit_width, _MAX_COLSPAN)
    spans: Dict[object, Tuple[int, int]] = {}
    for group in groups:
        for r, row in enumerate(group):
            remaining = len(group) - r
            for cell in cells[row]:
                colspan = min(max(_span_value(cell.get("colspan")), 1), colspan_cap)
                rowspan = _span_value(cell.get("rowspan"))
                rowspan = remaining if rowspan == 0 else min(max(rowspan, 1), remaining)
                changed = _write_span(cell, "colspan", colspan) or changed
                changed = _write_span(cell, "rowspan", rowspan) or changed
                spans[cell] = (rowspan, colspan)
    #    Spans that together still claim a grid too large to lay out (or to render)
    #    are dropped; every cell and its text stays.
    if _layout_work(groups, cells, spans) > _MAX_LAYOUT_WORK:
        for cell in spans:
            changed = _write_span(cell, "colspan", 1) or changed
            changed = _write_span(cell, "rowspan", 1) or changed
            spans[cell] = (1, 1)

    # 2. Reference width: rows no rowspan reaches into cannot carry a double count.
    reference = []
    for group in groups:
        covered_until = -1
        for r, row in enumerate(group):
            if covered_until < r:
                reference.append(sum(spans[c][1] for c in cells[row]))
            for cell in cells[row]:
                covered_until = max(covered_until, r + spans[cell][0] - 1)
    reference_width = max(reference)

    # 3. A row wider than the reference that also emitted an empty cell for a position
    #    a rowspan above already covers (a double-counted rowspan) loses those empty
    #    cells; nothing with text is ever removed.
    def drop_double_counts(row, carried) -> None:
        nonlocal changed
        excess = _occupancy(carried, _place(cells[row], carried, spans), spans) - reference_width
        natural = 0
        for cell in list(cells[row]):
            start, natural = natural, natural + spans[cell][1]
            if excess > 0 and start in carried and spans[cell] == (1, 1) and _is_blank_cell(cell):
                _remove_keep_tail(cell)
                cells[row].remove(cell)
                excess -= 1
                changed = True

    layout = _lay_out(groups, cells, spans, drop_double_counts)
    width = max(occupancy for occupancy, _ in layout.values())
    pads_needed = sum(width - occupancy for occupancy, _ in layout.values())
    written = sum(len(row_cells) for row_cells in cells.values())
    if (width * len(rows) > _MAX_GRID_CELLS or pads_needed > pad_budget[0]
            or pads_needed > _MAX_PADS_PER_CELL * written + _PAD_ALLOWANCE):
        return changed
    pad_budget[0] -= pads_needed

    # 4. Column profiles from the complete (full-width) data rows.
    kind_votes = [Counter() for _ in range(width)]
    tag_votes = [Counter() for _ in range(width)]
    for row in rows:
        occupancy, placed = layout[row]
        if occupancy != width or all(_tag(c) == "th" for c in cells[row]):
            continue
        for cell, start in placed:
            if spans[cell][1] == 1 and start < width:
                tag_votes[start][_tag(cell)] += 1
                kind = _cell_kind(cell)
                if kind:
                    kind_votes[start][kind] += 1
    kinds = [_dominant(v) for v in kind_votes]
    tags = [_dominant(v) for v in tag_votes]
    budget = [_MAX_ALIGNMENT_WORK]

    # 5. Pad every short row with empty cells where its cells line up best, then (a
    #    safety net for overlapping spans) pad whatever is still short at its end.
    def pad(row, carried, *, align: bool) -> None:
        nonlocal changed
        row_cells = cells[row]
        deficit = width - _occupancy(carried, _place(row_cells, carried, spans), spans)
        if deficit <= 0:
            return
        pads = [row.makeelement("td", {}) for _ in range(deficit)]
        for new in pads:
            spans[new] = (1, 1)
        position = len(row_cells)
        if align:
            candidate = _pad_position(row_cells, carried, spans, deficit, kinds, tags, budget)
            trial = row_cells[:candidate] + pads + row_cells[candidate:]
            # Keep a mid-row insertion only if the row then spans exactly the grid
            # (a shifted colspan can overlap a rowspan differently); else pad at the end.
            if candidate < len(row_cells) and _occupancy(carried, _place(trial, carried, spans), spans) == width:
                position = candidate
        for new in pads:
            if position < len(row_cells):
                row_cells[position].addprevious(new)
            else:
                row.append(new)
        row_cells[position:position] = pads
        changed = True

    _lay_out(groups, cells, spans, lambda row, carried: pad(row, carried, align=True))
    _lay_out(groups, cells, spans, lambda row, carried: pad(row, carried, align=False))
    return changed


def normalize_table_html(html: str) -> str:
    """Repair model-emitted ``<table>`` markup into rectangular grids.

    A valid table has every row occupy the same number of columns. Vision models
    transcribing a complex table can switch their column count mid-table, emitting
    rows of unequal effective width -- the data then misaligns. This enforces the
    invariant deterministically, per table (a nested table or a second table in the
    same field keeps its own grid):

    - spans are bounded: ``colspan`` never exceeds the widest row's cell count,
      ``rowspan`` never runs past its row group, and ``rowspan="0"`` is written out as
      the number of rows to the end of the group (its HTML meaning);
    - an empty cell emitted for a position that a rowspan above already covers (a
      double-counted rowspan) is dropped when it makes the row too wide;
    - a short row is padded with empty ``<td>`` cells to the grid width, inserted
      where the row's cells then line up best with their columns (so ``Cost | 80``
      under ``Item | Unit | 2024`` keeps ``80`` under ``2024``); without a clear
      signal the pads go at the end of the row.

    It is a no-op for already-rectangular tables and never drops a cell with content.
    Its cost is linear in the size of the grid. Fails open: on any parse error the
    (already-sanitized) input is returned unchanged.
    """
    if not html or not html.strip() or "<table" not in html.lower():
        return html
    try:
        from lxml import html as lxml_html
        frag = lxml_html.fragment_fromstring(html, create_parent="div")
    except Exception:
        return html
    changed = False
    pad_budget = [_MAX_GRID_CELLS]  # empty cells all tables of this field may add
    for table in list(frag.iter("table")):
        # Repeat until nothing changes (at most 3 rounds): dropping a double-counted
        # cell can lower the colspan cap, so the result is stable on re-validation.
        for _ in range(3):
            if not _normalize_table(table, pad_budget):
                break
            changed = True
    return _serialize(frag) if changed else html


# --------------------------------------------------------------------------- #
# Markdown boundary: escaping model-supplied text                             #
# --------------------------------------------------------------------------- #
# Every OCR string other than the sanitized Table.html reaches the Markdown output
# as text, escaped per the shared escaping policy: only what would change the
# Markdown/HTML structure is escaped. A "<" becomes "&lt;" only before a letter,
# "/", "!" or "?" (a tag, comment or processing instruction; "x < 5" stays), and
# "&" only where it starts an entity. Plain text (a verbatim transcription) also
# gets a backslash before line-leading block markers, so a transcribed "# 3", "1.",
# "==" underline or "[1]: url" line stays text instead of becoming structure.
_TAG_START_RE = re.compile(r"<(?=[A-Za-z/!?])")
_ENTITY_START_RE = re.compile(r"&(?=#[0-9]{1,8};|#[xX][0-9A-Fa-f]{1,8};|[A-Za-z][A-Za-z0-9]{1,31};)")
_BLOCK_MARKER_RE = re.compile(
    r"^([ \t]{0,3})(#{1,6}(?=[ \t]|$)|>|[-+*](?=[ \t]|$)|[0-9]{1,9}(?=[.)](?:[ \t]|$))|`{3,}|~{3,})",
    re.M,
)
# A rule (---, ***, ___), or a setext heading underline (any run of = or -).
_RULE_LINE_RE = re.compile(r"^([ \t]{0,3})(?=(?:[-=*_][ \t]*){3,}$|=+[ \t]*$|-+[ \t]*$)", re.M)
# "[label]: destination" would become an invisible link reference definition.
_LINK_DEFINITION_RE = re.compile(r"^([ \t]{0,3})(?=\[[^\]\n]*\]:)", re.M)


def _neutralize_html(text: str, *, keep_breaks: bool = False) -> str:
    text = _ENTITY_START_RE.sub("&amp;", text)
    if keep_breaks:
        return "<br>".join(_TAG_START_RE.sub("&lt;", part) for part in _BR_TAG_RE.split(text))
    return _TAG_START_RE.sub("&lt;", text)


def _escape_line_starts(text: str) -> str:
    def marker(match) -> str:
        indent, token = match.group(1), match.group(2)
        return f"{indent}{token}\\" if token[0] in "0123456789" else f"{indent}\\{token}"

    text = _RULE_LINE_RE.sub(lambda match: match.group(1) + "\\", text)
    text = _LINK_DEFINITION_RE.sub(lambda match: match.group(1) + "\\", text)
    return _BLOCK_MARKER_RE.sub(marker, text)


def _escape_text_block(text: str) -> str:
    """Plain multi-line text (a transcription, a caption) for a Markdown block."""
    return _escape_line_starts(_neutralize_html(_clean_controls(text)))


def _escape_inline(text: str) -> str:
    """Plain text for a single Markdown line (a heading, list item or label)."""
    return _escape_line_starts(" ".join(_neutralize_html(_clean_controls(text)).split()))


def _escape_cell(text: str) -> str:
    """Plain text for one Markdown table cell: one line, ``|`` escaped."""
    text = re.sub(r"\\(?=\|)", r"\\\\", _escape_inline(text))
    return text.replace("|", "\\|")


_TABLE_TAG_RE = re.compile(r"<(/?)table\b[^>]*>", re.I)
_ROW_MARKUP_RE = re.compile(r"<(?:tr|td|th)\b", re.I)


def _sanitize_markdown(text: str) -> str:
    """Model-written Markdown (``page_markdown``, ``Table.markdown``, a free-form OCR
    answer): its Markdown structure is kept; each ``<table>...</table>`` block goes
    through the ``Table.html`` sanitizer and normalizer, and every other piece of raw
    HTML except the inert ``<br>`` is neutralized. Apply it once, to the final text."""
    text = _clean_controls(text)
    out: List[str] = []
    depth, start, last = 0, 0, 0
    for match in _TABLE_TAG_RE.finditer(text):
        if not match.group(1):
            if depth == 0:
                start = match.start()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                out.append(_neutralize_html(text[last:start], keep_breaks=True))
                out.append(normalize_table_html(sanitize_table_html(text[start:match.end()])))
                last = match.end()
    if depth and _ROW_MARKUP_RE.search(text, start):
        # A table still open at the end, with rows in it: an answer cut off at max_tokens.
        out.append(_neutralize_html(text[last:start], keep_breaks=True))
        out.append(normalize_table_html(sanitize_table_html(text[start:])))
    else:  # no table left open, or a "<table>" merely mentioned in the prose
        out.append(_neutralize_html(text[last:], keep_breaks=True))
    return "".join(out)


# --------------------------------------------------------------------------- #
# RAW: what is literally on the page                                          #
# --------------------------------------------------------------------------- #
class Table(BaseModel):
    """A table transcribed verbatim from the image.

    ``html`` is the preferred representation: a clean ``<table>`` that can encode
    merged cells via ``colspan``/``rowspan`` (which ``headers``/``rows`` and
    markdown cannot). ``headers``/``rows`` remain a best-effort flat view for
    simple, machine-readable access.
    """
    caption: str = ""
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)
    html: str = Field(
        default="",
        description=(
            "Clean, valid HTML for this table using <table>/<tr>/<th>/<td>. Preserve "
            "the FULL grid: emit ONE <tr> per visual row and one cell per column — "
            "NEVER flatten a multi-row table into a single row or a single header. "
            "Use colspan for a cell that spans columns (e.g. a group header above "
            "several columns) and rowspan for a cell that spans rows (e.g. a row "
            "label covering several rows). Keep the top-left corner cell (often "
            "empty) when the table has both row and column headers; first-column "
            "labels are <th> cells. Cell text is verbatim. No CSS, classes, ids, or "
            "inline styles."
        ),
    )
    # Rendered markdown fallback for simple (non-merged) tables.
    markdown: str = ""
    # Provenance: True if these are demo/sample values (a screenshot/mockup region),
    # not real data. Indexers should down-weight or skip illustrative rows.
    illustrative: bool = False
    row_count: Optional[int] = Field(
        default=None,
        description="For a header-only illustrative table, the number of sample rows "
                    "that were intentionally not transcribed.",
    )

    @field_validator("html")
    @classmethod
    def _sanitize_html(cls, value: str) -> str:
        """Normalize then sanitize model-supplied HTML at the boundary so the stored
        value is always both a rectangular grid (see :func:`normalize_table_html`)
        and safe to embed (see :func:`sanitize_table_html`)."""
        return normalize_table_html(sanitize_table_html(value))


class KeyValue(BaseModel):
    """A label/value pair, e.g. for forms and receipts."""
    label: str = ""
    value: str = ""
    illustrative: bool = False  # True for demo/sample values (screenshot/mockup region)


class Metric(BaseModel):
    """A single typed numeric assertion printed on the page.

    Additive structured view of a number that is ALSO present verbatim in
    ``raw.text`` — never a relocation of it. Flat by design (4 fields) to stay
    fillable on weaker models; the BM42 sparse index reads ``raw.text`` while this
    makes the number queryable as a typed fact.
    """
    label: str = Field(
        default="",
        description="Verbatim label this number belongs to, exactly as printed "
                    "(e.g. 'Net revenue', 'Uptime SLA'). Empty when no adjacent label.",
    )
    value: str = Field(
        default="",
        description="The number exactly as printed — VERBATIM, never normalized or "
                    "computed (e.g. '$4.2B', '98.5%', '3.2x', '< 100 ms'). Do not convert "
                    "'$4.2B' to '4200000000'. This exact string also appears in raw.text.",
    )
    unit: str = Field(
        default="",
        description="Unit/currency only when printed SEPARATELY from the value (e.g. a "
                    "column header 'USD' or 'ms'). Empty when the unit is inside `value`.",
    )
    illustrative: bool = Field(
        default=False,
        description="True ONLY for clearly demo/sample numbers on a product screenshot/"
                    "mockup. Mirrors Table.illustrative. Default False — real numbers never flagged.",
    )


class RawExtraction(BaseModel):
    """Verbatim transcription. No commentary, no inference. BM42 token source."""
    text: str = Field(
        default="",
        description="All visible text, verbatim, in the original language. No analysis.",
    )
    tables: List[Table] = Field(default_factory=list)
    fields: List[KeyValue] = Field(
        default_factory=list,
        description="label/value pairs for forms & receipts",
    )
    # Additive verbatim indexes — each entry is a COPY of tokens already in `text`
    # (BM42 stays intact), surfaced so the indexer can boost/filter without re-parsing.
    headings: List[str] = Field(
        default_factory=list,
        description="Heading/section-title lines, copied VERBATIM character-for-character "
                    "(do not paraphrase, translate, or normalize case), in top-to-bottom "
                    "order. Omit body text. Each entry MUST also appear in raw.text. Empty "
                    "list when the page has no headings.",
    )
    dates: List[str] = Field(
        default_factory=list,
        description="Every date/time reference on the page, copied VERBATIM as printed "
                    "(e.g. 'March 15 2025', 'Q3 FY2024', '2024-01-01'). No normalization. "
                    "Empty list when no dates appear.",
    )
    metrics: List[Metric] = Field(
        default_factory=list,
        description="Typed numeric assertions printed on the page (KPIs, revenue, percentages, "
                    "durations, counts). Only labeled/clearly-contextualized quantities; each "
                    "`value` is VERBATIM and also present in raw.text. Empty list when none — "
                    "an additive typed view, never a replacement for raw.text.",
    )
    detected_language: Optional[str] = Field(
        default=None,
        description="The language actually seen on the page (not an echo of config).",
    )
    has_handwriting: bool = False


# --------------------------------------------------------------------------- #
# NESTED STRUCTURES (interpretation-layer): figures, hierarchy, knowledge graph #
# Shallow by design (max depth 4: OCRPage->interpretation->figures->data_points);#
# no recursion, no model-unions, all fields defaulted — to stay fillable under  #
# with_structured_output(json_schema). Verbatim strings mirror raw.text (BM42). #
# --------------------------------------------------------------------------- #
class DataPoint(BaseModel):
    """One (category, value, series) reading from a chart, flattened to tidy-long
    form (the deepest leaf, depth 4). label/value/series are VERBATIM copies of
    text also in raw.text; never pixel-estimated."""
    label: str = Field(
        default="",
        description="Verbatim x-axis / category label exactly as printed (e.g. 'Q3 2024'). "
                    "Empty when unlabelled. MUST also appear in raw.text. Never emit a point "
                    "whose label AND value are both empty.",
    )
    value: str = Field(
        default="",
        description="Verbatim value exactly as printed (e.g. '$4.2B', '38%'). Never "
                    "pixel-estimated/interpolated — leave empty if no printed data label. MUST "
                    "also appear in raw.text. If NO point has a printed value, leave data_points "
                    "empty and use Figure.trend.",
    )
    series: str = Field(
        default="",
        description="Verbatim legend/series label this point belongs to (e.g. 'Revenue'). Empty "
                    "for a single-series chart. When non-empty MUST also appear in raw.text.",
    )


class DiagramNode(BaseModel):
    """One labelled box/shape/actor in a flowchart, org chart, or network. No
    synthetic id — edges reference nodes by verbatim label (already in raw.text)."""
    label: str = Field(
        default="",
        description="Verbatim text printed in/beside the node (e.g. 'Approve Request', 'CFO'). "
                    "MUST also appear in raw.text. Never emit a node whose label AND kind are "
                    "both empty.",
    )
    kind: str = Field(
        default="",
        description="Shape/role hint — one of 'start','end','process','decision','data',"
                    "'entity','actor','swimlane','annotation'. Empty when unclear. Interpretive, "
                    "NOT a verbatim token.",
    )


class DiagramEdge(BaseModel):
    """A directed connection between two DiagramNodes, referenced by verbatim label
    (membership in nodes is mechanically checkable, like primary_date-in-raw.dates)."""
    from_label: str = Field(
        default="",
        description="Verbatim label of the SOURCE node — must match a DiagramNode.label in this "
                    "Figure.nodes (and is therefore in raw.text).",
    )
    to_label: str = Field(
        default="",
        description="Verbatim label of the TARGET node — must match a DiagramNode.label in this "
                    "Figure.nodes (and is therefore in raw.text).",
    )
    label: str = Field(
        default="",
        description="Verbatim label on the arrow/connector (e.g. 'Yes', 'Approved'). Empty when "
                    "unlabelled; when non-empty MUST also appear in raw.text.",
    )


class Figure(BaseModel):
    """Typed structured view of ONE chart/diagram/infographic panel, in
    interpretation.figures (flat list, never nested in each other). ``kind`` drives
    which branch fills: quantitative->data_points; structural->nodes+edges; else
    ->labels+meaning. Every verbatim string is an ADDITIVE copy of raw.text;
    ``meaning``/``trend`` are the always-attempt interpretive fallbacks."""
    kind: Literal[
        "bar", "line", "pie", "scatter", "area", "combo", "table_visual",
        "flowchart", "org_chart", "network", "timeline_diagram", "map",
        "infographic_panel", "other",
    ] = Field(
        default="other",
        description="Visual type — quantitative (bar/line/pie/scatter/area/combo)->data_points; "
                    "structural (flowchart/org_chart/network)->nodes+edges; timeline_diagram/map/"
                    "infographic_panel/table_visual->labels+meaning; other->meaning only. Always "
                    "set; fill ONE branch and leave the other's lists empty.",
    )
    title: str = Field(default="", description="Verbatim figure title/caption; empty when none; also in raw.text.")
    x_axis: str = Field(default="", description="Verbatim x-axis label; empty for non-chart/unlabelled; also in raw.text.")
    y_axis: str = Field(default="", description="Verbatim y-axis label; empty for non-chart/unlabelled; also in raw.text.")
    data_points: List[DataPoint] = Field(
        default_factory=list,
        description="Flattened chart readings — ONLY for quantitative kinds and ONLY when both "
                    "category label AND printed value are legible. Leave EMPTY (no value-less "
                    "shells) when values are unreadable/pixel-estimated — use trend. Empty for "
                    "diagram/infographic kinds.",
    )
    trend: str = Field(
        default="",
        description="1-sentence trend conclusion for chart kinds (paraphrase, not verbatim). The "
                    "graceful-degradation path when data_points cannot be filled. Empty for "
                    "non-chart kinds.",
    )
    nodes: List[DiagramNode] = Field(
        default_factory=list,
        description="Diagram nodes — ONLY for structural kinds. Empty for chart/infographic kinds.",
    )
    edges: List[DiagramEdge] = Field(
        default_factory=list,
        description="Directed connections — only when nodes non-empty; each from_label/to_label "
                    "must match a node label. Empty for chart/infographic kinds.",
    )
    labels: List[str] = Field(
        default_factory=list,
        description="BM42 completeness catch-all: ALL verbatim text in this visual NOT already in "
                    "title/x_axis/y_axis/data_points/nodes/edges — legend entries, callouts, "
                    "footnotes, scale markers, units, sources. Each MUST also appear in raw.text. "
                    "When in doubt, repeat the label here.",
    )
    meaning: str = Field(
        default="",
        description="1-sentence message/conclusion this visual asserts (paraphrase). Applies to "
                    "ALL kinds. The minimum useful output even when every other field is empty — "
                    "ALWAYS attempt to fill it.",
    )
    illustrative: bool = Field(
        default=False,
        description="True ONLY for clearly demo/mockup data in a product-screenshot context (same "
                    "gate as Table.illustrative). Default False — real visuals never flagged.",
    )


class Section(BaseModel):
    """One heading-delimited region, in reading order. Hierarchy is a FLAT list +
    int ``level`` (never recursive children). ``heading`` is a VERBATIM raw.headings
    entry; summary/key_points are paraphrase."""
    heading: str = Field(
        default="",
        description="Heading copied VERBATIM. MUST be one of raw.headings (and thus raw.text). "
                    "Create a Section ONLY for actual headings/section titles — NOT body "
                    "paragraphs/captions/footers. No paraphrase/translate/case-normalize.",
    )
    level: int = Field(
        default=1,
        description="Heading depth 1..6 from visual prominence (size/weight/indent/caps). Use 1 "
                    "when ambiguous or single-level. Do not invent non-visible levels.",
    )
    summary: str = Field(
        default="",
        description="1-2 sentence paraphrase of what this section covers (interpretation, NOT "
                    "verbatim). Empty for a heading-only divider.",
    )
    key_points: List[str] = Field(
        default_factory=list,
        description="1-5 concise paraphrased takeaways under this heading, one sentence each. "
                    "Empty for a divider/visual-only region. Do NOT copy raw.text verbatim. Never "
                    "exceed 5.",
    )


class Entity(BaseModel):
    """A typed named entity (replaces the flat entities list). Additive view of a
    name already VERBATIM in raw.text. Dates/money/KPIs stay in raw.dates/raw.metrics."""
    name: str = Field(
        default="",
        description="Entity name VERBATIM as printed, never normalized. MUST also appear in "
                    "raw.text. Do not emit a name=='' entity. Cap ~15 entities.",
    )
    type: Literal["person", "org", "product", "location", "concept", "other"] = Field(
        default="other",
        description="'person'=named individual; 'org'=company/institution/team; 'product'=named "
                    "product/service/brand; 'location'=named place; 'concept'=a named domain "
                    "term; 'other'. Dates/money/KPIs are NOT entities (they live in raw.*). Pick "
                    "the most specific.",
    )
    salience: Literal["primary", "secondary", "mentioned"] = Field(
        default="mentioned",
        description="Centrality: 'primary'=a subject of the page (MAX 3); 'secondary'=supports "
                    "the main claim; 'mentioned'=in passing (default). Never >3 'primary'.",
    )
    role: str = Field(
        default="",
        description="The entity's role AS STATED on the page — short noun phrase ≤8 words (e.g. "
                    "'CEO', 'acquiring company'). Do not invent. Empty when none stated.",
    )


class Relation(BaseModel):
    """A knowledge triple for a claim EXPLICITLY stated on the page (never inferred).
    Flat triple of strings; ``evidence`` makes it falsifiable. Highest confabulation
    risk — emit only when explicit."""
    subject: str = Field(
        default="",
        description="Subject — a verbatim entity name / shortest identifying phrase from the "
                    "page (MUST appear in raw.text). Do not construct one not on the page.",
    )
    relation: str = Field(
        default="",
        description="Predicate — short active verb phrase (2-6 words) matching the page's claim "
                    "(e.g. 'acquired', 'is CEO of'). No hedged predicates. Paraphrase OK.",
    )
    object: str = Field(
        default="",
        description="Object — a verbatim entity name / metric / phrase from the page (MUST "
                    "appear in raw.text). May be a quantity/date/entity name. Do not construct "
                    "one not on the page.",
    )
    evidence: str = Field(
        default="",
        description="The verbatim quote / close on-page paraphrase supporting this triple. If "
                    "you cannot identify supporting on-page text, DO NOT emit the Relation. From "
                    "the page only; never world knowledge.",
    )


# --------------------------------------------------------------------------- #
# INTERPRETATION: the model's analysis (omitted when detail="raw")            #
# --------------------------------------------------------------------------- #
class Interpretation(BaseModel):
    """The model's reading of the page. Never mixed into ``raw``."""
    document_type: Literal[
        "document", "table", "form", "receipt", "handwriting", "code",
        "chart", "photo", "screenshot", "diagram", "infographic",
        "logo", "stamp", "mixed", "blank", "other",
    ] = "other"
    summary: str = Field(
        default="",
        description="1-3 sentence description of the content and its purpose.",
    )
    key_findings: List[str] = Field(default_factory=list)
    visual_notes: str = Field(
        default="",
        description="Layout, branding, and non-text visual elements. For a chart/diagram/"
                    "infographic, describe the trend/structure/message here (printed text "
                    "still goes verbatim into raw.text).",
    )
    # Retrieval / comprehension anchors (interpretation-only; never threaten raw verbatim).
    page_title: Optional[str] = Field(
        default=None,
        description="The single most prominent title/heading on the page, copied VERBATIM "
                    "(do not rephrase). The retrieval chunk anchor. Null when no clear single "
                    "title is present.",
    )
    primary_message: Optional[str] = Field(
        default=None,
        description="The single most important claim/conclusion/takeaway in ONE sentence — "
                    "what a reader retains. Grounded in text visibly on the page; no "
                    "speculation. Null for title/agenda/section-header/blank pages.",
    )
    keywords: List[str] = Field(
        default_factory=list,
        description="3-8 topical keywords / abbreviation expansions / domain synonyms NOT "
                    "already prominent in raw.text (e.g. expand 'ROI'->'return on investment', "
                    "add 'attrition' when the page says 'churn'). Do not repeat dominant "
                    "raw.text words. Empty list when nothing to add.",
    )
    figures: List[Figure] = Field(
        default_factory=list,
        description="One Figure per distinct chart/diagram/infographic panel, structuring its "
                    "MEANING as data. Populated for chart/diagram/infographic content; empty for "
                    "pure-prose or photo pages. Complements (never replaces) visual_notes. Every "
                    "verbatim string inside is also in raw.text.",
    )
    sections: List[Section] = Field(
        default_factory=list,
        description="Flat reading-order list of Section objects; use Section.level to rebuild the "
                    "heading tree (do NOT nest children). Roughly one entry per raw.headings line. "
                    "Empty list when no headings. Never emit a heading not in raw.headings.",
    )
    typed_entities: List[Entity] = Field(
        default_factory=list,
        description="Typed named entities (replaces the old flat entities list). Up to ~15; each "
                    "name is VERBATIM and also in raw.text. Empty when none / low legibility. "
                    "Dates/money/KPIs stay in raw.dates / raw.metrics, not here.",
    )
    relations: List[Relation] = Field(
        default_factory=list,
        description="Up to ~10 knowledge triples for claims EXPLICITLY stated on the page. Empty "
                    "for purely visual pages, forms with no assertions, low legibility, or when "
                    "self_confidence < 0.7. Both subject and object are substrings of raw.text. "
                    "Never assert world-knowledge not printed here.",
    )
    column_layout: Literal["single", "double", "multi", "complex"] = Field(
        default="single",
        description="Page column structure as visually observed: 'single' (default)=one "
                    "column; 'double'=two columns; 'multi'=3+; 'complex'=slide/magazine layout "
                    "(sidebar+main+callout). Signals when raw.text token order is interleaved.",
    )
    page_role: Optional[Literal[
        "title", "agenda", "section_header", "content", "data",
        "case_study", "comparison", "timeline", "conclusion", "appendix", "other",
    ]] = Field(
        default=None,
        description="Structural role within a deck/report: 'title'=cover; 'agenda'=TOC; "
                    "'section_header'=divider; 'content'=body; 'data'=tables/charts/metrics; "
                    "'case_study'; 'comparison'; 'timeline'; 'conclusion'; 'appendix'; 'other'. "
                    "Null for a standalone document page not part of a deck.",
    )
    primary_date: Optional[str] = Field(
        default=None,
        description="The single date the page is 'about' (publication/invoice/meeting/version), "
                    "copied VERBATIM. Must be one of the strings in raw.dates (never invented). "
                    "Null when no date is contextually prominent.",
    )
    action_items: List[str] = Field(
        default_factory=list,
        description="Explicit tasks / next steps / recommendations stated on the page. Only "
                    "when the page explicitly frames something as an action — never inferred. "
                    "A concise paraphrase (the verbatim form is already in raw.text). Empty "
                    "list when none.",
    )
    definitions: List[KeyValue] = Field(
        default_factory=list,
        description="Term/definition pairs from glossaries/callouts/sidebars, using label=term, "
                    "value=definition. illustrative is always False. Empty list when none.",
    )
    self_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="The model's own 0..1 confidence estimate.",
    )
    legibility: Literal["high", "medium", "low"] = "high"
    content_fidelity: Literal["verbatim", "described", "caption", "skipped", "mixed"] = Field(
        default="verbatim",
        description=(
            "Which extraction policy the router applied. 'verbatim' = all printed text "
            "transcribed; 'described'/'caption' = some printed values intentionally "
            "withheld (meaning is in this interpretation); 'skipped' = blank. Only a "
            "'screenshot' document_type may pair 'described' with withheld text."
        ),
    )
    page_markdown: Optional[str] = Field(
        default=None,
        description=(
            "Clean, structured Markdown rendering of this WHOLE-PAGE image render — filled "
            "ONLY when explicitly instructed (image-strategy slide/scan pages); leave null "
            "otherwise. When filled it MUST: (a) cover EVERY word, number and CJK character "
            "from raw.text verbatim — drop nothing, paraphrase nothing, translate nothing; "
            "(b) add structure — '## ' for the page title, '### ' for sub-sections, numbered/"
            "bulleted lists for cards, 'A → B → C' arrow chains for process/flow diagrams; "
            "(c) for any table/grid region write a short '[see table]' placeholder (the "
            "authoritative HTML is in raw.tables) rather than re-transcribing it. It "
            "RE-LAYOUTS the text already in raw.text into a readable document; it never "
            "adds or removes content."
        ),
    )


# --------------------------------------------------------------------------- #
# TOP LEVEL                                                                    #
# --------------------------------------------------------------------------- #
class OCRPage(BaseModel):
    """One image's structured OCR result, carried on ``OCRResult.document``."""
    raw: RawExtraction = Field(default_factory=RawExtraction)
    # None for detail="raw", non-LLM providers, and parse-error fallback.
    interpretation: Optional[Interpretation] = None

    def to_markdown(self) -> str:
        """Render a readable markdown view of this page.

        Used as the back-compat ``OCRResult.text`` and by pipelines that want a
        single string. Prefers structured tables/fields over the flat text dump.

        This is the Markdown boundary for model output: the sanitized
        ``Table.html`` is the only live HTML emitted; every other field is escaped
        (see :func:`_escape_text_block`), and a table whose rows were withheld as
        illustrative is followed by a visible ``[N illustrative rows not
        transcribed]`` marker.
        """
        parts: List[str] = []
        raw = self.raw
        interp = self.interpretation

        # Synthesis fast-path: a structured page_markdown rendering REPLACES the flat
        # raw.text dump for whole-page image renders — BUT only when it verifiably
        # covers the verbatim text (this rendered string is the BM42 feed; the OCRPage
        # object is discarded downstream). Authoritative table HTML is appended for
        # spans, and any uncovered verbatim line is preserved in a hidden tail so BM42
        # keeps every token. If it under-covers (paraphrase/truncation), fall through
        # to the standard verbatim rendering — never worse than today.
        md = (interp.page_markdown or "").strip() if interp is not None else ""
        if md:
            # Coverage is judged on what is emitted: the sanitized rendering (text the
            # sanitizer removes, e.g. inside an <svg>, must land in the tail).
            out = [_sanitize_markdown(md)] + [_render_table_block(table) for table in raw.tables]
            covered, missing = _coverage(raw.text, "\n".join(out))
            if covered >= _SYNTH_COVERAGE_MIN:
                if missing:
                    out.append("<!-- raw-verbatim-tail\n" + "\n".join(missing) + "\n-->")
                return "\n\n".join(p for p in out if p)

        # Title anchor: its verbatim copy is already in raw.text, so only prepend
        # when raw.text does not already start with it (avoid duplicating tokens).
        if interp is not None and interp.page_title:
            title = interp.page_title.strip()
            if title and not raw.text.lstrip().startswith(title):
                parts.append(f"# {_escape_inline(title)}")
        if raw.text:
            parts.append(_escape_text_block(raw.text.strip()))
        parts.extend(_render_table_block(table) for table in raw.tables)
        # Typed metrics give a degraded-render payoff for the numeric index; skip
        # illustrative (sample/mockup) values so they never read as real data.
        real_metrics = [m for m in raw.metrics if not m.illustrative]
        if real_metrics:
            parts.append(_render_metrics(real_metrics))
        # Nested overlays (numeric facts first, then figures, then a navigational
        # section outline). Each renderer is degraded-safe and never re-dumps raw.text.
        if interp is not None:
            fig_md = _render_figures(interp.figures)
            if fig_md:
                parts.append(fig_md)
            sec_md = _render_sections(interp.sections)
            if sec_md:
                parts.append(sec_md)
        return "\n\n".join(p for p in parts if p)


def _table_has_rows(table: Table) -> bool:
    """Whether any data row of the table was transcribed (in any representation)."""
    if table.rows:
        return True
    if table.html and len(re.findall(r"<tr[\s>]", table.html, re.I)) > 1:
        return True
    return len([line for line in table.markdown.splitlines() if line.strip().startswith("|")]) > 2


def _withheld_marker(table: Table) -> str:
    """A visible trace of rows withheld from an illustrative (screenshot) table."""
    if not table.illustrative:
        return ""
    if table.row_count:
        return f"[{table.row_count} illustrative rows not transcribed]"
    return "" if _table_has_rows(table) else "[illustrative rows not transcribed]"


def _render_table_block(table: Table) -> str:
    if table.html:
        body = table.html.strip()
    elif table.markdown:
        body = _sanitize_markdown(table.markdown.strip())
    elif table.headers or table.rows:
        body = _render_table(table)
    else:
        body = ""
    return "\n\n".join(p for p in (body, _withheld_marker(table)) if p)


def _render_metrics(metrics: List["Metric"]) -> str:
    """Render non-illustrative metrics as a compact markdown table."""
    lines = ["| Metric | Value |", "| --- | --- |"]
    for m in metrics:
        label = (m.label or "").strip() or "—"
        value = (m.value or "").strip()
        unit = (m.unit or "").strip()
        if unit and unit not in value:
            value = f"{value} {unit}".strip()
        lines.append(f"| {_escape_cell(label)} | {_escape_cell(value)} |")
    return "\n".join(lines)


def _render_figures(figures: List["Figure"]) -> str:
    """Render non-illustrative figures degraded-safe. Never renders figure.labels
    (those tokens are already in raw.text) and skips a figure with nothing to show."""
    blocks: List[str] = []
    for fig in figures:
        if fig.illustrative:
            continue
        title = _escape_inline(fig.title or "")
        head = f"**Figure: {title}**" if title else f"**Figure ({fig.kind})**"
        sub = " ".join(s for s in (_escape_inline(fig.meaning or ""), _escape_inline(fig.trend or "")) if s)
        body: List[str] = []
        pts = [p for p in fig.data_points if (p.label or "").strip() or (p.value or "").strip()]
        if pts:
            if any((p.series or "").strip() for p in pts):
                body.append("| Series | Category | Value |")
                body.append("| --- | --- | --- |")
                for p in pts:
                    body.append(f"| {_escape_cell(p.series or '')} | {_escape_cell(p.label or '')} "
                                f"| {_escape_cell(p.value or '')} |")
            else:
                body.append("| Category | Value |")
                body.append("| --- | --- |")
                for p in pts:
                    body.append(f"| {_escape_cell(p.label or '')} | {_escape_cell(p.value or '')} |")
        edge_lines: List[str] = []
        connected: set = set()
        for e in fig.edges:
            frm, to = _escape_inline(e.from_label or ""), _escape_inline(e.to_label or "")
            if frm or to:
                label = _escape_inline(e.label or "")
                arrow = f" --{label}-->" if label else " -->"
                edge_lines.append(f"- {frm}{arrow} {to}".rstrip())
                connected.update({e.from_label, e.to_label})
        for n in fig.nodes:
            if n.label and n.label not in connected:
                kind = f" ({_escape_inline(n.kind)})" if (n.kind or "").strip() else ""
                edge_lines.append(f"- {_escape_inline(n.label)}{kind}")
        if not (sub or body or edge_lines):
            continue
        lines = [head]
        if sub:
            lines.append(f"*{sub}*")
        lines += body + edge_lines
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_sections(sections: List["Section"]) -> str:
    """Render a section outline ONLY when sections add paraphrase value (the verbatim
    headings are already in raw.text)."""
    if not any((s.summary or "").strip() or s.key_points for s in sections):
        return ""
    lines = ["**Section outline**"]
    for s in sections:
        heading = _escape_inline(s.heading or "")
        if not heading:
            continue
        indent = "  " * max(0, s.level - 1)
        lines.append(f"{indent}- {heading}")
        summary = _escape_inline(s.summary or "")
        if summary:
            lines.append(f"{indent}  {summary}")
        for kp in s.key_points:
            point = _escape_inline(kp or "")
            if point:
                lines.append(f"{indent}  - {point}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _render_table(table: Table) -> str:
    """Render a simple markdown table from headers + rows.

    Cells are escaped for a pipe table (``|`` as ``\\|``, line breaks as spaces), and
    the width is the widest row, so no cell is cut off.
    """
    lines: List[str] = []
    if table.caption:
        lines.append(_escape_text_block(table.caption.strip()))
        lines.append("")
    width = max([len(table.headers)] + [len(row) for row in table.rows])
    if not width:
        return "\n".join(lines).strip()
    headers = list(table.headers) + [""] * (width - len(table.headers))
    lines.append("| " + " | ".join(_escape_cell(h) for h in headers) + " |")
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in table.rows:
        cells = list(row) + [""] * (width - len(row))
        lines.append("| " + " | ".join(_escape_cell(c) for c in cells) + " |")
    return "\n".join(lines)


def _norm_ws(s: str) -> str:
    """Collapse all whitespace runs to single spaces for verbatim-substring checks.

    A model echoing a multi-line label naturally renders the line breaks as spaces
    (and may join a stacked list), so an exact substring test raises false BM42
    alarms. Normalizing whitespace on both sides keeps the check meaningful (the
    same tokens in the same order) without flagging pure re-spacing.
    """
    return " ".join((s or "").split())


# Use the synthesized page_markdown for display when it covers at least this fraction
# of raw.text's tokens; the verbatim-tail carries the residual so BM42 stays complete.
# Below this floor the render likely summarized away content -> fall back to raw.text.
_SYNTH_COVERAGE_MIN = 0.85

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.,%$/+\-]*|[一-鿿぀-ヿ]{2,}")


def _coverage(raw_text: str, rendered: str):
    """Fraction of raw_text's content TOKENS that appear in ``rendered``, plus the
    sorted list of tokens that do NOT. Token-based (not line-based) so short CJK
    labels — most of a slide's content — are actually checked. A token is a Latin/
    numeric word or a CJK/Kana run of >=2 chars. Used by to_markdown() to gate the
    synthesized page_markdown against verbatim loss and to build the hidden
    verbatim-tail (BM42 keeps every token even if page_markdown drops one)."""
    r = _norm_ws(rendered)
    toks = [t for t in _TOKEN_RE.findall(raw_text or "") if len(t) >= 2]
    if not toks:
        return 1.0, []
    missing = [t for t in toks if t not in r]
    return 1 - len(missing) / len(toks), sorted(set(missing))


def _withholds_values(page: "OCRPage") -> bool:
    raw = page.raw
    figs = page.interpretation.figures if page.interpretation else []
    return (
        any(t.illustrative for t in raw.tables)
        or any(f.illustrative for f in raw.fields)
        or any(m.illustrative for m in raw.metrics)
        or any(fig.illustrative for fig in figs)          # Figure gated like Table
    )


def withholding_violations(page: "OCRPage", *, context_attached: Optional[bool] = None) -> List[str]:
    """The router-firewall violations that withhold printed values (empty = OK).

    The subset of :func:`router_invariants` that the OCR providers enforce at run
    time on every structured result (a violating result is redone with a verbatim
    task): withheld (``illustrative``) values anywhere but on a high-confidence,
    legible ``screenshot``, and a ``described``/``caption`` fidelity without the
    meaning in ``interpretation.summary``. With ``context_attached=False`` (no
    neighbor-page PDF was attached to the request) any withholding is a violation
    too: the router's third gate needs that context, and the prompt says so.
    """
    violations: List[str] = []
    interp = page.interpretation
    has_illustrative = _withholds_values(page)
    dtype = interp.document_type if interp else None
    fidelity = interp.content_fidelity if interp else "verbatim"

    # 1. Withheld/illustrative data may appear ONLY on a screenshot.
    if has_illustrative and dtype != "screenshot":
        violations.append(
            f"illustrative content on document_type={dtype!r}; only 'screenshot' may withhold values"
        )
    # 2. A withholding screenshot must be high-confidence and legible.
    if dtype == "screenshot" and has_illustrative and interp is not None:
        if interp.self_confidence < 0.7 or interp.legibility != "high":
            violations.append(
                "screenshot withheld values with self_confidence<0.7 or legibility!='high' "
                "(should have fallen back to verbatim)"
            )
    # 3. described/caption must carry meaning in the interpretation.
    if fidelity in ("described", "caption") and (interp is None or not interp.summary.strip()):
        violations.append(f"content_fidelity={fidelity!r} but interpretation.summary is empty")
    # 4. Without neighbor-page context the policy is VERBATIM: nothing may be withheld.
    if context_attached is False and has_illustrative:
        violations.append("values withheld as illustrative although no neighbor-page context was attached")
    return violations


def router_invariants(page: "OCRPage") -> List[str]:
    """Return the router firewall violations for a structured page (empty = OK).

    Protects the BM42 invariant: real printed values are never withheld (marked
    ``illustrative``) except on a high-confidence ``screenshot`` page. Verbatim
    substring checks are whitespace-normalized (see :func:`_norm_ws`). The OCR
    providers enforce the withholding subset (:func:`withholding_violations`) on
    every structured result; the full set also serves as a CI/eval assertion over
    recorded structured outputs.
    """
    raw = page.raw
    interp = page.interpretation
    text_n = _norm_ws(raw.text or "")
    headings_n = {_norm_ws(h) for h in raw.headings}
    figs = interp.figures if interp else []
    fidelity = interp.content_fidelity if interp else "verbatim"

    # 1-3. Withholding (see withholding_violations).
    violations: List[str] = withholding_violations(page)
    # 4. skipped implies an empty raw layer.
    if fidelity == "skipped" and (raw.text.strip() or raw.tables or raw.fields):
        violations.append("content_fidelity='skipped' but raw is not empty")
    # 5. primary_date must be selected from the verbatim raw.dates list, not invented.
    if (interp is not None and interp.primary_date
            and _norm_ws(interp.primary_date) not in {_norm_ws(d) for d in raw.dates}):
        violations.append(
            "interpretation.primary_date not present in raw.dates (must be selected from them)"
        )

    if interp is None:
        return violations

    # 6. FIGURES — every VERBATIM figure string must be a substring of raw.text
    #    (interpretive meaning/trend and node.kind are NOT checked).
    for i, fig in enumerate(figs):
        verbatim: List[str] = [fig.title, fig.x_axis, fig.y_axis, *fig.labels]
        for p in fig.data_points:
            verbatim += [p.label, p.value, p.series]
        node_labels = {_norm_ws(n.label) for n in fig.nodes if n.label}
        for n in fig.nodes:
            verbatim.append(n.label)
        for e in fig.edges:
            verbatim += [e.from_label, e.to_label, e.label]
        for s in verbatim:
            if s and _norm_ws(s) not in text_n:
                violations.append(f"figures[{i}] verbatim string {s!r} not found in raw.text")
        # 6a. data_points must not be a list of value-less shells.
        if fig.data_points and not any((p.value or "").strip() for p in fig.data_points):
            violations.append(
                f"figures[{i}].data_points has no point with a printed value "
                "(unreadable chart should leave data_points empty and use trend)"
            )
        # 6b. every edge endpoint must reference an existing node label.
        for e in fig.edges:
            for endpoint in (e.from_label, e.to_label):
                if endpoint and _norm_ws(endpoint) not in node_labels:
                    violations.append(
                        f"figures[{i}] edge endpoint {endpoint!r} matches no DiagramNode.label"
                    )
        # 6c. shell guard for diagram nodes.
        for n in fig.nodes:
            if not (n.label or "").strip() and not (n.kind or "").strip():
                violations.append(f"figures[{i}] has a DiagramNode with empty label AND kind")

    # 7. SECTIONS — heading provenance + body-paragraph bloat guard.
    sections = interp.sections
    for sec in sections:
        if sec.heading and _norm_ws(sec.heading) not in headings_n:
            violations.append(f"sections heading {sec.heading!r} not present in raw.headings")
    if len(sections) > len(raw.headings) + 2:   # tolerance for minor over-segmentation
        violations.append(
            f"len(sections)={len(sections)} greatly exceeds len(raw.headings)={len(raw.headings)} "
            "(body paragraphs likely misread as sections)"
        )

    # 8. TYPED ENTITIES — every name is a verbatim substring of raw.text.
    for ent in interp.typed_entities:
        if ent.name and _norm_ws(ent.name) not in text_n:
            violations.append(f"typed_entities name {ent.name!r} not found in raw.text")

    # 9. RELATIONS — subject and object must be substrings of raw.text
    #    (predicate and evidence are paraphrase — NOT checked).
    for rel in interp.relations:
        for part_name, part in (("subject", rel.subject), ("object", rel.object)):
            if part and _norm_ws(part) not in text_n:
                violations.append(f"relations {part_name} {part!r} not found in raw.text")

    return violations


__all__ = [
    "Table",
    "KeyValue",
    "Metric",
    "DataPoint",
    "DiagramNode",
    "DiagramEdge",
    "Figure",
    "Section",
    "Entity",
    "Relation",
    "RawExtraction",
    "Interpretation",
    "OCRPage",
    "sanitize_table_html",
    "normalize_table_html",
    "router_invariants",
    "withholding_violations",
]
