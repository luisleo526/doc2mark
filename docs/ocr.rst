OCR
===

OCR is optional. doc2mark reads text, tables and structure from the file itself; an OCR provider
is used only for what has no usable text: scanned pages, text drawn as outlines, garbled text
layers, pictures that carry text, and image files. There are two ways to use it:

- **In the loader** (``ocr_images=True``): doc2mark decides page by page and picture by picture
  what to send (:doc:`ocr_policy`) and puts the text where it belongs in the document.
- **The** :class:`~doc2mark.OCR` **facade**: read images you already have and get structured
  results.

Providers
---------

.. list-table::
   :header-rows: 1
   :widths: 16 24 26 34

   * - Provider
     - Install
     - Credentials
     - Notes
   * - ``openai``
     - ``doc2mark[ocr]``
     - ``OPENAI_API_KEY`` (or ``api_key=``), read when the provider is created
     - Default model ``gpt-5.4-mini``. ``base_url`` (or ``OPENAI_BASE_URL``) points it at any
       OpenAI-compatible endpoint (vLLM, Ollama, a gateway); a key is still required, any value
       your endpoint accepts. Structured answers use JSON-schema output, which the endpoint
       must support.
   * - ``vertex_ai`` (also ``gemini``)
     - ``doc2mark[vertex_ai]``
     - Google Application Default Credentials; project from ``project=`` or
       ``GOOGLE_CLOUD_PROJECT``
     - Default model ``gemini-3.1-flash-lite-preview``, location ``global``.
   * - ``tesseract``
     - ``doc2mark[ocr]`` and the ``tesseract`` program with its language data
     - none
     - Local, offline. Returns the transcription only (no interpretation, no tables).

A provider is created without any network call; a missing extra or key shows up at the first OCR
request. Inside the loader that request's error is reported in ``metadata.extra["ocr_issues"]``
and the document still converts; a Tesseract that cannot run at all (no program, a language
that is not installed) fails the conversion instead (see :ref:`ocr-failures`).

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   openai_loader = UnifiedDocumentLoader(ocr_provider="openai", model="gpt-5.4-mini")
   vertex_loader = UnifiedDocumentLoader(ocr_provider="vertex_ai", project="my-gcp-project",
                                         location="global", model="gemini-3.1-flash-lite-preview")
   local_loader = UnifiedDocumentLoader(ocr_provider="tesseract")
   print(openai_loader.get_ocr_configuration()["model"], vertex_loader.ocr.model)

Loader settings
~~~~~~~~~~~~~~~

These :class:`~doc2mark.UnifiedDocumentLoader` arguments reach the provider (``"vertex_ai"`` and
its alias ``"gemini"`` take the same ones):

.. list-table::
   :header-rows: 1
   :widths: 30 20 20 30

   * - Argument (default)
     - OpenAI
     - Vertex AI / Gemini
     - Notes
   * - ``model``, ``temperature`` (0), ``max_tokens`` (8192)
     - yes
     - yes
     - They take precedence over the same fields of ``ocr_config``. With Vertex AI the default
       ``model`` (``gpt-5.4-mini``) means ``ocr_config.model``, else
       ``gemini-3.1-flash-lite-preview``. LangChain does not send a temperature to ``gpt-5``
       models.
   * - ``api_key``, ``base_url``
     - yes
     - no
     -
   * - ``project``, ``location`` (``"global"``)
     - no
     - yes
     -
   * - ``timeout`` (30 s), ``max_retries`` (3)
     - yes
     - yes
     - Per request.
   * - ``max_workers`` (``None``)
     - yes
     - yes
     - At most this many requests at once, when ``ocr_config.max_concurrency`` is not set (see
       *Concurrency* below).
   * - ``top_p`` (1.0), ``frequency_penalty`` (0.0), ``presence_penalty`` (0.0)
     - yes
     - yes
     - Sent only when set to another value than the default shown, which is the API's own.
       Some models reject them (OpenAI's reasoning models may); such a request fails and is
       reported like any failed request.
   * - ``prompt_template`` (``"default"``), ``default_prompt`` (``None``)
     - yes
     - yes
     - The prompt of free-form requests: ``structured=False`` and the free-form retry of an
       empty structured answer. ``prompt_template`` is ``"default"``, ``"table_focused"``,
       ``"document_focused"``, ``"multilingual"``, ``"form_focused"``, ``"receipt_focused"``,
       ``"handwriting_focused"`` or ``"code_focused"``; ``default_prompt`` is your own prompt
       text instead of the template's (the language instruction is still added). Structured
       requests use the task prompts.

``ocr_config`` (an :class:`~doc2mark.OCRConfig`) carries ``task``, ``language``, ``structured``,
``detail``, ``max_concurrency``, ``context_pages`` (:doc:`contextual_ocr`) and
``non_content_judge`` (see *Refusals*); ``task``, ``structured`` and ``detail`` can also be passed
to the loader directly. Tesseract takes only ``ocr_config`` (its ``language``, ``enhance_image``
and ``detect_layout``).

The OCR facade
--------------

.. code-block:: python

   from doc2mark import OCR

   ocr = OCR("openai")                       # or OCR("vertex_ai"), OCR("tesseract")
   results = ocr.read([open("receipt.png", "rb").read()])     # one OCRResult per image, in order
   result = results[0]

   print(result.text)                        # the page as safe Markdown
   page = result.document                    # OCRPage (None for structured=False)
   print(page.raw.text)                      # verbatim transcription
   print([(t.caption, t.html[:40]) for t in page.raw.tables])
   print([(f.label, f.value) for f in page.raw.fields])
   if page.interpretation is not None:
       print(page.interpretation.document_type, page.interpretation.summary)

``OCR(provider="openai", *, api_key=None, **settings)``: a keyword that is an
:class:`~doc2mark.OCRConfig` field (``task``, ``language``, ``structured``, ``detail``,
``max_concurrency``, ``model``, ``temperature``, ``max_tokens``, ...) goes into the config; one of
the provider's own arguments goes to the provider: ``project`` and ``location`` (Vertex AI),
``timeout`` and ``max_retries`` (the request timeout and retries, not the deprecated config
fields), ``max_workers``, ``prompt_template``, ``default_prompt``, ``top_p``,
``frequency_penalty``, ``presence_penalty``. Anything else raises ``TypeError``. A string
``task`` is checked against :class:`~doc2mark.Task` and ``detail`` must be ``"raw"`` or
``"full"``, otherwise ``ValueError``. The facade adds no cache and no document-level reports; use
the loader for those. For example ``OCR("vertex_ai", project="my-gcp-project",
location="europe-west4", model="gemini-2.0-flash", timeout=60)``.

``read(images, *, task=None, tasks=None, language=None, structured=None, detail=None)`` and
``read_one(image, **same)`` override the configuration per call; ``tasks`` gives one task per
image and must have the same length as ``images``. Tesseract ignores the per-call values and
uses its configured ``language``.

Structured results
~~~~~~~~~~~~~~~~~~

With the LLM providers every image becomes an :class:`~doc2mark.ocr.schema.OCRPage` (``result.document``)
with a hard boundary between two halves:

- ``raw``: what is on the page. ``text`` (all visible text in reading order, original language),
  ``tables`` (:class:`~doc2mark.ocr.schema.Table`: ``html`` with ``colspan`` / ``rowspan``, plus
  a flat ``headers`` / ``rows`` view), ``fields`` (label/value pairs), ``headings``, ``dates``,
  ``metrics``, ``detected_language``, ``has_handwriting``.
- ``interpretation``: the model's reading, or ``None``. ``document_type`` (one of 16: document,
  table, form, receipt, handwriting, code, chart, photo, screenshot, diagram, infographic, logo,
  stamp, mixed, blank, other), ``summary``, ``key_findings``, ``page_title``, ``keywords``,
  ``figures``, ``sections``, ``typed_entities``, ``relations``, ``self_confidence``,
  ``legibility`` and more (:doc:`api/schema`).

``result.text`` is ``page.to_markdown()``: the interpretation's ``page_title`` as a ``#``
heading (when ``raw.text`` does not start with it), ``raw.text`` (escaped), each table
(``html`` when present), a table of the page's metrics, the interpretation's figures and, when
its sections carry summaries, a section outline. ``fields``, ``headings``, ``dates``, the
summary and the entities are not rendered: read them from ``result.document``. For
whole-page renders of PDF pages the model also writes a cleaner ``page_markdown``, used instead
of ``raw.text`` when it covers at least 85 % of its words (:doc:`ocr_policy`).

``detail="raw"`` asks for the transcription only: Vertex AI returns ``interpretation=None``,
OpenAI asks the model to leave it out. ``structured=False`` returns free-form Markdown in
``result.text`` and ``result.document`` is ``None``. Tesseract always returns an ``OCRPage`` whose
``raw.text`` is the transcription and whose ``interpretation`` is ``None``.

With ``OCRConfig(response_model=YourModel)`` (a pydantic model; OpenAI and Vertex AI, for
example ``OCR("openai", response_model=Receipt)``) the answer is parsed into your model instead:
``result.document`` is that object (``result.document.total``) and ``result.text`` its fields as
JSON, escaped like a transcription. The free-form retry of an empty answer and the router
firewall apply to ``OCRPage`` answers only.

``result.confidence`` is the model's ``self_confidence`` for structured answers (``None``
without an interpretation or for your own model, 1.0 for free-form answers, ``None`` for
Tesseract);
``result.metadata`` holds the model, ``token_usage`` and flags such as ``failed``,
``ocr_refusal`` and ``non_content_suspected``.

Tasks
~~~~~

A :class:`~doc2mark.Task` tells an LLM provider what the image is: ``auto`` (default: the model
classifies the image first and transcribes verbatim unless it is a product screenshot with sample
data), ``table``, ``document``, ``form``, ``receipt``, ``handwriting``, ``code``. Tesseract
ignores it.

.. code-block:: python

   from pathlib import Path
   from doc2mark import OCR, Task

   images = [Path("receipt.png").read_bytes()] * 2
   ocr = OCR("openai", task="receipt")                          # default for every call
   tables = ocr.read(images, task=Task.TABLE)                    # this call
   mixed = ocr.read(images, tasks=["receipt", "handwriting"])    # one per image
   raw_only = ocr.read_one(images[0], detail="raw")
   free_form = ocr.read_one(images[0], structured=False)
   print(free_form.document, len(mixed))

.. _tesseract-languages:

Tesseract languages
~~~~~~~~~~~~~~~~~~~

``language`` (``OCRConfig``, the facade, or ``--ocr-lang`` on the CLI; default English) takes
Tesseract codes (``eng``, ``deu``, ``chi_tra``, ``jpn``, ...), ``+`` combinations
(``eng+chi_tra``) and these names: ``english``, ``chinese`` (``chi_sim+chi_tra``),
``chinese_simplified``, ``chinese_traditional``, ``spanish``, ``french``, ``german``,
``japanese``, ``korean``, ``russian``, ``arabic``. A language whose data is not installed, or a
value that is not a code, fails with ``OCREngineError`` before any image is read. For the LLM
providers ``language`` is the language the answer is written in, not a recognition setting.

.. code-block:: python

   from doc2mark import OCR, OCRConfig, UnifiedDocumentLoader

   ocr = OCR("tesseract", language="eng+chi_tra")
   print(ocr.read_one(open("receipt.png", "rb").read()).text)

   loader = UnifiedDocumentLoader(ocr_provider="tesseract", ocr_config=OCRConfig(language="english"))
   print(loader.load("scan.pdf", ocr_images=True).content)

Refusals and "no readable text" answers
---------------------------------------

A vision model sometimes answers with "I'm sorry, but I can't assist with that
request." or "圖片中沒有可辨識的文字。" instead of a transcription. doc2mark does not
index such an answer as page content:

1. Provider refusal signals count as no content: OpenAI's ``message.refusal`` (also
   when a structured answer ignores the schema), and Gemini answers stopped for
   ``SAFETY``, ``RECITATION``, ``BLOCKLIST``, ``PROHIBITED_CONTENT`` or ``SPII``.
2. A short answer that, as a whole, is the model speaking about itself or about its
   input image counts as no content too: "I'm sorry, but I can't assist with that
   request.", "I can't read the text in this image because it's too blurry.", "The
   image appears to be blank.", "No text detected in image", and the same statements
   in Chinese, Japanese, Korean, German, Spanish and French. The check is
   high-precision. Besides the refusal the answer may only hold a reason made of
   image-quality or sensitivity words ("It may contain sensitive content.") and a
   stock courtesy tail in the model's own wording ("Please provide a clearer image.",
   "If you have any other questions, feel free to ask!"). Anything else is content
   and the answer is kept: a number, a quote, a name ("Leider kann ich das Bild von
   Herrn Müller nicht erkennen."), a description of the image or a transcribed line
   after the refusal ("There is no text in this image. It shows a bar chart ..."), a
   person being addressed or asked for something ("I'm sorry Dave, I'm afraid I can't
   do that.", "Please send a clearer photo.", "your photo"), a file, folder or system,
   a sentence that goes
   on past what it refuses ("I can't read the scans until Dr. Lee signs off."), or a
   mere apology ("Sorry we missed you!", "This page intentionally left blank."). What
   the check cannot decide is left to the judge below.
3. A structured answer with no content goes to the free-form recovery, as an empty
   one always did. Only an otherwise empty page can be one: a page that also has
   tables, fields, headings, metrics, figures, sections, entities or a description
   is content whatever its ``raw.text`` says. If the recovered answer is a refusal as
   well, the result is empty text with ``metadata["ocr_refusal"] = True``; a whole-page
   render of a page with ink then reads ``[page N: OCR returned no content]`` in the
   Markdown (a blank page does not).

For the cases the patterns cannot decide, give the provider a ``non_content_judge``: a
callable returning the probability (0 to 1) that an answer is only a refusal, an error or a "no
readable text" statement, or ``None`` when it cannot tell. It sees answers of at most 600
characters that the patterns kept. From 0.5 the answer counts as no content; from 0.3 up to 0.5
it is kept and flagged ``metadata["non_content_suspected"]``; ``None``, an exception or a value
that is not a probability (outside 0 to 1, NaN, not a number) keeps it (flagged
``non_content_unjudged``, and not cached). The optional TypeSafe judge (:doc:`judge`)
provides one with its own thresholds.

.. code-block:: python

   from doc2mark import OCRConfig, UnifiedDocumentLoader

   def non_content_judge(ocr_text):
       return 0.99 if ocr_text.strip().lower().startswith("unable to process") else None

   loader = UnifiedDocumentLoader(ocr_provider="openai", ocr_config=OCRConfig(non_content_judge=non_content_judge))
   print(loader.load("scan.pdf", ocr_images=True).content[:80])

.. _ocr-failures:

Failures and reports
--------------------

A per-image failure of any provider (a timeout, a rate limit, a server error) comes back as a
result flagged ``metadata["failed"]``; in a PDF the picture shows ``[image: OCR unavailable]``.
Errors raised by an OCR request (a missing key or extra) are listed in the document's report.
Neither fails the document. An OCR engine that cannot run at all -- Tesseract without the program
or the requested language -- makes :meth:`~doc2mark.UnifiedDocumentLoader.load` raise
:class:`~doc2mark.ProcessingError` (its ``__cause__`` is ``doc2mark.ocr.base.OCREngineError``)
and the CLI exit 1, instead of writing placeholder text.

The loader reports per document in ``metadata.extra["ocr_issues"]``, present only when something
happened: ``refused`` (answers emitted empty as refusals), ``provider_refused`` (of those, the
provider's own refusal or safety block), ``failed``, ``withheld`` (sample values a screenshot
left out; the Markdown says ``[N illustrative rows not transcribed]``), ``suspected`` (answers
kept although the judge rated them close to no content), up to five ``errors`` and
``locations``: one ``{"issue", "image", "page" | "slide" | "sheet"}`` per affected image
(``image`` counts the images OCR'd in the document from 1).

Results flagged ``failed``, results that still withhold values after the verbatim redo, and
answers the judge could not screen are never cached; a provider's own refusal is cached for 10
minutes only (:doc:`caching`).

Concurrency, image size and cost
--------------------------------

- ``OCRConfig.max_concurrency`` caps how many images an LLM provider OCRs at once; when it is
  not set, the provider's ``max_workers`` (the loader's and the facade's ``max_workers``), then
  the ``OCR_MAX_CONCURRENCY`` environment variable. When none is set, LangChain's default thread
  pool is used (``min(32, CPU count + 4)`` threads). In PDFs ``max_concurrency`` also sets the
  batch size: 32 images per request batch, or twice ``max_concurrency`` when that is higher.
  Tesseract uses 4 threads.
- ``OCR_MAX_IMAGE_DIM`` (pixels, off by default) downscales images whose longest side is larger
  before an LLM provider sees them (re-encoded as PNG); smaller images are sent as they are.
- Token counts: each LLM result has ``metadata["token_usage"]``, which includes the free-form
  retry of an empty structured answer and the router firewall's verbatim redo; the loader adds a
  document's up in ``metadata.extra["token_usage"]`` (:doc:`chunking`).

.. code-block:: python

   from doc2mark import OCR

   ocr = OCR("openai", max_concurrency=16)
   result = ocr.read_one(open("receipt.png", "rb").read())
   print(result.metadata["model"], result.metadata["token_usage"]["total_tokens"])

Deprecated settings
-------------------

The ``OCRConfig`` fields ``enhance_image``, ``detect_tables``, ``detect_layout``, ``timeout``,
``max_retries`` and ``extra`` do nothing for the LLM providers. Creating an OpenAI or Vertex AI
provider with any of them set to a non-default value emits one ``DeprecationWarning``, which
names the line of your code that created the provider or the loader (so Python shows it by
default in a script); they will be removed. The request timeout and retries are the provider's
``timeout`` and ``max_retries`` arguments (the loader's, or ``OCR(..., timeout=...)``).
``enhance_image`` (grey-scale and threshold) and ``detect_layout`` (page segmentation mode) still
apply to Tesseract.

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
``?`` (so ``x < 5`` stays as it is), and control characters are removed. Entities
stay as written (``&copy;`` is how a model writes the character).

- Code spans and fenced code blocks are shown as written (``List<String>``,
  ``<div>code</div>``): a renderer shows them verbatim. Only what would be live if a
  renderer did not read the region as code is escaped there: a tag with an attribute
  value or a dangerous name (``<script>``, ``<img>``, ...), a comment, a declaration
  or a processing instruction.
- No OCR text creates an image (``![`` is escaped) or a link, link definition or
  autolink whose target has a scheme other than ``http``, ``https`` or ``mailto``
  (``javascript:``, ``data:``, ``vbscript:``, ``file:``), however the scheme is
  spelled with entities or backslash escapes.
- Transcriptions (``raw.text``, captions and Tesseract output) are plain text: a
  line that starts with a heading, quote, code fence, rule, setext underline or link
  definition marker gets a backslash, so it stays text. Their list markers stay: a
  transcribed list is a list. Single-line labels (figure and section labels, cells)
  escape a leading list marker too.
- Model-written Markdown (``page_markdown``, ``Table.markdown`` and free-form
  answers) keeps its Markdown; any ``<table>`` in it outside code goes through the
  table cleaner above, link definitions stay text, and other raw HTML except
  ``<br>`` is neutralised.
- The flat ``headers`` / ``rows`` table escapes ``|`` and turns line breaks into
  spaces, so every value stays in its cell.
- A picture inside an Office table cell is labelled ``[Image: <OCR text>]`` with the
  plain OCR text (the transcription, not its escaped Markdown), which the table
  renderer escapes once; an image or a non-http(s) link in it is broken with a blank.



