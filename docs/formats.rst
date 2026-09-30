Supported formats
=================

The format comes from the file extension, case-insensitively; the content is not sniffed, so a
file with the wrong extension goes to the wrong reader. ``.htm`` is read as HTML and
``.markdown`` as Markdown. Any other unknown extension (``.odt``, ``.xlsm``, ``.msg``, no
extension) raises :class:`~doc2mark.UnsupportedFormatError`.

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider=None)
   print(loader.supported_formats)   # ['docx', 'xlsx', 'pptx', 'doc', 'xls', 'ppt', 'rtf', 'pps', 'pdf', ...]

.. list-table::
   :header-rows: 1
   :widths: 30 20 50

   * - Extensions
     - OCR
     - How it is read
   * - ``.pdf``
     - with ``ocr_images``
     - PyMuPDF text layer, tables from the drawn grid, per-page OCR routing (:doc:`pdf`,
       :doc:`tables`, :doc:`ocr_policy`).
   * - ``.docx``, ``.pptx``, ``.xlsx``
     - with ``ocr_images``
     - The Office XML, below.
   * - ``.doc``, ``.xls``, ``.ppt``, ``.pps``, ``.rtf``
     - with ``ocr_images``
     - Converted by LibreOffice to ``.docx`` / ``.xlsx`` / ``.pptx``, then read like those.
   * - ``.png``, ``.jpg``, ``.jpeg``, ``.webp``, ``.tif``, ``.tiff``, ``.bmp``, ``.gif``,
       ``.heic``, ``.heif``, ``.avif``
     - with ``ocr_images``
     - Pillow; the text comes from OCR (:doc:`images`).
   * - ``.txt``, ``.csv``, ``.tsv``, ``.json``, ``.jsonl``
     - never
     - Standard library.
   * - ``.html``, ``.htm``, ``.xml``, ``.md``, ``.markdown``
     - never
     - BeautifulSoup / defusedxml / as written.
   * - ``.eml``
     - never
     - Standard-library ``email``.

"With ``ocr_images``" means: when ``ocr_images=True`` and an OCR provider is configured. Only PDF,
Office and image files return a ``json_content`` list; the others have ``json_content=None``
(see :doc:`output`).

Word, PowerPoint, Excel
-----------------------

Word, PowerPoint and Excel files are read from their XML. The Markdown starts each page, slide or
sheet with a marker comment (``<!-- page 2 -->``, ``<!-- slide 3 -->``, ``<!-- sheet 1 -->``);
a Word file stores no pages, so its page numbers count the page breaks recorded in it (explicit
breaks, section breaks and the page breaks Word saved at its last layout). Text is
escaped like PDF text (:doc:`pdf`), and ``json_content`` items carry ``page``.

**Word (.docx).** Headings follow the paragraph's outline level (Title ``#``, Subtitle ``##``,
Heading N one level per rank; a paragraph set to outline level "body text" is no heading) and
carry ``level``; lists keep their numbers and bullets (``1.``, ``-``, ``(1)``, ``壹、``, nested).
Text in content controls, tracked insertions, fields, hyperlinks and nested tables (flattened into
their cell) is kept; deleted text is not. Footnotes become ``[^N]`` / ``[^N]: ...``. Not
extracted or not in the Markdown:

- text boxes are not extracted at all;
- header and footer paragraphs are only ``text:header`` / ``text:footer`` items in
  ``json_content``, and tables in headers and footers are lost;
- bold, italics and link targets are not kept;
- a paragraph that starts with *Table*, *Figure*, *Chart* (and similar) or *Source:* / *Note:*
  is written as an italic caption.

**PowerPoint (.pptx).** Each slide lists its title (``#``), subtitle (``##``) and shapes top to
bottom, then its notes (``[Slide N Notes]``). A soft line break is a line break (``<br>`` in
table cells); bullets stay as their characters, not Markdown lists. Tables are always written in
the merged-cell style (``table_style``: HTML by default), even without merged cells; charts as
their title and axis captions. Known issues: the
slide layout's placeholder prompts (*Click to edit Master title style*, the date field, ``‹#›``)
appear as captions on every slide, and the text of plain shapes such as rectangles is emitted
twice.

**Excel (.xlsx).** Each sheet is ``# Sheet: <name>`` followed by its table. Cells show what
Excel displays: ``10`` not ``10.0``, ``25%``, ``$1,234.50``, a date in the cell's format
(``2026-03-31`` for ``yyyy-mm-dd``), rounded the way the format rounds, a formula saved without
a cached value as the formula (``=A2+B2``). Merged cells come from the sheet's merged ranges (a
sheet without them is a plain Markdown table; a range whose top-left cell is empty is ignored);
empty rows and columns are dropped; a single-cell row above the table becomes a paragraph only
when it is merged across the table or separated by a blank row.

**Pictures.** With ``extract_images=True`` and no OCR, each picture is embedded in the Markdown as
a ``data:`` URI image, is an ``image`` item of ``json_content``, and is listed in
``result.images`` as ``{"data": <bytes>, "page": n}``. With OCR, all pictures of a document are
sent in one batch and their text replaces them (``text:image_description``); a picture in a Word
or Excel table cell becomes ``[Image]`` / ``[Image: <text>]`` in that cell; a picture whose OCR
finds no text adds nothing; one whose OCR fails leaves the text ``OCR failed`` and is counted in
``metadata.extra["ocr_issues"]``.

**The Office image route.** A ``.docx`` or ``.pptx`` made mostly of pictures (slides that are
full-slide images, a Word file of scanned pages) has no text to read natively. With OCR on,
doc2mark measures picture coverage and text length from the XML (the same thresholds as for
PDFs: coverage at least 0.55 and fewer than 200 characters of text, per slide on average for
PowerPoint, for the whole document against one page for Word); a file that
looks image-dominant is converted to PDF with LibreOffice, and when that PDF's own route is
``image`` it is read by the PDF pipeline (whole-page OCR), with ``metadata.extra["routed_via"] ==
"pdf"``. Otherwise the file is read natively and, when the route was tried,
``metadata.extra`` records ``routed_via: "native"`` with ``route_reason`` (the PDF routes as
text) or ``route_error`` (for example LibreOffice missing). Excel files never take this route.

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider=None)
   doc = loader.load("report.docx")
   print(doc.metadata.format, doc.metadata.page_count)
   print(doc.content[:300])

Legacy Office files
-------------------

``.doc`` and ``.rtf`` are converted to ``.docx``, ``.ppt`` and ``.pps`` to ``.pptx``, ``.xls``
to ``.xlsx``, by LibreOffice (``soffice``, looked up on the ``PATH`` and in the usual install locations when
the loader is created), each conversion with its own throwaway profile; then the converted file is
read as above. The original format and file name are kept in the metadata, and
``metadata.extra`` has ``converted_from`` and ``converted_to`` (``"doc"``, ``"docx"``). Without
LibreOffice, :meth:`~doc2mark.UnifiedDocumentLoader.load` raises
:class:`~doc2mark.ProcessingError` with installation guidance. The loader's ``table_style``
applies to the converted file like it does to a Word, Excel or PowerPoint file.

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   doc = UnifiedDocumentLoader(ocr_provider=None).load("legacy.doc")
   print(doc.metadata.format, doc.metadata.extra["converted_from"], doc.metadata.extra["converted_to"])

Text and data
-------------

- **.txt**: read with ``encoding`` (default ``utf-8``, not detected: a file in another encoding
  fails unless you pass it); a short line written entirely in capitals becomes a ``##`` heading,
  everything else is kept as written. Metadata: ``word_count``, ``line_count``.
- **.csv**: the delimiter is the ``delimiter`` argument (one character) when you pass it,
  otherwise it is detected (``csv.Sniffer`` on the first 1,024 characters, a comma when nothing
  is found). The first row is the header of a Markdown table. Cell text is not escaped.
  Metadata: ``row_count`` (header included), ``column_count``, ``delimiter``.
- **.tsv**: the same table and metadata, always tab separated (``delimiter`` is the CSV option
  and does not apply). A TSV has no quoting: ``5" pipe`` and ``"Best" seller`` keep their quote
  characters, and a quote never joins rows.
- **.json**: objects become ``**key**: value`` lines, lists ``-`` items, nested with indentation.
  Metadata: ``data_type`` (``dict``, ``list``, ...), ``item_count``.
- **.jsonl**: ``# JSONL Data (N records)`` and one ``## Record i`` per valid line; invalid lines are
  skipped with a warning. Metadata: ``record_count``.

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   doc = UnifiedDocumentLoader(ocr_provider=None).load("data.csv")
   print(doc.metadata.delimiter, doc.metadata.row_count, doc.metadata.column_count)
   print(doc.content.splitlines()[0])

Markup
------

- **HTML** is parsed with BeautifulSoup and converted with ``markdownify`` when that package is
  installed (``pip install markdownify``; it is not a doc2mark dependency). Without it a much
  simpler built-in converter is used and a warning is logged: tables are flattened and script or
  style text can leak. Metadata: ``title``, ``word_count``, ``link_count``, ``image_count``.
- **XML** is parsed with defusedxml and written as a heading tree (element names as headings,
  attributes as lists, text kept). Metadata: ``root_tag``, ``element_count``.
- **Markdown** is kept as written. YAML front matter is moved to ``metadata.frontmatter`` when
  PyYAML is installed (it comes with the ``ocr`` and ``vertex_ai`` extras). It is front matter
  only when the file starts with a ``---`` line, a closing ``---`` line follows and the lines
  between them are YAML that parses to a mapping; everything else that starts with a rule
  (prose or a list between two rules, invalid YAML, a date that does not exist, no closing rule)
  stays in the text, which is never changed. The text after the front matter is kept as written, except for the blank
  lines right after the closing rule. Dates in front matter are ``datetime.date`` objects in
  ``metadata.frontmatter`` and ISO strings in ``to_dict()`` and the CLI's JSON. Metadata:
  ``header_count``, ``link_count``, ``image_count``, ``line_count``.

E-mail
------

``.eml`` files are read with the standard-library ``email`` package: ``# <Subject>``, then
``From``, ``To``, ``Cc`` and ``Date`` lines, a rule and the body. The body is every
``text/plain`` part joined (including text attachments), or, without one, the ``text/html`` part
converted with the built-in HTML converter. Attachments are not converted and nothing is OCR'd.
The metadata has only the file name, format and size.
