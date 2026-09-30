"""E2E: PDF text blocks become faithful, valid Markdown (headings, lists, captions, escaping).

Every test drives the real ``doc2mark`` CLI on a document generated at test time and parses
the Markdown it writes with a CommonMark parser (markdown-it-py), so the assertions are about
what a Markdown reader sees: which lines are headings (and at what level), list items,
captions or paragraphs, and whether every line of text survives verbatim.
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple

import pytest
from markdown_it import MarkdownIt

from tests.e2e import builders_md as b

_COMMONMARK = MarkdownIt("commonmark")
# The page/slide/sheet separators doc2mark itself writes between pages.
_PAGE_MARKER = re.compile(r"<!-- (?:page|slide|sheet) \d+ -->\s*")


@dataclass
class Block:
    """One rendered leaf block. ``text`` is what a reader sees (escapes and entities resolved,
    soft line breaks as ``\\n``); ``lists`` are the enclosing list kinds, outermost first;
    ``emphasis`` is True when all of the text is inside ``<em>``."""

    kind: str
    text: str
    level: int = 0
    lists: Tuple[str, ...] = ()
    quoted: bool = False
    emphasis: bool = False
    strong: Tuple[str, ...] = field(default_factory=tuple)


def _inline(children):
    text, outside_em, strong, current_strong = [], [], [], None
    em_depth = strong_depth = 0
    for child in children or []:
        kind = child.type
        if kind in ("text", "code_inline", "html_inline"):
            piece = child.content
        elif kind in ("softbreak", "hardbreak"):
            piece = "\n"
        elif kind == "em_open":
            em_depth += 1
            continue
        elif kind == "em_close":
            em_depth -= 1
            continue
        elif kind == "strong_open":
            strong_depth += 1
            current_strong = []
            continue
        elif kind == "strong_close":
            strong_depth -= 1
            strong.append("".join(current_strong))
            continue
        elif kind == "image":
            piece = child.content
        else:
            continue
        text.append(piece)
        if em_depth == 0:
            outside_em.append(piece)
        if strong_depth and current_strong is not None:
            current_strong.append(piece)
    rendered = "".join(text)
    return rendered, bool(rendered.strip()) and not "".join(outside_em).strip(), tuple(strong)


def parse_blocks(markdown: str) -> List[Block]:
    """Leaf blocks of ``markdown`` as CommonMark renders them, without doc2mark's own page markers."""
    blocks, lists, quote_depth, pending = [], [], 0, None
    for token in _COMMONMARK.parse(markdown):
        kind = token.type
        if kind == "html_block" and _PAGE_MARKER.fullmatch(token.content):
            continue
        if kind in ("bullet_list_open", "ordered_list_open"):
            lists.append("bullet" if kind == "bullet_list_open" else "ordered")
        elif kind in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
        elif kind == "blockquote_open":
            quote_depth += 1
        elif kind == "blockquote_close":
            quote_depth -= 1
        elif kind == "heading_open":
            pending = ("heading", int(token.tag[1]))
        elif kind == "paragraph_open":
            pending = ("paragraph", 0)
        elif kind == "inline" and pending:
            text, emphasis, strong = _inline(token.children)
            blocks.append(Block(pending[0], text, pending[1], tuple(lists), quote_depth > 0, emphasis, strong))
            pending = None
        elif kind in ("fence", "code_block", "html_block", "hr"):
            blocks.append(Block({"fence": "code", "code_block": "code", "html_block": "html"}.get(kind, kind),
                                token.content, 0, tuple(lists), quote_depth > 0))
    return blocks


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def headings(blocks):
    return [(block.level, norm(block.text)) for block in blocks if block.kind == "heading"]


def heading_texts(blocks):
    return [text for _, text in headings(blocks)]


def texts(blocks):
    return [norm(block.text) for block in blocks]


def convert(run_cli, path, *args):
    """Run the CLI with ``--ocr none`` (plus ``args``) and return (result, blocks)."""
    result = run_cli(path, "--ocr", "none", *args, fmt="both")
    assert result.exit_code == 0, result.describe()
    assert result.markdown is not None, result.describe()
    return result, parse_blocks(result.markdown)


def assert_no_level_jumps(blocks, result):
    """Each heading is at most one level deeper than the heading before it (the first at most ``##``)."""
    order = [level for level, _ in headings(blocks)]
    assert all(level <= previous + 1 for previous, level in zip([1] + order, order)), (order, result.describe())


def block_with_text(blocks, text, result):
    matches = [block for block in blocks if norm(block.text) == norm(text)]
    assert matches, f"no block renders as {text!r}\n{result.describe()}"
    return matches[0]


@pytest.fixture(scope="session")
def office_pdf(tmp_path_factory):
    """``office_pdf("report")`` builds that Office fixture once per session, exports it to PDF with
    LibreOffice and returns the PDF path. Call ``require_tool("soffice")`` first."""
    builders = {
        "report": (b.report_docx, "report.docx"),
        "word2013": (b.word2013_docx, "word2013.docx"),
        "zh_report": (b.zh_report_docx, "zh_report.docx"),
        "tables_adjacent": (b.tables_adjacent_docx, "tables_adjacent.docx"),
        "bullet_deck": (b.bullet_deck_pptx, "bullet_deck.pptx"),
        "compounds": (b.hyphenated_compounds_docx, "compounds.docx"),
    }
    root = tmp_path_factory.mktemp("e2e-md-office")
    cache = {}

    def get(name):
        if name not in cache:
            build, filename = builders[name]
            source = build(root / filename)
            cache[name] = b.office_to_pdf(source, root / "pdf")
        return cache[name]

    return get


