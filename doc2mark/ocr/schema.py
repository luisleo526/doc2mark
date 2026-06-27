"""Structured OCR output schema for doc2mark.

The redesigned OCR layer returns a *structured* result instead of a single
free-form markdown blob. Each image becomes an :class:`OCRPage` with a hard
boundary between two concerns:

- ``raw``: what is literally on the page (verbatim transcription, tables,
  label/value fields) — no inference, no commentary.
- ``interpretation``: the model's reading of the page (document type, summary,
  key findings) — omitted for ``detail="raw"`` and for non-LLM providers
  (e.g. Tesseract) that cannot infer.

These models are emitted by the LLM providers via LangChain's
``with_structured_output(method="json_schema")``. Every field is defaulted so
that OpenAI strict mode (which requires all properties to be present) is
satisfiable, and Optional fields serialize as ``anyOf: [T, null]``.
"""

from typing import List, Optional, Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# RAW: what is literally on the page                                          #
# --------------------------------------------------------------------------- #
class Table(BaseModel):
    """A table transcribed verbatim from the image.

    ``html`` is the preferred representation: a clean ``<table>`` that can encode
    merged cells via ``colspan``/``rowspan`` (which ``headers``/``rows`` and
    markdown cannot). ``headers``/``rows`` remain a best-effort flat view for
    simple, machine-readable access.
    """
    caption: str = ""
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)
    html: str = Field(
        default="",
        description=(
            "Clean, valid HTML for this table using <table>/<tr>/<th>/<td>, with "
            "colspan and rowspan for merged cells. No CSS, classes, or inline styles."
        ),
    )
    # Rendered markdown fallback for simple (non-merged) tables.
    markdown: str = ""


class KeyValue(BaseModel):
    """A label/value pair, e.g. for forms and receipts."""
    label: str = ""
    value: str = ""


class RawExtraction(BaseModel):
    """Verbatim transcription. No commentary, no inference."""
    text: str = Field(
        default="",
        description="All visible text, verbatim, in the original language. No analysis.",
    )
    tables: List[Table] = Field(default_factory=list)
    fields: List[KeyValue] = Field(
        default_factory=list,
        description="label/value pairs for forms & receipts",
    )
    detected_language: Optional[str] = Field(
        default=None,
        description="The language actually seen on the page (not an echo of config).",
    )
    has_handwriting: bool = False


# --------------------------------------------------------------------------- #
# INTERPRETATION: the model's analysis (omitted when detail="raw")            #
# --------------------------------------------------------------------------- #
class Interpretation(BaseModel):
    """The model's reading of the page. Never mixed into ``raw``."""
    document_type: Literal[
        "document", "table", "form", "receipt", "handwriting",
        "code", "chart", "photo", "mixed", "blank", "other",
    ] = "other"
    summary: str = Field(
        default="",
        description="1-3 sentence description of the content and its purpose.",
    )
    key_findings: List[str] = Field(default_factory=list)
    reading_order: List[int] = Field(
        default_factory=list,
        description="Block indices in natural reading order, top-to-bottom.",
    )
    visual_notes: str = Field(
        default="",
        description="Layout, branding, and non-text visual elements.",
    )
    self_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="The model's own 0..1 confidence estimate.",
    )
    legibility: Literal["high", "medium", "low"] = "high"


# --------------------------------------------------------------------------- #
# TOP LEVEL                                                                    #
# --------------------------------------------------------------------------- #
class OCRPage(BaseModel):
    """One image's structured OCR result, carried on ``OCRResult.document``."""
    raw: RawExtraction = Field(default_factory=RawExtraction)
    # None for detail="raw", non-LLM providers, and parse-error fallback.
    interpretation: Optional[Interpretation] = None

    def to_markdown(self) -> str:
        """Render a readable markdown view of this page.

        Used as the back-compat ``OCRResult.text`` and by pipelines that want a
        single string. Prefers structured tables/fields over the flat text dump.
        """
        parts: List[str] = []
        raw = self.raw
        if raw.text:
            parts.append(raw.text.strip())
        for table in raw.tables:
            if table.html:
                parts.append(table.html.strip())
            elif table.markdown:
                parts.append(table.markdown.strip())
            elif table.headers or table.rows:
                parts.append(_render_table(table))
        return "\n\n".join(p for p in parts if p)


def _render_table(table: Table) -> str:
    """Render a simple markdown table from headers + rows."""
    lines: List[str] = []
    if table.caption:
        lines.append(table.caption.strip())
    width = len(table.headers) or (len(table.rows[0]) if table.rows else 0)
    if not width:
        return "\n".join(lines)
    headers = table.headers or [""] * width
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in table.rows:
        cells = list(row) + [""] * (width - len(row))
        lines.append("| " + " | ".join(cells[:width]) + " |")
    return "\n".join(lines)


__all__ = [
    "Table",
    "KeyValue",
    "RawExtraction",
    "Interpretation",
    "OCRPage",
]
