"""Shared table rendering utilities for doc2mark pipelines."""

import logging
from enum import Enum
from typing import Dict, Iterator, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

logger = logging.getLogger(__name__)

# Module-level cached singletons (created once, reused everywhere)
_EMPTY_CELL = None
_CONTINUATION_CELL = None


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

        # Pass 2: Mark continuation cells
        claimed = set()
        for r in range(row_count):
            for c in range(col_count):
                cell = padded[r][c]
                if cell.is_continuation:
                    continue
                if cell.rowspan > 1 or cell.colspan > 1:
                    for sr in range(r, r + cell.rowspan):
                        for sc in range(c, c + cell.colspan):
                            if (sr, sc) != (r, c) and (sr, sc) not in claimed:
                                claimed.add((sr, sc))
                                padded[sr][sc] = Cell.continuation()

        # Auto-detect complexity
        if not self.is_complex and has_any_span:
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
            cells_text = []
            for cell in row_cells:
                text = "<br>".join(cell.text.split('\n'))
                text = text.replace("|", "\\|")
                cells_text.append(text)

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

                cell_text = cell.text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                cell_text = cell_text.replace('\n', '<br>')

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

        # Collect merge notes
        merge_notes = []
        for row_idx, row_cells in table.iter_rows():
            for col_idx, cell in enumerate(row_cells):
                if not cell.is_continuation and (cell.rowspan > 1 or cell.colspan > 1):
                    merge_notes.append(f"R{row_idx+1}C{col_idx+1}:{cell.rowspan}x{cell.colspan}")
        if merge_notes:
            lines.append(f"<!-- Merged: {', '.join(merge_notes)} -->")

        # Calculate column widths
        col_widths = [3] * table.col_count
        for _, row_cells in table.iter_rows():
            for i, cell in enumerate(row_cells):
                if not cell.is_continuation:
                    col_widths[i] = max(col_widths[i], len(cell.text))

        # Render rows
        for row_idx, row_cells in table.iter_rows():
            cells_text = []
            for col_idx, cell in enumerate(row_cells):
                if cell.is_continuation:
                    # Determine direction marker
                    is_vertical = any(
                        r < row_idx
                        and not table.cell(r, col_idx).is_continuation
                        and table.cell(r, col_idx).rowspan > 1
                        for r in range(row_idx)
                    )
                    cells_text.append(("↓" if is_vertical else "→").ljust(col_widths[col_idx]))
                else:
                    text = cell.text
                    if cell.rowspan > 1 or cell.colspan > 1:
                        text = f"{text} ⊕" if text else "⊕"
                    cells_text.append(text.ljust(col_widths[col_idx]))

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

                cell_text = cell.text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                cell_text = cell_text.replace('\n', '<br>')

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
