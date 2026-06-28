Contextual OCR strategy
=======================

When doc2mark OCRs a PDF page in isolation, the model sees only that one image.
It has no way to know how a term was spelled two pages earlier, what language the
surrounding document is written in, or whether a screenshot of round numbers is a
real financial table or an illustrative product mock-up. The **contextual OCR
strategy** fixes this: when transcribing the content on page *k*, doc2mark can
attach a small PDF of the neighboring pages ``{k-1, k, k+1}`` as *context* -- not
as something to transcribe -- so the model can anchor terminology, proper names,
and language continuity against the document's own neighbors.

This produces more consistent transcriptions (the same term spelled the same way
across pages), keeps the output in the document's own language, and gives the
self-routing AUTO prompt enough signal to judge a host document's purpose before
it applies any non-verbatim policy.

The idea
--------

The transcription target never changes: the page **image** is, and remains, the
sole thing the model transcribes. The context PDF rides alongside it as a
separate, clearly-labeled attachment. Both LLM providers prepend a strict
instruction (``_CONTEXT_PDF_INSTRUCTION`` in ``doc2mark/ocr/base.py``) telling
the model:

   The IMAGE above is the PRIMARY and ONLY target to transcribe. The attached PDF
   contains the neighboring pages (previous/current/next) of the same document and
   is provided STRICTLY as CONTEXT for terminology, names, and language
   continuity. Do NOT transcribe, summarize, or quote the PDF. [...] Transcribe
   ONLY what is visibly present in the image, and respond in the document's own
   language.

A second clause (``_ROUTER_CONFIDENCE_CLAUSE``) is appended so the AUTO router
uses the neighbors *only* to judge the host document's purpose, and may apply its
``describe``/``screenshot`` policies **only** when its ``self_confidence`` is at
least ``0.7`` and legibility is ``"high"`` -- otherwise it falls back to verbatim
transcription. Context augments the model's judgement; it never licenses dropping
real text.

The ``context_pages`` tiers
---------------------------

The feature is controlled by a single field on
:class:`~doc2mark.ocr.OCRConfig`::

   context_pages: int = 0

This integer is a **scope tier**, not a window size -- the window is always fixed
at ``{k-1, k, k+1}``. The three tiers are:

``context_pages = 0``
   Off (the default). Zero behavior change -- the off path is byte-identical to a
   build without the feature.

``context_pages = 1``
   Attach context to **whole-page renders** only (one PDF upload per rendered
   image page). This is the image-strategy route, where a page is mostly pictures
   and the whole page is rasterized and OCR'd.

``context_pages = 2``
   Renders **and** non-decorative embedded images. Opt-in; adds an upload per
   embedded figure that survives the decorative filter. More uploads, more cost.

The tier is resolved once, from the OCR instance's config, in the PDF loader::

   self._context_tier = int(getattr(cfg, "context_pages", 0) or 0)

and gates context building per image in ``_collect_all_images``: whole-page
renders pass ``self._context_tier >= 1``; embedded images pass
``self._context_tier >= 2``.

How the window PDF is built
---------------------------

``_build_window_pdf(k)`` (in
``doc2mark/pipelines/pymupdf_advanced_pipeline.py``) constructs the context PDF
for page index ``k``:

- **Fixed, clamped window.** It copies pages ``a = max(0, k-1)`` through
  ``b = min(n-1, k+1)`` inclusive into a fresh PDF with PyMuPDF's
  ``insert_pdf``. At the document edges the window simply clamps: page ``0``
  yields ``{0, 1}``, the last page yields ``{n-2, n-1}``, and a single-page
  document yields ``{0}``. It is therefore never more than three pages.

- **Compressed.** The PDF is serialized with ``tobytes(deflate=True,
  garbage=3)`` to compress streams and drop orphaned objects.

- **Per-page de-duplicated.** The result is cached in a small LRU
  (``_WINDOW_CACHE_MAXLEN = 4``) keyed by page index, so the window for a given
  page is built at most once even though several embedded images on that page
  share it, and overlapping windows on adjacent pages do not rebuild work.