# --- H-F4: list-marker rewriting never deletes text ----------------------------------------------


def test_letter_and_roman_markers_keep_their_letters(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.markers_pdf(e2e_dir / "markers.pdf"))

    for line in [
        "A. Smith and B. Jones reviewed the draft",
        "E. coli contamination was found in 3 samples",
        "a) The lessee shall pay rent monthly",
        "II. Methods",
        "(a) first option",
    ]:
        block_with_text(blocks, line, result)
    assert texts(blocks).count("I. Background") == 2, result.describe()
    for stripped in ["Smith and B. Jones reviewed the draft", "coli contamination was found in 3 samples",
                     "Background", "The lessee shall pay rent monthly"]:
        assert stripped not in texts(blocks), result.describe()


def test_letter_at_start_of_wrapped_line_stays_in_its_paragraph(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.midparagraph_letter_pdf(e2e_dir / "midparagraph.pdf"))

    assert [(block.kind, block.lists) for block in blocks] == [("paragraph", ())], result.describe()
    assert norm(blocks[0].text) == norm(" ".join(b.MIDPARAGRAPH_LINES)), result.describe()


def test_consecutive_lettered_items_are_list_items_that_keep_their_letters(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.letter_list_pdf(e2e_dir / "letter_list.pdf"))

    assert [norm(block.text) for block in blocks if block.lists] == b.LETTER_LIST_LINES, result.describe()
    page_reference = block_with_text(blocks, b.PAGE_REFERENCE_LINE, result)
    assert page_reference.lists == () and page_reference.kind == "paragraph", result.describe()


# --- H-F5: superscripts stay distinguishable -------------------------------------------------------


def test_superscripts_are_marked_instead_of_fused_into_numbers(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.superscripts_pdf(e2e_dir / "superscripts.pdf"))

    for marked in ["Energy is E = mc^2^", "The sample contained 10^6^ cells per litre", "Net revenue was $1.2bn^3^",
                   "^1^Source: company filings and internal estimates"]:
        assert marked in texts(blocks), result.describe()
    rendered = " ".join(texts(blocks))
    for fused in ["mc2", "106 cells", "$1.2bn3", "1Source"]:
        assert fused not in rendered, result.describe()


# --- H-F7 / T17: only real captions are captions, and caption Markdown is valid --------------------


def test_headings_next_to_a_table_are_headings_not_captions(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.headings_around_table_pdf(e2e_dir / "headings_around_table.pdf"))

    for heading in [b.HEADING_BEFORE_TABLE, b.HEADING_AFTER_TABLE]:
        assert heading in heading_texts(blocks), result.describe()
    for body in [b.BODY_BEFORE_TABLE, b.BODY_AFTER_HEADING]:
        block = block_with_text(blocks, body, result)
        assert block.kind == "paragraph" and not block.emphasis, result.describe()
    assert not any("*" in block.text for block in blocks), result.describe()


def test_numbered_figure_and_table_labels_are_valid_italic_captions(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.captions_pdf(e2e_dir / "captions.pdf"))

    captions = [norm(block.text) for block in blocks if block.emphasis]
    assert captions == [b.FIGURE_CAPTION, b.TABLE_CAPTION, norm(" ".join(b.TWO_LINE_CAPTION))], result.describe()
    for body in [
        "The following figure summarises the regional revenue development.",
        "Operating costs remained under control during the year.",
        "The closing paragraph of the page follows the figures and tables.",
    ]:
        assert not block_with_text(blocks, body, result).emphasis, result.describe()
    assert not any("*" in block.text for block in blocks), result.describe()


def test_caption_prefix_words_and_outline_numbers_are_not_captions(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.markers_pdf(e2e_dir / "markers.pdf"))

    for heading in ["1.2.3 Scope of Work", "1.1. Definitions", "Graph Neural Networks", "Image Classification Results"]:
        assert heading in heading_texts(blocks), result.describe()
    for body in ["Tablets were distributed to all students", "Chartered accountants signed off the accounts",
                 "Fighting fraud remains a top priority"]:
        block = block_with_text(blocks, body, result)
        assert block.kind == "paragraph" and not block.emphasis, result.describe()
    assert not any("*" in block.text for block in blocks), result.describe()


def test_sample_pdf_section_headings_are_headings_not_broken_captions(run_cli, sample_documents_dir):
    result, blocks = convert(run_cli, sample_documents_dir / "sample_pdf.pdf")

    # "Structured Table" and "Unstructured Table (Merged Cells)" sit right above their tables; with
    # PyMuPDF >= 1.28 their text block touches the table bbox and the table-overlap filter drops it
    # (H-F1, lane pdftext), so this test checks the headings that reach the Markdown.
    for heading in ["Introduction", "Sample Image", "Additional Text Content"]:
        assert heading in heading_texts(blocks), result.describe()
    intro = block_with_text(
        blocks,
        "This is a comprehensive sample DOCX document that demonstrates various document elements including "
        "text formatting, images, structured tables, and unstructured tables with merged cells.",
        result,
    )
    assert intro.kind == "paragraph" and not intro.emphasis, result.describe()
    assert not any("*" in block.text for block in blocks), result.describe()


def test_heading_above_a_word_table_is_a_heading(run_cli, require_tool, office_pdf):
    require_tool("soffice")
    result, blocks = convert(run_cli, office_pdf("tables_adjacent"))

    # Only the first heading: the second one touches its table and is dropped by the table-overlap
    # filter (H-F1, lane pdftext) before it is classified.
    assert b.TABLES_ADJACENT_HEADINGS[0] in heading_texts(blocks), result.describe()
    for body in b.TABLES_ADJACENT_BODY:
        block = block_with_text(blocks, body, result)
        assert block.kind == "paragraph" and not block.emphasis, result.describe()
    assert not any("*" in block.text for block in blocks), result.describe()


# --- H-F8: heading false positives -----------------------------------------------------------------


def test_uppercase_labels_and_cjk_lines_with_acronyms_are_body_text(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.allcaps_labels_pdf(e2e_dir / "allcaps.pdf"))

    for line in b.ALLCAPS_LABELS + b.CJK_ACRONYM_LINES + b.CJK_BODY_MARKER_LINES:
        assert block_with_text(blocks, line, result).kind == "paragraph", result.describe()


def test_body_sentence_starting_with_section_is_not_a_heading(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.markers_pdf(e2e_dir / "markers.pdf"))

    assert block_with_text(blocks, "Section 5 applies to all employees", result).kind == "paragraph", result.describe()


def test_small_chart_labels_do_not_turn_body_lines_into_headings(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.chart_labels_pdf(e2e_dir / "chart_labels.pdf"))

    for line in b.CHART_BODY_LINES:
        assert block_with_text(blocks, line, result).kind == "paragraph", result.describe()


def test_superscript_markers_do_not_make_bold_lead_ins_titles(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.footnote_density_pdf(e2e_dir / "footnote_density.pdf"))

    for line in b.BOLD_LEAD_INS:
        block = block_with_text(blocks, line, result)
        assert not (block.kind == "heading" and block.level == 1), result.describe()


def test_drop_cap_is_not_a_heading_and_rejoins_its_word(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.drop_cap_pdf(e2e_dir / "drop_cap.pdf"))

    assert [text for level, text in headings(blocks) if level == 1] == [b.DROP_CAP_TITLE], result.describe()
    assert "T" not in heading_texts(blocks), result.describe()
    assert any(text.startswith("The company delivered record results") for text in texts(blocks)), result.describe()


def test_running_header_line_is_not_a_heading(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.running_header_pdf(e2e_dir / "running_header.pdf"))

    assert "1 Introduction" in heading_texts(blocks), result.describe()
    assert "Chapter 1 - Introduction" not in heading_texts(blocks), result.describe()
    assert "Chapter 1 - Introduction" in texts(blocks), result.describe()


def test_bold_text_inside_paragraphs_and_lists_is_not_a_heading(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.bold_text_in_body_pdf(e2e_dir / "bold_in_body.pdf"))

    assert heading_texts(blocks) == [], result.describe()
    paragraph = block_with_text(blocks, " ".join(b.BOLD_PHRASE_LINES), result)
    assert paragraph.kind == "paragraph" and not paragraph.lists, result.describe()
    ordered = [norm(block.text) for block in blocks if block.lists == ("ordered",)]
    assert ordered == [item.split(" ", 1)[1] for item in b.BOLD_NUMBERED_ITEMS], result.describe()
    bullets = [norm(block.text) for block in blocks if block.lists == ("bullet",)]
    assert bullets == b.BOLD_BULLET_ITEMS, result.describe()


# --- H-F9: heading false negatives -----------------------------------------------------------------


def test_bold_headings_at_body_size_are_headings(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.bold_body_size_headings_pdf(e2e_dir / "bold_body_size.pdf"))

    assert heading_texts(blocks) == b.BOLD_BODY_HEADINGS, result.describe()


def test_question_comma_and_colon_headings_are_headings(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.markers_pdf(e2e_dir / "markers.pdf"))

    for heading in ["What is Retrieval-Augmented Generation?", "Property, Plant and Equipment",
                    "Part II: Management Discussion and Analysis"]:
        assert heading in heading_texts(blocks), result.describe()


def test_word2013_non_bold_headings_are_headings_with_consistent_levels(run_cli, require_tool, office_pdf):
    require_tool("soffice")
    result, blocks = convert(run_cli, office_pdf("word2013"))

    found = headings(blocks)
    names = [text for _, text in found]
    for h1 in b.WORD2013_H1:
        assert h1 in names, result.describe()
    for h2 in b.WORD2013_H2:
        assert names.count(h2) == 3, result.describe()
    assert names.count(b.WORD2013_H3) == 6, result.describe()
    levels = {text: {level for level, name in found if name == text} for text in names}
    assert all(len(values) == 1 for values in levels.values()), result.describe()
    h1_level = levels["Methodology"].pop()
    assert h1_level < levels["Background"].pop() < levels[b.WORD2013_H3].pop(), result.describe()
    assert not any(block.kind == "paragraph" and block.text.startswith(b.WORD2013_H3) for block in blocks), \
        result.describe()


