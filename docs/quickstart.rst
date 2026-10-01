Quickstart
==========

Convert a file
--------------

.. code-block:: python

   from doc2mark import load

   result = load("report.pdf")
   print(result.content)                  # the document as Markdown
   print(result.metadata.format)          # DocumentFormat.PDF
   print(result.metadata.page_count)      # 2

:func:`~doc2mark.load` builds a :class:`~doc2mark.UnifiedDocumentLoader`, converts one file and
returns a :class:`~doc2mark.ProcessedDocument`. The format comes from the file extension (see
:doc:`formats`). Nothing is sent anywhere: text, tables and structure are read from the file
itself, OCR runs only when you ask for it, and so does the optional judge (unless the
``DOC2MARK_JUDGE`` environment variable turns it on, see :doc:`judge`).

To convert many files, create the loader once and reuse it:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader()
   for name in ["report.pdf", "report.docx", "slides.pptx", "data.csv"]:
       result = loader.load(name)
       print(name, len(result.content), "characters")

``UnifiedDocumentLoader()`` sets up the OpenAI OCR provider by default, but it only needs the
``doc2mark[ocr]`` extra and ``OPENAI_API_KEY`` when a document is actually OCR'd; text-only
conversions work without either. ``UnifiedDocumentLoader(ocr_provider=None)`` turns OCR off
completely.

What you get back
-----------------

.. code-block:: python

   from doc2mark import load

   result = load("report.pdf")
   for item in result.json_content[:4]:
       print(item["type"], item.get("level"), item["page"], item["content"][:40])

``content`` is the Markdown. ``json_content`` lists the same content as typed items in reading
order (``text:title``, ``text:section``, ``text:normal``, ``table``, ...), each with its page;
it is what the chunker reads. ``metadata`` carries the file name, format, size, page count and
format-specific fields, and ``metadata.extra`` reports what happened during the conversion
(OCR routing, OCR problems, token usage). :doc:`output` describes every field.

Output formats
--------------

.. code-block:: python

   import json
   from doc2mark import load

   markdown = load("report.pdf").content                        # the default
   as_json = load("report.pdf", output_format="json").content   # a JSON string
   print(sorted(json.loads(as_json)))
   plain = load("report.pdf", output_format="text").content     # Markdown markers removed

The JSON string is ``result.to_dict()`` serialised: ``content`` (the Markdown), ``metadata``,
``images``, ``tables``, ``sections`` and ``json_content``. The CLI's ``--format json`` writes the
same payload. ``"text"`` removes heading markers, bold, italics, links and code marks from the
Markdown; tables stay as they are.

Reading scans and pictures (OCR)
--------------------------------

Pass an OCR provider and ``ocr_images=True``. Tesseract runs locally (``doc2mark[ocr]`` plus the
``tesseract`` program, see :doc:`installation`):

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="tesseract")
   result = loader.load("scan.pdf", ocr_images=True)
   print(result.content)
   print(result.metadata.extra["ocr_routing"])   # {'document_route': 'image', 'overrides': []}

The LLM providers read tables, forms and handwriting much better and return structured results
(:doc:`ocr`):

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="openai")      # reads OPENAI_API_KEY
   result = loader.load("scan.pdf", ocr_images=True)

``ocr_images=True`` implies ``extract_images=True``. doc2mark then decides page by page what
needs OCR: pages with a usable text layer keep their text, scanned or garbled pages are OCR'd
from a render, and pictures are OCR'd when they carry something to read (:doc:`ocr_policy`).

A folder of documents
---------------------

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider=None)
   results = loader.batch_process("documents", output_dir="converted", max_workers=4)
   for path, info in results.items():
       print(info["status"], path)

Every supported file under ``documents/`` (recursively) is converted and written to
``converted/`` as ``.md``, keeping the folder structure. See :doc:`loading` for the result
dictionaries, explicit file lists and progress callbacks.

From the command line
---------------------

.. code-block:: bash

   doc2mark report.pdf -o report.md
   doc2mark scan.pdf --ocr tesseract --ocr-images -o scan.md
   doc2mark documents/ -r -o converted/ --skip-errors

:doc:`cli` lists every option.

Next steps
----------

- :doc:`rag` -- a minimal retrieval pipeline: convert, chunk, embed.
- :doc:`output` -- the result model and the ``metadata.extra`` reports.
- :doc:`ocr` -- providers, tasks and structured OCR results.
- :doc:`pdf` -- how PDF text becomes Markdown (headings, lists, reading order, running headers).
- :doc:`tables` -- merged cells and table styles.
- :doc:`judge` -- the optional Jev quality judge for the cases the rules cannot settle (off by
  default; with it on, document text is sent to TypeSafe).
