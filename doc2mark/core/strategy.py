"""Shared content-based OCR strategy decision — used by every format pipeline.

A document is processed by one of two strategies, decided from per-document signals
(mean image coverage, mean text density, text-layer quality):

- ``"image"``: the document is mostly pictures with no usable text layer (slide
  decks, scans). It is rendered page-by-page and OCR'd as whole images, with the
  ``page_markdown`` synthesis producing structured Markdown.
- ``"text"``:  the document has a usable text layer (or little image coverage).
  The deterministic rule-based layer (text + tables, verbatim for BM42 sparse
  retrieval) is authoritative; embedded figures are OCR'd individually.

The document route is the default for every page. Where a pipeline can measure
single pages (PDF), a page overrides it when its own signals clearly disagree:
a searchable scan, a page whose text layer is garbage, a page whose only content
is vector outlines or inline images, a scanned page inside a text report, a dense
text page inside an image deck (:func:`decide_page_route`).

This module is the single source of truth for what the signals mean, the
thresholds and the decisions, so the PDF and Office routes never diverge. The
pipelines only measure (PyMuPDF, python-pptx, ...).
"""
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Literal, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

Route = Literal["image", "text"]

#: Optional legibility judge: ``judge(page_text) -> Optional[float]``; see :func:`judge_text_layer`.
LegibilityJudge = Callable[[str], Optional[float]]

# --- Density and coverage ---------------------------------------------------
# A page is "image-like" when raster images cover at least this fraction of it AND
# it carries less than this much legible text (i.e. no real text layer). Text density
# is the decisive signal: coverage alone misclassifies a text document that happens
# to carry large figures.
IMAGE_PAGE_COVERAGE = 0.55
IMAGE_PAGE_TEXT_LIMIT = 200

# Text density is counted in Latin-character equivalents (see text_weight): only
# non-whitespace, legible characters count, and a character of a script that packs
# more content per character counts more. A CJK ideograph is a whole morpheme, about
# three Latin letters; a kana or hangul syllable about two.
IDEOGRAPH_WEIGHT = 3.0
SYLLABLE_WEIGHT = 2.0

# --- Text-layer quality -----------------------------------------------------
# A selectable-text layer can exist yet be untrustworthy: designed/print PDFs often
# draw text with subset fonts that carry no (or a broken) ToUnicode map, so the page
# renders faithfully but extraction yields U+FFFD, private-use glyphs, control/CID
# codes or mojibake. A page's layer is "garbled" when at least MIN_GARBAGE_GLYPHS
# such glyphs make up at least GARBAGE_TEXT_RATIO of its text, where every character
# is weighted by its prominence, (font size / the page's body size) squared, capped
# at MAX_PROMINENCE: an unreadable title weighs as much as the body lines it
# visually outweighs, a single decorative glyph does not tip a page.
GARBAGE_TEXT_RATIO = 0.1
MIN_GARBAGE_GLYPHS = 3
MAX_PROMINENCE = 4.0

# Document level: an image-dominant document whose text pages are garbled in at least
# this share routes to image as a whole (a share of pages, never one worst page).
ILLEGIBLE_TEXT_RATIO = 0.3

# Optional judge (see judge_text_layer): a page it rates below this probability of
# being legible is treated like a garbled one. Consulted only for layers of at least
# MIN_JUDGED_CHARS characters that the deterministic detector did not flag.
LEGIBILITY_JUDGE_THRESHOLD = 0.7
MIN_JUDGED_CHARS = 20

# --- Per-page overrides -------------------------------------------------------
# A page overrides the document route only when it clears the thresholds by this
# margin (hysteresis), so pages near a threshold follow their document and a deck or
# report keeps one consistent treatment:
# - in a text document, a page covered by pictures for at least
#   IMAGE_PAGE_COVERAGE * (1 + margin) with less than IMAGE_PAGE_TEXT_LIMIT * (1 - margin)
#   legible text is a scanned page;
# - in an image document, a page covered for less than IMAGE_PAGE_COVERAGE * (1 - margin)
#   with at least IMAGE_PAGE_TEXT_LIMIT * (1 + margin) legible text is a text page.
PAGE_OVERRIDE_MARGIN = 0.5

# A page with less legible text than this has no usable text layer. If it still shows
# content the text route cannot capture, only OCR of the page render can read it:
# pictures the text route does not OCR one by one (inline images, picture tiles too
# small to count as figures) over at least MIN_UNCAPTURED_RASTER of the page, or ink
# (vector outlines, any colour) over at least MIN_UNCAPTURED_INK of it. Line art is not
# content by itself and is left out: horizontal and vertical strokes, rectangle
# outlines and fills thinner than 2 pt (rules, frames, table grids). Three short lines
# of 12 pt outlined text already cover about 0.2 %; a false alarm (a logo) costs one
# OCR call, a miss loses the page's words, so the floor is low.
NO_TEXT_LIMIT = IMAGE_PAGE_TEXT_LIMIT / 4
MIN_UNCAPTURED_RASTER = 0.05
MIN_UNCAPTURED_INK = 0.001

# Invisible (render mode 3, fully transparent) text is the text of what the page shows
# (a scanner's OCR layer, the transparent copy of text baked into artwork or drawn as
# outlines) when the page, rendered without its text, shows something under it.
# Invisible text over nothing visible is hidden text, and an invisible span repeating
# the painted text it lies on is a duplicate; neither is emitted. In doubt the text is
# kept: losing a scan's only text is worse than emitting a hidden line.
# - Over a picture it is the picture's text unless the region is blank: fewer than
#   LAYER_BLANK_SHARE of its pixels differ from the region's background (its most common
#   grey) by more than LAYER_BLANK_CONTRAST levels, at LAYER_DPI, so faint and
#   low-contrast scans count.
# - Elsewhere only glyph-like ink counts (outlined text), not a rule or a box edge: rules
#   and frames crossing the span left out, at least GLYPH_INK_SHARE of the region is ink
#   (INK_CONTRAST levels off its background), spread over at least GLYPH_SPREAD of its
#   rows and of its columns.
# A painted line of the verbatim tail (below) counts as shown when at least
# MIN_LAYER_INK of its box is ink on the page render.
LAYER_DPI = 100
LAYER_BLANK_CONTRAST = 12
LAYER_BLANK_SHARE = 0.01
INK_CONTRAST = 48
GLYPH_INK_SHARE = 0.05
GLYPH_SPREAD = 0.3
MIN_LAYER_INK = 0.03

# Bumped whenever routing changes what a page emits for the same input, so caches of
# converted documents (UnifiedDocumentLoader's cache_dir) do not serve stale output.
ROUTING_VERSION = 2

# Reasons reported for a page route.
REASON_DOCUMENT = "document_route"
REASON_SEARCHABLE_SCAN = "searchable_scan"
REASON_ILLEGIBLE = "illegible_text_layer"
REASON_NO_TEXT_LAYER = "no_text_layer"
REASON_IMAGE_PAGE = "image_dominant_page"
REASON_TEXT_PAGE = "dense_text_page"

# A page overridden to render OCR for one of these reasons may still carry legible
# painted text (a caption, a heading, a stamp, the clean body under a garbled title).
# Whatever legible line the OCR did not reproduce is kept verbatim after the OCR, so the
# override never loses real text. (Pages following an image document route are the
# document's slides or scans.)
VERBATIM_TAIL_REASONS = (REASON_SEARCHABLE_SCAN, REASON_NO_TEXT_LAYER, REASON_IMAGE_PAGE, REASON_ILLEGIBLE)


def decide_doc_strategy(
    mean_image_coverage: float,
    mean_text_chars_per_page: float,
    text_illegibility: float = 0.0,
) -> Route:
    """Return the document-level OCR strategy from per-document signals.

    Routes to ``"image"`` when the document is image-dominant
    (``mean_image_coverage >= IMAGE_PAGE_COVERAGE``) AND *either*:

    - it has little legible text
      (``mean_text_chars_per_page < IMAGE_PAGE_TEXT_LIMIT``, measured with
      :func:`text_weight`), i.e. no real text layer; **or**
    - its text layer is low quality (``text_illegibility >= ILLEGIBLE_TEXT_RATIO``,
      the share of its text pages whose layer is garbled), i.e. the render must be
      trusted instead.

    Otherwise ``"text"``. ``text_illegibility`` is kept for API compatibility; no
    doc2mark route passes it any more: the PDF route checks every page's text layer
    and OCRs garbled pages one by one (:func:`decide_page_route`), so garbled pages
    never take clean pages with them, and the Office route measures no text quality.
    """
    if mean_image_coverage >= IMAGE_PAGE_COVERAGE and (
        mean_text_chars_per_page < IMAGE_PAGE_TEXT_LIMIT
        or text_illegibility >= ILLEGIBLE_TEXT_RATIO
    ):
        return "image"
    return "text"


