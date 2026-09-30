import base64
import hashlib
import json
import logging
import numbers
import re
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Any, Union, Optional, Tuple

import pymupdf

from doc2mark.utils.image_utils import detect_image_format, get_mime_type
from doc2mark.utils.markdown import (escape_heading_closing, escape_inline_pieces, escape_line_start,
                                     escape_markdown_text)
from doc2mark.core.table import TableStyle, TableRenderer, TableData

# --- Image-dominant page OCR strategy ---------------------------------------
# Some PDFs (scanned documents, slide decks exported as pictures) carry their
# content as full-page raster images with little or no text layer. OCR'ing each
# embedded image individually fragments the content and wastes calls on
# decorative logos/icons. For such pages we render the whole page once and OCR
# that single image instead. Heuristic thresholds (general, not file-specific):
_PAGE_RENDER_XREF = -1          # sentinel xref marking a whole-page render
_PAGE_RENDER_DPI = 150          # rasterization DPI for page-level OCR
# Document and page strategy decisions live in core.strategy (shared with the Office
# route); pdf_routing measures the per-page signals they are made from.
from doc2mark.core.strategy import (  # noqa: E402
    decide_doc_strategy as _decide_doc_strategy,
    decide_page_route as _decide_page_route,
    document_signals as _document_signals,
    judge_text_layer as _judge_text_layer,
    pages_without_text as _pages_without_text,
    wants_judgment as _wants_judgment,
    VERBATIM_TAIL_REASONS as _VERBATIM_TAIL_REASONS,
    REASON_ILLEGIBLE as _REASON_ILLEGIBLE,
    MIN_UNCAPTURED_RASTER as _MIN_UNCAPTURED_RASTER,
    NO_TEXT_LIMIT as _NO_TEXT_LIMIT,
)
from doc2mark.pipelines import pdf_images, pdf_layout, pdf_routing  # noqa: E402
# OCR requests go to the provider in batches of at most _OCR_BATCH_IMAGES images (twice the
# provider's concurrency when that is higher) and _OCR_BATCH_BYTES of image data, released
# once answered: a several-thousand-page scan never holds more than one batch of renders.
_OCR_BATCH_IMAGES = 32
_OCR_BATCH_BYTES = 128 * 1024 * 1024

# --- Neighbor-page PDF context for OCR --------------------------------------
# Gemini's INLINE request cap is ~20MB total; stay under it so an inline PDF
# part never 400s. (OpenAI's file cap is 50MB but we gate to the tighter inline
# limit.)
_CONTEXT_PDF_MAX_BYTES = 18 * 1024 * 1024
_WINDOW_CACHE_MAXLEN = 4   # windows overlap; far pages are never reused -> tiny LRU

# --- Running headers/footers ("page chrome") ---------------------------------
# Text lines at the top or bottom of the page that repeat from page to page
# (running headers and footers, page numbers) are page furniture, not content.
# Only strong evidence removes a line; see PDFLoader._detect_page_chrome.
_CHROME_BAND = 0.12            # top/bottom fraction of the page height searched for chrome
_CHROME_MIN_PAGES = 3          # chrome repeats on more than a couple of pages...
_CHROME_MIN_SHARE = 0.5        # ...and on more than half of the document's pages
_CHROME_SLOT_TOLERANCE = 4.0   # points: a running header sits at the same height on every page
_CHROME_RUN_DISTANCE = 2       # a per-chapter header repeats on a page at most this far away
_CHROME_HEADING_RATIO = 1.15   # font size / body size from which a line reads as a heading
_CHROME_NUMBER_SIZE_RATIO = 1.5  # page numbers are never bigger than this x body size (KPI figures are)
_CHROME_JUDGE_THRESHOLD = 0.5  # boilerplate_judge probability from which an ambiguous line is chrome
# Page-number shapes (NFKC-normalised, case-folded text); the number in group "n" is masked
# to "#". Numbers anywhere else in a line stay literal: "Invoice No. 1001", "Step 3",
# "INV-2024-0001" or "Chapter 3" never look like page numbers. In "N/M" and "N of M" the total
# M stays literal too, and a page number is never larger than it: 15/03 ... 20/03 are dates, but
# 1/15 ... 6/15 on six pages read as page numbers.
_PAGE_NUMBER_PATTERNS = [
    (re.compile(r"第\s*(?P<n>\d{1,4})\s*[頁页]"), "第#頁"),                                  # 第 N 頁
    (re.compile(r"(?<!\w)(?P<k>page|pg|pp|p|seite|página|pagina)\.?\s*(?P<n>\d{1,4})(?![\w.,:/-])"),
     r"\g<k> #"),                                                                             # Page N, p. N
    (re.compile(r"(?<![\w.,:/-])(?P<n>\d{1,4})(?P<t>\s*/\s*\d{1,4}|\s+of\s+\d{1,4})(?![\w.,:/-])"),
     r"#\g<t>"),                                                                              # N/M, N of M
    (re.compile(r"(?<![\w-])[-–—]\s*(?P<n>\d{1,4})\s*[-–—](?![\w-])"), "-#-"),                # - N -
    (re.compile(r"^[\[(]?(?P<n>\d{1,4})[\])]?$"), "#"),                                        # N alone
    (re.compile(r"^(?P<n>\d{1,4})(?P<t>\s*[|·•–—]|\s+-\s)"), r"#\g<t>"),                      # N | ACME
    (re.compile(r"(?P<t>[|·•–—]\s*|\s-\s+)(?P<n>\d{1,4})$"), r"\g<t>#"),                      # ACME | N
]
_ROMAN_PAGE_NUMBER = re.compile(r"(?:(?:page|p\.?)\s*)?[-–—]?\s*(?P<r>[ivxlcdm]+)\s*[-–—]?")
_ROMAN_NUMERAL = re.compile(r"m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})")
# Words a bare page number may carry around its number ("Page 3 of 12", "Seite 3 von 12", "第 3 頁，共 12 頁").
_PAGE_WORDS = {"page", "pg", "pp", "p", "seite", "página", "pagina", "第", "頁", "页"}
_PAGE_NUMBER_WORDS = _PAGE_WORDS | {"of", "von", "de", "sur", "di", "van", "共"}

# boilerplate_judge(line_text, context) -> probability that a repeated line is page
# chrome rather than content, or None when it cannot tell (see PDFLoader).
BoilerplateJudge = Callable[[str, Dict[str, Any]], Optional[float]]

logger = logging.getLogger(__name__)


def _digest(data: Union[bytes, str, None]) -> Optional[str]:
    """sha256 of image bytes (or of a base64 context PDF); None for None."""
    if data is None:
        return None
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


from doc2mark.core.types import SimpleContent  # shared content model


@dataclass
class _TextContent(SimpleContent):
    """A text item that may be a heading: ``heading`` is its (font size, outline depth), from
    which ``PDFLoader._choose_title`` picks the title and ``_finalize_text_items`` the levels.
    ``layout`` is where it stands on the page, for the reading order (``PDFLoader._reading_order``)."""
    heading: Optional[Tuple[float, int]] = None
    layout: Optional[pdf_layout.Region] = None


@dataclass(frozen=True)
class _HeadingFeatures:
    normalized: str
    length: int
    line_count: int
    size_ratio: float
    max_size_ratio: float
    is_bold: bool
    is_all_caps: bool
    is_explicit_marker: bool
    is_structured_marker: bool
    text_after_marker: str
    has_cjk: bool
    has_checkbox_marker: bool
    has_sentence_punctuation: bool
    has_trailing_continuation: bool
    separator_count: int
    has_form_field_shape: bool
    has_long_clause_shape: bool
    letter_count: int = 0
    has_color_signal: bool = False
    is_bare_structured_marker: bool = False
    is_bare_cjk_explicit_marker: bool = False


@dataclass(slots=True)
class _PageLine:
    """One text line of a page for running header/footer detection.

    ``rect`` is the line bbox as ``get_text()`` reports it (unrotated page space);
    ``x0``..``y1`` are the same box as displayed, i.e. with ``/Rotate`` applied, so
    "top of the page" means what a reader sees. ``zone`` is "header"/"footer" for
    lines in the top/bottom band and None elsewhere.
    """
    page: int
    text: str
    rect: Tuple[float, float, float, float]
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    zone: Optional[str] = None
    edge: float = 0.0               # distance of the line's centre from its zone's page edge
    norm: str = ""
    template: Optional[str] = None
    numbers: Tuple[int, ...] = ()
    totals: Tuple[Tuple[int, int], ...] = ()  # (N, M) of each masked "N/M" or "N of M"


# --- PDF text blocks -> Markdown ---------------------------------------------
# Ligature glyphs (U+FB00..U+FB06) become their letters so lexical retrieval
# matches "financial", not "ﬁnancial".
_LIGATURES = {code: unicodedata.normalize("NFKC", chr(code)) for code in range(0xFB00, 0xFB07)}
_BOLD_FONT_NAME = re.compile(r"bold|black|heavy|semibold|demibold", re.IGNORECASE)
_ITALIC_FONT_NAME = re.compile(r"italic|oblique", re.IGNORECASE)
_CHAR_FLAG_BOLD = 8             # MuPDF FZ_STEXT_BOLD: bold font face or synthetic bold
_MIN_PAGE_BODY_CHARS = 300      # fewer body characters: use the document's body size
_RUNNING_TEXT_UNITS = 30        # a line this long (CJK characters count twice) is running text
_PROFILE_MAX_PAGES = 60         # pages sampled for the document body size
# Glyphs that always mark list items, including the Symbol/Wingdings bullets that
# Word exports in the private use area (U+F0B7 and friends).
_BULLET_GLYPHS = "•◦▪▫●○■‣⁃∙·➢➤►▶❖◆◇✓✔➔"
_PUA_BULLETS = "\uf0b7\uf0a7\uf0a8\uf076\uf06e\uf0d8\uf0de\uf0e0\uf0fc\uf0a1"
_LIST_GLYPH = re.compile(rf"(?:[{_BULLET_GLYPHS}]\s+|[{_PUA_BULLETS}]\s*)(?=\S)")
# Bullets that carry meaning (a check mark, an arrow, a dash): they stay in the item text after the
# Markdown marker. Word writes its Wingdings check mark and arrows into the private use area.
_MEANINGFUL_BULLETS = "✓✔➔➤►▶➢–—"
_PUA_MEANINGFUL_BULLETS = {"\uf0fc": "\u2713", "\uf0d8": "\u27a2", "\uf0e0": "\u2794"}
# ASCII bullets and dashes are list markers only next to other list items;
# alone they are text ("+ 20% bonus", "* marked fields", "— Mark Twain").
_LIST_CONTEXT_BULLET = re.compile(r"([-*+–—])\s+(?=\S)")
_LIST_ORDERED = re.compile(r"(\d{1,2})([.)])\s+(?=\S)")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_CJK_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
               "壹": 1, "貳": 2, "參": 3, "肆": 4, "伍": 5, "陸": 6, "柒": 7, "捌": 8, "玖": 9, "拾": 10}
# Enumerations that are list markers only in a sequence (a, b, c / i, ii, iii / 一、二、):
# a lone "A. Smith", "E. coli", "p. 12" or "I. Background" is ordinary text.
_LIST_ENUMERATIONS = [
    (re.compile(r"\(([a-z])\)\s*(?=\S)"), "paren-lower", "alpha"),
    (re.compile(r"\(([A-Z])\)\s*(?=\S)"), "paren-upper", "alpha"),
    (re.compile(r"\(([ivxlcdm]{1,6})\)\s*(?=\S)"), "paren-lower-roman", "roman"),
    (re.compile(r"\(([IVXLCDM]{1,6})\)\s*(?=\S)"), "paren-upper-roman", "roman"),
    (re.compile(r"([a-z])([.)])\s+(?=\S)"), "lower", "alpha"),
    (re.compile(r"([A-Z])([.)])\s+(?=\S)"), "upper", "alpha"),
    (re.compile(r"([ivxlcdm]{1,6})([.)])\s+(?=\S)"), "lower-roman", "roman"),
    (re.compile(r"([IVXLCDM]{1,6})([.)])\s+(?=\S)"), "upper-roman", "roman"),
    (re.compile(r"[(（](\d{1,3})[)）]\s*(?=\S)"), "paren-number", "number"),
    (re.compile(r"[(（]([一二三四五六七八九十]{1,3})[)）]\s*(?=\S)"), "paren-cjk", "cjk"),
    (re.compile(r"([一二三四五六七八九十]{1,3})([、．.])\s*(?=\S)"), "cjk", "cjk"),
    (re.compile(r"([壹貳參肆伍陸柒捌玖拾]{1,3})([、．.])\s*(?=\S)"), "cjk-formal", "cjk"),
    (re.compile(r"((?:\d{1,3}\.)+)(\d{1,3})\.?\s+(?=\S)"), "dotted", "number"),
]
# Text that is only a list marker (Word draws the marker and the item text as
# separate lines because of the tab between them).
_MARKER_ONLY = re.compile(
    rf"(?:[{_BULLET_GLYPHS}{_PUA_BULLETS}]|[-*+–—]|\d{{1,3}}[.)]|[a-zA-Z][.)]|[ivxlcdmIVXLCDM]{{1,6}}[.)]"
    r"|[(（](?:\d{1,3}|[a-zA-Z]|[ivxlcdmIVXLCDM]{1,6}|[一二三四五六七八九十]{1,3})[)）]"
    r"|[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾]{1,3}[、．])"
)
# Captions start with a numbered label ("Figure 3:", "Table 2.1", "Fig. 4 Revenue",
# "圖1", "表 2："); "Tablets …", "Fighting …" and "Table 3 shows …" do not.
_CAPTION_LABEL = re.compile(
    r"(?:(?i:figure|fig\.|table|tbl\.|tab\.|chart|graph|exhibit|plate|scheme|image|photo|diagram|illustration|map)"
    r"\s*(?:[A-Z]?\d+(?:[.\-–]\d+)*[a-z]?|[IVXLC]+|[A-Z])"
    r"(?=\s*$|\s*[.:：\-–—|)]|\s+(?:[A-Z(\"“'‘]|[^\x00-\x7f])))"
    r"|(?:附圖|附图|附表|圖|图|表)\s*[0-9０-９一二三四五六七八九十]+"
)
# Caption-shaped lead words for text directly attached to an image or table.
_CAPTION_KEYWORD = re.compile(r"(?i:sources?|notes?|credits?|photo)\b|資料來源|资料来源|來源|来源|註|说明|說明|備註|备注")
# A hyphen before these words is a suspended hyphen ("short- and long-term"), not a line-end join.
_SUSPENDED_HYPHEN_WORDS = {"and", "or", "to", "nor"}
_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:-[A-Za-zÀ-ÖØ-öø-ÿ]+)*")
# The plain word at the end of a line (not the last part of a hyphenated compound)
_TRAILING_WORD = re.compile(r"(?<![A-Za-zÀ-ÖØ-öø-ÿ-])[A-Za-zÀ-ÖØ-öø-ÿ]+$")
# A form label at the start of a CJK line ("提案單位："): not the wrapped end of the line before it.
_LABEL_START = re.compile(r"[^\s：:，,。.]{1,12}[：:]")
# Raised text that stays inline: ordinal suffixes after a number (Word raises them) and marks.
_INLINE_RAISED_MARKS = set("\u00ae\u2122\u2120\u00a9")
_SENTENCE_END = re.compile(r"[.!?。！？][\"'”’)\]」』]*$")
# The first thing a wrapped line may start with: a CJK character, a Latin word or number, with the
# opening brackets and quotes before it that cannot end the line above.
_LINE_START_TOKEN = re.compile(r"[「『（(【《〈“‘\"']*(?:[A-Za-z0-9]+|\S)")
# A line that ends a sentence or a lead-in: the next line starts afresh, even after a full line.
_LEAD_IN_END = re.compile(r"[.!?。！？:：;；][\"'”’)\]」』]*$")
# A heading number set a tab apart from its title ("1.2", "IV.", "Chapter 3", "第一章", "一、").
_HEADING_NUMBER = re.compile(
    r"(?:(?i:chapter|section|part|article|appendix)\s+)?(?:\d{1,3}(?:\.\d{1,3})*|[IVXLCDM]{1,6}|[A-Z])[.:)]?"
    r"|第\s*[一二三四五六七八九十百千零〇\d]+\s*[章節节條条篇部款項项編编]"
    r"|[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾]{1,3}[、．.]")
_CJK_CHAR = re.compile("[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")


@dataclass
class _Run:
    """A piece of a line with one style; ``text`` has ligatures expanded."""
    text: str
    bold: bool
    italic: bool
    superscript: bool


@dataclass
class _LineView:
    """One non-empty PDF line: its styled runs and the dominant style of its characters."""
    runs: List[_Run]
    text: str
    size: float
    bold: bool
    italic: bool
    color: int
    bbox: Optional[Tuple[float, float, float, float]]    # from the baseline, see _line_geometry
    baseline: Optional[float] = None
    solid_bold: bool = False                             # (nearly) every character is bold


@dataclass
class _BlockView:
    lines: List[_LineView]
    text: str
    size: float
    bold: bool
    italic: bool
    color: int
    bbox: Optional[Tuple[float, float, float, float]] = None


@dataclass
class _ListMarker:
    """A list marker at the start of a line. ``kind`` is ``glyph`` / ``ordered`` (always a list
    item), ``bullet`` (``-``/``*``/``+``/dashes, a list item next to other bullets) or ``enum``
    (letters, roman numerals, CJK or parenthesised numbers, a list item only in a sequence)."""
    kind: str
    text: str                        # the marker as written, without the spaces after it
    length: int                      # characters to drop from the line to get the item text
    sequence: Tuple[Tuple[str, int], ...] = ()   # (family, value) readings for "enum"
    delimiter: str = ""


def _weighted_median(weights: Dict[float, float]) -> float:
    total = sum(weights.values())
    if total <= 0:
        return 0.0
    running = 0.0
    for size in sorted(weights):
        running += weights[size]
        if running >= total / 2:
            return size
    return max(weights)


def _size_key(size: float) -> float:
    """Font size rounded to half a point, so 10.98pt and 11pt compare equal."""
    return round(size * 2) / 2


def _is_chromatic(color: int) -> bool:
    """True for clearly coloured text (blue headings), False for black and greys."""
    red, green, blue = (color >> 16) & 255, (color >> 8) & 255, color & 255
    return max(red, green, blue) - min(red, green, blue) >= 48


def _is_cjk(char: str) -> bool:
    return bool(char) and bool(_CJK_CHAR.match(char))


def _roman_value(text: str) -> int:
    values = [_ROMAN_VALUES[ch] for ch in text.lower()]
    total = 0
    for index, value in enumerate(values):
        total += -value if index + 1 < len(values) and value < values[index + 1] else value
    return total


def _cjk_value(text: str) -> int:
    if text in ("十", "拾"):
        return 10
    if text[0] in "十拾":
        return 10 + _CJK_DIGITS.get(text[1:], 0)
    if len(text) >= 2 and text[1] in "十拾":
        return _CJK_DIGITS.get(text[0], 0) * 10 + (_CJK_DIGITS.get(text[2:], 0) if len(text) > 2 else 0)
    return _CJK_DIGITS.get(text, 0)


def _parse_list_marker(text: str) -> Optional[_ListMarker]:
    """The list marker that starts ``text`` (see ``_ListMarker``), or None."""
    text = (text or "").lstrip()
    match = _LIST_GLYPH.match(text)
    if match:
        return _ListMarker("glyph", text[0], match.end())
    match = _LIST_ORDERED.match(text)
    if match and int(match.group(1)) > 0:
        return _ListMarker("ordered", match.group(1) + match.group(2), match.end(), delimiter=match.group(2))
    match = _LIST_CONTEXT_BULLET.match(text)
    if match:
        return _ListMarker("bullet", match.group(1), match.end())
    readings, found = [], None
    for pattern, family, numbering in _LIST_ENUMERATIONS:
        match = pattern.match(text)
        if not match:
            continue
        found = found or match
        if numbering == "alpha":
            value = ord(match.group(1).lower()) - ord("a") + 1
        elif numbering == "roman":
            value = _roman_value(match.group(1))
        elif numbering == "cjk":
            value = _cjk_value(match.group(1))
        elif family == "dotted":
            family, value = "dotted:" + match.group(1), int(match.group(2))
        else:
            value = int(match.group(1))
        delimiter = match.group(2) if family in ("lower", "upper", "lower-roman", "upper-roman", "cjk",
                                                   "cjk-formal") else ""
        readings.append((family + delimiter, value))
    if not readings:
        return None
    return _ListMarker("enum", text[:found.end()].rstrip(), found.end(), tuple(readings))


def _markers_in_sequence(first: Optional[_ListMarker], second: Optional[_ListMarker]) -> bool:
    """True when ``second`` can follow ``first`` in one list."""
    if first is None or second is None:
        return False
    if first.kind in ("glyph", "bullet") and second.kind in ("glyph", "bullet"):
        return first.kind == "glyph" or second.kind == "glyph" or first.text == second.text
    if first.kind == "enum" and second.kind == "enum":
        return any(family_a == family_b and value_b == value_a + 1
                   for family_a, value_a in first.sequence for family_b, value_b in second.sequence)
    return False


def _text_units(text: str) -> int:
    """Length of ``text`` for the running-text threshold: its non-space characters, a CJK
    character counting twice (it is about as wide as two Latin letters)."""
    return sum(1 for char in text if not char.isspace()) + len(_CJK_CHAR.findall(text))


def _follows(first: Optional[_ListMarker], second: Optional[_ListMarker]) -> bool:
    """True when ``second`` is the next item after ``first`` in one list: ``1.`` -> ``2.``,
    ``a)`` -> ``b)``, a bullet -> a bullet."""
    if first is None or second is None:
        return False
    if first.kind == "ordered" and second.kind == "ordered":
        return first.delimiter == second.delimiter and int(second.text[:-1]) == int(first.text[:-1]) + 1
    return _markers_in_sequence(first, second)


def _item_markers(markers: List[Optional[_ListMarker]], previous: str = "",
                  following: str = "") -> List[Optional[_ListMarker]]:
    """Of the markers that start the lines of a block (``_parse_list_marker``), those that make
    their line a list item. Bullets and numbers always do; ``-``/``*``/``+``/dashes only next to
    other bullets; letters, roman numerals and CJK/parenthesised numbers only in a sequence
    (a, b, c) with a neighbouring item, which may be ``previous`` (the item line before the
    block) or ``following`` (the line after it)."""
    items = [index for index, marker in enumerate(markers) if marker is not None]
    result: List[Optional[_ListMarker]] = [None] * len(markers)
    for position, index in enumerate(items):
        marker = markers[index]
        if marker.kind in ("glyph", "ordered"):
            result[index] = marker
            continue
        before = markers[items[position - 1]] if position > 0 else _parse_list_marker(previous)
        after = markers[items[position + 1]] if position + 1 < len(items) else _parse_list_marker(following)
        if _markers_in_sequence(before, marker) or _markers_in_sequence(marker, after):
            result[index] = marker
    return result


def _line_join(previous: str, following: str, wrap: int, cjk: str = "") -> Optional[str]:
    """How a line (``following``) attaches to the line before it (``previous``, both stripped
    text), given how it follows it on the page (``wrap``, see ``PDFLoader._line_wraps``):

    - ``"hyphen"``: the previous line ends with a hyphen after a letter or digit; the hyphen is
      kept and the lines are joined (``top-`` + ``down`` -> ``top-down``). Word and LibreOffice
      break lines after an existing hyphen and do not hyphenate by default, so a line-end hyphen
      is part of the word; ``PDFLoader._finalize_text_items`` removes it only when the document
      spells the joined word without it elsewhere. A soft hyphen (U+00AD) at a line end is
      handled the same way and written ``-``: some text layers read the printed hyphen so;
    - ``"direct"``: CJK text on both sides of the line break (CJK has no spaces), when ``cjk``
      says the break is inside a wrapped CJK paragraph (``"paragraph"``, see
      ``PDFLoader._cjk_joins``: stacked labels and items stay apart) or inside a heading
      (``"heading"``, except after a bare heading number such as ``第一章``);
    - None: the line break stays: before a line that starts with a list or outline marker or a
      CJK form label (``提案單位：``), at a hyphen before ``and``/``or`` (``short-`` + ``and
      long-term``) and at every other line end."""
    if not previous or not following or not wrap or _parse_list_marker(following) is not None:
        return None
    if previous[-1] in "-\u2010\u00ad" and len(previous) >= 2 and previous[-2].isalnum() and following[0].isalnum():
        return None if following.split(None, 1)[0].lower() in _SUSPENDED_HYPHEN_WORDS else "hyphen"
    if cjk and _is_cjk(previous[-1]) and _is_cjk(following[0]) and not _LABEL_START.match(following):
        if cjk == "paragraph" or (cjk == "heading" and not _HEADING_NUMBER.fullmatch(previous)):
            return "direct"
    return None


def _strip_runs(runs: List["_Run"]) -> List["_Run"]:
    """``runs`` without the whitespace at their start and end."""
    runs = [run for run in runs if run.text]
    while runs and not runs[0].text.strip():
        runs = runs[1:]
    while runs and not runs[-1].text.strip():
        runs = runs[:-1]
    if runs:
        runs[0] = replace(runs[0], text=runs[0].text.lstrip())
        runs[-1] = replace(runs[-1], text=runs[-1].text.rstrip())
    return runs


def _physical_lines(lines: List[List["_Run"]], wraps: List[int], hyphen_joins: Optional[Counter] = None,
                    cjk_heading: bool = False, cjk_joins: Optional[List[bool]] = None) -> List[List["_Run"]]:
    """The styled runs of a block's lines (one run list per line) after joining the line breaks
    that split a word (see ``_line_join``), one run list per output line. Emphasis is rendered
    afterwards, so a bold phrase that wraps stays one bold phrase. Each kept line-end hyphen
    between two plain words (not inside a longer compound such as ``self-`` +
    ``service-oriented``) is counted in ``hyphen_joins`` as ``(part before, part after)``,
    lowercased. CJK line breaks are joined inside a heading (``cjk_heading``) or where
    ``cjk_joins`` (per line, see ``PDFLoader._cjk_joins``) says so; see ``_line_join``."""
    physical: List[List[_Run]] = []
    previous = ""
    for index, line in enumerate(lines):
        runs = _strip_runs(line)
        text = "".join(run.text for run in runs)
        if not text:
            continue
        cjk = "heading" if cjk_heading else "paragraph" if cjk_joins and cjk_joins[index] else ""
        join = _line_join(previous, text, wraps[index] if index < len(wraps) else 2, cjk) if physical else None
        if join is None:
            physical.append(runs)
        else:
            if join == "hyphen" and previous.endswith("\u00ad"):
                last = physical[-1][-1]  # a soft hyphen read for the printed one: write the hyphen
                physical[-1][-1] = replace(last, text=last.text[:-1] + "-")
            if join == "hyphen" and hyphen_joins is not None:
                before = _TRAILING_WORD.search(previous[:-1])
                after = _WORD.match(text)
                if before and after and "-" not in after.group(0):
                    hyphen_joins[(before.group(0).lower(), after.group(0).lower())] += 1
            physical[-1] = physical[-1] + runs
        previous = text
    return physical


