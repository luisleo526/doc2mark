"""The questions the optional judge asks, one per hook, versioned.

Each question is a TypeSafe Noul (the probability that a yes/no statement holds) over a
small, named JSON state built from what the hook receives. A question's ``version`` is
part of every cached verdict's key: change the wording, the criteria or the state and
bump the version, so no verdict of the old question is replayed for the new one.

``RAW_THRESHOLDS`` are the Jev probabilities at which each hook acts, calibrated on the
TRAIN split of the labelled sets in ``tests/data/judge`` (``eval/judge_eval.py``) for the
pinned model; ``RAW_SUSPECT_THRESHOLDS`` mark the band below that in which a hook only flags
its case. Re-validate them before moving the pin.
"""

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

#: The model every question is asked of: a pinned version, never an alias (``jev-latest``
#: moves when a new release ships, and the thresholds below belong to this version).
MODEL = "jev-1.13.0"

#: Price of the pinned model per input token (output tokens are free), in US dollars.
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000

#: Longest page-text sample the legibility question sends (characters): a longer page is
#: sampled at its beginning, middle and end, a third each, joined by ``SAMPLE_GAP``.
MAX_PAGE_CHARS = 1500
SAMPLE_GAP = "\n[...]\n"

_REPLACEMENT = chr(0xFFFD)


@dataclass(frozen=True)
class Question:
    """One Noul: the hook it serves, its version and its text."""

    hook: str
    version: str
    instructions: str
    true: str
    false: str

    def payload(self) -> Dict[str, Any]:
        """The question as the TypeSafe API takes it (the SDK accepts plain dictionaries)."""
        return {"type": "noul", "instructions": self.instructions,
                "criteria": {"true": self.true, "false": self.false}}


LEGIBILITY = Question(
    hook="legibility",
    version="legibility-v2",
    instructions=(
        "`page_text` was extracted from the text layer of one PDF page (a long page is sampled: its "
        "beginning, middle and end, separated by [...]). Is it legible content that a person could read, "
        "in any language or script? Legible content includes prose, headings, short "
        "slide labels, tables of numbers, lists, part numbers, hashes, formulas and source code. It is NOT "
        "legible when it is garbled by a broken text layer: nonsense letter sequences, systematically "
        "substituted or shifted characters, mojibake, placeholder glyph codes such as (cid:12), private-use "
        f"glyphs, or replacement characters ({_REPLACEMENT})."
    ),
    true="Most of the text is legible, meaningful content.",
    false="Most of the text is garbled nonsense, substituted characters, mojibake or placeholders.",
)

BOILERPLATE = Question(
    hook="boilerplate",
    version="boilerplate-v2",
    instructions=(
        "`line` is printed in the same place near the top or bottom edge of many pages of one document "
        "(`where`, `font`). Is it page furniture, so that one copy of it is enough and its repeats on the "
        "other pages can be removed without losing information: a company, brand or product name or "
        "tagline, logo text, a confidentiality or classification marking, a copyright notice, a website or "
        "contact line, a page or slide number, or the same print date on every page? Answer no when a "
        "reader needs the line on each page where it appears: a title or label that names what is on the "
        "page (for example 'Lesson 3', 'Exhibit 4', a section or statement title), a unit note such as "
        "'(in thousands of EUR)', a disclaimer or legal note, a table header, or a date, name, account or "
        "ID that belongs to the content."
    ),
    true="Page furniture: branding, logo text, a marking, copyright, contact details or page numbering; one copy is enough.",
    false="A reader needs the line where it appears: a title, label, unit, legal note, header, date, name or ID.",
)