- **Size-guarded.** If the compressed PDF exceeds
  ``_CONTEXT_PDF_MAX_BYTES`` (18 MB -- chosen to stay under Gemini's ~20 MB
  inline request cap), the function logs a warning and returns ``None``. Any
  failure to build the PDF also returns ``None``. Callers treat ``None`` as "no
  context" and OCR the image normally, so the feature degrades gracefully and
  never blocks a page.

The output is **raw base64** (no ``data:`` URI prefix), which makes it
provider-independent -- each provider wraps it in its own attachment format.

How each provider attaches the context PDF
------------------------------------------

The per-image context base64 travels positionally alongside the image bytes as a
``context_pdfs`` list, and the loader only sets that keyword when at least one
image in the batch actually carries context (keeping the off-default path
unchanged). Each provider then attaches it as a **context-only** part next to the
image in the same ``HumanMessage``.

**OpenAI** (``doc2mark/ocr/openai.py``) appends the instruction text and a nested
``file`` block:

.. code-block:: python

   content.append({
       "type": "file",
       "file": {
           "filename": "context.pdf",
           "file_data": f"data:application/pdf;base64,{context_pdf}",
       },
   })

This is attached **only** when the PDF is present *and* the model can ingest a
PDF part. PDF capability is gated by ``_model_supports_pdf`` against the prefixes
``gpt-4o``, ``gpt-4.1``, ``gpt-5``, and ``o1`` -- so the default model,
``gpt-5.4-mini``, qualifies, while a non-PDF model silently skips the attachment
instead of 400-ing.

**Gemini / Vertex AI** (``doc2mark/ocr/vertex_ai.py``) appends the same
instruction text and a ``media`` part carrying the raw base64:

.. code-block:: python

   content.append({
       "type": "media",
       "mime_type": "application/pdf",
       "data": context_pdf,
   })

In both providers the image stays the first and only transcription target; the
PDF is an extra part the model is told to read for context only.

It augments -- it never replaces the verbatim layer
----------------------------------------------------

Context is purely additive. The deterministic, rule-based text and table layer
that doc2mark extracts from a text-authoritative PDF is always preserved
**verbatim** for the BM42 RAG flow -- the context PDF only rides on the OCR calls
that handle page renders (tier 1) and embedded figures (tier 2). Turning the
feature on does not alter, paraphrase, or re-derive any text that the rule-based
layer already captured; it only sharpens what the vision model produces for the
images it was already going to OCR.

Enabling it
-----------

Because context is built from neighboring PDF pages, it applies to **PDF sources
only**, and you enable it by passing an :class:`~doc2mark.ocr.OCRConfig` with
``context_pages`` set through the loader:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader
   from doc2mark.ocr.base import OCRConfig

   loader = UnifiedDocumentLoader(
       ocr_provider="openai",                 # gpt-5.4-mini is PDF-capable
       ocr_config=OCRConfig(context_pages=1),  # context on whole-page renders
   )

   doc = loader.load(
       "scanned-report.pdf",
       extract_images=True,
       ocr_images=True,
   )

Set ``context_pages=2`` to also attach context to non-decorative embedded
figures. Use Gemini/Vertex AI or a PDF-capable OpenAI model so the attachment is
actually consumed.

Cost and payload, honestly
--------------------------

Context is not free. Every OCR call that carries it uploads an extra PDF -- up to
three pages -- in addition to the page image:

- At **tier 1**, that is one context PDF per rendered image page.
- At **tier 2**, it is additionally one per surviving embedded figure, so a page
  with several figures multiplies the uploads (they share the same cached window
  PDF, but each call still ships it).

The window PDF is capped at 18 MB and compressed, but it still adds tokens and
bytes to every call, which means more latency and higher per-call cost. Enable it
when cross-page consistency, naming, and language continuity matter; leave it at
the default ``0`` when they do not.
