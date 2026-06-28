Quickstart
==========

doc2mark turns documents (PDF, Office, images, text/data, and legacy Office
files) into clean Markdown or structured Python objects. Text-only extraction
needs no API keys; OCR providers are initialized only when you actually ask
for OCR.

Install
-------

.. code-block:: bash

   pip install doc2mark

The 30-second example
---------------------

Load a PDF and read the Markdown:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader()
   result = loader.load("document.pdf")

   print(result.content)            # Markdown string
   print(result.metadata.filename)  # document.pdf

``result`` is a :class:`~doc2mark.ProcessedDocument`. Its most useful fields:

- ``content`` -- the rendered output (Markdown by default).
- ``metadata`` -- a :class:`~doc2mark.DocumentMetadata` (``filename``,
  ``format``, ``size_bytes``, ``page_count``, ...).
- ``tables`` / ``images`` / ``sections`` / ``json_content`` -- structured
  extras, populated depending on the format and options.

For one-off conversions there is a module-level :func:`~doc2mark.load` helper
that builds a loader for you:

.. code-block:: python

   from doc2mark import load

   markdown = load("report.docx").content

Output formats
--------------

``load()`` accepts an ``output_format`` of ``"markdown"`` (default),
``"json"``, or ``"text"`` (the :class:`~doc2mark.OutputFormat` enum values
``MARKDOWN``, ``JSON``, ``TEXT``).

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader()

   # Markdown (default)
   md = loader.load("document.pdf").content

   # JSON: result.content is a JSON string; result.json_content is the
   # structured list of content blocks (handy for chunking / RAG).
   doc = loader.load("document.pdf", output_format="json")
   print(doc.content)         # JSON string
   print(doc.json_content)    # list of {"type": ..., "content": ...} blocks

   # Plain text: layout and Markdown markup stripped out
   text = loader.load("document.pdf", output_format="text").content

Every result can also be turned into a plain dict with ``result.to_dict()``
(the same payload the CLI writes for ``--format json``).

Text-only vs OCR
----------------

By default doc2mark does **not** call any OCR model -- it extracts the text
that already lives in the file. This path needs no credentials and is the
right choice for digital PDFs and Office documents.

To describe images embedded in a document, opt in with two flags:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="openai")  # reads OPENAI_API_KEY

   result = loader.load(
       "scanned.pdf",
       extract_images=True,   # pull images out of the document
       ocr_images=True,       # run OCR on those images
   )

``ocr_images=True`` requires ``extract_images=True``. With
``extract_images=True, ocr_images=False`` images are kept as base64 data
instead of being described.

Picking a provider
------------------

Pass ``ocr_provider`` to the loader (or to :func:`~doc2mark.load`). Built-in
providers: ``"openai"`` (default), ``"vertex_ai"`` (Google Gemini), and
``"tesseract"`` (local, no API key). Use ``ocr_provider=None`` to disable OCR
entirely.

.. code-block:: python

   # OpenAI (default model: gpt-5.4-mini). Reads OPENAI_API_KEY by default.
   loader = UnifiedDocumentLoader(ocr_provider="openai")

   # Google Vertex AI / Gemini (default model: a gemini-3.x flash model).
   loader = UnifiedDocumentLoader(
       ocr_provider="vertex_ai",
       project="my-gcp-project",   # or GOOGLE_CLOUD_PROJECT env var
   )

   # Local Tesseract -- no network, no API key.
   loader = UnifiedDocumentLoader(ocr_provider="tesseract")

You can override the model and other knobs on the constructor, e.g.
``UnifiedDocumentLoader(ocr_provider="openai", model="gpt-5.4-mini",
temperature=0)``. See :doc:`/ocr` for the full set of OCR options.

From the command line
---------------------

The ``doc2mark`` CLI mirrors the Python API:

.. code-block:: bash

   # Single file to stdout (Markdown)
   doc2mark document.pdf

   # Save Markdown
   doc2mark document.pdf -o output.md

   # JSON output
   doc2mark document.pdf --format json -o output.json

   # OCR a scanned PDF with OpenAI (implies --extract-images)
   doc2mark scanned.pdf --ocr openai --ocr-images

   # Process a directory recursively, 4 files in parallel
   doc2mark docs/ -r -p 4 -o out/

OCR is off by default in the CLI (``--ocr none``). See :doc:`/cli` for every
flag.

Batch processing
----------------

Convert a whole directory in one call:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader()
   results = loader.batch_process(
       input_dir="documents",
       output_dir="converted",
       recursive=True,
       save_files=True,
       max_workers=4,   # process files concurrently
   )

``batch_process`` returns a dict mapping each input path to a per-file result
(status, output files, and metadata).

Where to next
-------------

- :doc:`/formats` -- every supported file type and how each is parsed.
- :doc:`/ocr` -- OCR providers, models, tasks, and structured output.
- :doc:`/ocr_policy` -- when OCR runs and how to keep extraction text-only.
- :doc:`/tables` -- table extraction and merged-cell (rowspan/colspan) output.
- :doc:`/contextual_ocr` -- richer, structured OCR interpretation of images.
- :doc:`/caching` -- document and OCR caching to avoid repeat work.