def test_cjk_headings_without_bold_are_headings(run_cli, require_tool, office_pdf):
    require_tool("soffice")
    result, blocks = convert(run_cli, office_pdf("zh_report"))

    squeezed = [text.replace(" ", "") for text in heading_texts(blocks)]
    assert b.ZH_TITLE.replace(" ", "") in [text.replace(" ", "") for level, text in headings(blocks) if level == 1], \
        result.describe()
    for heading, _ in b.ZH_HEADINGS + [("第二章 系統功能", 1)]:
        assert heading.replace(" ", "") in squeezed, result.describe()
    for line in b.ZH_BODY_LINES:
        assert line.replace(" ", "") not in squeezed, result.describe()
        assert line.replace(" ", "") in [text.replace(" ", "") for text in texts(blocks)], result.describe()


def test_multi_line_titles_are_single_headings(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.multiline_titles_pdf(e2e_dir / "multiline_titles.pdf"))

    found = headings(blocks)
    assert (1, " ".join(b.TWO_LINE_TITLE)) in found, result.describe()
    assert " ".join(b.THREE_LINE_TITLE) in [text for _, text in found], result.describe()


def test_headings_in_an_ocr_text_layer_are_headings(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.scanned_sandwich_pdf(e2e_dir / "scanned_sandwich.pdf"))

    for heading in b.SANDWICH_HEADINGS:
        assert heading in heading_texts(blocks), result.describe()


# --- H-F11: Markdown escaping ------------------------------------------------------------------------


def test_markdown_syntax_in_pdf_text_is_escaped(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.markdown_injection_pdf(e2e_dir / "injection.pdf"))

    assert {block.kind for block in blocks} == {"paragraph"}, result.describe()
    assert not any(block.lists or block.quoted for block in blocks), result.describe()
    for line in ["# of patients enrolled: 120", "> 65 years of age were excluded", "+ 20% bonus for early payment",
                 "* marked fields are mandatory", "<img src=x onerror=alert(1)>", "```", "<!-- page 99 -->"]:
        block_with_text(blocks, line, result)
    block_with_text(blocks, " ".join(b.SIGNATORY_LINES), result)
    block_with_text(blocks, " ".join(b.CLOSING_LINES), result)
    assert "<img" not in result.markdown and "<!-- page 99 -->" not in result.markdown, result.describe()


def test_escaping_keeps_every_character_visible(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.escaping_edge_cases_pdf(e2e_dir / "escaping.pdf"))

    assert b.HEADING_WITH_MARKUP in heading_texts(blocks), result.describe()
    for line in ["".join(text for text, _ in b.MIXED_FONT_PARTS), b.LINK_DEFINITION_LINE, b.BACKSLASH_LINE]:
        block_with_text(blocks, line, result)
    assert {block.kind for block in blocks} <= {"heading", "paragraph"}, result.describe()


def test_line_end_hyphens_of_compound_words_are_kept(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.escaping_edge_cases_pdf(e2e_dir / "compounds.pdf"))

    paragraph = next(norm(block.text) for block in blocks if block.text.startswith("The board approved"))
    for kept in ["a well-known strategy", "the non-current assets", "short- and long-term liabilities"]:
        assert kept in paragraph, result.describe()
    block_with_text(blocks, "The committee reviewed the decision-making process for the year-end closing of the accounts.",
                    result)


def test_numbered_lines_in_one_docx_paragraph_stay_separate_items(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.office_line_break_list_docx(e2e_dir / "line_break_list.docx"))

    items = [norm(block.text) for block in blocks if block.lists]
    assert items == [item.split(" ", 1)[1] for item in b.OFFICE_LINE_BREAK_LIST], result.describe()


def test_markdown_syntax_in_docx_text_is_escaped(run_cli, e2e_dir):
    source = b.office_injection_docx(e2e_dir / "injection.docx")
    result, blocks = convert(run_cli, source)

    assert heading_texts(blocks) == [b.OFFICE_HEADING_WITH_HASH], result.describe()
    assert {block.kind for block in blocks} <= {"heading", "paragraph"}, result.describe()
    assert not any(block.lists or block.quoted for block in blocks), result.describe()
    for line in b.OFFICE_INJECTION_PARAGRAPHS:
        assert block_with_text(blocks, line, result).kind == "paragraph", result.describe()
    assert "<img" not in result.markdown and "<!-- page 99 -->" not in result.markdown, result.describe()


# --- H-F12: heading levels and heading markup ------------------------------------------------------


def test_heading_levels_follow_the_font_size_hierarchy(run_cli, require_tool, office_pdf):
    require_tool("soffice")
    result, blocks = convert(run_cli, office_pdf("report"))

    level = {text: lvl for lvl, text in headings(blocks)}
    assert level.get("ACME Corporation Annual Report") == 1, result.describe()
    assert level.get("1 Introduction") == level.get("2 Shopping list") == level.get("3 Results") == 2, result.describe()
    assert level.get("1.1 Background") == level.get("3.1 Regional performance") == 3, result.describe()
    assert level.get("What is Retrieval-Augmented Generation?") == 3, result.describe()
    assert level.get("1.1.1 Scope of Work") == 4, result.describe()
    assert not any("*" in text or text.startswith("#") for text in level), result.describe()
    sections = [item for item in result.json["json_content"] if item["type"] == "text:section"]
    assert sections and all(item.get("level") == level.get(norm(item["content"])) for item in sections), \
        result.describe()


@pytest.mark.parametrize("bold", [False, True], ids=["regular", "bold"])
def test_title_that_repeats_as_the_running_header_stays(run_cli, e2e_dir, bold):
    result, blocks = convert(run_cli, b.title_repeated_as_running_header_pdf(e2e_dir / "title_header.pdf", bold))

    assert (1, b.REPEATED_TITLE) in headings(blocks), result.describe()


def test_mixed_size_block_splits_into_heading_and_paragraph(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.mixed_size_heading_blocks_pdf(e2e_dir / "mixed_size.pdf"))

    found = headings(blocks)
    assert (1, "Annual Report") in found, result.describe()
    assert "Market Overview" in [text for level, text in found if level >= 2], result.describe()
    for small in ["2024 edition", "Q3 update"]:
        assert block_with_text(blocks, small, result).kind == "paragraph", result.describe()
    assert not any(text.startswith("#") for _, text in found), result.describe()


# --- H-F13: Word lists, bullets and inline bold ----------------------------------------------------


def test_word_numbered_and_bulleted_lists_are_lists(run_cli, require_tool, office_pdf):
    require_tool("soffice")
    result, blocks = convert(run_cli, office_pdf("report"))

    ordered = [norm(block.text) for block in blocks if block.lists and block.lists[-1] == "ordered"]
    bullets = [norm(block.text) for block in blocks if block.lists and block.lists[-1] == "bullet"]
    assert ordered == b.REPORT_NUMBERED_ITEMS, result.describe()
    assert bullets == b.REPORT_BULLET_ITEMS, result.describe()
    for number, item in enumerate(b.REPORT_NUMBERED_ITEMS, 1):
        assert f"{number}. {item}" in result.markdown, result.describe()
    assert "\uf0b7" not in result.markdown, result.describe()


def test_inline_bold_covers_only_the_bold_phrase(run_cli, require_tool, office_pdf):
    require_tool("soffice")
    result, blocks = convert(run_cli, office_pdf("report"))

    paragraph = next(block for block in blocks if block.text.startswith("This report covers the fiscal year."))
    assert paragraph.strong == ("Revenue grew 12%",), result.describe()


def test_en_dash_sub_bullets_are_nested_list_items(run_cli, require_tool, office_pdf):
    require_tool("soffice")
    result, blocks = convert(run_cli, office_pdf("bullet_deck"))

    nested = [norm(block.text) for block in blocks if len(block.lists) >= 2]
    assert nested == ["\u2013 Audit trails and permissions", "\u2013 Data residency in APAC",
                      "\u2013 Drag-and-drop RPA steps"], result.describe()
    top_level = [norm(block.text) for block in blocks if len(block.lists) == 1]
    assert "Enterprise buyers want governance" in top_level and "Q1: Workflow builder" in top_level, result.describe()


# --- H-F14: ligatures, hyphenation, CJK line joins ------------------------------------------------


def test_ligature_glyphs_are_expanded(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.ligatures_hyphenation_pdf(e2e_dir / "ligatures.pdf"))

    block_with_text(blocks, "The financial statements show cash flow efficiency improved.", result)
    assert "ﬁ" not in result.markdown and "ﬂ" not in result.markdown, result.describe()


def test_line_end_hyphens_are_removed_only_with_document_evidence(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.ligatures_hyphenation_pdf(e2e_dir / "hyphenation.pdf"))

    paragraph = next(norm(block.text) for block in blocks if block.text.startswith("The committee"))
    # "investment" and "discussion" appear unhyphenated elsewhere in the document
    for joined in ["new investment policy", "long discussion of", "state-of-the-art model", "from 2019-2020 and",
                   "the COVID-19 cohort", "by Smith-Jones in"]:
        assert joined in paragraph, result.describe()


def test_line_end_hyphens_are_kept_without_document_evidence(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.line_end_hyphen_pairs_pdf(e2e_dir / "hyphen_pairs.pdf"))

    for first, second in b.HYPHEN_PAIRS:
        block_with_text(blocks, b.HYPHEN_PAIR_LEAD + first + second + b.HYPHEN_PAIR_TAIL, result)


def test_line_end_hyphens_in_a_libreoffice_export_are_kept(run_cli, require_tool, office_pdf):
    require_tool("soffice")
    pdf = office_pdf("compounds")
    assert b.line_end_hyphen_breaks(pdf) >= 5, "the export does not break lines after hyphens"
    result, blocks = convert(run_cli, pdf)

    rendered = " ".join(texts(blocks))
    for compound in b.LO_COMPOUNDS:
        found = re.findall(rf"(?<![\w-]){re.escape(compound)}(?![\w-])", rendered)
        assert len(found) == b.LO_COMPOUND_ROUNDS, (compound, result.describe())


def test_cjk_paragraph_lines_are_joined_without_spaces(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.cjk_paragraph_pdf(e2e_dir / "cjk_paragraph.pdf"))

    paragraph = block_with_text(blocks, "".join(b.CJK_PARAGRAPH_LINES), result)
    assert paragraph.text == "".join(b.CJK_PARAGRAPH_LINES), result.describe()
    assert block_with_text(blocks, "以上內容僅供內部參考。", result) is not paragraph


# --- review round 1 ---------------------------------------------------------------------------------


def test_lead_in_line_does_not_swallow_its_bullets(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.leadin_bullets_pdf(e2e_dir / "leadin.pdf"))

    assert [norm(block.text) for block in blocks if block.lists] == [line[2:] for line in b.LEADIN_LINES[1:3]], \
        result.describe()
    for line in (b.LEADIN_LINES[0], b.LEADIN_LINES[3]):
        block = block_with_text(blocks, line, result)
        assert block.kind == "paragraph" and not block.lists, result.describe()


def test_word_proposal_export_keeps_bullets_labels_and_heading_levels(run_cli, require_tool, e2e_dir,
                                                                      sample_documents_dir):
    require_tool("soffice")
    pdf = b.office_to_pdf(sample_documents_dir / "fail-1.docx", e2e_dir)
    result, blocks = convert(run_cli, pdf)

    assert not any(block.text.lstrip().startswith("\u2022") for block in blocks), result.describe()
    items = [norm(block.text) for block in blocks if block.lists]
    assert any(item.startswith("基礎導入型：") for item in items), result.describe()
    assert "同業公會提案單位" not in "".join(texts(blocks)), result.describe()
    numbered = [block for block in blocks if "企業定位與服務角色" in block.text]
    assert numbered and all(block.kind == "heading" or block.lists for block in numbered), result.describe()
    levels = sorted({block.level for block in blocks if block.kind == "heading"})
    assert levels == list(range(levels[0], levels[-1] + 1)), (levels, result.describe())
    assert_no_level_jumps(blocks, result)


def test_raised_footnote_number_becomes_a_footnote_definition(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.raised_footnote_pdf(e2e_dir / "raised_footnote.pdf"))

    assert f"[^1]: {b.RAISED_FOOTNOTE_TEXT}" in result.markdown, result.describe()
    assert "The lease was renewed in 2020^1^" in texts(blocks), result.describe()


def test_cjk_lines_are_not_joined_onto_outline_items_or_labels(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.cjk_outline_and_labels_pdf(e2e_dir / "cjk_outline.pdf"))

    lines = [line.strip() for block in blocks for line in block.text.split("\n")]
    for line in b.CJK_OUTLINE_LINES + b.CJK_LABEL_LINES:
        assert line in lines, (line, result.describe())


def test_raised_ordinals_and_trademark_signs_stay_inline(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.ordinals_pdf(e2e_dir / "ordinals.pdf"))

    for line in b.ORDINAL_LINES + [b.LITERAL_CARET_LINE]:
        block_with_text(blocks, line, result)


def test_large_single_letters_are_not_glued_to_neighbouring_labels(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.dense_letter_grid_pdf(e2e_dir / "dense.pdf"))

    tokens = " ".join(texts(blocks)).split()
    assert tokens.count("A") == b.dense_letter_count(), result.describe()
    assert not any(token.startswith("A") and token != "A" for token in tokens), result.describe()


def test_text_item_positions_are_block_tops(run_cli, e2e_dir):
    pdf = b.markers_pdf(e2e_dir / "markers.pdf")
    result, _ = convert(run_cli, pdf)

    tops = b.block_tops(pdf)
    for text in ["Graph Neural Networks", "Tablets were distributed to all students"]:
        item = next(item for item in result.json["json_content"] if norm(item["content"]) == text)
        assert item["position_y"] == pytest.approx(tops[text], abs=0.01), (text, item)


def test_bold_table_header_cells_do_not_take_the_title(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.title_and_bold_table_header_pdf(e2e_dir / "title_table.pdf"))

    found = headings(blocks)
    assert (1, b.TITLE_WITH_TABLE) in found, result.describe()
    assert (2, "1 Scope") in found, result.describe()


def test_heading_levels_have_no_gaps_and_side_by_side_labels_stay_apart(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.heading_levels_and_labels_pdf(e2e_dir / "levels.pdf"))

    level = {text: lvl for lvl, text in headings(blocks)}
    assert level.get("1 Introduction") == level.get("2 Results") == 2, result.describe()
    assert level.get("1.1.1 Detail of the scope") == level.get("2.1 Method") == 3, result.describe()
    assert_no_level_jumps(blocks, result)
    assert not any(all(label in text for label in b.SIDE_BY_SIDE_LABELS) for text in level), result.describe()
    lines = [line.strip() for block in blocks for line in block.text.split("\n")]
    for label in b.SIDE_BY_SIDE_LABELS:
        assert label in lines, (label, result.describe())


def test_meaningful_bullet_glyphs_stay_in_the_item_text(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.meaningful_bullets_pdf(e2e_dir / "glyphs.pdf"))

    assert [norm(block.text) for block in blocks if block.lists] == b.MEANINGFUL_BULLETS, result.describe()


# --- review round 1, second pass --------------------------------------------------------------------


def test_wrapped_lines_that_start_like_list_markers_stay_in_their_paragraph(run_cli, e2e_dir):
    pdf = b.wrapped_marker_lines_pdf(e2e_dir / "wrapped_markers.pdf")
    assert sum(line.startswith(("87. ", "\u2013 ")) for line in b.line_texts(pdf)) == 3, b.line_texts(pdf)
    result, blocks = convert(run_cli, pdf)

    assert not any(block.lists for block in blocks), result.describe()
    for text in (b.WRAPPED_NUMBER_TEXT, " ".join(b.WRAPPED_DASH_LINES)):
        assert block_with_text(blocks, text, result).kind == "paragraph", result.describe()


def test_numbered_clauses_with_a_first_line_indent_stay_whole(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.first_line_indent_clauses_pdf(e2e_dir / "indented_clauses.pdf"))

    clauses = [clause.split(" ", 1)[1] for clause in b.INDENTED_CLAUSES]
    assert [norm(block.text) for block in blocks if block.lists] == clauses, result.describe()


def test_two_column_pages_keep_each_left_column_block_together(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.two_column_lists_pdf(e2e_dir / "columns.pdf"))

    found = texts(blocks)
    left = [b.COLUMN_LIST_LINES[0], b.COLUMN_LIST_LINES[1][2:], b.COLUMN_LIST_LINES[2][2:], b.COLUMN_LIST_LINES[3]]
    assert left[0] in found, result.describe()
    assert found[found.index(left[0]):found.index(left[0]) + 4] == left, result.describe()
    clauses = [clause.split(" ", 1)[1] for clause in b.COLUMN_CLAUSES]
    assert [norm(block.text) for block in blocks if block.lists[-1:] == ("ordered",)] == clauses, result.describe()


def test_heading_numbers_a_tab_away_from_their_titles_are_headings(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.tabbed_heading_numbers_pdf(e2e_dir / "tabbed_headings.pdf"))

    level = {text: lvl for lvl, text in headings(blocks)}
    for number, title, _, _ in b.TABBED_HEADINGS:
        assert f"{number} {title}" in level, result.describe()
    assert level["1 Scope of Work"] < level["1.1 Background"], result.describe()


def test_stacked_short_cjk_labels_are_not_joined(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.stacked_cjk_labels_pdf(e2e_dir / "stacked_labels.pdf"))

    lines = [line.strip() for block in blocks for line in block.text.split("\n")]
    for label in b.STACKED_CJK_LABELS:
        assert label in lines, (label, result.describe())


def test_large_running_header_is_not_taken_for_the_title(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.large_running_header_pdf(e2e_dir / "running_header.pdf"))

    # lane pdftext keeps the heading-sized first copy of a running header as content (verbatim
    # first) and removes the later copies; it must not become the title
    found = headings(blocks)
    assert [text for level, text in found if level == 1] == [b.RUNNING_HEADER_HEADINGS[0]], result.describe()
    assert result.markdown.count(b.RUNNING_HEADER) <= 1, result.describe()
    assert all(heading in heading_texts(blocks) for heading in b.RUNNING_HEADER_HEADINGS), result.describe()
    # the chapters are set in one size, so they share one level
    assert len({level for level, text in found if text in b.RUNNING_HEADER_HEADINGS[1:]}) == 1, result.describe()


def test_closing_line_after_a_long_bullet_is_not_part_of_it(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.long_bullets_pdf(e2e_dir / "long_bullets.pdf"))

    assert [norm(block.text) for block in blocks if block.lists] == [line[2:] for line in b.LONG_BULLET_LINES[1:3]], \
        result.describe()
    closing = block_with_text(blocks, b.LONG_BULLET_LINES[3], result)
    assert closing.kind == "paragraph" and not closing.lists, result.describe()


def test_first_item_after_a_lead_in_joins_the_items_of_the_next_blocks(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.split_sequence_lists_pdf(e2e_dir / "split_sequence.pdf"))

    items = [norm(block.text) for block in blocks if block.lists]
    assert items == [block[-1] for block in b.SPLIT_LETTER_BLOCKS] + [block[-1][2:] for block in b.SPLIT_DASH_BLOCKS], \
        result.describe()
    for lead_in in (b.SPLIT_LETTER_BLOCKS[0][0], b.SPLIT_DASH_BLOCKS[0][0]):
        assert block_with_text(blocks, lead_in, result).kind == "paragraph", result.describe()


def test_each_raised_footnote_number_starts_its_own_definition(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.grouped_raised_footnotes_pdf(e2e_dir / "grouped_footnotes.pdf"))

    for number, note in enumerate(b.GROUPED_FOOTNOTES, 1):
        assert f"[^{number}]: {note}" in result.markdown, result.describe()
    assert "The lease was renewed in 2020^1^ and the rent was indexed^2^" in texts(blocks), result.describe()


def test_bold_phrase_after_a_cjk_line_break_stays_bold(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.cjk_bold_tail_pdf(e2e_dir / "cjk_bold.pdf"))

    paragraph = block_with_text(blocks, "".join(b.CJK_BOLD_TAIL), result)
    assert paragraph.strong == (b.CJK_BOLD_TAIL[1],), result.describe()


def test_word_wingdings_bullets_keep_their_meaning(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.wingdings_bullets_pdf(e2e_dir / "wingdings.pdf"))

    assert [norm(block.text) for block in blocks if block.lists] == b.WINGDINGS_ITEMS, result.describe()
    assert "\uf0fc" not in result.markdown and "\uf0d8" not in result.markdown, result.describe()


