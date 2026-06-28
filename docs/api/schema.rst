Structured OCR Schema
=====================

doc2mark's OCR layer does not return a single free-form markdown blob. Instead,
every image becomes a structured :class:`~doc2mark.ocr.schema.OCRPage`, carried
on ``OCRResult.document``. The schema enforces a hard boundary between two
concerns:

- **raw** — what is *literally* on the page: a verbatim transcription, any
  tables, label/value fields, and additive verbatim indexes (headings, dates,
  metrics). No inference, no commentary.
- **interpretation** — the model's *reading* of the page: document type, a
  short summary, key findings, a knowledge graph, and confidence. This is the
  part that requires a language model to reason about the content.

This split is the most important idea in the schema. The ``raw`` half is
always present and is the trustworthy, auditable record of the page. It is the
token source for the BM42 sparse index, so everything in it is verbatim. The
``interpretation`` half is :data:`None` whenever the model was not asked to —
or could not — reason about the page, specifically:

- ``detail="raw"`` was requested (raw transcription only),
- the provider is non-LLM (e.g. Tesseract, which cannot infer), or
- the structured-output parse failed and the layer fell back gracefully.

Every model on this page is a Pydantic ``BaseModel`` subclass, and **every
field is defaulted**: the LLM providers emit them through LangChain's
``with_structured_output(method="json_schema")``, and OpenAI strict mode
requires all properties to be present, so each field must be satisfiable
without input. Optional fields serialize as ``anyOf: [T, null]``.

.. note::

   Import these from ``doc2mark.ocr.schema``. The convenience accessor
   :meth:`~doc2mark.ocr.schema.OCRPage.to_markdown` collapses the structured
   page back into a single markdown string, which is what powers the
   back-compatible ``OCRResult.text``.

The authoritative, always-current field list for each model is generated below
directly from the source (defaults, types, and per-field descriptions). The
prose around each block explains *why* the field exists and how the models fit
together; the field documentation itself is the rendered docstring.


The verbatim / interpretation boundary
--------------------------------------

The single rule that holds the schema together: **the interpretation layer
never invents a printed value, and never moves one out of** ``raw``. Anything
the model claims is *on the page* — a figure axis label, an entity name, a
relation's subject — is an additive copy of a string that already appears in
``raw.text``. This keeps the sparse index intact (BM42 reads ``raw.text``)
while making the same facts queryable as typed structure.

That invariant is mechanically enforced. :func:`~doc2mark.ocr.schema.router_invariants`
returns a list of violations for a page (empty means OK) and is intended as a
CI / eval assertion over recorded outputs. It checks, among other things, that
every verbatim figure string is a substring of ``raw.text``, that every
``Section.heading`` came from ``raw.headings``, that ``primary_date`` was
selected from ``raw.dates`` rather than fabricated, that entity names and
relation subjects/objects are substrings of ``raw.text``, and that withheld
("illustrative") values appear only on a high-confidence ``screenshot`` page.

When you read a page, treat ``raw`` as ground truth and ``interpretation`` as
an *additive overlay* that is safe to ignore. Always check
``page.interpretation is not None`` before reading interpretive fields — with
``detail="raw"`` or a Tesseract backend it will be ``None``.


``OCRPage`` — the top-level result
----------------------------------

One image's structured OCR result. It bundles the always-present ``raw``
extraction with the optional ``interpretation``, and exposes one method.

**Signature**

.. code-block:: python

   class OCRPage(BaseModel):
       raw: RawExtraction = Field(default_factory=RawExtraction)
       interpretation: Optional[Interpretation] = None

``to_markdown() -> str``
    Render a readable markdown view of the page, used as the back-compatible
    ``OCRResult.text``. It prefers structured tables/fields over the flat text
    dump, and renders the additive overlays (real metrics, then figures, then a
    section outline) degraded-safe. When the interpretation carries a
    ``page_markdown`` whole-page rendering that verifiably covers the verbatim
    text, that structured rendering replaces the flat ``raw.text`` dump (with a
    hidden verbatim tail preserving any uncovered tokens so the index stays
    complete).

.. autoclass:: doc2mark.ocr.schema.OCRPage
   :members:
   :show-inheritance:


``RawExtraction`` — verbatim page content
-----------------------------------------

A verbatim transcription of the page: no commentary, no inference. This is the
``raw`` field of :class:`~doc2mark.ocr.schema.OCRPage` and the BM42 token
source. Alongside the full ``text`` and structured ``tables`` / ``fields``, it
carries three **additive verbatim indexes** — ``headings``, ``dates``, and
``metrics`` — each entry of which is a copy of tokens already present in
``text``, surfaced so an indexer can boost or filter without re-parsing. The
``metrics`` list holds :class:`~doc2mark.ocr.schema.Metric` objects (typed
numeric assertions); ``headings`` and ``dates`` are plain verbatim strings.

.. autoclass:: doc2mark.ocr.schema.RawExtraction
   :members:
   :show-inheritance:


``Table`` — a transcribed table
-------------------------------

A table transcribed verbatim, used inside ``RawExtraction.tables``. The
preferred representation is ``html``: a clean ``<table>`` that can encode merged
cells via ``colspan`` / ``rowspan`` (which the flat ``headers`` / ``rows`` grid
and markdown cannot). The ``html`` value is **sanitized at the model boundary**
by a field validator (see :func:`~doc2mark.ocr.schema.sanitize_table_html`) to a
strict table-only allowlist, so the stored string is always safe to embed in
rendered output. ``illustrative`` flags a demo/mockup table whose values are
not real data, and ``row_count`` records how many sample rows a header-only
illustrative table intentionally left untranscribed.

.. autoclass:: doc2mark.ocr.schema.Table
   :members:
   :show-inheritance:


``KeyValue`` — a label/value pair
---------------------------------

A single label/value pair, used both inside ``RawExtraction.fields`` (forms,
receipts) and inside ``Interpretation.definitions`` (term/definition pairs).
``illustrative`` flags a demo/sample value from a screenshot or mockup region.

.. autoclass:: doc2mark.ocr.schema.KeyValue
   :members:
   :show-inheritance:


``Metric`` — a typed numeric assertion
--------------------------------------

A single number printed on the page, captured as a flat, four-field typed fact
(``label``, ``value``, ``unit``, ``illustrative``). A ``Metric`` is an additive
view of a number that is *also* present verbatim in ``raw.text`` — never a
relocation of it. ``value`` is copied exactly as printed (e.g. ``"$4.2B"``,
``"98.5%"``, ``"< 100 ms"``) and never normalized or computed. Metrics live in
``RawExtraction.metrics`` and make a number queryable as a typed fact while the
sparse index keeps reading ``raw.text``.

.. autoclass:: doc2mark.ocr.schema.Metric
   :members:
   :show-inheritance:


The nested interpretation models
--------------------------------

The interpretation layer carries a small family of nested models that structure
a page's *meaning* — figures, hierarchy, and a knowledge graph. They are
deliberately **shallow** (max nesting depth 4: ``OCRPage`` → ``interpretation``
→ ``figures`` → ``data_points``), with no recursion, no model-unions, and every
field defaulted, so they stay fillable under
``with_structured_output(json_schema)``. They are **additive and BM42-safe**:
every verbatim string inside them mirrors text already in ``raw.text``, an
invariant enforced by :func:`~doc2mark.ocr.schema.router_invariants`.

- **Metric** (above) — a typed number, in ``raw.metrics``.
- **Figure** — one chart / diagram / infographic panel, in
  ``interpretation.figures``. Its ``kind`` drives which branch fills:
  quantitative kinds (bar/line/pie/…) fill ``data_points``; structural kinds
  (flowchart/org_chart/network) fill ``nodes`` + ``edges``; everything else
  falls back to ``labels`` + ``meaning``. ``meaning`` (and ``trend`` for
  charts) is the always-attempt interpretive fallback when the typed data can't
  be read.

  - **DataPoint** — one ``(label, value, series)`` chart reading, flattened to
    tidy-long form (the deepest leaf, depth 4). All three fields are verbatim
    copies of printed text; values are never pixel-estimated.
  - **DiagramNode** — one labelled box / shape / actor in a flowchart, org
    chart, or network. There is no synthetic id — edges reference nodes by
    their verbatim ``label``.
  - **DiagramEdge** — a directed connection between two nodes, referenced by
    verbatim ``from_label`` / ``to_label`` (each must match a
    ``DiagramNode.label`` in the same figure).

- **Section** — one heading-delimited region in reading order. Hierarchy is a
  **flat list plus an integer** ``level`` (never recursive children). Its
  ``heading`` is a verbatim ``raw.headings`` entry; ``summary`` and
  ``key_points`` are paraphrase.
- **Entity** — a typed named entity (person / org / product / location /
  concept / other) with a ``salience`` ranking. Its ``name`` is verbatim in
  ``raw.text``; dates, money, and KPIs are *not* entities — they live in
  ``raw.dates`` / ``raw.metrics``.
