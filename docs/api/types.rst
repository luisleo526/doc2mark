Results, enums and exceptions
=============================

Guide: :doc:`/output`.

ProcessedDocument
-----------------

.. autoclass:: doc2mark.ProcessedDocument
   :members: to_dict, get_chunks, markdown, text

   Fields: ``content`` (str), ``metadata`` (:class:`~doc2mark.DocumentMetadata`), ``images``,
   ``tables``, ``sections`` and ``json_content`` (lists of dicts or ``None``). The built-in
   processors never fill ``tables`` or ``sections``.

DocumentMetadata
----------------

.. autoclass:: doc2mark.DocumentMetadata

``filename``, ``format`` (a :class:`~doc2mark.DocumentFormat`) and ``size_bytes`` are always set;
``extra`` is a dict (``{}`` by default) of conversion reports (:doc:`/output`). The other fields
are ``None`` unless the format sets them:

.. list-table::
   :header-rows: 1
   :widths: 26 74

   * - Field
     - Set by
   * - ``page_count``
     - PDF (pages), Word (pages counted from page and section breaks), PowerPoint (slides),
       Excel (sheets), image files (1), legacy files.
   * - ``word_count``
     - PDF, Word, text, Markdown, HTML, image files (OCR text).
   * - ``title``
     - HTML (``<title>``).
   * - ``slide_count``
     - PowerPoint, but the value is unreliable (it counts the text "Slide " in the output); use
       ``page_count``.
   * - ``line_count``
     - Text and Markdown files.
   * - ``header_count``, ``frontmatter``
     - Markdown (``frontmatter`` needs PyYAML).
   * - ``link_count``, ``image_count``
     - HTML and Markdown; ``image_count`` also Word/PowerPoint/Excel (pictures kept as base64)
       and image files (1).
   * - ``encoding``
     - Text, CSV, JSON, JSONL, HTML, XML and Markdown files: the ``encoding`` argument (not
       detected).
   * - ``delimiter``, ``row_count``, ``column_count``
     - CSV (``row_count`` includes the header row).
   * - ``record_count``
     - JSONL.
   * - ``data_type``, ``item_count``
     - JSON (``data_type`` is the Python type name: ``dict``, ``list``, ...).
   * - ``element_count``, ``root_tag``
     - XML.
   * - ``language``, ``creation_date``, ``modification_date``, ``author``, ``sheet_names``,
       ``total_cells``
     - Not set by the default processors.

Enums
-----

.. autoclass:: doc2mark.DocumentFormat
   :members:
   :undoc-members:

.. autoclass:: doc2mark.OutputFormat
   :members:
   :undoc-members:

.. autoclass:: doc2mark.TableStyle
   :members:
   :undoc-members:

Exceptions
----------

:class:`~doc2mark.ProcessingError` is the base class of the library's own exceptions.
:meth:`~doc2mark.UnifiedDocumentLoader.load` raises :class:`~doc2mark.UnsupportedFormatError`
for an unknown extension and wraps every failure during conversion in a ``ProcessingError``
whose ``__cause__`` is the original error (for example ``OCREngineError``, a subclass of
:class:`~doc2mark.OCRError`, or :class:`~doc2mark.ConversionError`). A missing file raises
``FileNotFoundError`` and an unknown ``output_format`` raises ``ValueError``.

.. code-block:: python

   from doc2mark import ProcessingError, UnsupportedFormatError, load

   for name in ["archive.7z", "legacy.doc", "report.pdf"]:
       try:
           result = load(name)
           print(name, "ok", len(result.content))
       except UnsupportedFormatError as exc:
           print(name, "unsupported:", exc)
       except ProcessingError as exc:
           print(name, "failed:", exc, "| cause:", repr(exc.__cause__))

.. autoexception:: doc2mark.ProcessingError

.. autoexception:: doc2mark.UnsupportedFormatError

.. autoexception:: doc2mark.OCRError

.. autoexception:: doc2mark.ConversionError
