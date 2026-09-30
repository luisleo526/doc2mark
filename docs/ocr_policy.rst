When pages and pictures are OCR'd
=================================

doc2mark decides *how* to OCR a document from the document's **content**, not
from its file extension. The same content-based routing applies to PDFs and to
Office files, and it runs automatically: there are no routing flags to set. You
turn OCR on (``ocr_images=True``; it implies ``extract_images=True``) and
doc2mark chooses the cheapest correct path for every page and every embedded
image.

The guiding principle
---------------------

Text, data, and tables take the **deterministic path** -- they are read directly
from the document's structure (selectable text, ruled tables) and emitted
*verbatim*. This path is exact, free (no model calls), and lossless: it is the
authoritative source for the BM42 sparse-retrieval index, so every printed token
is preserved character-for-character.

Only a **true image page** -- a slide or scan whose content is baked into pixels
with no usable text layer -- is OCR'd as a whole page. An LLM provider is asked
to transcribe that page verbatim and *also* synthesize a clean Markdown
re-layout, but it is never allowed to drop real printed values. A text layer
that exists but cannot be trusted -- undecodable glyphs, an invisible scanner-OCR
layer, content drawn as vector outlines -- sends *that page* to OCR as well. On
the other pages, pictures that carry something to read are OCR'd one by one.

The policy is layered. Each layer narrows the decision:

#. **Document strategy and page routes** -- one ``"image"`` vs ``"text"``
   decision per document, which every page follows unless its own signals
   clearly disagree.
#. **Office image route** -- image-dominant ``.docx``/``.pptx`` borrow the PDF
   image strategy; text/table Office docs stay native.
#. **Per-image job-router** -- when a single image is OCR'd, the model
   classifies it and applies a per-type transcription policy.
#. **page_markdown synthesis + coverage guard** -- for image pages a structured
   Markdown rendering becomes the display body *only* when it provably covers
   the verbatim text.

The shared thresholds
~~~~~~~~~~~~~~~~~~~~~~

Both the document strategy and the Office route call one function,
``doc2mark.core.strategy.decide_doc_strategy``, and the PDF page routes come from
``decide_page_route`` in the same module: it is the single source of truth for
what the signals mean, the thresholds and the decisions, so the PDF and Office
paths never diverge. The pipelines only measure.

.. code-block:: python

   from doc2mark.core.strategy import decide_doc_strategy

   # decide_doc_strategy(mean_image_coverage, mean_text_chars_per_page,
   #                     text_illegibility=0.0)
   decide_doc_strategy(0.92, 35)         # -> "image"  (mostly pictures, no text layer)
   decide_doc_strategy(0.70, 900)        # -> "text"   (large figures, but real text)
   decide_doc_strategy(0.10, 1200)       # -> "text"   (ordinary text document)
   decide_doc_strategy(0.95, 900, 0.5)   # -> "image"  (pictures, half the text pages garbled)

The module constants are the only knobs, and they are deliberately not
user-facing:

.. list-table::
   :header-rows: 1
   :widths: 32 14 54

   * - Constant
     - Value
     - Meaning
   * - ``IMAGE_PAGE_COVERAGE``
     - ``0.55``
     - Minimum mean fraction of page area covered by raster images for the
       ``"image"`` strategy.
   * - ``IMAGE_PAGE_TEXT_LIMIT``
     - ``200``
     - The mean legible text per page (see ``text_weight`` below) must be
       **below** this for the ``"image"`` strategy.
   * - ``IDEOGRAPH_WEIGHT`` / ``SYLLABLE_WEIGHT``
     - ``3.0`` / ``2.0``
     - What one CJK ideograph / one kana or hangul syllable counts in the text
       density, in Latin-character equivalents.
   * - ``ILLEGIBLE_TEXT_RATIO``
     - ``0.3``
     - ``decide_doc_strategy(..., text_illegibility)``: share of garbled text
       pages at or above which an image-dominant document routes ``"image"``.
       Kept for API compatibility: no doc2mark route passes it any more (the PDF
       route gates every page on its own, the Office route measures no text
       quality).
   * - ``GARBAGE_TEXT_RATIO`` / ``MIN_GARBAGE_GLYPHS``
     - ``0.1`` / ``3``
     - A page's text layer is garbled when at least 3 undecodable glyphs make up
       at least 10 % of its prominence-weighted text (see *Text-layer quality
       gate*).
   * - ``PAGE_OVERRIDE_MARGIN``
     - ``0.5``
     - How far a page's own coverage and text must clear the thresholds before it
       overrides its document's route (see *Per-page routes*).
   * - ``LEGIBILITY_JUDGE_THRESHOLD``
     - ``0.7``
     - An optional legibility judge's probability below which a page counts as
       garbled.

The document rule (the PDF route)::

   "image"  iff  mean_image_coverage >= 0.55  AND  mean_legible_text < 200
   "text"   otherwise

Garbled text layers do not move the document route: every page is checked on
its own and a garbled page is OCR'd alone, so it never takes clean pages with it.

Text density is the decisive signal. Coverage alone would misclassify a normal
text document that merely carries a few large figures; requiring low text
density as well keeps such documents on the deterministic ``"text"`` path.

Text density is script-aware: ``text_weight`` counts only non-whitespace,
legible characters (undecodable glyphs do not count), and a CJK ideograph counts
three (a kana or hangul syllable two), because one such character carries about
as much content as three (two) Latin letters, so a Chinese deck and its English
version measure about the same. The page thresholds below (50, 100, 300) are in
the same units.

Layer 1 -- the document strategy and page routes
-------------------------------------------------

For a PDF, ``PDFLoader`` measures every page once
(``doc2mark.pipelines.pdf_routing``):

- **image coverage** -- the share of the visible page covered by raster images:
  the *union* of the image placements, clipped to the page (CropBox), inline
  (``BI``/``ID``/``EI``) images included. Parts of a picture outside the page
  (CropBox) and a picture placed twice or stacked on another are not counted.
  Clip paths inside the page are not considered.
- **legible text** -- ``text_weight`` of the *painted* text. Invisible text
  (render mode 3, fully transparent) is kept apart (see *Invisible text*).
- **text-layer quality** -- whether the text layer is garbled (see *Text-layer
  quality gate*).
- **uncaptured content** -- on a page with (almost) no usable text that is not a
  searchable scan: the share of the page covered by pictures the text route does
  not OCR one by one (inline images, picture tiles too small to count as
  figures), and the share showing other ink, any colour (vector-outlined text),
  on a 72 DPI render. Line art is not content by itself and does not count:
  horizontal and vertical strokes, rectangle outlines and fills thinner than
  2 pt (rules, frames, table grids).

``_document_image_strategy`` feeds the page means to ``decide_doc_strategy``,
caches the result and logs it, e.g.::

   📑 Document OCR strategy: image (mean coverage 0.94, mean legible text 12/page, garbled text pages 0%)

(the garbled-page share is reported, not used).

Per-page routes
~~~~~~~~~~~~~~~

With OCR on, the document route is every page's default, and a page overrides
it only when its own signals clearly disagree (``decide_page_route``; the first
matching rule wins):

.. list-table::
   :header-rows: 1
   :widths: 26 20 54

   * - Reason
     - Page route
     - When
   * - ``searchable_scan``
     - ``image``
     - An invisible OCR layer lying over raster images that cover the page
       (Acrobat "searchable image", ocrmypdf, scanner software), with little
       painted text. The render is OCR'd and the invisible layer dropped: one
       source per page, never both.
   * - ``illegible_text_layer``
     - ``image``
     - The text layer is garbled (detector or legibility judge).
   * - ``no_text_layer``
     - ``image``
     - Less than 50 legible characters, but pictures the text route cannot OCR
       cover at least 5 % of the page (scans stored as inline images or tiles),
       or other ink covers at least 0.1 % of it (text drawn as vector outlines;
       rules, frames and table grids do not count, so a blank page with a
       header rule and a page number keeps its text path).
   * - ``image_dominant_page``
     - ``image``
     - In a ``"text"`` document: pictures cover at least 0.825 of the page
       (0.55 x 1.5) and it has less than 100 legible characters (200 x 0.5) --
       a scanned page in a text report, including scans cut into tiles.
   * - ``dense_text_page``
     - ``text``
     - In an ``"image"`` document: pictures cover less than 0.275 of the page
       (0.55 x 0.5) and it has at least 300 legible characters (200 x 1.5) -- a
       text appendix in a slide deck keeps its verbatim text layer.

A page overridden to render OCR as a searchable scan, a page without a usable
text layer, a page with a garbled text layer or a scanned page may still carry
real, legible text (a caption, a heading, a stamp, the clean body under a garbled
title). Whatever legible line of it the OCR did not reproduce, and the page
visibly shows, is kept verbatim after the OCR text, so an override never loses
real text; garbled lines are not kept (the OCR read them from the render). On a
garbled page only the garbage detector can vouch for a line, so nothing is kept
when the legibility judge alone found the layer garbled, or when the OCR text
holds words no line of the layer accounts for, as many as half the words to
keep: the OCR then read those lines differently, and their text layer is wrong,
not missed.

The margins are hysteresis: a page near a threshold follows its document, so a
deck or a report keeps one consistent treatment, and only clear outliers switch.
Running headers, footers and page numbers the pipeline removed are not re-added
to an overridden page.

Without OCR there is nothing to route: every page takes the text path.

The ``"image"`` route
~~~~~~~~~~~~~~~~~~~~~

The page is rasterized to a single PNG (at 150 DPI) and OCR'd as one whole-page
image. The whole-page transcription **is** the page's content: a sparse text
layer on such a page is chrome (a logo, footer, or page number) that the
whole-page OCR already captures, an invisible scanner-OCR layer, or a garbled
layer, so the deterministic text layer is *not* also emitted -- emitting it
would duplicate tokens or add garbage.

If the render's OCR answers with nothing (a blank page, a refusal), the page
falls back to its own text layer instead of disappearing (with a warning when
that layer has text). A page that shows content (ink on its render) also carries
the marker ``[page N: OCR returned no content]``, so a page the OCR could not
read never vanishes silently; a blank page needs no marker. Such pages are
listed in ``metadata.extra["ocr_images"]["unread_pages"]``. If the render's OCR
*failed* (no answer: a timeout, a server error), the page takes the text route
instead: its text layer is emitted and its pictures show ``[image: OCR
unavailable]``; the failure is counted in ``ocr_images["failed"]`` and
``ocr_issues["failed"]``.

These whole-page renders also request ``page_markdown`` synthesis (Layer 4).

Renders are streamed: pages are rendered and sent to the provider in batches of
at most 32 images (or twice the provider's ``max_concurrency`` when that is
higher), a batch is sent early once 128 MiB of image data are pending, and each
batch's images are released once it is answered, so memory does not grow with
the page count (the E2E suite checks that a 160-page scan stays under 768 MB).
Renders and pictures go in separate batches. The output keeps page order.
Identical renders (blank pages, repeated slides) are one request, except with
neighbour-page context (``context_pages`` 1 or more), where each render carries
its own context.

The ``"text"`` route
~~~~~~~~~~~~~~~~~~~~

The deterministic rule-based layer is authoritative:

- **Tables** are detected with PyMuPDF's table finder and rendered with
  doc2mark's table renderer (including merged-cell handling), so cell text stays
  exact.
- **Text** is extracted block-by-block and classified (title / section /
  list / caption / footnote / header / footer) from font-size, weight, and
  layout heuristics -- preserved verbatim for the BM42 RAG flow.
- **Pictures** are OCR'd individually, each once, when they carry content (see
  *Pictures on the text route*).

Pictures on the text route
~~~~~~~~~~~~~~~~~~~~~~~~~~

``doc2mark.pipelines.pdf_images`` decides what the text route sends to OCR. Every
rule looks at what the page shows, never at the file's structure alone:

- **Placements.** An image counts once per place the page draws it
  (``get_image_info``): an image listed twice in the page's resources (directly
  and through a Form XObject) is drawn once and counts once.
- **Tiles.** Placements that abut edge to edge (at most 1 pt apart, overlapping by
  at most 5 % of the smaller one, alongside each other for at least half the
  shorter side) are tiles of one picture, like a scan cut into strips or a grid,
  or a figure a printer driver wrote as thin bands. Their region is rendered at
  the resolution the tiles carry (150 to 300 DPI), without the text painted over
  it (the text layer already emits that text), and OCR'd as one picture, so words
  are not cut at tile edges. An inline image (``BI``/``ID``/``EI``, it has no
  xref) is rendered the same way.
- **Shown.** What a picture shows is measured with its clip path and the visible
  page (CropBox) applied: MuPDF's text device reports each image's box cut by the
  clips around it. A picture the page shows whole always shows, however thin (a
  line of text kept as an image). A picture shown only in part (cropped by a clip
  path, or running off the page) shows when the part is at least 12 pt on both
  sides, and is OCR'd as the page shows it: the visible part is rendered like a
  region, so the text the crop hides is not read. A picture off the page, clipped
  away, or showing a sliver is left out, as is an image of fewer than 12 pixels a
  side. These rules judge whole pictures, after tiles are joined.
- **Content.** A picture is judged on a grey copy of at most 1024 pixels a side:

  * *plain* -- fewer than 24 edge pixels (neighbours at least 16 grey levels
    apart) once rows and columns that are at least 80 % edge (rules, frame lines)
    are left out: a flat colour, a gradient, a blank area or a frame. Never
    OCR'd: a themed slide background or a letterhead tint has nothing to read,
    and a language model would only describe it. The contrast is low on purpose,
    so a faint scan still counts as having detail.
  * *text* -- ink (32 grey levels off the picture's most common grey) broken into
    strokes the way glyphs are: at least 6 ink/background changes along the
    average inked pixel row. Printed words and numbers, charts with printed
    values, and photos read this way. Always OCR'd.
  * *shapes* -- anything else: an icon, a logo mark without letters.

  A *small* picture (under 10 % of the page in both directions, compared in the
  page's unrotated frame, or under 48 pt on both sides) is OCR'd only when it
  reads as text; a larger one unless it is plain. A picture whose pixels cannot
  be decoded is OCR'd (when unsure, keep). A 130 x 75 pt chart with printed
  numbers on a 1440 x 810 pt slide is therefore OCR'd, a 40 pt icon is not. The
  check samples a copy of the pixels (a stencil mask by its coverage, a
  transparent image with its soft mask applied); what OCR reads is the picture at
  its full resolution.
- **One request per content.** An image shown on many pages or at several places
  is sent to OCR once (the same pixels under another xref too), and its text is
  emitted once per place the page shows it, at that place. With neighbour-page
  context for embedded images (``context_pages=2``) the answer depends on the
  page, so the request is made once per page.

A picture whose OCR failed (the provider flags the answer ``failed``: a timeout,
a rate limit, a server error) or is missing (a failed batch) leaves the
placeholder ``[image: OCR unavailable]`` at each place it shows; one whose OCR
returned no text leaves nothing. What was sent is reported in
``metadata.extra["ocr_images"]``: ``ocr_requests`` (images sent to the
provider), ``page_renders``, ``batches`` and ``largest_batch``, ``empty`` and
``failed`` (requests answered with no text, or not answered), ``skipped``
(placements not OCR'd because the page does not show them, ``not_shown``, or
they carry nothing to read, ``no_content``) and ``unread_pages``.

On a page without a usable text layer the pictures are all the page shows, so
such a page never emits nothing: when every picture on it reads as *shapes*,
they are OCR'd anyway.

A picture repeated as page furniture -- the same OCR text at (nearly) the same
place, every edge within 4 pt, on at least 3 pages and on more than half of the
document's pages, like a letterhead logo or a slide template's wordmark -- is
kept once, like the first copy of a running header: the later copies are typed
``text:header`` / ``text:footer`` (kept in the JSON, left out of the Markdown). A
picture shown on fewer pages, or at moving places, stays everywhere.

Routing counts a page's pictures the same way: for *uncaptured content* (see
*Per-page routes*), the pictures the text route reads one by one whatever their
pixels are the image XObjects the page shows whole that are not small. Small
pictures (read only when they look like text), tiles, inline images and cropped
pictures are not, so a page without a text layer that shows them -- a catalogue
sheet of labelled thumbnails, a tiled or inline-image scan -- is OCR'd from its
render, one request for the whole page.

What is cached: an OCR answer is cached (``ocr_cache``) whatever it says,
including an answer with no text and a refusal or "no readable text" statement
-- a blank page or a photo without words gets that answer every time, and
asking again would cost a call (with the LLM providers two: the structured call
and the free-form recovery behind it) on every run. Only a failed answer, a
result that still withholds values after the router firewall's redo, and an
answer the non-content judge could not screen are asked again on the next run,
never replayed; an entry of such a kind already in a cache is a miss. A provider's own refusal or safety block (OpenAI's refusal
field, a Gemini SAFETY or RECITATION block) may not last: it is replayed for
``refusal_ttl_seconds`` only (10 minutes by default; a hit does not extend it).
A converted document is not written to ``cache_dir`` when its OCR failed
somewhere (``failed`` requests, ``ocr_issues["failed"]``), left a page showing
content unread (``unread_pages``), or holds a provider's own refusal or block
(``ocr_issues["provider_refused"]``), or, with a judge, when the judge could not
answer every question; answers with no text and "no readable text" statements do
not keep it out. A skipped cache write is logged at INFO with the reason.

Text-layer quality gate
~~~~~~~~~~~~~~~~~~~~~~~

Every page's text layer is checked, whatever its image coverage. Designed and
print PDFs often draw text with subset fonts that have no, or a broken,
ToUnicode map: the page renders correctly, but the extracted text is garbage.
The deterministic detector (``text_layer_stats``) counts as garbage glyphs:

- U+FFFD replacement characters and ``(cid:N)`` placeholders;
- control codes (raw character IDs);
- runs of private-use characters, except icons, which count as neither text nor
  garbage: a lone private-use glyph (a bullet), the private-use glyphs of icon
  and symbol font families (Font Awesome, Wingdings, Webdings, Zapf Dingbats,
  Symbol, Material Icons and the like, recognised by the start of the font
  name), and a row of up to 5 of one repeated glyph on a page that otherwise
  reads as text (a star rating). Adobe's private-use letters and figures
  (U+F6BE-U+F7FF: old-style digits, small capitals) are text a font failed to
  map, and always count as garbage;
- mojibake sequences (UTF-8 read as Latin-1 or cp1252), in a layer that has a
  typical one -- a Latin-1 letter read back as two characters (``Ã©``) or
  typographic punctuation (``â€™``) -- or at least two distinct ones: mojibake
  mangles every accented letter of a layer, while a lone match is ordinary
  punctuation after an accented letter (``fermé…”`` reads as one).

Each character is weighted by its prominence, ``(font size / the page's body
size)`` squared, capped at 4 squared: an unreadable 38 pt title weighs as much as
the body lines it visually outweighs, while one decorative glyph does not tip a
page. A page is **garbled** when at least ``MIN_GARBAGE_GLYPHS`` (3) garbage
glyphs make up at least ``GARBAGE_TEXT_RATIO`` (10 %) of its weighted text.

- With OCR on (a provider and ``ocr_images=True``), a garbled page is OCR'd from
  its render (``illegible_text_layer``).
- Without OCR, the text is kept as extracted (there is nothing better), the page
  is listed in ``metadata.extra["text_layer_quality"]`` with ``"action":
  "kept"`` and a warning names it.

Garbled pages never decide for the document, neither the worst page nor a share
of them: a brochure whose cover title (or cover and one more page) is unreadable
keeps its other pages on the text path, and only those pages are OCR'd.
``test-table.pdf`` in the sample
documents is such a page: its title "Technical Specifications" extracts as
U+FFFD, its two pictures lie almost entirely off the page (coverage 0.15), and
the quality gate, not the coverage, sends it to OCR.

The legibility judge
^^^^^^^^^^^^^^^^^^^^

Some broken text layers are valid Unicode -- letters shifted or substituted by a
wrong ToUnicode map, a bad invisible OCR layer -- and no character rule can see
them. For those, pass an optional judge:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   def judge(page_text: str) -> float | None:
       ...  # probability (0..1) that page_text is legible, or None

   loader = UnifiedDocumentLoader(ocr_provider="openai", legibility_judge=judge)

The contract (``doc2mark.core.strategy.judge_text_layer``):

- ``page_text`` is one page's text layer as extracted, lines joined with
  ``"\n"``: the painted text, or the invisible OCR layer of a searchable scan.
- The judge returns the probability that the text is legible content a person
  could read (prose, tables, code, identifiers, any script), or ``None`` when it
  cannot judge.
- It is consulted only with OCR on (an OCR provider and ``ocr_images=True``):
  its verdict can only send a page to OCR, so without OCR it is never called.
  It is asked at most once per page, only for layers of at least 20 characters
  that the deterministic detector did not already flag, and only for pages that
  would keep their text layer.
- Below ``LEGIBILITY_JUDGE_THRESHOLD`` (0.7) the page is treated as garbled.
  ``None``, an exception or a value outside 0..1 mean "cannot judge": the text
  is kept, exactly as without a judge.

Without a judge the deterministic gate alone decides, conservatively: when it
cannot tell, the text is kept.

Invisible text
~~~~~~~~~~~~~~

Text drawn in render mode 3 (or fully transparent) is not shown on the page.
Each invisible span is classified by what the page shows under it, with or
without OCR:

- **A copy of the painted text it lies on**, on the same line (the invisible
  duplicate some exporters and OCR tools add), is dropped; the painted text
  stays.
- **Over a picture** it is the picture's text -- a scanner's OCR layer, or the
  transparent copy a slide export keeps of text it baked into the artwork --
  unless the picture is blank under it: fewer than 1 % of the span's pixels differ
  from the region's background by more than 12 grey levels, on a 100 DPI render
  of the page with its text removed. A watermark or stamp painted over a scan
  therefore does not hide the scan, and faint or low-contrast scans still count.
- **Over other ink** it is the text of glyph-like ink (text drawn as vector
  outlines): ink spread over the span and broken into strokes along its rows.
  Rules, table grids, the edges of filled boxes and bands, and chart lines
  crossing the span do not count.
- **Over nothing but painted text, or over nothing**, it is hidden text -- a
  known prompt-injection vector in RAG.

In doubt the text is kept: when the page cannot be checked (a render or a copy
fails), all of its invisible text but the copies of painted text is kept and a
warning is logged, because losing a scan's only text is worse than emitting a
hidden line.

Text of what the page shows is emitted, except over a picture whose OCR in this
run returned text: that OCR replaces it, so each picture gives one source, not
two, while the invisible text over other pictures and over outlines on the same
page stays. A page that is mostly such a layer over a page-covering scan is a
*searchable scan* and is OCR'd from its render.

Hidden text is left out of paragraphs and of table cells (when a table meets it,
the table finder reads a copy of the page with that text removed); the pages are
listed in ``metadata.extra["hidden_text"]`` and a warning names them.

What this cannot catch: invisible text laid over a region of a picture that
shows something (a photo, scanned text) or over outlined glyphs (or other ink
broken into strokes, such as a dense hatching) looks exactly like an OCR layer
and is kept as the page's text. With PyMuPDF older than 1.27.1,
which cannot remove only the invisible glyphs where they touch painted text,
hidden text touching painted text inside a table can reach that table's cells
(paragraphs are not affected).

What the output records
~~~~~~~~~~~~~~~~~~~~~~~

PDF results carry the routing facts in ``metadata.extra`` (the ``json`` output
includes them):

- ``ocr_routing`` (OCR on): ``{"document_route": "text", "overrides": [{"page":
  1, "route": "image", "reason": "illegible_text_layer"}]}`` -- only the pages
  whose route differs from the document's are listed.
- ``text_layer_quality``: one entry per garbled page, ``{"page", "legible":
  false, "garbage_ratio", "garbage_glyphs", "judge_legibility", "action": "ocr" |
  "kept"}``.
- ``hidden_text``: pages whose hidden text was left out, with its length
  (invisible duplicates of painted text are dropped without being counted).

A document that yields no text at all never does so silently: a warning says so.
When some text was extracted, a warning names the pages that need OCR (without
OCR: scans, vector outlines) or that show content but produced no text (with
OCR).

Converted documents cached by ``UnifiedDocumentLoader(cache_dir=...)`` are keyed
by the legibility judge and by ``strategy.ROUTING_VERSION``, so a new judge or a
routing change is never answered from an older result.

Layer 2 -- the Office image route
---------------------------------

Office documents reach the *same* content-based decision, without a separate
heuristic. ``OfficeProcessor._maybe_route_image_dominant`` runs before native
extraction and is gated tightly:

- Only ``.docx`` and ``.pptx`` are eligible. An ``.xlsx`` never routes -- a
  spreadsheet is a data grid, always read natively.
- OCR must be requested (``ocr_images=True``; the loader turns
  ``extract_images`` on for it) and an OCR provider must be configured.

``_is_image_dominant`` then computes the two signals straight from the OOXML
structure -- no rendering required -- and calls the same
``decide_doc_strategy`` (the OOXML text count is a plain character count):

- **PPTX** (``_pptx_image_signals``): per slide, the union of every visible
  picture (picture shapes, grouped and placeholder pictures, picture fills, the
  slide's, layout's or master's background picture, layout and master
  pictures; hidden shapes excluded) and the text of text frames and table
  cells; then the means over the slides.
- **DOCX** (``_docx_image_signals``): the total extent of inline and floating
  pictures (body, headers, footers, text boxes) against one page, and the total
  rendered text (body, tables, content controls, text boxes, headers and
  footers; deleted revisions excluded).

These OOXML signals only pre-filter. When they say ``"image"``,
``_process_as_image_dominant`` converts the file to PDF via LibreOffice, and
the converted PDF's own document route decides: only when it is ``"image"`` is
the PDF run through the PDF pipeline (Layer 1: whole-page render OCR +
``page_markdown`` synthesis), with the original Office identity restored in the
metadata and ``metadata.extra['routed_via'] = 'pdf'``. The route never raises:
when the converted PDF routes as text, cannot be measured, or the conversion
fails (no LibreOffice on the host), the file is read natively and
``metadata.extra`` records ``routed_via: "native"`` with ``route_reason`` (for
example ``"converted PDF routes text"``) or ``route_error``. Files the OOXML
check passes over get no key.

Layer 3 -- the per-image job-router (``task="auto"``)
-----------------------------------------------------

When an individual image is OCR'd -- a whole-page render, or an embedded figure
on a ``"text"`` page -- the default task is ``auto``. The ``auto`` prompt is a
self-routing job-router: the model first **classifies** the image into exactly
one ``document_type``, then applies that type's transcription policy in the same
response, recording the type in
:attr:`Interpretation.document_type <doc2mark.ocr.schema.Interpretation>`.

The master rule overrides every policy below it: **transcribe every legible
printed character verbatim, in the original language.** Exactly one type --
``screenshot`` -- may omit printed values, and only when *all three* gates hold:

#. the image is a product / app / dashboard UI with toolbar, nav, tabs, or
   buttons; **and**
#. its data is clearly *illustrative* (round or sequential names, evenly spaced
   dates, repeated amounts, "Sample"/"Demo"); **and**
#. the surrounding context indicates a product, marketing, or feature
   introduction.

If any gate is missing -- or whenever the model is unsure -- it transcribes
verbatim. A dropped real table is unrecoverable, so the tie always breaks toward
verbatim.

The four policies
~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 20 62

   * - Policy
     - ``document_type``
     - Behavior
   * - **VERBATIM** (default)
     - ``document``, ``table``, ``form``, ``receipt``, ``handwriting``,
       ``code``, ``photo``, ``logo``, ``stamp``, ``mixed``, ``other``
     - Transcribe every character into ``raw.text``; tables go to
       ``raw.tables[].html`` with exact ``colspan``/``rowspan``; label/value
       pairs to ``raw.fields``. ``content_fidelity="verbatim"``.
   * - **SCREENSHOT** (triple-gated only)
     - ``screenshot``
     - Write only stable text -- screen/module name, section / nav / field /
       column **labels**, buttons, capability message. Leave each
       :class:`Table <doc2mark.ocr.schema.Table>` header-only with
       ``illustrative=true`` and a ``row_count`` of the withheld sample rows;
       put what the product *does* in ``interpretation``.
       ``content_fidelity="described"``.
   * - **DESCRIBE**
     - ``chart``, ``diagram``, ``infographic``
     - Keep **all** printed text verbatim (titles, axis / legend / node / edge
       labels, printed numbers); never invent or pixel-estimate values; put the
       trend / structure / message in ``interpretation``.
       ``content_fidelity="described"``.
   * - **SKIP**
     - ``blank``
     - Leave ``raw`` empty. ``content_fidelity="skipped"``.

A ruled grid of irregular, varied-precision, or internally consistent numbers
(subtotals that sum) is a *real* table and is transcribed as ``table``
regardless of surrounding app chrome; monospace code or a terminal is ``code``,
never ``screenshot``.

Neighbor-page context tightens the gate
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When neighbor-page PDF context is attached (``context_pages`` > 0, for any
PDF-capable model -- OpenAI and Gemini alike), the neighbors are read *only* to
judge the host document's purpose -- never transcribed. The non-verbatim
policies (``describe`` and ``screenshot``) may then be applied **only** when the
model's ``self_confidence >= 0.7`` *and* ``legibility == "high"``; otherwise it
falls back to verbatim. Without context (the default) every ``auto`` request
carries a clause that keeps it verbatim, and withholding without context counts
as a violation, so nothing is withheld.

The ``router_invariants`` firewall
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``doc2mark.ocr.schema.router_invariants(page)`` is a mechanical check (returns a
list of violation strings; empty means OK) of the policy, for CI and evaluation
over recorded structured outputs. Its withholding part
(``doc2mark.ocr.schema.withholding_violations``) also runs on every structured
OCR result at run time: a result that withholds values against the policy is
redone verbatim (one extra call), so **real printed values are never withheld
except on a high-confidence screenshot**; withheld values that remain are
marked ``[N illustrative ... not transcribed]`` and counted in
``ocr_issues["withheld"]``. Among the invariants ``router_invariants`` checks:

- Illustrative / withheld content (``illustrative=True`` on a table, field,
  metric, or figure) may appear **only** when ``document_type == "screenshot"``.
- A withholding screenshot must have ``self_confidence >= 0.7`` and
  ``legibility == "high"`` -- otherwise it should have fallen back to verbatim.
- ``content_fidelity`` of ``"described"`` / ``"caption"`` must carry meaning in
  ``interpretation.summary``; ``"skipped"`` implies an empty ``raw`` layer.
- ``interpretation.primary_date`` must be one of the verbatim strings in
  ``raw.dates`` (selected, never invented).
- Every verbatim string surfaced in a ``figure``, ``section``, typed entity, or
  relation must be a substring of ``raw.text``.

Layer 4 -- ``page_markdown`` synthesis and the coverage guard
-------------------------------------------------------------

For whole-page image renders only, doc2mark appends a synthesis instruction
asking the model to *also* fill
:attr:`Interpretation.page_markdown <doc2mark.ocr.schema.Interpretation>`: a
clean, well-structured Markdown rendering of the page (headings, lists, arrow
chains for flow diagrams) that **re-lays-out** the same text -- dropping,
paraphrasing, and translating nothing. Table regions become a short
``[see table]`` placeholder, since the authoritative HTML already lives in
``raw.tables``.

A flat OCR dump is hard to read, but a synthesized rendering risks summarizing
content away. The coverage guard in
:meth:`OCRPage.to_markdown <doc2mark.ocr.schema.OCRPage>` resolves the tension.
``page_markdown`` is used as the display body **only** when it verifiably covers
the verbatim text:

- A token-based coverage score is computed over ``raw.text`` (Latin / numeric
  words and CJK runs), against ``page_markdown`` plus the table HTML.
- If coverage ``>= 0.85`` (``_SYNTH_COVERAGE_MIN``), the synthesized Markdown
  becomes the body, the authoritative table HTML is appended, and any
  still-missing verbatim tokens are preserved in a hidden ``raw-verbatim-tail``
  HTML comment -- so BM42 keeps **every** token even if the rendering drops one.
- If coverage falls below the floor (likely paraphrase or truncation), doc2mark
  **falls back** to the standard verbatim rendering of ``raw.text`` + tables.

The result is never lossy: the structured rendering is used when it is provably
complete, and the raw verbatim dump is used otherwise.

Putting it together
-------------------

Because the routing is automatic, the only thing you do is enable OCR:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="tesseract")

   # A scan or a slide deck of pictures -> "image": whole-page render OCR.
   scan = loader.load("scan.pdf", ocr_images=True)
   print(scan.metadata.extra["ocr_routing"])

   # A text report -> "text": its text and tables verbatim, pictures that carry text OCR'd, and,
   # page by page, a scanned appendix, a garbled title page or an outlined flyer OCR'd from its render.
   report = loader.load("report.pdf", ocr_images=True)
   print(report.metadata.extra["ocr_routing"])

   # The same decision for Office: an image-dominant .pptx takes the PDF image route,
   # an ordinary .docx stays native (no "routed_via" key: the route was not tried).
   docx = loader.load("report.docx", ocr_images=True)
   print(docx.metadata.extra.get("routed_via"))

See :doc:`/ocr` for the OCR facade, providers, tasks, and the structured-output
schema, and :doc:`/api/schema` for the full model reference.
