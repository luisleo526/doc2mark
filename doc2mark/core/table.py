"""Shared table rendering utilities for doc2mark pipelines."""

import logging
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TableStyle(Enum):
    """Table output style options for complex tables with merged cells."""
    MINIMAL_HTML = "minimal_html"
    MARKDOWN_GRID = "markdown_grid"
    STYLED_HTML = "styled_html"

    @classmethod
    def default(cls):
        return cls.MINIMAL_HTML


class TableRenderer:
    """Renders table data to Markdown or HTML based on merge info and style."""

    def __init__(self, table_style: TableStyle = None):
        self.table_style = table_style or TableStyle.default()

    def render(self, table_data: List[List], table_info: Dict) -> str:
        if table_info.get('is_complex'):
            return self._render_html(table_data, table_info)
        else:
            return self._render_simple_markdown(table_data, table_info)

    def _render_html(self, table_data: List[List], table_info: Dict) -> str:
        if not table_data:
            return ""
        if self.table_style == TableStyle.STYLED_HTML:
            return self._render_styled_html(table_data, table_info)
        elif self.table_style == TableStyle.MARKDOWN_GRID:
            return self._render_markdown_grid(table_data, table_info)
        else:
            return self._render_minimal_html(table_data, table_info)

    def _render_simple_markdown(self, table_data: List[List], table_info: Dict) -> str:
        if not table_data:
            return ""

        markdown_lines = []
        col_count = table_info['col_count']

        for row_idx, row in enumerate(table_data):
            row_cells = []
            for col_idx in range(col_count):
                if col_idx < len(row) and row[col_idx] is not None:
                    cell_text = str(row[col_idx]).strip()
                else:
                    cell_text = ""
                cell_text = "<br>".join(cell_text.split('\n'))
                cell_text = cell_text.replace("|", "\\|")
                row_cells.append(cell_text)

            row_text = "| " + " | ".join(row_cells) + " |"
            markdown_lines.append(row_text)

            if row_idx == 0:
                separator = "|" + "|".join([" --- " for _ in range(col_count)]) + "|"
                markdown_lines.append(separator)

        return "\n".join(markdown_lines) + "\n\n"

    def _render_minimal_html(self, table_data: List[List], table_info: Dict) -> str:
        if not table_data:
            return ""

        html_lines = ["<table>"]
        processed_cells = set()
        col_count = table_info['col_count']
        row_count = table_info['row_count']
        cell_spans = table_info.get('cell_spans', {})

        for row_idx, row in enumerate(table_data):
            html_lines.append("<tr>")
            col_idx = 0
            while col_idx < col_count:
                if (row_idx, col_idx) in processed_cells and (row_idx, col_idx) not in cell_spans:
                    col_idx += 1
                    continue

                cell_text = str(row[col_idx]).strip() if col_idx < len(row) and row[col_idx] is not None else ""
                cell_text = cell_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                cell_text = cell_text.replace('\n', '<br>')

                attrs = []
                colspan = 1
                rowspan = 1

                if (row_idx, col_idx) in cell_spans:
                    rowspan, colspan = cell_spans[(row_idx, col_idx)]
                    if rowspan > 1:
                        attrs.append(f'rowspan="{rowspan}"')
                    if colspan > 1:
                        attrs.append(f'colspan="{colspan}"')
                    for r in range(row_idx, min(row_idx + rowspan, row_count)):
                        for c in range(col_idx, min(col_idx + colspan, col_count)):
                            processed_cells.add((r, c))

                cell_tag = "th" if row_idx == 0 else "td"
                attrs_str = " " + " ".join(attrs) if attrs else ""
                html_lines.append(f"<{cell_tag}{attrs_str}>{cell_text}</{cell_tag}>")
                col_idx += colspan

            html_lines.append("</tr>")

        html_lines.append("</table>")
        return "\n".join(html_lines) + "\n\n"

    def _render_markdown_grid(self, table_data: List[List], table_info: Dict) -> str:
        if not table_data:
            return ""

        lines = []
        processed_cells = set()
        col_count = table_info['col_count']
        row_count = table_info['row_count']
        cell_spans = table_info.get('cell_spans', {})

        if cell_spans:
            merge_notes = []
            for (r, c), (rowspan, colspan) in cell_spans.items():
                if rowspan > 1 or colspan > 1:
                    merge_notes.append(f"R{r+1}C{c+1}:{rowspan}x{colspan}")
            if merge_notes:
                lines.append(f"<!-- Merged: {', '.join(merge_notes)} -->")

        col_widths = [3] * col_count
        for row in table_data:
            for i, cell in enumerate(row[:col_count]):
                cell_text = str(cell).strip() if cell else ""
                col_widths[i] = max(col_widths[i], len(cell_text))

        for row_idx, row in enumerate(table_data):
            row_cells = []
            col_idx = 0
            while col_idx < col_count:
                if (row_idx, col_idx) in processed_cells and (row_idx, col_idx) not in cell_spans:
                    row_cells.append("↓" if any(
                        r < row_idx and c == col_idx and (r, c) in cell_spans
                        for r in range(row_idx) for c in [col_idx]
                    ) else "→")
                    col_idx += 1
                    continue

                cell_text = str(row[col_idx]).strip() if col_idx < len(row) and row[col_idx] is not None else ""

                if (row_idx, col_idx) in cell_spans:
                    rowspan, colspan = cell_spans[(row_idx, col_idx)]
                    if rowspan > 1 or colspan > 1:
                        cell_text = f"{cell_text} ⊕" if cell_text else "⊕"
                    for r in range(row_idx, min(row_idx + rowspan, row_count)):
                        for c in range(col_idx, min(col_idx + colspan, col_count)):
                            processed_cells.add((r, c))

                row_cells.append(cell_text.ljust(col_widths[col_idx]))
                col_idx += 1

            lines.append("| " + " | ".join(row_cells) + " |")

            if row_idx == 0:
                sep_cells = ["-" * w for w in col_widths]
                lines.append("| " + " | ".join(sep_cells) + " |")

        return "\n".join(lines) + "\n\n"

    def _render_styled_html(self, table_data: List[List], table_info: Dict) -> str:
        if not table_data:
            return ""

        html_lines = ["<!-- Complex table converted to HTML for better structure preservation -->"]
        html_lines.append('<table border="1" style="border-collapse: collapse; width: 100%;">')

        processed_cells = set()
        col_count = table_info['col_count']
        row_count = table_info['row_count']
        cell_spans = table_info.get('cell_spans', {})

        for row_idx, row in enumerate(table_data):
            html_lines.append("  <tr>")
            col_idx = 0
            while col_idx < col_count:
                if (row_idx, col_idx) in processed_cells and (row_idx, col_idx) not in cell_spans:
                    col_idx += 1
                    continue

                cell_text = str(row[col_idx]).strip() if col_idx < len(row) and row[col_idx] is not None else ""
                cell_text = cell_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                cell_text = cell_text.replace('\n', '<br>')

                cell_attrs = []
                colspan = 1
                rowspan = 1

                if (row_idx, col_idx) in cell_spans:
                    rowspan, colspan = cell_spans[(row_idx, col_idx)]
                    if rowspan > 1:
                        cell_attrs.append(f'rowspan="{rowspan}"')
                    if colspan > 1:
                        cell_attrs.append(f'colspan="{colspan}"')
                    for r in range(row_idx, min(row_idx + rowspan, row_count)):
                        for c in range(col_idx, min(col_idx + colspan, col_count)):
                            processed_cells.add((r, c))

                cell_tag = "th" if row_idx == 0 else "td"
                if cell_tag == "th":
                    style = 'style="background-color: #f0f0f0; font-weight: bold; padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd"'
                else:
                    style = 'style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd"'

                attrs_str = " " + " ".join(cell_attrs) if cell_attrs else ""
                html_lines.append(f'    <{cell_tag}{attrs_str} {style}>{cell_text}</{cell_tag}>')
                col_idx += colspan

            html_lines.append("  </tr>")

        html_lines.append("</table>")
        return "\n".join(html_lines) + "\n\n"
