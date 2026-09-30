"""Unit tests for doc2mark.utils.markdown: the escaping policy for document text in Markdown.

Control characters and entity-like sequences cannot be produced reliably through a PDF text layer,
so the helper is checked directly here; tests/e2e/test_md.py covers it through the CLI.
"""

import pytest
from markdown_it import MarkdownIt

from doc2mark.utils.markdown import (
    escape_heading_text,
    escape_inline,
    escape_list_item,
    escape_markdown_text,
    normalize_control_chars,
)

COMMONMARK = MarkdownIt("commonmark")


def rendered_paragraph_text(markdown: str) -> str:
    """The text of the single paragraph ``markdown`` renders to (fails on any other block)."""
    tokens = COMMONMARK.parse(markdown)
    assert [token.type for token in tokens] == ["paragraph_open", "inline", "paragraph_close"], markdown
    return "".join(child.content if child.type != "softbreak" else "\n" for child in tokens[1].children)


@pytest.mark.parametrize("line", [
    "# of patients enrolled: 120",
    "###### deep",
    "> 65 years of age were excluded",
    "- dash item",
    "+ 20% bonus",
    "* marked fields",
    "2024. The year the plant opened",
    "3) third",
    "```",
    "~~~ fence",
    "---",
    "===",
    "***",
    "___",
    "- - -",
    "   # indented heading",
    "<img src=x onerror=alert(1)>",
    "<!-- page 99 -->",
    "</div>",
    "<?php echo 1; ?>",
    "<!DOCTYPE html>",
])
def test_block_syntax_renders_as_the_same_text(line):
    assert rendered_paragraph_text(escape_markdown_text(line)) == line.strip()


@pytest.mark.parametrize("text", ["#hashtag", "1.5x faster", "x < 5 and y > 3", "AT&T", "C# and F#", "a_b_c"])
def test_text_without_structure_is_left_alone(text):
    assert escape_markdown_text(text) == text


def test_entity_like_sequences_are_escaped_and_render_literally():
    text = "&amp; &#39; &#x27; &copy;"
    assert rendered_paragraph_text(escape_markdown_text(text)) == text


def test_every_line_start_is_escaped():
    text = "Authorised signatory\n----------------------------\nJohn Smith, CFO"
    assert rendered_paragraph_text(escape_markdown_text(text)) == text


def test_control_characters_are_dropped_and_line_separators_become_newlines():
    assert normalize_control_chars("a\x00b\x07c\td\r\ne\rf\x0bg\x0ch\x1fi") == "abc\td\ne\nf\ng\nhi"
    assert escape_inline("x\x08y") == "xy"


@pytest.mark.parametrize("text, rendered", [
    ("Ticket #", "Ticket #"),
    ("###", "###"),
    ("C#", "C#"),
    ("Enterprise AI\nOperating System", "Enterprise AI Operating System"),
    ("<b>bold</b> heading", "<b>bold</b> heading"),
])
def test_heading_text_is_one_line_and_keeps_trailing_hashes(text, rendered):
    tokens = COMMONMARK.parse(f"## {escape_heading_text(text)}")
    assert tokens[0].type == "heading_open" and tokens[1].content, text
    assert "".join(child.content for child in tokens[1].children) == rendered


def test_list_item_keeps_its_marker_and_escapes_the_rest():
    tokens = COMMONMARK.parse(escape_list_item("1. # of units"))
    assert [token.type for token in tokens][:3] == ["ordered_list_open", "list_item_open", "paragraph_open"]
    assert tokens[3].content == "\\# of units"
    assert escape_list_item("2024. The year") == "2024\\. The year"
    assert escape_list_item("plain > text") == "plain > text"


@pytest.mark.parametrize("line", [
    "[1]: https://example.com/ref",
    "  [note]: see below",
    "C:\\temp\\*.txt",
    "\\\\server\\share",
    "ends with a backslash \\",
    "escaped \\# already",
])
def test_link_definitions_and_backslashes_render_literally(line):
    assert rendered_paragraph_text(escape_markdown_text(line)) == line.strip()


def test_inline_escaping_turns_line_separators_into_spaces():
    assert escape_inline("Totals\r# Injected\x0c- bullet\nend") == "Totals # Injected - bullet end"


def test_each_line_of_a_multi_line_list_item_keeps_its_marker():
    tokens = COMMONMARK.parse(escape_list_item("1. First\n2. Second\n3. Third"))
    items = [token.content for token in tokens if token.type == "inline"]
    assert items == ["First", "Second", "Third"]
