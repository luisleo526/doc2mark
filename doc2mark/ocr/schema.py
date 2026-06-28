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

import re
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
            "Clean, valid HTML for this table using <table>/<tr>/<th>/<td>. Preserve "
            "the FULL grid: emit ONE <tr> per visual row and one cell per column — "
            "NEVER flatten a multi-row table into a single row or a single header. "
            "Use colspan for a cell that spans columns (e.g. a group header above "
            "several columns) and rowspan for a cell that spans rows (e.g. a row "
            "label covering several rows). Keep the top-left corner cell (often "
            "empty) when the table has both row and column headers; first-column "
            "labels are <th> cells. Cell text is verbatim. No CSS, classes, ids, or "
            "inline styles."
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
# NESTED STRUCTURES (interpretation-layer): figures, hierarchy, knowledge graph #
# Shallow by design (max depth 4: OCRPage->interpretation->figures->data_points);#
# no recursion, no model-unions, all fields defaulted — to stay fillable under  #
# with_structured_output(json_schema). Verbatim strings mirror raw.text (BM42). #
# --------------------------------------------------------------------------- #
class DataPoint(BaseModel):
    """One (category, value, series) reading from a chart, flattened to tidy-long
    form (the deepest leaf, depth 4). label/value/series are VERBATIM copies of
    text also in raw.text; never pixel-estimated."""
    label: str = Field(
        default="",
        description="Verbatim x-axis / category label exactly as printed (e.g. 'Q3 2024'). "
                    "Empty when unlabelled. MUST also appear in raw.text. Never emit a point "
                    "whose label AND value are both empty.",
    )
    value: str = Field(
        default="",
        description="Verbatim value exactly as printed (e.g. '$4.2B', '38%'). Never "
                    "pixel-estimated/interpolated — leave empty if no printed data label. MUST "
                    "also appear in raw.text. If NO point has a printed value, leave data_points "
                    "empty and use Figure.trend.",
    )
    series: str = Field(
        default="",
        description="Verbatim legend/series label this point belongs to (e.g. 'Revenue'). Empty "
                    "for a single-series chart. When non-empty MUST also appear in raw.text.",
    )


class DiagramNode(BaseModel):
    """One labelled box/shape/actor in a flowchart, org chart, or network. No
    synthetic id — edges reference nodes by verbatim label (already in raw.text)."""
    label: str = Field(
        default="",
        description="Verbatim text printed in/beside the node (e.g. 'Approve Request', 'CFO'). "
                    "MUST also appear in raw.text. Never emit a node whose label AND kind are "
                    "both empty.",
    )
    kind: str = Field(
        default="",
        description="Shape/role hint — one of 'start','end','process','decision','data',"
                    "'entity','actor','swimlane','annotation'. Empty when unclear. Interpretive, "
                    "NOT a verbatim token.",
    )


class DiagramEdge(BaseModel):
    """A directed connection between two DiagramNodes, referenced by verbatim label
    (membership in nodes is mechanically checkable, like primary_date-in-raw.dates)."""
    from_label: str = Field(
        default="",
        description="Verbatim label of the SOURCE node — must match a DiagramNode.label in this "
                    "Figure.nodes (and is therefore in raw.text).",
    )
    to_label: str = Field(
        default="",
        description="Verbatim label of the TARGET node — must match a DiagramNode.label in this "
                    "Figure.nodes (and is therefore in raw.text).",
    )
    label: str = Field(
        default="",
        description="Verbatim label on the arrow/connector (e.g. 'Yes', 'Approved'). Empty when "
                    "unlabelled; when non-empty MUST also appear in raw.text.",
    )