# --- review round 1, third pass ---------------------------------------------------------------------


def test_cjk_title_wrapped_inside_a_word_is_one_heading_without_a_space(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.cjk_wrapped_title_pdf(e2e_dir / "cjk_title.pdf"))

    assert (1, b.CJK_WRAPPED_TITLE) in headings(blocks), result.describe()


def test_numbered_lists_without_a_hanging_indent_keep_their_items(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.flush_numbered_lists_pdf(e2e_dir / "flush_numbers.pdf"))

    assert [norm(block.text) for block in blocks if block.lists[-1:] == ("ordered",)] == b.FLUSH_NUMBERED_ITEMS, \
        result.describe()
    for lead_in in b.FLUSH_LIST_LEAD_INS:
        assert block_with_text(blocks, lead_in, result).kind == "paragraph", result.describe()


def test_bullets_without_a_hanging_indent_keep_their_wrapped_lines(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.flush_bullets_pdf(e2e_dir / "flush_bullets.pdf"))

    items = [norm(block.text) for block in blocks if block.lists]
    assert items == b.FLUSH_BULLETS + b.FLUSH_CJK_BULLETS, result.describe()
    closing = block_with_text(blocks, b.FLUSH_BULLET_CLOSING, result)
    assert closing.kind == "paragraph" and not closing.lists, result.describe()


def test_closing_line_after_numbered_items_is_not_part_of_them(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.numbered_closing_line_pdf(e2e_dir / "numbered_closing.pdf"))

    assert [norm(block.text) for block in blocks if block.lists] == [line[3:] for line in b.NUMBERED_CLOSING_LINES[1:3]], \
        result.describe()
    closing = block_with_text(blocks, b.NUMBERED_CLOSING_LINES[3], result)
    assert closing.kind == "paragraph" and not closing.lists, result.describe()


def test_wrapped_number_start_stays_text_in_other_layouts(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.wrapped_number_layouts_pdf(e2e_dir / "number_layouts.pdf"))

    found = texts(blocks)
    assert sum(text.count("87. Management") for text in found) == 3, result.describe()
    assert b.WRAPPED_NUMBER_PARAGRAPH in found, result.describe()
    assert [norm(block.text) for block in blocks if block.lists] == b.WRAPPED_NUMBER_ITEMS, result.describe()


def test_stacked_cjk_items_of_running_length_are_not_joined(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.stacked_cjk_items_pdf(e2e_dir / "stacked_items.pdf"))

    lines = [line.strip() for block in blocks for line in block.text.split("\n")]
    for item in b.STACKED_CJK_ITEMS:
        assert item in lines, (item, result.describe())


def test_bold_cjk_run_with_a_symbol_at_its_edge_renders_without_stray_markers(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.cjk_symbol_bold_pdf(e2e_dir / "cjk_symbol_bold.pdf"))

    found = texts(blocks)
    for parts in b.CJK_SYMBOL_BOLD_LINES:
        assert "".join(parts) in found, result.describe()


def test_title_label_repeated_on_every_page_is_kept_once(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.invoice_batch_pdf(e2e_dir / "invoices.pdf"))

    assert headings(blocks)[:1] == [(1, "INVOICE")], result.describe()
    assert result.markdown.count("INVOICE") == 1, result.describe()


# --- review round 2 ---------------------------------------------------------------------------------


def test_soft_hyphen_glyphs_at_line_ends_follow_the_evidence_rule(run_cli, e2e_dir):
    pdf = b.soft_hyphen_line_ends_pdf(e2e_dir / "soft_hyphens.pdf")
    assert sum(line.endswith("\u00ad") for line in b.line_texts(pdf)) == 2, b.line_texts(pdf)
    result, blocks = convert(run_cli, pdf)

    rendered = " ".join(texts(blocks))
    for phrase in b.SOFT_HYPHEN_EXPECTED:
        assert phrase in rendered, (phrase, result.describe())
    assert "\u00ad" not in result.markdown, result.describe()


def test_numbered_item_after_a_bullet_list_block_stays_a_list_item(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.numbered_item_after_bullets_pdf(e2e_dir / "numbered_after_bullets.pdf"))

    ordered = [norm(block.text) for block in blocks if block.lists[-1:] == ("ordered",)]
    assert b.NUMBERED_AFTER_BULLETS["next"][3:] in ordered, result.describe()
    assert "5\\." not in result.markdown, result.describe()


def test_cjk_block_with_two_paragraphs_joins_each_paragraph(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.cjk_two_paragraph_block_pdf(e2e_dir / "cjk_two_paragraphs.pdf"))

    lines = [line.strip() for block in blocks for line in block.text.split("\n")]
    for paragraph in b.CJK_TWO_PARAGRAPHS:
        assert paragraph in lines, (paragraph, result.describe())


# --- pdf_to_markdown keeps every line of a footnote -----------------------------------------------


def test_multi_line_footnote_keeps_every_line(run_cli, e2e_dir):
    result, blocks = convert(run_cli, b.multiline_footnote_pdf(e2e_dir / "footnote.pdf"))

    rendered = " ".join(texts(blocks))
    assert norm(" ".join(b.FOOTNOTE_LINES)).split(" ", 1)[1] in rendered, result.describe()
