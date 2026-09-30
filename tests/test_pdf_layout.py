"""Unit tests for the reading order of page items (doc2mark.pipelines.pdf_layout).

The E2E suite (tests/e2e/test_order.py) covers real PDFs through the CLI; these pin the rules
on hand-made boxes: columns, rows, anchored items and the permutation guarantee.
"""

import re

import pymupdf

from doc2mark import UnifiedDocumentLoader
from doc2mark.pipelines.pdf_layout import Region, cluster_boxes, reading_order
from tests.e2e import builders_order


def paragraph(x0, top, lines, width=240.0, size=10.0, pitch=12.0, group=None):
    """A text region of ``lines`` full-width lines starting at ``top``."""
    measured = tuple((width, size, top + k * pitch, top + k * pitch + size) for k in range(lines))
    return Region((x0, top, x0 + width, top + (lines - 1) * pitch + size), "text", measured, group=group)


def label(x0, top, width=80.0, size=10.0):
    return Region((x0, top, x0 + width, top + size), "text", ((width, size, top, top + size),))


def by_top(regions):
    order = sorted(range(len(regions)), key=lambda k: regions[k].box[1] if regions[k].box else 0.0)
    return [regions[k] for k in order]


def test_two_columns_read_left_then_right_with_spanning_title_first():
    title = Region((50, 40, 545, 60), "text", ((495, 18, 40, 60),))
    left = [paragraph(50, 100, 8), paragraph(50, 210, 8), paragraph(50, 320, 6)]
    right = [paragraph(305, 100, 5), paragraph(305, 175, 9), paragraph(305, 300, 7)]
    regions = by_top([title] + left + right)
    order = [regions[k] for k in reading_order(regions)]
    assert order == [title] + left + right


def test_label_value_rows_keep_the_top_to_bottom_order():
    regions = by_top([label(72, 100 + 24 * k) for k in range(6)]
                     + [paragraph(240, 100 + 24 * k, 1, width=280) for k in range(6)])
    assert reading_order(regions) == list(range(len(regions)))


def test_single_column_is_untouched():
    regions = by_top([paragraph(72, 100 + 110 * k, 8, width=450) for k in range(5)])
    assert reading_order(regions) == list(range(len(regions)))


def test_running_header_opens_and_footnote_closes_a_column_page():
    # a right-aligned header over the right column, a footnote under the left column while the
    # right column runs lower
    header = Region((400, 20, 545, 30), "text", ((145, 8, 20, 30),), anchored=True, edge=True)
    footnote = Region((50, 700, 290, 720), "text", ((240, 7, 700, 720),), anchored=True, edge=True)
    left = [paragraph(50, 100, 20), paragraph(50, 360, 20)]
    right = [paragraph(305, 100, 15), paragraph(305, 300, 25), paragraph(305, 620, 10)]
    regions = by_top([header, footnote] + left + right)
    order = [regions[k] for k in reading_order(regions, 842)]
    assert order == [header] + left + right + [footnote]


def test_picture_behind_a_label_stays_in_its_column():
    left = [paragraph(50, 100, 20), paragraph(50, 360, 20)]
    right = [paragraph(305, 100, 10), paragraph(305, 400, 20)]
    picture = Region((305, 240, 545, 380), "image")
    label = Region((320, 300, 400, 310), "text", ((80, 10, 300, 310),))
    regions = by_top(left + right + [picture, label])
    order = [regions[k] for k in reading_order(regions, 842)]
    assert order == left + [right[0], picture, label, right[1]]


def test_labels_centred_on_two_line_values_are_rows():
    labels = [label(72, 106 + 40 * k) for k in range(6)]
    values = [paragraph(240, 100 + 40 * k, 2, width=280) for k in range(6)]
    regions = by_top(labels + values)
    assert reading_order(regions) == list(range(len(regions)))


def test_narrow_sidebar_is_read_after_the_main_text_it_stands_beside():
    sidebar = [paragraph(50, 150, 3, width=100), paragraph(50, 200, 6, width=100)]
    main = [paragraph(180, 100, 12, width=360), paragraph(180, 260, 12, width=360)]
    regions = by_top(sidebar + main)
    order = [regions[k] for k in reading_order(regions)]
    assert order == main + sidebar


def test_pieces_of_one_block_stay_together_and_the_result_is_a_permutation():
    regions = by_top([paragraph(50, 100, 1, group=7), paragraph(50, 114, 9, group=7), paragraph(50, 240, 10),
                      paragraph(305, 100, 8), paragraph(305, 210, 12), Region(None)])
    order = reading_order(regions)
    assert sorted(order) == list(range(len(regions)))
    first, second = [k for k, region in enumerate(regions) if region.group == 7]
    assert order.index(second) == order.index(first) + 1
    assert order != list(range(len(regions)))   # the columns were reordered


def test_background_picture_does_not_hide_the_columns():
    background = Region((0, 0, 595, 842), "image")
    left = [paragraph(50, 100, 20), paragraph(50, 360, 20)]
    right = [paragraph(305, 100, 15), paragraph(305, 300, 25)]
    regions = by_top([background] + left + right)
    order = [regions[k] for k in reading_order(regions)]
    assert order == [background] + left + right


# --- Review round 1 ----------------------------------------------------------------------------

def test_ragged_line_reaching_into_the_gutter_stays_in_its_column():
    # the right column ends early; lower in the left column a paragraph runs 2pt further right than
    # the lines beside the right column's text, into the gutter: it is not an item across the gutter
    left = [paragraph(50, 100, 10, width=236), paragraph(50, 230, 10, width=236), paragraph(50, 360, 10, width=242)]
    right = [paragraph(305, 100, 8, width=240), paragraph(305, 205, 8, width=240)]
    regions = by_top(left + right)
    order = [regions[k] for k in reading_order(regions)]
    assert order == left + right


def test_page_number_centred_in_the_gutter_closes_the_columns():
    left = [paragraph(50, 100, 20), paragraph(50, 360, 20)]
    right = [paragraph(305, 100, 15), paragraph(305, 300, 25)]
    number = Region((294, 810, 300, 820), "text", ((6, 9, 810, 820),))
    regions = by_top(left + right + [number])
    order = [regions[k] for k in reading_order(regions)]
    assert order == left + right + [number]


def test_figure_that_is_not_an_item_separates_the_bands():
    upper = [paragraph(50, 60, 12), paragraph(305, 60, 12)]
    lower = [paragraph(50, 520, 12), paragraph(305, 520, 12)]
    regions = by_top(upper + lower)
    figure = (50, 330, 545, 480)
    order = [regions[k] for k in reading_order(regions, 842, figures=lambda: [figure])]
    assert order == upper + lower
    # without the figure the two bands are one pair of columns
    assert [regions[k] for k in reading_order(regions, 842)] == [upper[0], lower[0], upper[1], lower[1]]


def test_rule_in_the_gutter_and_under_a_pull_quote_do_not_cut_the_columns():
    left = [paragraph(50, 100, 20), paragraph(50, 360, 20)]
    right = [paragraph(305, 100, 15), paragraph(305, 300, 25)]
    regions = by_top(left + right)
    rules = [(297, 90, 298, 700), (150, 250, 450, 251)]   # a column rule; a rule across the gutter, beside text
    order = [regions[k] for k in reading_order(regions, 842, figures=lambda: rules)]
    assert order == left + right


def test_picture_with_text_over_it_is_not_a_band_separator():
    left = [paragraph(50, 100, 20), paragraph(50, 360, 20)]
    right = [paragraph(305, 100, 15), paragraph(305, 300, 25)]
    regions = by_top(left + right)
    background = (0, 0, 595, 842)
    order = [regions[k] for k in reading_order(regions, 842, figures=lambda: [background])]
    assert order == left + right


def test_terms_centred_on_multi_line_definitions_are_rows():
    terms, definitions = [], []
    for k in range(6):
        top = 80 + 90 * k
        definitions.append(paragraph(180, top, 6, width=360))
        terms.append(paragraph(50, top + 18, 3, width=70))   # three lines, centred on the definition
    regions = by_top(terms + definitions)
    assert reading_order(regions) == list(range(len(regions)))


def test_paragraphs_starting_level_by_chance_are_still_columns():
    # two of five paragraph starts level in each column (paragraphs on one line grid)
    left = [paragraph(50, 100 + 96 * k, 7) for k in range(5)]
    right = [paragraph(305, 100, 5), paragraph(305, 172, 9), paragraph(305, 292, 5), paragraph(305, 364, 9),
             paragraph(305, 484, 7)]
    regions = by_top(left + right)
    order = [regions[k] for k in reading_order(regions)]
    assert order == left + right


def test_one_row_of_long_table_labels_is_not_running_text():
    # a table header row: a label on the left, the column labels on the right in pieces of one row
    labels = Region((48, 123, 131, 129), "text", ((83, 6, 123, 129),))
    header = Region((137, 123, 533, 129), "text", tuple((120, 6, 123, 129) for _ in range(3)))
    title = Region((72, 73, 296, 85), "text", ((224, 12, 73, 85),))
    body = [Region((48, 135 + 30 * k, 531, 160 + 30 * k), "text", ((483, 6, 135 + 30 * k, 141 + 30 * k),))
            for k in range(3)]
    regions = by_top([title, labels, header] + body)
    assert reading_order(regions) == list(range(len(regions)))


def test_cluster_boxes_groups_nearby_paths_and_drops_small_marks():
    bars = [(70 + 38 * k, 400, 96 + 38 * k, 470) for k in range(10)]   # 12pt apart: separate figures
    frame = [(50, 330, 545, 330.5), (50, 480, 545, 480.5), (50, 330, 50.5, 480), (545, 330, 545.5, 480)]
    bullet = [(40, 600, 43, 603)]
    figures = cluster_boxes(bars + frame + bullet)
    assert len(figures) == 1 and figures[0] == (50, 330, 545.5, 480.5)
    assert cluster_boxes([(0, 0, 1, 1)] * 5000) == []


def test_figures_across_the_columns_are_measured_without_reading_the_pictures(tmp_path, monkeypatch):
    """PR #23 follow-up: a page with a column gutter asks for the boxes of its pictures, and those were measured
    with ``get_image_info(xrefs=True)``, which decodes every image of the page to hash it. On the real
    Traditional-Chinese deck (20 of 30 slides have a candidate gutter) conversion got 15% slower with nothing
    reordered. The figure still cuts the columns, from boxes measured without the pixels."""
    path, tags = builders_order.spanning_figure_pdf(tmp_path / "spanning.pdf", "picture")
    calls = []
    measure = pymupdf.Page.get_image_info

    def spy(page, *args, **kwargs):
        calls.append(kwargs)
        return measure(page, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_image_info", spy)
    result = UnifiedDocumentLoader(ocr_provider=None).load(str(path))

    read = re.findall(r"\[/?(?:[A-Z]+\d*)\]", result.content)
    assert read == [marker for tag in tags for marker in (f"[{tag}]", f"[/{tag}]")]
    assert calls, "the page's pictures were not measured at all"
    assert not [kwargs for kwargs in calls if kwargs.get("xrefs") or kwargs.get("hashes")], calls
