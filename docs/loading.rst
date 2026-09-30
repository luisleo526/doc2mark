Loading documents
=================

The loader
----------

:class:`~doc2mark.UnifiedDocumentLoader` holds the configuration that applies to every document:
the OCR provider and its settings, the table style, the caches and the optional judge. Create it
once and call :meth:`~doc2mark.UnifiedDocumentLoader.load` for each file (the batch methods
below call it from several threads at once).

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(
       ocr_provider="tesseract",        # "openai" (default), "vertex_ai", "tesseract", None
       table_style="markdown_grid",     # "minimal_html" (default), "markdown_grid", "styled_html"
       cache_dir=".doc2mark-cache",     # reuse converted documents across runs
   )
   result = loader.load("report.pdf")
   print(result.content[:200])

The most used constructor arguments (the :doc:`API reference <api/loader>` has all of them):

``ocr_provider``
   ``"openai"`` (default), ``"vertex_ai"`` (also ``"gemini"``), ``"tesseract"``, an
   :class:`~doc2mark.OCRProvider`, a provider instance, or ``None`` / ``"none"`` for no OCR.
   The provider is only used when a document is loaded with ``ocr_images=True``.
``api_key``, ``model``, ``base_url``, ``project``, ``location``, ``temperature``, ``max_tokens``
   Provider settings (:doc:`ocr`). ``model`` defaults to ``gpt-5.4-mini`` for OpenAI and to
   ``gemini-3.1-flash-lite-preview`` for Vertex AI.
``ocr_config``
   An :class:`~doc2mark.OCRConfig` for everything else about OCR (task, language, structured
   output, concurrency, neighbour-page context, non-content judge). ``task``, ``structured``
   and ``detail`` can also be given directly.
``table_style``
   How tables with merged cells are written (:doc:`tables`).
``cache_dir``, ``ocr_cache``
   The converted-document cache and the OCR answer cache (:doc:`caching`).
``judge``, ``legibility_judge``, ``boilerplate_judge``
   The optional judge (:doc:`judge`).

Loading one file
----------------

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider=None)
   result = loader.load("data.csv", encoding="utf-8")
   print(result.metadata.row_count, result.metadata.column_count)

``load(file_path, output_format="markdown", extract_images=False, ocr_images=False,
show_progress=False, encoding="utf-8", delimiter=None)``:

``output_format``
   ``"markdown"``, ``"json"`` or ``"text"`` (:doc:`output`).
``extract_images``
   Return the pictures of PDF, Word, Excel and PowerPoint files: without OCR they are embedded in
   the Markdown as ``data:`` URI images, are ``image`` items of ``json_content`` and are listed in
   ``images``. For an image file, the file itself re-encoded as PNG (:doc:`images`).
``ocr_images``
   OCR what needs it: scanned or garbled pages and the pictures that carry something to read
   (:doc:`ocr_policy`), and image files. When an OCR provider is configured it implies
   ``extract_images=True``. It has no effect on text, CSV, JSON, HTML, XML, Markdown and e-mail
   files.
``encoding``, ``delimiter``
   ``encoding`` is used for text, CSV, JSON and markup files (default ``utf-8``, not detected).
   ``delimiter`` (one character) is the delimiter of CSV files; without it the delimiter is
   detected. A ``.tsv`` file is always tab separated.

Convenience functions
---------------------

For scripts, the module-level functions create a loader for one call:

.. code-block:: python

   from doc2mark import document_to_markdown, load

   result = load("report.docx")
   markdown = document_to_markdown("report.docx", output_path="out/report.md")

:func:`~doc2mark.load`, :func:`~doc2mark.document_to_markdown`,
:func:`~doc2mark.batch_convert_to_markdown`, :func:`~doc2mark.batch_process_documents` and
``batch_process_files`` take ``ocr_provider`` (default ``"openai"``), ``api_key`` and
``ocr_cache`` for the loader, and route every other keyword argument where it belongs: an option
of ``load()`` (``encoding``, ``delimiter``, ``show_progress``) or of the batch method
(``encoding``, ``delimiter``, ``max_workers``, ``progress_callback``) goes to that call, every
other :class:`~doc2mark.UnifiedDocumentLoader` setting (``table_style``, ``model``,
``cache_dir``, ``judge``, ...) to the loader. So ``load("report.pdf",
table_style="markdown_grid")`` and ``load("report.pdf", cache_dir=".cache")`` work, and in a
batch function ``max_workers`` is the number of files converted at once, not the OCR
concurrency (set that on a loader of your own). A name that is neither raises ``TypeError``.

Folders and file lists
----------------------

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider=None)
   results = loader.batch_process(
       "documents",
       output_dir="converted",
       max_workers=4,
       progress_callback=lambda done, total, path: print(f"{done}/{total} {path}"),
   )
   failed = {path: info["error"] for path, info in results.items() if info["status"] == "failed"}
   print(len(results), "files,", len(failed), "failed")

:meth:`~doc2mark.UnifiedDocumentLoader.batch_process` finds every file under ``input_dir``
(``recursive=True`` by default) that :meth:`~doc2mark.UnifiedDocumentLoader.load` accepts,
whatever the case of its extension (``.PDF``, ``.htm`` and ``.markdown`` included; folders are
never converted themselves), in path order, converts it and, with ``save_files=True`` (the
default), writes ``<name>.md`` (or ``.json``; the dots of the file name are kept: ``v1.2.txt`` gives
``v1.2.md``), keeping the folder structure (two files of one folder with the same stem, such as
``report.txt`` and ``report.md``, write the same output file: the later one wins; the CLI names
them apart), and, when pictures
were extracted, a ``<name>_images/`` folder (a known issue: a PDF whose pictures were extracted
without OCR is then reported as failed although its ``.md`` was written). With
``output_format="text"`` no file is written. Without ``output_dir`` the files are written next to
the inputs. One file failing does not stop the batch.

The returned dict maps each input path (a string, in input order) to a result:

.. code-block:: python

   {"status": "success", "format": "pdf", "content_length": 8120, "duration": 0.41,
    "output_files": ["converted/report.md"],
    "metadata": {"images_extracted": 0, "tables_found": 0, "pages": 2}}

   {"status": "failed", "error": "Processing failed: ...", "format": ".pdf"}

``tables_found`` is the number of tables of the document (``ProcessedDocument.tables``, see
:doc:`output`); it is 0 for files without content items (text, data and markup files).

:meth:`~doc2mark.UnifiedDocumentLoader.batch_process_files` takes a list of paths instead. It
writes only when ``output_dir`` is given, as ``output_dir/<file stem>.md`` (two inputs with the
same stem overwrite each other), and its results have no ``pages``.

``max_workers`` above 1 converts that many files at once in threads (``None``, the default,
converts them one after the other); results keep the input order. It is separate from the OCR
concurrency inside one document (``OCRConfig.max_concurrency``). ``progress_callback(done, total,
path)`` is called after each file, failed or not.

:meth:`~doc2mark.UnifiedDocumentLoader.load_directory` returns the
:class:`~doc2mark.ProcessedDocument` objects of a folder instead of writing files; files it cannot
convert are skipped with a warning:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider=None)
   documents = loader.load_directory("documents", pattern="*.pdf")
   print([d.metadata.filename for d in documents])