def _stays_inline(text: str, before: str) -> bool:
    """Raised text that is not a superscript to mark: an ordinal suffix after a number (``1st``,
    Word raises it) or a trademark/registered/copyright sign."""
    core = text.strip()
    if core and all(char in _INLINE_RAISED_MARKS for char in core):
        return True
    return core.lower() in ("st", "nd", "rd", "th") and before.rstrip()[-1:].isdigit()


def _starts_with_raised_number(line: "_LineView") -> bool:
    """True when the line starts with a raised number (a footnote number in Word's footnote
    area)."""
    runs = [run for run in line.runs if run.text.strip()]
    return bool(runs) and runs[0].superscript and runs[0].text.strip().isdigit()


def _is_punctuation(char: str) -> bool:
    """CommonMark (0.31) punctuation: a Unicode punctuation (P*) or symbol (S*) character, which
    covers all ASCII punctuation."""
    return bool(char) and unicodedata.category(char)[0] in "PS"


def _emphasis_fits(before: str, core: str, after: str) -> bool:
    """True when emphasis markers around ``core`` (``before`` / ``after``: the characters next to
    it, "" at a line edge) keep Latin words whole (no ``A**I**``) and CommonMark can open and
    close them: a marker next to punctuation inside needs a space, punctuation or the line edge
    outside, so ``的**資料治理、**流程`` stays unmarked and ``的**資料治理、流程。**`` is marked."""
    for outside, inside in ((before, core[:1]), (after, core[-1:])):
        if outside.isalnum() and not _is_cjk(outside):
            return False
        if outside and not outside.isspace() and not _is_punctuation(outside) and _is_punctuation(inside):
            return False
    return True


def _sampled_pages(count: int) -> List[int]:
    """At most ``_PROFILE_MAX_PAGES`` page indexes: the first 10 and an even spread of the rest."""
    if count <= _PROFILE_MAX_PAGES:
        return list(range(count))
    step = (count - 10) / (_PROFILE_MAX_PAGES - 10)
    return list(range(10)) + sorted({10 + int(index * step) for index in range(_PROFILE_MAX_PAGES - 10)})


def _wrap_inline(text: str, marker: str) -> str:
    """Wrap ``text`` in an inline marker, keeping surrounding spaces outside it."""
    core = text.strip()
    if not core:
        return text
    start = len(text) - len(text.lstrip())
    return f"{text[:start]}{marker}{core}{marker}{text[start + len(core):]}"


def _render_runs(runs: List[_Run], emphasis: bool = True) -> str:
    """Inline Markdown for the styled runs of one line: escaped text (the whole line is escaped
    at once, so a ``<`` in one run sees the letter in the next), ``^x^`` superscripts and, when
    ``emphasis`` is set, ``**bold**`` / ``*italic*`` around exactly the styled runs.

    A styled run is emphasised only where the markers keep Latin words whole and render
    (``_emphasis_fits``): markers inside a word would split it for lexical retrieval (``A**I**``),
    and CommonMark cannot close emphasis between CJK characters after punctuation
    (``的**資料治理、**流程``). Such runs keep their text without markers."""
    escaped = escape_inline_pieces("".join(run.text for run in runs))
    groups: List[List[Any]] = []   # [markdown text, (bold, italic)]
    offset = 0
    for run in runs:
        text = "".join(escaped[offset:offset + len(run.text)])
        offset += len(run.text)
        if run.superscript:
            text = _wrap_inline(text.replace("^", "\\^"), "^")
        style = (run.bold, run.italic) if emphasis else (False, False)
        if groups and (style == groups[-1][1] or not run.text.strip()):
            groups[-1][0] += text
        else:
            groups.append([text, style])
    pieces: List[str] = []
    for index, (text, (bold, italic)) in enumerate(groups):
        marker = "***" if bold and italic else "**" if bold else "*" if italic else ""
        if marker and text.strip():
            before = text[:1] if text[:1].isspace() else (groups[index - 1][0][-1:] if index else "")
            after = text[-1:] if text[-1:].isspace() else (groups[index + 1][0][:1] if index + 1 < len(groups) else "")
            if _emphasis_fits(before, text.strip(), after):
                text = _wrap_inline(text, marker)
        pieces.append(text)
    return "".join(pieces)


def _drop_prefix(runs: List[_Run], count: int) -> List[_Run]:
    """``runs`` without their first ``count`` characters."""
    kept: List[_Run] = []
    for run in runs:
        if count >= len(run.text):
            count -= len(run.text)
            continue
        kept.append(_Run(run.text[count:], run.bold, run.italic, run.superscript) if count else run)
        count = 0
    return kept


def _outline_depth(text: str) -> int:
    """Depth of a leading decimal outline number: ``1`` -> 1, ``1.2`` -> 2, ``1.2.3.`` -> 3."""
    match = re.match(r"(\d{1,3}(?:\.\d{1,3})*)\.?(?=\s|$)", text)
    return match.group(1).count(".") + 1 if match else 0