# --- Measuring text ---------------------------------------------------------

_CID = re.compile(r"\(cid:\d+\)")
# Mojibake: UTF-8 bytes shown as Latin-1 or cp1252 characters. A candidate is a UTF-8
# lead byte followed by a continuation byte (Latin-1 or cp1252 reading); it counts
# only when the characters, turned back into bytes, form one complete UTF-8 sequence
# ("\u00c3\u00a9" is "\u00e9"), so an accented letter before punctuation ("\u00e9\u00ae",
# a French no-break space) is not garbage.
_MOJIBAKE_LEADS = bytes(range(0xC2, 0xF5)).decode("latin-1")
_MOJIBAKE_TRAILS = (bytes(range(0x80, 0xC0)).decode("latin-1")
                    + bytes(range(0x80, 0xA0)).decode("cp1252", errors="ignore"))
_MOJIBAKE_CANDIDATE = re.compile(f"[{re.escape(_MOJIBAKE_LEADS)}][{re.escape(_MOJIBAKE_TRAILS)}]")


def _byte_of(char: str) -> Optional[int]:
    """The single Latin-1 or cp1252 byte a character stands for, if any."""
    if ord(char) < 0x100:
        return ord(char)
    try:
        encoded = char.encode("cp1252")
    except UnicodeEncodeError:
        return None
    return encoded[0] if len(encoded) == 1 else None


def _mojibake_positions(text: str) -> Tuple[set, set]:
    """(indexes of the characters that are UTF-8 sequences read as Latin-1/cp1252, those
    sequences)."""
    positions: set = set()
    sequences: set = set()
    for match in _MOJIBAKE_CANDIDATE.finditer(text):
        start = match.start()
        if start in positions:
            continue
        lead = ord(text[start])
        length = 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
        raw = [_byte_of(char) for char in text[start:start + length]]
        if len(raw) < length or any(b is None or not 0x80 <= b <= 0xBF for b in raw[1:]):
            continue
        # A no-break space after an accented capital or a sign is ordinary typography
        # ("S\u00c9CURIT\u00c9\u00a0:", "210\u00a0\u00d7\u00a0297"); it is mojibake only
        # after the Latin-1 readings of the two most common lead bytes.
        if 0xA0 in raw[1:] and lead not in (0xC2, 0xC3):
            continue
        try:
            bytes([lead] + raw[1:]).decode("utf-8")
        except UnicodeDecodeError:
            continue
        positions.update(range(start, start + length))
        sequences.add(text[start:start + length])
    return positions, sequences


# Mojibake is a property of a whole text layer: every non-ASCII character comes out
# mangled. A match made only by an accented letter before punctuation
# ("ferm\u00e9\u2026\u201d" reads as one) is ordinary typography, so matches count as
# garbage only when the layer has a typical one -- the reading of a Latin-1 letter (lead
# byte C2 or C3, "\u00c3\u00a9") or of typographic punctuation ("\u00e2\u20ac\u2122") --
# or MIN_MOJIBAKE_SEQUENCES distinct ones.
MIN_MOJIBAKE_SEQUENCES = 2
_TYPICAL_MOJIBAKE = (chr(0xC2), chr(0xC3), chr(0xE2) + chr(0x20AC))


def _mangled(sequences: set) -> bool:
    """Whether the mojibake matches of a layer make it a mangled layer (see MIN_MOJIBAKE_SEQUENCES)."""
    return len(sequences) >= MIN_MOJIBAKE_SEQUENCES or any(seq.startswith(_TYPICAL_MOJIBAKE) for seq in sequences)


# Icon and symbol fonts put their glyphs in the private-use area: stars, bullets,
# arrows. Their glyphs are icons, not garbage; so are the private-use glyphs of any
# font when a span holds only a short row of them (MAX_ICON_GLYPHS) on a page that
# otherwise reads as text (MIN_PAGE_LETTERS letters).
_ICON_FONT = re.compile(r"awesome|icon|glyph|symbol|wingding|webding|dingbat|material|emoji|zapf|fontello|"
                        r"entypo|octicon", re.IGNORECASE)
MAX_ICON_GLYPHS = 5
MIN_PAGE_LETTERS = 10


def _is_private_use(code: int) -> bool:
    return 0xE000 <= code <= 0xF8FF or 0xF0000 <= code <= 0x10FFFD


def _script_weight(code: int) -> float:
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0xF900 <= code <= 0xFAFF \
            or 0x20000 <= code <= 0x323AF:
        return IDEOGRAPH_WEIGHT
    if 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF or 0xFF66 <= code <= 0xFF9D \
            or 0xAC00 <= code <= 0xD7AF:
        return SYLLABLE_WEIGHT
    return 1.0


def _scan_text(text: str, *, mojibake: bool = True, icons: bool = False) -> Tuple[int, int, float]:
    """(garbage glyphs, counted characters, legible weight) of one run of text.

    Counted characters are the non-whitespace ones, except private-use glyphs that are
    icons or bullets (a lone one; all of them when ``icons``), which are neither text
    nor garbage. Garbage: U+FFFD, ``(cid:N)``, control codes, private-use runs, and
    mojibake sequences when ``mojibake``.
    """
    text = _CID.sub("\ufffd", text)
    mangled = _mojibake_positions(text)[0] if mojibake else set()
    garbage = counted = 0
    weight = 0.0
    for index, char in enumerate(text):
        if char.isspace():
            continue
        code = ord(char)
        if _is_private_use(code):
            neighbours = (text[index - 1] if index else "", text[index + 1] if index + 1 < len(text) else "")
            if icons or not any(n and _is_private_use(ord(n)) for n in neighbours):
                continue
            garbage += 1
        elif char == "\ufffd" or code < 0x20 or 0x7F <= code <= 0x9F or index in mangled:
            garbage += 1
        else:
            weight += _script_weight(code)
        counted += 1
    return garbage, counted, weight


def text_weight(text: str) -> float:
    """Amount of legible text in Latin-character equivalents: non-whitespace,
    non-garbage characters, a CJK ideograph counting ``IDEOGRAPH_WEIGHT`` and a
    kana or hangul syllable ``SYLLABLE_WEIGHT``. This is the unit of
    ``IMAGE_PAGE_TEXT_LIMIT``."""
    return text_layer_stats([(text, 0.0)]).weight


@dataclass(frozen=True)
class TextLayerStats:
    """Size and quality of one text layer of a page (see :func:`text_layer_stats`)."""

    chars: int = 0              # counted characters (non-whitespace, garbage included)
    weight: float = 0.0         # legible text, in text_weight units
    garbage_glyphs: int = 0
    garbage_ratio: float = 0.0  # prominence-weighted share of garbage glyphs

    @property
    def garbled(self) -> bool:
        """The deterministic garbage detector's verdict."""
        return self.garbage_glyphs >= MIN_GARBAGE_GLYPHS and self.garbage_ratio >= GARBAGE_TEXT_RATIO


def _layer_runs(spans: Iterable[Sequence]) -> List[Tuple[float, int, int, float]]:
    """(font size, garbage glyphs, counted characters, legible weight) of each run of a text
    layer, judged in the context of the whole layer (see :func:`text_layer_stats`)."""
    spans = [(span[0] or "", float(span[1] or 0.0), span[2] if len(span) > 2 else "") for span in spans]
    mojibake = _mangled(set().union(*(_mojibake_positions(text)[1] for text, _, _ in spans)))
    letters = sum(1 for text, _, _ in spans for char in text if char.isalpha() and not _is_private_use(ord(char)))
    runs = []
    for text, size, font in spans:
        private = sum(1 for char in text if _is_private_use(ord(char)))
        icons = bool(private) and (bool(_ICON_FONT.search(font or ""))
                                   or (private <= MAX_ICON_GLYPHS and letters >= MIN_PAGE_LETTERS))
        runs.append((size,) + _scan_text(text, mojibake=mojibake, icons=icons))
    return runs


