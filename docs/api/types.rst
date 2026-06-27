Core Types & Exceptions
=======================

This page documents the core data types, enumerations, and exceptions that
form the foundation of ``doc2mark``.  Every call to :func:`doc2mark.load` or
:meth:`~doc2mark.UnifiedDocumentLoader.load` returns a
:class:`~doc2mark.ProcessedDocument` whose fields are described here.
The enumerations control which input formats are recognised and which output
representations are available, while the exception hierarchy lets callers
handle errors at the right level of granularity.


Enumerations
------------

DocumentFormat
~~~~~~~~~~~~~~

An enumeration of every file format that ``doc2mark`` can ingest.  Each member
value is the lowercase file extension (without a leading dot).

.. list-table::
   :header-rows: 1
   :widths: 30 30 40

   * - Category
     - Members
     - Extension values
   * - Office
     - ``DOCX``, ``XLSX``, ``PPTX``
     - ``"docx"``, ``"xlsx"``, ``"pptx"``
   * - Legacy Office
     - ``DOC``, ``XLS``, ``PPT``, ``RTF``, ``PPS``
     - ``"doc"``, ``"xls"``, ``"ppt"``, ``"rtf"``, ``"pps"``
   * - PDF
     - ``PDF``
     - ``"pdf"``
   * - Text / Data
     - ``TXT``, ``CSV``, ``TSV``, ``JSON``, ``JSONL``
     - ``"txt"``, ``"csv"``, ``"tsv"``, ``"json"``, ``"jsonl"``
   * - Markup
     - ``HTML``, ``XML``, ``MARKDOWN``
     - ``"html"``, ``"xml"``, ``"md"``
   * - Email
     - ``EML``
     - ``"eml"``
   * - Image
     - ``PNG``, ``JPG``, ``JPEG``, ``WEBP``, ``TIFF``, ``TIF``,
       ``BMP``, ``GIF``, ``HEIC``, ``HEIF``, ``AVIF``
     - ``"png"``, ``"jpg"``, ``"jpeg"``, ``"webp"``, ``"tiff"``,
       ``"tif"``, ``"bmp"``, ``"gif"``, ``"heic"``, ``"heif"``,
       ``"avif"``

.. code-block:: python

   from doc2mark import DocumentFormat

   fmt = DocumentFormat.PDF
   print(fmt.value)  # "pdf"

.. autoclass:: doc2mark.DocumentFormat
   :members:
   :undoc-members:
   :show-inheritance:


OutputFormat
~~~~~~~~~~~~

Controls the representation produced by the loader.

``MARKDOWN``
   Produce a Markdown string (value ``"markdown"``).  This is the default.

``JSON``
   Produce a JSON-structured representation (value ``"json"``).

``TEXT``
   Produce plain text with formatting stripped (value ``"text"``).

.. code-block:: python

   from doc2mark import OutputFormat

   fmt = OutputFormat.MARKDOWN
   print(fmt.value)  # "markdown"

.. autoclass:: doc2mark.OutputFormat
   :members:
   :undoc-members:
   :show-inheritance:


TableStyle
~~~~~~~~~~

Selects how complex tables (those with merged cells) are rendered in the
output.  Defined in :mod:`doc2mark.core.table`.

``MINIMAL_HTML``
   Bare ``<table>`` markup without inline styles (value ``"minimal_html"``).
   This is the **default**, returned by :meth:`TableStyle.default`.

``MARKDOWN_GRID``
   A Markdown pipe table annotated with merge markers and an HTML comment
   listing spanned regions (value ``"markdown_grid"``).

``STYLED_HTML``
   A fully styled ``<table>`` with borders, padding, and background colours
   on header cells (value ``"styled_html"``).

.. code-block:: python

   from doc2mark import TableStyle

   style = TableStyle.default()       # TableStyle.MINIMAL_HTML
   print(style.value)                  # "minimal_html"

.. autoclass:: doc2mark.TableStyle
   :members:
   :undoc-members:
   :show-inheritance:


Result Objects
--------------

ProcessedDocument
~~~~~~~~~~~~~~~~~

The primary return value from any document-loading call.  It is a
:func:`~dataclasses.dataclass` with the following fields:

``content`` : str
   The converted document body (Markdown by default).

``metadata`` : :class:`~doc2mark.DocumentMetadata`
   Structured metadata about the source file.

``images`` : Optional[List[Dict[str, Any]]]
   Extracted images, each represented as a dictionary.  ``None`` when image
   extraction is not requested.

``tables`` : Optional[List[Dict[str, Any]]]
   Extracted table data.  ``None`` when no tables are present.

``sections`` : Optional[List[Dict[str, Any]]]
   Document sections, when the format supports them.  ``None`` otherwise.

``json_content`` : Optional[List[Dict[str, Any]]]
   A structured JSON representation of the document used for chunking
   (compatible with the ``UnifiedMarkdownLoader`` pipeline).  ``None`` when
   the output format does not produce it.

Convenience properties and methods:

``markdown`` : str (property)
   Alias for ``content``.

``text`` : str (property)
   Returns ``content`` with common Markdown formatting stripped.

``to_dict()`` -> Dict[str, Any]
   Returns a fully JSON-serializable dictionary of the document (enum values
   are converted to strings, bytes are base64-encoded).

``get_chunks(config=None)``
   Split the document into section-aware chunks for RAG pipelines.
   Accepts an optional :class:`~doc2mark.ChunkingConfig`; uses defaults
   when ``None``.

