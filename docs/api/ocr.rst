===
OCR
===

The ``doc2mark.ocr`` package turns page images into text. It is built around a
single ergonomic facade — :class:`~doc2mark.ocr.OCR` — that wraps an
interchangeable *provider* (OpenAI, Vertex/Gemini, or Tesseract) behind one
small surface: construct ``OCR(provider, **config)`` and call
:meth:`~doc2mark.ocr.OCR.read` (a batch of images) or
:meth:`~doc2mark.ocr.OCR.read_one` (a single image).

By default OCR is **structured**: each image is parsed into a
:class:`~doc2mark.OCRPage` with a hard boundary between what is literally on the
page (``document.raw``) and the model's analysis of it
(``document.interpretation``). The flat ``OCRResult.text`` is still always
populated — rendered from the raw transcription — so older code keeps working.

This page documents the facade, the configuration and result types, the
provider registry, and the three bundled providers. The structured result model
itself (:class:`~doc2mark.OCRPage` and friends) is documented on the
:doc:`schema page <schema>`.

.. contents:: On this page
   :local:
   :depth: 2


The OCR facade
==============

``OCR`` is the one class users are expected to touch. Everything else in this
package is either a type it returns, a configuration object it accepts, or a
provider it delegates to.

.. code-block:: python

   from doc2mark import OCR

   ocr = OCR("openai")                 # credentials from OPENAI_API_KEY
   results = ocr.read(list_of_png_bytes)   # List[bytes] -> List[OCRResult]

   first = results[0]
   first.text                          # rendered markdown (back-compat)
   first.document.raw.text             # verbatim transcription
   first.document.interpretation       # structured analysis (may be None)

Constructor
-----------

.. code-block:: python

   OCR(provider="openai", *, api_key=None, **config_kwargs)

``provider``
    Provider name (``"openai"``, ``"vertex_ai"``, ``"gemini"``,
    ``"tesseract"``) or an :class:`~doc2mark.ocr.OCRProvider` enum member.
    Defaults to ``"openai"``.

``api_key``
    Keyword-only API key forwarded to the provider. When omitted, providers
    fall back to their environment variable (e.g. ``OPENAI_API_KEY``);
    Vertex/Gemini uses Application Default Credentials and Tesseract needs no
    key at all.

``**config_kwargs``
    Any field of :class:`~doc2mark.ocr.OCRConfig` (``model``, ``task``,
    ``language``, ``detail``, ``structured``, ``max_concurrency``, ...). Two
    fields are coerced/validated eagerly for ergonomics: a string ``task`` is
    mapped to the matching :class:`~doc2mark.ocr.Task` member, and ``detail``
    must be ``"raw"`` or ``"full"``. An unknown value raises ``ValueError``
    immediately, listing the valid options.

.. note::

   ``OCR("openai", task="receipt")`` is shorthand — the string ``"receipt"``
   is coerced to :data:`Task.RECEIPT <doc2mark.ocr.Task>` before the config is
   built.

``read`` and ``read_one``
-------------------------

.. code-block:: python

   read(images, *, task=None, tasks=None, language=None,
        structured=None, detail=None) -> List[OCRResult]
   read_one(image, **kw) -> OCRResult

Both methods accept the same per-call overrides; ``None`` preserves whatever was
configured on the facade. ``read_one`` is a thin convenience wrapper that calls
``read([image], **kw)[0]``.

``task``
    A single OCR intent applied to every image, overriding ``config.task``.
    Accepts a :class:`~doc2mark.ocr.Task` or its string name.

``tasks``
    A per-image list of intents for mixed batches. Its length **must** equal
    ``len(images)`` or a ``ValueError`` is raised. When given, ``tasks`` wins
    over the single ``task``.

``language``
    Output-language hint appended to the prompt (overrides ``config.language``).

``structured``
    Override of ``config.structured`` for this call. ``False`` selects the
    legacy free-form path: ``OCRResult.text`` is filled and
    ``OCRResult.document`` is ``None``.