- **Relation** — a knowledge triple (``subject`` / ``relation`` / ``object``)
  for a claim **explicitly stated** on the page, with an ``evidence`` quote that
  makes it falsifiable. ``subject`` and ``object`` are substrings of
  ``raw.text``; the predicate and evidence may paraphrase.

Finally, the interpretation can carry a ``page_markdown`` string: a clean,
structured Markdown re-layout of a whole-page image render. It is filled only
when explicitly instructed (slide / scan image-strategy pages) and, when filled,
must cover every word, number, and CJK character from ``raw.text`` verbatim — it
re-flows existing content into a readable document, it never adds or removes any.

.. autoclass:: doc2mark.ocr.schema.Figure
   :members:
   :show-inheritance:

.. autoclass:: doc2mark.ocr.schema.DataPoint
   :members:
   :show-inheritance:

.. autoclass:: doc2mark.ocr.schema.DiagramNode
   :members:
   :show-inheritance:

.. autoclass:: doc2mark.ocr.schema.DiagramEdge
   :members:
   :show-inheritance:

.. autoclass:: doc2mark.ocr.schema.Section
   :members:
   :show-inheritance:

.. autoclass:: doc2mark.ocr.schema.Entity
   :members:
   :show-inheritance:

.. autoclass:: doc2mark.ocr.schema.Relation
   :members:
   :show-inheritance:


``Interpretation`` — the model's reading
----------------------------------------

The model's analysis of the page, never mixed into ``raw``. This is the
``interpretation`` field of :class:`~doc2mark.ocr.schema.OCRPage`, and is
``None`` whenever the model was not asked to (or could not) reason.

It classifies the page via ``document_type`` (one of 16 values: ``document``,
``table``, ``form``, ``receipt``, ``handwriting``, ``code``, ``chart``,
``photo``, ``screenshot``, ``diagram``, ``infographic``, ``logo``, ``stamp``,
``mixed``, ``blank``, ``other``) and carries the retrieval / comprehension
anchors and overlays described above — ``page_title``, ``primary_message``,
``keywords``, the nested ``figures`` / ``sections`` / ``typed_entities`` /
``relations``, plus ``column_layout``, ``page_role``, ``primary_date``,
``action_items``, ``definitions``, ``content_fidelity``, ``self_confidence``,
``legibility``, and ``page_markdown``. See the rendered field list for the exact
defaults and meanings.

.. autoclass:: doc2mark.ocr.schema.Interpretation
   :members:
   :show-inheritance:


A complete filled ``OCRPage`` (receipt)
---------------------------------------

The example below shows a fully populated page for a grocery receipt, with both
the verbatim ``raw`` half and the model's ``interpretation`` half.

.. code-block:: python

   from doc2mark.ocr.schema import (
       OCRPage, RawExtraction, Interpretation, Table, KeyValue, Metric,
   )

   page = OCRPage(
       raw=RawExtraction(
           text=(
               "ACME STORE\n"
               "123 Market St\n"
               "Apples  2  $3.00\n"
               "Bread   1  $2.50\n"
               "Milk    1  $4.00\n"
               "Total      $9.50\n"
               "Thank you for shopping!"
           ),
           tables=[
               Table(
                   caption="Items",
                   headers=["Item", "Qty", "Price"],
                   rows=[
                       ["Apples", "2", "$3.00"],
                       ["Bread", "1", "$2.50"],
                       ["Milk", "1", "$4.00"],
                   ],
               ),
           ],
           fields=[
               KeyValue(label="Store", value="ACME STORE"),
               KeyValue(label="Total", value="$9.50"),
           ],
           headings=["ACME STORE"],
           metrics=[Metric(label="Total", value="$9.50")],
           detected_language="en",
           has_handwriting=False,
       ),
       interpretation=Interpretation(
           document_type="receipt",
           summary=(
               "Grocery receipt from ACME Store listing three items "
               "with a total of $9.50."
           ),
           key_findings=["3 line items", "Total: $9.50"],
           primary_message="Three grocery items were purchased for $9.50.",
           self_confidence=0.94,
           legibility="high",
       ),
   )

   # Always-present raw half:
   assert page.raw.fields[1].value == "$9.50"

   # Interpretation present here because an LLM provider ran with full detail:
   assert page.interpretation is not None
   assert page.interpretation.document_type == "receipt"

   # Single-string markdown view (back-compat OCRResult.text):
   print(page.to_markdown())
