OCR
===

doc2mark includes an AI-powered OCR layer that returns **structured output** by
default. OCR providers are optional -- the package can process normal text
documents without any OCR credentials.

The OCR facade
--------------

The :class:`~doc2mark.ocr.OCR` class is the recommended entry point:

.. code-block:: python

   from doc2mark import OCR

   ocr = OCR("openai")                        # creds from OPENAI_API_KEY env var
   results = ocr.read([image_bytes])           # List[bytes] -> List[OCRResult]
   r = results[0]

   r.document.raw.text                         # verbatim transcription
   r.document.raw.tables                       # list of Table objects
   r.document.raw.fields                       # list of KeyValue pairs
   r.document.interpretation.summary           # model's summary
   r.document.interpretation.document_type     # "receipt", "form", ...

   r.text                                      # back-compat rendered markdown

For a single image use :meth:`~doc2mark.ocr.OCR.read_one`:

.. code-block:: python

   r = ocr.read_one(image_bytes)

Constructor signature::

   OCR(provider="openai", *, api_key=None, **config_kwargs)

All keyword arguments are forwarded to :class:`~doc2mark.ocr.base.OCRConfig`.

Structured output schema
------------------------

Every result carries an :class:`~doc2mark.ocr.schema.OCRPage` on
``result.document``. It enforces a hard boundary between **raw extraction**
(verbatim, no inference) and **interpretation** (the model's analysis).

.. code-block:: python

   OCRPage(
       raw=RawExtraction(
           text="WHOLE FOODS MARKET\n123 Main St\nOrganic Bananas  $2.49\n...",
           tables=[Table(
               caption="Line items",
               headers=["Item", "Price"],
               rows=[["Organic Bananas", "$2.49"], ["Almond Milk", "$4.99"]],
           )],
           fields=[
               KeyValue(label="Merchant", value="Whole Foods Market"),
               KeyValue(label="Subtotal", value="$7.48"),
               KeyValue(label="Tax", value="$0.62"),
               KeyValue(label="Total", value="$8.10"),
           ],
           detected_language="en",
           has_handwriting=False,
       ),
       interpretation=Interpretation(
           document_type="receipt",
           summary="A grocery receipt for two items totaling $8.10 including tax.",
           key_findings=["2 line items", "Total $8.10", "Tax rate ~8.3%"],
           self_confidence=0.93,
           legibility="high",
       ),
   )

``raw`` fields
~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Field
     - Description
   * - ``text``
     - All visible text, verbatim, in the original language.
   * - ``tables``
     - List of :class:`~doc2mark.ocr.schema.Table` (``headers``, ``rows``, ``caption``, ``markdown``).
   * - ``fields``
     - List of :class:`~doc2mark.ocr.schema.KeyValue` label/value pairs (forms, receipts).
   * - ``detected_language``
     - Language actually seen on the page (not an echo of config).
   * - ``has_handwriting``
     - Whether handwriting was detected.

``interpretation`` fields
~~~~~~~~~~~~~~~~~~~~~~~~~

``interpretation`` is ``None`` when ``detail="raw"``, for Tesseract, or on
parse-error fallback.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Field
     - Description
   * - ``document_type``
     - One of: ``document``, ``table``, ``form``, ``receipt``, ``handwriting``, ``code``, ``chart``, ``photo``, ``mixed``, ``blank``, ``other``.
   * - ``summary``
     - 1--3 sentence description of the content.
   * - ``key_findings``
     - Notable observations as a list of strings.
   * - ``self_confidence``
     - Model's own 0--1 confidence estimate.
   * - ``legibility``
     - ``"high"``, ``"medium"``, or ``"low"``.
   * - ``visual_notes``
     - Layout, branding, and non-text visual elements.
   * - ``reading_order``
     - Block indices in natural reading order.

Tasks
-----

Tasks replace the old ``PromptTemplate`` variants. Set a task at construction
time or override per call:

.. code-block:: python

   ocr = OCR("openai", task="receipt")          # all calls default to receipt
   results = ocr.read(images, task="table")     # per-call override

For mixed batches, assign a task per image:

.. code-block:: python

   results = ocr.read(images, tasks=["table", "receipt", "handwriting"])

Available :class:`~doc2mark.ocr.base.Task` values:

- ``auto`` -- general-purpose (default)
- ``table`` -- tabular data
- ``document`` -- text documents with headings and lists
- ``form`` -- form label/value extraction
- ``receipt`` -- receipts and invoices
- ``handwriting`` -- handwritten text
- ``code`` -- source code or terminal output

Raw and legacy modes
--------------------

Skip the interpretation pass to save tokens (10--30% fewer output tokens):

.. code-block:: python

   results = ocr.read(images, detail="raw")
   # r.document.interpretation is None; r.document.raw is still populated

Disable structured output entirely for free-form markdown (legacy behaviour):

.. code-block:: python

   results = ocr.read(images, structured=False)
   # r.text contains free-form markdown; r.document is None

Providers
---------

OpenAI
~~~~~~

Uses GPT-4.1 vision. Requires ``OPENAI_API_KEY``.

.. code-block:: bash

   pip install "doc2mark[ocr]"
   export OPENAI_API_KEY=sk-...

.. code-block:: python

   ocr = OCR("openai")
   ocr = OCR("openai", model="gpt-4o-mini")                       # cheaper model
   ocr = OCR("openai", base_url="http://localhost:11434/v1")       # Ollama / compatible

Google Gemini
~~~~~~~~~~~~~

Uses Gemini models via ``langchain-google-genai``. Both ``"vertex_ai"`` and
``"gemini"`` are accepted as provider names. Authenticates with
`Application Default Credentials <https://cloud.google.com/docs/authentication/application-default-credentials>`_.

.. code-block:: bash

   pip install "doc2mark[vertex_ai]"
   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json

.. code-block:: python

   ocr = OCR("gemini")
   ocr = OCR("vertex_ai", model="gemini-2.0-flash")

Tesseract (offline)
~~~~~~~~~~~~~~~~~~~

Local OCR, no API key. Returns ``raw`` only (``interpretation`` is always
``None``).

.. code-block:: bash

   pip install "doc2mark[ocr]"

.. code-block:: python

   ocr = OCR("tesseract", language="eng")

Concurrency
-----------

Control how many images are OCR'd in parallel:

.. code-block:: python

   ocr = OCR("openai", max_concurrency=32)

Or set the ``OCR_MAX_CONCURRENCY`` environment variable. When neither is set,
LangChain's default thread pool is used.

Using OCR with the document loader
-----------------------------------

:class:`~doc2mark.core.loader.UnifiedDocumentLoader` uses the OCR layer
internally when ``ocr_images=True``:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="openai")
   result = loader.load("scan.pdf", extract_images=True, ocr_images=True)

