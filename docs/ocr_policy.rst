Content-aware OCR policy
========================

doc2mark decides *how* to OCR a document from the document's **content**, not
from its file extension. The same content-based routing applies to PDFs and to
Office files, and it runs automatically: there are no routing flags to set. You
turn OCR on (``ocr_images=True``) and doc2mark chooses the cheapest correct path
for every page and every embedded image.

The guiding principle
---------------------

Text, data, and tables take the **deterministic path** -- they are read directly
from the document's structure (selectable text, ruled tables) and emitted
*verbatim*. This path is exact, free (no model calls), and lossless: it is the
authoritative source for the BM42 sparse-retrieval index, so every printed token
is preserved character-for-character.

Only a **true image-page** -- a slide or scan whose content is baked into
pixels with no usable text layer -- is sent to an LLM vision model. The model is
asked to transcribe that page verbatim and *also* synthesize a clean Markdown
re-layout, but it is never allowed to drop real printed values.

The policy is layered. Each layer narrows the decision:

#. **Document strategy** -- one ``"image"`` vs ``"text"`` decision per document.
#. **Office image route** -- image-dominant ``.docx``/``.pptx`` borrow the PDF
   image strategy; text/table Office docs stay native.
#. **Per-image job-router** -- when a single image is OCR'd, the model
   classifies it and applies a per-type transcription policy.
#. **page_markdown synthesis + coverage guard** -- for image pages a structured
   Markdown rendering becomes the display body *only* when it provably covers
   the verbatim text.

The shared thresholds
~~~~~~~~~~~~~~~~~~~~~~

Both the document strategy and the Office route call one function,
``doc2mark.core.strategy.decide_doc_strategy``, the single source of truth so
the PDF and Office paths never diverge:

.. code-block:: python

   from doc2mark.core.strategy import decide_doc_strategy

   # decide_doc_strategy(mean_image_coverage, mean_text_chars_per_page)
   decide_doc_strategy(0.92, 35)    # -> "image"   (mostly pictures, no text layer)
   decide_doc_strategy(0.70, 900)   # -> "text"    (large figures, but real text)
   decide_doc_strategy(0.10, 1200)  # -> "text"    (ordinary text document)

The two module constants are the only knobs, and they are deliberately not
user-facing:

.. list-table::
   :header-rows: 1
   :widths: 32 14 54

   * - Constant
     - Value
     - Meaning
   * - ``IMAGE_PAGE_COVERAGE``
     - ``0.55``
     - Minimum mean fraction of page area covered by raster images for the
       ``"image"`` strategy.
   * - ``IMAGE_PAGE_TEXT_LIMIT``
     - ``200``
     - The mean selectable-text characters per page must be **below** this for
       the ``"image"`` strategy.

The rule is a logical AND::

   "image"  iff  mean_image_coverage >= 0.55  AND  mean_text_chars_per_page < 200
   "text"   otherwise

Text density is the decisive signal. Coverage alone would misclassify a normal
text document that merely carries a few large figures; requiring low text
density as well keeps such documents on the deterministic ``"text"`` path.

Layer 1 -- the document strategy
--------------------------------

For a PDF, ``PDFLoader._document_image_strategy`` computes the two signals
deterministically and caches the result once per document:

- ``mean_image_coverage`` -- the mean per-page fraction of page area covered by
  raster images (capped at ``1.0``).
- ``mean_text_chars_per_page`` -- the mean length of the page's stripped
  selectable text.

It feeds them to ``decide_doc_strategy`` and logs the decision, e.g.::

   📑 Document OCR strategy: image (mean coverage 0.94, mean text 12 chars/page)

A single uniform strategy is chosen for the whole document so that OCR-only and
rule-based pages are never mixed.

The ``"image"`` strategy
~~~~~~~~~~~~~~~~~~~~~~~~~

Every page is rasterized to a single PNG (at 150 DPI) and OCR'd as one
whole-page image. The whole-page transcription **is** the page's content: a
sparse text layer on such a page is chrome (a logo, footer, or page number) that
the whole-page OCR already captures, so the deterministic text layer is *not*
also emitted -- emitting it would just duplicate tokens and add junk
header/footer mini-tables.

These whole-page renders also request ``page_markdown`` synthesis (Layer 4).

The ``"text"`` strategy
~~~~~~~~~~~~~~~~~~~~~~~~

The deterministic rule-based layer is authoritative:

- **Tables** are detected with PyMuPDF's table finder and rendered with
  doc2mark's table renderer (including merged-cell handling), so cell text stays
  exact.
- **Text** is extracted block-by-block and classified (title / section /
  list / caption / footnote / header / footer) from font-size, weight, and
  layout heuristics -- preserved verbatim for the BM42 RAG flow.
- **Embedded figures** are OCR'd individually. Tiny decorative images (logos,
  icons, bullets -- smaller than 10% of the page in *both* width and height) are
  skipped before paying for extraction or an OCR call.

Layer 2 -- the Office image route
---------------------------------

Office documents reach the *same* content-based decision, without a separate
heuristic. ``OfficeProcessor._maybe_route_image_dominant`` runs before native
extraction and is gated tightly:

- Only ``.docx`` and ``.pptx`` are eligible. **``.xlsx`` never routes** -- a
  spreadsheet is a data grid, always read natively.
- OCR must be requested (both ``ocr_images=True`` and ``extract_images=True``)
  and an OCR provider must be configured.

``_is_image_dominant`` then computes the two signals straight from the OOXML
structure -- no rendering required -- and calls the same
``decide_doc_strategy``:

- **PPTX** (``_pptx_image_signals``): mean picture-shape coverage and mean text
  characters **per slide**.
- **DOCX** (``_docx_image_signals``): total inline-picture coverage against one
  page, and total paragraph text length. Totals suffice because a real
  multi-page text document easily clears the 200-character limit, and
  undercounting floating images biases toward ``"text"`` -- the safe direction.

When the decision is ``"image"``, ``_process_as_image_dominant`` converts the
file to PDF via LibreOffice and runs it through the PDF **image strategy**
(whole-page render OCR + ``page_markdown`` synthesis), then restores the original
Office identity in the metadata and records ``metadata.extra['routed_via'] =
'pdf'``. Text/table Office docs stay on the native pipeline. The route never
raises: any failure (including no LibreOffice on the host) falls back cleanly to
native extraction.

Layer 3 -- the per-image job-router (``task="auto"``)
-----------------------------------------------------

When an individual image is OCR'd -- a whole-page render, or an embedded figure
on a ``"text"`` page -- the default task is ``auto``. The ``auto`` prompt is a
self-routing job-router: the model first **classifies** the image into exactly
one ``document_type``, then applies that type's transcription policy in the same
response, recording the type in
:attr:`Interpretation.document_type <doc2mark.ocr.schema.Interpretation>`.

The master rule overrides every policy below it: **transcribe every legible
printed character verbatim, in the original language.** Exactly one type --
``screenshot`` -- may omit printed values, and only when *all three* gates hold:

#. the image is a product / app / dashboard UI with toolbar, nav, tabs, or
   buttons; **and**
#. its data is clearly *illustrative* (round or sequential names, evenly spaced
   dates, repeated amounts, "Sample"/"Demo"); **and**
#. the surrounding context indicates a product, marketing, or feature
   introduction.

If any gate is missing -- or whenever the model is unsure -- it transcribes
verbatim. A dropped real table is unrecoverable, so the tie always breaks toward
verbatim.

The four policies
~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 20 62

   * - Policy
     - ``document_type``
     - Behavior
   * - **VERBATIM** (default)
     - ``document``, ``table``, ``form``, ``receipt``, ``handwriting``,
       ``code``, ``photo``, ``logo``, ``stamp``, ``mixed``, ``other``
     - Transcribe every character into ``raw.text``; tables go to
       ``raw.tables[].html`` with exact ``colspan``/``rowspan``; label/value
       pairs to ``raw.fields``. ``content_fidelity="verbatim"``.
   * - **SCREENSHOT** (triple-gated only)
     - ``screenshot``
     - Write only stable text -- screen/module name, section / nav / field /
       column **labels**, buttons, capability message. Leave each
       :class:`Table <doc2mark.ocr.schema.Table>` header-only with
       ``illustrative=true`` and a ``row_count`` of the withheld sample rows;
       put what the product *does* in ``interpretation``.
       ``content_fidelity="described"``.
   * - **DESCRIBE**
     - ``chart``, ``diagram``, ``infographic``
     - Keep **all** printed text verbatim (titles, axis / legend / node / edge
       labels, printed numbers); never invent or pixel-estimate values; put the
       trend / structure / message in ``interpretation``.
       ``content_fidelity="described"``.
   * - **SKIP**
     - ``blank``
     - Leave ``raw`` empty. ``content_fidelity="skipped"``.

A ruled grid of irregular, varied-precision, or internally consistent numbers
(subtotals that sum) is a *real* table and is transcribed as ``table``
regardless of surrounding app chrome; monospace code or a terminal is ``code``,
never ``screenshot``.

Neighbor-page context tightens the gate
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When neighbor-page PDF context is attached (``context_pages`` > 0, Gemini /
Vertex only), the neighbors are read *only* to judge the host document's purpose
-- never transcribed. The non-verbatim policies (``describe`` and
``screenshot``) may then be applied **only** when the model's
``self_confidence >= 0.7`` *and* ``legibility == "high"``; otherwise, and
whenever context is absent or conflicting, it falls back to verbatim.

The ``router_invariants`` firewall
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``doc2mark.ocr.schema.router_invariants(page)`` is a mechanical check (returns a
list of violation strings; empty means OK) that enforces the policy after the
fact -- a BM42-safety net intended as a CI / eval assertion over recorded
structured outputs. It guarantees that **real printed values are never withheld
except on a high-confidence screenshot.** Among the invariants it checks:

- Illustrative / withheld content (``illustrative=True`` on a table, field,
  metric, or figure) may appear **only** when ``document_type == "screenshot"``.
- A withholding screenshot must have ``self_confidence >= 0.7`` and
  ``legibility == "high"`` -- otherwise it should have fallen back to verbatim.
- ``content_fidelity`` of ``"described"`` / ``"caption"`` must carry meaning in
  ``interpretation.summary``; ``"skipped"`` implies an empty ``raw`` layer.
- ``interpretation.primary_date`` must be one of the verbatim strings in
  ``raw.dates`` (selected, never invented).
- Every verbatim string surfaced in a ``figure``, ``section``, typed entity, or
  relation must be a substring of ``raw.text``.

Layer 4 -- ``page_markdown`` synthesis and the coverage guard
-------------------------------------------------------------

For whole-page image renders only, doc2mark appends a synthesis instruction
asking the model to *also* fill
:attr:`Interpretation.page_markdown <doc2mark.ocr.schema.Interpretation>`: a
clean, well-structured Markdown rendering of the page (headings, lists, arrow
chains for flow diagrams) that **re-lays-out** the same text -- dropping,
paraphrasing, and translating nothing. Table regions become a short
``[see table]`` placeholder, since the authoritative HTML already lives in
``raw.tables``.

A flat OCR dump is hard to read, but a synthesized rendering risks summarizing
content away. The coverage guard in
:meth:`OCRPage.to_markdown <doc2mark.ocr.schema.OCRPage>` resolves the tension.
``page_markdown`` is used as the display body **only** when it verifiably covers
the verbatim text:

- A token-based coverage score is computed over ``raw.text`` (Latin / numeric
  words and CJK runs), against ``page_markdown`` plus the table HTML.
- If coverage ``>= 0.85`` (``_SYNTH_COVERAGE_MIN``), the synthesized Markdown
  becomes the body, the authoritative table HTML is appended, and any
  still-missing verbatim tokens are preserved in a hidden ``raw-verbatim-tail``
  HTML comment -- so BM42 keeps **every** token even if the rendering drops one.
- If coverage falls below the floor (likely paraphrase or truncation), doc2mark
  **falls back** to the standard verbatim rendering of ``raw.text`` + tables.

The result is never lossy: the structured rendering is used when it is provably
complete, and the raw verbatim dump is used otherwise.

Putting it together
-------------------

Because the routing is automatic, the only thing you do is enable OCR:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="openai")

   # A slide deck or scan -> "image" strategy: whole-page render OCR + page_markdown.
   deck = loader.load("pitch_deck.pdf", extract_images=True, ocr_images=True)

   # A text report -> "text" strategy: deterministic text/tables, verbatim,
   # with only its embedded figures sent to the model.
   report = loader.load("annual_report.pdf", extract_images=True, ocr_images=True)

   # Same content-based decision for Office; an image-dominant .pptx is routed
   # through the PDF image strategy, an ordinary .docx stays native.
   slides = loader.load("slides.pptx", extract_images=True, ocr_images=True)

See :doc:`/ocr` for the OCR facade, providers, tasks, and the structured-output
schema, and :doc:`/api/schema` for the full model reference.
