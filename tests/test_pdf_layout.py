"""Unit tests for the reading order of page items (doc2mark.pipelines.pdf_layout).

The E2E suite (tests/e2e/test_order.py) covers real PDFs through the CLI; these pin the rules
on hand-made boxes: columns, rows, anchored items and the permutation guarantee.
"""

from doc2mark.pipelines.pdf_layout import Region, reading_order


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
