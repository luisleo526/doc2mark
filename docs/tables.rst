Tables
======

Tables are where naive conversion loses the most: a group header over three columns, or a row
label spanning two lines, collapses into a misaligned ``| a | b | c |`` grid as soon as merged
cells are ignored. doc2mark reads the merges from the source and keeps them.

Rendering styles
----------------

A table **without** merged cells is an ordinary Markdown pipe table (PowerPoint tables are the
exception: they are always written in the table style). A table **with** merged cells is written
in the loader's ``table_style`` (``--table-style`` on the CLI):

``minimal_html`` (default)
   A plain ``<table>`` with only ``rowspan`` / ``colspan`` attributes; the first row is ``<th>``.
``markdown_grid``
   A pipe table that stays pure Markdown: an ``<!-- Merged: R1C3:1x3, ... -->`` comment lists
   the merges (row, column, rows x columns), ``⊕`` marks a merged cell and ``→`` / ``↓`` the
   positions it covers.
``styled_html``
   A ``<table border="1">`` with inline styles on every cell, after a comment line.

.. code-block:: python

   from doc2mark.core.table import TableData, TableRenderer, TableStyle

   rows = [
       ["", "", "Revenue (USD)", "", ""],
       ["Region", "Country", "2023", "2024", "2025"],
       ["Americas", "USA", "$4.2B", "$4.8B", "$5.1B"],
       ["", "Canada", "$0.9B", "$1.0B", "$1.1B"],
   ]
   spans = {(0, 2): (1, 3), (2, 0): (2, 1)}      # (row, column): (rows, columns)
   table = TableData.from_raw(rows, {"is_complex": True, "cell_spans": spans})
   print(TableRenderer(TableStyle.MINIMAL_HTML).render(table))
   print(TableRenderer(TableStyle.MARKDOWN_GRID).render(table))

prints

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
   </table>


   <!-- Merged: R1C3:1x3, R3C1:2x1 -->
   |            |         | Revenue (USD) ⊕ | →     | →     |
   | ---------- | ------- | --------------- | ----- | ----- |
   | Region     | Country | 2023            | 2024  | 2025  |
   | Americas ⊕ | USA     | $4.2B           | $4.8B | $5.1B |
   | ↓          | Canada  | $0.9B           | $1.0B | $1.1B |

The two empty corner cells stay, the group header is one ``<th colspan="3">``, and the
*Canada* row has no first cell because that position is covered by *Americas*. In the document
loader you only choose the style (a name in any case, or a :class:`~doc2mark.TableStyle`; an
unknown name raises ``ValueError`` when the loader is created; the style applies to every
format, legacy ``.doc``/``.ppt``/``.xls`` files included):

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider=None, table_style="markdown_grid")
   print(loader.load("quarterly_report.docx").content[:1200])

Cell text
---------

Cell text is escaped only as far as the table needs, the same way in every style:

- A line break inside a cell (``\n``, ``\r\n``, ``\r``, vertical tab -- a PowerPoint soft line
  break --, form feed, U+2028/U+2029) is ``<br>`` in HTML and in Markdown cells. In Markdown
  cells the spaces around a break and blank lines at the start and end of a cell are dropped;
  other control characters (except tab) are removed.