class Figure(BaseModel):
    """Typed structured view of ONE chart/diagram/infographic panel, in
    interpretation.figures (flat list, never nested in each other). ``kind`` drives
    which branch fills: quantitative->data_points; structural->nodes+edges; else
    ->labels+meaning. Every verbatim string is an ADDITIVE copy of raw.text;
    ``meaning``/``trend`` are the always-attempt interpretive fallbacks."""
    kind: Literal[
        "bar", "line", "pie", "scatter", "area", "combo", "table_visual",
        "flowchart", "org_chart", "network", "timeline_diagram", "map",
        "infographic_panel", "other",
    ] = Field(
        default="other",
        description="Visual type — quantitative (bar/line/pie/scatter/area/combo)->data_points; "
                    "structural (flowchart/org_chart/network)->nodes+edges; timeline_diagram/map/"
                    "infographic_panel/table_visual->labels+meaning; other->meaning only. Always "
                    "set; fill ONE branch and leave the other's lists empty.",
    )
    title: str = Field(default="", description="Verbatim figure title/caption; empty when none; also in raw.text.")
    x_axis: str = Field(default="", description="Verbatim x-axis label; empty for non-chart/unlabelled; also in raw.text.")
    y_axis: str = Field(default="", description="Verbatim y-axis label; empty for non-chart/unlabelled; also in raw.text.")
    data_points: List[DataPoint] = Field(
        default_factory=list,
        description="Flattened chart readings — ONLY for quantitative kinds and ONLY when both "
                    "category label AND printed value are legible. Leave EMPTY (no value-less "
                    "shells) when values are unreadable/pixel-estimated — use trend. Empty for "
                    "diagram/infographic kinds.",
    )
    trend: str = Field(
        default="",
        description="1-sentence trend conclusion for chart kinds (paraphrase, not verbatim). The "
                    "graceful-degradation path when data_points cannot be filled. Empty for "
                    "non-chart kinds.",
    )
    nodes: List[DiagramNode] = Field(
        default_factory=list,
        description="Diagram nodes — ONLY for structural kinds. Empty for chart/infographic kinds.",
    )
    edges: List[DiagramEdge] = Field(
        default_factory=list,
        description="Directed connections — only when nodes non-empty; each from_label/to_label "
                    "must match a node label. Empty for chart/infographic kinds.",
    )
    labels: List[str] = Field(
        default_factory=list,
        description="BM42 completeness catch-all: ALL verbatim text in this visual NOT already in "
                    "title/x_axis/y_axis/data_points/nodes/edges — legend entries, callouts, "
                    "footnotes, scale markers, units, sources. Each MUST also appear in raw.text. "
                    "When in doubt, repeat the label here.",
    )
    meaning: str = Field(
        default="",
        description="1-sentence message/conclusion this visual asserts (paraphrase). Applies to "
                    "ALL kinds. The minimum useful output even when every other field is empty — "
                    "ALWAYS attempt to fill it.",
    )
    illustrative: bool = Field(
        default=False,
        description="True ONLY for clearly demo/mockup data in a product-screenshot context (same "
                    "gate as Table.illustrative). Default False — real visuals never flagged.",
    )


class Section(BaseModel):
    """One heading-delimited region, in reading order. Hierarchy is a FLAT list +
    int ``level`` (never recursive children). ``heading`` is a VERBATIM raw.headings
    entry; summary/key_points are paraphrase."""
    heading: str = Field(
        default="",
        description="Heading copied VERBATIM. MUST be one of raw.headings (and thus raw.text). "
                    "Create a Section ONLY for actual headings/section titles — NOT body "
                    "paragraphs/captions/footers. No paraphrase/translate/case-normalize.",
    )
    level: int = Field(
        default=1,
        description="Heading depth 1..6 from visual prominence (size/weight/indent/caps). Use 1 "
                    "when ambiguous or single-level. Do not invent non-visible levels.",
    )
    summary: str = Field(
        default="",
        description="1-2 sentence paraphrase of what this section covers (interpretation, NOT "
                    "verbatim). Empty for a heading-only divider.",
    )
    key_points: List[str] = Field(
        default_factory=list,
        description="1-5 concise paraphrased takeaways under this heading, one sentence each. "
                    "Empty for a divider/visual-only region. Do NOT copy raw.text verbatim. Never "
                    "exceed 5.",
    )


class Entity(BaseModel):
    """A typed named entity (replaces the flat entities list). Additive view of a
    name already VERBATIM in raw.text. Dates/money/KPIs stay in raw.dates/raw.metrics."""
    name: str = Field(
        default="",
        description="Entity name VERBATIM as printed, never normalized. MUST also appear in "
                    "raw.text. Do not emit a name=='' entity. Cap ~15 entities.",
    )
    type: Literal["person", "org", "product", "location", "concept", "other"] = Field(
        default="other",
        description="'person'=named individual; 'org'=company/institution/team; 'product'=named "
                    "product/service/brand; 'location'=named place; 'concept'=a named domain "
                    "term; 'other'. Dates/money/KPIs are NOT entities (they live in raw.*). Pick "
                    "the most specific.",
    )
    salience: Literal["primary", "secondary", "mentioned"] = Field(
        default="mentioned",
        description="Centrality: 'primary'=a subject of the page (MAX 3); 'secondary'=supports "
                    "the main claim; 'mentioned'=in passing (default). Never >3 'primary'.",
    )
    role: str = Field(
        default="",
        description="The entity's role AS STATED on the page — short noun phrase ≤8 words (e.g. "
                    "'CEO', 'acquiring company'). Do not invent. Empty when none stated.",
    )


