OCR
===

doc2mark includes an AI-powered OCR layer that returns **structured output** by
default. OCR providers are optional -- the package processes normal text
documents without any OCR credentials; OCR is only invoked when an image needs
to be read.

This page is a task-oriented guide to *using* OCR. The full result schema (every
field of every model) lives on :doc:`/api/schema`, and the exhaustive facade /
provider / config reference lives on :doc:`/api/ocr`.


The OCR facade
--------------

The :class:`~doc2mark.ocr.OCR` class is the one entry point you are expected to
touch. Construct it with a provider name, then call
:meth:`~doc2mark.ocr.OCR.read` (a batch) or :meth:`~doc2mark.ocr.OCR.read_one`
(a single image):

.. code-block:: python

   from doc2mark import OCR

   ocr = OCR("openai")                         # creds from OPENAI_API_KEY env var
   results = ocr.read([image_bytes])           # List[bytes] -> List[OCRResult]
   r = results[0]

   r.document.raw.text                         # verbatim transcription
   r.document.raw.tables                       # list of Table objects (html / headers+rows)
   r.document.raw.fields                       # list of KeyValue label/value pairs
   r.document.interpretation.summary           # model's summary (None for detail="raw")
   r.document.interpretation.document_type     # e.g. "receipt", "form", "chart"

   r.text                                      # back-compat rendered markdown

For a single image:

.. code-block:: python

   r = ocr.read_one(image_bytes)

Constructor signature::

   OCR(provider="openai", *, api_key=None, **config_kwargs)

``provider`` is one of ``"openai"``, ``"vertex_ai"``, ``"gemini"``, or
``"tesseract"`` (or an :class:`~doc2mark.ocr.OCRProvider` member). All keyword
arguments are forwarded to :class:`~doc2mark.ocr.OCRConfig`; a string ``task`` is
coerced to the matching :class:`~doc2mark.ocr.Task` member and ``detail`` is
validated to ``"raw"`` / ``"full"`` eagerly, so a bad value raises ``ValueError``
immediately.


What a result looks like
------------------------

Every call returns one :class:`~doc2mark.ocr.OCRResult` per input image, in input
order. Its ``text`` is always populated (rendered markdown, for back-compat) and
``document`` carries the structured :class:`~doc2mark.ocr.schema.OCRPage` for the
LLM providers (and for Tesseract, with an empty ``interpretation``), or ``None``
for the legacy free-form path.

