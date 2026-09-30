"""Markdown escaping for text taken from documents.

Converters turn untrusted document text into Markdown. The policy shared by all converters is to
escape only what would change the Markdown/HTML structure or make characters disappear, so the
text stays verbatim for lexical retrieval:

* raw HTML: ``<`` becomes ``&lt;`` when it could open a tag, closing tag, comment, declaration or
  processing instruction (``<`` followed by a letter, ``/``, ``!`` or ``?``); ``x < 5`` is kept;
* entities: ``&`` becomes ``&amp;`` only when it starts an entity-like sequence (``&amp;``,
  ``&#39;``, ``&#x27;``), so ``AT&T`` is kept;
* backslashes: a backslash before ASCII punctuation or at the end of a line is doubled, since
  CommonMark would otherwise consume it (``C:\\*.txt``, ``\\\\server``);
* block syntax at the start of a line gets a backslash: ATX headings (``#`` .. ``######`` + space),
  block quotes (``>``), bullets (``-``/``+``/``*`` + space), ordered-list markers (``12.``/``12)`` +
  space), code fences (```` ``` ````/``~~~``), lines made only of ``-``/``=``/``*``/``_``
  (thematic breaks and setext underlines) and link reference definitions (``[1]: …``);
* C0 control characters are removed, except ``\\n`` and ``\\t``; ``\\r\\n``, ``\\r``, ``\\v`` and ``\\f``
  are line separators and become ``\\n`` (a space in text that stays on one line).

Inline ``*``, ``_`` and backticks are left alone: they can change emphasis, not structure.
"""

import re
import unicodedata
from typing import List, Optional

_LINE_SEPARATORS = re.compile(r"\r\n?|[\x0b\x0c]")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0e-\x1f]")
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]{0,31});")
_ASCII_PUNCTUATION = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_BLOCK_START = re.compile(
    r"(?P<indent>[ \t]{0,3})(?:"
    r"(?P<marker>#{1,6}(?=[ \t]|$)|>|[-+*](?=[ \t]|$)|`{3,}|~{3,}|\[(?=[^\]]*\]:))"
    r"|\d{1,9}(?P<delimiter>[.)])(?=[ \t]|$)"
    r")"
)
_RULE_LINE = re.compile(r"(?P<indent>[ \t]{0,3})[-=*_][-=*_ \t]*$")
_ATX_CLOSING = re.compile(r"(?:^|(?<=[ \t]))#+[ \t]*$")
_LIST_ITEM = re.compile(r"[ \t]*(?:[-+*]|\d{1,2}[.)])[ \t]+(?=\S)")
_CJK_CHAR = re.compile("[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")


def normalize_control_chars(text: str) -> str:
    """Turn ``\\r\\n``/``\\r``/``\\v``/``\\f`` into ``\\n`` and drop the other C0 controls (``\\t`` is kept)."""
    return _CONTROL_CHARS.sub("", _LINE_SEPARATORS.sub("\n", text))


def escape_inline_pieces(text: str) -> List[str]:
    """The inline escaping of ``text`` one character at a time: ``pieces[i]`` replaces
    ``text[i]``. Converters that build a line from several styled pieces escape the whole line
    with this and slice it, so a ``<`` or ``&`` in one piece still sees the characters after it.
    Line separators become spaces and other control characters disappear."""
    entity_starts = {match.start() for match in _ENTITY.finditer(text)}
    pieces: List[str] = []
    for index, char in enumerate(text):
        following = text[index + 1:index + 2]
        if char in "\r\n\x0b\x0c":
            pieces.append("" if char == "\n" and text[index - 1:index] == "\r" else " ")
        elif _CONTROL_CHARS.match(char):
            pieces.append("")
        elif char == "<" and following and (following.isascii() and following.isalpha() or following in "/!?"):
            pieces.append("&lt;")
        elif char == "&" and index in entity_starts:
            pieces.append("&amp;")
        elif char == "\\" and (not following or following in _ASCII_PUNCTUATION or following in "\r\n"):
            pieces.append("\\\\")
        else:
            pieces.append(char)
    return pieces


