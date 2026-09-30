"""Shared table rendering utilities for doc2mark pipelines."""

import logging
import re
from enum import Enum
from typing import Dict, Iterator, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

logger = logging.getLogger(__name__)

# Module-level cached singletons (created once, reused everywhere)
_EMPTY_CELL = None
_CONTINUATION_CELL = None

# Set once TableData has had to shrink a span that would have hidden a value, so the
# warning is logged once per process instead of once per table.
_SPAN_OVERLAP_LOGGED = False

# --- Cell text escaping ------------------------------------------------------------
# Every renderer passes cell text through these, following the escaping policy shared
# with the Markdown body-text path: escape only what would change the table's
# Markdown/HTML structure or make characters disappear, keep everything else verbatim.
_LINE_BREAK = re.compile(r"\r\n|[\r\n\x0b\x0c\x85  ]")
_CONTROL_CHAR = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")  # C0 controls except \t and \n
_ENTITY_START = re.compile(r"&(?=#[0-9]+;|#[xX][0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]*;)")
_TAG_START = re.compile(r"<(?=[A-Za-z/!?])")
_CONSUMED_BACKSLASH = re.compile(r"\\(?=[!-/:-@\[-`{-~]|$)")  # before ASCII punctuation or at the end


def _plain_cell_text(text: str) -> str:
    """Every line-break form (CR/LF, CR, VT, FF, NEL, LS, PS) becomes ``\\n`` and the other
    C0 control characters (except tab) are removed."""
    return _CONTROL_CHAR.sub("", _LINE_BREAK.sub("\n", text))


