Complex Table Preservation
==========================

Tables are where naive document conversion loses the most information. A budget
sheet with a group header that spans three quarters, or an invoice with a row
label that spans two line items, collapses into a flat ``| a | b | c |`` grid the
moment you ignore merged cells. doc2mark keeps those spans intact.

The key design decision: for **digital** documents (PDFs with a real text layer,
and Office files), doc2mark reconstructs tables with a **deterministic,
rule-based path** -- not the vision/OCR model. The geometry and the markup are
already in the file, so doc2mark reads them directly. OCR is reserved for images
and scanned pages where there is no structure to recover.

Why rule-based instead of OCR
-----------------------------

A whole-page OCR pass *describes* a table; the rule-based path *reconstructs* it
from ground truth:

* **PDFs** carry vector cell boundaries. doc2mark asks PyMuPDF for the table
  grid and the per-cell bounding boxes, then measures ``rowspan`` / ``colspan``
  from the geometry -- no model guessing required.
* **Office files** (DOCX / PPTX / XLSX) store merges explicitly in OOXML
  (``w:gridSpan`` / ``w:vMerge`` for Word, ``gridSpan`` / ``vMerge`` for
  PowerPoint, ``merged_cells.ranges`` for Excel). doc2mark reads those
  attributes directly, so the merge map is exact.

This matters empirically. Native extraction reproduces **every** span
cell-accurately, including the common 2-wide column merge (a header sitting over
two adjacent columns). Whole-page OCR tends to *under-apply* those narrow merges
-- it transcribes the text but flattens the 2-column header back into a single
cell -- which silently corrupts the grid. Because the rule-based path is exact
where OCR is lossy, complex tables in digital documents stay on the rule-based
path and never round-trip through the vision model.

The PDF path: tables from the drawn grid
----------------------------------------

For each page, the PyMuPDF pipeline behind
:class:`~doc2mark.core.loader.UnifiedDocumentLoader` calls ``page.find_tables()``
and turns every grid it finds into a ``TableData`` (``doc2mark.pipelines.pdf_tables``).

**Cell text.** The page's characters are read once per page, from the text page
``find_tables()`` built (the same characters and coordinates PyMuPDF's own
``Table.extract()`` uses, also on rotated pages). Every character goes to exactly
one cell: the smallest cell, over all tables on the page, that contains the
centre of the glyph. So a table nested inside another table's cell keeps its own
text and the outer cell does not repeat it, and cells that overlap (an L-shaped
merged region, a frame drawn around cells) never share a character. Inside a
cell, words split at spaces and at gaps wider than 3 pt, and lines stay separate
(``\n``), as in ``Table.extract()``.

Text drawn over other text is handled by what it is:

* A run of text that redraws another run's text over it is a duplicate: its
  characters (spaces aside) appear in the same order in the other run, it is at
  least half as long, at about the same size (within 20%) and baseline, and within
  the other run's extent. That is fake-bold overprint, or a second text layer from
  design software (``385 / 1 405`` drawn over ``385 / 491 / 1 405``). Only the
  longer run is kept. A run of one or two characters must sit exactly on the same
  glyphs to count.
* Anything else is kept: a value typed over a ``____`` placeholder in a flattened
  form, a tick drawn over a checkbox, a watermark crossing a row. Where such runs
  overlap, each run is split into words on its own and the words are read left to
  right, so a typed value stays in one piece next to its label
  (``Name: John Smith ____``, ``姓名：＿＿＿ 王小明``); a single glyph dropped onto
  text stays in place (``[X] Yes [ ] No``).

**Merged cells.** PyMuPDF reports the grid as rows of cell boxes (``None`` where a
merged box covers a position). Column ``j`` starts at the ``j``-th distinct cell
left edge and row ``i`` at the top of row ``i``. A drawn cell box spans every
column and row line it crosses by more than 1 pt, which gives its ``colspan`` and
``rowspan``; a span that would cover another drawn cell is shrunk (widest first)
so every drawn cell keeps its position. Blank cells are blank cells: they never
become merges, and a merge is detected however much of its column it covers. A
grid position that no drawn cell covers (an open corner of a header row) is an
empty cell, and text drawn there (``Unit: NT$ thousand``) is kept in it.

**Which grids are tables.** ``find_tables()`` also finds grids in page decoration:
a logo built from two filled rectangles, a slide background with panels. A grid is
emitted as a table only if

* it has more than one cell and some text, and
* it has text in at least two rows and two columns, its cells do not overlap, and
  it covers less than 85% of the page -- **or** its cell borders are drawn as lines
  (at least two cell edges covered by stroked lines or hairline fills of at most
  2 pt; large filled areas do not count).

A ruled single-row table (a signature line) is kept; a logo made of filled shapes
(one row of text), a frame of overlapping panels or a page-sized background is
not, and its text stays with the normal text output.

**Tables without vertical rules.** On pages where at least three text lines break
into three or more pieces at wide gaps (at least 8 pt and one font size), the text
is also searched with PyMuPDF's text strategy (``find_tables(strategy="text")``).
Such a candidate is kept only when, after caption and note lines above and below
it are set aside:

* it has at least three rows and three columns (two columns when booktabs rules
  run above and below it),
* at least one column besides the first is mostly numbers,
* no row's text runs across a column boundary with ordinary word spacing (a
  boundary that splits a label in most rows, such as ``Line item | 1``, is merged
  instead) and no word straddles a boundary,
* at least half of its cells have text, at most a quarter of its rows have a
  single cell, and 70% of its cells are short (at most 40 characters).

A wrapped line of a cell (closer to its row than rows are to each other, no
numbers) stays in that cell. Prose, two-column articles, key/value blocks, tables
of contents and slide text boxes fail these checks and stay text. Measured on 1,379
pages of reference PDFs (reports, decks, forms, scans with a text layer), the
search found 18 tables, all real, and no false ones.

**Header rows.** A header row that PyMuPDF finds just above the ruled cells (drawn
without borders, for example bold names over a rule) becomes the table's header
when it names at least two columns. When a page's first table continues the
previous page's last table -- consecutive pages, nothing but the running
header/footer bands between them, the same column edges -- its first row is kept
as its header only if it repeats the previous header or is styled as one (bold
over plain rows). Otherwise the first row stays a data row: the previous header
is repeated when it is known to be a header (bold, or drawn above the cells), and
an empty header row is used when it is not, because a plain first row may just as
well be the first pair of a key/value form.

A table's bounding box (including a header drawn above it) is what the text
output skips, so table text is not emitted twice.

The Office path: OOXML grid spans
---------------------------------

The Office pipeline reads the merge map straight from the markup rather than
guessing from blank cells.

* **Word (DOCX):** for each ``w:tc`` cell, ``w:gridSpan/@w:val`` gives the
  ``colspan``. ``w:vMerge`` gives vertical merges: ``val="restart"`` opens a
  rowspan and ``val="continue"`` (or a bare ``w:vMerge``) extends it. A second
  pass counts the continuation rows under each ``restart`` cell to compute the
  final ``rowspan``. This is an O(n*m) attribute read, not an O(n^2*m^2)
  cell-identity comparison.
* **PowerPoint (PPTX):** each cell's ``gridSpan`` and ``vMerge`` properties are
  read off the shape's table; continuation cells are blank, and the origin cell
  accumulates ``rowspan`` / ``colspan``.
* **Excel (XLSX):** ``merged_cells.ranges`` gives merge rectangles directly; the
  span is ``max_row - min_row + 1`` by ``max_col - min_col + 1``, with spans
  re-clamped when columns inside the range are dropped.

All three converge on the same ``TableData`` structure used by the PDF path.

From ``TableData`` to clean HTML
--------------------------------

``TableData`` is a validated, self-normalizing grid. Its model validators:

* pad ragged rows to a rectangle,
* clamp every span to the table bounds (a ``rowspan`` can never run past the last
  row),
* never let a span cover a non-empty cell or another span: such a span is shrunk
  to the widest run of empty cells in its first row, then to as many rows as stay
  empty across that width, so no value is ever hidden (a warning is logged the
  first time this happens),
* mark the positions covered by a span as *continuation* cells, and
* auto-set ``is_complex = True`` when any span is present.

A ``TableRenderer`` then renders it. The renderer chooses its output from the
``table_style`` you configured (see below). For a complex table the default
**minimal HTML** renderer emits one ``<tr>`` per visual row, writes ``<th>`` for
the first physical row and ``<td>`` elsewhere, attaches ``rowspan`` / ``colspan``
only when greater than 1 and **skips continuation cells entirely**. Simple
(span-free) tables render as ordinary pipe-delimited Markdown instead.

Cell text is escaped only as far as the table structure needs, the same way in
every style:

* Line breaks of every kind (``\r\n``, ``\r``, vertical tab -- a soft line break in
  PowerPoint --, form feed, U+2028/U+2029) become ``<br>`` in HTML cells and a
  space in Markdown cells; other C0 control characters (except tab) are removed.
* HTML cells escape ``&``, ``<`` and ``>`` (``styled_html`` also ``"``).
* Markdown cells (pipe tables and ``markdown_grid``) escape ``|`` as ``\|`` (a
  backslash right before it is doubled so it survives), ``<`` only where it would
  start a tag (``<img …>`` becomes ``&lt;img …>``; ``x < 5`` stays as is) and ``&``
  only where it would start an entity (``&lt;`` in the text becomes
  ``&amp;lt;``). Everything else, including ``*``, ``_`` and backticks, is kept
  verbatim.

Choosing the output style
-------------------------

The loader exposes ``table_style``, which maps to :class:`~doc2mark.TableStyle`:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(table_style="minimal_html")
   doc = loader.load("quarterly_report.pdf")

From the command line, pass ``--table-style``:

.. code-block:: bash

   doc2mark quarterly_report.pdf --table-style markdown_grid

The three accepted values (string or enum) are:

``minimal_html`` (default)
   Clean ``<table>`` with only ``rowspan`` / ``colspan`` attributes -- the
   recommended style for downstream Markdown and RAG.

``markdown_grid``
   A Markdown grid that keeps cell alignment and records merges as an
   ``<!-- Merged: R1C2:1x3, ... -->`` comment plus ``⊕`` / ``→`` / ``↓`` span
   markers, for pipelines that must stay pure-Markdown.

``styled_html``
   Full HTML with inline ``border`` / ``style`` attributes (legacy; verbose).

Worked example: a merged-cell table
------------------------------------

Consider revenue by region and country, with a group header spanning the three
year columns and a region label spanning two country rows:

.. code-block:: text

   +----------+----------+-----------------------------+
   |          |          |        Revenue (USD)        |   <- spans 3 columns
   +----------+----------+--------+--------+-----------+
   | Region   | Country  |  2023  |  2024  |   2025    |
   +----------+----------+--------+--------+-----------+
   | Americas | USA      | $4.2B  | $4.8B  |   $5.1B   |   <- "Americas"
   +  (spans  +----------+--------+--------+-----------+      spans 2 rows
   |  2 rows) | Canada   | $0.9B  | $1.0B  |   $1.1B   |
   +----------+----------+--------+--------+-----------+
   | EMEA     | Germany  | $2.1B  | $2.3B  |   $2.5B   |
   +----------+----------+--------+--------+-----------+

The geometry (PDF) or the ``gridSpan`` / ``vMerge`` markup (Office) yields a
``colspan=3`` on *Revenue (USD)* and a ``rowspan=2`` on *Americas*. With the
default ``minimal_html`` style, doc2mark emits exactly:

.. code-block:: text

   <table>
   <tr>
   <th></th>
   <th></th>
   <th colspan="3">Revenue (USD)</th>
   </tr>
   <tr>
   <td>Region</td>
   <td>Country</td>
   <td>2023</td>
   <td>2024</td>
   <td>2025</td>
   </tr>
   <tr>
   <td rowspan="2">Americas</td>
   <td>USA</td>
   <td>$4.2B</td>
   <td>$4.8B</td>
   <td>$5.1B</td>
   </tr>
   <tr>
   <td>Canada</td>
   <td>$0.9B</td>
   <td>$1.0B</td>
   <td>$1.1B</td>
   </tr>
   <tr>
   <td>EMEA</td>
   <td>Germany</td>
   <td>$2.1B</td>
   <td>$2.3B</td>
   <td>$2.5B</td>
   </tr>
   </table>

Note the two empty top-left ``<th></th>`` corner cells are preserved (they
anchor the row/column header axes), the group header is a single
``<th colspan="3">`` rather than three separate cells, and *Americas* appears
once as ``<td rowspan="2">`` -- the *Canada* row omits its first cell because that
position is a continuation of the span. The renderer marks only the first
physical row as ``<th>``; the *Region / Country / 2023...* sub-header row is
emitted as ``<td>``.

The OCR table path and ``Table.html``
-------------------------------------

When a table *is* an image (a scanned page, or a screenshot region), there is no
geometry to read, so it goes through the OCR layer instead. Each image becomes an
:class:`~doc2mark.ocr.schema.OCRPage`, and any tables land in
``page.raw.tables`` as :class:`~doc2mark.ocr.schema.Table` objects. The same
``rowspan`` / ``colspan`` idea applies, but the HTML now comes from the vision
model, so it is **sanitized at the model boundary** before it is ever stored or
rendered.

:class:`~doc2mark.ocr.schema.Table` carries several views of the same table:

* ``html`` -- the preferred representation; a clean ``<table>`` that can encode
  merged cells via ``colspan`` / ``rowspan``.
* ``headers`` / ``rows`` -- a best-effort flat view for simple machine access.
* ``markdown`` -- a rendered Markdown fallback for simple (non-merged) tables.
* ``caption`` -- the table caption, if any.
* ``illustrative`` -- ``True`` when the table holds demo/sample values (e.g. a
  product-screenshot mockup) rather than real data, so indexers can down-weight
  it.
* ``row_count`` -- for a header-only ``illustrative`` table, how many sample rows
  were intentionally not transcribed.

The ``html`` field runs through ``doc2mark.ocr.schema.sanitize_table_html`` as a
Pydantic validator, so the stored value is always safe to embed. The sanitizer:

* keeps only table-structural tags -- ``table``, ``thead``, ``tbody``,
  ``tfoot``, ``tr``, ``th``, ``td``, ``caption``, ``col``, ``colgroup`` -- and
  **unwraps** every other tag while preserving its inner text;
* keeps only the ``colspan``, ``rowspan``, and ``scope`` attributes (dropping
  classes, ids, inline styles, URLs, event handlers, and everything else), and
  drops ``colspan`` / ``rowspan`` whose value is not an integer;
* strips dangerous elements entirely (``script``, ``style``, ``iframe``,
  ``object``, ``embed``, ``form``, ``svg``, ``math``, and similar);
* tolerates a model wrapping its output in a ``` ```html ``` code fence; and
* **fails closed** -- it returns ``""`` for empty or unparseable input, so
  unsanitized model HTML is never emitted.

``to_markdown()`` prefers ``html``
----------------------------------

:meth:`~doc2mark.ocr.schema.OCRPage.to_markdown` renders a single readable
Markdown string from a page and is the source of the back-compat
``OCRResult.text``. For each table in ``raw.tables`` it follows a strict
preference order so merged cells survive:

#. ``table.html`` (the sanitized HTML with spans) -- used whenever present;
#. else ``table.markdown`` (the rendered simple-table fallback);
#. else a Markdown table reconstructed from ``table.headers`` / ``table.rows``.

So if the model produced span-bearing HTML, ``to_markdown()`` keeps it verbatim
rather than degrading to a flat header/row grid.

Reading tables from a result
----------------------------

**Digital documents (rule-based path).** ``loader.load(...)`` returns a
:class:`~doc2mark.ProcessedDocument`. The rendered table HTML is embedded inline
in ``doc.content``, and each table is also a discrete item in
``doc.json_content`` with ``type == "table"``:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader, OutputFormat

   loader = UnifiedDocumentLoader(table_style="minimal_html")
   doc = loader.load("quarterly_report.docx", output_format=OutputFormat.JSON)

   for item in doc.json_content or []:
       if item["type"] == "table":
           print(item["content"])     # the <table>...</table> string with spans

**Image OCR (vision path).** When you OCR an image directly, the structured
table objects live on the page:

.. code-block:: python

   from doc2mark import OCR

   ocr = OCR("openai")
   result = ocr.read_one(image_bytes)        # -> OCRResult

   page = result.document                    # OCRPage
   for table in page.raw.tables:             # list[Table]
       print(table.caption)
       print(table.html)                     # sanitized HTML, colspan/rowspan
       if not table.html:
           print(table.headers, table.rows)  # flat fallback view

   print(result.text)                        # to_markdown(): prefers table.html

In both paths the merged-cell structure is preserved: as exact, geometry- or
markup-derived HTML for digital documents, and as sanitized model HTML for
images.