.. code-block:: python

   from doc2mark import load

   result = load("report.pdf")

   # Markdown body
   print(result.content[:200])

   # Metadata
   print(result.metadata.filename)
   print(result.metadata.page_count)

   # Plain-text view (Markdown formatting removed)
   plain = result.text

   # JSON-safe dict for serialisation
   data = result.to_dict()

   # Section-aware chunks for RAG
   chunks = result.get_chunks()

.. autoclass:: doc2mark.ProcessedDocument
   :members:
   :show-inheritance:


DocumentMetadata
~~~~~~~~~~~~~~~~

A :func:`~dataclasses.dataclass` that carries information about the source
file.  Only the first three fields are always populated; the rest are
``None`` unless the format provides them.

**Always present:**

``filename`` : str
   Original file name.

``format`` : :class:`~doc2mark.DocumentFormat`
   Detected input format.

``size_bytes`` : int
   File size in bytes.

**Common optional fields:**

``page_count`` : Optional[int]
   Number of pages (PDF, DOCX, PPTX).

``word_count`` : Optional[int]
   Approximate word count.

``language`` : Optional[str]
   Detected language code.

``creation_date`` : Optional[str]
   Creation timestamp from file metadata.

``modification_date`` : Optional[str]
   Last-modification timestamp from file metadata.

``author`` : Optional[str]
   Author name from file metadata.

``title`` : Optional[str]
   Document title from file metadata.

**Format-specific optional fields:**

``sheet_names`` : Optional[List[str]]
   Sheet names (XLSX).

``slide_count`` : Optional[int]
   Number of slides (PPTX).

``line_count`` : Optional[int]
   Number of lines (text files).

``header_count`` : Optional[int]
   Number of headings (Markdown).

``link_count`` : Optional[int]
   Number of hyperlinks (HTML, Markdown).

``image_count`` : Optional[int]
   Number of images found in the document.

``total_cells`` : Optional[int]
   Total cell count (XLSX).

``encoding`` : Optional[str]
   Detected character encoding (text files).

``delimiter`` : Optional[str]
   Field delimiter (CSV, TSV).

``record_count`` : Optional[int]
   Number of records (JSONL).

``row_count`` : Optional[int]
   Number of rows (CSV).

``column_count`` : Optional[int]
   Number of columns (CSV).

``element_count`` : Optional[int]
   Number of XML elements (XML).

``root_tag`` : Optional[str]
   Root element tag name (XML).

``frontmatter`` : Optional[Dict[str, Any]]
   Parsed YAML front-matter (Markdown).

``data_type`` : Optional[str]
   Top-level JSON type descriptor (JSON).

``item_count`` : Optional[int]
   Number of top-level items (JSON).

``extra`` : Dict[str, Any]
   Catch-all dictionary for additional metadata not covered by the fields
   above.  Defaults to an empty dict.

.. code-block:: python

   from doc2mark import load

   result = load("spreadsheet.xlsx")
   meta = result.metadata

   print(meta.filename)       # "spreadsheet.xlsx"
   print(meta.format)         # DocumentFormat.XLSX
   print(meta.size_bytes)     # 14832
   print(meta.sheet_names)    # ["Sheet1", "Summary"]

.. autoclass:: doc2mark.DocumentMetadata
   :members:
   :show-inheritance:


Exceptions
----------

All exceptions raised by ``doc2mark`` inherit from :class:`ProcessingError`,
so a single ``except ProcessingError`` clause catches every library-specific
failure.

.. code-block:: none

   Exception
    +-- ProcessingError
         +-- UnsupportedFormatError
         +-- OCRError
         +-- ConversionError


ProcessingError
~~~~~~~~~~~~~~~

Base exception for all document-processing errors.  Catch this when you want
a single handler for any ``doc2mark`` failure.

.. code-block:: python

   from doc2mark import load, ProcessingError

   try:
       result = load("data.bin")
   except ProcessingError as exc:
       print(f"Could not process file: {exc}")

.. autoexception:: doc2mark.ProcessingError
   :show-inheritance:


UnsupportedFormatError
~~~~~~~~~~~~~~~~~~~~~~

Raised when the file extension is not recognised or no processor is
registered for the detected :class:`~doc2mark.DocumentFormat`.

.. code-block:: python

   from doc2mark import load, UnsupportedFormatError

   try:
       result = load("archive.7z")
   except UnsupportedFormatError:
       print("This file type is not supported.")

.. autoexception:: doc2mark.UnsupportedFormatError
   :show-inheritance:


OCRError
~~~~~~~~

Raised when an OCR backend (e.g. :class:`~doc2mark.OCRProvider` ``OPENAI`` or
``VERTEX_AI``) fails -- for example due to an invalid API key, a network
timeout, or an unsupported image.

.. code-block:: python

   from doc2mark import load, OCRError

   try:
       result = load("scan.pdf", extract_images=True, ocr_images=True)
   except OCRError:
       print("OCR processing failed.")

.. autoexception:: doc2mark.OCRError
   :show-inheritance:


ConversionError
~~~~~~~~~~~~~~~

Raised when format conversion fails internally -- for instance when a legacy
``.doc`` file cannot be converted to ``.docx`` before processing.

.. code-block:: python

   from doc2mark import load, ConversionError

   try:
       result = load("legacy.doc")
   except ConversionError:
       print("Document conversion failed.")

.. autoexception:: doc2mark.ConversionError
   :show-inheritance:
