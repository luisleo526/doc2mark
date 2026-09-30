Troubleshooting
===============

Seeing what doc2mark does
-------------------------

doc2mark logs through the ``doc2mark`` logger, to which it attaches only a ``NullHandler``.
Warnings name the pages that need attention (a garbled text layer kept without OCR, hidden text
left out, pages with no text), and ``INFO`` shows routing and cache decisions:

.. code-block:: python

   import logging
   from doc2mark import load

   logging.basicConfig(level=logging.INFO)
   result = load("report.pdf")

The CLI logs warnings to stderr by default, everything with ``-v`` and only errors with ``-q``.
Most answers are also in ``result.metadata.extra`` (:doc:`output`).

Common problems
---------------

**A scanned PDF comes out empty.** Its pages have no text layer; a warning says so. Turn OCR on:
``loader.load("scan.pdf", ocr_images=True)`` with an OCR provider, or
``doc2mark scan.pdf --ocr tesseract --ocr-images``.

**Garbled characters in PDF text.** The PDF's fonts do not map glyphs to characters.
``metadata.extra["text_layer_quality"]`` lists the pages; with OCR on they are OCR'd from their
render (:doc:`ocr_policy`). Layers that are valid Unicode but nonsense are caught only by the
optional :doc:`judge`.

**Tesseract language errors.** The requested language data is not installed (``tesseract
--list-langs`` shows what is). doc2mark raises :class:`~doc2mark.ProcessingError` (cause
``OCREngineError``) instead of writing placeholder text, and the CLI exits 1. Install the data
(``tesseract-ocr-deu`` and so on) or fix ``TESSDATA_PREFIX``.

**The OpenAI provider does nothing, or fails.** It needs ``pip install "doc2mark[ocr]"`` and
``OPENAI_API_KEY`` (or ``api_key=``) set before the loader is created, and OCR only runs with
``ocr_images=True``. Without the extra or the key every OCR request fails: the document still
converts, the pictures show a placeholder (``[image: OCR unavailable]`` in PDF output) and the
error is listed in ``metadata.extra["ocr_issues"]["errors"]``.

**A legacy Office file fails.** LibreOffice (``soffice``) was not found when the loader was
created; see :doc:`installation`.

**A header line appears once, or a line repeats on every page.** Running headers and footers are
handled verbatim first: bare page numbers are dropped, a line repeating a title or heading is
dropped, any other repeated header or footer keeps its first copy, and a repeated line without
strong evidence (fewer than 3 pages, not set apart from the content) keeps every copy. The
optional judge can thin those. :doc:`pdf` has the details.

**The CLI prints only the start of the document.** Without ``-o`` the Markdown on stdout is cut
after 1,000 characters; use ``-v`` or ``-o FILE``.

**A folder run stops at a sub-folder or an unsupported file.** The CLI tries every entry the
pattern matches; pass ``--pattern "*.pdf"`` (or similar) or ``--skip-errors``.

**Batch processing skips files.** ``batch_process`` looks for lower-case extensions
(``report.PDF`` is not found) and not for ``.htm``; convert such files with ``load()`` or
``batch_process_files()``.

**A TSV file fails.** TSV conversion currently fails with a ``ProcessingError`` (a known bug);
rename the file to ``.csv``, whose delimiter detection finds the tabs.

**HTML comes out flat.** Install ``markdownify`` (``pip install markdownify``); without it a
simple built-in converter is used and a warning is logged.

**The document cache does not speed up a re-run.** A document is not cached when its OCR failed
somewhere, a page showing content stayed unread, the provider refused an image, or the judge
could not answer; an ``INFO`` log line gives the reason. Changing the loader's options (output
format, table style, OCR provider, judges) also changes the cache key (:doc:`caching`).

**Token chunking stalls or fails offline.** tiktoken downloads its encoding data on first use;
pre-fill ``TIKTOKEN_CACHE_DIR`` on machines without network access.

**PyMuPDF prints to stdout in my application.** PyMuPDF 1.26.7 and later print a recommendation
of its ``pymupdf_layout`` package the first time tables are searched, and MuPDF prints errors
about damaged files. The ``doc2mark`` CLI turns both away from stdout; in your own program call
``pymupdf.no_recommend_layout()`` and ``pymupdf.set_messages(...)`` if stdout matters.

**HEIC/HEIF images cannot be opened.** Install ``doc2mark[heif]``.
