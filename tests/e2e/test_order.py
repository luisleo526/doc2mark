"""E2E: PDF reading order follows the page's columns, sidebars and rotation (finding H-F3).

Every test drives the real ``doc2mark`` CLI on a PDF generated at test time
(``tests/e2e/builders_order.py``) and reads the order of the tagged paragraphs in the Markdown it
writes. A paragraph ``[P05] … [/P05]`` can start at the foot of one column and end at the top of
the next; read in order, its start comes before its end and before the next paragraph.

The last tests cover two follow-ups of lane md that sit in the same page flow: a kept running
header is escaped like any other text, and a numbered line after bullets is an item of its own.
"""

import re
from typing import List

import markdown as python_markdown
import pytest
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from tests.e2e import builders_order as b
from tests.e2e.builders_pdftext import docx_to_pdf

_COMMONMARK = MarkdownIt("commonmark")
_TAG = re.compile(r"\[/?(?:[A-Z]+\d*(?:\.\d+)?)\]")
_PAGE_MARKER = re.compile(r"<!-- page (\d+) -->")


def convert(run_cli, path, *args):
    result = run_cli(path, "--ocr", "none", *args)
    assert result.exit_code == 0, result.describe()
    assert result.markdown is not None, result.describe()
    return result


def tags_in(markdown: str) -> List[str]:
    """The ``[TAG]`` / ``[/TAG]`` markers in the order the Markdown gives them."""
    return _TAG.findall(markdown)


def pairs(tags: List[str]) -> List[str]:
    """``[T]``, ``[/T]`` for every tag, in order: the sequence of paragraphs read in order."""
    return [marker for tag in tags for marker in (f"[{tag}]", f"[/{tag}]")]


def pages(markdown: str) -> dict:
    """Markdown per page (doc2mark writes ``<!-- page N -->`` before every page after the first)."""
    parts = _PAGE_MARKER.split(markdown)
    result = {1: parts[0]}
    for number, text in zip(parts[1::2], parts[2::2]):
        result[int(number)] = text
    return result


def test_two_column_paper_reads_column_by_column(run_cli, e2e_dir):
    path, tags = b.two_column_paper_pdf(e2e_dir / "paper.pdf")
    result = convert(run_cli, path)
    found = tags_in(result.markdown)

    # every paragraph once, start before end, one after the other (P05 crosses from the left
    # column to the right one, P10 from page 1 to page 2)
    assert [tag for tag in found if tag.lstrip("[/").startswith("P")] == pairs(tags), result.describe()
    # the spanning title, author line and abstract come first, in their order
    markdown = result.markdown
    assert markdown.index("Reading Order in Multi Column Documents") < markdown.index("[AUTH]"), result.describe()
    assert found[:4] == ["[AUTH]", "[/AUTH]", "[ABS]", "[/ABS]"], result.describe()
    # the footnote at the foot of the left column comes after the text of page 1's columns
    assert found.index("[FN1]") > found.index("[P10]"), result.describe()
    assert found.index("[/FN1]") < found.index("[/P10]"), result.describe()


@pytest.mark.parametrize("images", [False, True], ids=["default", "extract-images"])
def test_three_unequal_columns_with_a_spanning_figure(run_cli, e2e_dir, images):
    path, above, below = b.three_column_pdf(e2e_dir / "three.pdf")
    result = convert(run_cli, path, *(["--extract-images"] if images else []))
    found = tags_in(result.markdown)

    assert found == pairs(above) + ["[FIG]", "[/FIG]"] + pairs(below), result.describe()
    markdown = result.markdown
    assert markdown.index("Three Column Newsletter") < markdown.index("[A1]"), result.describe()
    if images:
        # the picture spanning the columns sits between the two bands, before its caption
        picture = markdown.index("![Image](data:")
        assert markdown.index("[/A5]") < picture < markdown.index("[FIG]"), result.describe()


def test_sidebar_and_pull_quote_do_not_split_the_main_flow(run_cli, e2e_dir):
    path, main = b.sidebar_pdf(e2e_dir / "sidebar.pdf")
    result = convert(run_cli, path)
    found = tags_in(result.markdown)

    start = found.index(f"[{main[0]}]")
    assert found[start:start + 2 * len(main)] == pairs(main), result.describe()
    for tag in ("SIDE", "PQ"):
        assert found.count(f"[{tag}]") == 1 and found.count(f"[/{tag}]") == 1, result.describe()
        begin, end = found.index(f"[{tag}]"), found.index(f"[/{tag}]")
        assert end == begin + 1, result.describe()
        assert end < start or begin > start + 2 * len(main) - 1, result.describe()
    # the sidebar's heading stays with the sidebar's text
    markdown = result.markdown
    between = markdown[markdown.index("Key facts") + len("Key facts"):markdown.index("[SIDE]")]
    assert not re.search(r"\w", between), result.describe()


def test_pull_quote_across_the_gutter_is_read_after_the_columns(run_cli, e2e_dir):
    path, tags = b.pull_quote_pdf(e2e_dir / "pull_quote.pdf")
    result = convert(run_cli, path)
    found = tags_in(result.markdown)

    assert [tag for tag in found if tag.lstrip("[/").startswith("Q")] == pairs(tags), result.describe()
    assert found.index("[/PQ]") == found.index("[PQ]") + 1, result.describe()
    assert found.index("[PQ]") > found.index(f"[/{tags[-1]}]"), result.describe()


def test_rotated_pages_read_in_their_displayed_orientation(run_cli, e2e_dir):
    path, order = b.rotated_pages_pdf(e2e_dir / "rotated.pdf")
    result = convert(run_cli, path)
    by_page = pages(result.markdown)

    assert sorted(by_page) == sorted(order), result.describe()
    for number, tokens in order.items():
        text = by_page[number]
        positions = [text.find(token) for token in tokens]
        assert -1 not in positions, (number, tokens, result.describe())
        # header text, then the paragraphs top to bottom, the table after its title, the closing line
        assert positions == sorted(positions), (number, tokens, result.describe())
        # the page number at the bottom of the page is not read into the text
        assert str(number) not in [line.strip() for line in text.splitlines()], (number, result.describe())
    assert result.markdown.count("ACME Corp - Confidential") == 1, result.describe()
    assert result.markdown.index("ACME Corp - Confidential") < result.markdown.index("[R1.1]"), result.describe()


def test_word_two_column_section_reads_column_by_column(run_cli, require_tool, e2e_dir):
    require_tool("soffice")
    docx, tags = b.word_two_column_docx(e2e_dir / "word_columns.docx")
    result = convert(run_cli, docx_to_pdf(docx))
    found = tags_in(result.markdown)

    assert found == pairs(tags), result.describe()
    assert result.markdown.index("Two Column Paper Title") < result.markdown.index("[ABS]"), result.describe()


def test_form_rows_keep_their_order(run_cli, e2e_dir):
    result = convert(run_cli, b.form_pdf(e2e_dir / "form.pdf"))
    markdown = result.markdown

    # labels and values stand side by side like two columns, but the rows start at the same
    # heights: the page keeps its top-to-bottom order, a row's label next to its value
    rows = [sorted((markdown.find(label), markdown.find(value))) for label, value in b.FORM_ROWS]
    assert all(-1 not in row for row in rows), result.describe()
    spans = [(markdown.find("[INTRO]"), markdown.find("[INTRO]"))] + rows + [(markdown.find("[CLOSE]"),) * 2]
    assert all(before[1] < after[0] for before, after in zip(spans, spans[1:])), result.describe()


def test_single_column_page_keeps_its_order(run_cli, e2e_dir):
    path, tokens = b.single_column_pdf(e2e_dir / "single.pdf")
    result = convert(run_cli, path, "--extract-images")
    markdown = result.markdown

    positions = [markdown.find(token) for token in ["Annual Letter"] + tokens]
    assert -1 not in positions and positions == sorted(positions), result.describe()


def test_kept_running_header_is_escaped(run_cli, e2e_dir):
    result = convert(run_cli, b.markup_running_header_pdf(e2e_dir / "header.pdf"))
    html = _COMMONMARK.render(result.markdown)

    # the first copy is kept as content (verbatim first) and shows as text, not as an HTML tag
    assert html.count("&lt;Draft&gt; ACME Corp - Internal") == 1, (html, result.describe())
    assert "<draft>" not in html.lower(), result.describe()


def test_numbered_line_after_bullets_is_an_item_of_its_own(run_cli, e2e_dir):
    result = convert(run_cli, b.bullets_then_number_pdf(e2e_dir / "bullets.pdf"))

    # CommonMark starts a new list at "5." ...
    items, lists = [], []
    for token in _COMMONMARK.parse(result.markdown):
        if token.type in ("bullet_list_open", "ordered_list_open"):
            lists.append((token.type, token.attrGet("start")))
        elif token.type in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
        elif token.type == "inline" and lists:
            items.append((lists[-1], token.content))
    assert (("bullet_list_open", None), "Costs decreased") in items, (items, result.describe())
    assert (("ordered_list_open", 5), "Outlook for the next year") in items, (items, result.describe())
    # ... and so do renderers that keep list types apart (Python-Markdown's sane_lists), where a
    # numbered line right under a bullet would be a lazy continuation of the bullet's text
    html = BeautifulSoup(python_markdown.markdown(result.markdown, extensions=["sane_lists"]), "html.parser")
    texts = [" ".join(item.get_text(" ").split()) for item in html.find_all("li")]
    assert "Costs decreased" in texts and "Outlook for the next year" in texts, (texts, result.describe())
    assert [ol.get("start") for ol in html.find_all("ol")] == ["5"], (str(html), result.describe())


