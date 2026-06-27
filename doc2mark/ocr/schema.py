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

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Table HTML sanitization                                                     #
# --------------------------------------------------------------------------- #
# Table.html is produced by a vision model reading a (possibly adversarial)
# document image and flows into rendered output via OCRPage.to_markdown(). To
# avoid an HTML-injection / XSS sink, it is sanitized to a strict allowlist of
# table-structural tags + span attributes; everything else is dropped.
_ALLOWED_TABLE_TAGS = frozenset({
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "col", "colgroup",
})
_ALLOWED_TABLE_ATTRS = frozenset({"colspan", "rowspan", "scope"})
_DANGEROUS_TAGS = (
    "script", "style", "iframe", "object", "embed", "link", "meta", "base",
    "form", "input", "button", "noscript", "template", "svg", "math",
)


def sanitize_table_html(html: str) -> str:
    """Sanitize model-produced table HTML to a strict table-only allowlist.

    Keeps only table-structural tags and ``colspan``/``rowspan``/``scope``
    attributes (cell text is preserved); drops scripts, styles, event handlers,
    URLs, and every other tag/attribute. Fails **closed**: returns ``""`` when the
    input is empty or cannot be parsed, so unsanitized HTML is never emitted.
    """
    if not html or not html.strip():
        return ""
    text = html.strip()
    # Strip a leading ```/```html ... ``` code fence a model might wrap it in.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text[: text.rfind("```")]
    text = text.strip()
    if not text:
        return ""
    try:
        from lxml import etree, html as lxml_html
        frag = lxml_html.fragment_fromstring(text, create_parent="div")
    except Exception:
        return ""  # fail closed — never emit unparsed LLM HTML
    # 1. Remove dangerous elements together with their text content.
    etree.strip_elements(frag, *_DANGEROUS_TAGS, with_tail=False)
    # 2. Unwrap every remaining non-allowlisted element (keeps inner text).
    #    strip_tags preserves the root wrapper, so nested <div>/<span>/<a>/... go.
    present = {e.tag for e in frag.iter() if isinstance(e.tag, str)}
    unwrap = tuple(t for t in present if t.lower() not in _ALLOWED_TABLE_TAGS)
    if unwrap:
        etree.strip_tags(frag, *unwrap)
    # 3. Drop every attribute outside the allowlist; require integer spans.
    for el in frag.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            name = attr.lower()
            if name not in _ALLOWED_TABLE_ATTRS:
                del el.attrib[attr]
            elif name in ("colspan", "rowspan") and not el.attrib[attr].strip().isdigit():
                del el.attrib[attr]
    inner = "".join(etree.tostring(child, encoding="unicode") for child in frag)
    return inner.strip()


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
    # Provenance: True if these are demo/sample values (a screenshot/mockup region),
    # not real data. Indexers should down-weight or skip illustrative rows.
    illustrative: bool = False
    row_count: Optional[int] = Field(
        default=None,
        description="For a header-only illustrative table, the number of sample rows "
                    "that were intentionally not transcribed.",
    )

    @field_validator("html")
    @classmethod
    def _sanitize_html(cls, value: str) -> str:
        """Sanitize model-supplied HTML at the boundary so the stored value is
        always safe to embed (see :func:`sanitize_table_html`)."""
        return sanitize_table_html(value)


class KeyValue(BaseModel):
    """A label/value pair, e.g. for forms and receipts."""
    label: str = ""
    value: str = ""
    illustrative: bool = False  # True for demo/sample values (screenshot/mockup region)


class Metric(BaseModel):
    """A single typed numeric assertion printed on the page.

    Additive structured view of a number that is ALSO present verbatim in
    ``raw.text`` — never a relocation of it. Flat by design (4 fields) to stay
    fillable on weaker models; the BM42 sparse index reads ``raw.text`` while this
    makes the number queryable as a typed fact.
    """
    label: str = Field(
        default="",
        description="Verbatim label this number belongs to, exactly as printed "
                    "(e.g. 'Net revenue', 'Uptime SLA'). Empty when no adjacent label.",
    )
    value: str = Field(
        default="",
        description="The number exactly as printed — VERBATIM, never normalized or "
                    "computed (e.g. '$4.2B', '98.5%', '3.2x', '< 100 ms'). Do not convert "
                    "'$4.2B' to '4200000000'. This exact string also appears in raw.text.",
    )
    unit: str = Field(
        default="",
        description="Unit/currency only when printed SEPARATELY from the value (e.g. a "
                    "column header 'USD' or 'ms'). Empty when the unit is inside `value`.",
    )
    illustrative: bool = Field(
        default=False,
        description="True ONLY for clearly demo/sample numbers on a product screenshot/"
                    "mockup. Mirrors Table.illustrative. Default False — real numbers never flagged.",
    )


class RawExtraction(BaseModel):
    """Verbatim transcription. No commentary, no inference. BM42 token source."""
    text: str = Field(
        default="",
        description="All visible text, verbatim, in the original language. No analysis.",
    )
    tables: List[Table] = Field(default_factory=list)
    fields: List[KeyValue] = Field(
        default_factory=list,
        description="label/value pairs for forms & receipts",
    )
    # Additive verbatim indexes — each entry is a COPY of tokens already in `text`
    # (BM42 stays intact), surfaced so the indexer can boost/filter without re-parsing.
    headings: List[str] = Field(
        default_factory=list,
        description="Heading/section-title lines, copied VERBATIM character-for-character "
                    "(do not paraphrase, translate, or normalize case), in top-to-bottom "
                    "order. Omit body text. Each entry MUST also appear in raw.text. Empty "
                    "list when the page has no headings.",
    )
    dates: List[str] = Field(
        default_factory=list,
        description="Every date/time reference on the page, copied VERBATIM as printed "
                    "(e.g. 'March 15 2025', 'Q3 FY2024', '2024-01-01'). No normalization. "
                    "Empty list when no dates appear.",
    )
    metrics: List[Metric] = Field(
        default_factory=list,
        description="Typed numeric assertions printed on the page (KPIs, revenue, percentages, "
                    "durations, counts). Only labeled/clearly-contextualized quantities; each "
                    "`value` is VERBATIM and also present in raw.text. Empty list when none — "
                    "an additive typed view, never a replacement for raw.text.",
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
        "document", "table", "form", "receipt", "handwriting", "code",
        "chart", "photo", "screenshot", "diagram", "infographic",
        "logo", "stamp", "mixed", "blank", "other",
    ] = "other"
    summary: str = Field(
        default="",
        description="1-3 sentence description of the content and its purpose.",
    )
    key_findings: List[str] = Field(default_factory=list)
    visual_notes: str = Field(
        default="",
        description="Layout, branding, and non-text visual elements. For a chart/diagram/"
                    "infographic, describe the trend/structure/message here (printed text "
                    "still goes verbatim into raw.text).",
    )
    # Retrieval / comprehension anchors (interpretation-only; never threaten raw verbatim).
    page_title: Optional[str] = Field(
        default=None,
        description="The single most prominent title/heading on the page, copied VERBATIM "
                    "(do not rephrase). The retrieval chunk anchor. Null when no clear single "
                    "title is present.",
    )
    primary_message: Optional[str] = Field(
        default=None,
        description="The single most important claim/conclusion/takeaway in ONE sentence — "
                    "what a reader retains. Grounded in text visibly on the page; no "
                    "speculation. Null for title/agenda/section-header/blank pages.",
    )
    keywords: List[str] = Field(
        default_factory=list,
        description="3-8 topical keywords / abbreviation expansions / domain synonyms NOT "
                    "already prominent in raw.text (e.g. expand 'ROI'->'return on investment', "
                    "add 'attrition' when the page says 'churn'). Do not repeat dominant "
                    "raw.text words. Empty list when nothing to add.",
    )
    entities: List[str] = Field(
        default_factory=list,
        description="5-15 named entities on the page: people (full names), organizations, "
                    "products, locations, key dates ('Q3 2024'), key amounts ('$4.2M revenue'). "
                    "Each a short noun phrase copied from the page, not a sentence. Omit generic "
                    "nouns. Empty list when none / low legibility.",
    )
    column_layout: Literal["single", "double", "multi", "complex"] = Field(
        default="single",
        description="Page column structure as visually observed: 'single' (default)=one "
                    "column; 'double'=two columns; 'multi'=3+; 'complex'=slide/magazine layout "
                    "(sidebar+main+callout). Signals when raw.text token order is interleaved.",
    )
    page_role: Optional[Literal[
        "title", "agenda", "section_header", "content", "data",
        "case_study", "comparison", "timeline", "conclusion", "appendix", "other",
    ]] = Field(
        default=None,
        description="Structural role within a deck/report: 'title'=cover; 'agenda'=TOC; "
                    "'section_header'=divider; 'content'=body; 'data'=tables/charts/metrics; "
                    "'case_study'; 'comparison'; 'timeline'; 'conclusion'; 'appendix'; 'other'. "
                    "Null for a standalone document page not part of a deck.",
    )
    primary_date: Optional[str] = Field(
        default=None,
        description="The single date the page is 'about' (publication/invoice/meeting/version), "
                    "copied VERBATIM. Must be one of the strings in raw.dates (never invented). "
                    "Null when no date is contextually prominent.",
    )
    action_items: List[str] = Field(
        default_factory=list,
        description="Explicit tasks / next steps / recommendations stated on the page. Only "
                    "when the page explicitly frames something as an action — never inferred. "
                    "A concise paraphrase (the verbatim form is already in raw.text). Empty "
                    "list when none.",
    )
    definitions: List[KeyValue] = Field(
        default_factory=list,
        description="Term/definition pairs from glossaries/callouts/sidebars, using label=term, "
                    "value=definition. illustrative is always False. Empty list when none.",
    )
    self_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="The model's own 0..1 confidence estimate.",
    )
    legibility: Literal["high", "medium", "low"] = "high"
    content_fidelity: Literal["verbatim", "described", "caption", "skipped", "mixed"] = Field(
        default="verbatim",
        description=(
            "Which extraction policy the router applied. 'verbatim' = all printed text "
            "transcribed; 'described'/'caption' = some printed values intentionally "
            "withheld (meaning is in this interpretation); 'skipped' = blank. Only a "
            "'screenshot' document_type may pair 'described' with withheld text."
        ),
    )


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
        interp = self.interpretation
        # Title anchor: its verbatim copy is already in raw.text, so only prepend
        # when raw.text does not already start with it (avoid duplicating tokens).
        if interp is not None and interp.page_title:
            title = interp.page_title.strip()
            if title and not raw.text.lstrip().startswith(title):
                parts.append(f"# {title}")
        if raw.text:
            parts.append(raw.text.strip())
        for table in raw.tables:
            if table.html:
                parts.append(table.html.strip())
            elif table.markdown:
                parts.append(table.markdown.strip())
            elif table.headers or table.rows:
                parts.append(_render_table(table))
        # Typed metrics give a degraded-render payoff for the numeric index; skip
        # illustrative (sample/mockup) values so they never read as real data.
        real_metrics = [m for m in raw.metrics if not m.illustrative]
        if real_metrics:
            parts.append(_render_metrics(real_metrics))
        return "\n\n".join(p for p in parts if p)


def _render_metrics(metrics: List["Metric"]) -> str:
    """Render non-illustrative metrics as a compact markdown table."""
    lines = ["| Metric | Value |", "| --- | --- |"]
    for m in metrics:
        label = (m.label or "").strip() or "—"
        value = (m.value or "").strip()
        unit = (m.unit or "").strip()
        if unit and unit not in value:
            value = f"{value} {unit}".strip()
        lines.append(f"| {label} | {value} |")
    return "\n".join(lines)


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


def router_invariants(page: "OCRPage") -> List[str]:
    """Return the router firewall violations for a structured page (empty = OK).

    Protects the BM42 invariant: real printed values are never withheld (marked
    ``illustrative``) except on a high-confidence ``screenshot`` page. Intended as
    a CI/eval assertion over recorded structured outputs.
    """
    violations: List[str] = []
    raw = page.raw
    interp = page.interpretation
    has_illustrative = (
        any(t.illustrative for t in raw.tables)
        or any(f.illustrative for f in raw.fields)
        or any(m.illustrative for m in raw.metrics)
    )
    dtype = interp.document_type if interp else None
    fidelity = interp.content_fidelity if interp else "verbatim"

    # 1. Withheld/illustrative data may appear ONLY on a screenshot.
    if has_illustrative and dtype != "screenshot":
        violations.append(
            f"illustrative content on document_type={dtype!r}; only 'screenshot' may withhold values"
        )
    # 2. A withholding screenshot must be high-confidence and legible.
    if dtype == "screenshot" and has_illustrative and interp is not None:
        if interp.self_confidence < 0.7 or interp.legibility != "high":
            violations.append(
                "screenshot withheld values with self_confidence<0.7 or legibility!='high' "
                "(should have fallen back to verbatim)"
            )
    # 3. described/caption must carry meaning in the interpretation.
    if fidelity in ("described", "caption") and (interp is None or not interp.summary.strip()):
        violations.append(f"content_fidelity={fidelity!r} but interpretation.summary is empty")
    # 4. skipped implies an empty raw layer.
    if fidelity == "skipped" and (raw.text.strip() or raw.tables or raw.fields):
        violations.append("content_fidelity='skipped' but raw is not empty")
    # 5. primary_date must be selected from the verbatim raw.dates list, not invented.
    if interp is not None and interp.primary_date and interp.primary_date not in raw.dates:
        violations.append(
            "interpretation.primary_date not present in raw.dates (must be selected from them)"
        )
    return violations


__all__ = [
    "Table",
    "KeyValue",
    "Metric",
    "RawExtraction",
    "Interpretation",
    "OCRPage",
    "sanitize_table_html",
    "router_invariants",
]