``OCRPage`` enforces a hard boundary between **raw extraction** (verbatim, no
inference -- the trustworthy record of the page) and **interpretation** (the
model's analysis, which may be ``None``):

.. code-block:: python

   r = ocr.read_one(receipt_png, task="receipt")
   page = r.document                           # an OCRPage

   # raw -- always present, always verbatim
   page.raw.text                               # all visible text, original language
   page.raw.tables                             # List[Table]
   page.raw.fields                             # List[KeyValue]
   page.raw.headings                           # List[str], verbatim heading lines
   page.raw.dates                              # List[str], verbatim dates
   page.raw.metrics                            # List[Metric], typed numeric facts
   page.raw.detected_language                  # language actually seen
   page.raw.has_handwriting                    # bool

   # interpretation -- guard for None first
   if page.interpretation is not None:
       page.interpretation.document_type       # 16-way classification (below)
       page.interpretation.summary
       page.interpretation.self_confidence     # 0.0 .. 1.0
       page.interpretation.legibility          # "high" / "medium" / "low"

``interpretation`` is ``None`` when ``detail="raw"`` was requested, for the
Tesseract provider, or when a structured-output parse failed and the layer fell
back gracefully. **Always check ``page.interpretation is not None`` before
reading interpretive fields.**

``document_type`` is one of 16 values: ``document``, ``table``, ``form``,
``receipt``, ``handwriting``, ``code``, ``chart``, ``photo``, ``screenshot``,
``diagram``, ``infographic``, ``logo``, ``stamp``, ``mixed``, ``blank``,
``other``.

Beyond the basics shown above, ``raw`` also carries the additive verbatim indexes
``headings`` / ``dates`` / ``metrics``, and ``interpretation`` carries retrieval
and knowledge-graph anchors -- ``content_fidelity``, ``page_title``,
``primary_message``, ``keywords``, ``figures`` (List[:class:`~doc2mark.ocr.schema.Figure`]),
``sections`` (List[:class:`~doc2mark.ocr.schema.Section`]), ``typed_entities``
(List[:class:`~doc2mark.ocr.schema.Entity`]), ``relations``
(List[:class:`~doc2mark.ocr.schema.Relation`]), ``column_layout``, ``page_role``,
``primary_date``, ``action_items``, ``definitions``, and ``page_markdown``. See
:doc:`/api/schema` for the authoritative, always-current field list of every
model.


Tasks
-----

A :class:`~doc2mark.ocr.Task` names the *intent* of an image; the intent selects a
short, schema-aligned instruction that steers the model toward the right ``raw``
fields. Set a task at construction time or override it per call:

.. code-block:: python

   ocr = OCR("openai", task="receipt")          # all calls default to receipt
   results = ocr.read(images, task="table")     # per-call override

For mixed batches, assign one task per image with ``tasks`` (its length must equal
``len(images)``; it wins over the single ``task``):

.. code-block:: python

   results = ocr.read(images, tasks=["table", "receipt", "handwriting"])

Available task values:

- ``auto`` -- general-purpose self-routing default (classify-then-act)
- ``table`` -- tabular data (reproduced as HTML in ``Table.html``)
- ``document`` -- prose with headings, lists, reading order
- ``form`` -- form label/value extraction
- ``receipt`` -- receipts and invoices
- ``handwriting`` -- handwritten text
- ``code`` -- source code or terminal output

``language`` is intentionally **not** a task -- it is a separate config field
(and a per-call ``read(..., language=...)`` override), so there is no
"multilingual" task.


Raw and legacy modes
--------------------

Skip the interpretation pass to save output tokens:

.. code-block:: python

   results = ocr.read(images, detail="raw")
   # r.document.interpretation is None; r.document.raw is still fully populated

Disable structured output entirely for free-form markdown (legacy behaviour):

.. code-block:: python

   results = ocr.read(images, structured=False)
   # r.text contains free-form markdown; r.document is None

Both ``detail`` and ``structured`` can also be set once on the facade
(``OCR("openai", detail="raw")``) and overridden per call.


Tables
------

Each transcribed table is a :class:`~doc2mark.ocr.schema.Table`. Its preferred
representation is the ``html`` field: a clean ``<table>`` that can encode merged
cells via ``colspan`` / ``rowspan`` -- something the flat ``headers`` / ``rows``
grid cannot. The flat grid and a ``markdown`` fallback remain populated for simple
machine-readable access.

.. code-block:: python

   for table in r.document.raw.tables:
       print(table.html)                        # merged-cell-aware HTML (preferred)
       print(table.headers, table.rows)         # best-effort flat view
       if table.illustrative:                   # demo/mockup values, not real data
           print("sample rows omitted:", table.row_count)

See :doc:`/tables` for how tables flow through the loader and into the final
document.


Sanitised output
----------------

OCR text comes from a model reading an image, and an image can show anything,
including markup. ``OCRResult.text`` (what the loader writes into the document) is
therefore rendered safe, while the structured fields keep what the model returned.

``Table.html`` is cleaned when the result is built:

- only table tags (``table``/``thead``/``tbody``/``tfoot``/``tr``/``th``/``td``/
  ``caption``/``col``/``colgroup``), the inert ``<br>`` and the ``colspan`` /
  ``rowspan`` / ``scope`` attributes survive; scripts, styles and embedded objects are
  removed with their content, every other tag is unwrapped keeping its text, and HTML
  comments and processing instructions are dropped;
- line structure inside a cell (``<br>``, ``<p>``, ``<li>``, ``<div>``, a newline)
  becomes ``<br>``, so ``Net<br>income`` never turns into ``Netincome``;
- text next to a table is kept: before it, it becomes the table's ``<caption>`` (a
  title or a unit line such as ``Unit: NT$ thousand``); after it, it follows the
  table. A Markdown pipe table put in the field is converted to HTML;
