"""The built-in HTML converter, ``SimpleHTMLToMarkdown``.

``MarkupProcessor`` falls back to it when ``markdownify`` is not installed (it is not a dependency of doc2mark, so
a plain ``pip install doc2mark`` always uses it), and the e-mail processor uses it for the HTML body of a message.
"""

from doc2mark.formats.markup import MarkupProcessor, SimpleHTMLToMarkdown


def _convert(html: str) -> str:
    parser = SimpleHTMLToMarkdown()
    parser.feed(html)
    return parser.get_markdown()


def test_script_and_style_text_is_not_content():
    """Breaks if the text of <script> and <style> elements is written into the Markdown."""
    html = (
        "<html><head><style>p { color: red; }</style></head>"
        "<body><p>Before</p><script>var hidden = 1;</script><p>After</p></body></html>"
    )

    markdown = _convert(html)

    assert "Before" in markdown
    assert "After" in markdown
    assert "color" not in markdown
    assert "hidden" not in markdown


def test_anchor_without_href_leaves_no_bracket():
    """Breaks if an <a> that is only a target (no href) opens a link bracket that is never closed."""
    markdown = _convert('<p>Jump to <a name="top">the top</a> now</p>')

    assert "the top" in markdown
    assert "[" not in markdown
    assert "]" not in markdown
    # A real link is unchanged
    assert _convert('<a href="https://example.com/x">link</a>') == "[link](https://example.com/x)"


def test_code_block_has_no_inline_code_backticks_of_its_own():
    """Breaks if <code> inside <pre> adds backticks inside the fenced block, where they become part of the code."""
    assert _convert("<pre><code>print(1)</code></pre>") == "```\nprint(1)\n```"
    # Inline code outside a block keeps its backticks
    assert "`x`" in _convert("<p>Use <code>x</code> here</p>")


def test_html_file_without_markdownify_drops_style_and_script_text(tmp_path, monkeypatch):
    """Breaks if the file route puts CSS or JavaScript into the Markdown when the built-in converter is used."""
    monkeypatch.setattr(MarkupProcessor, "markdownify", property(lambda self: None))
    page = tmp_path / "page.html"
    page.write_text(
        "<html><head><title>Page title</title><style>body { margin: 0; }</style></head>"
        "<body><h1>Heading</h1><p>Visible text.</p><script>window.tracker = true;</script></body></html>",
        encoding="utf-8",
    )

    result = MarkupProcessor().process(page)

    assert "Heading" in result.content
    assert "Visible text." in result.content
    assert "margin" not in result.content
    assert "tracker" not in result.content