NON_CONTENT = Question(
    hook="non_content",
    version="non-content-v1",
    instructions=(
        "An OCR model was asked to transcribe or describe an image from a document, and `ocr_answer` is "
        "its answer. Is `ocr_answer` only a refusal, an apology, an error message, or a statement that the "
        "image has no readable text, with no text or description taken from the image itself? Answer no "
        "when `ocr_answer` contains any transcribed text, table, label, or description of what the image "
        "shows, even if that content itself mentions apologies, errors, or blank pages."
    ),
    true="The answer is only a refusal, apology, error or 'no text' statement from the OCR model.",
    false="The answer contains content transcribed or described from the image.",
)

QUESTIONS: Mapping[str, Question] = {q.hook: q for q in (LEGIBILITY, BOILERPLATE, NON_CONTENT)}

#: Jev probability from which each hook acts (legibility: a page is illegible *below* it;
#: boilerplate and non_content: the line is chrome / the answer is no content *at or
#: above* it). See the module docstring.
RAW_THRESHOLDS: Mapping[str, float] = {"legibility": 0.8, "boilerplate": 0.7, "non_content": 0.95}
#: Jev probability from which a hook that does not act yet flags its case (non_content: the
#: answer is kept, with ``metadata["non_content_suspected"]``).
RAW_SUSPECT_THRESHOLDS: Mapping[str, float] = {"non_content": 0.9}


# --- state -------------------------------------------------------------------


def legibility_state(page_text: str) -> Dict[str, Any]:
    """The page text the legibility question reads: all of it up to ``MAX_PAGE_CHARS``
    characters; a longer page is sampled at its beginning, middle and end (a third of the
    budget each, cut at line breaks when one is near), so a legible opening cannot hide a
    garbled body."""
    text = (page_text or "").strip()
    if len(text) <= MAX_PAGE_CHARS:
        return {"page_text": text}
    part = MAX_PAGE_CHARS // 3
    head = text[:part]
    cut = head.rfind("\n", part // 2)
    head = head[:cut] if cut > 0 else head
    middle_start = len(text) // 2 - part // 2
    middle = text[middle_start:middle_start + part]
    tail = text[-part:]
    pieces = []
    for piece, trim_start, trim_end in ((head, False, False), (middle, True, True), (tail, True, False)):
        if trim_start:
            start = piece.find("\n", 0, part // 2)
            piece = piece[start + 1:] if start >= 0 else piece
        if trim_end:
            end = piece.rfind("\n", len(piece) // 2)
            piece = piece[:end] if end > 0 else piece
        pieces.append(piece.strip())
    return {"page_text": SAMPLE_GAP.join(pieces)}


def _font_words(size: Optional[float], body: Optional[float]) -> str:
    try:
        size, body = float(size), float(body)
    except (TypeError, ValueError):
        return "unknown size"
    if size <= 0 or body <= 0:
        return "unknown size"
    if size < 0.85 * body:
        return "smaller than the body text"
    if size > 1.15 * body:
        return "larger than the body text"
    return "the same size as the body text"


def boilerplate_state(line_text: str, context: Mapping[str, Any]) -> Dict[str, Any]:
    """The line and, in words, where and how often it repeats (numbers and sizes are
    turned into words: the model reads semantics better than arithmetic)."""
    context = context or {}
    zone = context.get("zone")
    edge = {"header": "at the top of the page", "footer": "at the bottom of the page"}.get(zone, "near a page edge")
    pages = context.get("repeated_on") or context.get("pages") or []
    count = context.get("page_count")
    where = edge
    if pages and count:
        where = f"{edge}, in the same place on {len(pages)} of the {count} pages"
    state = {
        "line": " ".join((line_text or "").split()),
        "where": where,
        "font": _font_words(context.get("font_size"), context.get("body_font_size")),
    }
    if context.get("reason") == "numbered_label":
        state["note"] = "The number in this line goes up by one from each page to the next, like a page number."
    return state


def non_content_state(answer: str) -> Dict[str, Any]:
    """The OCR answer as the model wrote it."""
    return {"ocr_answer": (answer or "").strip()}


STATE_BUILDERS = {
    "legibility": legibility_state,
    "boilerplate": boilerplate_state,
    "non_content": non_content_state,
}