def text_layer_stats(spans: Iterable[Sequence]) -> TextLayerStats:
    """Measure a text layer given as ``(text, font size)`` or ``(text, font size, font name)``
    runs in reading order.

    The body size is the character-weighted median font size; a character's
    prominence is ``min(size / body size, MAX_PROMINENCE) ** 2``. Mojibake counts only
    in a mangled layer (see ``MIN_MOJIBAKE_SEQUENCES``); private-use glyphs of icon
    fonts, and short private-use rows on a page that otherwise reads as text, are icons.
    """
    runs = [run for run in _layer_runs(spans) if run[2]]
    if not runs:
        return TextLayerStats()
    by_size = sorted(runs)
    half, seen, body = sum(run[2] for run in runs) / 2, 0, by_size[-1][0]
    for size, _, counted, _ in by_size:
        seen += counted
        if seen >= half:
            body = size
            break
    garbage_mass = total_mass = 0.0
    for size, garbage, counted, _ in runs:
        prominence = min(size / body, MAX_PROMINENCE) ** 2 if size > 0 and body > 0 else 1.0
        garbage_mass += garbage * prominence
        total_mass += counted * prominence
    return TextLayerStats(
        chars=sum(run[2] for run in runs),
        weight=sum(run[3] for run in runs),
        garbage_glyphs=sum(run[1] for run in runs),
        garbage_ratio=garbage_mass / total_mass if total_mass else 0.0,
    )


def legible_lines(lines: Sequence[Sequence[Sequence]]) -> List[bool]:
    """Whether each line of a page's text layer (its runs, as for :func:`text_layer_stats`)
    reads as text: fewer than ``GARBAGE_TEXT_RATIO`` of its characters are garbage, judged
    in the context of the whole page (mojibake, icon glyphs)."""
    runs = iter(_layer_runs([span for line in lines for span in line]))
    verdicts = []
    for line in lines:
        line_runs = [next(runs) for _ in line]
        garbage, counted = sum(run[1] for run in line_runs), sum(run[2] for run in line_runs)
        verdicts.append(garbage < GARBAGE_TEXT_RATIO * counted if garbage else True)
    return verdicts


def judge_text_layer(judge: Optional[LegibilityJudge], layer: TextLayerStats, text: str) -> Optional[float]:
    """Ask the optional legibility judge about a text layer; return its verdict or None.

    Contract of ``judge(page_text) -> Optional[float]``:

    - ``page_text`` is the text layer of one page, exactly as extracted (lines joined
      with ``"\\n"``): the visible text, or the invisible OCR layer of a searchable scan.
    - It returns the probability, in ``[0, 1]``, that the text is legible content a
      person could read (prose, tables, code, identifiers, any script), as opposed to
      text garbled by a broken text layer (substituted or shifted letters, mojibake,
      placeholder glyphs); or ``None`` when it cannot judge.
    - It is consulted only when an OCR provider is active (its verdict can only send a
      page to OCR), and only where the deterministic detector cannot decide: for layers
      of at least ``MIN_JUDGED_CHARS`` characters that are not already garbled, once
      per page. Below ``LEGIBILITY_JUDGE_THRESHOLD`` the page is treated as garbled and
      OCR'd from its render.
    - ``None``, an exception or a value outside ``[0, 1]`` count as "cannot judge":
      the text is kept, exactly as without a judge.
    """
    if judge is None or layer.garbled or layer.chars < MIN_JUDGED_CHARS:
        return None
    try:
        verdict = judge(text)
    except Exception as exc:  # the judge is optional: its failures must not fail the conversion
        logger.warning(f"legibility_judge failed ({exc!r}); keeping the text layer")
        return None
    if verdict is None:
        return None
    try:
        verdict = float(verdict)
    except Exception:  # a judge may return anything; unusable means "cannot judge"
        verdict = math.nan
    if not 0.0 <= verdict <= 1.0:
        logger.warning(f"legibility_judge returned {verdict!r}, not a probability; ignoring it")
        return None
    return verdict


# --- Page and document decisions --------------------------------------------


@dataclass(frozen=True)
class PageSignals:
    """What a pipeline measured on one page.

    ``image_coverage`` is the share of the page covered by raster images (the union of
    their visible rectangles). ``visible`` describes the painted text; ``invisible`` the
    invisible text lying over what the page shows (see ``LAYER_DPI``: a scanner's OCR
    layer, a transparent copy of text baked into artwork or drawn as outlines).
    ``hidden_chars`` counts the invisible characters over nothing visible.
    ``uncaptured_raster`` is the share of the page covered by pictures the text route
    cannot OCR one by one (inline images, picture tiles), ``uncaptured_ink`` the share
    showing other ink (line art left out) neither the text layer nor the pictures account
    for. Both are measured only on pages without a usable text layer that are not
    searchable scans (``uncaptured_ink`` is None when not measured, and on image-dominant
    pages). ``judge_legibility`` is the optional judge's verdict on :attr:`text_layer`.
    """

    image_coverage: float = 0.0
    visible: TextLayerStats = field(default_factory=TextLayerStats)
    invisible: TextLayerStats = field(default_factory=TextLayerStats)
    hidden_chars: int = 0
    uncaptured_raster: float = 0.0
    uncaptured_ink: Optional[float] = None
    judge_legibility: Optional[float] = None

    @property
    def searchable_scan(self) -> bool:
        """An invisible text layer over a page-covering scan, with (almost) no painted text."""
        return (self.invisible.chars > 0
                and self.image_coverage >= IMAGE_PAGE_COVERAGE
                and self.visible.weight < IMAGE_PAGE_TEXT_LIMIT)

    @property
    def text_layer(self) -> TextLayerStats:
        """The layer the page's text comes from: the OCR layer of a searchable scan, else the painted text."""
        return self.invisible if self.searchable_scan else self.visible

    @property
    def text_layer_illegible(self) -> bool:
        if self.text_layer.garbled:
            return True
        return self.judge_legibility is not None and self.judge_legibility < LEGIBILITY_JUDGE_THRESHOLD

    @property
    def uncaptured_content(self) -> bool:
        """No usable text layer, yet content only OCR of the render can read (inline or
        tiled pictures, vector outlines)."""
        return self.visible.weight < NO_TEXT_LIMIT and (
            self.uncaptured_raster >= MIN_UNCAPTURED_RASTER
            or (self.uncaptured_ink is not None and self.uncaptured_ink >= MIN_UNCAPTURED_INK))


def document_signals(pages: Sequence[PageSignals]) -> Tuple[float, float, float]:
    """(mean image coverage, mean legible text weight, share of garbled text pages)."""
    if not pages:
        return 0.0, 0.0, 0.0
    text_pages = [page for page in pages if page.visible.chars and not page.searchable_scan]
    illegible = sum(1 for page in text_pages if page.text_layer_illegible)
    return (
        sum(page.image_coverage for page in pages) / len(pages),
        sum(page.visible.weight for page in pages) / len(pages),
        illegible / len(text_pages) if text_pages else 0.0,
    )


def decide_page_route(page: PageSignals, document_route: Route) -> Tuple[Route, str]:
    """Route one page when an OCR provider is active: ``(route, reason)``.

    ``"image"``: OCR the page render, and the render is the page's only content.
    ``"text"``: emit the page's text layer and tables, OCR its embedded pictures.
    A page follows ``document_route`` unless one of these holds (first match wins):

    1. searchable scan: OCR the render and drop the invisible layer, one source only;
    2. its text layer is garbled (detector or judge): OCR the render;
    3. no usable text layer, but ink the text route cannot capture: OCR the render;
    4. in a text document, a clearly scanned page (see ``PAGE_OVERRIDE_MARGIN``): image;
    5. in an image document, a clearly text page (see ``PAGE_OVERRIDE_MARGIN``): text.
    """
    if page.searchable_scan:
        return "image", REASON_SEARCHABLE_SCAN
    if page.text_layer_illegible:
        return "image", REASON_ILLEGIBLE
    if page.uncaptured_content:
        return "image", REASON_NO_TEXT_LAYER
    if document_route == "text":
        if (page.image_coverage >= min(1.0, IMAGE_PAGE_COVERAGE * (1 + PAGE_OVERRIDE_MARGIN))
                and page.visible.weight < IMAGE_PAGE_TEXT_LIMIT * (1 - PAGE_OVERRIDE_MARGIN)):
            return "image", REASON_IMAGE_PAGE
    elif (page.image_coverage < IMAGE_PAGE_COVERAGE * (1 - PAGE_OVERRIDE_MARGIN)
          and page.visible.weight >= IMAGE_PAGE_TEXT_LIMIT * (1 + PAGE_OVERRIDE_MARGIN)):
        return "text", REASON_TEXT_PAGE
    return document_route, REASON_DOCUMENT


def pages_without_text(pages: Sequence[PageSignals]) -> List[int]:
    """0-based indexes of pages with (almost) no usable text layer whose content is in
    page-covering pictures or in ink only OCR can read: without OCR their content is
    not extracted."""
    return [
        index for index, page in enumerate(pages)
        if page.text_layer.weight < NO_TEXT_LIMIT
        and (page.image_coverage >= IMAGE_PAGE_COVERAGE or page.uncaptured_content)
    ]