def markdown_cell(text: str) -> str:
    """Cell text for a pipe table: line breaks as ``<br>`` (GFM; blank lines at the ends
    dropped), ``|`` escaped as ``\\|``, a backslash doubled where a Markdown renderer would
    consume it (before ASCII punctuation, before a line break, at the end of the cell), and
    ``<`` / ``&`` escaped only where they would start an HTML tag or an entity.
    ``x < 5 & y`` stays as is."""
    lines = [line.strip() for line in _plain_cell_text(text).split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "<br>".join(_markdown_cell_line(line) for line in lines)


def _markdown_cell_line(line: str) -> str:
    line = _CONSUMED_BACKSLASH.sub(r"\\\\", line)  # first: it looks at the characters as written
    line = _ENTITY_START.sub("&amp;", line)
    line = _TAG_START.sub("&lt;", line)
    return line.replace("|", "\\|")


def html_cell(text: str, quote: bool = False) -> str:
    """Cell text for an HTML table: ``&``, ``<``, ``>`` (and ``"`` with ``quote``) escaped,
    line breaks as ``<br>``."""
    text = _plain_cell_text(text).strip("\n")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if quote:
        text = text.replace('"', "&quot;")
    return text.replace("\n", "<br>")


class TableStyle(Enum):
    """Table output style options for complex tables with merged cells."""
    MINIMAL_HTML = "minimal_html"
    MARKDOWN_GRID = "markdown_grid"
    STYLED_HTML = "styled_html"

    @classmethod
    def default(cls):
        return cls.MINIMAL_HTML


class Cell(BaseModel):
    """Immutable cell in a table grid. Use factory classmethods for common patterns."""
    model_config = ConfigDict(frozen=True)

    text: str = ""
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False
    is_continuation: bool = False

    @field_validator('text', mode='before')
    @classmethod
    def coerce_text(cls, v):
        """Coerce any input to clean string. Never crashes."""
        if v is None:
            return ""
        try:
            return str(v).strip()
        except Exception:
            return ""

    @field_validator('rowspan', 'colspan', mode='before')
    @classmethod
    def clamp_span(cls, v):
        """Clamp spans to >= 1. Handles None, negative, non-numeric."""
        if v is None:
            return 1
        try:
            return max(1, int(v))
        except (TypeError, ValueError):
            return 1

    @classmethod
    def empty(cls) -> 'Cell':
        """Create an empty cell with default values. Returns cached singleton."""
        global _EMPTY_CELL
        if _EMPTY_CELL is None:
            _EMPTY_CELL = cls()
        return _EMPTY_CELL

    @classmethod
    def header(cls, text, **kwargs) -> 'Cell':
        """Create a header cell."""
        return cls(text=text, is_header=True, **kwargs)

    @classmethod
    def continuation(cls) -> 'Cell':
        """Create a continuation cell (covered by another cell's span). Returns cached singleton."""
        global _CONTINUATION_CELL
        if _CONTINUATION_CELL is None:
            _CONTINUATION_CELL = cls(is_continuation=True)
        return _CONTINUATION_CELL

    @classmethod
    def merged(cls, text, rowspan: int = 1, colspan: int = 1, is_header: bool = False) -> 'Cell':
        """Create a cell with span info (for merged cells)."""
        return cls(text=text, rowspan=rowspan, colspan=colspan, is_header=is_header)


class TableData(BaseModel):
    """Validated, normalized table structure.

    Invariants enforced by validators:
    - cells is always rectangular (ragged rows padded)
    - All spans clamped to table bounds
    - A span never covers a cell holding other text (empty cells and cells repeating the
      span's own text are absorbed): such spans are shrunk (widest first, then tallest) so
      every value stays visible
    - Continuation cells marked for spanned regions
    - is_complex auto-detected from spans
    - No None values — empty Cell() for missing data
    """
    model_config = ConfigDict(validate_default=True)

    cells: List[List[Cell]] = []
    is_complex: bool = False

    @model_validator(mode='before')
    @classmethod
    def coerce_input(cls, data):
        """Pre-validation: ensure cells is a list of lists, filter garbage."""
        if isinstance(data, dict):
            cells = data.get('cells', [])
            if not isinstance(cells, list):
                data['cells'] = []
                return data
            cleaned = []
            for row in cells:
                if row is None:
                    continue
                if not isinstance(row, (list, tuple)):
                    continue
                cleaned.append(list(row))
            data['cells'] = cleaned
        return data

    @model_validator(mode='after')
    def normalize(self) -> 'TableData':
        """Post-validation: pad, clamp, mark continuations, detect complexity."""
        if not self.cells:
            return self

        max_width = max(len(row) for row in self.cells)
        if max_width == 0:
            self.cells = []
            return self

        # Fast path: if all rows uniform and no spans, skip heavy normalization
        all_uniform = all(len(row) == max_width for row in self.cells)
        has_any_span = any(
            c.rowspan > 1 or c.colspan > 1
            for row in self.cells for c in row
        )
        if all_uniform and not has_any_span:
            return self

        # Pass 1: Pad ragged rows + clamp spans (combined)
        row_count = len(self.cells)
        col_count = max_width
        padded = []
        for r, row in enumerate(self.cells):
            new_row = list(row)
            # Pad if short
            while len(new_row) < max_width:
                new_row.append(Cell.empty())
            # Clamp spans
            for c in range(col_count):
                cell = new_row[c]
                clamped_rs = min(cell.rowspan, row_count - r)
                clamped_cs = min(cell.colspan, col_count - c)
                if clamped_rs != cell.rowspan or clamped_cs != cell.colspan:
                    new_row[c] = Cell.model_construct(
                        text=cell.text,
                        rowspan=max(1, clamped_rs),
                        colspan=max(1, clamped_cs),
                        is_header=cell.is_header,
                        is_continuation=cell.is_continuation
                    )
            padded.append(new_row)

        # Pass 2: Mark continuation cells. A span may cover cells no earlier span claimed that
        # are empty or repeat its own text (a merged range reported on, or filled into, every
        # position it covers); a span that would cover other text is shrunk (keep the widest
        # run of free cells in its first row, then as many rows as stay free across that
        # width), so no value is ever hidden.
        claimed = set()

        def is_free(r, c, text):
            covered = padded[r][c].text.strip()
            return (r, c) not in claimed and (not covered or covered == text)

        shrunk = 0
        for r in range(row_count):
            for c in range(col_count):
                cell = padded[r][c]
                if cell.is_continuation or (cell.rowspan == 1 and cell.colspan == 1):
                    continue
                text = cell.text.strip()
                width = 1
                while width < cell.colspan and is_free(r, c + width, text):
                    width += 1
                height = 1
                while height < cell.rowspan and all(is_free(r + height, c + k, text) for k in range(width)):
                    height += 1
                if (height, width) != (cell.rowspan, cell.colspan):
                    shrunk += 1
                    padded[r][c] = Cell.model_construct(
                        text=cell.text,
                        rowspan=height,
                        colspan=width,
                        is_header=cell.is_header,
                        is_continuation=False
                    )
                for sr in range(r, r + height):
                    for sc in range(c, c + width):
                        if (sr, sc) != (r, c):
                            claimed.add((sr, sc))
                            padded[sr][sc] = Cell.continuation()

        # A continuation cell that no span covers would leave a hole in the rendered row.
        for r in range(row_count):
            for c in range(col_count):
                if padded[r][c].is_continuation and (r, c) not in claimed:
                    padded[r][c] = Cell.empty()

        if shrunk:
            global _SPAN_OVERLAP_LOGGED
            log = logger.debug if _SPAN_OVERLAP_LOGGED else logger.warning
            _SPAN_OVERLAP_LOGGED = True
            log("TableData: shrank %d merged-cell span(s) that would have covered non-empty cells; "
                "the covered values are kept as separate cells", shrunk)

        # Complexity follows the spans that are left: a table whose spans were all shrunk away
        # renders as a simple table
        has_span = any(c.rowspan > 1 or c.colspan > 1 for row in padded for c in row)
        if shrunk:
            self.is_complex = has_span
        elif has_span:
            self.is_complex = True

        self.cells = padded
        return self

    @property
    def row_count(self) -> int:
        return len(self.cells)

    @property
    def col_count(self) -> int:
        if not self.cells:
            return 0
        return len(self.cells[0])

    def cell(self, row: int, col: int) -> Cell:
        """Bounds-safe cell access. Returns empty Cell for out-of-bounds."""
        if 0 <= row < self.row_count and 0 <= col < self.col_count:
            return self.cells[row][col]
        return Cell.empty()

    def row(self, idx: int) -> List[Cell]:
        """Get a row by index. Returns empty list for out-of-bounds."""
        if 0 <= idx < self.row_count:
            return self.cells[idx]
        return []

    def column(self, idx: int) -> List[Cell]:
        """Get all cells in a column."""
        return [
            row[idx] if 0 <= idx < len(row) else Cell.empty()
            for row in self.cells
        ]

    def iter_rows(self) -> Iterator[Tuple[int, List[Cell]]]:
        """Iterate rows as (row_index, cells) pairs."""
        for i, row in enumerate(self.cells):
            yield i, row

    @classmethod
    def empty(cls) -> 'TableData':
        """Create a valid empty table."""
        return cls(cells=[], is_complex=False)

    @classmethod
    def from_2d_array(cls, data: List[List]) -> 'TableData':
        """Create a simple table from a 2D array (no merge info).
        First row treated as header."""
        if not data:
            return cls.empty()
        cells = []
        for r_idx, row in enumerate(data):
            if row is None:
                continue
            cell_row = []
            for val in row:
                if r_idx == 0:
                    cell_row.append(Cell.header(val))
                else:
                    cell_row.append(Cell(text=val))
            cells.append(cell_row)
        return cls(cells=cells)

    @classmethod
    def from_raw(cls, data: List[List], info: Optional[Dict] = None) -> 'TableData':
        """Bridge from legacy (data, info) format.

        Args:
            data: 2D array of raw cell values (str, None, int, etc.)
            info: Dict with keys: is_complex, cell_spans, merged_cells, row_count, col_count.
                  All keys are optional with safe defaults.
        """
        if not data:
            return cls.empty()
        if info is None:
            info = {}

        cell_spans = info.get('cell_spans', {})
        is_complex = info.get('is_complex', False)

        cells = []
        for r_idx, row in enumerate(data):
            if row is None:
                cells.append([])
                continue
            cell_row = []
            for c_idx, val in enumerate(row):
                rs, cs = cell_spans.get((r_idx, c_idx), (1, 1))
                text = str(val).strip() if val is not None else ""
                cell_row.append(Cell.model_construct(
                    text=text,
                    rowspan=max(1, rs),
                    colspan=max(1, cs),
                    is_header=(r_idx == 0),
                    is_continuation=False
                ))
            cells.append(cell_row)

        return cls(cells=cells, is_complex=is_complex)


class TableRenderer:
    """Renders table data to Markdown or HTML based on merge info and style."""

    def __init__(self, table_style: TableStyle = None):
        self.table_style = table_style or TableStyle.default()

    @staticmethod
    def _resolve_table(table_data_or_table, table_info=None) -> TableData:
        """Convert input to TableData, supporting both old and new signatures."""
        if isinstance(table_data_or_table, TableData):
            return table_data_or_table
        return TableData.from_raw(table_data_or_table, table_info)

    def render(self, table_data_or_table, table_info=None) -> str:
        table = self._resolve_table(table_data_or_table, table_info)
        if not table.cells:
            return ""
        if table.is_complex:
            return self._render_html(table)
        return self._render_simple_markdown(table)

    def _render_html(self, table_data_or_table, table_info=None) -> str:
        table = self._resolve_table(table_data_or_table, table_info)
        if not table.cells:
            return ""
        if self.table_style == TableStyle.STYLED_HTML:
            return self._render_styled_html(table)
        elif self.table_style == TableStyle.MARKDOWN_GRID:
            return self._render_markdown_grid(table)
        return self._render_minimal_html(table)

    def _render_simple_markdown(self, table_data_or_table, table_info=None) -> str:
        table = self._resolve_table(table_data_or_table, table_info)
        if not table.cells:
            return ""

        markdown_lines = []
        for row_idx, row_cells in table.iter_rows():
            cells_text = [markdown_cell(cell.text) for cell in row_cells]
            markdown_lines.append("| " + " | ".join(cells_text) + " |")

            if row_idx == 0:
                separator = "|" + "|".join([" --- " for _ in range(table.col_count)]) + "|"
                markdown_lines.append(separator)

        return "\n".join(markdown_lines) + "\n\n"

    def _render_minimal_html(self, table: TableData) -> str:
        if not table.cells:
            return ""

        html_lines = ["<table>"]
        for row_idx, row_cells in table.iter_rows():
            html_lines.append("<tr>")
            for cell in row_cells:
                if cell.is_continuation:
                    continue

                cell_text = html_cell(cell.text)

                attrs = []
                if cell.rowspan > 1:
                    attrs.append(f'rowspan="{cell.rowspan}"')
                if cell.colspan > 1:
                    attrs.append(f'colspan="{cell.colspan}"')

                cell_tag = "th" if cell.is_header else "td"
                attrs_str = " " + " ".join(attrs) if attrs else ""
                html_lines.append(f"<{cell_tag}{attrs_str}>{cell_text}</{cell_tag}>")

            html_lines.append("</tr>")

        html_lines.append("</table>")
        return "\n".join(html_lines) + "\n\n"

    def _render_markdown_grid(self, table: TableData) -> str:
        if not table.cells:
            return ""

        lines = []

        # Collect merge notes, and which anchor row covers each continuation cell
        merge_notes = []
        anchor_row = {}
        for row_idx, row_cells in table.iter_rows():
            for col_idx, cell in enumerate(row_cells):
                if not cell.is_continuation and (cell.rowspan > 1 or cell.colspan > 1):
                    merge_notes.append(f"R{row_idx+1}C{col_idx+1}:{cell.rowspan}x{cell.colspan}")
                    for r in range(row_idx, row_idx + cell.rowspan):
                        for c in range(col_idx, col_idx + cell.colspan):
                            anchor_row[(r, c)] = row_idx
        if merge_notes:
            lines.append(f"<!-- Merged: {', '.join(merge_notes)} -->")

        # Cell texts: escaped like any pipe-table cell; ⊕ marks a merged cell's origin,
        # ↓ / → the positions it covers below / to the right
        grid_rows = []
        for row_idx, row_cells in table.iter_rows():
            texts = []
            for col_idx, cell in enumerate(row_cells):
                if cell.is_continuation:
                    texts.append("↓" if anchor_row.get((row_idx, col_idx), row_idx) < row_idx else "→")
                else:
                    text = markdown_cell(cell.text)
                    if cell.rowspan > 1 or cell.colspan > 1:
                        text = f"{text} ⊕" if text else "⊕"
                    texts.append(text)
            grid_rows.append(texts)

        col_widths = [3] * table.col_count
        for texts in grid_rows:
            for i, text in enumerate(texts):
                col_widths[i] = max(col_widths[i], len(text))

        # Render rows
        for row_idx, texts in enumerate(grid_rows):
            cells_text = [text.ljust(col_widths[i]) for i, text in enumerate(texts)]
            lines.append("| " + " | ".join(cells_text) + " |")

            if row_idx == 0:
                sep_cells = ["-" * w for w in col_widths]
                lines.append("| " + " | ".join(sep_cells) + " |")

        return "\n".join(lines) + "\n\n"

    def _render_styled_html(self, table: TableData) -> str:
        if not table.cells:
            return ""

        html_lines = ["<!-- Complex table converted to HTML for better structure preservation -->"]
        html_lines.append('<table border="1" style="border-collapse: collapse; width: 100%;">')

        for row_idx, row_cells in table.iter_rows():
            html_lines.append("  <tr>")
            for cell in row_cells:
                if cell.is_continuation:
                    continue

                cell_text = html_cell(cell.text, quote=True)

                cell_attrs = []
                if cell.rowspan > 1:
                    cell_attrs.append(f'rowspan="{cell.rowspan}"')
                if cell.colspan > 1:
                    cell_attrs.append(f'colspan="{cell.colspan}"')

                cell_tag = "th" if cell.is_header else "td"
                if cell_tag == "th":
                    style = 'style="background-color: #f0f0f0; font-weight: bold; padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd"'
                else:
                    style = 'style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd"'

                attrs_str = " " + " ".join(cell_attrs) if cell_attrs else ""
                html_lines.append(f'    <{cell_tag}{attrs_str} {style}>{cell_text}</{cell_tag}>')

            html_lines.append("  </tr>")

        html_lines.append("</table>")
        return "\n".join(html_lines) + "\n\n"