- the grid is made rectangular per table (a nested table or a second table keeps its
  own grid). Spans are bounded: ``colspan`` never exceeds the widest row's cell count
  and ``rowspan`` never runs past its row group (``rowspan="0"`` is written out as the
  rows to the end of the group), so a model cannot make one table cost seconds or
  megabytes. An empty cell emitted for a position a rowspan already covers is dropped,
  and a short row is padded where its cells line up with their columns (``Cost | 80``
  under ``Item | Unit | 2024`` keeps ``80`` under ``2024``).

Every other string is escaped when the page is rendered
(:meth:`~doc2mark.ocr.schema.OCRPage.to_markdown`), following the escaping policy used
for all document text: a ``<`` becomes ``&lt;`` only before a letter, ``/``, ``!`` or
``?`` (so ``x < 5`` stays as it is), ``&`` only where it starts an entity, and control
characters are removed.

- Transcriptions (``raw.text``, captions, figure and section labels, and Tesseract
  output) are plain text: a line that starts with a Markdown block marker (``#``,
  ``>``, ``-``, ``1.``, a code fence, a rule) gets a backslash, so it stays text.
- Model-written Markdown (``page_markdown``, ``Table.markdown`` and free-form
  answers) keeps its Markdown; any ``<table>`` in it goes through the table cleaner
  above, and other raw HTML except ``<br>`` is neutralised.
- The flat ``headers`` / ``rows`` table escapes ``|`` and turns line breaks into
  spaces, so every value stays in its cell.


Refusals and "no readable text" answers
---------------------------------------

A vision model sometimes answers with "I'm sorry, but I can't assist with that
request." or "圖片中沒有可辨識的文字。" instead of a transcription. doc2mark never
indexes such an answer as page content:

1. Provider refusal signals count as no content: OpenAI's ``message.refusal`` (also
   when a structured answer ignores the schema), and Gemini answers stopped for
   ``SAFETY``, ``RECITATION``, ``BLOCKLIST``, ``PROHIBITED_CONTENT`` or ``SPII``.
2. A short answer that starts with a refusal or "no readable text" phrase (English,
   Chinese, Japanese, Korean, German, Spanish, French) counts as no content too. The
   check is conservative: real text that merely mentions an apology ("Sorry we missed
   you!", "This page intentionally left blank.") is kept.
3. A structured answer with no content goes to the free-form recovery, as an empty
   one always did. If the recovered answer is a refusal as well, the result is empty
   text with ``metadata["ocr_refusal"] = True``.

For the cases the patterns cannot decide, pass a judge:

.. code-block:: python

   def judge(ocr_text: str) -> float | None:
       """Probability (0..1) that ocr_text is ONLY a refusal, apology, error or
       "no readable text" statement; None when it cannot tell."""

   ocr = OCR("openai", non_content_judge=judge)
   loader = UnifiedDocumentLoader(ocr_provider="openai", ocr_config=OCRConfig(non_content_judge=judge))

The judge sees answers of at most 600 characters, and only when the patterns did not
fire. A probability of 0.5 or more counts as no content; ``None``, or an exception
(logged), keeps the answer. Without a judge the patterns alone decide, and an
undecided answer is kept.

The loader reports what happened per document in
``ProcessedDocument.metadata.extra["ocr_issues"]`` (present only when something did):
``refused`` (answers emitted empty as refusals), ``failed`` (images that could not be
read), ``withheld`` (images whose illustrative rows stayed withheld; their Markdown
says ``[N illustrative rows not transcribed]``) and up to five ``errors``. An OCR engine
that cannot run at all, such as Tesseract without the requested language data, raises
``OCREngineError`` from ``load()`` instead of producing placeholder text, and the CLI
exits non-zero.


Providers
---------

OpenAI
~~~~~~

GPT vision via LangChain. Structured output is produced with
``with_structured_output(method="json_schema")``. Requires ``OPENAI_API_KEY``
(or ``api_key=``) and the ``doc2mark[ocr]`` extra. The default model is
``gpt-5.4-mini``.

.. code-block:: bash

   pip install "doc2mark[ocr]"
   export OPENAI_API_KEY=sk-...

.. code-block:: python

   ocr = OCR("openai")
   ocr = OCR("openai", model="gpt-5.4-mini")                       # explicit default
   ocr = OCR("openai", base_url="http://localhost:11434/v1")       # Ollama / compatible

``base_url`` (or the ``OPENAI_BASE_URL`` env var) targets OpenAI-compatible
endpoints. Model knobs follow the precedence **explicit constructor argument ->
OCRConfig field -> built-in default**.

Google Gemini (Vertex AI)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Gemini via ``langchain-google-genai`` on the Vertex AI backend. Both
``"vertex_ai"`` and ``"gemini"`` resolve to the same implementation.
Authenticates with `Application Default Credentials
<https://cloud.google.com/docs/authentication/application-default-credentials>`_
rather than an API key. The default model is ``gemini-3.1-flash-lite-preview``
and the default location is ``"global"``.

.. code-block:: bash

   pip install "doc2mark[vertex_ai]"
   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
   export GOOGLE_CLOUD_PROJECT=my-gcp-project

.. code-block:: python

   ocr = OCR("gemini")
   ocr = OCR("vertex_ai", project="my-gcp-project", model="gemini-3.1-flash-lite-preview")

Tesseract (offline)
~~~~~~~~~~~~~~~~~~~

Local OCR via ``pytesseract`` + Pillow, no API key. It is **raw-only**:
``interpretation`` is always ``None`` (a non-LLM engine cannot infer document
type, summaries, or confidence). ``language`` is mapped to a Tesseract language
code (e.g. ``"chinese"`` -> ``chi_sim+chi_tra``), defaulting to English.

.. code-block:: bash

   pip install "doc2mark[ocr]"

.. code-block:: python

   ocr = OCR("tesseract", language="english")
   r = ocr.read_one(scanned_png)
   print(r.text)                               # transcription
   print(r.document.interpretation)            # always None for Tesseract


Concurrency
-----------

Control how many images the LLM providers OCR in parallel (inside LangChain's
``batch_as_completed``):

.. code-block:: python

   ocr = OCR("openai", max_concurrency=32)

Or set the ``OCR_MAX_CONCURRENCY`` environment variable. Precedence is **explicit
config value -> env var -> ``None``**, where ``None`` means "use the LangChain
default" (a CPU-tied thread pool, typically ~12). Raise it to keep large scanned
documents within an SLA (e.g. ``32`` for a several-thousand-page job).


Using OCR with the document loader
-----------------------------------

:class:`~doc2mark.UnifiedDocumentLoader` uses the OCR layer internally when
``ocr_images=True``:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="openai")
   result = loader.load("scan.pdf", extract_images=True, ocr_images=True)

Disable OCR when it is not needed:

.. code-block:: python

   loader = UnifiedDocumentLoader(ocr_provider=None)
   result = loader.load("document.pdf")

.. code-block:: bash

   doc2mark document.pdf --ocr none


Deprecation notice
------------------

The old :class:`~doc2mark.ocr.OCRConfig` fields ``enhance_image``,
``detect_tables``, ``detect_layout``, ``timeout``, ``max_retries``, and ``extra``
are inert for the LLM providers (OpenAI / Vertex / Gemini). Setting any of them to
a non-default value emits a single ``DeprecationWarning`` at construction, and
they will be removed in a future release. (``enhance_image`` and ``detect_layout``
remain live for the Tesseract provider.) Use the live knobs -- ``model``,
``task``, ``language``, ``max_concurrency``, and the structured-output controls
(``structured`` / ``detail`` / ``response_model`` / ``on_parse_error``) --
instead.


See also
--------

- :doc:`/tables` -- how transcribed tables (``Table.html``) flow into output.
- :doc:`/contextual_ocr` -- attaching neighbor-page PDF context (``context_pages``).
- :doc:`/ocr_policy` -- the ``auto`` router's classify-then-act extraction policy.
- :doc:`/api/ocr` -- full facade, provider, and configuration reference.
- :doc:`/api/schema` -- the complete structured result schema.