- HTML cells escape ``&``, ``<`` and ``>`` (``styled_html`` also ``"``).
- Markdown cells escape ``|`` as ``\|``, a ``<`` that would start a tag (``<img ...>`` becomes
  ``&lt;img ...>``; ``x < 5`` stays), the ``&`` of an entity (``&lt;`` in the text becomes
  ``&amp;lt;``), and double a backslash that a Markdown renderer would consume (``C:\*.txt``
  becomes ``C:\\*.txt``). ``*``, ``_`` and backticks are kept.

.. code-block:: python

   from doc2mark.core.table import TableData, TableRenderer

   table = TableData.from_raw([["Item", "Note"], ["Net\nincome", "a | b <img src=x> &lt; C:\\*.txt"]])
   print(TableRenderer().render(table))

.. code-block:: text

   | Item | Note |
   | --- | --- |
   | Net<br>income | a \| b &lt;img src=x> &amp;lt; C:\\*.txt |

The merge model
---------------

Every source ends up in the same validated grid (``doc2mark.core.table.TableData``). It pads
ragged rows, clamps spans to the table, and never lets a span hide a value: a span absorbs empty
cells and cells that repeat its own text, and is otherwise shrunk until it covers only those, so
no value disappears (a warning is logged the first time). A table whose spans were all shrunk
away is written as a plain Markdown table.

Where the merges come from
--------------------------

- **Word**: ``w:gridSpan`` gives the ``colspan``; ``w:vMerge`` (``restart`` / ``continue``) the
  ``rowspan``.
- **PowerPoint**: each cell's ``gridSpan`` and ``vMerge``.
- **Excel**: the sheet's merged ranges (a range whose top-left cell is empty is ignored). A
  sheet without merged ranges is a plain Markdown table; cells show their displayed value (``10``
  not ``10.0``, ``25%``, ``$1,234.50``, dates in the cell's format); empty rows and columns are
  dropped.
- **PDF**: the drawn cell boxes, below.
- **OCR**: the model's HTML, sanitised (see *Tables in OCR results*).

PDF tables
----------

For each page, the PyMuPDF pipeline behind
:class:`~doc2mark.UnifiedDocumentLoader` calls ``page.find_tables()``
and turns every grid it finds into a ``TableData`` (``doc2mark.pipelines.pdf_tables``).

**Cell text.** The page's characters are read once per page, from the text page
``find_tables()`` built (the same characters and coordinates PyMuPDF's own
``Table.extract()`` uses, also on rotated pages; PyMuPDF releases before 1.27.1 do not
expose it, and the page's own text is read in the same coordinates). Every character
goes to exactly one cell: the smallest cell, over all tables on the page, that
contains the centre of the glyph. So a table nested inside another table's cell
keeps its own text and the outer cell does not repeat it, and cells that overlap (an
L-shaped merged region, a frame drawn around cells) never share a character. Inside
a cell, words split at spaces and at gaps wider than 3 pt, and lines stay separate
(``\n``), as in ``Table.extract()``. A glyph's height is taken from its baseline and
font size: the text page ``find_tables()`` builds in PyMuPDF 1.28 measures glyphs by
their ink, which put an underscore below its line (``user_id`` read as ``user id``
and ``_``).

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
* Invisible text (an OCR layer: fully transparent, or neither filled nor stroked)
  drawn over visible text in a cell is dropped; the visible glyphs are the text and
  the hidden layer only repeats them, often with recognition errors
  (``Acc0unt``, ``1,25O,OOO``). A hidden line is judged as a whole: it is dropped
  when at least half of its glyphs lie over visible ones, including any part that
  runs on past them. Invisible text with nothing visible under it, as on a scanned
  page, is the cell's text.

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

**Tables without vertical rules.** A page is also searched with PyMuPDF's text
strategy (``find_tables(strategy="text")``) when at least three of its text lines
break into three or more pieces at wide gaps (at least 8 pt and one font size) and
some column of those pieces (sharing a left edge, a right edge or a centre) holds at
least two numbers that make up 60% of it, the least a table below needs; a
multi-column directory or article has the gaps but no such column and costs nothing
extra. (A page whose only aligned text is a two-column table is therefore not
searched.) A candidate is kept only when, after caption and note lines above and
below it are set aside:

* it has at least three rows and three columns (two columns when booktabs rules
  run above and below it),
* at least one column besides the first is mostly numbers,
* no row's text runs across a column boundary with ordinary word spacing (a
  boundary that splits a label in most rows, such as ``Line item | 1``, is merged
  instead), and no word straddles a column boundary or the table's left or right
  edge (text running on past the last column, such as a long comment or a sidebar
  set on the same lines, would be cut off),
* at least half of its cells have text, at most a quarter of its rows have a
  single cell, and 70% of its cells are short (at most 40 characters),
* it is not a table of contents (dot leaders, or entries whose only number is a
  last-column page number that never goes down; a table without a header row whose
  only numbers are such a column -- small counts in ascending order -- is taken for
  one and stays text),
* every text block that touches it lies inside it: a caption, lead-in sentence or note
  set at the rows' own leading -- which puts it in the same text block as the rows --
  would otherwise be split between the table and the text around it; such a table
  stays text instead.

A wrapped line of a cell (closer to its row than rows are to each other, no
numbers) stays in that cell. Prose, two-column articles, key/value blocks, tables
of contents, slide text boxes and sidebars fail these checks and stay text.
When this search was added (PR #15, September 2026) it was measured on 1,391 pages of
reference PDFs that are not part of the repository (reports, decks, forms, scans with a
text layer): it found 18 tables, all real, and no false ones, and a reviewer's set of
adversarial pages (tables inside paragraphs, a free-text last column, a sidebar, tables
of contents) kept every word. The table code has not changed since.

**Header rows.** A header row that PyMuPDF finds just above the ruled cells (drawn
without borders, for example bold names over a rule) becomes the table's header when
it names at least two columns, and only if no text block around it reaches outside
the table and the header. When a page's first table continues the previous page's
last table -- consecutive pages, the same column edges, nothing after the previous
table but the page's bottom 8%, and nothing above this one but lines in the top 8%
that repeat a line from the top 8% of the previous or the next page (a running
header, which with Word's "different first page" only the next page repeats; a
heading over a new table is not one) -- its first row is kept as its header only if
it repeats the previous header or is styled as one (bold over plain rows). Otherwise
the first row stays a data row: the previous header is repeated when it is known to
be a header (bold, or drawn above the cells), and an empty header row is used when
it is not, because a plain first row may just as well be the first pair of a
key/value form.

A table's bounding box (including a header drawn above it) is what the text
output skips, so table text is not emitted twice.

Tables in OCR results
---------------------

When a table is only an image (a scan, a screenshot), the OCR model transcribes it. LLM providers
return it in ``raw.tables`` of the :class:`~doc2mark.ocr.schema.OCRPage` as a
:class:`~doc2mark.ocr.schema.Table` with several views:

- ``html``: the preferred view, a ``<table>`` that can carry ``colspan`` / ``rowspan``;
- ``headers`` / ``rows``: a best-effort flat grid;
- ``markdown``: a Markdown rendering for simple tables;
- ``caption``;
- ``illustrative`` / ``row_count``: a table of sample values in a product screenshot, left
  header-only with the number of rows not transcribed.

``html`` is cleaned when the result is built (``doc2mark.ocr.schema.sanitize_table_html``): only
table tags, ``<br>`` and the ``colspan`` / ``rowspan`` / ``scope`` attributes survive, scripts
and styles are removed with their content, other tags are unwrapped, line structure inside a
cell becomes ``<br>``, text right before a table becomes its ``<caption>``, a Markdown pipe table
put in the field is converted, and spans are bounded (a ``colspan`` by the widest row and by
1,000, a ``rowspan`` by its row group), so a model cannot make one table cost megabytes. Empty or
unparseable input gives ``""``. When the page is rendered to Markdown,
:meth:`~doc2mark.ocr.schema.OCRPage.to_markdown` uses ``html`` when present, else ``markdown``,
else a table built from ``headers`` / ``rows``.

Reading tables from a result
----------------------------

Tables are in ``content`` and are the ``table`` items of ``json_content`` (HTML or a pipe table,
as written in ``content``); PDFs also report ``metadata.extra["tables_count"]``.

.. code-block:: python

   from doc2mark import load

   result = load("quarterly_report.pdf")
   tables = [item for item in result.json_content if item["type"] == "table"]
   print(len(tables), result.metadata.extra.get("tables_count"))
   print(tables[0]["page"], tables[0]["content"][:120])