``detail``
    ``"full"`` (default) returns ``raw`` *and* ``interpretation``; ``"raw"``
    tells the model to skip the interpretation subtree, saving output tokens.

.. code-block:: python

   from doc2mark import OCR, Task

   ocr = OCR("openai", detail="full")

   # One intent for the whole batch:
   pages = ocr.read(images, task="document")

   # Mixed batch — one intent per image:
   pages = ocr.read(images, tasks=[Task.RECEIPT, Task.TABLE, "handwriting"])

   # Cheap, transcription-only pass (no interpretation):
   page = ocr.read_one(image, detail="raw")

.. autoclass:: doc2mark.ocr.OCR
   :members:
   :show-inheritance:


Tasks
=====

:class:`~doc2mark.ocr.Task` is a small ``str`` enum naming the *intent* of an
image. The intent selects a short, schema-aligned instruction that steers the
model toward the right ``raw`` fields (tables, label/value pairs, handwriting
flag, ...). Because ``Task`` subclasses ``str``, members compare equal to their
string value, which is why ``task="receipt"`` and ``Task.RECEIPT`` are
interchangeable.

================= =============================================================
Member            ``.value`` / intent
================= =============================================================
``AUTO``          ``"auto"`` — general-purpose default
``TABLE``         ``"table"`` — image dominated by tabular data
``DOCUMENT``      ``"document"`` — prose with headings, lists, reading order
``FORM``          ``"form"`` — labelled fields and filled values
``RECEIPT``       ``"receipt"`` — receipts/invoices (merchant, totals, items)
``HANDWRITING``   ``"handwriting"`` — handwritten content
``CODE``          ``"code"`` — source code or terminal output
================= =============================================================

``language`` is intentionally **not** a task — it is a separate config field, so
there is no "multilingual" task.

.. code-block:: python

   from doc2mark import Task

   list(Task)                       # all members
   Task.RECEIPT.value               # 'receipt'
   Task("table") is Task.TABLE      # True

.. autoclass:: doc2mark.ocr.Task
   :members:
   :show-inheritance:


Configuration
=============

:class:`~doc2mark.ocr.OCRConfig` is the dataclass that holds every knob. You
rarely build it directly — the ``OCR`` facade constructs it from your keyword
arguments — but it documents exactly what is tunable.

Live LLM knobs
--------------

============================ ===================== ===========================================
Field                        Default               Meaning
============================ ===================== ===========================================
``model``                    ``None``              Provider model id; ``None`` uses the provider default
``task``                     ``Task.AUTO``         Default intent for every image
``language``                 ``None``              Output-language hint
``temperature``              ``None``              Sampling temperature
``max_tokens``               ``None``              Max response tokens
``base_url``                 ``None``              OpenAI-compatible endpoint override
``max_concurrency``          ``None``              Cap on concurrent image calls (see below)
``structured``               ``True``              Structured output is the default
``detail``                   ``"full"``            ``"raw"`` skips interpretation
``response_model``           ``None``              BYO pydantic schema; ``None`` ⇒ :class:`~doc2mark.OCRPage`
``on_parse_error``           ``"raw_text"``        ``"raw_text"`` degrades gracefully; ``"raise"`` errors
============================ ===================== ===========================================

Tesseract-only fields
---------------------

``enhance_image`` (default ``True``) and ``detect_layout`` (default ``True``)
are read only by the Tesseract provider. ``detect_tables`` exists but is not
consumed by the current Tesseract path.

.. warning::

   **Deprecated, inert fields.** ``enhance_image``, ``detect_tables``,
   ``detect_layout``, ``timeout``, ``max_retries``, and ``extra`` do nothing for
   the LLM providers (OpenAI / Vertex / Gemini). Setting any of them to a
   non-default value makes the provider emit a single ``DeprecationWarning`` at
   construction. :meth:`OCRConfig.deprecated_llm_overrides` returns the list of
   such fields a caller has touched. Prefer the live knobs above.

max_concurrency and image downscaling
-------------------------------------