Disabling OCR
-------------

.. code-block:: python

   loader = UnifiedDocumentLoader(ocr_provider=None)
   result = loader.load("document.pdf")

.. code-block:: bash

   doc2mark document.pdf --ocr none

Deprecation notice
------------------

The old ``OCRConfig`` fields ``enhance_image``, ``detect_tables``,
``detect_layout``, ``timeout``, ``max_retries``, and ``extra`` are inert for
LLM providers. Setting them now emits a ``DeprecationWarning`` and they will be
removed in a future release. Use ``task`` and the structured output controls
instead.

API reference
-------------

.. autoclass:: doc2mark.ocr.OCR
   :members:

.. autoclass:: doc2mark.ocr.base.Task
   :members:
   :undoc-members:

.. autoclass:: doc2mark.ocr.schema.OCRPage
   :members:

.. autoclass:: doc2mark.ocr.schema.RawExtraction
   :members:

.. autoclass:: doc2mark.ocr.schema.Interpretation
   :members:

.. autoclass:: doc2mark.ocr.schema.Table
   :members:

.. autoclass:: doc2mark.ocr.schema.KeyValue
   :members:

.. autoclass:: doc2mark.ocr.base.OCRConfig
   :members:

.. autoclass:: doc2mark.ocr.base.OCRResult
   :members:
