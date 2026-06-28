Supported formats & how each is handled
=======================================

doc2mark recognises an input file by its **extension** and dispatches it to a
dedicated processor. Every processor produces the same
:class:`~doc2mark.ProcessedDocument` (Markdown ``content`` plus
:class:`~doc2mark.DocumentMetadata`), so the format you feed in never changes
how you consume the result.

Two ideas are worth keeping in mind while reading this page:

- **Rule-based extraction is the default.** Office, PDF, text, markup, and email
  files are parsed structurally (no model calls), so they need **no OCR
  credentials**. OCR is *opt-in*: it only runs when you pass
  ``extract_images=True`` together with ``ocr_images=True`` (and only for the
  formats that carry images). See :doc:`/ocr_policy` for exactly how that
  decision is made, and :doc:`/ocr` for configuring a provider.
- **Legacy Office formats need LibreOffice.** ``.doc``, ``.xls``, ``.ppt``,
  ``.pps``, and ``.rtf`` are converted to their modern equivalents by a local
  LibreOffice (``soffice``) install before parsing.

How a format is detected
------------------------

Detection is extension-based (case-insensitive). The loader strips the leading
dot, lowercases it, and matches it against :class:`~doc2mark.DocumentFormat`.
Two aliases are normalised: ``.htm`` maps to ``HTML`` and ``.markdown`` maps to
``MARKDOWN`` (whose canonical extension value is ``"md"``). An unknown extension
raises :class:`~doc2mark.UnsupportedFormatError`.

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider=None)  # no OCR needed
   print(loader.supported_formats)   # ['docx', 'xlsx', 'pptx', 'doc', ...]

Extension → processor → OCR
---------------------------

The "OCR" column reflects whether OCR can ever be involved. Where it says
*Optional*, OCR runs only when both ``extract_images=True`` and
``ocr_images=True`` are passed **and** an OCR provider is configured; otherwise
the file is handled purely by rule-based extraction. Where it says *Never*, the
processor never calls OCR regardless of those flags.

.. list-table::
   :header-rows: 1
   :widths: 26 22 18 34

   * - Extensions
     - Processor
     - OCR
     - Extraction approach
   * - ``.docx``, ``.pptx``, ``.xlsx``
     - ``OfficeProcessor``
     - Optional
     - Rule-based OOXML parsing (advanced Office pipeline). Embedded images can
       be extracted as base64 and optionally OCR'd.
   * - ``.pdf``
     - ``PDFProcessor``
     - Optional
     - Rule-based text/table extraction via the advanced PDF pipeline
       (PyMuPDF fallback). Page/figure images can be extracted and optionally
       OCR'd.
   * - ``.doc``, ``.ppt``, ``.pps``, ``.xls``, ``.rtf``
     - ``LegacyProcessor``
     - Optional
     - LibreOffice converts to ``.docx`` / ``.pptx`` / ``.xlsx``, then the
       Office processor runs. **Requires LibreOffice.**
   * - ``.png``, ``.jpg``, ``.jpeg``, ``.webp``, ``.tiff``, ``.tif``,
       ``.bmp``, ``.gif``, ``.heic``, ``.heif``, ``.avif``
     - ``ImageProcessor``
     - Optional
     - Pillow reads the image; text content comes from OCR when requested.
       ``.heic`` / ``.heif`` need the ``pillow-heif`` extra.
   * - ``.txt``, ``.csv``, ``.tsv``, ``.json``, ``.jsonl``
     - ``TextProcessor``
     - Never
     - Plain-text and structured-data parsing only.
   * - ``.html``, ``.htm``, ``.xml``, ``.md``, ``.markdown``
     - ``MarkupProcessor``
     - Never
     - HTML/XML/Markdown to Markdown conversion.
   * - ``.eml``
     - ``EmailProcessor``
     - Never
     - RFC 822 header + body extraction (optional; see below).

Office formats (DOCX, PPTX, XLSX)
---------------------------------

Modern Office files are parsed by ``OfficeProcessor`` through the advanced
Office pipeline. Paragraphs, headings, lists, and tables are converted to
Markdown with page / slide / sheet markers preserved. Complex tables with
merged cells respect the loader's ``table_style`` (``'minimal_html'`` by
default, or ``'markdown_grid'`` / ``'styled_html'``).

- **Images.** With ``extract_images=True`` the embedded media is returned as
  base64 in ``ProcessedDocument.images``; adding ``ocr_images=True`` runs the
  configured OCR provider over those images (batched).
- **Image-dominant docs.** A ``.docx`` / ``.pptx`` that is mostly pictures with
  little real text (decided from the OOXML structure, no rendering) is routed
  through the PDF image strategy: LibreOffice renders it to PDF, the PDF
  processor OCRs whole pages, and the original Office identity is restored in the
  metadata (``metadata.extra['routed_via'] == 'pdf'``). This route is gated on
  OCR being requested and silently falls back to native parsing if anything
  (including a missing LibreOffice) is unavailable. ``.xlsx`` never routes this
  way.

.. code-block:: python

   loader = UnifiedDocumentLoader(ocr_provider=None)
   doc = loader.load("report.docx")
   print(doc.metadata.format)        # DocumentFormat.DOCX
   print(doc.metadata.page_count)

If the advanced pipeline is unavailable, the processor falls back to basic
python-docx / openpyxl / python-pptx parsing.

PDF
---

``PDFProcessor`` uses the advanced PDF pipeline (``pdf_to_simple_json`` →
``pdf_to_markdown``) to extract text and tables; table extraction is always on.
The ``ocr_images`` flag is mapped to the pipeline's ``use_ocr`` and only takes
effect together with ``extract_images=True``. When the advanced pipeline cannot
be imported, the processor falls back to PyMuPDF (``fitz``): per-page text
extraction, and — if OCR is enabled — rendering each page at 300 DPI and OCRing
pages that have no text layer.

.. code-block:: python

   loader = UnifiedDocumentLoader(ocr_provider="openai", api_key="sk-...")
   doc = loader.load("scanned.pdf", extract_images=True, ocr_images=True)
   print(doc.content)

Legacy Office formats (DOC, PPT, PPS, XLS, RTF)
-----------------------------------------------

``LegacyProcessor`` shells out to a local LibreOffice (``soffice``) install to
convert each legacy file to a modern container, then hands the result to
``OfficeProcessor``:

.. list-table::
   :header-rows: 1
   :widths: 30 30 40

   * - Source extension
     - Converted to
     - Backing processor
   * - ``.doc``
     - ``.docx``
     - Office (Word)
   * - ``.rtf``
     - ``.docx``
     - Office (Word)
   * - ``.ppt``
     - ``.pptx``
     - Office (PowerPoint)
   * - ``.pps``
     - ``.pptx``
     - Office (PowerPoint)
   * - ``.xls``
     - ``.xlsx``
     - Office (Excel)

The original format and filename are restored in the metadata, and the
conversion is recorded under ``metadata.extra`` (``converted_from`` /
``converted_to``). If LibreOffice is not found, processing raises
:class:`~doc2mark.ProcessingError` with installation guidance. Because the
output is a real Office document, the same image extraction / OCR options apply.

Images (PNG, JPG, JPEG, WEBP, TIFF, TIF, BMP, GIF, HEIC, HEIF, AVIF)
--------------------------------------------------------------------

``ImageProcessor`` opens the file with Pillow and writes a small Markdown header
describing the image (format, dimensions, mode, size). For standalone images the
only way to recover text is OCR:

- ``ocr_images=True`` (with a configured provider) transcribes the image; the
  text is appended under an *OCR Extracted Text* section and mirrored in
  ``json_content`` as a ``text:image_description`` entry.
- ``extract_images=True`` (the default for this processor) also returns the
  image re-encoded as PNG base64 in ``ProcessedDocument.images``.

``.heic`` / ``.heif`` decoding requires the optional ``pillow-heif`` package; if
it is not installed those formats simply cannot be opened.

.. code-block:: python

   loader = UnifiedDocumentLoader(ocr_provider="openai", api_key="sk-...")
   doc = loader.load("receipt.jpg", ocr_images=True)
   print(doc.content)

Text & data (TXT, CSV, TSV, JSON, JSONL)
----------------------------------------

``TextProcessor`` handles plain-text and structured-data files entirely with the
standard library — **no OCR, ever**:

- ``.txt`` — read as text (``encoding`` defaults to ``utf-8``); all-caps short
  lines are promoted to Markdown headings.
- ``.csv`` — the delimiter is auto-detected with :class:`csv.Sniffer` (override
  with the ``delimiter`` argument) and rows become a Markdown table.
- ``.tsv`` — same as CSV with a tab delimiter.
- ``.json`` — parsed and rendered as nested Markdown; metadata records the
  top-level ``data_type`` and ``item_count``.
- ``.jsonl`` — each non-empty line is parsed as one record; invalid lines are
  skipped with a warning, and each record is rendered under its own heading.

.. code-block:: python

   loader = UnifiedDocumentLoader(ocr_provider=None)
   doc = loader.load("data.csv", delimiter=";")
   print(doc.metadata.row_count, doc.metadata.column_count)

Markup (HTML, XML, Markdown)
----------------------------

``MarkupProcessor`` converts markup to Markdown without any OCR:

- **HTML / HTM** — parsed with BeautifulSoup and converted via ``markdownify``
  (ATX headings, ``-`` bullets) when available, falling back to a built-in
  ``SimpleHTMLToMarkdown`` parser. Title, word, link, and image counts are
  recorded in the metadata.
- **XML** — parsed safely with ``defusedxml`` and rendered as a heading tree
  (tags → headings, attributes → bullet lists, text/tail preserved). Metadata
  captures ``root_tag`` and ``element_count``.
- **Markdown (.md / .markdown)** — passed through largely as-is. Leading YAML
  front-matter is parsed (when PyYAML is installed) into ``metadata.frontmatter``,
  and heading / link / image counts are computed.

Email (EML)
-----------

``EmailProcessor`` reads RFC 822 ``.eml`` files with the standard-library
``email`` package — no OCR. It extracts the ``From``, ``To``, ``Cc``,
``Subject``, and ``Date`` headers and the best body representation, preferring a
``text/plain`` part and otherwise converting the ``text/html`` part to Markdown
with the same ``SimpleHTMLToMarkdown`` converter used for web pages. The default
Markdown rendering uses the subject as the top-level heading followed by the
remaining headers and the body; ``TEXT`` and ``JSON`` output formats are also
supported.

.. note::

   ``.eml`` support is registered only when the email module and the
   ``DocumentFormat.EML`` member are both present, so the rest of the loader
   keeps working even in builds without it.

Choosing whether OCR runs
-------------------------

For every format above, plain extraction happens with no credentials. OCR is a
deliberate add-on you enable per call via ``extract_images`` / ``ocr_images``
(images, Office, PDF, and image-dominant routing). The full decision
procedure — when an OCR provider is built, when it is invoked, and what happens
without credentials — is documented in :doc:`/ocr_policy`.
