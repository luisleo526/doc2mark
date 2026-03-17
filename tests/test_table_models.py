"""Tests for Cell, TableData Pydantic models and TableRenderer."""

import pytest
from pydantic import ValidationError

from doc2mark.core.table import Cell, TableData, TableRenderer, TableStyle


# ---------------------------------------------------------------------------
# Cell unit tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCell:
    def test_defaults(self):
        cell = Cell()
        assert cell.text == ""
        assert cell.rowspan == 1
        assert cell.colspan == 1
        assert cell.is_header is False
        assert cell.is_continuation is False

    def test_coerce_none_text(self):
        assert Cell(text=None).text == ""

    def test_coerce_int_text(self):
        assert Cell(text=123).text == "123"

    def test_coerce_dict_text(self):
        cell = Cell(text={"a": 1})
        assert "a" in cell.text

    def test_strip_whitespace(self):
        assert Cell(text="  hello  ").text == "hello"

    def test_newlines_preserved_in_text(self):
        assert Cell(text="line1\nline2").text == "line1\nline2"

    def test_clamp_negative_rowspan(self):
        assert Cell(rowspan=-5).rowspan == 1

    def test_clamp_zero_colspan(self):
        assert Cell(colspan=0).colspan == 1

    def test_clamp_invalid_type_span(self):
        assert Cell(rowspan="abc").rowspan == 1

    def test_clamp_none_span(self):
        assert Cell(rowspan=None).rowspan == 1
        assert Cell(colspan=None).colspan == 1

    def test_valid_span(self):
        cell = Cell(rowspan=3, colspan=2)
        assert cell.rowspan == 3
        assert cell.colspan == 2

    def test_frozen(self):
        cell = Cell(text="hello")
        with pytest.raises(ValidationError):
            cell.text = "world"

    def test_empty_factory(self):
        cell = Cell.empty()
        assert cell.text == ""
        assert cell.rowspan == 1

    def test_empty_singleton(self):
        assert Cell.empty() is Cell.empty()

    def test_header_factory(self):
        cell = Cell.header("Name")
        assert cell.text == "Name"
        assert cell.is_header is True

    def test_header_factory_with_kwargs(self):
        cell = Cell.header("Name", colspan=2)
        assert cell.text == "Name"
        assert cell.is_header is True
        assert cell.colspan == 2

    def test_continuation_factory(self):
        cell = Cell.continuation()
        assert cell.is_continuation is True
        assert cell.text == ""

    def test_continuation_singleton(self):
        assert Cell.continuation() is Cell.continuation()

    def test_merged_factory(self):
        cell = Cell.merged("data", rowspan=2, colspan=3)
        assert cell.text == "data"
        assert cell.rowspan == 2
        assert cell.colspan == 3
        assert cell.is_header is False

    def test_merged_factory_header(self):
        cell = Cell.merged("H", rowspan=1, colspan=2, is_header=True)
        assert cell.is_header is True


# ---------------------------------------------------------------------------
# TableData unit tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTableData:
    def test_empty(self):
        table = TableData.empty()
        assert table.row_count == 0
        assert table.col_count == 0
        assert table.cells == []
        assert table.is_complex is False

    def test_simple_table(self):
        cells = [
            [Cell(text="A"), Cell(text="B")],
            [Cell(text="C"), Cell(text="D")],
        ]
        table = TableData(cells=cells)
        assert table.row_count == 2
        assert table.col_count == 2
        assert table.is_complex is False

    def test_ragged_rows_padded(self):
        cells = [
            [Cell(text="A")],
            [Cell(text="B"), Cell(text="C")],
        ]
        table = TableData(cells=cells)
        assert table.col_count == 2
        assert table.cell(0, 1).text == ""  # padded

    def test_span_clamping(self):
        cells = [
            [Cell(text="X", rowspan=100)],
            [Cell(text="Y")],
            [Cell(text="Z")],
        ]
        table = TableData(cells=cells)
        assert table.cell(0, 0).rowspan == 3  # clamped to table height

    def test_colspan_clamping(self):
        cells = [
            [Cell(text="X", colspan=10), Cell(text="Y")],
        ]
        table = TableData(cells=cells)
        assert table.cell(0, 0).colspan == 2  # clamped to table width

    def test_continuation_marking(self):
        cells = [
            [Cell.merged("X", rowspan=2), Cell(text="A")],
            [Cell(text="B"), Cell(text="C")],
        ]
        table = TableData(cells=cells)
        assert table.cell(1, 0).is_continuation is True
        assert table.cell(1, 1).is_continuation is False

    def test_colspan_continuation(self):
        cells = [
            [Cell.merged("H", colspan=2)],
            [Cell(text="A"), Cell(text="B")],
        ]
        table = TableData(cells=cells)
        assert table.cell(0, 0).text == "H"
        assert table.cell(0, 1).is_continuation is True

    def test_auto_detect_complex(self):
        cells = [
            [Cell.merged("X", colspan=2)],
            [Cell(text="A"), Cell(text="B")],
        ]
        table = TableData(cells=cells)
        assert table.is_complex is True

    def test_simple_not_complex(self):
        cells = [
            [Cell(text="A"), Cell(text="B")],
            [Cell(text="C"), Cell(text="D")],
        ]
        table = TableData(cells=cells)
        assert table.is_complex is False

    def test_cell_bounds_safe(self):
        table = TableData(cells=[[Cell(text="only")]])
        assert table.cell(999, 999).text == ""
        assert table.cell(-1, 0).text == ""

    def test_row_bounds_safe(self):
        table = TableData(cells=[[Cell(text="only")]])
        assert table.row(999) == []
        assert table.row(-1) == []

    def test_column(self):
        cells = [
            [Cell(text="A"), Cell(text="B")],
            [Cell(text="C"), Cell(text="D")],
        ]
        table = TableData(cells=cells)
        col = table.column(1)
        assert col[0].text == "B"
        assert col[1].text == "D"

    def test_column_out_of_bounds(self):
        table = TableData(cells=[[Cell(text="A")]])
        col = table.column(999)
        assert len(col) == 1
        assert col[0].text == ""

    def test_iter_rows(self):
        cells = [
            [Cell(text="A")],
            [Cell(text="B")],
        ]
        table = TableData(cells=cells)
        rows = list(table.iter_rows())
        assert len(rows) == 2
        assert rows[0][0] == 0
        assert rows[1][0] == 1

    def test_garbage_cells_string(self):
        table = TableData(cells="not a list")
        assert table.row_count == 0

    def test_garbage_cells_with_none_rows(self):
        table = TableData(cells=[None, [Cell(text="OK")]])
        assert table.row_count == 1
        assert table.cell(0, 0).text == "OK"

    def test_garbage_cells_with_non_list_rows(self):
        table = TableData(cells=["bad", [Cell(text="OK")]])
        assert table.row_count == 1


@pytest.mark.unit
class TestTableDataFromRaw:
    def test_empty_data(self):
        table = TableData.from_raw([])
        assert table.row_count == 0

    def test_none_data(self):
        table = TableData.from_raw(None)
        assert table.row_count == 0

    def test_none_info(self):
        table = TableData.from_raw([["A", "B"]], None)
        assert table.row_count == 1
        assert table.cell(0, 0).text == "A"

    def test_simple_data(self):
        data = [["H1", "H2"], ["a", "b"]]
        table = TableData.from_raw(data)
        assert table.row_count == 2
        assert table.cell(0, 0).is_header is True
        assert table.cell(1, 0).is_header is False

    def test_with_cell_spans(self):
        data = [["Merged", "", "C"], ["A", "B", "D"]]
        info = {
            'is_complex': True,
            'cell_spans': {(0, 0): (1, 2)},
            'row_count': 2,
            'col_count': 3,
        }
        table = TableData.from_raw(data, info)
        assert table.cell(0, 0).colspan == 2
        assert table.cell(0, 1).is_continuation is True
        assert table.is_complex is True

    def test_none_values_in_data(self):
        data = [[None, "B"], ["C", None]]
        table = TableData.from_raw(data)
        assert table.cell(0, 0).text == ""
        assert table.cell(1, 1).text == ""

    def test_none_row_in_data(self):
        data = [["A", "B"], None, ["C", "D"]]
        table = TableData.from_raw(data)
        # None row becomes empty row, padded to width
        assert table.row_count == 3

    def test_numeric_values(self):
        data = [[1, 2.5], [True, None]]
        table = TableData.from_raw(data)
        assert table.cell(0, 0).text == "1"
        assert table.cell(0, 1).text == "2.5"
        assert table.cell(1, 0).text == "True"

    def test_empty_info_dict(self):
        data = [["A"]]
        table = TableData.from_raw(data, {})
        assert table.cell(0, 0).text == "A"


@pytest.mark.unit
class TestTableDataFrom2dArray:
    def test_empty(self):
        table = TableData.from_2d_array([])
        assert table.row_count == 0

    def test_simple(self):
        table = TableData.from_2d_array([["H1", "H2"], ["a", "b"]])
        assert table.cell(0, 0).is_header is True
        assert table.cell(1, 0).is_header is False
        assert table.cell(0, 0).text == "H1"

    def test_none_values(self):
        table = TableData.from_2d_array([[None, "B"]])
        assert table.cell(0, 0).text == ""

    def test_none_row_skipped(self):
        table = TableData.from_2d_array([["A"], None, ["B"]])
        assert table.row_count == 2


# ---------------------------------------------------------------------------
# TableRenderer tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTableRenderer:
    def test_empty_table_returns_empty(self):
        renderer = TableRenderer()
        assert renderer.render(TableData.empty()) == ""

    def test_simple_table_markdown(self):
        table = TableData.from_2d_array([["H1", "H2"], ["a", "b"]])
        result = TableRenderer().render(table)
        assert "| H1 | H2 |" in result
        assert "| --- |" in result
        assert "| a | b |" in result

    def test_complex_table_html(self):
        data = [["Merged", "", "C"], ["A", "B", "D"]]
        info = {'is_complex': True, 'cell_spans': {(0, 0): (1, 2)}}
        table = TableData.from_raw(data, info)
        result = TableRenderer().render(table)
        assert "<table>" in result
        assert 'colspan="2"' in result

    def test_backward_compat_render_list_dict(self):
        data = [["H1", "H2"], ["a", "b"]]
        info = {'is_complex': False, 'cell_spans': {}, 'row_count': 2, 'col_count': 2}
        result = TableRenderer().render(data, info)
        assert "| H1 | H2 |" in result

    def test_backward_compat_render_html(self):
        data = [["Merged", ""], ["A", "B"]]
        info = {'is_complex': True, 'cell_spans': {(0, 0): (1, 2)}, 'row_count': 2, 'col_count': 2}
        result = TableRenderer()._render_html(data, info)
        assert "<table>" in result

    def test_backward_compat_simple_markdown(self):
        data = [["H1", "H2"], ["a", "b"]]
        info = {'is_complex': False, 'cell_spans': {}, 'row_count': 2, 'col_count': 2}
        result = TableRenderer()._render_simple_markdown(data, info)
        assert "| H1 | H2 |" in result

    def test_single_cell_table(self):
        table = TableData(cells=[[Cell(text="only")]])
        result = TableRenderer().render(table)
        assert "| only |" in result

    def test_pipe_escaped_in_markdown(self):
        table = TableData.from_2d_array([["a|b", "c"]])
        result = TableRenderer().render(table)
        assert "a\\|b" in result

    def test_html_entities_escaped(self):
        data = [["<b>bold</b>", "a&b"]]
        info = {'is_complex': True, 'cell_spans': {}}
        table = TableData.from_raw(data, info)
        # Force complex to get HTML output
        result = TableRenderer().render(table)
        assert "&lt;b&gt;" in result
        assert "&amp;" in result

    @pytest.mark.parametrize("style", [TableStyle.MINIMAL_HTML, TableStyle.MARKDOWN_GRID, TableStyle.STYLED_HTML])
    def test_all_styles_with_merged_cells(self, style):
        cells = [
            [Cell.merged("H", colspan=2, is_header=True)],
            [Cell(text="A"), Cell(text="B")],
        ]
        table = TableData(cells=cells)
        result = TableRenderer(table_style=style).render(table)
        assert result  # non-empty output for all styles

    @pytest.mark.parametrize("style", [TableStyle.MINIMAL_HTML, TableStyle.MARKDOWN_GRID, TableStyle.STYLED_HTML])
    def test_all_styles_empty_table(self, style):
        result = TableRenderer(table_style=style).render(TableData.empty())
        assert result == ""

    def test_styled_html_has_border(self):
        cells = [
            [Cell.merged("H", colspan=2, is_header=True)],
            [Cell(text="A"), Cell(text="B")],
        ]
        table = TableData(cells=cells)
        result = TableRenderer(table_style=TableStyle.STYLED_HTML).render(table)
        assert 'border="1"' in result
        assert "background-color" in result

    def test_markdown_grid_merge_annotation(self):
        cells = [
            [Cell.merged("H", colspan=2, is_header=True)],
            [Cell(text="A"), Cell(text="B")],
        ]
        table = TableData(cells=cells)
        result = TableRenderer(table_style=TableStyle.MARKDOWN_GRID).render(table)
        assert "<!-- Merged:" in result
        assert "⊕" in result

    def test_header_cell_uses_th(self):
        cells = [
            [Cell.header("H1"), Cell.header("H2")],
            [Cell(text="A"), Cell(text="B")],
        ]
        table = TableData(cells=cells, is_complex=True)
        result = TableRenderer(table_style=TableStyle.MINIMAL_HTML).render(table)
        assert "<th>" in result
        assert "<td>" in result

    def test_rowspan_in_html(self):
        cells = [
            [Cell.merged("Span", rowspan=2, is_header=True), Cell.header("B")],
            [Cell(text="placeholder"), Cell(text="D")],
        ]
        table = TableData(cells=cells)
        result = TableRenderer(table_style=TableStyle.MINIMAL_HTML).render(table)
        assert 'rowspan="2"' in result