``max_concurrency`` caps how many image OCR calls the LLM providers run at once
inside LangChain's ``batch_as_completed``. ``None`` (the default) means "use the
LangChain default" — a CPU-tied thread pool, typically around 12. Precedence is
**explicit config value → ``OCR_MAX_CONCURRENCY`` env var → ``None``**. Raise it
to keep large scanned documents within an SLA (e.g. ``32`` for a several-thousand
page job).

A second environment variable, ``OCR_MAX_IMAGE_DIM``, controls **downscaling**.
When set to a positive integer, every image is resized so its longest side is at
most that many pixels before it is sent to the model. This trims token cost and
upload size for high-resolution scans; an unset or invalid value leaves images
untouched.

.. code-block:: python

   import os
   from doc2mark import OCR

   os.environ["OCR_MAX_IMAGE_DIM"] = "2048"   # downscale big scans
   ocr = OCR("openai", max_concurrency=32)     # 32 images in flight at once
   pages = ocr.read(images)

.. autoclass:: doc2mark.ocr.OCRConfig
   :members:
   :show-inheritance:


Results
=======

Every OCR call returns one :class:`~doc2mark.ocr.OCRResult` per input image, in
input order. ``text`` is always present; ``document`` carries the structured
:class:`~doc2mark.OCRPage` for the LLM providers (and for Tesseract, with an
empty ``interpretation``), or ``None`` for the legacy free-form path.

============== ============================== ================================================
Attribute      Type                           Meaning
============== ============================== ================================================
``text``       ``str``                        Rendered markdown — always populated
``confidence`` ``Optional[float]``            Self-confidence (LLM) or avg score (Tesseract)
``language``   ``Optional[str]``              Detected/configured language
``metadata``   ``Optional[dict]``             Model, token usage, batch index, ...
``document``   ``Optional[OCRPage]``          Structured page, or ``None`` for free-form
============== ============================== ================================================

.. code-block:: python

   from doc2mark import OCR

   ocr = OCR("openai", detail="full")
   r = ocr.read_one(receipt_png, task="receipt")

   # Flat view (back-compat):
   print(r.text)

   # Structured RAW — what is literally on the page:
   print(r.document.raw.text)              # verbatim transcription
   for kv in r.document.raw.fields:        # label/value pairs
       print(kv.label, "=", kv.value)
   for table in r.document.raw.tables:     # transcribed tables
       print(table.headers, table.rows)

   # Structured INTERPRETATION — the model's reading (None when detail="raw"):
   interp = r.document.interpretation
   if interp is not None:
       print(interp.document_type)         # e.g. "receipt"
       print(interp.summary)
       print(interp.self_confidence)       # 0.0 .. 1.0

.. autoclass:: doc2mark.ocr.OCRResult
   :members:
   :show-inheritance:


Provider registry
=================

Providers are selected by name through a tiny registry. ``OCRProvider`` is the
enum of known providers, and ``OCRFactory`` resolves a name to a concrete
:class:`~doc2mark.ocr.BaseOCR` subclass.

OCRProvider
-----------

============= ================= ===========================================
Member        ``.value``        Implementation
============= ================= ===========================================
``OPENAI``    ``"openai"``      :class:`~doc2mark.ocr.openai.OpenAIOCR`
``VERTEX_AI`` ``"vertex_ai"``   :class:`~doc2mark.ocr.vertex_ai.VertexAIOCR`
``GEMINI``    ``"gemini"``      alias → :class:`~doc2mark.ocr.vertex_ai.VertexAIOCR`
``TESSERACT`` ``"tesseract"``   :class:`~doc2mark.ocr.tesseract.TesseractOCR`
============= ================= ===========================================

``GEMINI`` is an **alias**: it is registered against the same
``VertexAIOCR`` implementation, so ``OCR("gemini")`` and ``OCR("vertex_ai")``
behave identically.

.. autoclass:: doc2mark.ocr.OCRProvider
   :members:
   :show-inheritance:

OCRFactory
----------