def escape_inline(text: str) -> str:
    """Escape raw HTML starts, entity-like ``&`` sequences and backslashes that CommonMark would
    consume, drop control characters and turn line separators into spaces. For text that stays
    on one Markdown line (inside a heading, after a list marker, between inline markup)."""
    return "".join(escape_inline_pieces(text))


def escape_line_start(line: str) -> str:
    """Backslash-escape block syntax at the start of one Markdown line (see the module docstring)."""
    match = _BLOCK_START.match(line)
    if match:
        if match.group("delimiter"):
            cut = match.start("delimiter")
        else:
            cut = match.start("marker")
        return line[:cut] + "\\" + line[cut:]
    match = _RULE_LINE.match(line)
    if match:
        cut = match.end("indent")
        return line[:cut] + "\\" + line[cut:]
    return line


def escape_markdown_text(text: str) -> str:
    """Escape body text: inline escaping plus block syntax at the start of every line."""
    return "\n".join(escape_line_start(escape_inline(line)) for line in normalize_control_chars(text).split("\n"))


def escape_heading_text(text: str) -> str:
    """Escape the text of an ATX heading: one line, inline escaping, and a trailing run of ``#``
    that CommonMark would drop as the closing sequence (``Ticket #``) keeps its ``#``."""
    lines = normalize_control_chars(text).split("\n")
    return escape_heading_closing(escape_inline(" ".join(part.strip() for part in lines if part.strip())))


def escape_heading_closing(line: str) -> str:
    """Backslash-escape a trailing run of ``#`` in an (already inline-escaped) heading line, which
    CommonMark would otherwise drop as the heading's closing sequence."""
    match = _ATX_CLOSING.search(line)
    if match:
        line = line[:match.start()] + "\\" + line[match.start():]
    return line


def _is_cjk(char: str) -> bool:
    return bool(char) and bool(_CJK_CHAR.match(char))


def _is_punctuation(char: str) -> bool:
    """CommonMark (0.31) punctuation: a Unicode punctuation (P*) or symbol (S*) character."""
    return bool(char) and unicodedata.category(char)[0] in "PS"


def _continues_word(char: str) -> bool:
    """A letter or digit of a script that separates words with spaces, fullwidth Latin letters and
    digits included: markup next to it would split a word for lexical retrieval (``A**I**``). CJK
    characters need no spaces between words."""
    return char.isalnum() and (not _is_cjk(char) or 0xFF10 <= ord(char) <= 0xFF5A)


def emphasis_fits(before: str, core: str, after: str) -> bool:
    """True when emphasis markers around ``core`` (``before`` / ``after``: the characters next to it,
    "" at a line edge) keep Latin words whole (no ``A**I**``) and CommonMark can open and close
    them: a marker next to punctuation inside needs a space, punctuation or the line edge outside,
    so ``的**資料治理、**流程`` stays unmarked and ``的**資料治理、流程。**`` is marked."""
    for outside, inside in ((before, core[:1]), (after, core[-1:])):
        if _continues_word(outside):
            return False
        if outside and not outside.isspace() and not _is_punctuation(outside) and _is_punctuation(inside):
            return False
    return True


def wrap_inline(text: str, opening: str, closing: Optional[str] = None) -> str:
    """Wrap ``text`` in inline markers (``closing`` defaults to ``opening``), keeping the spaces around it
    outside them."""
    core = text.strip()
    if not core:
        return text
    start = len(text) - len(text.lstrip())
    closing = opening if closing is None else closing
    return f"{text[:start]}{opening}{core}{closing}{text[start + len(core):]}"


def escape_list_item(text: str) -> str:
    """Escape list item text that may already carry its own markers (``- ``, ``* ``, ``+ ``,
    ``3. ``, ``3) ``): on every line a marker is kept and the rest is escaped as body text. Only
    ``1``..``99`` count as item numbers; ``2024. The year …`` is text and is escaped."""
    escaped = []
    for line in normalize_control_chars(text).split("\n"):
        match = _LIST_ITEM.match(line)
        if match:
            escaped.append(match.group(0) + escape_line_start(escape_inline(line[match.end():])))
        else:
            escaped.append(escape_line_start(escape_inline(line)))
    return "\n".join(escaped)
