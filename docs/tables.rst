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
  grid and the per-cell bounding boxes, then infers ``rowspan`` / ``colspan``
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

The PDF path: geometric span detection
--------------------------------------

For each page, :class:`~doc2mark.core.loader.UnifiedDocumentLoader` (via its
PyMuPDF pipeline) calls ``page.find_tables()`` and, for every detected table,
runs ``_convert_table_to_markdown_enhanced``. That method:

#. Extracts cell text per cell with de-duplication of overlapping spans, so
   text that visually straddles a boundary is not double-counted.
#. Calls ``_analyze_table_with_boundaries`` to build a normalized grid.

``_analyze_table_with_boundaries`` prefers **true geometry** when PyMuPDF exposes
per-cell boxes: ``_get_cell_boundaries`` reads each cell's bounding box, and
``_detect_merges_from_boundaries`` checks, via ``_bboxes_overlap_significantly``
(default 80% overlap), how many logical grid positions a single physical box
covers. A box that covers two columns becomes ``colspan=2``; one that covers two
rows becomes ``rowspan=2``.

When per-cell boxes are unavailable, it falls back to a conservative
**empty-cell** heuristic with explicit guards against false positives on sparse
data:

* It pre-computes, per column, the fraction of empty cells. A column that is
  more than 50% empty is treated as *legitimately sparse* (``col_mostly_empty``)
  and is **not** read as a merge.
* **First pass -- colspans:** a non-empty cell absorbs trailing empty cells to
  its right *only* when those columns are not mostly-empty; absorbed positions
  are recorded so they cannot also be claimed as rowspans.
* **Second pass -- rowspans:** a non-empty cell (not already part of a colspan,
  not in a mostly-empty column) absorbs empty cells directly below it.

The result is a ``TableData`` object (from ``doc2mark.core.table``) with the
spans attached.

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
* mark the positions covered by a span as *continuation* cells, and
* auto-set ``is_complex = True`` when any span is present.

A ``TableRenderer`` then renders it. The renderer chooses its output from the
``table_style`` you configured (see below). For a complex table the default
**minimal HTML** renderer emits one ``<tr>`` per visual row, writes ``<th>`` for
the first physical row and ``<td>`` elsewhere, attaches ``rowspan`` / ``colspan``
only when greater than 1, **skips continuation cells entirely**, and
HTML-escapes ``&``, ``<``, ``>``. Simple (span-free) tables render as ordinary
pipe-delimited Markdown instead.

Choosing the output style
-------------------------

The loader exposes ``table_style``, which maps to :class:`~doc2mark.TableStyle`:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(table_style="minimal_html")
   doc = loader.load("quarterly_report.pdf")

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