``OCRFactory`` is a class-level registry. Providers register themselves at
import time via :meth:`~doc2mark.ocr.OCRFactory.register_provider`; the facade
calls :meth:`~doc2mark.ocr.OCRFactory.create` for you. Provider lookup is
case-insensitive and accepts either an :class:`~doc2mark.ocr.OCRProvider` or a
string. An unknown or unregistered name raises ``ValueError``.

.. code-block:: python

   from doc2mark import OCRFactory, OCRConfig

   OCRFactory.list_providers()              # e.g. ['openai', 'vertex_ai', 'gemini', 'tesseract']
   provider = OCRFactory.create("tesseract", config=OCRConfig(language="english"))
   pages = provider.batch_process_images(list_of_png_bytes)

.. autoclass:: doc2mark.ocr.OCRFactory
   :members:
   :show-inheritance:

BaseOCR
-------

``BaseOCR`` is the abstract base every provider implements. The only required
method is :meth:`~doc2mark.ocr.BaseOCR.batch_process_images`, which all built-in
providers implement on top of efficient batch processing. Subclass it to add a
custom provider, then register it with ``OCRFactory``.

.. autoclass:: doc2mark.ocr.BaseOCR
   :members:
   :show-inheritance:


Providers
=========

OpenAIOCR
---------

GPT-4V-class OCR via LangChain. Structured output is produced with
``with_structured_output(method="json_schema")``, so the default result carries
a full :class:`~doc2mark.OCRPage`. Requires an API key
(``api_key=`` or ``OPENAI_API_KEY``) and the ``doc2mark[ocr]`` extra. The
default model is ``gpt-4.1``; ``base_url`` (or ``OPENAI_BASE_URL``) targets
OpenAI-compatible endpoints. Model knobs follow the precedence **explicit
constructor argument → ``OCRConfig`` field → built-in default**.

.. code-block:: python

   from doc2mark import OCR

   ocr = OCR("openai", model="gpt-4.1", detail="full")
   pages = ocr.read(images, task="document")
   print(pages[0].document.raw.text)

.. autoclass:: doc2mark.ocr.openai.OpenAIOCR
   :members:
   :show-inheritance:

VertexAIOCR
-----------

Google Gemini OCR via ``langchain-google-genai`` (Vertex AI backend). Like
OpenAI it is structured by default. It authenticates with Application Default
Credentials rather than an API key — set ``GOOGLE_CLOUD_PROJECT`` (or pass
``project=``) and configure ADC — and needs the ``doc2mark[vertex_ai]`` extra.
The default model is ``gemini-3.1-flash-lite-preview`` and the default location
is ``"global"``. Reachable as either ``OCR("vertex_ai")`` or ``OCR("gemini")``.

.. code-block:: python

   from doc2mark import OCR

   ocr = OCR("gemini", project="my-gcp-project")   # alias for vertex_ai
   pages = ocr.read(images, tasks=["receipt", "table"])
   print(pages[0].document.interpretation.summary)

.. autoclass:: doc2mark.ocr.vertex_ai.VertexAIOCR
   :members:
   :show-inheritance:

TesseractOCR
------------

Offline, local OCR via ``pytesseract`` + Pillow. It is **raw-only**: it produces
an :class:`~doc2mark.OCRPage` whose ``raw.text`` holds the transcription and
whose ``interpretation`` is always ``None`` (a non-LLM engine cannot infer
document type, summaries, or confidence in the same way). It needs no API key
and honours the Tesseract-only config fields ``enhance_image`` and
``detect_layout``. ``language`` is mapped to a Tesseract language code (e.g.
``"chinese"`` → ``chi_sim+chi_tra``), defaulting to English.

.. code-block:: python

   from doc2mark import OCR

   ocr = OCR("tesseract", language="english", enhance_image=True)
   r = ocr.read_one(scanned_png)
   print(r.text)                       # transcription
   print(r.document.interpretation)    # always None for Tesseract

.. autoclass:: doc2mark.ocr.tesseract.TesseractOCR
   :members:
   :show-inheritance:
