PDF text structure
==================

A PDF's text layer is a set of positioned glyphs; doc2mark turns it into Markdown with PyMuPDF
(1.27.1 or later) and deterministic rules. The rules keep the text as printed unless there is
strong evidence to change it (*verbatim first*): when in doubt, a line stays where it is and as
it is. Tables are described in :doc:`tables`, and which pages are OCR'd in :doc:`ocr_policy`.
Each page after the first starts with a ``<!-- page N -->`` comment in the Markdown, and every
``json_content`` item carries its ``page``.

Headings
--------

- The title is ``#``: the largest heading of the first page with text, when no other heading of
  the document is as large. It appears at most once (a page that was OCR'd can still carry its
  own page title).
- Section headings are ``##`` and deeper, ranked by font size across the document and by decimal
  outline depth (``1.2`` below ``1``), without skipped levels. Bold body-size lines, Word 2013+
  headings and ``第一條``-style CJK headings count. A heading is one line without bold or italic
  markup. The ``json_content`` items ``text:title`` / ``text:section`` carry the same ``level``.
- Uppercase labels, chart labels, drop caps, running headers and text set across the page's
  reading direction (a vertical arXiv stamp) are not headings.

Lists, captions, footnotes, superscripts
----------------------------------------

- **Lists.** Bullets become ``- `` (nested by indentation); ``*`` and ``+`` keep their
  character, and a bullet that carries meaning stays in the item (``- ✓ Approved``). Numbered
  items keep their number. Letters, roman numerals and CJK or parenthesised numbers stay in the
  item text (``- a) ...``) and make a list only in a sequence, so a lone ``A. Smith`` or
  ``E. coli`` line is text. A lead-in line and the paragraph after a list are separate
  paragraphs.
- **Captions.** Numbered labels (``Figure 3:``, ``Table 2``, ``圖1``) and short caption-shaped
  text attached to an image or table are captions, written in italics line by line; body text
  next to a table or image is not.
- **Superscripts** are written ``10^6^``, ``mc^2^``, ``$1.2bn^3^`` (footnote marks included),
  never fused into the number. Raised ordinals and marks stay inline (``1st``, ``ACME™``).
- **Footnotes.** A footnote at the foot of a page whose number is raised becomes
  ``[^1]: ...``; every line of a multi-line footnote is kept. The mark in the body text stays a
  superscript (``^1^``), not a ``[^1]`` reference.

Words and lines
---------------

- Ligature glyphs are expanded (``ﬁ`` becomes ``fi``).
- A word broken at a line-end hyphen is joined and keeps the hyphen (``top-down``); the hyphen is
  removed only when the document spells the word without it elsewhere (``invest-`` + ``ment``
  next to ``investment``).
- CJK lines of one paragraph are joined without spaces; short stacked lines stay apart.
- Bold and italic mark only the styled words. Overprinted, fake-bold and shadowed text is emitted
  once.

Escaping
--------

PDF and Office text that looks like Markdown or HTML is escaped so it renders as printed: ``#
of patients`` becomes ``\# of patients``, a leading ``-`` or ``1.`` that is not a list marker
becomes ``\-`` / ``1\.``, a ``<`` that would open a tag becomes ``&lt;`` (``x < 5`` stays),
and the ``&`` of an entity-like sequence becomes ``&amp;``. The escaping helpers are in
``doc2mark.utils.markdown``. Table cells and OCR text have their own rules (:doc:`tables`,
:doc:`ocr`).

Reading order
-------------

- A page is read top to bottom. A ``/Rotate`` page is read as displayed, so a table follows its
  title and the running header and page number stay at the edges.
- When a page clearly shows columns (two or three, equal or unequal widths), they are read one
  after the other. Text, a picture or a drawing across the columns (a title, an abstract, a
  full-width figure) keeps its place between the columns above and below it, also when images
  are not extracted. Running headers open the page; footnotes and page numbers close it.
- A sidebar or pull-quote beside the main text (much narrower, with fewer lines) comes after the
  text it stands beside, as a whole.
- Columns need clear evidence: a vertical strip of white space between items that stand side by
  side, running text on both sides, and nothing reaching across the strip. Text set out in rows
  (a form's labels and values, terms level with their definitions, parallel texts whose
  paragraphs start level on both sides) keeps its order, as do pages that mix column layouts and
  pages with more than 400 text blocks, tables and pictures. Reordering never drops or repeats
  text.

Running headers, footers and page numbers
-----------------------------------------

A line counts as a running header or footer only on strong evidence: it sits in the top or
bottom 12 % of the page, repeats at about the same height (within 4 pt) on at least 3 pages (or
on nearby pages of a chapter), at a height that carries such lines on more than half of the
pages, and is set apart from the content by a clear gap (a bare page number needs none); a table
header row is never one. A PDF of one or two pages keeps every line. Then:

- **Bare page numbers are removed from every page**: ``7``, ``Page 3 of 12``, ``3/12``,
  ``- 3 -``, ``p. 3``, roman numerals, ``第 3 頁``, ``Seite 3 von 12``, and a number printed alone
  that counts with the pages (``1001``, ``1002``, ...).
- **A line that repeats a title or heading of the document is removed from every page.**
- **Any other running header or footer keeps its first copy**, as plain text (or classified
  like other content when it is heading-sized): a statement title, a unit note, a disclaimer, a
  letterhead, ``ACME | Page 3``, a per-chapter header. Its later copies are left out of
  ``content`` and of the chunks; they are in ``json_content`` as ``text:header`` /
  ``text:footer`` items.
- **Anything with weaker evidence keeps every copy**: a line repeated on only two pages, a line
  attached to the content, a number labelled like the pages but without a page word
  (``3 | ACME Corp``, ``Lesson · 3``), a date such as ``15/03``.

A picture repeated at the same place on most pages (a logo) is kept once in the same way when it
is OCR'd. The optional :doc:`judge` can thin repeated lines the rule keeps on every page, never
below one copy.

Hidden and duplicate text
-------------------------

Invisible text (render mode 3, or fully transparent) over nothing the page shows is not output:
it is a known way to inject instructions into RAG pipelines. The pages and character counts are
in ``metadata.extra["hidden_text"]`` and a warning names them. An invisible copy of painted text
is dropped too, while the invisible OCR layer of a scan (over a picture that shows something) or
over vector-outlined glyphs is kept as the page's text, unless that picture was OCR'd, so each
source is emitted once. A searchable scan yields one text: the OCR of its render with OCR on,
its invisible layer without.

A garbled text layer (undecodable glyphs, private-use characters, mojibake) is kept as extracted
without OCR and reported in ``metadata.extra["text_layer_quality"]``; with OCR on the page is
OCR'd from its render (:doc:`ocr_policy`).

PyMuPDF output
--------------

From 1.26.7 PyMuPDF prints a recommendation of its ``pymupdf_layout`` package to stdout the first
time tables are searched, and MuPDF prints errors about damaged files (``MuPDF error: library
error: zlib error: ...``) to stdout. The ``doc2mark`` CLI switches the recommendation off and
logs MuPDF's messages on stderr, so its stdout holds only the document. The library leaves
PyMuPDF's settings alone: a program that writes to stdout itself can call
``pymupdf.no_recommend_layout()`` and ``pymupdf.set_messages(...)``. With a PyMuPDF older than
1.27.1 forced into the environment, each missing capability is logged once and a fallback is
used. If the PDF pipeline itself cannot be imported (a broken installation), a basic PyMuPDF
text extraction runs instead, with a warning: ``### Page N`` sections, no tables, no
``json_content``.

Example
-------

.. code-block:: python

   from doc2mark import load

   result = load("report.pdf")
   print(result.content[:300])
   for item in result.json_content:
       if item["type"] in ("text:title", "text:section"):
           print(item["level"], item["content"])