# ---------------------------------------------------------------------------
# Integration tests with sample documents
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestTableIntegration:
    """Integration tests that process real documents and verify table output."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from doc2mark.core.loader import UnifiedDocumentLoader
        self.loader = UnifiedDocumentLoader(ocr_provider='tesseract')

    @pytest.fixture
    def sample_dir(self):
        from pathlib import Path
        d = Path(__file__).parent.parent / "sample_documents"
        if not d.exists():
            pytest.skip("sample_documents directory not found")
        return d

    def _load(self, path):
        from doc2mark.core.base import OutputFormat
        return self.loader.load(
            str(path),
            output_format=OutputFormat.MARKDOWN,
            extract_images=False,
            ocr_images=False,
        )

    def test_pdf_table(self, sample_dir):
        pdf = sample_dir / "test-table.pdf"
        if not pdf.exists():
            pytest.skip("test-table.pdf not found")
        result = self._load(pdf)
        assert result.content
        assert len(result.content) > 100

    def test_docx_table(self, sample_dir):
        docx = sample_dir / "sample_document.docx"
        if not docx.exists():
            pytest.skip("sample_document.docx not found")
        result = self._load(docx)
        assert result.content

    def test_xlsx_table(self, sample_dir):
        xlsx = sample_dir / "sample_spreadsheet.xlsx"
        if not xlsx.exists():
            pytest.skip("sample_spreadsheet.xlsx not found")
        result = self._load(xlsx)
        assert result.content

    def test_pptx_table(self, sample_dir):
        pptx = sample_dir / "sample_presentation.pptx"
        if not pptx.exists():
            pytest.skip("sample_presentation.pptx not found")
        result = self._load(pptx)
        assert result.content

    @pytest.mark.parametrize("filename", [
        "complex_table_test.docx",
        "complex_table_test.xlsx",
        "complex_table_test.pptx",
        "complex_table_test.pdf",
    ])
    def test_complex_table_no_crash(self, sample_dir, filename):
        path = sample_dir / "complex-tables" / filename
        if not path.exists():
            pytest.skip(f"{filename} not found")
        result = self._load(path)
        assert result.content

    @pytest.mark.parametrize("filename", [
        "complex_table_test.docx",
        "complex_table_test.xlsx",
        "complex_table_test.pptx",
        "complex_table_test.pdf",
    ])
    def test_complex_table_has_html(self, sample_dir, filename):
        """Complex tables should produce HTML output with table tags."""
        path = sample_dir / "complex-tables" / filename
        if not path.exists():
            pytest.skip(f"{filename} not found")
        result = self._load(path)
        # Complex tables should render as HTML tables
        assert "<table>" in result.content or "| " in result.content