class PDFLoader:
    """PDF loader that extracts content in reading order and exports to various formats"""

    def __init__(self, pdf_path: Union[str, Path], ocr=None, table_style: Union[str, TableStyle] = None,
                 legibility_judge=None, boilerplate_judge: Optional[BoilerplateJudge] = None):
        """Open ``pdf_path``.

        Args:
            pdf_path: PDF file to load.
            ocr: Optional OCR provider for images and image-dominant pages.
            table_style: Output style for complex tables (see ``TableStyle``).
            legibility_judge: Optional ``judge(page_text) -> Optional[float]`` hook of the
                text-layer quality gate; see :func:`doc2mark.core.strategy.judge_text_layer`
                for its contract.
            boilerplate_judge: Optional ``boilerplate_judge(line_text, context) -> Optional[float]``.
                Running headers/footers are removed only on strong evidence, and only a
                bare page number or a line repeating a title or heading loses every copy
                (see ``_detect_page_chrome``). The lines the rule keeps although they repeat
                in the top/bottom band are *ambiguous*. When a judge is given, it is asked
                about them and about nothing else, once per distinct line (per group of
                numbered lines): ``line_text`` is the line as extracted (a group's first
                line); ``context`` is a dict with ``zone`` ("header"/"footer"), ``pages``
                (1-based pages where the rule kept the line), ``repeated_on`` (1-based pages
                where it repeats, including copies already removed), ``page_count``,
                ``font_size``, ``body_font_size`` and ``reason``, why the rule kept it:

                * "numbered_label": a number labelled with anything but a page word that
                  follows the page order on most pages ("3 | ACME Corp", "Lesson · 3"),
                  which may be a page number or per-page content; asked once per group.
                  At or above 0.5 the group is handled like a running header with a page
                  number: its copies at the page edge are chrome except the first, which
                  stays; copies the rule cannot take off the page edge stay, as plain text.
                * "few_pages": it repeats on too few pages.
                * "attached_to_content": no clear gap separates it from the page's text,
                  or other text sits between it and the page edge.

                The judge returns the probability (0..1) that the line is page chrome rather
                than content: at or above 0.5 every kept copy of the line but its first is
                treated as chrome (typed text:header / text:footer, left out of the Markdown),
                except as said for "numbered_label"; below 0.5, None, an invalid value or an
                exception keeps them all. The first copy of a repeated line always stays (as
                plain text unless it is heading-sized; or, when the text is already in the page
                body at or before it, as on a cover, that copy is the one left): the judge can
                thin a line out to one copy, never remove it. A running header's first copy, left alone by the rule (which
                removed the other copies), is not asked about. Without a judge, ambiguous lines
                are kept.
        """
        self.pdf_path = Path(pdf_path)
        self.doc = None
        self.ocr = ocr  # Store the OCR instance
        self.boilerplate_judge = boilerplate_judge
        self._first_text_page_num = None
        self._legibility_judge = legibility_judge
        self._page_measures: Dict[int, "pdf_routing.PageMeasure"] = {}
        self._page_routes: Dict[int, Tuple[str, str]] = {}
        self._rendered_pages: set = set()  # pages whose content is the OCR of their render
        self._unread_pages: set = set()    # pages showing content whose render OCR returned nothing
        # Pictures the text route OCRs, per page: (pictures, placements left out by reason); see _page_pictures.
        self._pictures: Dict[int, Tuple[List["pdf_images.Picture"], Dict[str, int]]] = {}
        self._xref_content: Dict[int, str] = {}  # content class of each image XObject (see pdf_images.classify)
        self._placements: Dict[int, List["pdf_images.Placement"]] = {}  # image placements per page
        # (page, position_y, OCR item content, placement rect) of every picture item emitted with its OCR text,
        # for _detect_repeated_pictures.
        self._picture_records: List[Tuple[int, float, str, Tuple[float, float, float, float]]] = []
        self._judged_pages: set = set()    # pages the legibility judge was asked about
        self._chrome_regions: Optional[Dict[int, List[Tuple[Tuple[float, float, float, float], str, bool]]]] = None
        # OCR options of the conversion under way: the page text depends on them (see _text_page).
        self._text_options: Tuple[bool, Optional[Dict[tuple, str]]] = (False, None)

        # Neighbor-page PDF context (off by default). Resolve the context tier
        # once from the OCR instance's config (NOT self.config, which does not
        # exist). 0=off, 1=page-renders only, 2=renders + embedded images.
        self._window_pdf_cache: "OrderedDict[int, Optional[str]]" = OrderedDict()
        cfg = getattr(self.ocr, "config", None)
        self._context_tier = int(getattr(cfg, "context_pages", 0) or 0) if (self.ocr and cfg) else 0
        self._doc_strategy: Optional[str] = None  # lazy: "image" | "text" (document-level route)

        # Set table output style
        if table_style is None:
            self.table_style = TableStyle.default()
        elif isinstance(table_style, str):
            self.table_style = TableStyle(table_style)
        else:
            self.table_style = table_style

        # Log OCR configuration if available
        if self.ocr:
            logger.info(f"📷 OCR configured for PDFLoader: {type(self.ocr).__name__}")
            if hasattr(self.ocr, 'config') and self.ocr.config and self.ocr.config.language:
                logger.info(f"🌍 OCR Language setting: {self.ocr.config.language}")

        self._open_document()
        self._copies = pdf_routing.PageCopies(self.doc)  # editable page copies for the routing checks
        self._record_rotated_crop_boxes()

    def _open_document(self):
        """Open PDF document with error handling"""
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {self.pdf_path}")

        try:
            self.doc = pymupdf.open(self.pdf_path)

            # Log PDF configuration
            logger.info("=" * 60)
            logger.info(f"PDF Configuration for: {self.pdf_path.name}")
            logger.info("=" * 60)
            logger.info(f"File path: {self.pdf_path}")
            logger.info(f"File size: {self.pdf_path.stat().st_size / (1024 * 1024):.2f} MB")
            logger.info(f"Total pages: {len(self.doc)}")

            # Count total images in the PDF
            total_images = 0
            images_per_page = []
            for page_num in range(len(self.doc)):
                page = self.doc.load_page(page_num)
                images = page.get_images(full=True)
                num_images = len(images)
                total_images += num_images
                if num_images > 0:
                    images_per_page.append(f"Page {page_num + 1}: {num_images} images")

            logger.info(f"Total images: {total_images}")
            if images_per_page and len(images_per_page) <= 10:
                # Show per-page breakdown if not too many pages with images
                for page_info in images_per_page:
                    logger.info(f"  {page_info}")
            elif images_per_page:
                logger.info(f"  Images found on {len(images_per_page)} pages")

            # Log metadata if available
            metadata = self.doc.metadata
            if metadata:
                logger.info("PDF Metadata:")
                for key, value in metadata.items():
                    if value:
                        logger.info(f"  {key}: {value}")

            # Log PDF version and encryption status
            # Try to get PDF version from various possible attributes
            pdf_version = "Unknown"
            if hasattr(self.doc, 'pdf_version'):
                pdf_version = self.doc.pdf_version
            elif hasattr(self.doc, 'version'):
                pdf_version = self.doc.version
            elif metadata and 'format' in metadata:
                pdf_version = metadata['format']

            logger.info(f"PDF version: {pdf_version}")

            # Check encryption status
            is_encrypted = False
            if hasattr(self.doc, 'is_encrypted'):
                is_encrypted = self.doc.is_encrypted
            elif hasattr(self.doc, 'isEncrypted'):
                is_encrypted = self.doc.isEncrypted
            elif hasattr(self.doc, 'needs_pass'):
                is_encrypted = self.doc.needs_pass

            logger.info(f"Encrypted: {is_encrypted}")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"Failed to open PDF: {e}")
            raise

    def _extract_image_bytes(self, xref: int) -> Optional[Tuple[bytes, str, str]]:
        """Extract image bytes with Pixmap fallback for problematic formats (e.g. JBIG2).

        Args:
            xref: Image cross-reference number

        Returns:
            Tuple of (image_bytes, extension, mime_type) or None if extraction fails
        """
        # Primary path: extract_image (fast, preserves original format)
        try:
            base_image = self.doc.extract_image(xref)
            if base_image and base_image.get("image"):
                image_bytes = base_image["image"]
                ext = base_image.get("ext", "png")
                fmt = detect_image_format(image_bytes)
                mime = get_mime_type(fmt)
                return image_bytes, ext, mime
        except Exception as e:
            logger.debug(f"extract_image failed for xref {xref}: {e}")

        # Fallback: render via Pixmap (handles JBIG2, JPEG2000, etc.)
        try:
            pix = pymupdf.Pixmap(self.doc, xref)
            if pix.alpha:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            img_bytes = pix.tobytes("png")
            logger.info(f"Used Pixmap fallback for xref {xref} ({len(img_bytes)} bytes)")
            return img_bytes, "png", "image/png"
        except Exception as e:
            logger.warning(f"Pixmap fallback also failed for xref {xref}: {e}")

        return None

    def convert_to_json(self,
                        extract_images: bool = True,
                        ocr_images: bool = False,
                        show_progress: bool = True) -> Dict[str, Any]:
        """
        Convert PDF to simplified JSON format with content in reading order
        
        Args:
            extract_images: Whether to extract images as base64
            ocr_images: Whether to use OCR to convert images to text descriptions
                (implies image extraction; needs an OCR instance)
            show_progress: Whether to show progress messages

        Returns:
            Simplified JSON with content array containing:
            - text:title - Main document title
            - text:section - Section headers (larger fonts)
            - text:normal - Regular paragraph text
            - text:list - Bullet points or numbered lists
            - text:caption - Figure/table captions (smaller text near images/tables)
            - text:image_description - OCR-generated image descriptions (when ocr_images=True)
            - table - Tables with complex structure support:
                * Simple tables: Markdown format with span annotations (*[2x3]* for merged cells)
                * Complex tables: HTML format preserving rowspan/colspan attributes
                * Line breaks in cells preserved using <br> tags
                * Automatic detection and labeling of merged cells
            - image - Base64-encoded images (when ocr_images=False)
            plus, when applicable, ``ocr_routing``, ``text_layer_quality`` and
            ``hidden_text`` (see _record_routing).
        """
        # Initialize document structure
        document = {
            "filename": self.pdf_path.name,
            "pages": len(self.doc),
            "content": []  # Simple array of content items
        }

        self._rendered_pages = set()
        self._unread_pages = set()
        self._pictures = {}
        self._picture_records = []

        # OCR needs the images: asking for OCR implies extracting them for it.
        if ocr_images and self.ocr is None:
            logger.warning(f"{self.pdf_path.name}: ocr_images=True but no OCR provider is configured; "
                           f"images are not OCR'd")
            ocr_images = False
        if ocr_images and not extract_images:
            logger.info("ocr_images=True implies image extraction for OCR")
            extract_images = True

        # If OCR is requested, collect all images first for batch processing
        ocr_results_map = {}
        # The page text depends on this run's OCR (see _text_source): the document-wide passes
        # that read every page through _text_page run afresh for it.
        self._text_options = (ocr_images, ocr_results_map)
        self._chrome_regions = None
        self._first_text_page_num = None
        self._body_size = None
        # Line-end hyphens kept while joining lines, see _finalize_text_items
        self._hyphen_joins = Counter()
        if extract_images and ocr_images:
            self._window_pdf_cache = OrderedDict()
            document["ocr_images"] = self._ocr_document(ocr_results_map, show_progress=show_progress)

        # Process each page
        for page_num in range(len(self.doc)):
            if show_progress:
                logger.info(f"Processing page {page_num + 1}/{len(self.doc)}")

            page_content = self._process_page(
                page_num,
                extract_images=extract_images,
                ocr_images=ocr_images,
                ocr_results_map=ocr_results_map  # Pass pre-computed OCR results
            )

            # Add page content to document
            document["content"].extend(page_content)

        if self._unread_pages and "ocr_images" in document:
            document["ocr_images"]["unread_pages"] = sorted(self._unread_pages)

        # The title, chosen among all headings before repeated tables are retyped
        self._choose_title(document["content"])
        # Post-process: detect and tag repeated headers/footers
        self._detect_repeated_content(document)
        self._detect_repeated_pictures(document)
        # Document-wide decisions on the text: line-end hyphens and heading levels
        self._finalize_text_items(document)

        self._record_routing(document, ocr_active=bool(ocr_images))

        return document

    def _ocr_batch(self, batch: List[Dict[str, Any]], ocr_results_map: Dict[tuple, str], *,
                   synthesis_markdown: bool, show_progress: bool) -> None:
        """OCR one batch of images (see _ocr_document) into ``ocr_results_map``: each image's text
        goes to every ``(page, key)`` of its ``targets``.

        A failed batch leaves its images without results: they become lightweight
        placeholders (see _extract_images_simple) while the deterministic text/table
        layer is still emitted. Never fall back to base64 extraction: dumping
        megabytes of base64 image data into a text/RAG output is useless and harmful.
        """
        try:
            # Prepare image data for batch processing
            image_data_list = [info["image"] for info in batch]
            # Per-image neighbor-page PDF context (aligned positionally
            # with image_data_list). All None when the feature is off.
            context_pdfs = [info.get("context_pdf_b64") for info in batch]

            # Pass language configuration if available
            kwargs = {}
            if hasattr(self.ocr, 'config') and self.ocr.config and self.ocr.config.language:
                kwargs['language'] = self.ocr.config.language
                logger.info(f"🌍 Passing language configuration to OCR: {self.ocr.config.language}")

            # Only inject context when at least one image carries it, so
            # the off-default path stays byte-identical (cache keys + call).
            if any(context_pdfs):
                kwargs['context_pdfs'] = context_pdfs
            if synthesis_markdown:
                kwargs['synthesis_markdown'] = True

            # Always use batch processing for efficiency
            logger.info(f"🚀 Using batch OCR processing for {len(image_data_list)} images")
            ocr_results = self.ocr.batch_process_images(image_data_list, **kwargs)
            # Tell the loader's OCR issue record which page each image is on.
            label_issues = getattr(self.ocr, "label_last_batch", None)
            if callable(label_issues):
                label_issues([{"page": info["page_num"] + 1} for info in batch])

            # Map results back to image locations. A failed image (flagged by the provider: a
            # timeout, a rate limit) was not read: it gets no answer, so it becomes a placeholder
            # and counts as failed; an answer with no text is an answer.
            for info, result in zip(batch, ocr_results):
                if (getattr(result, "metadata", None) or {}).get("failed"):
                    continue
                text = result.text if hasattr(result, 'text') else str(result)
                for target in info["targets"]:
                    ocr_results_map[target] = text

            if show_progress:
                logger.info(f"Successfully processed {len(image_data_list)} images with configured OCR")
        except Exception as e:
            logger.error(f"Batch OCR processing failed: {e}; emitting image placeholders")

    @staticmethod
    def _choose_title(content: List[Dict[str, Any]]) -> None:
        """Keep one ``text:title``: the largest title candidate (a heading of the first page with
        text, near its largest font size, see ``_is_title_candidate``) when no other heading of
        the document is as large, the others become sections. Running header and footer lines
        are taken out before conversion (``_detect_page_chrome``), but the heading-sized first
        copy of a running header stays as content: a candidate whose text also stands alone on
        another page yields to one whose text does not, and such repeated headings do not
        compete; when every candidate repeats (a title that is also the running header,
        ``INVOICE`` on every page), the largest one is the title, so its text is kept once."""
        pages: Dict[str, set] = defaultdict(set)
        for item in content:
            if item.get("type", "").startswith("text:"):
                pages[" ".join((item.get("content") or "").split())].add(item.get("page"))

        def repeated(item: Dict[str, Any]) -> bool:
            return len(pages[" ".join((item.get("content") or "").split())]) > 1

        headings = [(item, item["_heading"][0]) for item in content
                    if item.get("_heading") is not None and item.get("type") in ("text:title", "text:section")]
        rivals = [(item, size) for item, size in headings if not repeated(item)]
        candidates = [(item, size) for item, size in headings if item["type"] == "text:title"]
        pool = [candidate for candidate in candidates if not repeated(candidate[0])] or candidates
        title = None
        if pool:
            largest, size = max(pool, key=lambda candidate: candidate[1])
            if not any(other_size >= size - 0.25 for other, other_size in rivals if other is not largest):
                title = largest
        for item, _ in candidates:
            if item is not title:
                item["type"] = "text:section"

    def _finalize_text_items(self, document: Dict[str, Any]) -> None:
        """Decisions on the text items that need the whole document, made once every page is
        converted. Modifies document["content"] in place:

        - a line-end hyphen kept while joining two lines (``top-`` + ``down``, see ``_line_join``)
          is removed only when the document spells the joined word without it elsewhere and
          never with it outside those line ends (``invest-`` + ``ment`` next to ``investment``);
        - heading levels: 1 for the title (``_choose_title``); sections are ranked by the heading
          font sizes of the document and decimal outline depths (``1.2`` is deeper than ``1``),
          and each is one level below the nearest open section that ranks above it, so no level
          is skipped and sections of one rank under the same parent share a level.
        Only emitted headings count, so text in table regions (bold header cells) never takes a
        level."""
        content = document.get("content", [])
        self._remove_line_end_hyphens(content)
        self._assign_heading_levels(content)

    def _remove_line_end_hyphens(self, content: List[Dict[str, Any]]) -> None:
        """Remove the line-end hyphens the document shows to be hyphenation (see
        ``_finalize_text_items``)."""
        joins = getattr(self, "_hyphen_joins", None)
        if not joins:
            return
        counts = Counter(word.lower() for item in content
                         if item.get("type", "").startswith("text:") or item.get("type") == "table"
                         for word in _WORD.findall(item.get("content") or ""))
        removable = {f"{before}-{after}": len(before) for (before, after), joined in joins.items()
                     if counts[before + after] > 0 and counts[f"{before}-{after}"] == joined}
        if not removable:
            return

        def dehyphenate(match):
            cut_at = removable.get(match.group(0).lower())
            word = match.group(0)
            return word if cut_at is None else word[:cut_at] + word[cut_at + 1:]

        for item in content:
            if item.get("type", "").startswith("text:") and "-" in (item.get("content") or ""):
                item["content"] = _WORD.sub(dehyphenate, item["content"])

    @staticmethod
    def _assign_heading_levels(content: List[Dict[str, Any]]) -> None:
        """Set ``level`` on the headings (see ``_finalize_text_items``) and remove the private
        ``_heading`` data from every item."""
        headings = []
        for item in content:
            heading = item.pop("_heading", None)
            if heading is not None and item.get("type") in ("text:title", "text:section"):
                headings.append((item, heading[0], heading[1]))
        sections = [(size, depth) for item, size, depth in headings if item["type"] == "text:section"]
        tiers: List[float] = []
        for size in sorted({size for size, _ in sections}, reverse=True):
            if not tiers or tiers[-1] - size > 0.25:
                tiers.append(size)

        def rank(size: float, depth: int) -> int:
            return max(2 + sum(1 for tier in tiers if tier > size + 0.25), 1 + depth)

        # A section is one level below the nearest open section that ranks above it.
        open_ranks: List[int] = []
        for item, size, depth in headings:
            if item["type"] == "text:title":
                item["level"] = 1
                open_ranks = []
                continue
            own = rank(size, depth)
            while open_ranks and open_ranks[-1] >= own:
                open_ranks.pop()
            open_ranks.append(own)
            item["level"] = min(1 + len(open_ranks), 6)

    def _detect_repeated_content(self, document: Dict[str, Any]) -> None:
        """Retype repeated page furniture that only exists as whole items.

        Running header/footer *text lines* are recognised line by line before
        conversion (``_detect_page_chrome``) and arrive here already typed
        ``text:header`` / ``text:footer``. This pass handles tables: a table repeated
        unchanged at the same place in the top or bottom band of most pages (a
        letterhead, a logo box) keeps its first copy, and the later copies are retyped
        ``text:header`` / ``text:footer``, which ``pdf_to_markdown`` leaves out. OCR
        text of images and page renders (``text:image_description``, including the
        ``[image: OCR unavailable]`` placeholder) is page content or a failure marker
        and is never retyped. Items are retyped, never removed.

        Modifies document["content"] in place.
        """
        total_pages = document.get("pages", 0)
        content = document.get("content", [])
        if total_pages < _CHROME_MIN_PAGES or not content:
            return

        heights: Dict[int, float] = {}
        tables: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        for item in content:
            page, pos_y = item.get("page"), item.get("position_y")
            if item.get("type") != "table" or page is None or pos_y is None:
                continue
            if page not in heights:
                heights[page] = self._page_height(page - 1)
            if pos_y < _CHROME_BAND * heights[page]:
                zone = "header"
            elif pos_y > (1 - _CHROME_BAND) * heights[page]:
                zone = "footer"
            else:
                continue
            tables[(zone, " ".join(item.get("content", "").split()))].append(item)

        for (zone, _), items in tables.items():
            first = min(items, key=lambda item: (item["page"], item["position_y"]))
            copies = [item for item in items
                      if abs(item["position_y"] - first["position_y"]) <= _CHROME_SLOT_TOLERANCE]
            pages = {item["page"] for item in copies}
            if len(pages) < _CHROME_MIN_PAGES or len(pages) <= _CHROME_MIN_SHARE * total_pages:
                continue
            for item in copies:
                if item is not first:
                    item["type"] = f"text:{zone}"

    def _detect_repeated_pictures(self, document: Dict[str, Any]) -> None:
        """Retype pictures repeated as page furniture: the same OCR text at (nearly) the same place
        (every edge within _CHROME_SLOT_TOLERANCE) on at least _CHROME_MIN_PAGES pages and on more
        than _CHROME_MIN_SHARE of the document's pages -- a letterhead logo, a slide template's
        wordmark -- is kept once, like the first copy of a running header: the first copy stays,
        the later ones are typed ``text:header`` (top half of the page) or ``text:footer``, which
        ``pdf_to_markdown`` leaves out. A picture shown on fewer pages, or at moving places, is
        content and stays everywhere. Items are retyped, never removed; placeholders and page
        renders are never retyped.

        Modifies document["content"] in place.
        """
        records, self._picture_records = self._picture_records, []
        total_pages = document.get("pages", 0)
        if total_pages < _CHROME_MIN_PAGES or not records:
            return
        by_text: Dict[str, List[Tuple[int, float, Tuple[float, float, float, float]]]] = defaultdict(list)
        for page, position_y, content, rect in records:
            by_text[content].append((page, position_y, rect))
        furniture: Dict[Tuple[int, float, str], List[Tuple[float, float, float, float]]] = defaultdict(list)
        for content, places in by_text.items():
            places.sort()
            while places:
                first = places[0]
                slot = [place for place in places
                        if all(abs(a - b) <= _CHROME_SLOT_TOLERANCE for a, b in zip(place[2], first[2]))]
                places = [place for place in places if place not in slot]
                pages = {place[0] for place in slot}
                if len(pages) < _CHROME_MIN_PAGES or len(pages) <= _CHROME_MIN_SHARE * total_pages:
                    continue
                for page, position_y, rect in slot[1:]:
                    furniture[(page, position_y, content)].append(rect)
        for item in document.get("content", []):
            if item.get("type") != "text:image_description":
                continue
            rects = furniture.get((item.get("page"), item.get("position_y"), item.get("content")))
            if not rects:
                continue
            rect = rects.pop()
            middle = (rect[1] + rect[3]) / 2
            item["type"] = "text:header" if middle < self._page_height(item["page"] - 1) / 2 else "text:footer"

    def _record_rotated_crop_boxes(self) -> None:
        """Record, before any table detection runs, how to read the tables of rotated pages.

        PyMuPDF's find_tables() derotates a /Rotate page by rewriting it, reports table boxes
        as the page is displayed, and deletes the page's own /CropBox. On a cropped page (a
        CropBox that is not its MediaBox) it derotates with the cropped size but lays the
        page out uncropped, so its boxes come back shifted. Per rotated page:

        * ``_rotated_crop_boxes``: its own raw /CropBox entry. ``_uncrop_for_tables`` takes it
          off before the page's tables are found, so find_tables() and everything else that
          reads the tables sees the whole MediaBox in one frame; ``_restore_cropbox`` puts it
          back right after.
        * ``_crop_offsets``: where the visible area (the CropBox within the MediaBox) starts in
          the MediaBox, which brings those uncropped boxes into the frame of get_text().
        * ``_rotated_cropped_pages``: pages whose boxes cannot be brought back (a CropBox
          inherited from /Pages, which stays while find_tables() runs, or page boxes that
          cannot be read). They get no table suppression: the table's text may repeat, but
          no text is dropped.
        """
        self._rotated_crop_boxes: Dict[int, str] = {}
        self._crop_offsets: Dict[int, Tuple[float, float]] = {}
        self._rotated_cropped_pages: set = set()
        self._table_frames: Dict[int, Any] = {}
        for number in range(len(self.doc)):
            try:
                page = self.doc.load_page(number)
                if not page.rotation:
                    continue
                # Recorded before anything else is read: a page that fails below still gets its
                # CropBox back after find_tables(), only no table suppression.
                kind, own = self.doc.xref_get_key(page.xref, "CropBox")
                if kind in ("array", "xref"):
                    self._rotated_crop_boxes[number] = own
                media = self._box_numbers(self._inherited_page_entry(page.xref, "MediaBox"))
                if media is None:
                    raise ValueError("no MediaBox")
                kind, parent = self.doc.xref_get_key(page.xref, "Parent")
                inherited = self._inherited_page_entry(int(parent.split()[0]), "CropBox") if kind == "xref" else None
                if inherited is not None and self._box_numbers(inherited) != media:
                    self._rotated_cropped_pages.add(number)
                    continue
                if number not in self._rotated_crop_boxes:
                    continue
                crop = self._box_numbers(own)
                visible = (max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3]))
                if visible[0] >= visible[2] or visible[1] >= visible[3]:
                    raise ValueError(f"CropBox {own} outside the MediaBox")
                self._crop_offsets[number] = (visible[0] - media[0], media[3] - visible[3])
            except Exception as e:
                logger.debug(f"Could not read the page boxes of page {number + 1}, its tables are not suppressed: {e}")
                self._rotated_cropped_pages.add(number)

    def _inherited_page_entry(self, xref: int, key: str) -> Optional[str]:
        """A page's /CropBox or /MediaBox entry, its own or inherited through /Parent."""
        for _ in range(64):  # the page tree is shallow; this only guards against cycles
            kind, value = self.doc.xref_get_key(xref, key)
            if kind in ("array", "xref"):
                return value
            kind, parent = self.doc.xref_get_key(xref, "Parent")
            if kind != "xref":
                return None
            xref = int(parent.split()[0])
        return None

    def _box_numbers(self, source: Optional[str]) -> Optional[Tuple[float, float, float, float]]:
        """The rectangle of a PDF box entry (``[0 0 595 842]``, an indirect ``12 0 R``, or an
        array holding indirect numbers) with its corners in order, as MuPDF reads it; None
        without an entry. Raises ValueError when the entry is not four numbers."""
        if not source:
            return None
        if not source.lstrip().startswith("["):
            source = self.doc.xref_object(int(source.split()[0]), compressed=True)
        tokens = source.strip().strip("[]").split()
        values = []
        while tokens:
            if len(tokens) >= 3 and tokens[2] == "R":
                values.append(float(self.doc.xref_object(int(tokens[0]), compressed=True)))
                tokens = tokens[3:]
            else:
                values.append(float(tokens.pop(0)))
        if len(values) != 4:
            raise ValueError(f"not a rectangle: {source!r}")
        x0, y0, x1, y1 = values
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def _page_height(self, page_index: int) -> float:
        """Displayed height of a page (``/Rotate`` applied); 800 when it cannot be read."""
        try:
            return self.doc.load_page(page_index).rect.height or 800.0
        except Exception:
            return 800.0

    def _page_chrome_regions(self, page_index: int) -> List[Tuple[Tuple[float, float, float, float], str, bool]]:
        """Running header/footer lines of a page as ``[(line bbox, "header" | "footer", kept), ...]``.

        ``kept`` marks a line that stays as content, emitted as plain text: the first copy of a
        running header, a numbered label, a page number the rule leaves. The whole document is
        analysed once, on first use (``_detect_page_chrome``).
        """
        if getattr(self, "_chrome_regions", None) is None:
            try:
                self._chrome_regions = self._detect_page_chrome()
            except Exception as e:
                logger.warning(f"Running header/footer detection failed, keeping every line: {e}")
                self._chrome_regions = {}
        return self._chrome_regions.get(page_index, [])

    def _detect_page_chrome(self) -> Dict[int, List[Tuple[Tuple[float, float, float, float], str, bool]]]:
        """Find running headers, footers and page numbers, line by line, in the whole document.

        A line is page chrome only on strong evidence. It sits in the top or bottom
        ``_CHROME_BAND`` of the displayed page, at a height ("slot") where lines with that
        evidence appear on at least ``_CHROME_MIN_PAGES`` pages and on more than half of all
        pages, and there it

        * repeats: the same normalised text (Unicode width and whitespace folded, case
          ignored, with at least one letter) on ``_CHROME_MIN_PAGES`` pages, or on a page at
          most ``_CHROME_RUN_DISTANCE`` away (per-chapter running headers). Heading-sized
          text must repeat on more than half of all pages, so the titles of consecutive
          slides are not taken for running headers; or
        * is a page number: the same text once page-number-shaped tokens are masked
          (``_page_number_template``), with one number per page that follows the page order
          on ``_CHROME_MIN_PAGES`` pages (``_page_number_lines``), and nothing around the
          number but page words (``3``, ``- 3 -``, ``Page 3 of 12``, ``ACME | Page 3``,
          ``第 3 頁``). A bare page number printed elsewhere on a page before or after the
          numbered ones (a cover or contents page with its own header or footer) continues that
          numbering when it has the same printed form and sits about as close to its page edge.

        It must also be at the page edge: every row between it and the edge is chrome too,
        and a clear gap separates the chrome from the page's content, which is not a table
        continuing under a repeated header row (``_peel_chrome_rows``).

        A number labelled with anything but a page word that follows the page order
        (``3 | ACME Corp``, ``Lesson · 3``, ``Ticket | 3``) may be a page number or per-page
        content, so the rule keeps it on every page, as plain text (a page number beside it in
        the same row still goes); the optional ``boilerplate_judge`` may say it is chrome. A
        bare number that follows the page order (``1124``, ``2019``) cannot be told from a page
        number and is treated as one.

        Verbatim first: a chrome line loses *every* copy only when it is a bare page number
        or when its text is a title or heading of the document (heading-sized content on or
        before the page of its first copy: a title that is also the running header; in the
        middle of a page it is looked for only on that page and the ``_CHROME_RUN_DISTANCE``
        pages before it).
        Otherwise its first copy stays as content, so a statement title, a unit note or a
        disclaimer repeated as page furniture is not lost; only the later copies are chrome.

        Lines that repeat but miss the evidence bar are ambiguous and kept, unless the optional
        ``boilerplate_judge`` says they are chrome: then their later copies go and the first
        stays, as plain text. The judge is never asked about a first copy the rule keeps.

        Returns ``{page index: [(line bbox in get_text() space, "header" | "footer", kept), ...]}``
        where ``kept`` marks a line that stays as content, emitted as plain text.
        """
        page_count = len(self.doc) if self.doc is not None else 0
        if page_count < 2:
            return {}

        pages_rows: List[Tuple[List[List[_PageLine]], List[List[_PageLine]]]] = []
        band_lines: List[_PageLine] = []
        body_lines: List[_PageLine] = []
        sizes: Dict[float, int] = defaultdict(int)
        for page_index in range(page_count):
            with self._text_page(self.doc.load_page(page_index)) as page:
                lines = self._page_lines(page, page_index)
            for line in lines:
                sizes[round(line.size, 1)] += len(line.text)
                if line.zone:
                    line.norm = self._normalized(line.text)
                    line.template, line.numbers, line.totals = self._page_number_template(line.norm)
                    band_lines.append(line)
                else:
                    body_lines.append(line)
            pages_rows.append(self._edge_rows(self._rows(lines)))
        if not band_lines:
            return {}
        body_size = self._weighted_median(list(sizes.items()))
        heading_size = body_size * _CHROME_HEADING_RATIO
        judge = getattr(self, "boilerplate_judge", None)

        # Evidence for each band line, within its slot. ``recurring``: its text is in the
        # slot on another page too, the widest notion of "repeated" (the judge's band).
        # ``labelled``: numbered like the pages, but with a label that is not a page word.
        slots = self._chrome_slots(band_lines)
        recurring, repeated, numbered, labelled = set(), set(), set(), set()
        slot_of: Dict[int, int] = {}
        for slot_id, slot in enumerate(slots):
            pages_by_norm = defaultdict(set)
            for line in slot:
                pages_by_norm[line.norm].add(line.page)
                slot_of[id(line)] = slot_id
            for line in slot:
                pages = pages_by_norm[line.norm]
                if not re.search(r"[^\W\d_]", line.norm) or len(pages) < 2:
                    continue
                recurring.add(id(line))
                if line.size >= heading_size:
                    strong = len(pages) >= _CHROME_MIN_PAGES and len(pages) > _CHROME_MIN_SHARE * page_count
                else:
                    strong = (len(pages) >= _CHROME_MIN_PAGES
                              or any(0 < abs(page - line.page) <= _CHROME_RUN_DISTANCE for page in pages))
                if strong:
                    repeated.add(id(line))
            for line in self._page_number_lines(slot, body_size):
                (numbered if self._is_page_number_template(line.template) else labelled).add(id(line))

        def page_context(lines: List[_PageLine], reason: str, repeated_on) -> Dict[str, Any]:
            first = min(lines, key=lambda line: (line.page, line.y0))
            return {
                "zone": first.zone,
                "pages": sorted({line.page + 1 for line in lines}),
                "repeated_on": sorted(repeated_on),
                "page_count": page_count,
                "font_size": round(first.size, 2),
                "body_font_size": round(body_size, 2),
                "reason": reason,
            }

        # Numbered labels on most pages go to the judge; one it calls chrome is handled like a
        # running header with a page number from here on, and not asked about again (but for
        # its first copy, see below).
        judged: set = set()
        if judge is not None:
            groups: Dict[Tuple[int, str], List[_PageLine]] = defaultdict(list)
            for line in band_lines:
                if id(line) in labelled:
                    groups[(slot_of[id(line)], line.template)].append(line)
            for lines in groups.values():
                pages = {line.page + 1 for line in lines}
                if len(pages) < _CHROME_MIN_PAGES or len(pages) <= _CHROME_MIN_SHARE * page_count:
                    continue
                first = min(lines, key=lambda line: (line.page, line.y0))
                if self._judged_boilerplate(judge, first.text, page_context(lines, "numbered_label", pages)):
                    ids = {id(line) for line in lines}
                    numbered |= ids
                    labelled -= ids
                    judged |= ids

        capable = set()
        for slot_id, slot in enumerate(slots):
            evident_pages = {line.page for line in slot if id(line) in repeated or id(line) in numbered}
            if len(evident_pages) >= _CHROME_MIN_PAGES and len(evident_pages) > _CHROME_MIN_SHARE * page_count:
                capable.add(slot_id)
        candidates = {line_id for line_id in repeated | numbered if slot_of[line_id] in capable}

        # A bare page number printed elsewhere on a page before or after the numbered ones (a
        # cover or contents page with its own header or footer) continues the page numbering: same
        # offset and printed form ("(3)" is not "3"), in either band, and at most about a line
        # further from its page edge than the numbering's own lines. It is still removed only at
        # the page edge.
        series: Dict[tuple, List[_PageLine]] = defaultdict(list)
        for line in band_lines:
            if id(line) in candidates and id(line) in numbered and self._is_bare_page_number(line.template):
                for position, value in enumerate(line.numbers):
                    series[(line.template, position, value - line.page - 1)].append(line)
        for line in band_lines:
            if (id(line) in candidates or line.template is None or not self._is_bare_page_number(line.template)
                    or line.size > body_size * _CHROME_NUMBER_SIZE_RATIO or any(n > m for n, m in line.totals)):
                continue
            for position, value in enumerate(line.numbers):
                members = series.get((line.template, position, value - line.page - 1), [])
                pages = {member.page for member in members}
                if (len(pages) >= _CHROME_MIN_PAGES and not min(pages) <= line.page <= max(pages)
                        and self._number_shape(line.norm) in {self._number_shape(member.norm) for member in members}
                        and line.edge <= max(member.edge + 1.5 * member.size for member in members)):
                    numbered.add(id(line))
                    candidates.add(id(line))
                    break

        page_numbers = {id(line) for line in band_lines if id(line) in candidates and id(line) in numbered
                        and self._is_bare_page_number(line.template)}
        chrome: Dict[int, _PageLine] = {}
        for top_rows, bottom_rows in pages_rows:
            for zone, edge_first in (("header", top_rows), ("footer", bottom_rows)):
                for row in self._peel_chrome_rows(edge_first, zone, candidates, page_numbers, labelled):
                    chrome.update((id(line), line) for line in row if id(line) not in labelled)

        def repeat_key(line: _PageLine) -> Tuple[str, str]:
            return line.zone, line.template if id(line) in numbered else line.norm

        # Verbatim first: unless the line is a bare page number or repeats a title or heading
        # (heading-sized content on or before the page of its first copy), its first copy
        # stays (see the docstring). A contents entry or other body-sized text with the same
        # words is not the title. Titles near the top or bottom of a page are in the lines read
        # above; the middle of a page is read only for the page of a first copy and the
        # _CHROME_RUN_DISTANCE pages before it.
        firsts: Dict[Tuple[str, str], _PageLine] = {}
        for line in chrome.values():
            if id(line) in numbered and self._is_bare_page_number(line.template):
                continue
            key = repeat_key(line)
            if key not in firsts or (line.page, line.y0) < (firsts[key].page, firsts[key].y0):
                firsts[key] = line
        near = {page for line in firsts.values()
                for page in range(max(0, line.page - _CHROME_RUN_DISTANCE), line.page + 1)}
        headings: Dict[str, int] = {}
        for line in band_lines + body_lines:
            if line.size >= heading_size and id(line) not in chrome:
                norm = line.norm or self._normalized(line.text)
                headings[norm] = min(headings.get(norm, page_count), line.page)
        for page_index in sorted(near):
            for size, text in self._middle_lines(page_index):
                if size >= heading_size:
                    norm = self._normalized(text)
                    headings[norm] = min(headings.get(norm, page_count), page_index)
        first_copies = [line for line in firsts.values() if headings.get(line.norm, page_count) > line.page]
        for line in first_copies:
            del chrome[id(line)]
        kept = {id(line) for line in first_copies}

        # The judge only ever takes later copies the rule kept: the first copy of a repeated line
        # always stays (as plain text), so a line it calls chrome is left once, never lost -- or
        # the text is already on the page body at or before that copy (a cover), and that copy
        # is the one that stays. A running header whose later copies the rule removed already is
        # not asked about.
        judged_firsts: List[_PageLine] = []
        if judge is not None:
            in_body: Dict[str, int] = {}
            for line in body_lines:
                norm = self._normalized(line.text)
                in_body[norm] = min(in_body.get(norm, page_count), line.page)
            copies: Dict[Tuple[str, str], set] = defaultdict(set)
            earliest: Dict[Tuple[str, str], _PageLine] = {}
            ambiguous: Dict[Tuple[str, str], List[_PageLine]] = defaultdict(list)
            for line in band_lines:
                if id(line) in recurring or id(line) in numbered:
                    key = repeat_key(line)
                    copies[key].add(line.page + 1)
                    if key not in earliest or (line.page, line.y0) < (earliest[key].page, earliest[key].y0):
                        earliest[key] = line
                    if id(line) not in chrome:
                        ambiguous[key].append(line)
            for key, lines in ambiguous.items():
                if all(id(line) in judged for line in lines):
                    continue  # asked about as a numbered label already
                first = earliest[key]
                later = [line for line in lines if line is not first]
                if not later:
                    continue  # only the first copy is left, and it always stays
                reason = "attached_to_content" if any(id(line) in candidates for line in later) else "few_pages"
                on_body = in_body.get(first.norm, page_count) <= first.page
                if on_body:
                    later = list(lines)  # the copy in the page body stays
                asked = min(lines, key=lambda line: (line.page, line.y0))
                if self._judged_boilerplate(judge, asked.text, page_context(lines, reason, copies[key])):
                    chrome.update((id(line), line) for line in later)
                    kept.difference_update(id(line) for line in later)
                    if not on_body and any(line is first for line in lines):
                        judged_firsts.append(first)

        # A kept line with a number counting with the pages (a page number the rule leaves, a
        # numbered label) and a kept first copy of a running header or of a line the judge called
        # chrome are emitted as plain text, so they never turn into a heading, list item or
        # footnote; a heading-sized first copy without such a number stays in its block and is
        # classified like any other content (a title repeated on every page).
        regions: Dict[int, List[Tuple[Tuple[float, float, float, float], str, bool]]] = defaultdict(list)
        for line in chrome.values():
            regions[line.page].append((line.rect, line.zone, False))
        plain = [line for line in band_lines
                 if id(line) not in chrome and (id(line) in numbered or id(line) in labelled)]
        plain += [line for line in first_copies
                  if id(line) in kept and id(line) not in numbered and line.size < heading_size]
        plain += [line for line in judged_firsts if line.size < heading_size]
        for line in {id(line): line for line in plain}.values():
            regions[line.page].append((line.rect, line.zone, True))
        return dict(regions)

    def _page_lines(self, page, page_index: int) -> List[_PageLine]:
        """The page's non-empty text lines near its top and bottom edges, with displayed
        coordinates and band zone.

        Only the outer 2.5 bands of an unrotated page are extracted: the band and the rows
        right after it are all the chrome decision needs (``_middle_lines`` reads the rest when
        needed). Rotated pages are extracted whole.
        """
        self._restore_cropbox(page, page_index)
        rect = page.rect
        height = rect.height or 1.0
        matrix = page.rotation_matrix if page.rotation else None
        if matrix is None:
            reach = 2.5 * _CHROME_BAND * height
            clips = [pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + reach),
                     pymupdf.Rect(rect.x0, rect.y1 - reach, rect.x1, rect.y1)]
        else:
            clips = [None]
        lines = []
        blocks = [block for clip in clips
                  for block in page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_LIGATURES, clip=clip)["blocks"]]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(span.get("text", "") for span in spans).strip()
                if not text:
                    continue
                shown = pymupdf.Rect(self._visual_box(line))
                if matrix is not None:
                    shown = shown * matrix
                centre = (shown.y0 + shown.y1) / 2
                if centre < _CHROME_BAND * height:
                    zone = "header"
                elif centre > (1 - _CHROME_BAND) * height:
                    zone = "footer"
                else:
                    zone = None
                lines.append(_PageLine(page=page_index, text=text, rect=tuple(line["bbox"]),
                                       x0=shown.x0, y0=shown.y0, x1=shown.x1, y1=shown.y1,
                                       size=max((span.get("size", 0.0) for span in spans), default=0.0),
                                       zone=zone, edge=centre if zone != "footer" else height - centre))
        return lines

    def _middle_lines(self, page_index: int) -> List[Tuple[float, str]]:
        """``(font size, text)`` of the lines ``_page_lines`` leaves out: the middle of an
        unrotated page, with a margin reaching into the edge areas so that no line is cut."""
        with self._text_page(self.doc.load_page(page_index)) as page:
            if page.rotation:
                return []
            rect = page.rect
            reach = 2.5 * _CHROME_BAND * (rect.height or 1.0) - 50
            clip = pymupdf.Rect(rect.x0, rect.y0 + reach, rect.x1, rect.y1 - reach)
            if clip.is_empty:
                return []
            lines = []
            for block in page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_LIGATURES, clip=clip)["blocks"]:
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text = "".join(span.get("text", "") for span in spans).strip()
                    if text:
                        lines.append((max((span.get("size", 0.0) for span in spans), default=0.0), text))
            return lines

    @staticmethod
    def _number_shape(norm: str) -> str:
        """A normalised line with its numbers masked: the printed form of a page number."""
        return re.sub(r"\d+", "#", norm)

    @staticmethod
    def _normalized(text: str) -> str:
        """Text as running headers are compared: Unicode width folded, whitespace collapsed, case ignored."""
        return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()

    @staticmethod
    def _rows(lines: List[_PageLine]) -> List[List[_PageLine]]:
        """Lines grouped into rows (vertically overlapping lines), top to bottom as displayed."""
        rows: List[List[_PageLine]] = []
        top = bottom = 0.0
        for line in sorted(lines, key=lambda line: (line.y0, line.x0)):
            if rows:
                overlap = min(bottom, line.y1) - max(top, line.y0)
                if overlap >= 0.5 * max(min(bottom - top, line.y1 - line.y0), 1e-6):
                    rows[-1].append(line)
                    bottom = max(bottom, line.y1)
                    continue
            rows.append([line])
            top, bottom = line.y0, line.y1
        return rows

    @staticmethod
    def _edge_rows(rows: List[List[_PageLine]]) -> Tuple[List[List[_PageLine]], List[List[_PageLine]]]:
        """The rows the chrome decision can use, from each page edge inward: the rows in that
        band and the three rows after them (``_row_detached`` looks that far)."""
        top = next((i for i, row in enumerate(rows) if any(line.zone != "header" for line in row)), len(rows))
        bottom = next((i for i, row in enumerate(rows[::-1]) if any(line.zone != "footer" for line in row)),
                      len(rows))
        return rows[:top + 3], rows[::-1][:bottom + 3]

    @staticmethod
    def _chrome_slots(lines: List[_PageLine]) -> List[List[_PageLine]]:
        """Band lines grouped by zone and distance from that page edge (within tolerance)."""
        slots: List[List[_PageLine]] = []
        anchor = 0.0
        for line in sorted(lines, key=lambda line: (line.zone, line.edge)):
            if slots and slots[-1][0].zone == line.zone and line.edge - anchor <= _CHROME_SLOT_TOLERANCE:
                slots[-1].append(line)
            else:
                slots.append([line])
                anchor = line.edge
        return slots

    @staticmethod
    def _page_number_template(norm: str) -> Tuple[Optional[str], Tuple[int, ...], Tuple[Tuple[int, int], ...]]:
        """Mask the page-number-shaped tokens of a normalised line.

        Returns ``(template, numbers, totals)``: the line with each page-number-shaped token
        (``_PAGE_NUMBER_PATTERNS``, or a whole-line roman numeral such as ``iv``,
        ``page iv``, ``- iv -``) replaced by ``#``, the tokens' values, and ``(N, M)`` for each
        masked ``N/M`` or ``N of M``; or ``(None, (), ())`` when the line has none.
        """
        roman = _ROMAN_PAGE_NUMBER.fullmatch(norm)
        if roman and _ROMAN_NUMERAL.fullmatch(roman.group("r")):
            digits = [{"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}[c] for c in roman.group("r")]
            value = sum(-d if i + 1 < len(digits) and digits[i + 1] > d else d for i, d in enumerate(digits))
            return norm[:roman.start("r")] + "#" + norm[roman.end("r"):], (value,), ()

        numbers: List[int] = []
        totals: List[Tuple[int, int]] = []

        def mask(match, template):
            value = int(match.group("n"))
            numbers.append(value)
            total = re.search(r"\d+", match.groupdict().get("t") or "")
            if total:
                totals.append((value, int(total.group())))
            return match.expand(template)

        text = norm
        for pattern, template in _PAGE_NUMBER_PATTERNS:
            text = pattern.sub(lambda match: mask(match, template), text)
        return (text, tuple(numbers), tuple(totals)) if numbers else (None, (), ())

    @staticmethod
    def _page_number_lines(slot: List[_PageLine], body_size: float) -> List[_PageLine]:
        """The slot's lines whose masked numbers follow the page order.

        Lines with the same template are grouped. A line qualifies when, for one masked
        token, at least ``_CHROME_MIN_PAGES`` pages share its offset between that token's
        value and the page number (chapter-restarting numbering keeps one offset per
        chapter). Pages showing several different numbers of the shape (a row of chart axis
        years), heading-sized numbers (KPI figures) and groups where a number is larger than
        the total printed after it (the dates 15/03 ... 20/03, or 01/03 ... 06/03) never count.
        Whether a qualifying line is a page number depends on its words
        (``_is_page_number_template``).
        """
        groups: Dict[Tuple[str, int], List[_PageLine]] = defaultdict(list)
        for line in slot:
            if line.template is not None and line.size <= body_size * _CHROME_NUMBER_SIZE_RATIO:
                groups[(line.template, len(line.numbers))].append(line)
        found: List[_PageLine] = []
        for (template, count), lines in groups.items():
            if any(number > total for line in lines for number, total in line.totals):
                continue
            values_by_page = defaultdict(set)
            for line in lines:
                values_by_page[line.page].add(line.numbers)
            lines = [line for line in lines if len(values_by_page[line.page]) == 1]
            following = set()
            for position in range(count):
                pages_by_offset = defaultdict(set)
                for line in lines:
                    pages_by_offset[line.numbers[position] - (line.page + 1)].add(line.page)
                following.update(id(line) for line in lines
                                 if len(pages_by_offset[line.numbers[position] - (line.page + 1)]) >= _CHROME_MIN_PAGES)
            found.extend(line for line in lines if id(line) in following)
        return found

    def _peel_chrome_rows(self, rows: List[List[_PageLine]], zone: str, candidates: set,
                          page_numbers: set, riders: set = frozenset()) -> List[List[_PageLine]]:
        """The rows at one page edge that hold chrome; ``rows`` runs from that edge inward.

        Rows are taken while every line in them is a chrome candidate of this zone, or a
        ``riders`` line (a numbered label, which stays content) next to at least one candidate.
        The innermost taken row must then stand apart from the rows after it
        (``_row_detached``; a row of bare page numbers, ``page_numbers``, needs no gap);
        otherwise it is given back, and so on.
        """
        peeled: List[List[_PageLine]] = []
        for row in rows:
            if (not all(line.zone == zone and (id(line) in candidates or id(line) in riders) for line in row)
                    or not any(id(line) in candidates for line in row)):
                break
            peeled.append(row)
        while peeled and len(peeled) < len(rows) and not self._row_detached(
                peeled[-1], rows[len(peeled):], zone, all(id(line) in page_numbers for line in peeled[-1])):
            peeled.pop()
        return peeled

    def _row_detached(self, chrome_row: List[_PageLine], next_rows: List[List[_PageLine]], zone: str,
                      page_numbers: bool = False) -> bool:
        """Does ``chrome_row`` stand apart from ``next_rows``, the rows after it toward the page centre?

        It needs a gap of at least 0.7 of its height (3pt minimum; heights measured from the
        baselines) before the next row, except a row of bare page numbers (``page_numbers``):
        Word and LibreOffice set the page number right under a full page's last line. And it
        must not be a table's header row: one with three or more cells (lines, or parts of a
        line set apart by runs of spaces) lined up with three cells of one of the next three rows.
        """
        top = min(line.y0 for line in chrome_row)
        bottom = max(line.y1 for line in chrome_row)
        if zone == "header":
            gap = min(line.y0 for line in next_rows[0]) - bottom
        else:
            gap = top - max(line.y1 for line in next_rows[0])
        if not page_numbers and gap < max(3.0, 0.7 * (bottom - top)):
            return False
        cells = self._row_cells(chrome_row)
        if len(cells) < 3:
            return True
        for row in next_rows[:3]:
            unmatched = list(cells)
            aligned = 0
            for x0, x1 in self._row_cells(row):
                for cell in unmatched:
                    if min(x1, cell[1]) > max(x0, cell[0]):
                        unmatched.remove(cell)
                        aligned += 1
                        break
            if aligned >= 3:
                return False
        return True

    @staticmethod
    def _row_cells(row: List[_PageLine]) -> List[Tuple[float, float]]:
        """Horizontal extents of a row's cells: its lines, split where a line has a run of two or
        more spaces. Positions inside a line are estimated from typical glyph widths (a
        space is narrower than a digit, a CJK character wider than a letter)."""
        def advance(char: str) -> float:
            if char.isspace():
                return 0.28
            if unicodedata.east_asian_width(char) in ("W", "F"):
                return 1.0
            return 0.67 if char.isupper() else 0.56 if char.isdigit() else 0.5

        cells = []
        for line in row:
            offsets = [0.0]
            for char in line.text:
                offsets.append(offsets[-1] + advance(char))
            scale = (line.x1 - line.x0) / (offsets[-1] or 1.0)
            for part in re.finditer(r"\S+(?: \S+)*", line.text):
                cells.append((line.x0 + scale * offsets[part.start()], line.x0 + scale * offsets[part.end()]))
        return cells

    @staticmethod
    def _weighted_median(pairs: List[Tuple[float, int]]) -> float:
        """Median of values weighted by counts (with character counts: the body font size)."""
        pairs = sorted((value, weight) for value, weight in pairs if weight > 0)
        total = sum(weight for _, weight in pairs)
        seen = 0
        for value, weight in pairs:
            seen += weight
            if 2 * seen >= total:
                return value
        return 0.0

    @staticmethod
    def _is_bare_page_number(template: str) -> bool:
        """A page-number template with nothing but page-number words around its numbers."""
        return all(word in _PAGE_NUMBER_WORDS for word in re.findall(r"[^\W\d_]+", template))

    @staticmethod
    def _is_page_number_template(template: str) -> bool:
        """Whether numbers following the page order in lines of this template are page numbers
        by their words: bare (``#``, ``- # -``, ``# of 12``) or with a page word
        (``acme | page #``, ``第#頁``). Other labels (``# | acme corp``, ``lesson · #``) may be
        per-page content."""
        words = re.findall(r"[^\W\d_]+", template)
        return all(word in _PAGE_NUMBER_WORDS for word in words) or any(word in _PAGE_WORDS for word in words)

    @staticmethod
    def _judged_boilerplate(judge: BoilerplateJudge, text: str, context: Dict[str, Any]) -> bool:
        """Ask ``judge`` about an ambiguous repeated line; anything but a probability >= 0.5 keeps it."""
        try:
            probability = judge(text, context)
        except Exception as e:
            logger.warning(f"boilerplate_judge failed on {text[:40]!r}: {e}; keeping the line")
            return False
        if probability is None:
            return False
        if (isinstance(probability, bool) or not isinstance(probability, numbers.Real)
                or not 0.0 <= float(probability) <= 1.0):
            logger.warning(f"boilerplate_judge returned {probability!r} for {text[:40]!r} "
                           f"(expected None or 0..1); keeping the line")
            return False
        return float(probability) >= _CHROME_JUDGE_THRESHOLD

    def _uncrop_for_tables(self, page, page_num: Optional[int] = None) -> None:
        """Take a rotated page's own /CropBox off before its tables are found, so that
        find_tables() reports them in one uncropped frame (see ``_record_rotated_crop_boxes``);
        ``_restore_cropbox`` puts it back once the tables are extracted. ``page_num`` is the
        page's index in ``self.doc`` (default ``page.number``): the page object may be another
        handle on the file (a cleaned copy), whose own document is the one written to.

        While the CropBox is off, ``pdf_routing.PageCopies`` does not copy the page (the copy
        would not show what the page shows), so the tables of such a page are read from the
        page itself even when it has hidden text; the text path still leaves that text out."""
        number = page.number if page_num is None else page_num
        if number not in getattr(self, "_rotated_crop_boxes", {}) or number in self._rotated_cropped_pages:
            return
        try:
            doc = page.parent
            doc.xref_set_key(page.xref, "CropBox", "null")
            self._table_frames[number] = (doc, page.xref, page.derotation_matrix)
        except Exception as e:
            logger.debug(f"Could not uncrop page {number + 1} for table detection, its tables are not suppressed: {e}")
            self._rotated_cropped_pages.add(number)

    def _restore_cropbox(self, page, page_num: Optional[int] = None) -> None:
        """Put back the /CropBox that ``_uncrop_for_tables`` or PyMuPDF's find_tables() took off
        a rotated page, so its text is read in its own frame (see ``_record_rotated_crop_boxes``).
        Written as the original entry, without PyMuPDF's validation, on the page's own document
        (another handle on the file than ``self.doc`` when text is read from a cleaned copy), and
        on the page ``_uncrop_for_tables`` uncropped if the text now comes from another object.
        ``page_num`` is the page's index in ``self.doc`` (default ``page.number``). If writing
        fails, the page gets no table suppression."""
        number = page.number if page_num is None else page_num
        source = getattr(self, "_rotated_crop_boxes", {}).get(number)
        if source is None:
            return
        targets = [(page.parent, page.xref)]
        frame = getattr(self, "_table_frames", {}).get(number)
        if frame is not None and (frame[0] is not targets[0][0] or frame[1] != targets[0][1]):
            targets.append(frame[:2])
        for doc, xref in targets:
            try:
                if doc.xref_get_key(xref, "CropBox")[0] == "null":
                    doc.xref_set_key(xref, "CropBox", source)
            except Exception as e:
                logger.debug(f"Could not restore the CropBox of page {number + 1}: {e}")
                self._rotated_cropped_pages.add(number)

    @contextmanager
    def _text_page(self, page, ocr_images: Optional[bool] = None,
                   ocr_results_map: Optional[Dict[tuple, str]] = None):
        """The page object whose text is read for ``page`` (a page of ``self.doc``): the page as
        ``_text_source`` gives it, without its hidden text. Text and tables (``_process_page``),
        running headers and footers (``_detect_page_chrome``, ``_middle_lines``) and the
        first-text-page check all read a page through here, so they see the same text.
        ``ocr_images`` and ``ocr_results_map`` default to the conversion under way (see
        ``convert_to_json``). The page copies the text source makes are discarded when the
        block ends, so blocks never nest: ``_process_page`` runs the document-wide passes
        before opening its page's block."""
        copies = getattr(self, "_copies", None)
        if copies is None:  # no routing state (a loader built without __init__): the page itself
            yield page
            return
        if ocr_images is None:
            ocr_images, ocr_results_map = getattr(self, "_text_options", (False, None))
        try:
            yield self._text_source(page, page.number, ocr_images, ocr_results_map)
        finally:
            copies.discard()

    def _get_first_text_page_num(self) -> int:
        """Return the first page index containing non-empty text, falling back to 0.

        Running header/footer lines do not count: a cover whose text layer holds only
        page chrome is not where the document title is.
        """
        cached = getattr(self, "_first_text_page_num", None)
        if cached is not None:
            return cached

        doc = getattr(self, "doc", None)
        if doc is None:
            return 0

        try:
            for page_num in range(len(doc)):
                with self._text_page(doc.load_page(page_num)) as page:
                    text_dict = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_LIGATURES)
                for block in text_dict.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        bbox = line.get("bbox")
                        if bbox and self._chrome_line(bbox, self._page_chrome_regions(page_num)):
                            continue
                        for span in line.get("spans", []):
                            if span.get("text", "").strip():
                                self._first_text_page_num = page_num
                                return page_num
        except Exception as e:
            logger.debug(f"Failed to detect first text page, defaulting to page 0: {e}")
            self._first_text_page_num = 0
            return 0

        self._first_text_page_num = 0
        return 0

    def _page_measure(self, page_num: int) -> "pdf_routing.PageMeasure":
        """Routing signals of one page (measured once, see pdf_routing.measure_page)."""
        measure = self._page_measures.get(page_num)
        if measure is None:
            page = self.doc.load_page(page_num)
            measure = pdf_routing.measure_page(
                page,
                ocr_rects=self._ocr_image_rects,
                keep_text=self._legibility_judge is not None and self.ocr is not None,
                copies=self._copies,
            )
            self._page_measures[page_num] = measure
        return measure

    def _judged_signals(self, page_num: int):
        """The page's signals with the optional legibility judge's verdict (asked once,
        only for text layers the deterministic detector did not flag)."""
        measure = self._page_measure(page_num)
        if self._legibility_judge is None or page_num in self._judged_pages:
            return measure.signals
        self._judged_pages.add(page_num)
        verdict = _judge_text_layer(self._legibility_judge, measure.signals.text_layer, measure.text or "")
        if verdict is not None:
            measure = replace(measure, signals=replace(measure.signals, judge_legibility=verdict))
            self._page_measures[page_num] = measure
        return measure.signals

    def _placements_of(self, page) -> List["pdf_images.Placement"]:
        """The page's image placements (see pdf_images.placements), measured once per page."""
        placed = self._placements.get(page.number)
        if placed is None:
            placed = self._placements[page.number] = pdf_images.placements(page)
        return placed

    def _prefetch_legibility(self) -> None:
        """Hand a legibility judge that can ``prefetch(texts)`` every page text it is about to
        be asked about (pages whose route would keep their text layer, see ``_page_route``),
        so it can ask them all at once; the per-page calls that follow read its answers."""
        prefetch = getattr(self._legibility_judge, "prefetch", None)
        if self.ocr is None or not callable(prefetch) or self.doc is None:
            return
        document_route = self._document_image_strategy()
        texts = []
        for page_num in range(len(self.doc)):
            if page_num in self._judged_pages or page_num in self._page_routes:
                continue
            measure = self._page_measure(page_num)
            if (_decide_page_route(measure.signals, document_route)[0] == "text"
                    and _wants_judgment(measure.signals.text_layer)):
                texts.append(measure.text or "")
        if texts:
            try:
                prefetch(texts)
            except Exception as e:  # the judge is optional: its failures must not fail the conversion
                logger.debug(f"legibility_judge prefetch failed: {e!r}")

    def _ocr_image_rects(self, page) -> List[Any]:
        """Placements the text route reads one by one whatever their pixels: image XObjects the page
        shows whole that are not small (see pdf_images.single_rects; geometry only, no pixels). Small
        pictures, tiles, inline images and cropped pictures are not, so a page without a text layer
        that shows them is OCR'd from its render."""
        try:
            return pdf_images.single_rects(page, self._placements_of(page))
        except Exception as e:
            logger.debug(f"Failed to get the image placements of page {page.number + 1}: {e}")
            return []

    def _page_signals(self, page_num: int):
        return self._page_measure(page_num).signals

    def _document_image_strategy(self) -> str:
        """High-level DOCUMENT route (core.strategy.decide_doc_strategy) from the
        pages' signals: mean visible image coverage and mean legible text density
        (script-aware, see core.strategy.text_weight). Text-layer quality is not a
        document signal here: every page is checked and garbled pages are OCR'd one
        by one (see _page_route), so they never take clean pages with them.

        - "image": pages are mostly pictures (mean coverage high) AND carry little
          legible text — the real content is in the page images. Pages are rendered
          and OCR'd as whole images (with neighbor-page context when enabled); the
          OCR is authoritative.
        - "text": there is a usable text layer or little image coverage. The
          deterministic rule-based layer (complex tables + text, preserved verbatim
          for BM42 RAG) is authoritative, and embedded figures are OCR'd individually.

        This is every page's default; a page whose own signals clearly disagree
        overrides it (see _page_route). Decided once per document (cached).
        """
        if self._doc_strategy is not None:
            return self._doc_strategy
        n = len(self.doc) if self.doc is not None else 0
        if n == 0:
            self._doc_strategy = "text"
            return self._doc_strategy
        mean_cov, mean_text, illegible = _document_signals([self._page_signals(i) for i in range(n)])
        self._doc_strategy = _decide_doc_strategy(mean_cov, mean_text)
        logger.info(f"📑 Document OCR strategy: {self._doc_strategy} "
                    f"(mean coverage {mean_cov:.2f}, mean legible text {mean_text:.0f}/page, "
                    f"garbled text pages {illegible:.0%})")
        return self._doc_strategy

    def _page_route(self, page_num: int) -> Tuple[str, str]:
        """``(route, reason)`` of one page when an OCR provider is active: the
        document route, unless the page's own signals clearly disagree (a
        searchable scan, a garbled text layer, content only as vector outlines or
        inline images, a scanned page in a text report, a dense text page in an
        image deck). See core.strategy.decide_page_route."""
        route = self._page_routes.get(page_num)
        if route is None:
            document_route = self._document_image_strategy()
            route = _decide_page_route(self._page_signals(page_num), document_route)
            if route[0] == "text" and self._legibility_judge is not None:
                # Only a page that would keep its text layer needs the judge's opinion.
                route = _decide_page_route(self._judged_signals(page_num), document_route)
            self._page_routes[page_num] = route
        return route

    def _text_source(self, page, page_num: int, ocr_images: bool, ocr_results_map: Optional[Dict[tuple, str]]):
        """The page as the text and table extractors should read it (discard
        ``self._copies`` once they are done).

        Invisible (render mode 3) text over nothing the page shows is hidden text, and
        an invisible copy of the painted text it lies on is a duplicate: neither reaches
        them (H-F15). Invisible text over what the page shows (a scanner's OCR layer, a
        transparent copy of text baked into artwork or drawn as outlines) is that
        content's text: kept, except over a picture whose OCR in this run returned text,
        so each picture gives one source, not two (R-F1).
        """
        measure = self._page_measure(page_num)
        if not measure.has_invisible_text:
            return page
        ocrd = self._ocrd_picture_rects(page, page_num, ocr_images, ocr_results_map)
        return pdf_routing.text_source(page, measure, self._copies, ocrd_rects=ocrd)

    def _ocrd_picture_rects(self, page, page_num: int, ocr_images: bool,
                            ocr_results_map: Optional[Dict[tuple, str]]) -> List[Any]:
        """Placements on this page whose OCR in this run returned text (the batch results;
        the legacy per-page OCR runs after text extraction, so its results are unknown
        here and the invisible text over its pictures is kept)."""
        if not ocr_images or self.ocr is None or not ocr_results_map:
            return []
        rects = []
        for picture in self._pictures.get(page_num, ([], {}))[0]:
            if (ocr_results_map.get((page_num, picture.key)) or "").strip():
                rects.extend(picture.rects)
        return rects

    def _record_routing(self, document: Dict[str, Any], ocr_active: bool) -> None:
        """Record the routing facts in ``document`` and warn about text that cannot be trusted or extracted.

        - ``ocr_routing`` (OCR active only): the document route and the pages that
          overrode it, with the reason (see core.strategy.decide_page_route).
        - ``text_layer_quality``: pages whose text layer is garbled (the detector, or
          the legibility judge when OCR is active), and whether it was replaced by OCR
          of the render (``"ocr"``) or kept as extracted (``"kept"``, with a warning).
        - ``hidden_text``: pages whose invisible (render mode 3) text over nothing the
          page shows was left out.
        - Warnings when the output is empty, when pages showing content produced no
          text (OCR returned nothing), or, without OCR, for pages that have no usable
          text layer but show content (scans, vector outlines).
        """
        n = len(self.doc)
        if n == 0:
            return
        name = self.pdf_path.name
        if ocr_active:
            doc_route = self._document_image_strategy()
            overrides = []
            for i in range(n):
                route, reason = self._page_route(i)
                if route != doc_route:
                    overrides.append({"page": i + 1, "route": route, "reason": reason})
            document["ocr_routing"] = {"document_route": doc_route, "overrides": overrides}
        # The legibility judge was asked (by _page_route) only when OCR is active: its
        # verdict can only choose OCR for a page, so without OCR it is never consulted.
        signals = [self._page_signals(i) for i in range(n)]

        quality, kept = [], []
        for i, page in enumerate(signals):
            if not page.text_layer_illegible:
                continue
            ocrd = i in self._rendered_pages
            layer = page.text_layer
            quality.append({
                "page": i + 1,
                "legible": False,
                "garbage_ratio": round(layer.garbage_ratio, 3),
                "garbage_glyphs": layer.garbage_glyphs,
                "judge_legibility": page.judge_legibility,
                "action": "ocr" if ocrd else "kept",
            })
            if not ocrd:
                kept.append(i)
        if quality:
            document["text_layer_quality"] = quality
        if kept:
            logger.warning(f"{name} {pdf_routing.describe_pages(kept)}: the text layer looks garbled (undecodable "
                           f"glyphs or unreadable text) and was kept as extracted; OCR of the page render (an OCR "
                           f"provider with ocr_images=True; CLI: --ocr <provider> --ocr-images) reads it instead")

        hidden = [i for i, page in enumerate(signals) if page.hidden_chars]
        if hidden:
            document["hidden_text"] = [{"page": i + 1, "chars": signals[i].hidden_chars} for i in hidden]
            logger.warning(f"{name} {pdf_routing.describe_pages(hidden)}: invisible (render mode 3) text over "
                           f"nothing the page shows was left out of the output")

        emitted = {item.get("page") for item in document.get("content", []) if str(item.get("content", "")).strip()}
        if not emitted:
            hint = ("" if ocr_active else "; its pages carry no usable text layer, enable OCR (an OCR provider "
                    "with ocr_images=True; CLI: --ocr <provider> --ocr-images) to read scanned or drawn pages")
            logger.warning(f"{name}: no text could be extracted from its {n} page(s){hint}")
        elif ocr_active:
            silent = [i for i, page in enumerate(signals) if i + 1 not in emitted
                      and (page.image_coverage >= _MIN_UNCAPTURED_RASTER or page.uncaptured_content
                           or page.invisible.chars)]
            if silent:
                logger.warning(f"{name} {pdf_routing.describe_pages(silent)}: no text was extracted although the "
                               f"page shows content (the OCR returned nothing)")
        else:
            missing = _pages_without_text(signals)
            if missing:
                logger.warning(f"{name} {pdf_routing.describe_pages(missing)}: little or no text layer; content "
                               f"shown only as scanned images or drawn outlines was not extracted; enable OCR (an "
                               f"OCR provider with ocr_images=True; CLI: --ocr <provider> --ocr-images) to read it")

    def _render_page_png(self, page) -> bytes:
        """Rasterize a whole page to PNG bytes for page-level OCR."""
        return page.get_pixmap(dpi=_PAGE_RENDER_DPI).tobytes("png")

    def _build_window_pdf(self, k: int) -> Optional[str]:
        """Base64 (RAW, no data-uri prefix) of a PDF with only pages {k-1,k,k+1},
        clamped to doc bounds. Built once per page index, LRU-bounded. Returns None
        on failure/oversize -> caller treats None as 'no context' (graceful)."""
        if k in self._window_pdf_cache:
            self._window_pdf_cache.move_to_end(k)
            return self._window_pdf_cache[k]
        result: Optional[str] = None
        try:
            a = max(0, k - 1)
            b = min(len(self.doc) - 1, k + 1)          # k=0->{0,1}; last->{n-2,n-1}; single->{0}
            out = pymupdf.open()
            try:
                out.insert_pdf(self.doc, from_page=a, to_page=b)   # inclusive/inclusive
                data = out.tobytes(deflate=True, garbage=3)        # compress + drop orphans
            finally:
                out.close()
            if len(data) <= _CONTEXT_PDF_MAX_BYTES:
                result = base64.b64encode(data).decode("utf-8")
            else:
                logger.warning(f"Context PDF for page {k+1} is {len(data)} bytes "
                               f"(> {_CONTEXT_PDF_MAX_BYTES}); skipping context for this page.")
        except Exception as e:
            logger.warning(f"Failed to build context PDF for page {k+1}: {e}; OCR without context.")
        self._window_pdf_cache[k] = result
        if len(self._window_pdf_cache) > _WINDOW_CACHE_MAXLEN:
            self._window_pdf_cache.popitem(last=False)             # evict oldest (LRU)
        return result

    def _page_pictures(self, page_num: int) -> List["pdf_images.Picture"]:
        """The pictures of a page the text route OCRs (see pdf_images): placements the page shows,
        tiles joined into one picture, and only pictures whose pixels carry content (a plain
        background, a gradient, a frame, or a small icon without text is left out). Decided once
        per page per conversion; image XObjects are judged once per document."""
        cached = self._pictures.get(page_num)
        if cached is not None:
            return cached[0]
        page = self.doc.load_page(page_num)
        try:
            pictures, skipped = pdf_images.page_pictures(page, self._placements_of(page))
        except Exception as e:
            logger.warning(f"Failed to list the images of page {page_num + 1}: {e}")
            pictures, skipped = [], {}
        kept, shapes = [], []
        for picture in pictures:
            if picture.xref:
                content = self._xref_content.get(picture.xref)
                if content is None:
                    content = pdf_images.classify(page, picture, lambda xref: pdf_images.image_grey(self.doc, xref))
                    self._xref_content[picture.xref] = content
            else:
                content = pdf_images.classify(page, picture, None, getattr(self, "_copies", None))
            if pdf_images.ocr_worthy(page, picture, content):
                kept.append(picture)
            elif content == pdf_images.SHAPES:
                shapes.append(picture)
            else:
                skipped["no_content"] = skipped.get("no_content", 0) + len(picture.rects)
        if shapes and not kept and self._without_text_layer(page_num):
            # A page without a text layer shows nothing but these pictures: read them rather than
            # emit nothing (a small photo over a printed label reads as "shapes").
            kept = shapes
        else:
            skipped["no_content"] = skipped.get("no_content", 0) + sum(len(picture.rects) for picture in shapes)
        self._pictures[page_num] = (kept, skipped)
        return kept

    def _without_text_layer(self, page_num: int) -> bool:
        """Whether the page has no usable text layer (see core.strategy.NO_TEXT_LIMIT)."""
        try:
            return self._page_signals(page_num).visible.weight < _NO_TEXT_LIMIT
        except Exception as e:
            logger.debug(f"Could not measure the text layer of page {page_num + 1}: {e}")
            return False

    def _ocr_jobs(self):
        """What the document's pages show for OCR, page by page (a generator: a page is rendered only
        when its turn comes). A page routed "image" (see _page_route) is ONE whole-page render; the
        other pages give their pictures (see _page_pictures). Each job has ``page_num``, ``xref``
        (``_PAGE_RENDER_XREF`` for a render), ``is_page_render``, ``targets`` (the ``(page, key)``
        entries of ``ocr_results_map`` its text answers), ``context_pdf_b64``, ``identity`` (what
        makes two jobs the same OCR request) and the image bytes (``image``), or ``load`` to fetch
        them when the request is new."""
        self._prefetch_legibility()
        for page_num in range(len(self.doc)):
            page = self.doc.load_page(page_num)
            if self.ocr is not None and self._page_route(page_num)[0] == "image":
                try:
                    png = self._render_page_png(page)
                except Exception as e:
                    logger.warning(f"Page render failed on page {page_num + 1}: {e}; "
                                   f"falling back to per-image OCR")
                else:
                    ctx = self._build_window_pdf(page_num) if self._context_tier >= 1 else None
                    yield {"page_num": page_num, "xref": _PAGE_RENDER_XREF, "is_page_render": True,
                           "image": png, "context_pdf_b64": ctx, "targets": [(page_num, _PAGE_RENDER_XREF)],
                           "identity": ("image", _digest(png), _digest(ctx))}
                    continue
            yield from self._picture_jobs(page, page_num, self._page_pictures(page_num))

    def _picture_jobs(self, page, page_num: int, pictures: List["pdf_images.Picture"]):
        """OCR jobs (see _ocr_jobs) for ``pictures`` of a page: an image XObject is fetched only when
        its request is new (``load``); a region is rendered now, without the text painted over it."""
        ctx = self._build_window_pdf(page_num) if (pictures and self._context_tier >= 2) else None
        for picture in pictures:
            job = {"page_num": page_num, "xref": picture.xref, "is_page_render": False,
                   "context_pdf_b64": ctx, "targets": [(page_num, picture.key)]}
            if picture.xref:
                # One request per image, however often it shows (per page when its neighbour
                # pages go along as context: the answer then depends on the page).
                job["identity"] = ("xref", picture.xref, page_num if ctx else None)
                job["load"] = lambda xref=picture.xref: self._picture_bytes(xref)
            else:
                copies = getattr(self, "_copies", None)
                try:
                    pix = pdf_images.render_region(page, picture.region, picture.dpi, copies)
                    job["image"] = pix.tobytes("png")
                except Exception as e:
                    logger.warning(f"Failed to render a picture on page {page_num + 1}: {e}")
                    continue
                finally:
                    if copies is not None:
                        copies.discard()
                job["identity"] = ("image", _digest(job["image"]), _digest(ctx))
            yield job

    def _picture_bytes(self, xref: int) -> Optional[Tuple[bytes, str, str]]:
        """What OCR reads of an image XObject: a transparent image composited onto white (as the page
        shows it), any other image as extracted (see _extract_image_bytes)."""
        flattened = pdf_images.flattened_png(self.doc, xref)
        if flattened is not None:
            return flattened, "png", "image/png"
        return self._extract_image_bytes(xref)

    def _ocr_batch_limit(self) -> int:
        """Images per OCR call: _OCR_BATCH_IMAGES, or twice the provider's concurrency when higher."""
        from doc2mark.ocr.base import resolve_max_concurrency
        try:
            concurrency = resolve_max_concurrency(getattr(getattr(self.ocr, "config", None), "max_concurrency", None))
        except Exception:
            concurrency = None
        return max(_OCR_BATCH_IMAGES, 2 * concurrency) if concurrency else _OCR_BATCH_IMAGES

    def _ocr_document(self, ocr_results_map: Dict[tuple, str], *, show_progress: bool) -> Dict[str, Any]:
        """OCR everything the pages show into ``ocr_results_map``, streaming (see _ocr_jobs).

        One request per distinct image content: an image shown on many pages or places is sent
        once, and the same pixels under another xref or a repeated render are not sent again.
        Requests go to the provider in batches bounded in count and bytes, whole-page renders
        (which also synthesize ``page_markdown``) and pictures apart; a batch's images are
        released once it is answered, so memory does not grow with the page count.

        Returns what was sent (``metadata.extra["ocr_images"]``): ``ocr_requests``,
        ``page_renders``, ``batches``, ``largest_batch``, ``empty`` / ``failed`` (requests
        answered with no text / not answered), and ``skipped``: placements not OCR'd because
        the page does not show them (``not_shown``) or they carry nothing to read
        (``no_content``). convert_to_json adds ``unread_pages``: pages that show content but
        whose render OCR returned nothing (each carries a ``[page N: OCR returned no
        content]`` marker).
        """
        stats: Dict[str, Any] = {"ocr_requests": 0, "page_renders": 0, "batches": 0, "largest_batch": 0,
                                 "empty": 0, "failed": 0}
        limit = self._ocr_batch_limit()
        pending: Dict[bool, List[Dict[str, Any]]] = {True: [], False: []}
        pending_bytes = {True: 0, False: 0}
        known: Dict[tuple, Dict[str, Any]] = {}

        def flush(render: bool) -> None:
            batch = pending[render]
            if not batch:
                return
            pending[render], pending_bytes[render] = [], 0
            if show_progress:
                logger.info(f"Processing {len(batch)} images with batch OCR...")
            self._ocr_batch(batch, ocr_results_map, synthesis_markdown=render, show_progress=show_progress)
            stats["batches"] += 1
            stats["largest_batch"] = max(stats["largest_batch"], len(batch))
            for job in batch:
                job["done"] = True
                job.pop("image", None)             # release the pixels
                job.pop("context_pdf_b64", None)
                text = ocr_results_map.get(job["targets"][0])
                if text is None:
                    stats["failed"] += 1
                elif not text.strip():
                    stats["empty"] += 1

        def reuse(identity: tuple, job: Dict[str, Any]) -> bool:
            first = known.get(identity)
            if first is None:
                return False
            if not first.get("done"):
                first["targets"].extend(job["targets"])
            elif first["targets"][0] in ocr_results_map:
                for target in job["targets"]:
                    ocr_results_map[target] = ocr_results_map[first["targets"][0]]
            return True

        for job in self._ocr_jobs():
            if reuse(job["identity"], job):
                continue
            load = job.pop("load", None)
            if load is not None:
                known[job["identity"]] = job
                loaded = load()
                if loaded is None:              # not extractable: it becomes a placeholder
                    job["done"] = True
                    stats["failed"] += 1
                    continue
                job["image"] = loaded[0]
                content = ("image", _digest(job["image"]), _digest(job["context_pdf_b64"]))
                if reuse(content, job):         # the same pixels under another xref
                    known[job["identity"]] = known[content]
                    continue
                known[content] = job
            else:
                known[job["identity"]] = job
            render = bool(job["is_page_render"])
            stats["ocr_requests"] += 1
            stats["page_renders"] += render
            pending[render].append(job)
            pending_bytes[render] += len(job["image"]) + len(job.get("context_pdf_b64") or "")
            if len(pending[render]) >= limit or pending_bytes[render] >= _OCR_BATCH_BYTES:
                flush(render)
        flush(True)
        flush(False)
        skipped: Dict[str, int] = {}
        for _, reasons in self._pictures.values():
            for reason, count in reasons.items():
                skipped[reason] = skipped.get(reason, 0) + count
        stats["skipped"] = skipped
        return stats

    def _process_page(self, page_num: int, extract_images: bool = True, ocr_images: bool = False,
                      ocr_results_map: Dict[tuple, str] = None) -> List[Dict[str, Any]]:
        """Process a single page, routed by its OCR route (_page_route, applied in
        _ocr_jobs):

        - IMAGE-authoritative (a whole-page render was OCR'd): emit ONLY the OCR
          transcription. A sparse text layer on such a page is chrome
          (logo / footer / page number), the invisible layer of a searchable scan,
          or a garbled layer; the whole-page OCR already captures what it shows,
          so emitting it too would duplicate content or add garbage. When the
          render OCR'd to nothing, the page falls back to its own text layer
          instead of disappearing.
        - TEXT-authoritative: emit the deterministic text/table layer (preserved
          verbatim for the BM42 RAG flow) plus per-image OCR for embedded figures.

        Invisible (render mode 3) text is content only over what the page shows (a
        scanner's OCR layer, text drawn as outlines) and only where no OCR of that
        picture replaced it; see _text_source.
        """
        page = self.doc.load_page(page_num)

        # --- IMAGE-authoritative page: the whole-page OCR IS the content. ---
        if (ocr_images and ocr_results_map is not None
                and (page_num, _PAGE_RENDER_XREF) in ocr_results_map):
            render_text = (ocr_results_map.get((page_num, _PAGE_RENDER_XREF)) or "").strip()
            if render_text:
                self._rendered_pages.add(page_num)
                items = [{
                    "type": "text:image_description",
                    "content": f"<image_ocr_result>{render_text}</image_ocr_result>",
                    "page": page_num + 1,
                    "position_y": 0.0,
                }]
                # A page overridden to render OCR keeps whatever real painted text the
                # OCR did not reproduce (verbatim first).
                reason = self._page_routes.get(page_num, (None, None))[1]
                if reason in _VERBATIM_TAIL_REASONS:
                    missing = pdf_routing.missing_painted_lines(page, self._page_measure(page_num), render_text,
                                                                garbled=reason == _REASON_ILLEGIBLE)
                    if missing:
                        items.append({
                            "type": "text:normal",
                            "content": "\n".join(missing),
                            "page": page_num + 1,
                            "position_y": float(page.rect.height),
                        })
                return items
            # Refusal (or empty answer) on a page with ink: say so on the page (ocr_issues names
            # the page too). Either way keep the page's own text layer (verbatim first) rather
            # than drop the page. A blank page needs no marker.
            marker = {
                "type": "text:image_description",
                "content": f"<image_ocr_result>[page {page_num + 1}: OCR returned no content]</image_ocr_result>",
                "page": page_num + 1,
                "position_y": 0.0,
            }
            fallback = self._process_page(page_num, extract_images=False, ocr_images=False)
            if fallback:
                logger.warning(f"{self.pdf_path.name} page {page_num + 1}: OCR of the page render returned "
                               f"no text; keeping the page's own text layer")
            if pdf_routing.uncaptured_ink(page, ()) < pdf_routing.MIN_UNCAPTURED_INK:
                return fallback
            self._unread_pages.add(page_num + 1)
            return [marker] + fallback

        # --- TEXT-authoritative page: rule-based text/tables + per-image OCR. ---
        # The document-wide passes read every page through _text_page: run them (once) before
        # this page's block opens, so blocks never nest.
        self._page_chrome_regions(page_num)
        self._get_first_text_page_num()
        self._document_body_size()
        content_items = []
        with self._text_page(page, ocr_images, ocr_results_map) as text_page:
            # A rotated page's tables are found and read uncropped, in one frame; its CropBox
            # is back before anything else reads the page (see _record_rotated_crop_boxes).
            self._uncrop_for_tables(text_page, page_num)
            try:
                table_items, table_bboxes = self._extract_tables_as_markdown(text_page, page_num)
            finally:
                self._restore_cropbox(text_page, page_num)
            content_items.extend(table_items)
            text_items = self._extract_text_as_markdown(text_page, page_num, table_bboxes)
            content_items.extend(text_items)
        image_items = []
        if extract_images:
            image_items = self._extract_images_simple(
                page, page_num, ocr_images=ocr_images, ocr_results_map=ocr_results_map)
            content_items.extend(image_items)

        content_items = self._reading_order(page, content_items, list(zip(table_items, table_bboxes)), image_items)

        simple_content = []
        for item in content_items:
            if item.type.startswith("text:"):
                entry = {
                    "type": item.type,
                    "content": item.content,
                    "page": item.page,
                    "position_y": item.position_y
                }
                heading = getattr(item, "heading", None)
                if heading is not None:
                    entry["_heading"] = heading  # (font size, outline depth), see _finalize_text_items
                simple_content.append(entry)
            elif item.type == "table":
                simple_content.append({
                    "type": "table",
                    "content": item.content,  # markdown table
                    "page": item.page,
                    "position_y": item.position_y
                })
            elif item.type == "image":
                entry = {
                    "type": "image",
                    "content": item.content,  # base64 data
                    "page": item.page,
                    "position_y": item.position_y
                }
                if item.mime_type:
                    entry["mime_type"] = item.mime_type
                simple_content.append(entry)

        return simple_content

    def _reading_order(self, page, items: List[SimpleContent], tables: List[Tuple[SimpleContent, tuple]],
                       images: List[SimpleContent]) -> List[SimpleContent]:
        """The page's items in reading order (see :mod:`doc2mark.pipelines.pdf_layout`).

        The starting point is the order by ``position_y`` (the top of the text block a piece
        came from, so the pieces of one block stay together). On a ``/Rotate`` page, text,
        pictures and page chrome are measured unrotated and tables as displayed, so the page is
        ordered by where each item is displayed instead. Then pdf_layout reads columns column by
        column when the page clearly shows them. ``tables`` pairs each table item with its box
        (as displayed); ``images`` are the picture items (their boxes are looked up)."""
        regions: Dict[int, pdf_layout.Region] = {}
        for item, bbox in tables:
            regions[id(item)] = pdf_layout.Region(tuple(bbox), kind="table")
        for item, box in zip(images, self._image_item_boxes(page, images) if images else []):
            regions[id(item)] = pdf_layout.Region(box, kind="image")

        rotated = bool(page.rotation % 360)

        def region(item) -> pdf_layout.Region:
            layout = getattr(item, "layout", None) or regions.get(id(item)) or pdf_layout.Region(None)
            if layout.box is None and not rotated:
                # unknown place (a picture whose placement was not found): placed by its height
                layout = replace(layout, box=(0.0, item.position_y, 0.0, item.position_y), anchored=True)
            if item.type == "text:footnote" and not layout.anchored:
                layout = replace(layout, anchored=True)   # footnotes close the page, never a column
            return layout

        if rotated:
            group_tops: Dict[Any, float] = {}
            for item in items:
                layout = region(item)
                if layout.group is not None and layout.box is not None:
                    top = group_tops.get(layout.group)
                    group_tops[layout.group] = layout.box[1] if top is None else min(top, layout.box[1])

            def displayed_top(item) -> float:
                layout = region(item)
                if layout.group is not None and layout.group in group_tops:
                    return group_tops[layout.group]
                return layout.box[1] if layout.box is not None else item.position_y

            items = sorted(items, key=displayed_top)
        else:
            items = sorted(items, key=lambda item: item.position_y)
        try:
            order = pdf_layout.reading_order([region(item) for item in items])
        except Exception as e:   # never lose a page over its layout: keep the top-to-bottom order
            logger.warning(f"Reading order of page {page.number + 1} failed, keeping top-to-bottom order: {e}")
            return items
        return [items[index] for index in order]

    def _lines_region(self, page, lines: List[Dict[str, Any]], group=None, anchored: bool = False) -> pdf_layout.Region:
        """Where text lines stand on the page as displayed: their box (from the glyphs' baselines,
        ``_visual_box``) and each line's width, font size, top and bottom."""
        box, measured = None, []
        try:
            matrix = page.rotation_matrix
            for line in lines:
                if not self._raw_line_text(line).strip():
                    continue
                rect = pymupdf.Rect(self._visual_box(line)) * matrix
                size = max((span.get("size") or 0.0 for span in line.get("spans", [])), default=0.0)
                measured.append((rect.width, size, rect.y0, rect.y1))
                box = rect if box is None else box | rect
        except Exception as e:   # unknown place: the item is placed by its height alone
            logger.debug(f"Line geometry unavailable: {e}")
            box, measured = None, []
        return pdf_layout.Region(tuple(box) if box is not None else None, "text", tuple(measured),
                                 group=group, anchored=anchored)

    def _image_item_boxes(self, page, items: List[SimpleContent]) -> List[Optional[tuple]]:
        """The displayed box of each picture item: the placement of a picture on the page whose
        top is the item's ``position_y``, taken in the order the placements are listed; None
        when there is none (the item is then placed by its height alone)."""
        placements = []
        try:
            for info in page.get_images(full=True):
                placements.extend(page.get_image_rects(info[0]))
        except Exception as e:
            logger.debug(f"Picture placements of page {page.number + 1} unavailable: {e}")
        matrix = page.rotation_matrix
        boxes, used, cursor = [], set(), 0
        for item in items:
            candidates = list(range(cursor, len(placements))) + list(range(cursor))
            match = next((k for k in candidates
                          if k not in used and abs(placements[k].y0 - item.position_y) <= 0.01), None)
            if match is None:
                boxes.append(None)
                continue
            used.add(match)
            cursor = match + 1
            boxes.append(tuple(placements[match] * matrix))
        return boxes

    def _extract_text_as_markdown(self, page, page_num: int, table_bboxes: List[tuple] = None) -> List[SimpleContent]:
        """Extract text blocks and convert to markdown format with text type classification.

        Blocks are filtered line by line first (``_filter_text_blocks``): text inside a
        table is left to the table item, running headers/footers become text:header /
        text:footer items, and overprinted duplicates are emitted once. The remaining blocks
        are regrouped into paragraphs, headings and lists (``_prepare_text_blocks``), then
        classified and rendered (``_text_item_from_block``).
        """
        text_items = []
        self._restore_cropbox(page, page_num)
        # find_tables() reports display (rotated) coordinates, get_text() unrotated ones.
        table_bboxes = self._table_regions_in_text_space(page, table_bboxes or [], page_num)

        # Get text dictionary with formatting info
        text_dict = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_LIGATURES)

        # Body font size (robust: character-weighted, without superscripts, table cells or
        # chart labels) and the largest real font size on the page
        avg_font_size, max_font_size = self._page_font_stats(text_dict, table_bboxes)

        # Get image positions for caption detection
        image_bboxes = self._get_image_bboxes(page)

        blocks, chrome_items = self._filter_text_blocks(page, text_dict["blocks"], table_bboxes, page_num)
        text_items.extend(chrome_items)
        # Regroup into paragraphs, headings and lists, then classify and render each one
        for block in self._prepare_text_blocks(blocks, avg_font_size):
            item = self._text_item_from_block(
                block, avg_font_size, max_font_size, page_num, image_bboxes, table_bboxes
            )
            if item is not None:
                item.layout = self._lines_region(page, block["lines"], group=block.get("number"))
                text_items.append(item)

        return text_items

    def _table_regions_in_text_space(self, page, table_bboxes: List[tuple], page_num: Optional[int] = None) -> List[tuple]:
        """Table bboxes mapped into the coordinate space of ``get_text()``.

        ``find_tables()`` reports bboxes as the page is displayed (``/Rotate`` applied)
        while ``get_text()`` reports unrotated page coordinates. On a rotated page the two
        must be brought together before comparing them, or the table's text is emitted a
        second time and unrelated text where the rotated box lands is dropped. A cropped
        page's tables were found uncropped (``_uncrop_for_tables``): their boxes are derotated
        as for the whole MediaBox, then moved by where the visible area starts. Where that is
        not possible (see ``_record_rotated_crop_boxes``), or when the tables were found on
        another page object than the one uncropped (text read from a copy), none are returned:
        the table's text may then appear twice, but no text is dropped. ``page_num`` is the
        page's index in ``self.doc`` (default ``page.number``).
        """
        number = page.number if page_num is None else page_num
        frame = getattr(self, "_table_frames", {}).pop(number, None)
        if not table_bboxes or not page.rotation:
            return list(table_bboxes)
        uncropped = frame is not None and frame[0] is page.parent and frame[1] == page.xref
        if number in getattr(self, "_rotated_cropped_pages", ()) or (
                not uncropped and (frame is not None or number in getattr(self, "_rotated_crop_boxes", {}))):
            logger.debug(f"Page {number + 1} is rotated and cropped: table text is not suppressed")
            return []
        matrix = frame[2] if uncropped else page.derotation_matrix
        dx, dy = getattr(self, "_crop_offsets", {}).get(number, (0.0, 0.0))
        return [tuple(pymupdf.Rect(bbox) * matrix + (-dx, -dy, -dx, -dy)) for bbox in table_bboxes]

    def _filter_text_blocks(self, page, blocks: List[Dict[str, Any]], table_bboxes: List[tuple],
                            page_num: Optional[int] = None) -> Tuple[List[Dict[str, Any]], List[SimpleContent]]:
        """Split a page's text blocks into content blocks and running header/footer items.

        * A span whose centre lies inside a table bbox is dropped: the table item carries
          it. The other lines of the same PyMuPDF block (a caption or note printed right
          against the table, a title next to a logo box) stay.
        * A running header/footer line (``_detect_page_chrome``) becomes its own item with
          its raw text, so it is never classified as a heading, list item or footnote:
          text:header / text:footer, or text:normal for a line kept as content (a running
          header's first copy, a numbered label, a page number the rule leaves), escaped like
          all body text (``doc2mark.utils.markdown``).
        * Overprinted copies of a line are dropped (``_drop_overprinted_lines``).

        Returns ``(blocks, header/footer items)``: the text blocks in their original order,
        each untouched block as it was and the others with their surviving lines and a
        recomputed bbox. ``page_num`` is the page's index in ``self.doc`` (default ``page.number``).
        """
        page_num = page.number if page_num is None else page_num
        chrome_regions = self._page_chrome_regions(page_num)
        entries: List[List[Any]] = []
        chrome_items: List[SimpleContent] = []
        for index, block in enumerate(blocks):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                chrome = self._chrome_line(line["bbox"], chrome_regions)
                line = self._line_outside_tables(line, table_bboxes)
                if line is None:
                    continue
                if chrome:
                    zone, kept = chrome
                    text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                    # a kept copy is emitted as a paragraph: Markdown, escaped like every other text item
                    chrome_items.append(_TextContent(
                        type="text:normal" if kept else f"text:{zone}",
                        content=escape_markdown_text(text) if kept else text,
                        page=page_num + 1, position_y=line["bbox"][1],
                        layout=self._lines_region(page, [line], anchored=True)))
                else:
                    entries.append([index, line])

        lines_by_block: Dict[int, List[Dict[str, Any]]] = OrderedDict()
        for index, line in self._drop_overprinted_lines(page, entries):
            lines_by_block.setdefault(index, []).append(line)
        kept_blocks = []
        for index, lines in lines_by_block.items():
            block = blocks[index]
            if len(lines) == len(block["lines"]) and all(a is b for a, b in zip(lines, block["lines"])):
                kept_blocks.append(block)
            else:
                kept_blocks.append(dict(block, lines=lines, bbox=self._union_bbox(line["bbox"] for line in lines)))
        return kept_blocks, chrome_items

    def _line_outside_tables(self, line: Dict[str, Any], table_bboxes: List[tuple]) -> Optional[Dict[str, Any]]:
        """``line`` without its spans inside a table bbox; None when no text is left."""
        spans = line.get("spans", [])
        up = self._text_up(line)
        kept = [span for span in spans if not self._point_inside(self._span_anchor(span, up), table_bboxes)]
        if len(kept) == len(spans):
            return line
        if not any(span.get("text", "").strip() for span in kept):
            return None
        return dict(line, spans=kept, bbox=self._union_bbox(span["bbox"] for span in kept))

    @staticmethod
    def _point_inside(point: Tuple[float, float], regions: List[tuple]) -> bool:
        x, y = point
        return any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in regions)

    @staticmethod
    def _text_up(line: Dict[str, Any]) -> Optional[Tuple[int, int]]:
        """The direction the glyph tops point to for an axis-aligned line: ``(0, -1)`` for
        ordinary text, ``(-1, 0)``, ``(0, 1)`` or ``(1, 0)`` for text turned 90/180/270
        degrees; None for slanted text."""
        dx, dy = line.get("dir", (1.0, 0.0))
        if abs(dy) < 1e-3 and abs(abs(dx) - 1) < 1e-3:
            return (0, -1) if dx > 0 else (0, 1)
        if abs(dx) < 1e-3 and abs(abs(dy) - 1) < 1e-3:
            return (-1, 0) if dy < 0 else (1, 0)
        return None

    @staticmethod
    def _span_anchor(span: Dict[str, Any], up: Optional[Tuple[int, int]]) -> Tuple[float, float]:
        """Where a span's glyphs are: on its middle, 0.3 x size above the baseline (mid
        x-height), or the bbox centre for slanted text. Span boxes can be several times taller
        than the text (fonts declaring Word/Cambria-like ascent and descent in PyMuPDF 1.28)."""
        x0, y0, x1, y1 = span["bbox"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if up is None or "origin" not in span or not span.get("size"):
            return cx, cy
        if up[1]:
            return cx, span["origin"][1] + up[1] * 0.3 * span["size"]
        return span["origin"][0] + up[0] * 0.3 * span["size"], cy

    @staticmethod
    def _visual_box(line: Dict[str, Any]) -> Tuple[float, float, float, float]:
        """A line's box from its glyphs' baselines and sizes: across the text it runs from
        0.25 x size below the baseline to 0.8 x size above it (within the reported box), along
        the text it is the reported box. Reported boxes can be several times taller than the
        text, which would make neighbouring lines overlap. Slanted lines keep their bbox."""
        box = list(line["bbox"])
        up = PDFLoader._text_up(line)
        spans = [span for span in line.get("spans", [])
                 if span.get("text", "").strip() and "origin" in span and span.get("size")]
        if not spans or up is None:
            return tuple(box)
        axis = 1 if up[1] else 0  # the axis across the text: y for horizontal lines, x for vertical ones
        ends = [span["origin"][axis] + up[axis] * k * span["size"] for span in spans for k in (0.8, -0.25)]
        low, high = max(box[axis], min(min(ends), box[axis + 2])), min(box[axis + 2], max(max(ends), box[axis]))
        box[axis], box[axis + 2] = low, high
        return tuple(box)

    @staticmethod
    def _union_bbox(bboxes) -> Tuple[float, float, float, float]:
        boxes = list(bboxes)
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    @staticmethod
    def _chrome_line(bbox: tuple, regions: List[Tuple[tuple, str, bool]]) -> Optional[Tuple[str, bool]]:
        """``(zone, kept)`` of the running header/footer line that is this line (IoU >= 0.6), if any."""
        for rect, zone, kept in regions:
            width = min(bbox[2], rect[2]) - max(bbox[0], rect[0])
            height = min(bbox[3], rect[3]) - max(bbox[1], rect[1])
            if width <= 0 or height <= 0:
                continue
            overlap = width * height
            union = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                     + (rect[2] - rect[0]) * (rect[3] - rect[1]) - overlap)
            if union > 0 and overlap / union >= 0.6:
                return zone, kept
        return None

    def _drop_overprinted_lines(self, page, entries: List[List[Any]]) -> List[List[Any]]:
        """Emit text drawn 2-3 times at (nearly) the same place once.

        Fake bold, drop shadows and highlight overlays repeat glyphs on top of each
        other, and PyMuPDF returns every copy as a line of its own. ``entries`` is
        ``[[block index, line], ...]`` in extraction order. Two lines are compared only
        when they overlap both ways (vertically by 70% of the shorter one, measured from the
        baselines: ``_visual_box``) and have the same font size; then

        * same text, horizontal overlap of 70% of the narrower line: the later copy goes;
        * one text inside the other and every glyph of it drawn on the same glyph of the
          other (``_glyphs_coincide``): the shorter line goes (a highlighted phrase);
        * a line that starts inside another one and whose first characters are drawn on
          that line's last characters (one fake-bold word in a sentence): those
          characters are cut from it.

        Repeats that are not drawn on top of each other ("No No", "10 10", one word on two
        lines, clipped spreadsheet cells that merely overlap) are real text and stay.
        """
        if len(entries) < 2:
            return entries
        boxes = [self._visual_box(line) for _, line in entries]
        order = sorted(range(len(entries)), key=lambda k: boxes[k][1])
        dropped = set()
        for position, i in enumerate(order):
            for j in order[position + 1:]:
                if i in dropped or boxes[j][1] >= boxes[i][3]:
                    break
                a, b = boxes[i], boxes[j]
                if j in dropped or min(a[2], b[2]) <= max(a[0], b[0]) or not self._overlaps_vertically(a, b):
                    continue
                first, second = sorted((i, j))
                verdict = self._overprint_verdict(page, entries[first][1], entries[second][1])
                if verdict is None:
                    continue
                target = first if verdict[1] == "first" else second
                if verdict[0] == "drop":
                    dropped.add(target)
                else:
                    entries[target][1] = self._trim_line_start(entries[target][1], verdict[2])
                    boxes[target] = self._visual_box(entries[target][1])
        return [entry for k, entry in enumerate(entries) if k not in dropped]

    @staticmethod
    def _overlaps_vertically(a: tuple, b: tuple) -> bool:
        overlap = min(a[3], b[3]) - max(a[1], b[1])
        return overlap > 0 and overlap >= 0.7 * min(a[3] - a[1], b[3] - b[1])

    def _overprint_verdict(self, page, first: Dict[str, Any], second: Dict[str, Any]) -> Optional[tuple]:
        """How two overlapping lines relate; ``first`` was extracted first.

        Returns None (not an overprint), ``("drop", "first" | "second")`` or
        ``("trim", "first" | "second", characters)``; see ``_drop_overprinted_lines``.
        """
        lines = []
        for name, line in (("first", first), ("second", second)):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            size = max((span.get("size", 0.0) for span in line.get("spans", [])), default=0.0)
            if not text or size <= 0:
                return None
            lines.append((name, text, line["bbox"], line, size))
        (_, text_a, box_a, _, size_a), (_, text_b, box_b, _, size_b) = lines
        if abs(size_a - size_b) > 0.1 * max(size_a, size_b):
            return None
        if " ".join(text_a.split()) == " ".join(text_b.split()):
            overlap = min(box_a[2], box_b[2]) - max(box_a[0], box_b[0])
            if overlap >= 0.7 * min(box_a[2] - box_a[0], box_b[2] - box_b[0]):
                return "drop", "second"
            return None

        # One text drawn a second time over its own occurrence inside the other line.
        short, long = sorted(lines, key=lambda item: len(item[1]))
        if (len(short[1]) >= 2 and short[1] in long[1]
                and short[2][0] >= long[2][0] - 2 and short[2][2] <= long[2][2] + 2):
            if self._glyphs_coincide(page, short[3], len(short[1])):
                return "drop", short[0]
            return None

        # A left-to-right line starting inside another and repeating that line's last characters.
        left, right = sorted(lines, key=lambda item: item[2][0])
        if (all(self._text_up(item[3]) == (0, -1) for item in lines)
                and left[2][0] < right[2][0] < left[2][2] < right[2][2]):
            for count in range(min(len(left[1]), len(right[1]) - 1), 1, -1):
                if left[1].endswith(right[1][:count]):
                    if self._glyphs_coincide(page, right[3], count):
                        return "trim", right[0], count
                    break
        return None

    @staticmethod
    def _glyphs_coincide(page, line: Dict[str, Any], count: int) -> bool:
        """Is each of the first ``count`` characters of ``line`` (after leading blanks) drawn on
        the same character of another line, its origin (baseline point) within 2pt? Read from
        the glyph positions of the line's own area (``rawdict``); when the copies cannot be
        told apart, the answer is no."""
        wanted = [c for c in "".join(span.get("text", "") for span in line.get("spans", [])).lstrip()[:count]
                  if not c.isspace()]
        clip = pymupdf.Rect(line["bbox"]) + (-1, -1, 1, 1)
        raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_LIGATURES, clip=clip)
        raw_lines = [[char for span in raw_line.get("spans", []) for char in span.get("chars", [])
                      if not char["c"].isspace()]
                     for block in raw.get("blocks", []) if block.get("type") == 0
                     for raw_line in block.get("lines", [])]
        own = next((chars for chars in raw_lines if [char["c"] for char in chars[:len(wanted)]] == wanted), None)
        if not wanted or own is None:
            return False
        others = [char for chars in raw_lines if chars is not own for char in chars]
        return all(any(other["c"] == glyph["c"] and abs(other["origin"][0] - glyph["origin"][0]) <= 2.0
                       and abs(other["origin"][1] - glyph["origin"][1]) <= 2.0
                       for other in others)
                   for glyph in own[:len(wanted)])

    @staticmethod
    def _trim_line_start(line: Dict[str, Any], count: int) -> Dict[str, Any]:
        """``line`` without its first ``count`` characters after any leading blanks."""
        text = "".join(span.get("text", "") for span in line["spans"])
        remaining = len(text) - len(text.lstrip()) + count
        spans = []
        for span in line["spans"]:
            span_text = span.get("text", "")
            if remaining >= len(span_text):
                remaining -= len(span_text)
                continue
            if remaining:
                x0, y0, x1, y1 = span["bbox"]
                cut = x0 + (x1 - x0) * remaining / len(span_text)
                span = dict(span, text=span_text[remaining:], bbox=(cut, y0, x1, y1))
                if "origin" in span:
                    span["origin"] = (cut, span["origin"][1])
                remaining = 0
            spans.append(span)
        return dict(line, spans=spans, bbox=PDFLoader._union_bbox(span["bbox"] for span in spans))

    def _get_image_bboxes(self, page) -> List[tuple]:
        """Get all image bounding boxes on the page"""
        image_bboxes = []
        try:
            image_list = page.get_images(full=True)
            for img_info in image_list:
                xref = img_info[0]
                try:
                    img_rects = page.get_image_rects(xref)
                    for img_rect in img_rects:
                        image_bboxes.append((img_rect.x0, img_rect.y0, img_rect.x1, img_rect.y1))
                except Exception as e:
                    logger.debug(f"Failed to get image rects: {e}")
        except Exception as e:
            logger.debug(f"Failed to get image bboxes: {e}")
        return image_bboxes

    def _is_near_image_or_table(self, bbox: tuple, image_bboxes: List[tuple], table_bboxes: List[tuple],
                                threshold: float = 50) -> bool:
        """True when the text box sits at most ``threshold`` points above or below an image or a
        table (without overlapping it) and overlaps it horizontally or is roughly centred on it."""
        x0, y0, x1, y1 = bbox
        text_center_x = (x0 + x1) / 2
        for other_x0, other_y0, other_x1, other_y1 in list(image_bboxes or []) + list(table_bboxes or []):
            gap = max(other_y0 - y1, y0 - other_y1)
            if gap < -2 or gap > threshold:
                continue
            horizontal_overlap = min(x1, other_x1) - max(x0, other_x0)
            if horizontal_overlap > 0 or abs(text_center_x - (other_x0 + other_x1) / 2) < 100:
                return True
        return False

    # --- font statistics ------------------------------------------------------------------------

    @staticmethod
    def _is_superscript_span(span: Dict[str, Any]) -> bool:
        """A short span that PyMuPDF flags as superscript (exponent, footnote mark, ordinal). A
        long flagged span is a raised phrase on a designed page, not a superscript."""
        return bool((span.get("flags", 0) or 0) & pymupdf.TEXT_FONT_SUPERSCRIPT) \
            and 0 < len((span.get("text") or "").strip()) <= 6

    @classmethod
    def _span_style(cls, span: Dict[str, Any]) -> Tuple[bool, bool, bool]:
        """(bold, italic, superscript) of a PyMuPDF span. Bold also comes from MuPDF's bold
        character flag (synthetic bold) and from the font name (``Calibri-Bold``)."""
        flags = span.get("flags", 0) or 0
        font = span.get("font") or ""
        bold = (bool(flags & pymupdf.TEXT_FONT_BOLD) or bool((span.get("char_flags") or 0) & _CHAR_FLAG_BOLD)
                or bool(_BOLD_FONT_NAME.search(font)))
        italic = bool(flags & pymupdf.TEXT_FONT_ITALIC) or bool(_ITALIC_FONT_NAME.search(font))
        return bold, italic, cls._is_superscript_span(span)

    @staticmethod
    def _span_in_boxes(span: Dict[str, Any], boxes: List[tuple]) -> bool:
        bbox = span.get("bbox")
        if not bbox or not boxes:
            return False
        center_x, center_y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        return any(x0 <= center_x <= x1 and y0 <= center_y <= y1 for x0, y0, x1, y1 in boxes)

    def _size_weights(self, text_dict: Dict[str, Any], table_bboxes: List[tuple] = None,
                      running_only: bool = False) -> Tuple[Dict[float, float], float]:
        """Characters per font size (superscripts and table cells left out), and the largest size
        of a span with at least two letters (so drop caps, bullets and KPI numbers do not count).
        With ``running_only`` only lines of running text count (at least ``_RUNNING_TEXT_UNITS``
        characters, a CJK character counting twice): table cells, headings and labels are short."""
        weights: Dict[float, float] = defaultdict(float)
        max_size = 0.0
        for block in text_dict.get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                if running_only and _text_units(self._raw_line_text(line)) < _RUNNING_TEXT_UNITS:
                    continue
                for span in line.get("spans", []):
                    size = span.get("size", 0) or 0
                    text = span.get("text") or ""
                    chars = sum(1 for char in text if not char.isspace())
                    if size <= 0 or not chars or self._is_superscript_span(span):
                        continue
                    if table_bboxes and self._span_in_boxes(span, table_bboxes):
                        continue
                    weights[size] += chars
                    if sum(1 for char in text if char.isalpha()) >= 2:
                        max_size = max(max_size, size)
        return weights, max_size

    def _page_font_stats(self, text_dict: Dict[str, Any], table_bboxes: List[tuple] = None) -> Tuple[float, float]:
        """Body font size and largest font size of a page, for heading detection.

        The body size is the character-weighted median size of the page's text without
        superscripts and table cells, so chart labels, footnote markers and small table text do
        not pull it down. A page with little text (a cover, a slide) uses the document's body
        size instead (``_document_body_size``)."""
        weights, max_size = self._size_weights(text_dict, table_bboxes)
        body = _weighted_median(weights)
        if sum(weights.values()) < _MIN_PAGE_BODY_CHARS:
            document_body = self._document_body_size()
            if document_body > 0:
                body = document_body
        if body <= 0:
            body = 12.0
        return body, max(max_size, body)

    def _document_body_size(self) -> float:
        """Body font size of the whole document (0.0 without a document), computed once per
        conversion, before a page's ``_text_page`` block opens (``_process_page``): the
        character-weighted median size of the running text of at most ``_PROFILE_MAX_PAGES``
        pages (``_sampled_pages``), each read through ``_text_page`` and dropped before the next
        is read. Only lines of running text count
        (``_size_weights``): headings, chart labels and most table cells are shorter. Table
        regions themselves are not detected here (that would run ``find_tables`` on every sampled
        page a second time). A document with little running text uses all of its text."""
        if getattr(self, "_body_size", None) is not None:
            return self._body_size
        self._body_size = 0.0
        doc = getattr(self, "doc", None)
        if doc is None:
            return 0.0
        running: Dict[float, float] = defaultdict(float)
        every: Dict[float, float] = defaultdict(float)
        try:
            for number in _sampled_pages(len(doc)):
                with self._text_page(doc.load_page(number)) as page:
                    text_dict = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_LIGATURES)
                for totals, running_only in ((running, True), (every, False)):
                    for size, chars in self._size_weights(text_dict, running_only=running_only)[0].items():
                        totals[size] += chars
        except Exception as e:
            logger.debug(f"Document body size unavailable: {e}")
        self._body_size = _weighted_median(running if sum(running.values()) >= _MIN_PAGE_BODY_CHARS else every)
        logger.debug(f"Document body size: {self._body_size:.1f}pt")
        return self._body_size

    # --- regrouping PyMuPDF blocks ----------------------------------------------------------------

    @staticmethod
    def _raw_line_text(line: Dict[str, Any]) -> str:
        return "".join(span.get("text") or "" for span in line.get("spans", []))

    @staticmethod
    def _boxes_union(*boxes) -> Optional[tuple]:
        boxes = [box for box in boxes if box]
        if not boxes:
            return None
        return (min(box[0] for box in boxes), min(box[1] for box in boxes),
                max(box[2] for box in boxes), max(box[3] for box in boxes))

    def _block_from_lines(self, block: Dict[str, Any], lines: List[Dict[str, Any]]) -> Dict[str, Any]:
        bbox = self._boxes_union(*(line.get("bbox") for line in lines)) or block.get("bbox")
        new_block = {key: value for key, value in block.items() if key not in ("lines", "bbox", "_views")}
        new_block["lines"] = lines
        if bbox is not None:
            new_block["bbox"] = bbox
        return new_block

    def _prepare_text_blocks(self, blocks: List[Dict[str, Any]], body_size: float) -> List[Dict[str, Any]]:
        """Regroup PyMuPDF text blocks into the units Markdown needs.

        - a list marker drawn as its own line (Word separates ``1.`` or U+F0B7 from the item
          text with a tab) is joined to the item text on the same row;
        - leading or trailing heading lines (larger, bolder or coloured) are split off the
          paragraph MuPDF grouped them with;
        - list items are split off the lead-in line before them and the paragraph after them;
        - a drop cap is put back in front of the word it starts;
        - consecutive blocks that continue one paragraph (a CJK paragraph exported line by
          line), one heading (a title over two blocks) or one list are merged.
        The blocks keep the PyMuPDF shape (``bbox``, ``lines``, ``spans``); non-text blocks and
        empty lines are dropped. ``_top`` is the top of the PyMuPDF block a piece came from,
        ``_views`` holds the analysed lines (``_line_view``, computed once per line),
        ``_prev_item`` / ``_next_item`` the list lines of the neighbouring blocks, so a lettered
        item can see the rest of its sequence, and ``_wrapped_start`` marks a block whose first
        line the block before wrapped onto (double-spaced text has a block per line)."""
        memo: Dict[int, Tuple[Dict[str, Any], Optional[_LineView]]] = {}

        def view_of(line: Dict[str, Any]) -> Optional[_LineView]:
            cached = memo.get(id(line))
            if cached is None or cached[0] is not line:
                cached = memo[id(line)] = (line, self._line_view(line))
            return cached[1]

        pieces: List[Dict[str, Any]] = []
        for block in blocks:
            if block.get("type", 0) != 0:
                continue
            lines = [line for line in block.get("lines", []) if self._raw_line_text(line).strip()]
            if lines:
                top = (block.get("bbox") or self._boxes_union(*(line.get("bbox") for line in lines)) or (0.0, 0.0))[1]
                block = self._block_from_lines(block, self._join_marker_lines(lines))
                block["_top"] = top
                pieces.extend(self._split_block_by_style(block, view_of))
        prepared: List[Dict[str, Any]] = []
        for index, piece in enumerate(pieces):
            before = (self._last_item_text(pieces[index - 1])
                      if index and self._blocks_adjacent(pieces[index - 1], piece, view_of) else "")
            after = (self._raw_line_text(pieces[index + 1]["lines"][0]).strip()
                     if index + 1 < len(pieces) and self._blocks_adjacent(piece, pieces[index + 1], view_of) else "")
            prepared.extend(self._split_list_items(piece, view_of, before, after))
        prepared = self._merge_drop_caps(prepared, body_size, view_of)
        prepared = self._merge_continuation_blocks(prepared, body_size, view_of)
        for index, block in enumerate(prepared):
            block["_views"] = [view_of(line) for line in block["lines"]]
            block.pop("_prev_item", None)
            block.pop("_next_item", None)
            block.pop("_wrapped_start", None)
            if index > 0 and self._blocks_adjacent(prepared[index - 1], block, view_of):
                block["_prev_item"] = self._last_item_text(prepared[index - 1])
                if self._wraps_onto_block(prepared[index - 1], block, view_of):
                    block["_wrapped_start"] = True
            if index + 1 < len(prepared) and self._blocks_adjacent(block, prepared[index + 1], view_of):
                block["_next_item"] = self._raw_line_text(prepared[index + 1]["lines"][0]).strip()
        return prepared

    def _last_item_text(self, block: Dict[str, Any]) -> str:
        """Text of the last line of ``block`` that starts with a list marker ("" if none)."""
        for line in reversed(block["lines"]):
            text = self._raw_line_text(line).strip()
            if _parse_list_marker(text) is not None:
                return text
        return ""

    def _join_marker_lines(self, lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Join a line that is only a list marker to the item text that follows it on the same row."""
        joined: List[Dict[str, Any]] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            text = self._raw_line_text(line)
            if index + 1 < len(lines) and _MARKER_ONLY.fullmatch(text.strip()) \
                    and self._same_row(line, lines[index + 1]):
                following = lines[index + 1]
                spans = list(line.get("spans", []))
                following_spans = list(following.get("spans", []))
                if not text[-1:].isspace() and following_spans:
                    spans.append(dict(following_spans[0], text=" "))
                merged = dict(following)
                merged["spans"] = spans + following_spans
                bbox = self._boxes_union(line.get("bbox"), following.get("bbox"))
                if bbox is not None:
                    merged["bbox"] = bbox
                joined.append(merged)
                index += 2
                continue
            joined.append(line)
            index += 1
        return joined

    def _same_row(self, first: Dict[str, Any], second: Dict[str, Any]) -> bool:
        a, _ = self._line_geometry(first)
        b, _ = self._line_geometry(second)
        if not a or not b:
            return True
        overlap = min(a[3], b[3]) - max(a[1], b[1])
        return b[0] >= a[0] and overlap >= 0.5 * min(a[3] - a[1], b[3] - b[1])

    def _split_block_by_style(self, block: Dict[str, Any], view_of: Optional[Callable] = None) -> List[Dict[str, Any]]:
        """Split heading lines off a block whose other lines have a different style."""
        lines = block["lines"]
        if len(lines) < 2:
            return [block]
        view_of = view_of or self._line_view
        views = [view_of(line) for line in lines]
        if any(view is None for view in views):
            return [block]
        groups: List[List[int]] = []
        for index, view in enumerate(views):
            key = (_size_key(view.size), view.bold, _is_chromatic(view.color))
            previous = views[groups[-1][-1]] if groups else None
            if previous is not None and key == (_size_key(previous.size), previous.bold, _is_chromatic(previous.color)):
                groups[-1].append(index)
            else:
                groups.append([index])
        if len(groups) == 1:
            return [block]
        cuts = []
        for number in range(len(groups) - 1):
            upper, lower = groups[number], groups[number + 1]
            # A heading starts the block or follows a finished sentence; it never interrupts one
            # (a bold phrase that fills a line in the middle of a paragraph stays in it).
            upper_starts_fresh = upper[0] == 0 or bool(_SENTENCE_END.search(views[upper[0] - 1].text.strip()))
            upper_ends_sentence = bool(_SENTENCE_END.search(views[upper[-1]].text.strip()))
            if (self._is_heading_run(views, upper, lower) and upper_starts_fresh) or (
                    self._is_heading_run(views, lower, upper) and upper_ends_sentence):
                cuts.append(lower[0])
        if not cuts:
            return [block]
        pieces, start = [], 0
        for cut in cuts + [len(lines)]:
            if lines[start:cut]:
                pieces.append(self._block_from_lines(block, lines[start:cut]))
            start = cut
        return pieces

    @staticmethod
    def _is_heading_run(views: List[_LineView], run: List[int], other: List[int]) -> bool:
        """True when the lines ``run`` look like a heading next to the lines ``other``."""
        if len(run) > 3:
            return False
        texts = [views[index].text.strip() for index in run]
        marker = _parse_list_marker(texts[0])
        if any(len(text) > 100 for text in texts) or (marker is not None and marker.kind in ("glyph", "bullet")):
            return False  # bulleted lines are list items, never headings
        size = max(views[index].size for index in run)
        other_size = max(views[index].size for index in other)
        if other_size <= 0:
            return False
        if size >= other_size * 1.08:
            return True
        if size < other_size * 0.95 or (_SENTENCE_END.search(texts[-1]) and not texts[-1].endswith(("?", "？"))):
            return False
        bold = all(views[index].solid_bold for index in run) and not any(views[index].bold for index in other)
        colored = (len(run) <= 2 and all(_is_chromatic(views[index].color) for index in run)
                   and not any(_is_chromatic(views[index].color) for index in other))
        return bold or colored

    def _split_list_items(self, block: Dict[str, Any], view_of: Callable, previous_item: str = "",
                          next_line: str = "") -> List[Dict[str, Any]]:
        """Split a block where a list starts or ends inside it: before the first item after a
        lead-in line, and before a line after an item that does not continue the item (see
        ``_continues_item``). Word and LibreOffice exports often put a lead-in sentence, its
        bullets and the next paragraph in one block. ``previous_item`` / ``next_line`` are the
        list lines of the neighbouring blocks, so an ``(a)`` next to its lead-in joins the ``(b)``
        of the next block. Footnotes grouped in one block are split before each raised number. A
        piece that follows a list or starts a footnote is marked ``_starts_paragraph``, so
        ``_merge_continuation_blocks`` keeps it apart."""
        lines = block["lines"]
        if len(lines) < 2:
            return [block]
        views = [view_of(line) for line in lines]
        if any(view is None for view in views):
            return [block]
        markers = self._line_markers(views, previous_item, next_line)
        wrapped = self._wrapped_onto(views)
        # A line is "between items" when a later line of the block, or the first line of the next
        # block, is an item of the same list.
        last = max((index for index, marker in enumerate(markers) if marker is not None), default=None)
        continued = last is not None and _follows(markers[last], _parse_list_marker(next_line))
        between = [False] * len(views)
        later = continued
        for index in range(len(views) - 1, -1, -1):
            between[index] = later
            later = later or markers[index] is not None
        layout = self._list_layout(views, markers, between)
        cuts: List[int] = []
        fresh = set()
        item: Optional[Tuple[_LineView, _ListMarker]] = None
        for index, view in enumerate(views):
            if index and _starts_with_raised_number(view):
                cuts.append(index)
                fresh.add(index)
                item = None
            elif markers[index] is not None:
                if item is None and index > 0:
                    cuts.append(index)
                item = (view, markers[index])
            elif item is not None and not self._continues_item(
                    item, views[index - 1], view, wrapped[index], layout, between[index]):
                cuts.append(index)
                fresh.add(index)
                item = None
        if not cuts:
            return [block]
        pieces, start = [], 0
        for cut in cuts + [len(lines)]:
            piece = self._block_from_lines(block, lines[start:cut])
            if start in fresh:
                piece["_starts_paragraph"] = True
            pieces.append(piece)
            start = cut
        return pieces

    def _line_markers(self, lines: List[_LineView], previous_item: str = "", next_line: str = "",
                      wrapped_start: bool = False) -> List[Optional[_ListMarker]]:
        """Per line, the list marker that makes it a list item, or None (``_item_markers``). A
        marker other than a bullet glyph that starts a wrapped line of running text is text
        (``… was`` / ``87. Management …``, spaced en dashes): the line before ends no sentence or
        lead-in, the marker would not have fitted at its end (``_wrapped_onto``; for the first
        line, ``wrapped_start``) and no item of the same list comes before or after it
        (``_has_list_context``)."""
        markers = [_parse_list_marker(line.text) for line in lines]
        wrapped = self._wrapped_onto(lines)
        for index, marker in enumerate(markers):
            if marker is None or marker.kind == "glyph":
                continue
            if index == 0 and not wrapped_start:
                continue
            if index and (not wrapped[index] or _LEAD_IN_END.search(lines[index - 1].text.strip())):
                continue
            if not self._has_list_context(lines, markers, index, previous_item, next_line):
                markers[index] = None
        return _item_markers(markers, previous_item, next_line)

    @staticmethod
    def _has_list_context(lines: List[_LineView], markers: List[Optional[_ListMarker]], index: int,
                          previous_item: str, next_line: str) -> bool:
        """True when the marker of line ``index`` belongs to a list: an earlier item that it
        follows (``previous_item``: the last item line of the block before) or a later item that
        follows it (``next_line``: the first line of the block after) exists. Bulleted items hang,
        so two bullets count only when the lines between them are indented past the bullet."""
        marker = markers[index]

        def hang_between(first: int, second: int) -> bool:
            x0 = lines[first].bbox[0] if lines[first].bbox else None
            return (marker.kind != "bullet" or x0 is None
                    or all(line.bbox is not None and line.bbox[0] > x0 + 2 for line in lines[first + 1:second]))

        if any(markers[other] is not None and _follows(markers[other], marker) and hang_between(other, index)
               for other in range(index)):
            return True
        if any(markers[other] is not None and _follows(marker, markers[other]) and hang_between(index, other)
               for other in range(index + 1, len(markers))):
            return True
        return _follows(_parse_list_marker(previous_item), marker) or _follows(marker, _parse_list_marker(next_line))

    @staticmethod
    def _list_layout(lines: List[_LineView], markers: List[Optional[_ListMarker]],
                     between: List[bool]) -> Optional[str]:
        """How the lists of a block continue their items: ``"hanging"`` when a line right after
        an item, or between two items, is indented past the item's marker; ``"flush"`` when a line
        between two items starts at the marker's margin; None when the block does not show it."""
        hanging = flush = False
        marker_x = None
        for index, line in enumerate(lines):
            if markers[index] is not None:
                marker_x = line.bbox[0] if line.bbox else None
                continue
            if marker_x is None or not line.bbox:
                continue
            if line.bbox[0] > marker_x + 2 and (between[index] or markers[index - 1] is not None):
                hanging = True
            elif abs(line.bbox[0] - marker_x) <= 2 and between[index]:
                flush = True
        return "hanging" if hanging else "flush" if flush else None

    @staticmethod
    def _wrapped_onto(lines: List[_LineView], right_edge: Optional[float] = None) -> List[bool]:
        """Per line, True when the line before it wrapped onto it: the first word of the line
        would not have fitted at the end of the line before, within the right edge (by default
        the block's; word processors break lines this way)."""
        if right_edge is None:
            right_edge = max((line.bbox[2] for line in lines if line.bbox), default=0.0)
        wrapped = [False]
        for previous, line in zip(lines, lines[1:]):
            text = line.text.strip()
            same_row = (previous.baseline is not None and line.baseline is not None
                        and line.baseline - previous.baseline < 0.5 * max(previous.size, line.size, 1.0))
            if not previous.bbox or not line.bbox or not text or same_row:
                wrapped.append(False)
                continue
            word = text[:1] if _is_cjk(text[:1]) else text.split()[0]
            char_width = (line.bbox[2] - line.bbox[0]) / max(len(text), 1)
            wrapped.append(previous.bbox[2] + char_width * (len(word) + 1) > right_edge)
        return wrapped

    def _wraps_onto_block(self, upper: Dict[str, Any], lower: Dict[str, Any], view_of: Callable) -> bool:
        """True when the last line of ``upper`` wrapped onto the first line of ``lower``: same
        style, no sentence or lead-in end, and the first word would not have fitted. A block that
        holds list items is not a paragraph: a marker after it starts the next item or list."""
        if self._last_item_text(upper):
            return False
        views = [view for view in map(view_of, upper["lines"] + lower["lines"]) if view is not None and view.bbox]
        last, first = view_of(upper["lines"][-1]), view_of(lower["lines"][0])
        if last is None or first is None or not last.bbox or not first.bbox or not views:
            return False
        if abs(last.size - first.size) > 0.5 or last.bold != first.bold or _LEAD_IN_END.search(last.text.strip()):
            return False
        return self._wrapped_onto([last, first], max(view.bbox[2] for view in views))[1]

    @staticmethod
    def _continues_item(item: Tuple[_LineView, _ListMarker], previous: _LineView, line: _LineView,
                        wrapped: bool, layout: Optional[str], between: bool) -> bool:
        """True when ``line`` (without a list marker) continues the list item that starts with the
        line ``item``:

        - it is indented past the item's marker (a hanging indent), or it goes on with a sentence
          (lowercase after a line that ends none);
        - in a list whose items hang (``layout``), any other line starts a paragraph;
        - left of the marker: numbered or lettered clauses whose number is indented like a first
          line, when the line sits between two items or the line before wrapped onto it;
        - at the marker's margin: between two items of the list (a list typed flush), or when the
          line before wrapped onto it in a flush list, or in CJK text (which wraps anywhere)."""
        view, marker = item
        if not view.bbox or not line.bbox:
            return True
        if line.bbox[0] > view.bbox[0] + 2:
            return True
        if line.text.lstrip()[:1].islower() and not _SENTENCE_END.search(previous.text.strip()):
            return True
        if layout == "hanging":
            return False
        if line.bbox[0] < view.bbox[0] - 2:
            return marker.kind in ("ordered", "enum") and (between or wrapped)
        if between:
            return True
        return wrapped and (layout == "flush" or _is_cjk(line.text.lstrip()[:1]))

    def _merge_drop_caps(self, blocks: List[Dict[str, Any]], body_size: float,
                         view_of: Optional[Callable] = None) -> List[Dict[str, Any]]:
        """Put a drop cap (a one-letter block at least twice the body size) back in front of the
        lowercase word it starts: the first line of a paragraph of two or more lines that starts
        just right of the letter, within the letter's height."""
        view_of = view_of or self._line_view
        # Paragraphs that start with a lowercase letter are rare, so each letter checks them all.
        paragraphs = []
        for index, block in enumerate(blocks):
            first = view_of(block["lines"][0]) if len(block["lines"]) >= 2 else None
            if first is not None and first.bbox and first.text.lstrip()[:1].islower():
                paragraphs.append((index, first.bbox))
        removed, taken = set(), set()
        for index, block in enumerate(blocks if paragraphs else []):
            if len(block["lines"]) != 1:
                continue
            view = view_of(block["lines"][0])
            if view is None or not view.bbox or not (len(view.text.strip()) == 1 and view.text.strip().isalpha()) \
                    or view.size < 2 * body_size:
                continue
            box = view.bbox
            for other_index, first_box in paragraphs:
                if other_index in taken:
                    continue
                center_y = (first_box[1] + first_box[3]) / 2
                if -2 <= first_box[0] - box[2] <= 2 * body_size and box[1] - 2 <= center_y <= box[3]:
                    other = blocks[other_index]
                    first = other["lines"][0]
                    spans = list(first.get("spans", []))
                    letter = dict(spans[0], text=view.text.strip()) if spans else {"text": view.text.strip()}
                    merged_line = dict(first)
                    merged_line["spans"] = [letter] + spans
                    other["lines"] = [merged_line] + list(other["lines"][1:])
                    other["bbox"] = self._boxes_union(other.get("bbox"), block.get("bbox"))
                    removed.add(index)
                    taken.add(other_index)
                    break
        return [block for index, block in enumerate(blocks) if index not in removed]

    def _merge_continuation_blocks(self, blocks: List[Dict[str, Any]], body_size: float,
                                   view_of: Optional[Callable] = None) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        for block in blocks:
            if merged and self._continues_block(merged[-1], block, body_size, view_of):
                merged[-1] = self._block_from_lines(merged[-1], merged[-1]["lines"] + block["lines"])
            else:
                merged.append(block)
        return merged

    def _continues_block(self, previous: Dict[str, Any], block: Dict[str, Any], body_size: float,
                         view_of: Optional[Callable] = None) -> bool:
        """True when ``block`` continues the paragraph, heading or list of ``previous``."""
        if not previous.get("bbox") or not block.get("bbox") or block.get("_starts_paragraph"):
            return False
        view_of = view_of or self._line_view
        last = view_of(previous["lines"][-1])
        first = view_of(block["lines"][0])
        if last is None or first is None or last.baseline is None or first.baseline is None:
            return False
        if _starts_with_raised_number(first):
            return False  # the next footnote
        size = max(last.size, first.size)
        if abs(last.size - first.size) > 0.5 or size <= 0:
            return False
        pitch = first.baseline - last.baseline   # baseline to baseline
        if pitch < 0.8 * size or pitch > 2.2 * size:
            return False
        first_marker = _parse_list_marker(first.text)
        if first_marker is not None:
            # The next item of the list in ``previous`` (not a numbered heading below another one)
            leading = view_of(previous["lines"][0])
            if leading is None or _parse_list_marker(leading.text) is None or size >= body_size * 1.15:
                return False
            if leading.bbox and first.bbox[0] < leading.bbox[0] - 2:
                return False
            # the next item of that list: a bullet after bullets, 5. after 4., b) after a)
            return _follows(_parse_list_marker(self._last_item_text(previous)), first_marker)
        if last.bold != first.bold or _is_chromatic(last.color) != _is_chromatic(first.color):
            return False
        if _SENTENCE_END.search(last.text.strip()):
            return False
        # An indented line starts a new paragraph; the line after an indented first line starts
        # further left, at the margin.
        left_aligned = last.bbox[0] - 4 * size <= first.bbox[0] <= last.bbox[0] + 3
        if size >= body_size * 1.15 or last.bold:
            # A heading drawn as separate blocks, one per line (a title over two lines).
            centred = abs((first.bbox[0] + first.bbox[2]) / 2 - (last.bbox[0] + last.bbox[2]) / 2) <= size
            return ((left_aligned or centred) and pitch <= 1.6 * size
                    and len(previous["lines"]) + len(block["lines"]) <= 3)
        # A paragraph continues when its previous line runs to the right edge of the column.
        right_edge = max(previous["bbox"][2], block["bbox"][2])
        return left_aligned and last.bbox[2] >= right_edge - 1.5 * size

    def _blocks_adjacent(self, upper: Dict[str, Any], lower: Dict[str, Any],
                         view_of: Optional[Callable] = None) -> bool:
        view_of = view_of or self._line_view
        last = view_of(upper["lines"][-1])
        first = view_of(lower["lines"][0])
        if last is None or first is None or last.baseline is None or first.baseline is None:
            return True
        return 0 < first.baseline - last.baseline <= 3.2 * max(last.size, first.size, 1.0)

    # --- block analysis -----------------------------------------------------------------------------

    def _line_view(self, line: Dict[str, Any]) -> Optional[_LineView]:
        spans = [(span, (span.get("text") or "").translate(_LIGATURES)) for span in line.get("spans", [])]
        spans = [(span, text) for span, text in spans if text]
        raised_spans = self._raised_small_spans([span for span, _ in spans])
        runs: List[_Run] = []
        size = bold_chars = italic_chars = total = 0
        sizes = []
        colors: Dict[int, int] = defaultdict(int)
        before = ""
        for index, (span, text) in enumerate(spans):
            bold, italic, raised = self._span_style(span)
            raised = raised or index in raised_spans
            # ordinal suffixes (1st) and trademark signs are raised but read inline
            runs.append(_Run(text, bold, italic, raised and not _stays_inline(text, before)))
            before += text
            chars = sum(1 for char in text if not char.isspace())
            sizes.append(span.get("size", 0) or 0)
            if not chars or raised:
                continue
            size = max(size, span.get("size", 0) or 0)
            total += chars
            bold_chars += chars if bold else 0
            italic_chars += chars if italic else 0
            colors[span.get("color", 0) or 0] += chars
        text = "".join(run.text for run in runs)
        if not text.strip():
            return None
        bbox, baseline = self._line_geometry(line)
        return _LineView(
            runs=runs,
            text=text,
            size=size or max(sizes or [0]),
            bold=total > 0 and bold_chars * 2 > total,
            italic=total > 0 and italic_chars * 2 > total,
            color=max(colors, key=colors.get) if colors else 0,
            bbox=bbox,
            baseline=baseline,
            solid_bold=total > 0 and bold_chars >= 0.9 * total,
        )

    @classmethod
    def _raised_small_spans(cls, spans: List[Dict[str, Any]]) -> set:
        """Indexes of short spans set smaller and higher than the rest of their line: superscripts
        PyMuPDF did not flag (it compares with the previous character, so a footnote number that
        starts a line is missed)."""
        measured = [(index, span) for index, span in enumerate(spans)
                    if (span.get("text") or "").strip() and span.get("origin") and (span.get("size") or 0) > 0
                    and not cls._is_superscript_span(span)]
        if len(measured) < 2:
            return set()
        main_size = max(span["size"] for _, span in measured)
        baseline = max(span["origin"][1] for _, span in measured if span["size"] >= 0.9 * main_size)
        return {index for index, span in measured
                if len(span["text"].strip()) <= 6 and span["size"] <= 0.8 * main_size
                and span["origin"][1] <= baseline - 0.2 * main_size}

    @classmethod
    def _line_geometry(cls, line: Dict[str, Any]) -> Tuple[Optional[tuple], Optional[float]]:
        """A horizontal line's box from its baseline and font size, and its baseline. PyMuPDF's
        own line bbox follows the font's ascender and descender, which some fonts make several
        lines tall (Cambria in PyMuPDF 1.28), so gaps and order are measured from baselines.
        Rotated lines and lines without span origins keep the PyMuPDF bbox (no baseline)."""
        bbox = line.get("bbox")
        raw = tuple(bbox) if bbox else None
        direction = line.get("dir") or (1, 0)
        spans = [span for span in line.get("spans", []) if (span.get("text") or "").strip()]
        if not spans or abs(direction[0] - 1) > 0.01 or abs(direction[1]) > 0.01:
            return raw, None
        main = [span for span in spans if not cls._is_superscript_span(span)] or spans
        origins = [span["origin"][1] for span in main if span.get("origin")]
        size = max((span.get("size", 0) or 0) for span in main)
        if not origins or size <= 0:
            return raw, None
        baseline = max(origins)
        boxes = [span["bbox"] for span in spans if span.get("bbox")]
        x0 = min(box[0] for box in boxes) if boxes else (raw[0] if raw else 0.0)
        x1 = max(box[2] for box in boxes) if boxes else (raw[2] if raw else 0.0)
        return (x0, baseline - 0.8 * size, x1, baseline + 0.25 * size), baseline

    def _block_view(self, block: Dict[str, Any]) -> Optional[_BlockView]:
        views = block.get("_views")
        if views is None:
            views = [self._line_view(line) for line in block.get("lines", [])]
        lines = [view for view in views if view is not None]
        if not lines:
            return None
        chars = [(view, sum(1 for char in view.text if not char.isspace())) for view in lines]
        total = sum(count for _, count in chars) or 1
        colors: Dict[int, int] = defaultdict(int)
        for view, count in chars:
            colors[view.color] += count
        return _BlockView(
            lines=lines,
            text=" ".join(view.text.strip() for view in lines),
            size=max(view.size for view in lines),
            bold=sum(count for view, count in chars if view.bold) * 2 > total,
            italic=sum(count for view, count in chars if view.italic) * 2 > total,
            color=max(colors, key=colors.get),
            bbox=self._boxes_union(*(view.bbox for view in lines)) or block.get("bbox"),
        )

    @staticmethod
    def _is_all_caps(text: str) -> bool:
        """At least four Latin letters, none lowercase, and no CJK (CJK has no case)."""
        latin = [char for char in text if char.isascii() and char.isalpha()]
        return len(latin) >= 4 and not any(char.islower() for char in latin) and not _CJK_CHAR.search(text)

    def _view_heading_features(self, view: _BlockView, body_size: float, max_size: float) -> _HeadingFeatures:
        return self._build_heading_features(
            view.text,
            line_count=len(view.lines),
            block_max_size=view.size,
            avg_font_size=body_size,
            max_font_size=max_size,
            is_bold=view.bold,
            is_all_caps=self._is_all_caps(view.text),
            has_color_signal=_is_chromatic(view.color),
        )

    def _list_markers(self, view: _BlockView, block: Dict[str, Any]) -> List[Optional[_ListMarker]]:
        """Per line, the list marker that makes it a list item, or None (see ``_line_markers``);
        a lettered item may continue a sequence from the previous or next block."""
        return self._line_markers(view.lines, block.get("_prev_item", ""), block.get("_next_item", ""),
                                  block.get("_wrapped_start", False))

    @staticmethod
    def _may_be_heading(view: _BlockView, markers: List[Optional[_ListMarker]]) -> bool:
        """Bulleted lines and blocks of two or more list items are lists, even when they are bold."""
        first = _parse_list_marker(view.lines[0].text)
        if first is not None and first.kind in ("glyph", "bullet"):
            return False
        return sum(1 for marker in markers if marker is not None) < 2

    def _is_title_candidate(self, page_num: int, features: _HeadingFeatures) -> bool:
        """A heading that may be the document title: on the first page with text, short and near
        the page's largest font size. ``_choose_title`` keeps at most one."""
        return (page_num == self._get_first_text_page_num() and self._has_title_layout_signal(features)
                and features.length < 120 and features.line_count <= 3)

    def _is_attached_caption(self, block: Dict[str, Any], view: _BlockView, body_size: float,
                             image_bboxes: List[tuple], table_bboxes: List[tuple]) -> bool:
        """Caption-shaped text directly above or below an image or table: short, not larger than
        the body text, and italic, smaller than the body text or starting with a caption word
        (``Source:``, ``Note``, ``資料來源``)."""
        bbox = view.bbox
        if not bbox or not (image_bboxes or table_bboxes):
            return False
        if len(view.lines) > 4 or len(view.text) > 300 or view.size > body_size * 1.02:
            return False
        if not (view.italic or view.size <= body_size * 0.92 or _CAPTION_KEYWORD.match(view.text.strip())):
            return False
        return self._is_near_image_or_table(tuple(bbox), image_bboxes, table_bboxes,
                                            threshold=max(14.0, 1.5 * body_size))

    # --- classification and rendering ---------------------------------------------------------------

    def _text_item_from_block(self, block: Dict[str, Any], avg_font_size: float, max_font_size: float,
                              page_num: int, image_bboxes: List[tuple],
                              table_bboxes: List[tuple]) -> Optional[SimpleContent]:
        markdown_text, text_type, heading = self._classify_block(
            block, avg_font_size, max_font_size, page_num, image_bboxes, table_bboxes)
        if not markdown_text.strip():
            return None
        # position_y: the top of the PyMuPDF block the text came from, as for a whole block, so the
        # pieces of one block stay together and in order when the page's items are sorted
        top = block.get("_top", (block.get("bbox") or (0, 0, 0, 0))[1])
        return _TextContent(type=text_type, content=markdown_text, page=page_num + 1, position_y=top, heading=heading)

    def _classify_block(self, block: Dict[str, Any], avg_font_size: float, max_font_size: float,
                        page_num: int, image_bboxes: List[tuple], table_bboxes: List[tuple]) -> \
            Tuple[str, str, Optional[Tuple[float, int]]]:
        """Type of a text block (``text:title`` / ``section`` / ``caption`` / ``list`` / ``footnote`` /
        ``normal``), its Markdown content and, for headings, its font size and outline depth. A
        ``text:title`` is a candidate: ``_choose_title`` picks the title, ``_finalize_text_items``
        the levels."""
        view = self._block_view(block)
        if view is None:
            return "", "text:normal", None
        markers = self._list_markers(view, block)
        wraps = self._line_wraps(view.lines)
        total_text = view.text
        block_max_size = view.size
        heading_features = self._view_heading_features(view, avg_font_size, max_font_size)

        # Determine text type based on characteristics
        text_type = "text:normal"  # Default
        heading = None

        # Check if it's a footnote (small text at bottom of page with numeric marker, or with a
        # raised number, as in Word's footnote area); page_num is 0-indexed here
        try:
            page_height = self.doc.load_page(page_num).rect.height if page_num >= 0 else 0
            if page_height > 0:
                block_y_pct = (view.bbox or block["bbox"])[1] / page_height
                if block_y_pct > 0.85 and (
                        (block_max_size < avg_font_size * 0.9
                         and re.match(r'^[\d\*\u2020\u2021\u00a7]+[\.\)\s]', total_text.strip()))
                        or (_starts_with_raised_number(view.lines[0]) and block_max_size < avg_font_size * 0.95)):
                    text_type = "text:footnote"
        except (IndexError, AttributeError, KeyError, TypeError):
            pass

        # Only run further classification if not already classified as footnote
        if text_type == "text:normal":
            if _CAPTION_LABEL.match(total_text.strip()) and len(view.lines) <= 6:
                # "Figure 3: …", "Table 2.1 …", "圖1 …"
                text_type = "text:caption"
            elif (self._is_probable_heading_features(heading_features, require_layout_signal=True)
                  and heading_features.length <= 100 and not self._starts_mid_row(view.lines)
                  and not self._spread_on_row(view.lines, wraps) and self._may_be_heading(view, markers)):
                heading = (_size_key(view.size), _outline_depth(heading_features.normalized))
                text_type = "text:title" if self._is_title_candidate(page_num, heading_features) else "text:section"
            elif self._is_attached_caption(block, view, avg_font_size, image_bboxes, table_bboxes):
                text_type = "text:caption"
            elif markers and markers[0] is not None:
                text_type = "text:list"

        markdown_text = self._render_block(view, text_type, markers, wraps)

        # Debug logging for classification
        if text_type != "text:normal":
            logger.debug(
                f"Classified as {text_type}: '{total_text[:50]}...' (size: {block_max_size:.1f}, avg: {avg_font_size:.1f})")

        return markdown_text, text_type, heading

    @staticmethod
    def _line_wraps(lines: List[_LineView]) -> List[int]:
        """How each line follows the line before it (see ``_line_join``): 0 = not its continuation
        (beside it on the same row, as labels under icons, or more than two lines below it),
        1 = on the next row, 2 = on the next row after a line that runs to the right edge of the
        block (a wrapped line). The first line counts as 2."""
        right_edge = max((line.bbox[2] for line in lines if line.bbox), default=0.0)
        wraps = [2]
        for previous, line in zip(lines, lines[1:]):
            if previous.baseline is None or line.baseline is None or not previous.bbox or not line.bbox:
                wraps.append(2)
                continue
            size = max(previous.size, line.size, 1.0)
            pitch = line.baseline - previous.baseline
            if pitch < 0.5 * size or pitch > 2.0 * size or line.bbox[0] > previous.bbox[2]:
                wraps.append(0)
            else:
                wraps.append(2 if previous.bbox[2] >= right_edge - 2 * size else 1)
        return wraps

    @staticmethod
    def _cjk_joins(lines: List[_LineView]) -> List[bool]:
        """Per line, True when the CJK line break before it is inside a wrapped paragraph, so it
        is joined without a space. The line before must have wrapped onto this one: CJK text
        wraps at any character, so the gap it leaves at the block's right edge is narrower than
        this line's first character, or its first Latin word or an opening bracket with the
        character after it (which cannot end a line), plus under 1 em that line-start rules may
        leave. And the paragraph must go on: this line wraps onto the next one too, ends a
        sentence, lead-in or bracket, or ends a paragraph of two or more wrapped lines. Stacked
        labels and items, of varying length and without sentence ends, stay apart, and so does a
        paragraph from the next one in the same block. In a block of two lines the first must
        also be running text: a short label above a sentence is not one paragraph with it."""
        joins = [False] * len(lines)
        if len(lines) < 2 or any(line.bbox is None for line in lines):
            return joins
        right_edge = max(line.bbox[2] for line in lines)

        def wraps_onto(previous: _LineView, line: _LineView) -> bool:
            text = line.text.strip()
            token = _LINE_START_TOKEN.match(text)
            char_width = (line.bbox[2] - line.bbox[0]) / max(len(text), 1)
            first = char_width * len(token.group(0) if token else text[:1])
            return previous.bbox[2] + first + 0.9 * max(previous.size, 1.0) > right_edge

        wrapped = [False] + [wraps_onto(previous, line) for previous, line in zip(lines, lines[1:])]
        for index in range(1, len(lines)):
            previous, line = lines[index - 1], lines[index]
            if not wrapped[index] or (len(lines) == 2 and _text_units(previous.text) < _RUNNING_TEXT_UNITS):
                continue
            text = line.text.strip()
            goes_on = index + 1 < len(lines) and wrapped[index + 1]
            joins[index] = bool(goes_on or _LEAD_IN_END.search(text) or text[-1:] in ")）」』】"
                                or (index > 1 and joins[index - 1]))
        return joins

    @staticmethod
    def _spread_on_row(lines: List[_LineView], wraps: List[int]) -> bool:
        """True when a line sits on the same row as the line before it, far from it (more than
        2.5 em apart): labels side by side (under icons, in a grid), not one heading. A heading
        number that starts the block (``1.2``, ``Chapter 3``, ``第一章``) may sit any distance
        from its title."""
        for index, (previous, line, wrap) in enumerate(zip(lines, lines[1:], wraps[1:])):
            if wrap == 0 and previous.bbox and line.bbox \
                    and line.bbox[0] - previous.bbox[2] > 2.5 * max(previous.size, line.size, 1.0) \
                    and not (index == 0 and _HEADING_NUMBER.fullmatch(previous.text.strip())):
                return True
        return False

    @staticmethod
    def _starts_mid_row(lines: List[_LineView]) -> bool:
        """True when the first line starts well right of the second one without being centred
        above it: the tail of a sentence that began elsewhere, not a heading."""
        if len(lines) < 2 or not lines[0].bbox or not lines[1].bbox:
            return False
        first, second = lines[0].bbox, lines[1].bbox
        size = max(lines[0].size, lines[1].size, 1.0)
        centred = abs((first[0] + first[2]) / 2 - (second[0] + second[2]) / 2) <= size
        return first[0] > second[0] + 2 * size and not centred

    def _render_block(self, view: _BlockView, text_type: str, markers: List[Optional[_ListMarker]],
                      wraps: Optional[List[int]] = None) -> str:
        """Markdown for a classified block. Text is escaped (``doc2mark.utils.markdown``) and kept
        verbatim otherwise: lines are joined only where a word is broken (``_line_join``),
        headings become one line without emphasis, list markers are normalised (see
        ``_render_list``), superscripts are written ``^x^`` and bold/italic mark exactly the
        styled runs of body text."""
        wraps = wraps if wraps is not None else self._line_wraps(view.lines)
        if text_type == "text:list":
            return self._render_list(view, markers, wraps)
        heading = text_type in ("text:title", "text:section")
        physical = _physical_lines([line.runs for line in view.lines], wraps, getattr(self, "_hyphen_joins", None),
                                   cjk_heading=heading, cjk_joins=None if heading else self._cjk_joins(view.lines))
        if heading:
            return escape_heading_closing(" ".join(_render_runs(runs, emphasis=False) for runs in physical))
        if text_type == "text:caption":
            return "\n".join(escape_line_start(_render_runs(runs, emphasis=False)) for runs in physical)
        if text_type == "text:footnote":
            # The footnote number stays at the line start, plain even when it is raised:
            # pdf_to_markdown turns it into [^N]:
            first = physical[0] if physical else []
            if first and first[0].superscript and first[0].text.strip().isdigit():
                label = replace(first[0], superscript=False)
                if len(first) > 1 and not first[1].text[:1].isspace():
                    label = replace(label, text=label.text + " ")
                physical[0] = [label] + first[1:]
            lines = [_render_runs(runs) for runs in physical]
            return "\n".join(lines[:1] + [escape_line_start(line) for line in lines[1:]])
        return "\n".join(escape_line_start(_render_runs(runs)) for runs in physical)

    def _render_list(self, view: _BlockView, markers: List[Optional[_ListMarker]], wraps: List[int]) -> str:
        """Markdown list for a block whose first line is a list item. Lines without a marker
        continue the item above them; items indented further than the previous item are nested.
        Bullets become ``- `` (``-``, ``*`` and ``+`` keep their own character); a bullet that
        carries meaning (``✓``, ``➔``, a dash, Word's Wingdings check mark and arrows as their
        Unicode forms) stays in the item text (``- ✓ Approved``);
        numbered items keep their number; letters and roman numerals stay in the item text."""
        items: List[Tuple[Optional[float], str, List[List[_Run]], List[int], List[_LineView]]] = []
        for index, (line, marker) in enumerate(zip(view.lines, markers)):
            if marker is None:
                if items:
                    items[-1][2].append(line.runs)
                    items[-1][3].append(wraps[index])
                    items[-1][4].append(line)
                continue
            indent = len(line.text) - len(line.text.lstrip())
            if marker.kind == "enum":
                prefix, runs = "- ", line.runs
            elif marker.kind == "ordered":
                prefix, runs = f"{marker.text} ", _drop_prefix(line.runs, indent + marker.length)
            elif marker.text in _MEANINGFUL_BULLETS:
                prefix, runs = "- ", _drop_prefix(line.runs, indent)
            elif marker.text in _PUA_MEANINGFUL_BULLETS:
                glyph = _Run(_PUA_MEANINGFUL_BULLETS[marker.text] + " ", False, False, False)
                prefix, runs = "- ", [glyph] + _drop_prefix(line.runs, indent + marker.length)
            else:
                prefix = f"{marker.text} " if marker.text in ("-", "*", "+") else "- "
                runs = _drop_prefix(line.runs, indent + marker.length)
            items.append((line.bbox[0] if line.bbox else None, prefix, [runs], [2], [line]))

        def list_kind(marker: str) -> str:
            return marker.rstrip()[-1] if marker[:1].isdigit() else "-"

        output: List[str] = []
        stack: List[Tuple[Optional[float], str, str]] = []   # open levels: (x0, indent, marker prefix)
        joins = getattr(self, "_hyphen_joins", None)
        previous = None   # marker prefix of the item above
        for x0, prefix, item_lines, item_wraps, item_views in items:
            while len(stack) > 1 and x0 is not None and stack[-1][0] is not None and x0 < stack[-1][0] - 2:
                stack.pop()
            # An item that starts another kind of list than the item above it (numbers after
            # bullets, bullets after numbers, ``1)`` after ``1.``), or a nested numbered list that
            # does not start at 1, goes after a blank line: without one it reads as a lazy
            # continuation of the item above in renderers that keep list types apart (Python-Markdown's
            # sane_lists) and, nested, in CommonMark too.
            kind = list_kind(prefix)
            if not stack:
                indent, new_list = "", False
            elif x0 is not None and stack[-1][0] is not None and x0 > stack[-1][0] + 2:
                indent = stack[-1][1] + " " * len(stack[-1][2])
                new_list = kind != "-" and prefix.rstrip()[:-1] != "1"
            else:
                sibling = stack.pop()
                indent = sibling[1]
                new_list = kind != list_kind(sibling[2]) or kind != list_kind(previous)
            if new_list and output:
                output.append("")
            stack.append((x0, indent, prefix))
            previous = prefix
            physical = _physical_lines(item_lines, item_wraps, joins, cjk_joins=self._cjk_joins(item_views))
            lines = [_render_runs(runs) for runs in physical] or [""]
            output.append(indent + prefix + escape_line_start(lines[0]))
            continuation = indent + " " * len(prefix)
            output.extend(continuation + escape_line_start(line) for line in lines[1:])
        return "\n".join(output)

    def _normalized_heading_text(self, text: str) -> str:
        return re.sub(r'\s+', ' ', (text or '')).strip()

    def _explicit_heading_match(self, normalized: str):
        explicit_heading_patterns = [
            r'^第\s*[一二三四五六七八九十百千零〇\d]+\s*[條条章節节篇款項项編编]',
            r'^(附錄|附录|附件|附表)\s*(?:[A-Za-z\d]+|[一二三四五六七八九十百千]+)(?![A-Za-z\d])',
            r'^(Appendix|Chapter|Section)\b',
        ]
        for pattern in explicit_heading_patterns:
            match = re.match(pattern, normalized, re.IGNORECASE)
            if match:
                return match
        return None

    def _structured_heading_match(self, normalized: str):
        structured_heading_patterns = [
            r'^\d+(?:\.\d+)+',
            r'^\d+(?:-\d+)+',
            r'^\d+[\.)、．]',
            r'^[\(（]\d+[\)）]',
            r'^[一二三四五六七八九十百千]+[、．\.]',
            r'^[壹貳參肆伍陸柒捌玖拾]+[、．\.]',
            r'^[\(（][一二三四五六七八九十百千]+[\)）]',
        ]
        for pattern in structured_heading_patterns:
            match = re.match(pattern, normalized, re.IGNORECASE)
            if match and self._has_structured_marker_boundary(normalized, match):
                return match
        return None

    def _has_structured_marker_boundary(self, normalized: str, match) -> bool:
        """Avoid treating decimal/version/percentage prefixes as outline markers."""
        if match.end() >= len(normalized):
            return True
        next_char = normalized[match.end()]
        if next_char.isspace() or _is_cjk(next_char):
            return True
        return next_char in ".)、．:：）"

    def _build_heading_features(
        self,
        text: str,
        *,
        line_count: int = 1,
        block_max_size: float = 0.0,
        avg_font_size: float = 0.0,
        max_font_size: float = 0.0,
        is_bold: bool = False,
        is_all_caps: bool = False,
        has_color_signal: bool = False,
    ) -> _HeadingFeatures:
        normalized = self._normalized_heading_text(text)
        explicit_match = self._explicit_heading_match(normalized)
        structured_match = self._structured_heading_match(normalized)
        marker_match = explicit_match or structured_match
        text_after_marker = normalized[marker_match.end():].strip() if marker_match else normalized
        separator_count = sum(normalized.count(separator) for separator in (',', '，', '、', ':', '：'))
        size_ratio = block_max_size / avg_font_size if avg_font_size > 0 else 1.0
        max_size_ratio = block_max_size / max_font_size if max_font_size > 0 else 1.0
        has_structured_marker = structured_match is not None and bool(text_after_marker)
        has_long_clause_shape = (
            has_structured_marker
            and len(normalized) > 32
            and any(separator in text_after_marker for separator in (',', '，', '、', ':', '：'))
        )
        # A question or exclamation mark may end a heading; sentence punctuation elsewhere may not.
        inner = normalized[:-1] if normalized[-1:] in "?!？！" else normalized
        # "第一章 總則" or "第 1 條": an article/chapter marker followed by a space, a bracket or nothing
        bare_cjk_explicit = bool(re.match(
            r'^第\s*[一二三四五六七八九十百千零〇\d]+\s*[條条章節节篇款項项編编](?:$|\s|[（(【\[])', normalized
        )) and len(normalized) <= 20 and line_count == 1

        return _HeadingFeatures(
            normalized=normalized,
            length=len(normalized),
            line_count=line_count,
            size_ratio=size_ratio,
            max_size_ratio=max_size_ratio,
            is_bold=is_bold,
            is_all_caps=is_all_caps,
            is_explicit_marker=explicit_match is not None,
            is_structured_marker=has_structured_marker,
            text_after_marker=text_after_marker,
            has_cjk=bool(re.search(r'[一-鿿]', normalized)),
            has_checkbox_marker=bool(re.match(r'^[□■☑☐]', normalized)),
            has_sentence_punctuation=bool(re.search(r'[。！？!?；;]', inner) or normalized.endswith('.')),
            has_trailing_continuation=normalized.endswith(('，', ',', '、', '；', ';')),
            separator_count=separator_count,
            has_form_field_shape=bool(re.search(r'_{3,}|\.{4,}|…{2,}', normalized)),
            has_long_clause_shape=has_long_clause_shape,
            letter_count=sum(1 for char in normalized if char.isalpha()),
            has_color_signal=has_color_signal,
            is_bare_structured_marker=structured_match is not None and not text_after_marker,
            is_bare_cjk_explicit_marker=bare_cjk_explicit,
        )

    def _heading_signal_strength(self, features: _HeadingFeatures) -> int:
        """How strongly the layout marks a block as a heading: 3 = clearly larger (or larger and
        bold), 2 = bold at body size, or slightly larger and all caps or coloured, 1 = coloured
        at body size (enough only together with an outline marker), 0 = body text."""
        ratio = features.size_ratio
        if ratio >= 1.15 or (features.is_bold and ratio >= 1.05):
            return 3
        if (features.is_bold and ratio >= 0.95) or (
                ratio >= 1.05 and (features.is_all_caps or features.has_color_signal)):
            return 2
        if features.has_color_signal and ratio >= 0.95:
            return 1
        return 0

    def _has_title_layout_signal(self, features: _HeadingFeatures) -> bool:
        return (
            features.max_size_ratio >= 0.85
            and (features.size_ratio >= 1.15
                 or ((features.is_bold or features.is_all_caps) and features.size_ratio >= 1.05))
        )

    def _has_hard_body_shape(self, features: _HeadingFeatures) -> bool:
        return (
            features.has_checkbox_marker
            or features.has_form_field_shape
            or features.has_sentence_punctuation
            or features.has_trailing_continuation
            or features.length > 120
        )

    def _has_soft_body_shape(self, features: _HeadingFeatures) -> bool:
        if any(separator in features.text_after_marker for separator in (',', '，')) and features.length > 24:
            return True
        if any(separator in features.text_after_marker for separator in (':', '：')) and features.length > 30:
            return True
        if features.separator_count >= 3:
            return True
        if features.separator_count >= 2 and not features.is_structured_marker and features.length > 36:
            return True
        return features.has_long_clause_shape

    def _fits_relaxed_heading_shape(self, features: _HeadingFeatures) -> bool:
        """Latin headings with a comma or a colon ("Property, Plant and Equipment", "Part II:
        Management Discussion and Analysis"): short enough to still be a heading when the layout
        clearly marks them as one."""
        if features.has_cjk or features.has_long_clause_shape:
            return False
        commas = sum(features.normalized.count(separator) for separator in (',', '，'))
        colons = sum(features.normalized.count(separator) for separator in (':', '：'))
        return commas <= 2 and colons <= 1 and features.length <= (60 if colons else 40)

    def _is_probable_heading_features(
        self,
        features: _HeadingFeatures,
        *,
        require_layout_signal: bool = False,
    ) -> bool:
        if not features.normalized:
            return False
        if features.letter_count < 2 and not features.is_bare_structured_marker:
            return False  # numbers, KPI figures, drop caps
        if features.line_count > 3:
            return False
        if self._has_hard_body_shape(features):
            return False

        strength = self._heading_signal_strength(features)
        if features.line_count == 3 and strength < 3:
            return False
        has_soft_body_shape = self._has_soft_body_shape(features)
        if has_soft_body_shape and strength >= 2 and self._fits_relaxed_heading_shape(features):
            has_soft_body_shape = False

        if features.is_explicit_marker:
            if require_layout_signal and strength == 0 and not features.is_bare_cjk_explicit_marker:
                return False  # "Section 5 applies to …", "Chapter 1 - Introduction" at body size
            return features.length <= 80 and not (has_soft_body_shape and features.length > 60)

        if require_layout_signal and strength == 0:
            return False

        if features.is_structured_marker:
            if features.has_long_clause_shape:
                return False
            if has_soft_body_shape and strength < 2:
                return False
            return features.length <= 80

        if has_soft_body_shape:
            return False
        if require_layout_signal and strength < 2:
            return False  # colour alone marks a heading only together with an outline marker

        length_limit = 24 if features.has_cjk else 80
        return features.length <= length_limit

    def _extract_tables_as_markdown(self, page, page_num: int) -> Tuple[List[SimpleContent], List[Tuple]]:
        """Extract the page's tables as Markdown/HTML (see :mod:`doc2mark.pipelines.pdf_tables`).

        Returns the rendered tables and their bounding boxes (a header row drawn above
        the ruled cells included); the text path skips text inside those boxes. Grids
        that are not tables (logo shapes, page frames) are not returned, so their text
        stays with the text path. Borderless tables with clear numeric columns are found
        too. A table that continues from the previous page gets that page's header row
        instead of promoting its first data row.
        """
        from doc2mark.pipelines import pdf_tables

        try:
            finder = page.find_tables()
            found = list(getattr(finder, "tables", None) or [])
        except Exception as e:
            logger.warning(f"Failed to find tables on page {page_num + 1}: {e}")
            finder, found = None, []

        try:
            tables, outside = pdf_tables.extract_page_tables(page, found, getattr(finder, "textpage", None))
            self._table_carry = pdf_tables.continue_table(
                tables, outside, page_num, page.rect.height, getattr(self, "_table_carry", None),
                lambda: pdf_tables.next_page_top_lines(page))
            renderer = TableRenderer(self.table_style)
            table_items, table_bboxes = [], []
            for table in tables:
                markdown_table = renderer.render(table.table_data())
                if markdown_table.strip():
                    table_items.append(SimpleContent(
                        type="table",
                        content=markdown_table,
                        page=page_num + 1,
                        position_y=table.bbox[1]
                    ))
                    table_bboxes.append(tuple(table.bbox))
            return table_items, table_bboxes
        except Exception as e:
            logger.warning(f"Failed to extract tables on page {page_num + 1}, using PyMuPDF's plain cell text: {e}")
            self._table_carry = None
            return self._extract_tables_plain(found, page_num)

    def _extract_tables_plain(self, tables, page_num: int) -> Tuple[List[SimpleContent], List[Tuple]]:
        """Last resort: PyMuPDF's own cell text, no merged cells."""
        renderer = TableRenderer(self.table_style)
        table_items, table_bboxes = [], []
        for table in tables:
            try:
                data = table.extract()
                if not any((cell or "").strip() for row in data or [] for cell in row):
                    continue  # no text found: leave the region to the text path
                markdown_table = renderer.render(TableData.from_2d_array(data))
            except Exception as e:
                logger.debug(f"Plain table extraction failed: {e}")
                continue
            if markdown_table.strip():
                table_items.append(SimpleContent(type="table", content=markdown_table, page=page_num + 1,
                                                 position_y=table.bbox[1]))
                table_bboxes.append(tuple(table.bbox))
        return table_items, table_bboxes

    def _extract_images_simple(self, page, page_num: int, ocr_images: bool = False,
                               ocr_results_map: Dict[tuple, str] = None) -> List[SimpleContent]:
        """Extract images and convert to base64 or text descriptions using OCR

        With OCR, the page's pictures (see _page_pictures) give their OCR text once per place the
        page shows them; a picture whose OCR is missing (a failed batch) gives the placeholder
        ``[image: OCR unavailable]``, one that OCR'd to nothing gives nothing. Without OCR, every
        image placement the page shows gives its base64 data once.

        Args:
            page: PyMuPDF page object
            page_num: Page number (0-indexed)
            ocr_images: If True, use OCR to convert images to text descriptions
            ocr_results_map: Pre-computed OCR results (see _ocr_document); when None, this page's
                pictures are OCR'd now

        Returns:
            List of SimpleContent items with type 'image' (base64) or 'text:image_description' (OCR text)
        """
        image_items = []

        if ocr_images and self.ocr is not None:
            pictures = self._page_pictures(page_num)
            if ocr_results_map is None:
                # A page processed on its own (not through convert_to_json): OCR its pictures now.
                ocr_results_map = {}
                batch = []
                for job in self._picture_jobs(page, page_num, pictures):
                    load = job.pop("load", None)
                    loaded = load() if load is not None else (job["image"],)
                    if loaded is not None:
                        job["image"] = loaded[0]
                        batch.append(job)
                if batch:
                    self._ocr_batch(batch, ocr_results_map, synthesis_markdown=False, show_progress=False)
            for picture in pictures:
                text = ocr_results_map.get((page_num, picture.key))
                if text is None:
                    # OCR was requested but this picture has no result (batch failure or partial
                    # result). Emit a lightweight placeholder -- never dump raw base64 into a text/RAG output.
                    logger.warning(f"OCR result not found for image {picture.key} on page {page_num + 1}")
                    content = "<image_ocr_result>[image: OCR unavailable]</image_ocr_result>"
                elif text.strip():
                    content = f"<image_ocr_result>{text.strip()}</image_ocr_result>"
                else:
                    continue  # skip images that OCR'd to nothing
                for rect in picture.rects:
                    image_items.append(SimpleContent(
                        type="text:image_description",
                        content=content,
                        page=page_num + 1,
                        position_y=rect.y0,
                    ))
                    if text is not None:
                        self._picture_records.append((page_num + 1, rect.y0, content, tuple(rect)))
            return image_items

        # Regular base64 extraction (OCR disabled): once per placement the page shows.
        extracted: Dict[int, Optional[Tuple[bytes, str, str]]] = {}
        for placement in self._placements_of(page):
            if not placement.xref or placement.visible.is_empty:
                continue
            try:
                if placement.xref not in extracted:
                    extracted[placement.xref] = self._extract_image_bytes(placement.xref)
                result = extracted[placement.xref]
                if result is None:
                    continue
                image_bytes, _, mime = result
                image_items.append(SimpleContent(
                    type="image",
                    content=base64.b64encode(image_bytes).decode('utf-8'),
                    page=page_num + 1,
                    position_y=placement.visible.y0,
                    mime_type=mime
                ))
            except Exception as e:
                logger.warning(f"Failed to extract image {placement.xref}: {e}")

        return image_items

    def export_to_dict(self, extract_images: bool = True, ocr_images: bool = False, show_progress: bool = True) -> Dict[
        str, Any]:
        """
        Export PDF content to a dictionary ready for JSON dumps
        
        Args:
            extract_images: Whether to extract images as base64
            ocr_images: Whether to use OCR to convert images to text descriptions (implies image extraction)
            show_progress: Whether to show progress messages
        
        Returns:
            Dictionary with content array containing various content types
        """
        return self.convert_to_json(extract_images=extract_images, ocr_images=ocr_images, show_progress=show_progress)

    def export_to_markdown(self, extract_images: bool = True, ocr_images: bool = False,
                           show_progress: bool = True) -> str:
        """
        Export PDF content to markdown string
        
        Args:
            extract_images: Whether to extract images as base64
            ocr_images: Whether to use OCR to convert images to text descriptions (implies image extraction)
            show_progress: Whether to show progress messages
        
        Returns:
            Markdown-formatted string with all content
        """
        # First get the content as dictionary
        json_data = self.convert_to_json(extract_images=extract_images, ocr_images=ocr_images,
                                         show_progress=show_progress)

        # Use the pdf_to_markdown function for consistent formatting
        return pdf_to_markdown(json_data)

    def save_json(self, output_path: Union[str, Path], json_data: Dict[str, Any]):
        """Save the extracted data to JSON file"""
        output_path = Path(output_path)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        logger.info(f"JSON saved to: {output_path}")

    def save_markdown(self, output_path: Union[str, Path], json_data: Dict[str, Any]):
        """Save the content as a markdown file with embedded images"""
        output_path = Path(output_path)

        # Use the pdf_to_markdown function for consistent formatting
        markdown_content = pdf_to_markdown(json_data)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        logger.info(f"Markdown saved to: {output_path}")

    def close(self):
        """Close the document"""
        if getattr(self, "_copies", None) is not None:
            self._copies.close()
        if self.doc:
            self.doc.close()
            logger.info("Document closed")


# Convenience function for simple usage
def pdf_to_simple_json(
        pdf_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
        output_markdown: bool = False,
        extract_images: bool = True,
        ocr_images: bool = False,
        show_progress: bool = True,
        ocr=None,
        table_style: Union[str, TableStyle] = None,
        legibility_judge=None,
        boilerplate_judge: Optional[BoilerplateJudge] = None,
) -> Dict[str, Any]:
    """
    Convert PDF to simplified JSON with content in reading order

    Args:
        pdf_path: Path to the PDF file
        output_path: Optional path to save JSON output
        output_markdown: Also save as markdown file
        extract_images: Extract images as base64
        ocr_images: Use OCR to convert images to text descriptions (implies image extraction)
        show_progress: Show progress messages
        ocr: OCR instance for image processing
        table_style: Output style for complex tables:
            - 'minimal_html': Clean HTML with only rowspan/colspan (default)
            - 'markdown_grid': Markdown with merge annotations
            - 'styled_html': Full HTML with inline styles (legacy)
        legibility_judge: Optional ``judge(page_text) -> Optional[float]`` for the
            text-layer quality gate (see doc2mark.core.strategy.judge_text_layer)
        boilerplate_judge: Optional judge for repeated header/footer lines the
            deterministic rule keeps; see ``PDFLoader``.
    
    Returns:
        Simplified JSON data with content array containing:
        - text:title - Main document title
        - text:section - Section headers  
        - text:normal - Regular paragraph text
        - text:list - Bullet points or numbered lists
        - text:caption - Figure/table captions
        - text:image_description - OCR-generated image descriptions (when ocr_images=True)
        - table - Tables with complex structure support:
            * Simple tables: Markdown format with span annotations (*[2x3]* for merged cells)
            * Complex tables: HTML format preserving rowspan/colspan attributes
            * Line breaks in cells preserved using <br> tags
            * Automatic detection and labeling of merged cells
        - image - Base64-encoded images (when ocr_images=False)
    """
    converter = PDFLoader(pdf_path, ocr=ocr, table_style=table_style, legibility_judge=legibility_judge,
                          boilerplate_judge=boilerplate_judge)

    try:
        json_data = converter.convert_to_json(
            extract_images=extract_images,
            ocr_images=ocr_images,
            show_progress=show_progress
        )

        if output_path:
            converter.save_json(output_path, json_data)

            if output_markdown:
                markdown_path = Path(output_path).with_suffix('.md')
                converter.save_markdown(markdown_path, json_data)

        return json_data

    finally:
        converter.close()


def pdf_to_markdown(json_data: Dict[str, Any]) -> str:
    """
    Convert PDF JSON data to markdown string with proper formatting.
    
    This function ensures PDFs get the same quality markdown output as Office documents,
    including proper headers, formatted tables, and OCR results in XML code blocks.
    
    Args:
        json_data: The JSON data from pdf_to_simple_json
        
    Returns:
        Formatted markdown string
    """
    markdown_parts = []
    current_page = None
    
    # Debug: Log all content items
    logger.debug(f"Converting {len(json_data.get('content', []))} content items to markdown")
    
    for item in json_data.get("content", []):
        item_type = item.get("type", "")
        content = item.get("content", "")

        # Skip empty content
        if not content or not content.strip():
            continue

        # Skip repeated headers/footers (tagged by _detect_repeated_content)
        if item_type in ("text:header", "text:footer"):
            continue

        # Add page separator if needed (but not at the beginning)
        if 'page' in item and item['page'] != current_page:
            if current_page is not None and markdown_parts:
                # Only add page break if we have content and it's not the first page
                markdown_parts.append("")
                markdown_parts.append(f"<!-- page {item['page']} -->")
            current_page = item['page']
        
        if item_type in ("text:title", "text:section"):
            # ATX heading at the item's level (title 1, sections from 2, see _assign_heading_levels); the
            # content is one escaped line (older JSON may still end in a newline)
            level = item.get("level") or (1 if item_type == "text:title" else 2)
            heading = " ".join(part.strip() for part in content.split("\n") if part.strip())
            markdown_parts.append(f"{'#' * max(1, min(int(level), 6))} {heading}")
            markdown_parts.append("")  # Empty line after heading

        elif item_type == "text:normal":
            # Regular paragraphs
            markdown_parts.append(content)
            markdown_parts.append("")  # Empty line after paragraph

        elif item_type == "text:list":
            # List items (already formatted with bullets/numbers)
            markdown_parts.append(content)
            markdown_parts.append("")  # Empty line after list

        elif item_type == "text:caption":
            # Captions in italics, one emphasis per line so every line is valid Markdown
            caption_lines = [line.strip() for line in content.strip().split("\n") if line.strip()]
            markdown_parts.append("\n".join(f"*{line}*" for line in caption_lines))
            markdown_parts.append("")  # Empty line after caption
            
        elif item_type == "text:image_description":
            # OCR'd-image text — strip the internal provenance wrapper and emit
            # clean text (no code-fence / <ocr_result> noise) for a readable,
            # RAG-clean export.
            ocr_text = content
            if ocr_text.startswith('<image_ocr_result>') and ocr_text.endswith('</image_ocr_result>'):
                ocr_text = ocr_text[18:-19]
            markdown_parts.append(ocr_text.strip())
            markdown_parts.append("")  # Empty line after OCR result
            
        elif item_type == "table":
            # Tables are already in markdown or HTML format
            markdown_parts.append(content)
            # Table content already includes trailing newlines
            
        elif item_type == "text:footnote":
            # Format as markdown footnote definition if it matches N. pattern; every line of the
            # footnote is kept (DOTALL), not just the first one
            footnote_text = content.strip()
            m = re.match(r'^(\d+)[\.\)\s]+(.+)', footnote_text, re.DOTALL)
            if m:
                markdown_parts.append(f"[^{m.group(1)}]: {m.group(2)}")
            else:
                markdown_parts.append(escape_line_start(footnote_text))
            markdown_parts.append("")

        elif item_type == "image":
            mime = item.get("mime_type") or 'image/png'
            markdown_parts.append(f'![Image](data:{mime};base64,{content})')
            markdown_parts.append("")  # Empty line after image
    
    # Clean up extra empty lines
    result = "\n".join(markdown_parts)
    # Remove multiple consecutive empty lines
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    
    return result.strip()


# Example usage
if __name__ == "__main__":
    # Process a PDF file
    try:
        # Method 1: Using the convenience function
        # result = pdf_to_simple_json(
        #     pdf_path="../../data/test.pdf",
        #     output_path="output_simple.json",
        #     output_markdown=True,  # Also create markdown file
        #     extract_images=True,
        #     ocr_images=True,
        #     show_progress=True
        # )

        # print(f"\nProcessing completed successfully!")
        # print(f"Check 'output_simple.json' for the results.")
        # print(f"Also created 'output_simple.md' with markdown format.")

        # Method 2: Using the PDFLoader class directly with new export methods
        print("\n--- Using PDFLoader class directly ---")
        loader = PDFLoader("../../../data/test2.pdf")

        # Export to dict (ready for JSON dumps)
        # pdf_dict = loader.export_to_dict(extract_images=True, ocr_images=False, show_progress=False)
        # print(f"\nExported to dict with {len(pdf_dict['content'])} content items")

        # Export to markdown string with OCR
        markdown_str = loader.export_to_markdown(extract_images=True, ocr_images=True, show_progress=False)
        # save to file
        with open("output_simple.md", "w", encoding="utf-8") as f:
            f.write(markdown_str)

        print(f"Exported to markdown string with OCR ({len(markdown_str)} characters)")

        loader.close()

        # # Show sample of the output
        # print("\nSample output structure:")
        # if result["content"]:
        #     for i, item in enumerate(result["content"][:10]):  # Show first 10 items
        #         if item["type"].startswith("text:"):
        #             preview = item["content"].strip()[:80] + "..." if len(item["content"]) > 80 else item[
        #                 "content"].strip()
        #             # Remove newlines for preview
        #             preview = preview.replace('\n', ' ')
        #             print(f"Item {i}: {item['type']} - {preview}")
        #         elif item["type"] == "table":
        #             lines = item["content"].strip().split('\n')
        #             print(f"Item {i}: Table - {len(lines)} rows")
        #             if lines:
        #                 print(f"  First row: {lines[0][:60]}...")
        #         elif item["type"] == "image":
        #             print(f"Item {i}: Image - base64 data ({len(item['content'])} chars)")

    except Exception as e:
        logger.error(f"Error processing PDF: {e}")
        raise