class Relation(BaseModel):
    """A knowledge triple for a claim EXPLICITLY stated on the page (never inferred).
    Flat triple of strings; ``evidence`` makes it falsifiable. Highest confabulation
    risk — emit only when explicit."""
    subject: str = Field(
        default="",
        description="Subject — a verbatim entity name / shortest identifying phrase from the "
                    "page (MUST appear in raw.text). Do not construct one not on the page.",
    )
    relation: str = Field(
        default="",
        description="Predicate — short active verb phrase (2-6 words) matching the page's claim "
                    "(e.g. 'acquired', 'is CEO of'). No hedged predicates. Paraphrase OK.",
    )
    object: str = Field(
        default="",
        description="Object — a verbatim entity name / metric / phrase from the page (MUST "
                    "appear in raw.text). May be a quantity/date/entity name. Do not construct "
                    "one not on the page.",
    )
    evidence: str = Field(
        default="",
        description="The verbatim quote / close on-page paraphrase supporting this triple. If "
                    "you cannot identify supporting on-page text, DO NOT emit the Relation. From "
                    "the page only; never world knowledge.",
    )


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
    figures: List[Figure] = Field(
        default_factory=list,
        description="One Figure per distinct chart/diagram/infographic panel, structuring its "
                    "MEANING as data. Populated for chart/diagram/infographic content; empty for "
                    "pure-prose or photo pages. Complements (never replaces) visual_notes. Every "
                    "verbatim string inside is also in raw.text.",
    )
    sections: List[Section] = Field(
        default_factory=list,
        description="Flat reading-order list of Section objects; use Section.level to rebuild the "
                    "heading tree (do NOT nest children). Roughly one entry per raw.headings line. "
                    "Empty list when no headings. Never emit a heading not in raw.headings.",
    )
    typed_entities: List[Entity] = Field(
        default_factory=list,
        description="Typed named entities (replaces the old flat entities list). Up to ~15; each "
                    "name is VERBATIM and also in raw.text. Empty when none / low legibility. "
                    "Dates/money/KPIs stay in raw.dates / raw.metrics, not here.",
    )
    relations: List[Relation] = Field(
        default_factory=list,
        description="Up to ~10 knowledge triples for claims EXPLICITLY stated on the page. Empty "
                    "for purely visual pages, forms with no assertions, low legibility, or when "
                    "self_confidence < 0.7. Both subject and object are substrings of raw.text. "
                    "Never assert world-knowledge not printed here.",
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
    page_markdown: Optional[str] = Field(
        default=None,
        description=(
            "Clean, structured Markdown rendering of this WHOLE-PAGE image render — filled "
            "ONLY when explicitly instructed (image-strategy slide/scan pages); leave null "
            "otherwise. When filled it MUST: (a) cover EVERY word, number and CJK character "
            "from raw.text verbatim — drop nothing, paraphrase nothing, translate nothing; "
            "(b) add structure — '## ' for the page title, '### ' for sub-sections, numbered/"
            "bulleted lists for cards, 'A → B → C' arrow chains for process/flow diagrams; "
            "(c) for any table/grid region write a short '[see table]' placeholder (the "
            "authoritative HTML is in raw.tables) rather than re-transcribing it. It "
            "RE-LAYOUTS the text already in raw.text into a readable document; it never "
            "adds or removes content."
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

        # Synthesis fast-path: a structured page_markdown rendering REPLACES the flat
        # raw.text dump for whole-page image renders — BUT only when it verifiably
        # covers the verbatim text (this rendered string is the BM42 feed; the OCRPage
        # object is discarded downstream). Authoritative table HTML is appended for
        # spans, and any uncovered verbatim line is preserved in a hidden tail so BM42
        # keeps every token. If it under-covers (paraphrase/truncation), fall through
        # to the standard verbatim rendering — never worse than today.
        md = (interp.page_markdown or "").strip() if interp is not None else ""
        if md:
            table_text = " ".join((t.html or t.markdown or "") for t in raw.tables)
            covered, missing = _coverage(raw.text, md + "\n" + table_text)
            if covered >= _SYNTH_COVERAGE_MIN:
                out = [md]
                for table in raw.tables:
                    if table.html:
                        out.append(table.html.strip())
                    elif table.markdown:
                        out.append(table.markdown.strip())
                    elif table.headers or table.rows:
                        out.append(_render_table(table))
                if missing:
                    out.append("<!-- raw-verbatim-tail\n" + "\n".join(missing) + "\n-->")
                return "\n\n".join(p for p in out if p)

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
        # Nested overlays (numeric facts first, then figures, then a navigational
        # section outline). Each renderer is degraded-safe and never re-dumps raw.text.
        if interp is not None:
            fig_md = _render_figures(interp.figures)
            if fig_md:
                parts.append(fig_md)
            sec_md = _render_sections(interp.sections)
            if sec_md:
                parts.append(sec_md)
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


def _render_figures(figures: List["Figure"]) -> str:
    """Render non-illustrative figures degraded-safe. Never renders figure.labels
    (those tokens are already in raw.text) and skips a figure with nothing to show."""
    blocks: List[str] = []
    for fig in figures:
        if fig.illustrative:
            continue
        title = (fig.title or "").strip()
        head = f"**Figure: {title}**" if title else f"**Figure ({fig.kind})**"
        sub = " ".join(s for s in ((fig.meaning or "").strip(), (fig.trend or "").strip()) if s)
        body: List[str] = []
        pts = [p for p in fig.data_points if (p.label or "").strip() or (p.value or "").strip()]
        if pts:
            if any((p.series or "").strip() for p in pts):
                body.append("| Series | Category | Value |")
                body.append("| --- | --- | --- |")
                for p in pts:
                    body.append(f"| {(p.series or '').strip()} | {(p.label or '').strip()} | {(p.value or '').strip()} |")
            else:
                body.append("| Category | Value |")
                body.append("| --- | --- |")
                for p in pts:
                    body.append(f"| {(p.label or '').strip()} | {(p.value or '').strip()} |")
        edge_lines: List[str] = []
        connected: set = set()
        for e in fig.edges:
            frm, to = (e.from_label or "").strip(), (e.to_label or "").strip()
            if frm or to:
                arrow = f" --{e.label.strip()}-->" if (e.label or "").strip() else " -->"
                edge_lines.append(f"- {frm}{arrow} {to}".rstrip())
                connected.update({e.from_label, e.to_label})
        for n in fig.nodes:
            if n.label and n.label not in connected:
                kind = f" ({n.kind.strip()})" if (n.kind or "").strip() else ""
                edge_lines.append(f"- {n.label.strip()}{kind}")
        if not (sub or body or edge_lines):
            continue
        lines = [head]
        if sub:
            lines.append(f"*{sub}*")
        lines += body + edge_lines
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_sections(sections: List["Section"]) -> str:
    """Render a section outline ONLY when sections add paraphrase value (the verbatim
    headings are already in raw.text)."""
    if not any((s.summary or "").strip() or s.key_points for s in sections):
        return ""
    lines = ["**Section outline**"]
    for s in sections:
        heading = (s.heading or "").strip()
        if not heading:
            continue
        indent = "  " * max(0, s.level - 1)
        lines.append(f"{indent}- {heading}")
        if (s.summary or "").strip():
            lines.append(f"{indent}  {s.summary.strip()}")
        for kp in s.key_points:
            if (kp or "").strip():
                lines.append(f"{indent}  - {kp.strip()}")
    return "\n".join(lines) if len(lines) > 1 else ""


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


def _norm_ws(s: str) -> str:
    """Collapse all whitespace runs to single spaces for verbatim-substring checks.

    A model echoing a multi-line label naturally renders the line breaks as spaces
    (and may join a stacked list), so an exact substring test raises false BM42
    alarms. Normalizing whitespace on both sides keeps the check meaningful (the
    same tokens in the same order) without flagging pure re-spacing.
    """
    return " ".join((s or "").split())


# Use the synthesized page_markdown for display when it covers at least this fraction
# of raw.text's tokens; the verbatim-tail carries the residual so BM42 stays complete.
# Below this floor the render likely summarized away content -> fall back to raw.text.
_SYNTH_COVERAGE_MIN = 0.85

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.,%$/+\-]*|[一-鿿぀-ヿ]{2,}")


def _coverage(raw_text: str, rendered: str):
    """Fraction of raw_text's content TOKENS that appear in ``rendered``, plus the
    sorted list of tokens that do NOT. Token-based (not line-based) so short CJK
    labels — most of a slide's content — are actually checked. A token is a Latin/
    numeric word or a CJK/Kana run of >=2 chars. Used by to_markdown() to gate the
    synthesized page_markdown against verbatim loss and to build the hidden
    verbatim-tail (BM42 keeps every token even if page_markdown drops one)."""
    r = _norm_ws(rendered)
    toks = [t for t in _TOKEN_RE.findall(raw_text or "") if len(t) >= 2]
    if not toks:
        return 1.0, []
    missing = [t for t in toks if t not in r]
    return 1 - len(missing) / len(toks), sorted(set(missing))


def router_invariants(page: "OCRPage") -> List[str]:
    """Return the router firewall violations for a structured page (empty = OK).

    Protects the BM42 invariant: real printed values are never withheld (marked
    ``illustrative``) except on a high-confidence ``screenshot`` page. Verbatim
    substring checks are whitespace-normalized (see :func:`_norm_ws`). Intended as
    a CI/eval assertion over recorded structured outputs.
    """
    violations: List[str] = []
    raw = page.raw
    interp = page.interpretation
    text_n = _norm_ws(raw.text or "")
    headings_n = {_norm_ws(h) for h in raw.headings}
    figs = interp.figures if interp else []
    has_illustrative = (
        any(t.illustrative for t in raw.tables)
        or any(f.illustrative for f in raw.fields)
        or any(m.illustrative for m in raw.metrics)
        or any(fig.illustrative for fig in figs)          # Figure gated like Table
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
    if (interp is not None and interp.primary_date
            and _norm_ws(interp.primary_date) not in {_norm_ws(d) for d in raw.dates}):
        violations.append(
            "interpretation.primary_date not present in raw.dates (must be selected from them)"
        )

    if interp is None:
        return violations

    # 6. FIGURES — every VERBATIM figure string must be a substring of raw.text
    #    (interpretive meaning/trend and node.kind are NOT checked).
    for i, fig in enumerate(figs):
        verbatim: List[str] = [fig.title, fig.x_axis, fig.y_axis, *fig.labels]
        for p in fig.data_points:
            verbatim += [p.label, p.value, p.series]
        node_labels = {_norm_ws(n.label) for n in fig.nodes if n.label}
        for n in fig.nodes:
            verbatim.append(n.label)
        for e in fig.edges:
            verbatim += [e.from_label, e.to_label, e.label]
        for s in verbatim:
            if s and _norm_ws(s) not in text_n:
                violations.append(f"figures[{i}] verbatim string {s!r} not found in raw.text")
        # 6a. data_points must not be a list of value-less shells.
        if fig.data_points and not any((p.value or "").strip() for p in fig.data_points):
            violations.append(
                f"figures[{i}].data_points has no point with a printed value "
                "(unreadable chart should leave data_points empty and use trend)"
            )
        # 6b. every edge endpoint must reference an existing node label.
        for e in fig.edges:
            for endpoint in (e.from_label, e.to_label):
                if endpoint and _norm_ws(endpoint) not in node_labels:
                    violations.append(
                        f"figures[{i}] edge endpoint {endpoint!r} matches no DiagramNode.label"
                    )
        # 6c. shell guard for diagram nodes.
        for n in fig.nodes:
            if not (n.label or "").strip() and not (n.kind or "").strip():
                violations.append(f"figures[{i}] has a DiagramNode with empty label AND kind")

    # 7. SECTIONS — heading provenance + body-paragraph bloat guard.
    sections = interp.sections
    for sec in sections:
        if sec.heading and _norm_ws(sec.heading) not in headings_n:
            violations.append(f"sections heading {sec.heading!r} not present in raw.headings")
    if len(sections) > len(raw.headings) + 2:   # tolerance for minor over-segmentation
        violations.append(
            f"len(sections)={len(sections)} greatly exceeds len(raw.headings)={len(raw.headings)} "
            "(body paragraphs likely misread as sections)"
        )

    # 8. TYPED ENTITIES — every name is a verbatim substring of raw.text.
    for ent in interp.typed_entities:
        if ent.name and _norm_ws(ent.name) not in text_n:
            violations.append(f"typed_entities name {ent.name!r} not found in raw.text")

    # 9. RELATIONS — subject and object must be substrings of raw.text
    #    (predicate and evidence are paraphrase — NOT checked).
    for rel in interp.relations:
        for part_name, part in (("subject", rel.subject), ("object", rel.object)):
            if part and _norm_ws(part) not in text_n:
                violations.append(f"relations {part_name} {part!r} not found in raw.text")

    return violations


__all__ = [
    "Table",
    "KeyValue",
    "Metric",
    "DataPoint",
    "DiagramNode",
    "DiagramEdge",
    "Figure",
    "Section",
    "Entity",
    "Relation",
    "RawExtraction",
    "Interpretation",
    "OCRPage",
    "sanitize_table_html",
    "router_invariants",
]
