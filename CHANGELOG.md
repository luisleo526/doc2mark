# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Behaviour changes

What existing users will notice compared with 0.6.1, in their output, their install and their CLI
runs. The groups below give the details and the reasons. Only the judge add-on is opt-in; everything
else applies by default.

**Install, API and CLI**

- **pandas is no longer installed, numpy is a declared dependency.** `pip install doc2mark` no
  longer pulls in pandas (nothing in doc2mark imports it), so code that relied on that must depend on
  it directly; `XlsxLoader.df_sheets` is gone. `numpy>=1.21.0` is now listed (it used to arrive through
  pandas). (#14, #18)
- **`ocr_images=True` implies `extract_images=True` for every format** when an OCR provider is
  configured, not only in the CLI. (#18)
- **Tesseract failures are visible.** A Tesseract that cannot run (missing binary or language data,
  bad `TESSDATA_PREFIX`) makes the CLI exit non-zero with the cause on stderr, and `load()` raise
  `ProcessingError` (cause `OCREngineError`), as soon as the document has something to OCR. Before, the
  run exited 0 with a placeholder (`[image: OCR unavailable]`, `OCR failed`) or with the pictures
  silently missing. Directory runs with `--skip-errors` list the file as failed and go on (and exit 0).
  An `--ocr-lang` that is not installed is rejected the same way, and `--ocr-lang deu`, `chi_tra` and
  `eng+chi_tra` now take effect (they silently meant English before). (#19)
- **New optional `doc2mark[typesafe]` extra and `--judge {none,typesafe}` flag** (also
  `UnifiedDocumentLoader(judge="typesafe")` and `DOC2MARK_JUDGE=typesafe`). Off by default: nothing is
  sent anywhere unless it is enabled. `doc2mark[all]` now includes the extra. (#22)
- **`D2M_E2E_*` environment variables for the test runner** (`scripts/run_e2e_docker.sh`, developers
  only): `D2M_E2E_STRICT=1` (set by the runner) makes a missing Tesseract or LibreOffice fail an E2E
  test instead of skipping it, `D2M_E2E_PASS_ENV` names the host variables forwarded into the
  container, and `D2M_E2E_EXTRAS` picks the extras installed there (default `ocr,dev`). Separately,
  `D2M_REQUIRE_TYPESAFE=1` makes the `requires_typesafe` tests fail instead of skip when the extra or
  the key is missing. The runner exits 90 when it could not set the run up. (#13, #22)

**Word, Excel and PowerPoint**

- **XLSX shows each cell as the spreadsheet displays it, rounded the way it displays it:** `10` not
  `10.0`, `25%` not `0.25`, `$1,234.50`, and a date in the cell's own date format (`2026-03-31` for
  `yyyy-mm-dd`, not `2026-03-31 00:00:00`; Excel's built-in short date comes out as `mm-dd-yy`,
  `03-31-26`). That is the displayed value, not the stored precision: `#,##0` shows 1234.5678 as
  `1,235`, `0%` shows 0.12345 as `12%`, `0.00` shows 2.675 as `2.68` (rounded half-up, as Excel does for
  display); `General` keeps up to 15 significant digits (every digit of an integer). A negative number
  keeps its minus, even where Excel shows the sign only by colour (`#,##0.00;[Red]#,##0.00` shows
  -1234.5 as `-1,234.50`); the exception is a negative (or condition-selected) section that prints a
  bare or backslash-escaped `-` or `(` itself, while quoted text counts as a label, not a sign.
  Condition sections such as `[>=100]` are honoured, and a formula saved without a cached value shows
  the formula (`=A2+B2`). (#14)
- **XLSX layout:** sheets without merged cells come out as plain Markdown tables (no invented
  `rowspan`/`colspan`), empty rows and columns are gone, and a single-cell row above the table becomes a
  paragraph only when it (or a row between it and the table) is merged across the table or a blank row
  separates it. With image extraction on, a picture in a table is marked `[Image]` or `[Image: OCR
  text]` where it sits, and without OCR an anchored picture is also returned after the table; `#VALUE!`
  no longer stands in for a picture. (#14)
- **DOCX** gains list markers (`1.`, `-`, `(1)`, `壹、`, nested), deeper heading levels and text that was
  missing (content controls, tracked insertions, fields, nested tables). A paragraph set to outline
  level "body text" (on the paragraph or on its style, such as Word's `TOC Heading`) is not a heading
  whatever its style is called, while Title and Subtitle keep `#` and `##`. **PPTX** soft line breaks
  are line breaks (`<br>` in table cells), and with image extraction a slide's own background picture
  and pictures in content placeholders appear. In every Office format a picture in which OCR finds no
  text no longer yields an empty description item. (#14)
- **Office image route:** PPTX decks built from background pictures, grouped pictures or picture
  placeholders, and DOCX files made up of floating pictures or of pictures in headers and footers, now
  take the image route when OCR is on; forms and text in tables, groups and text boxes stay native (DOCX
  text-box text is then not extracted). `metadata.extra["routed_via"]` (already `pdf` in 0.6.1) is now
  also `native`, with `route_reason` or `route_error`, whenever the route was tried and the file stayed
  native (files the OOXML check passes over get no key). Each LibreOffice conversion now uses its own
  profile, which adds a fraction of a second per conversion (about 0.7 s where it was measured) and
  makes conversions safe to run in parallel. (#14)

**PDF text, structure and reading order**

- **Running headers and footers are verbatim first.** A line counts as a running header or footer only
  on strong evidence: it sits in the top or bottom band of the page and repeats at about the same height
  on at least 3 pages (or per chapter), at a height that carries such lines on more than half of the
  pages; it is set apart from the content by a clear gap (bare page numbers need none) and is not a
  table header row. Anything weaker keeps every copy. Of the lines that qualify, only bare page numbers
  (now in many more forms: `Page N of M`, `N/M`, `- N -`, roman numerals, `第 N 頁`, `Seite N von M`, ...,
  and a number printed alone that counts with the pages, such as `1001`, `1002`) and lines that repeat a
  title or heading of the document (heading-sized text printed on or before their first copy; a mid-page
  heading counts only on the first copy's page and the two before it) leave the Markdown on every page.
  Any other one (a statement title, a unit note, a disclaimer, a letterhead, `ACME | Page N`, a
  per-chapter header) keeps its **first copy**, as plain text (or classified like other content when
  heading-sized), instead of vanishing; its later copies stay out of the Markdown (they are
  `text:header` / `text:footer` items in the JSON: one raw line each, or the whole table for a repeated
  table). A per-page number labelled without a page word (`3 | ACME Corp`, `Lesson · 3`) stays on every
  page. Documents of exactly 3 pages are now analysed too. (#17)
- **Text next to tables is kept:** a caption or note printed against a table, or a title beside a logo
  box, no longer disappears with the table; rotated (`/Rotate`) pages no longer print table text twice
  or lose text (with a CropBox inherited from `/Pages`, table text can still print twice); overprinted,
  fake-bold and shadowed text is emitted once. (#17)
- **Heading levels** are ranked by font size and outline depth without skipped levels, are one line and
  carry no bold markup. In the text layer `#` (the title) appears at most once: the largest heading of
  the first page with text, when no other heading in the document is as large (an OCR'd page can still
  carry its own page title). More lines become headings (bold body-size lines, Word 2013+ headings,
  `第一條`-style CJK headings) and far fewer become captions: body paragraphs next to tables and images
  are no longer italic. (#16)
- **Superscripts are written `^x^`** (`10^6^`, `mc^2^`, `$1.2bn^3^`, footnote marks included); raised
  ordinals (`1st`) and `™`/`®` stay inline. (#16)
- **Escapes appear in the Markdown source** where PDF or Office text looks like Markdown or HTML
  (`\#`, `\-`, `1\.`, `\>`, `&lt;` for a `<` that would open a tag, `&amp;` for the `&` of an
  entity-like sequence); they render as written. Table cells and OCR text follow their own escaping
  rules, described below. (#15, #16, #19)
- **Lists and text:** bullets are `- ` (`*` and `+` keep their character; check-mark, arrow and dash bullets
  keep their glyph, `- ✓ ...`), lettered items are `- a) ...`, nested items are indented, and a lead-in
  line and the paragraph after a list are separate paragraphs. A word broken at a line-end hyphen is
  joined and keeps the hyphen (`top-down`) unless the document spells it without one elsewhere
  (`investment`); ligatures are expanded; CJK lines of one paragraph are joined without spaces; bold and
  italic mark only the styled words. JSON `text:title` / `text:section` items carry `level`, and text
  `content` no longer ends with a newline. (#16)
- **Multi-column PDF pages are read column by column** when the page clearly shows columns. Sidebars,
  pull-quotes and margin notes come after the text they stand beside, a picture or drawing with no text
  over it across the columns keeps its place between them (also without image extraction), `/Rotate`
  pages are read as displayed (a table follows its title) and sideways margin text (an arXiv stamp) is
  plain text, never the title. Single-column pages and text set out in rows (forms, tables set as text,
  parallel texts) keep their order, as do pages that mix column layouts and pages with more than 400
  text blocks, tables and pictures. Heading levels can differ on reordered pages. (#23)

**PDF tables**

- **A line break inside a table cell is `<br>` in every table style** (the HTML styles, `markdown_grid`
  and the pipe tables used for tables without merged cells; PDF cells used to be joined with a space).
  Markdown cells escape `|`, a `<` that would open a tag and an entity-like `&`, and double a backslash
  that Markdown would swallow; HTML cells escape `&`, `<` and `>`; control characters are removed. PDF
  cell text follows PyMuPDF's word assembly (touching runs in different fonts are no longer split by a
  space, whitespace including full-width spaces collapses to one space). (#15)
- **Different tables are found:** logos, frames and slide backgrounds drawn as boxes are no longer
  tables (their text stays text); borderless and booktabs tables of at least three rows and three
  columns with a numeric column (not the first) now are (a table of contents, or a table that shares its
  text block with a caption or note, stays text); a table continued on the next page gets the previous
  header, or an empty header row, instead of promoting its first data row; a header row drawn without
  borders is part of the table; merged cells follow the drawn cell boxes. Pages whose text lines up in
  numeric columns pay for a second, text-strategy table search. (#15)

**OCR, routing and pictures**

- **OCR is routed per page.** A page overrides its document's route when its own signals clearly
  disagree: scanned pages in a text report, vector-outlined pages, pages with a garbled text layer and
  pages with little text over inline pictures are OCR'd from their render, and dense text pages in an
  image deck keep their text. On a page overridden to render OCR, legible painted text that the page
  shows but the OCR missed is kept after it, verbatim. (#18)
- **Hidden text and duplicate scan layers are gone:** invisible text over nothing the page shows is no
  longer output (pages and character counts are recorded in `metadata.extra["hidden_text"]`), and a
  searchable scan yields one text (the OCR, or the invisible layer when OCR is off) instead of both. (#18)
- **Pictures are OCR'd by their content, not by size alone, once per image and emitted once per place
  they show.** Small charts, stamps and lettered logos are read, plain backgrounds, colour blocks and
  icons without text are not sent to OCR, tiled figures and pictures cropped by a clip path are read as
  the page shows them, and a logo repeated at the same place on most pages is kept once. PDF renders and
  pictures go to the provider in bounded batches (32 images, or twice `max_concurrency` when that is
  higher) instead of one call per document. (#21)
- **Refusals are no longer indexed.** A provider refusal, a safety block, or a short answer that is, as
  a whole, a refusal or a "no readable text" statement is (in the default structured mode) re-read once
  in free-form mode and, if that also refuses or returns nothing, becomes an empty result
  (`ocr_refusal`), listed in `metadata.extra["ocr_issues"]`. A whole-page render of a page that shows
  content, but whose OCR answered with nothing, reads `[page N: OCR returned no content]`, followed by
  the page's own text layer (a failed render is counted in `ocr_issues` instead). Withheld illustrative
  rows, fields, metrics and figures leave an `[N illustrative ... not transcribed]` marker, and a result
  that violates the router firewall costs one extra verbatim OCR call. (#19)
- **OCR Markdown is safe to embed:** model text is escaped, tables keep `<br>` inside cells and their
  `<caption>`, table spans are bounded, and no OCR text creates an image, or a link whose target has a
  scheme other than http(s) or mailto. (#19)
- **Caches miss once:** the document cache key gains `routing_version` (now 3) and the judges'
  identities, and the OCR cache schema is now `ocr-cache-v5` (was v4), so cached results are recomputed
  once. OCR answers are cached as answers, including an empty one and a refusal (now stored empty,
  flagged `ocr_refusal`); a failed answer, which 0.6.1 cached as an empty one, is never cached, and a
  document is not written to `cache_dir` when an OCR image came back failed, a PDF picture could not be
  extracted, a PDF OCR batch failed or a PDF page showing content stayed unread, or (with a TypeSafe
  judge) when the judge could not answer every question (an INFO log names the reason). (#18, #19, #21,
  #22)
- **New `metadata.extra` keys:** for PDFs `ocr_routing` and `ocr_images` (with OCR on), and
  `text_layer_quality` and `hidden_text` when a page's text layer is garbled or hidden text is found
  (#18, #21); `ocr_issues` when an OCR image was refused, failed or withheld, or an OCR call raised
  (#19); `route_reason` / `route_error`, and `routed_via: "native"`, when the Office image route was
  tried and the file stayed native (#14); `judge` when a TypeSafe judge was asked something (#22). OCR
  results can carry `ocr_refusal`, `router_fallback`, `non_content_suspected` and `non_content_unjudged`
  in their `metadata`. Warnings are logged for garbled layers kept without OCR, hidden text, pages that
  need OCR, and empty or partly unextractable PDFs. (#14, #18, #19, #21, #22)

- **PyMuPDF 1.27.1 or later is required** (the declared `>=1.23.0` could not even import `pymupdf`);
  with an older PyMuPDF forced in, each missing capability is logged once. The CLI turns off PyMuPDF's
  `pymupdf_layout` recommendation and logs MuPDF's messages on stderr, so neither lands in Markdown or
  JSON written to stdout; the library leaves PyMuPDF's own output as PyMuPDF sets it. (#25, #26)

### Added
- **Optional quality judge (TypeSafe/Jev).** `pip install 'doc2mark[typesafe]'`, then `--judge
  typesafe`, `UnifiedDocumentLoader(judge="typesafe")` or `DOC2MARK_JUDGE=typesafe`, with the key in
  `TYPESAFE_API_KEY` (see `docs/judge.rst`). `doc2mark.judge.TypeSafeJudge` asks TypeSafe's Jev (pinned
  `jev-1.13.0`) the three questions the deterministic rules leave open: is a PDF page's text layer
  legible (asked only with OCR on, for nearly every text page the garbage detector does not flag; a
  garbled page is OCR'd from its render), is a repeated header or footer line page chrome (it is
  thinned, normally to its first copy, and never removed), and is an answer from an LLM OCR provider
  only a refusal, an error or a "no readable text" statement (from 0.95 it is re-read or dropped, from
  0.90 up to 0.95 it is kept and flagged `non_content_suspected`). On the held-out TEST set accuracy
  rises from 67 % to 100 % (legibility), 50 % to 95 % (page chrome) and 77 % to 98 % (OCR non-content);
  on a separate external set from 69 % to 100 %, 67 % to 100 % (3 of its 20 items reach the judge) and
  63 % to 83 %. It is off by default, without the extra or the key the output is unchanged, and a judge
  that cannot answer never fails a conversion. With it on, document text is sent to TypeSafe
  (`api.typesafe.ai` by default): up to 1,500 characters of each judged page (with OCR on, nearly every
  text page), the judged header/footer lines and OCR answers of up to 600 characters. Verdicts are
  cached on disk (in the user cache directory unless `$DOC2MARK_JUDGE_CACHE` says otherwise),
  `$DOC2MARK_JUDGE_HOOKS` picks which questions it answers, and `metadata.extra["judge"]` records the
  questions asked, cached and failed, the tokens and the cost. The labelled sets are in
  `tests/data/judge`; `eval/judge_eval.py` re-measures the numbers (it needs the extra and a TypeSafe
  key). (#22)
- **Judge hooks.** The three decisions are plain callables that return the probability of a "yes", or
  `None` when they cannot judge (the rule then decides): `legibility_judge(page_text)` (#18) and
  `boilerplate_judge(line_text, context)` (#17), both accepted by `UnifiedDocumentLoader`,
  `PDFProcessor`, `PDFLoader` and `pdf_to_simple_json`, and `OCRConfig(non_content_judge=...)` (#19),
  which travels with the OCR provider. A plain hook is compared with the pipeline's own thresholds
  (garbled below 0.7, page chrome from 0.5, no content from 0.5, flagged from 0.3);
  `UnifiedDocumentLoader(judge=...)` wires all three from one object, and `TypeSafeJudge`'s hooks
  rescale Jev's probabilities so that its thresholds (0.8, 0.7, 0.95 and 0.90) fall on the pipeline's.
  (#17, #18, #19, #22)
- **Table style on the CLI:** `--table-style {minimal_html,markdown_grid,styled_html}` (the default is
  unchanged). (#15)
- **Per-document reports in `metadata.extra`.** PDFs get `ocr_routing` (the document route and the pages
  that overrode it) and `ocr_images` (OCR requests, page renders, batches, empty and failed requests,
  skipped placements, unread pages) when OCR is on, and `text_layer_quality` and `hidden_text` when a
  page's text layer is garbled or hidden text is found (#18, #21); any format gets `ocr_issues`
  (refused, failed and withheld OCR images, with their image number and page, slide or sheet where
  known) when one occurred (#19); DOCX and PPTX files the image route was tried on get `routed_via`:
  `pdf` (as in 0.6.1), or now `native` with `route_reason` or `route_error` (#14); documents on which
  the TypeSafe judge was asked something get `judge` (#22).
- **New public names:** `doc2mark.ocr.base.OCREngineError`,
  `doc2mark.ocr.schema.withholding_violations`, `doc2mark.ocr.refusal`, the escaping helpers in
  `doc2mark.utils.markdown`, and `doc2mark_refusal` / `doc2mark_failure` keys in the usage dict of
  free-form `batch_invoke` results the provider refused, blocked or failed. (#16, #19, #21)
- **CLI-driven E2E test suite** (`tests/e2e`): it runs the installed `doc2mark` command as a subprocess
  against real Tesseract and LibreOffice, in a reference Docker image (`scripts/run_e2e_docker.sh`; see
  `docs/development.rst`). CI runs it on every pull request to `main`; the `requires_typesafe` tests
  skip there because CI has neither the extra nor a key. (#13, #20, #22)

### Changed
- **Dependencies.** `pandas` is no longer a dependency (nothing in doc2mark imports it; the XLSX loader
  reads cells with openpyxl, and `XlsxLoader.df_sheets` is gone). `numpy>=1.21.0` is now declared: PDF
  routing uses it and it used to arrive through pandas. `doc2mark[all]` now includes `typesafe-sdk`, which
  is imported only when a judge is enabled. (#14, #18, #22)
- **`ocr_images=True` implies `extract_images=True` for every format** when an OCR provider is configured
  (`UnifiedDocumentLoader.load()`; the PDF pipeline implies it too and warns when no provider is set).
  For DOCX and PPTX this also turns on the LibreOffice image-dominance route (LibreOffice is required,
  otherwise the file stays native). (#18)
- **Running headers and footers: verbatim first.** A running header or footer line (see Behaviour
  changes for what counts as one) that is not a bare page number and does not repeat a title or heading
  of the document keeps its first copy, as plain text (or classified like other content when
  heading-sized); the later copies are typed `text:header` / `text:footer` in the JSON and stay out of
  the Markdown. Statement titles, unit notes and disclaimers used as page furniture are no longer lost.
  The optional judge is asked only about repeated top/bottom lines the rule leaves more than one copy of
  (too few pages; not set apart from the content, including repeated table header rows; numbered
  labels); it can thin them, normally to one copy, but never removes a line's last copy. (#17, #22)
- **Caches.** The document cache key gains the legibility judge and `routing_version` (now 3, so cached
  converted documents are rebuilt once), plus the `boilerplate_judge` and `non_content_judge` identities
  when they are set; the OCR cache schema is `ocr-cache-v5` (was v4) and its key includes the
  `non_content_judge`'s qualified name and `version`. OCR answers are cached as answers, including an
  empty one and a refusal (a refusal is now stored empty and flagged `ocr_refusal`); a failed answer
  (0.6.1 cached it as an empty one), a firewall-unresolved result and an answer the judge could not
  screen are never cached. A document is not written to `cache_dir` when an OCR image came back failed,
  a PDF picture could not be extracted, a PDF OCR batch failed or a PDF page showing content stayed
  unread, or (with a TypeSafe judge) when the judge could not answer every question; each skipped write
  is logged at INFO with the reason. (#18, #19, #21, #22)
- **PDF OCR requests are streamed.** Renders and pictures go to the provider in batches of 32 images (or
  twice the provider's `max_concurrency` when higher), sent early once 128 MiB have accumulated, and each
  batch is released once answered, instead of one call that holds every render; identical images or
  renders are sent once. Whole-page renders and embedded figures are OCR'd in separate batches, so
  figures are no longer asked for page-Markdown synthesis. Office images still go to the provider in one
  call per document. (#18, #21)
- **The router firewall runs on every structured OCR result.** Results that withhold printed values
  against the policy are redone verbatim (one extra call), and every auto-routed prompt carries the
  context-absent-means-verbatim clause. (#19)
- **Tests and CI.** The unit job excludes `tests/e2e` and no longer cancels the other Python versions
  when one fails. A separate E2E job runs the suite in Docker (Tesseract eng/chi_tra/chi_sim, LibreOffice,
  Noto CJK) with pytest-xdist workers (`-n 4`) and `OMP_THREAD_LIMIT=1` (Tesseract otherwise runs several
  OpenMP threads for every call, which slows parallel runs down and can push tests past their
  timeouts), which takes the job from about 34 minutes to about 12; the unit matrix stays serial because
  xdist saved at most a few seconds there, within run-to-run noise. `scripts/run_e2e_docker.sh` installs
  the extras named in `$D2M_E2E_EXTRAS`, and the `dev` extra gains `markdown-it-py` (the CommonMark
  parser the tests use). (#16, #20, #22, #24)

- Requires PyMuPDF 1.27.1 or later; `metadata.extra["ocr_issues"]` gains `suspected` and
  `provider_refused` counts (always present, 0 when none) and `suspected` locations. (#25)

### Fixed
- **Word, Excel and PowerPoint conversion no longer drops text.** XLSX: sheets without merged ranges no
  longer get `rowspan`/`colspan` invented from blank cells (which overwrote values), merges come only from
  the file, and header-only sheets, single-cell sheets, error values (`#DIV/0!`) and columns with a header
  but no data are kept. Formulas saved without a cached value show their formula, also in
  namespace-prefixed sheet XML. Values display as the spreadsheet shows them (see Behaviour changes for
  the rounding and sign rules). A title row above a table becomes text only when it is merged across the
  table or separated from it by a blank row; empty rows and columns are dropped; with image extraction
  on, pictures are marked `[Image]` / `[Image: OCR text]` in their cell instead of `#VALUE!`, and without
  OCR an anchored picture is still returned. A title merged across the full sheet width (`A1:XFD1`) no
  longer exhausts memory (a 20,000-row sheet now converts in about a second). DOCX: text inside
  hyperlinks in cells, content controls, tracked insertions, simple fields, smart tags and custom XML is
  kept (deleted text is not), nested tables are flattened into their cell, rows with `gridBefore` /
  `gridAfter` keep their columns and no longer send the whole document to the basic converter, headings
  keep their level (Heading 3 is `###`, Subtitle is `##`, and a paragraph set to outline level "body
  text" is no heading unless it is styled Title or Subtitle) and lists keep their numbers and bullets.
  PPTX: soft line breaks become line breaks, and a slide's own background picture and pictures in
  content placeholders are extracted. Office pictures in which OCR finds no text no longer produce empty
  description items. Still open: DOCX text boxes are not extracted, DOCX header and footer paragraphs are
  only in the JSON items (`text:header` / `text:footer`), not in the Markdown, and tables in DOCX headers
  and footers are lost. (#14)
- **Reliable Office image route.** Background picture fills, grouped pictures and placeholder pictures count
  as images; text in tables, groups, text boxes and DOCX headers and footers counts as text; the OOXML
  signals only pre-filter, and the converted PDF's own decision is final (only `image` routes, `text` or
  no answer means native extraction). LibreOffice conversions use a per-conversion profile, so
  `batch_process(max_workers>1)` and `doc2mark --parallel` no longer fail or fall back, a timed-out
  conversion cannot hang the caller, and any fallback is recorded in `metadata.extra` (`routed_via`,
  `route_reason`, `route_error`). (#14)
- **PDF tables are built from what is drawn on the page.** Values drawn over other text (flattened form
  fields, ticks in checkboxes, rows crossed by a watermark) are no longer deleted (only true overprint
  duplicates are merged), an invisible OCR layer over visible text no longer garbles cells, nested tables
  and overlapping cells no longer duplicate text, and cells keep their line breaks. Merged cells come from
  the drawn cell boxes (blank cells are not shown as merges, tall merges and 2x2 blocks are found), also on
  landscape pages with PyMuPDF before 1.27. Logos, page frames and slide backgrounds are no longer output
  as tables. A borderless header row above a table is its header, a table continued on the next page no
  longer turns its first data row into the header, and a new table under a heading at the top of a page is
  not taken for a continuation. Borderless and booktabs tables with a numeric column are detected; a table
  of contents, or a table whose text block also holds a caption, a note or text running past its edges,
  stays text so no text is lost. Identifiers with underscores (`user_id`) no longer split with PyMuPDF
  1.28, and dense tables are extracted much faster (about 8x on a 1,200-cell benchmark). (#15)
- **A merged cell can no longer hide another cell's value**; cells that repeat the merged value are merged
  into it, and a table whose merges were all dropped renders as a plain Markdown table instead of HTML.
  Table cells no longer break rows or leak markup in any style: Markdown cells escape `|`, a `<` that
  would open a tag and an entity-like `&` and double a backslash that Markdown would swallow, HTML cells
  escape `&`, `<` and `>`, control characters are removed, and line breaks of every kind become `<br>`.
  (#15)
- **PDF text is no longer lost to table, rotation and header/footer handling.** Text next to a table (a
  caption or note against its rules, a title beside a logo box) is no longer dropped together with the
  table; only text inside the table's box is left to the table. Pages with `/Rotate` 90/180/270 no
  longer emit table text twice or drop text where the rotated table box lands, including cropped pages
  (where the CropBox is inherited from `/Pages` or the page boxes cannot be read, table text may still
  repeat, but nothing is lost). Running headers and footers are recognised line by line, before
  classification, so they no longer turn into list items, headings or footnotes; bare page numbers in
  common forms (`Page N of M`, `N/M`, `- N -`, `p. N`, roman numerals, `第 N 頁`, bold, a number right
  under a full page's text or placed differently on a cover page) and numbers printed alone that count
  with the pages (`1001`, `1002`) are removed from every page, while lines such as `ACME | Page N` and
  per-chapter running headers keep only their first copy, and labelled numbers and IDs (`Lesson · 3`, `3
  | ACME Corp`), dates such as `15/03`, titles, headings, continued slide titles, repeated table header
  rows of three or more cells and repeated body text are kept. OCR text of page renders and `[image: OCR
  unavailable]` placeholders are no longer hidden as page headers, overprinted, fake-bold and shadowed
  text is emitted once, and a table repeated at the top or bottom of most pages (a letterhead or logo
  box) appears once. (#17)
- **PDF Markdown is faithful and valid.** List-marker rewriting no longer deletes text (`A. Smith`,
  `E. coli`, `p. 3`, `I. Background` keep their letters); letters, roman numerals and CJK markers make a
  list only in a sequence. Superscripts are written `^x^` instead of fusing into the number, and a
  footnote whose number is raised becomes a `[^N]:` definition. Only numbered labels (`Figure 3:`,
  `Table 2`, `圖1`) or short caption-shaped text attached to an image or table become captions, and
  captions are valid Markdown. Heading detection uses a robust body size and layout tiers, so uppercase
  labels, CJK lines with acronyms, `Section 5 applies...`, running headers, chart labels and drop caps are
  no longer headings while Word 2013+ non-bold headings, bold body-size headings, `?`/comma/colon
  headings, CJK headings without bold, `第一条`, three-line titles and OCR text layers are; heading levels
  follow the document's font-size tiers and outline numbers without skipped levels, multi-line headings
  stay whole and labels side by side are not merged into one heading (no more `## ##` or `## **x**`).
  Word bullets, numbers separated from their item by a tab, lists that share a block with their lead-in
  line, nested sub-bullets and inline bold render correctly. Ligatures are expanded, a word broken at a
  line-end hyphen is joined and keeps the hyphen unless the document spells it without one elsewhere, CJK
  paragraph lines are joined without spaces, and every line of a multi-line footnote is kept (only the
  first one was). (#16)
- **Text that looks like Markdown or HTML is escaped, in PDF and Office text alike** (`\# of patients`,
  `&lt;img ...>`), per one shared policy (`doc2mark.utils.markdown`); a kept running-header copy is escaped
  too, and a numbered list right after bullets (or the reverse) is separated by a blank line instead of
  being glued to the last item. (#16, #23)
- **PDF reading order follows the layout.** Multi-column pages (two or three columns, equal or unequal
  widths, with a title, abstract or figure across the columns) are read column by column; sidebars,
  pull-quotes and margin notes come after the text they stand beside; pictures or drawings with no text
  over them across the columns keep their place between them even when images are not extracted; `/Rotate`
  pages are ordered as displayed, so a table follows its title; and vertical margin text (such as an arXiv
  stamp) is no longer taken for the title. Columns are used only when the page clearly shows them:
  single-column pages and text set out in rows (forms, tables set as text, parallel texts) keep their
  order. (#23)
- **PDF OCR routing is decided per page.** A page overrides its document's route when its own signals
  clearly disagree (searchable scan, garbled text layer, vector-outlined or inline-image content, a
  scanned page in a text report, a dense text page in an image deck), and on such pages legible painted
  text that the render OCR misses is kept verbatim after it. Searchable scans emit one text (the OCR, or
  the invisible OCR layer when OCR is off) instead of both. A text-layer quality gate checks every page
  (U+FFFD, private-use runs except icon-font glyphs, control and CID codes, mojibake, weighted by
  prominence): with OCR on, garbled pages are OCR'd from their render (the legible lines the OCR did not
  reproduce are kept after it, unless only the judge found the layer garbled or the OCR read those lines
  differently); with OCR off, the text is kept and reported in `metadata.extra["text_layer_quality"]`
  with a warning. Garbled pages no longer change the route of the rest of the document. Image coverage is
  the union of the visible picture area (clipped to the page, inline images included) and text density
  counts legible non-whitespace characters, CJK-aware. Invisible (render mode 3) text over nothing the
  page shows is no longer emitted, in paragraphs or table cells (`metadata.extra["hidden_text"]`; with
  PyMuPDF older than 1.27, hidden text that touches painted text inside a table can still reach its
  cells), and neither are invisible copies of painted text; invisible text over a picture that shows
  something (a scan's OCR layer) or over outlined glyphs is kept, unless that picture's OCR returned
  text. Empty or partly unextractable PDFs log warnings instead of producing silent empty output. (#18)
- **PDF pictures are collected once per place the page shows them.** An image listed twice (directly and
  through a Form XObject) or placed several times is one OCR request and one item per placement, and
  identical images and renders are OCR'd once. Pictures are OCR'd by content, not by size alone: small
  charts, stamps and lettered logos are read, while plain backgrounds, colour blocks, frames and icons
  without text are not sent to OCR; tiles of one picture, inline images and pictures cropped by a clip
  path or the page edge are OCR'd as the page shows them, images off the page or clipped away are
  skipped, and transparent pictures are composited onto white instead of read as solid black. A page
  without a text layer whose small, tiled, inline or cropped pictures cover at least 5 % of the page is
  OCR'd from its render; on other such pages, when no picture qualifies on its own, the pictures that
  are not plain are OCR'd rather than skipped. A picture repeated at the same place on most pages is
  kept once. (#18, #21)
- **OCR failures are told apart from answers.** The OpenAI and Vertex AI providers flag a per-image
  failure `metadata["failed"]` (as Tesseract does); a failed PDF picture shows `[image: OCR
  unavailable]` (failed Office pictures are counted in `ocr_issues`) and is never cached, an answer with
  no text is an answer, and a PDF page that shows content but whose render OCR answered with nothing
  carries `[page N: OCR returned no content]` (a failed render is counted in `ocr_issues`, and its
  pictures show `[image: OCR unavailable]`). (#19, #21)
- **OCR tables** keep line breaks, paragraphs and list items inside cells (`<br>`), caption and unit text
  next to a table, nested and multiple tables, `rowspan="0"` and Markdown tables in `Table.html`; spans are
  bounded (a model-emitted `colspan=50000` used to expand to about 250,000 characters), ragged rows are
  padded where their cells line up, a free-form table cut off at `max_tokens` is still a table, and the
  flat headers/rows fallback escapes `|` and line breaks. (#19)
- **OCR refusals and "no readable text" answers** (OpenAI `message.refusal`, Gemini safety and
  recitation blocks, multilingual refusal phrases) are no longer indexed: they go to the free-form
  recovery and, if that also refuses or returns nothing, the result is empty with
  `metadata["ocr_refusal"] = True`. The pattern check fires only on answers that are, as a whole, a
  refusal or "no text" statement and never on a structured page with other content; what it cannot
  decide is kept, or left to `non_content_judge`. (#19)
- **Runtime router firewall.** Structured results that withhold printed values against the policy are
  redone verbatim, and withheld rows, fields, metrics and figures leave a visible `[N illustrative ... not
  transcribed]` marker. (#19)
- **Tesseract:** `--ocr-lang` accepts native codes and `+` combinations (`deu`, `chi_tra`,
  `eng+chi_tra`), keeps the long-name aliases and rejects languages that are not installed; an OCR
  engine that cannot run fails the conversion as soon as there is something to OCR (non-zero CLI exit;
  `--skip-errors` reports the file as failed and continues) instead of writing placeholder Markdown;
  Tesseract output is escaped like any OCR text. (#19)

- **Review follow-ups:** a default `--judge typesafe` run no longer prints a log line per TypeSafe
  request (`-v` shows request lines, never bodies); OCR answers kept although the non-content judge
  suspected them are counted in `ocr_issues["suspected"]` with their page, slide or sheet; a provider's
  own refusal or safety block is cached for `refusal_ttl_seconds` (10 minutes, not extended by hits)
  and keeps the document out of `cache_dir`; Office pictures OCR'd one at a time report their slide or
  sheet; the verbatim tail of an OCR'd page no longer re-adds a printed line the OCR returned inside
  markup or with other words between, nor running headers, footers or page numbers; table text is
  emitted once on rotated hidden-text pages whose CropBox is the MediaBox; reading order measures
  figures without decoding images (slide decks back to their pre-#23 speed). (#25)
- **A refused recovery counts as the provider's refusal.** When a structured OCR answer came back empty
  and the provider refused or blocked the free-form recovery (OpenAI `message.refusal`, a Gemini safety
  block), the empty result was flagged `ocr_refusal` but not as the provider's own refusal: the OCR cache
  replayed it for its full TTL, `ocr_issues["provider_refused"]` did not count it and `cache_dir` stored
  the document for good. It now carries `non_content="provider_refusal"` and the reason, like a refused
  first answer. (#26)
- **Refusals cached before the short refusal TTL are not replayed.** The OCR cache key version is now
  `ocr-cache-v6` (default Redis prefix `doc2mark:ocr:ocr-cache-v6`): entries written by earlier versions
  are never read, so each image cached before is sent to the provider once more after upgrading. (#26)
- **The verbatim tail no longer loses a printed line to the words of another.** One OCR word stands for
  one printed line: a page printing `Total` twice where the OCR read it once keeps its second copy.
  Whole copies of lines claim their words first, then the best fitting alignments, whatever the line
  order: a missed `Tax 1,200` is appended even when the OCR read `Net loss before tax 1,200` as a table
  row with a `Note 4` cell in between, and a total row the OCR left out no longer takes the words of the
  revenue row it read. A line of three words or fewer must appear in one piece; a longer line may gain
  or miss only max(2, a fifth of its words), so `Revenue 2024 up 12 percent` is no longer taken for
  reproduced by a chart's `Revenue by year 2024 up from 2023 12 percent growth`; no word may come between
  two characters of one CJK word (`營業收入` is not `營業外收入`); a one-word line without CJK characters
  is not matched inside CJK text (`AI` in `財務AI使用介面`); and when either of two lines could be the OCR
  text, both are kept. (#26)
- **The verbatim tail is fast again on CJK pages whose OCR answer holds the layer's characters in another
  order:** 25 lines of 200 characters took 87 s per page (50 lines of 100: 6 s), now under 0.1 s, below
  the 0.4 s of the matcher before #25. (#26)
- **The CLI's stdout holds only the document for damaged PDFs too.** MuPDF's errors (`MuPDF error:
  library error: zlib error: ...` for a stream that does not inflate) went to stdout, so the Markdown
  carried them and `--format json` did not parse; they are logged as warnings on stderr instead (shown
  by default and with `-v`, not with `-q`, also from `--parallel` workers). (#26)
- **Importing doc2mark leaves PyMuPDF's own output alone.** The PDF pipeline switched PyMuPDF's
  `pymupdf_layout` recommendation off for the whole process when imported; now only the CLI does. (#26)
- **One clip-path warning per PDF.** When MuPDF's clipped image extents do not pair up with a page's
  images, the warning that its pictures are measured without their clip paths is logged once per
  document instead of twice per page. (#26)
- **Loader OCR settings reach the requests.** `top_p`, `frequency_penalty` and `presence_penalty` were
  stored and never sent: they now go into the OpenAI request and to the Vertex AI (Gemini) client when set
  to another value than the API default (1.0, 0.0, 0.0), so a default request is unchanged. `max_workers`
  was ignored: it caps how many OCR requests run at once when `OCRConfig.max_concurrency` is not set (its
  default is now `None`, which keeps LangChain's default). `default_prompt` never reached a model: it is
  the prompt of free-form requests (`structured=False` and the retry of an empty structured answer), like
  `prompt_template`. `timeout` and `max_retries` now reach Vertex AI too. (#29)
- **Vertex AI and Gemini get their settings.** `OCR("vertex_ai", project=..., location=...)` raised
  `TypeError`: the facade now passes the provider's own arguments (`project`, `location`, `timeout`,
  `max_retries`, `max_workers`, `prompt_template`, `default_prompt`, the sampling settings) to the
  provider, so `OCR("openai", timeout=60)` is the request timeout rather than the deprecated, inert
  `OCRConfig.timeout`. The Vertex AI provider takes `OCRConfig.model`, `temperature` and `max_tokens`
  (it kept its defaults), and `UnifiedDocumentLoader(ocr_provider="gemini", ...)` gets the same model,
  project, location and other settings as `"vertex_ai"` (they were dropped). (#29)
- **The OCR cache no longer answers with a result made under other settings.** Its key left out
  `OCRConfig.task`, so a shared cache replayed a receipt answer for a table request; it now holds every
  setting that changes an answer (task, parse-error mode, the model settings as the provider sends them,
  the response model and its schema, and the text of doc2mark's own prompts and page schema) and no
  longer `max_concurrency`, which changes no answer. Key version `ocr-cache-v7` (default Redis prefix
  `doc2mark:ocr:ocr-cache-v7`): entries of earlier versions are not read. (#29)
- **`cache_dir` no longer returns OCR text made with other OCR settings.** Its key named only the OCR
  provider's class, so after a change of model, task, language, detail, structured mode or prompt the
  old OCR text came back; it now holds the same answer-changing settings as the OCR cache key and the
  neighbour-page context tier. The
  document cache schema is `doc2mark-document-cache-v2`, so files cached by earlier versions are
  converted again once. (#29)
- **A `non_content_judge` value that is not a probability is no verdict**, as it already was for the
  other two hooks: 1.5 or 7 dropped a real OCR answer as "no content", and -1 or NaN counted as a verdict.
  A value outside [0, 1], NaN or a non-number keeps the answer and, like `None`, is not cached. (#29)
- **`token_usage` includes the free-form retry of an empty structured answer.** That second request's
  tokens were missing from the result's and the document's `token_usage`. (#29)
- **OpenAI with a custom `OCRConfig.response_model` works.** Every image failed with `OCRError:
  '<Model>' object has no attribute 'interpretation'`; the answer is now parsed into your model, as
  documented: `OCRResult.document` is that object and `OCRResult.text` its fields as JSON, escaped for
  Markdown. Vertex AI does the same (it returned `document=None` and the model's `str()`), the provider
  keeps the model after a free-form retry, and the OCR cache, Redis included, returns the parsed model.
  (#29)
- **The DeprecationWarning for inert `OCRConfig` fields names your code.** It named a doc2mark line, so
  Python's default filters hid it; it now names the line that created the provider or the loader, and a
  script shows it without `-W`. (#29)
- **`.tsv` files convert.** Every `.tsv` failed with `got multiple values for argument 'delimiter'`. A TSV is
  now a tab-separated table like a CSV (`metadata.delimiter` is `"\t"`; the `delimiter` argument is the CSV
  option and does not apply to it). It has no quoting, so `5" pipe` and `"Best" seller` keep their quote
  characters and a quote never swallows the rows after it. (#28)
- **An explicit CSV `delimiter=` is honoured.** `load("semi.csv", delimiter=",")` and the batch methods used
  the sniffed delimiter whatever was passed. The delimiter is sniffed only when none is given, and one that is
  not a single character is an error. (#28)
- **CLI folder runs convert files only and write the input tree.** The default `--pattern "*"` also yielded
  sub-folders, which failed with `Cannot detect format for extension:` and stopped the run; only files are
  converted now and `-r` decides whether the files inside sub-folders match. The output was flat by file stem
  (`2024/report.md` and `report.txt` both wrote `report.md`, one was lost): `-o DIR` now mirrors the input tree
  (`DIR/2024/report.md`), and each document is written as soon as it is converted, so a failure that stops the
  run keeps what was written before it, and a file is written whole or not at all (a document `--encoding`
  cannot hold is a failed file, not an empty `.md`). Files of one folder that would write the same name
  (`report.txt` and `report.md`) are written as `report.txt.md` with a warning. `-o` may not be the input
  folder (usage error, exit code 2: the outputs would sit among their sources and be converted again by the
  next run); an output folder inside the input folder is left out of the input files, so a run can be
  repeated. (#28)
- **`--timeout` stops a slow file.** It was only passed to `future.result()` after a file had finished, so
  it never applied. Every file of a folder run (also without `-p`) is now converted in a worker process that
  is stopped when the file takes longer than `--timeout` seconds (retries included), together with the
  LibreOffice it started; the file counts as failed (`timed out after 600 s (--timeout)`), like any failure
  (the run stops unless `--skip-errors`). The default is `0`, no limit, so a slow scan is never cut off
  (the help said 300, which never applied). A worker that dies (out of memory, a crash in a native library)
  is a failed file too, no longer a broken pool, and terminating the CLI (SIGTERM, hang-up) stops its
  workers and their LibreOffice. A single-file run is not limited. `--retry` must be 0 or more. (#28)
- **`--preserve-structure` is deprecated.** It was parsed and never read. A folder run always mirrors the
  input tree now, so the flag has nothing left to do: it is still accepted, with a deprecation warning. (#28)
- **The CLI `--help` is true.** The example `--max-files 10 --sort size` says it processes the 10 smallest
  files (sorts are ascending; it said largest), `--ocr openai` no longer says "GPT-4V" (the default model is
  read from the loader) and the supported-formats list has the image formats and EML. (#28)
- **`batch_process()` finds every file `load()` accepts.** It globbed lower-case extensions only and not
  `.htm`, so `report.PDF`, `Notes.TXT` and `page.htm` were skipped. One walk now takes every supported
  extension in any case (`.htm` and `.markdown` too), in path order. (#28)
- **`batch_process()` outputs no longer overwrite each other.** The output name was made with `with_suffix`
  on the stem, so `v1.2.txt` and `v1.3.txt` both wrote `v1.md` and one conversion was lost; they write
  `v1.2.md` and `v1.3.md` (also for JSON output). With an `output_dir` of its own, `batch_process()` and
  `batch_process_files()` also name files that would share an output (`report.txt` and `report.csv`, or
  `q1.pdf` of two folders) by their whole file name (`report.txt.md`, `q1.pdf-2.md`), as the CLI does, and an
  `output_dir` inside the input folder is left out of the inputs (before, every run converted the outputs of
  the run before and added copies of the text). Without an `output_dir` (outputs next to the inputs) an output
  still replaces the one of an earlier run, but a result is never written over the file it was converted from
  (a Markdown file next to itself lost its front matter). (#28)
- **`ProcessedDocument.tables` and `.sections` are filled.** No processor set them, so a batch result's
  `tables_found` was always 0 and the JSON output had `"tables": null`. They are read from the content
  items: one `{"page", "format", "content"}` per `table` item and one `{"level", "title", "page"}` per
  heading (`text:title`, `text:section`, titled as the Markdown shows it, with its number), for PDF, Office and
  image files (`None` for formats without content items); `tables_found` counts them. A document replayed
  from `cache_dir` has what was stored. (#28)
- **The convenience functions take loader settings.** `load(path, table_style=...)` and `cache_dir=` raised
  `TypeError` because every extra keyword went to `loader.load()`. Loader settings now reach the loader, the
  options of `load()` / the batch method (`encoding`, `delimiter`, `max_workers`, ...) reach that call, and a
  name that is neither is a `TypeError` that says what is accepted. (#28)
- **`table_style` is validated once, at the loader, and reaches legacy files.** A name is matched in any case
  (`"MARKDOWN_GRID"`) or given as a `TableStyle`; an unknown one is a `ValueError` listing the valid styles.
  Before, it failed PDF conversion but silently sent Word, Excel and PowerPoint files to the basic converter
  (no merged cells, no `json_content`), and `.doc`/`.xls`/`.ppt`/`.rtf` files ignored the style altogether. (#28)
- **A Markdown file that starts with a `---` rule keeps its text.** The first block (prose, a list, anything
  between two `---` lines) went to `metadata.frontmatter` and out of the content. Only a `---` line, YAML
  that parses to a mapping and a closing `---` line are front matter now; everything else is left as
  written, and so is the text after the front matter (only the blank lines right after it go). A date that
  does not exist (`2024-02-30`) no longer fails the whole file: it is not front matter, the text is kept. (#28)
- **Dates and sets in Markdown front matter no longer break JSON output.** YAML reads `date: 2024-05-01` as a
  date (and `!!set` as a set), which `--format json`, `output_format="json"` and `cache_dir` could not write;
  they are ISO strings (sorted lists) there (`datetime.date` objects in `metadata.frontmatter`). (#28)
- **A Word, PowerPoint, Excel or image file whose OCR failed is marked, counted and not cached.** A
  picture whose OCR request raised (for example without an API key) or whose answer the provider flagged
  failed left the literal text `OCR failed` (`[Image: OCR failed]` in a cell, `*OCR extraction failed:
  <error>*` for an image file) as if the picture said so, `ocr_issues` counted no failure, and
  `cache_dir` stored the document, so the error text was replayed after the key was fixed. Such a picture
  now shows `[image: OCR unavailable]`, as in PDFs; `ocr_issues["failed"]` counts it (for every format,
  also the images of any OCR request that raised) with its page, slide or sheet in `locations`; the
  pictures of a batch request that raised are not sent again one by one; and the document is not stored
  in `cache_dir`, so the next run reads the pictures. When an OCR cache (`ocr_cache=`) holds the answers
  for some pictures of a batch whose request raised, those answers are used (the cache wrapper raised for
  the whole batch), and a PDF batch that raised reports the page of each of its pictures. (#30)
- **PowerPoint slides no longer carry their layout's prompts and fields.** "Click to edit Master title
  style", the layout's date and `‹#›` were added to every slide as captions, and so were slide-level date
  and slide-number fields; only text a slide sets in its placeholders is kept (a typed footer or date).
  Text a layout or master draws on the slides (a tagline) is kept once, on the first slide that shows it,
  instead of on every slide (a master's was lost). The text of plain shapes such as rectangles (and of
  LibreOffice-converted text boxes) is no longer emitted twice. (#30)
- **`slide_count` is the number of slides.** It counted the text "Slide " in the Markdown (a 5-slide deck
  gave 1); decks taken through the Office image route now report it too. (#30)
- **Word text boxes are read.** Text in text boxes and shapes (also grouped, in table cells and in
  headers, and the VML text boxes of older files) was not extracted at all; it now follows the text of
  the paragraph it is anchored in, once (not from both copies Word saves); its lists are numbered on their
  own. (#30)
- **Word headers and footers are in the Markdown, with their tables and pictures.** They were only
  `text:header` / `text:footer` items of `json_content`, header tables were lost, and a header picture
  was looked up in the body's relationships (the Markdown got another part's bytes as a PNG). Each header
  and footer a section shows is now written once, between `<!-- header -->` / `<!-- /header -->` (or
  `footer`) lines where the section starts (ends), its items typed like body text with `"region":
  "header"` / `"footer"` (a heading-styled header line is a plain line); a header repeating the text of
  one already written is not written again, a line that only shows a page number is left out, and header
  lists are numbered apart from the body's. (#30)
- **Only caption-shaped Word paragraphs are captions.** Any paragraph starting with Table, Figure, Chart,
  Image and similar words became an italic caption ("Tablets are popular ..."); a caption is now a
  paragraph in a caption style or one that starts with the word, a number and a separator ("Figure 2:
  ..."). (#30)
- **Word bold, italics and links are kept.** Runs are written `**bold**`, `*italic*` and `[text](url)` in
  paragraphs and list items, only where that changes nothing a reader sees: markers keep words whole, a
  backslash before a marker is kept, no emphasis next to a literal `*` and no markup on a line with a
  backtick, links only to http(s) and mailto addresses, set off from the text and not after `!`. The text
  itself is escaped as before; the JSON item keeps it as written in `content` and gets the Markdown in
  `markdown`. Headings, captions and table cells stay plain. (#30)
- **Excel results report `sheet_names` and `total_cells`.** Both were set only by the basic fallback
  converter; `total_cells` counts the cells that show a value, in all sheets. (#30)
- **Word `word_count` counts words.** It counted the words of the Markdown's `<!-- page N -->` marker
  comments too. (#30)

### Security
- **OCR output is sanitized at the Markdown boundary.** Every model-supplied string except sanitized tables is
  escaped, no OCR text can create an image, or a link whose target has a scheme other than http(s) or
  mailto, raw HTML in model output shows as text (code spans and fenced blocks keep their markup where it
  is inert), HTML comments in tables are dropped, and a `<` right before a table that the sanitizer
  unwraps cannot complete a tag. Tesseract output goes through the same boundary. (#19)
- **Table cells cannot inject markup in any table style.** `|`, HTML tags (`<img onerror>`, `<script>`) and
  control characters in PDF, Office and OCR cells are escaped or removed, so a cell can no longer break
  its row or leak live HTML into the output. (#15, #19)
- **Spans in OCR tables are bounded** (a colspan by the widest row and by 1,000, a rowspan by its row
  group; a table that would need more than 500,000 column slots loses its spans), so a model-emitted
  `colspan=50000` (about 250,000 characters of Markdown before) or a wall of huge spans can no longer
  balloon the output. (#19)
- **The optional judge does not log document text or the key.** Request-failure log lines name the hook,
  the exception class, the HTTP status and the request id, never document text or the key, and the
  TypeSafe SDK's wire log (which would contain request bodies) stays off unless `TYPESAFE_LOG_LEVEL` is
  set or the `typesafe_sdk` logger is configured; the key is read from `TYPESAFE_API_KEY`. With the judge
  enabled, text does leave the machine for TypeSafe; see `docs/judge.rst`. (#22)

### Documentation
- **README and docs checked against the code and rewritten.** The README is a pitch with a quick start,
  a re-measured merged-cell comparison with markitdown and Docling, and links into the docs; the Sphinx
  site is reorganised into getting started, user guide and an API reference generated with autodoc, with
  new pages for installation, a minimal RAG pipeline, loading and batches, the result model and every
  `metadata.extra` key, PDF text structure (reading order, verbatim-first running headers), images,
  chunking and troubleshooting. Every code example and CLI command of README.md and docs/ runs in the
  E2E image (`eval/docs_audit/`), and the judge numbers were re-measured. (#27)

## [0.6.1] - 2026-07-03

### Added
- **Document-level OCR token-usage aggregation.** The token usage that each LLM
  OCR call records in `OCRResult.metadata["token_usage"]` (LangChain
  `usage_metadata` — `input_tokens`/`output_tokens`/`total_tokens`) is now summed
  across every OCR call made during a single `UnifiedDocumentLoader.load()` and
  stamped onto `ProcessedDocument.metadata.extra["token_usage"]`, so downstream
  consumers can meter OCR cost without reaching into per-image internals. A thin
  `UsageAggregatingOCR` wrapper (same transparent shape as `CachedOCR`) is placed
  around the shared OCR instance handed to every processor, so the count covers
  all providers (OpenAI, Vertex/Gemini) and all source families (PDF page
  renders/embedded figures, Office/image extracted images) through the one merge
  point. Naming variants (`prompt_tokens`/`completion_tokens`, total-only
  payloads) are folded defensively, and `total_tokens` is derived when absent.
  A no-OCR load — or a usage-less provider such as Tesseract — leaves `extra`
  byte-identical (nothing is stamped). Accumulation is per-`load()` and
  thread-local, so the loader's concurrent batch processing does not
  cross-contaminate. The aggregated count reflects **only fresh provider spend**
  this `load()`: a `CachedOCR` cache hit, an intra-batch dedup fan-out copy, and a
  whole-document `cache_dir` replay each carry no new spend and are excluded, so a
  billing consumer of `metadata.extra["token_usage"]` is never charged for tokens
  that were not spent. `CachedOCR` flags such non-fresh results
  (`metadata["doc2mark_from_cache"]`) on the returned copy only — never on the
  value written to the cache, so the marker neither round-trips into a stored
  payload nor changes the cache key. A document-cache replay renames its stamped
  `token_usage` to `token_usage_cached` (the original run was billed once; the
  count stays visible for diagnostics).

## [0.6.0] - 2026-07-03

### Added
- **Image-dominant Office docs routed like image PDFs.** A `.docx`/`.pptx` that is
  mostly pictures with no usable text layer (e.g. a slide deck exported as images)
  is now detected from its OOXML structure (picture coverage + text density, via the
  shared `core.strategy` decision) and routed through the PDF image strategy —
  converted to PDF, then whole-page render OCR + `page_markdown` synthesis — instead
  of the native per-embedded-image path that fragmented such files. Text/table office
  docs keep their exact native OOXML extraction (byte-identical); XLSX never routes.
  The route is gated to OCR-enabled runs and falls back to native extraction on any
  failure (including no LibreOffice). On the office-image benchmark, meaningfulness
  rose from 2.0 to ~4.7 with no regression on any other document. Internals were also
  consolidated: a shared `core.strategy` (two-signal decision), `core.types`
  (`SimpleContent`), and `utils.libreoffice` (converter), and shared
  empty-structured detection/recovery on `BaseOCR`.
- **Meaningful Markdown for image-heavy pages.** Image-strategy pages (slide
  decks / scans) now emit a structured `interpretation.page_markdown` synthesis
  in the same OCR call — `##` headings, numbered cards, `A → B → C` flow chains —
  used as the rendered display body instead of the flat OCR dump. A verbatim
  coverage guard keeps it BM42-safe: it is only used when it covers the raw text's
  tokens, any residual tokens are carried in a hidden tail, and it falls back to
  the verbatim raw dump if the synthesis under-covers. Gated strictly to the
  image strategy, so ordinary text/table/data documents are byte-identical (their
  faithful rule-based output is untouched). On the image-deck benchmark the
  meaningfulness judge rose 3.0 → 4.0 with no regression on any other document.
- **Extraction eval harness** (`eval/extraction_harness.py`) scoring every
  document on the three requirements — body-text preservation, complex-table
  structure (col/row spans, span-expanded cell match), and meaningfulness (LLM
  judge) — so extraction changes are validated system-wide, not fit to one file.
- **Structured OCR output.** The OCR layer now returns an `OCRPage` (on
  `OCRResult.document`) with a hard boundary between `raw` (verbatim
  transcription, tables, key/value fields) and `interpretation` (summary,
  document type, key findings). Structured output is the default.
- **Richer, nested OCR schema for image-strategy pages.** When the OCR output is
  the only representation of a page, the model now extracts deep structure in one
  pass. `raw` gains verbatim, BM42-safe additive indexes — `headings`, `dates`,
  and typed `metrics` (`Metric`: label/value/unit, never normalized).
  `interpretation` gains retrieval/comprehension anchors (`page_title`,
  `primary_message`, `keywords`, `column_layout`, `page_role`, `primary_date`,
  `action_items`, `definitions`) **and nested structures**: `figures`
  (`Figure`/`DataPoint`/`DiagramNode`/`DiagramEdge` — charts as data points,
  diagrams as nodes/edges, with `meaning`/`trend` fallbacks), a flat-with-`level`
  `sections` heading hierarchy, typed `typed_entities` (`Entity`: name/type/
  salience/role, replacing the flat string list), and `relations` (`Relation`
  knowledge triples for explicitly-stated claims). The meaningless `reading_order`
  was removed. `router_invariants()` enforces that every verbatim string inside a
  figure/entity/relation/section is a substring of `raw.text`/`raw.headings`
  (BM42), that diagram edges reference real nodes, that chart data points carry a
  printed value, and `primary_date ∈ raw.dates`; `to_markdown()` renders figures
  and a section outline degraded-safe. Designed fill-ability-first (max nesting
  depth 4, no recursion/unions, all fields defaulted) and verified to fill on
  `gpt-5.4-mini` with no empty-object fallback.
- **OCR table extraction with merged cells.** Each ``Table`` in the structured
  result now carries an ``html`` field; the model is guided to reproduce tables
  as clean HTML using ``colspan``/``rowspan`` for merged cells (which the flat
  ``headers``/``rows`` and markdown cannot represent). ``OCRPage.to_markdown()``
  prefers it.
- **Document-level OCR strategy route.** Each PDF is classified once from two
  deterministic signals — mean per-page image coverage AND mean per-page
  selectable-text density. "image" docs (high coverage, low text/page: slide
  decks, scans) are OCR'd whole-page-by-page with the OCR authoritative; "text"
  docs keep the deterministic rule-based text/table layer (BM42) and OCR only
  embedded figures. Text density is the decisive signal: coverage alone
  misclassifies a text document that carries large figures. A uniform per-doc
  strategy avoids mixing OCR-only and rule-based pages.
- **Page-level OCR for image-dominant PDFs.** Scanned pages and slide decks
  exported as pictures (little/no text layer, images covering the page) are now
  rendered and OCR'd once per page instead of per embedded image — coherent
  per-page content, far fewer API calls, and decorative logos/icons skipped.
- **Self-routing image OCR (job-router).** The default OCR prompt now classifies
  each image and applies a type-specific policy instead of blind verbatim
  transcription: most images are transcribed verbatim; a product UI mockup with
  illustrative sample data — triple-gated by app chrome + sample-data signature +
  marketing/module-intro context — describes the demonstrated capabilities and
  withholds the fake records; charts/diagrams/infographics keep all printed text
  and describe the trend/structure. New schema: `document_type` widened to 16
  values, `Interpretation.content_fidelity`, `Table.illustrative`/`row_count`,
  `KeyValue.illustrative`. A `router_invariants()` firewall guarantees real
  printed values are never withheld except on a high-confidence `screenshot`
  (BM42-safe); the free-form fallback path stays verbatim-only. When unsure, the
  router always transcribes verbatim.
- **Neighbor-page PDF context for OCR.** When OCR'ing content on PDF page *k*,
  doc2mark can attach a small PDF of pages *{k-1, k, k+1}* as context (Gemini
  inline PDF part) to anchor terminology and language, improving consistency.
  Controlled by `OCRConfig.context_pages` (0=off default, 1=page-renders,
  2=+embedded images); per-page-deduplicated, size-guarded, and context-aware
  cache keys. The deterministic rule-based text layer is always preserved
  verbatim — LLM OCR only augments image content (never replaces it).
- **`OCR` facade.** New ergonomic entry point: `OCR("openai")` with `.read()`
  and `.read_one()` methods, replacing direct provider construction.
- **`Task` enum.** Replaces the eight free-form `PromptTemplate` variants with
  intent names (`auto`, `table`, `document`, `form`, `receipt`, `handwriting`,
  `code`). Supports per-call (`task=`) and per-image (`tasks=[]`) overrides.
- **`gemini` provider alias.** `OCR("gemini")` is now accepted alongside
  `"vertex_ai"`.
- **`detail="raw"` mode.** Skips the interpretation pass to save 10-30% output
  tokens while still returning structured `raw` extraction.
- **`.eml` email ingestion.** New `EmailProcessor` (`DocumentFormat.EML`)
  extracts headers and body to Markdown/JSON/text using the stdlib parser.
- **Opt-in cross-document batch parallelism.** `batch_process` /
  `batch_process_files` accept `max_workers` and a `progress_callback`; default
  stays sequential.
- **Token-aware chunking.** `ChunkingConfig(size_unit="tokens")` measures chunk
  size with `tiktoken` (new `[tokenizers]` extra; graceful char fallback).
- **Pre-OCR image downscale.** Optional longest-side cap (`OCR_MAX_IMAGE_DIM`
  env var or `max_dim` arg) to reduce Vision-API token cost.
- **Typed public API + `py.typed`** marker (PEP 561) for downstream type
  checkers.

### Changed
- OCR cache schema bumped to v4 to store the new structured `document` field.
  Existing v3 cache entries are invalidated on first access (one-time re-OCR).
- Cache key no longer includes inert config fields, reducing spurious misses.
- Minimum supported Python raised to 3.10 (matching the tested CI matrix).
- CI now enforces test coverage and runs ruff/black/isort/bandit/mypy gates.
- Removed the dead `UnifiedProcessor` stack and a duplicate LibreOffice
  converter; `formats/legacy.py` is the single legacy-conversion path.

### Deprecated
- `OCRConfig` fields `enhance_image`, `detect_tables`, `detect_layout`,
  `timeout`, `max_retries`, and `extra` are inert for LLM providers and now
  emit a `DeprecationWarning` when set to non-default values. Use `task` and
  the structured output controls instead.

### Fixed
- **Cleaner OCR export for image-heavy PDFs (no duplication, no marker noise).**
  Image-dominant pages are now *OCR-authoritative* — the whole-page OCR is the
  content, routed by the page's image-occupancy ratio. The sparse text-layer
  chrome (logo / footer / page numbers) is no longer emitted alongside it (it
  duplicated the OCR 2-3× and produced junk header/footer mini-tables).
  Text-bearing pages still keep the deterministic text/table layer (BM42). The
  internal ``<ocr_result>`` code-fence wrapper around OCR'd image text was also
  removed from the Markdown export — OCR text is emitted clean.
- **Dense structured OCR no longer truncates or aborts the whole batch.** The
  default `max_tokens` was raised 4096 → 8192 (a dense page's structured JSON
  exceeded 4096 tokens, and the resulting truncation error aborted OCR for the
  entire document). Batch OCR now isolates per-image failures (`return_exceptions`)
  so one image's error degrades only that image (recovered or placeholdered),
  never the batch.
- **OCR batch failure no longer dumps raw base64 into the output.** Previously a
  single error in the image-OCR batch flipped the whole PDF to base64 image
  extraction, producing tens of MB of useless base64 in the text/RAG output.
  Image-OCR failures now degrade to lightweight `[image: OCR unavailable]`
  placeholders while the deterministic text/table layer is preserved.
- **Structured OCR no longer silently loses content.** When a model can read an
  image but cannot fill the json_schema (some weaker/preview models return an
  empty ``OCRPage`` on dense or non-Latin images), the OpenAI and Vertex/Gemini
  providers now recover by re-OCR'ing those images in free-form mode.
- Repaired the release pipeline: `.bumpversion.cfg` no longer targets a
  non-existent `pyproject.toml` version line that aborted every release.
- Forward `OCRConfig` timeout/`max_retries` to the LLM clients (were inert).
- `LegacyProcessor` preserves `metadata.extra` object identity (`is None` guard).

### Security
- Hardened XML parsing against XXE and entity-expansion: DOCX/PPTX footnote
  parsing uses a locked-down lxml parser (`resolve_entities=False`), and the
  markup path uses `defusedxml`.
- Sanitized the LLM-produced ``Table.html`` (OCR) to a strict table-tag
  allowlist (`colspan`/`rowspan`/`scope` only), dropping scripts, styles, event
  handlers, and URLs to remove an HTML-injection / XSS sink. Fails closed.

## [0.5.2] - 2025-05-20

### Added
- OCR result caching with pluggable backends (`MemoryOCRCache`, `RedisOCRCache`,
  `NoOpOCRCache`) and a `create_ocr_cache()` factory helper.
- Redis-backed OCR cache backend (requires the new `[redis]` optional extra).
- Configurable `max_workers` / `max_concurrency` for LLM-based OCR batch
  processing.

### Changed
- OCR providers are now initialized lazily so that text-only document
  processing works without OCR extras or API keys.
- Migrated packaging metadata from `setup.py` to `pyproject.toml`.

### Fixed
- OCR cache key scoping now accounts for provider config, model, prompt, and
  API-key hash to prevent cross-provider cache collisions.
- Reduced false-positive heading detection in PDF heuristics.