def test_sibling_items_of_a_nested_list_stay_together(run_cli, e2e_dir):
    result = convert(run_cli, b.nested_bullets_pdf(e2e_dir / "nested.pdf"))
    lines = result.markdown.splitlines()

    first = next(k for k, line in enumerate(lines) if "Plan the kick-off sessions" in line)
    assert all(line.strip().startswith("- ") for line in lines[first:first + 3]), result.describe()
    nested = [token.content for token in _COMMONMARK.parse(result.markdown) if token.type == "inline"]
    assert nested[-3:] == ["Run the hands-on courses", "Track adoption after launch", "Reporting"], \
        (nested, result.describe())


# --- Review round 1 ----------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["picture", "drawing"])
def test_figure_across_the_columns_cuts_them_without_image_extraction(run_cli, e2e_dir, kind):
    # CLI default (no --extract-images): the picture is not an output item, and the short caption
    # under it does not reach across the gutter; the figure itself still separates the two bands
    path, tags = b.spanning_figure_pdf(e2e_dir / f"spanning_{kind}.pdf", kind)
    result = convert(run_cli, path)

    assert tags_in(result.markdown) == pairs(tags), result.describe()


def test_word_picture_between_column_sections(run_cli, require_tool, e2e_dir):
    require_tool("soffice")
    built = [b.word_picture_between_sections_docx(e2e_dir / f"wfig{k}.docx", caption=not k) for k in (0, 1)]
    pdfs = b.docx_to_pdfs([docx for docx, _ in built])
    for pdf, (_, tags) in zip(pdfs, built):
        result = convert(run_cli, pdf)
        assert tags_in(result.markdown) == pairs(tags), (pdf.name, result.describe())


def test_ragged_lines_reaching_into_the_gutter_stay_in_their_column(run_cli, e2e_dir):
    path, tags = b.ragged_columns_pdf(e2e_dir / "ragged.pdf")
    result = convert(run_cli, path)

    assert tags_in(result.markdown) == pairs(tags), result.describe()
    assert result.markdown.count("Journal of Ordering Vol. 3") == 1, result.describe()


@pytest.mark.parametrize("rotation", [90, 270])
def test_rotated_two_column_page_reads_column_by_column(run_cli, e2e_dir, rotation):
    path, tags = b.rotated_two_column_pdf(e2e_dir / f"rotated{rotation}.pdf", rotation)
    result = convert(run_cli, path)

    assert tags_in(result.markdown) == pairs(tags), result.describe()


def test_word_ragged_two_column_exports(run_cli, require_tool, e2e_dir):
    require_tool("soffice")
    built = [b.word_columns_docx(e2e_dir / f"w{seed:02d}.docx", seed) for seed in (0, 11)]
    pdfs = b.docx_to_pdfs([docx for docx, _ in built])
    for pdf, (_, tags) in zip(pdfs, built):
        result = convert(run_cli, pdf)
        assert tags_in(result.markdown) == pairs(tags), (pdf.name, result.describe())


def test_terms_centred_on_their_definitions_keep_their_rows(run_cli, e2e_dir):
    # three-line terms set vertically centred on their definitions are rows, not a column: each
    # term stays next to its definition (the page keeps its top-to-bottom order)
    path, rows = b.glossary_centred_terms_pdf(e2e_dir / "glossary.pdf")
    result = convert(run_cli, path)
    found = [tag.strip("[]") for tag in tags_in(result.markdown) if not tag.startswith("[/")]

    assert sorted(found) == sorted(tag for row in rows for tag in row), result.describe()
    for term, definition in rows:
        assert abs(found.index(term) - found.index(definition)) == 1, (term, found, result.describe())


def test_vertical_margin_stamp_is_neither_the_title_nor_a_heading(run_cli, e2e_dir):
    result = convert(run_cli, b.margin_stamp_paper_pdf(e2e_dir / "stamp.pdf"))
    headings = [(len(line) - len(line.lstrip("#")), line.lstrip("#").strip())
                for line in result.markdown.splitlines() if re.match(r"#{1,6} ", line)]

    assert headings[0] == (1, "Ordering Text In Two Column Papers"), (headings, result.describe())
    assert b.STAMP.split()[0] in result.markdown, result.describe()
    assert not any(b.STAMP.split()[0] in text for _, text in headings), (headings, result.describe())
    # sibling sections get one level, whichever page they are on
    levels = {text: level for level, text in headings}
    assert {levels.get(section) for section in b.PAPER_SECTIONS} == {2}, (headings, result.describe())
