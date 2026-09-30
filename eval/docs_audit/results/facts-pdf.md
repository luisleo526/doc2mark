# doc2mark docs audit: PDF pages (claim verdicts + PDF fact sheet)

Code audited: `/Users/haoliangwen/code/doc2mark-d2m-docs-audit` at `becb74b` (= origin/main). Doc line numbers
are at HEAD (the audited docs are unmodified in the worktree). Verdicts: TRUE / FALSE / PARTLY / UNVERIFIABLE.

Probes: `/tmp/d2m-docs-audit-probe/*.py`, run from `/tmp/d2m-docs-audit-probe` with the worktree on PYTHONPATH (import
path printed each run), PyMuPDF 1.27.2 (`/Users/haoliangwen/miniconda3/bin/python3`). The Mac's default `python3` has
PyMuPDF 1.26.4 (below the floor) and was used only for the old-PyMuPDF check. No network; OCR probes use an in-process
fake provider. Outputs are in section 6.

File keys: `strategy`=doc2mark/core/strategy.py, `routing`=doc2mark/pipelines/pdf_routing.py,
`images`=doc2mark/pipelines/pdf_images.py, `layout`=doc2mark/pipelines/pdf_layout.py, `tables`=doc2mark/pipelines/pdf_tables.py,
`pipe`=doc2mark/pipelines/pymupdf_advanced_pipeline.py, `compat`=doc2mark/pipelines/pymupdf_compat.py,
`pdf`=doc2mark/formats/pdf.py, `office`=doc2mark/formats/office.py, `md`=doc2mark/utils/markdown.py,
`loader`=doc2mark/core/loader.py, `usage`=doc2mark/ocr/usage.py, `chunker`=doc2mark/core/chunker.py,
`schema`=doc2mark/ocr/schema.py, `ocrbase`=doc2mark/ocr/base.py, `cache`=doc2mark/ocr/cache.py, `cli`=doc2mark/cli.py.

---------------------------------------------------------------------------------------------------------------------

## 0. FALSE / PARTLY / UNVERIFIABLE at a glance

| Doc:line | Verdict | Problem |
|---|---|---|
| formats.rst:126-127 | FALSE | `ocr_images` does not need `extract_images=True` (implied when a provider is set), and it maps to the *processor's* `use_ocr`, not the pipeline's |
| ocr_policy.rst:226-231 | PARTLY | a *failed* render gets no `[page N: OCR returned no content]` marker and is not in `unread_pages`: the page takes the text route and its pictures show `[image: OCR unavailable]` |
| ocr_policy.rst:521-524 | FALSE | DOCX signals now count floating (anchored) pictures and all rendered text (tables, text boxes, headers/footers); "inline-picture coverage / undercounting floating images" is obsolete |
| ocr_policy.rst:526-533 | PARTLY | extra gate (the converted PDF's own route must be `image`); fallback is not silent: `routed_via:"native"` + `route_reason` / `route_error` |
| ocr_policy.rst:519-520 | PARTLY | PPTX coverage now includes background fills, grouped/placeholder/picture-filled shapes, layout/master pictures |
| ocr_policy.rst:612-616 | PARTLY | router firewall's withholding subset runs at run time on every structured result (redo), not only as a CI/eval assertion |
| ocr_policy.rst:489-491 | PARTLY | `text_layer_quality` entries also carry `page` and `legible: false` |
| ocr_policy.rst:495-497 | PARTLY | a document with no text at all gets one warning that names no pages |
| ocr_policy.rst:388-389 | PARTLY | garbled page is OCR'd only with OCR on (provider AND `ocr_images=True`), not with just a provider |
| ocr_policy.rst:341-356 | PARTLY | omits: `non_content_unjudged` answers are not cached; `cache_dir` also skipped when the judge could not answer |
| ocr_policy.rst:235-242 | PARTLY | 128 MiB is a flush trigger (a batch can exceed it); identical renders are not merged when `context_pages>=1` |
| ocr_policy.rst:20-25 | PARTLY | not "only image pages": text-route pictures are OCR'd too |
| ocr_policy.rst:118-120, 205-212, 238-241 | UNVERIFIABLE | "six statements 290/260 vs 339/91", TC-deck example, 400-page memory figures: no committed measurement (memory figures only in an E2E docstring) |
| tables.rst:129-132 | PARTLY | rule still applied, but "the text output skips each text block that touches a table" is false since #17 (only spans inside the table box are skipped) |
| tables.rst:137-140 | UNVERIFIABLE | "1,391 pages ... 18 tables": no committed corpus/script/result; only in the local PR #15 body draft |
| README.md:405-408 (+35-36) | PARTLY | verbatim first: only bare page numbers and title/heading repeats leave every page; most running lines keep their first copy; nothing leaves `json_content` |
| README.md:410 | PARTLY | needs at least 3 pages (a 2-page PDF keeps every copy) |
| README.md:410-412 | PARTLY | first copies reach Markdown and chunks by design; removed copies stay in `json_content`/JSON output |
| formats.rst:127-130 | TRUE, caveats | fallback still exists and is reachable; triggers on any ImportError during the run; very different output |
| formats.rst:205-217 | TRUE, omission | >400 items keep top-to-bottom order; columns MuPDF merged into one block stay interleaved |
| formats.rst:218-220 | TRUE, nuance | sideways text is never a heading, but can still be a caption/list/footnote |
| formats.rst:106-108 (Office, outside the PDF section) | FALSE | "silently falls back": the fallback logs and records `routed_via`/`route_*` |

---------------------------------------------------------------------------------------------------------------------

## 1. docs/ocr_policy.rst

**Intro (4-38)**
- 4-9 no routing flags; `ocr_images=True` implies `extract_images=True`: TRUE with a provider (loader:630-634, pipe:849-855); without a provider `ocr_images` is dropped with a warning (pipe:849-852).
- 20-25 "Only a true image-page is sent to an LLM vision model": PARTLY. Whole-page OCR is for image pages only, but the text route also OCRs every content-bearing picture (pipe:2226-2275), and the provider may be Tesseract. The rest (garbled, invisible, outlined layers send the page to OCR) is TRUE (strategy:514-540).
- 29-38 four layers: TRUE.

**Shared thresholds (40-120)**
- 43-47 single source `decide_doc_strategy` / `decide_page_route`: TRUE (strategy:148-175, 514-540; pipe:2017; office:349-364).
- 53-58 examples → image, text, text, image: TRUE (probe P0).
- 63-101 constants: all values TRUE: IMAGE_PAGE_COVERAGE 0.55, IMAGE_PAGE_TEXT_LIMIT 200 (strategy:41-42), weights 3.0/2.0 (48-49), ILLEGIBLE_TEXT_RATIO 0.3 (66; unused by both routes: pipe:2017, office:360), GARBAGE_TEXT_RATIO 0.1 / MIN_GARBAGE_GLYPHS 3 (60-61), PAGE_OVERRIDE_MARGIN 0.5 (83), LEGIBILITY_JUDGE_THRESHOLD 0.7 (71). The table is incomplete (not wrong): NO_TEXT_LIMIT 50, MIN_UNCAPTURED_RASTER 0.05, MIN_UNCAPTURED_INK 0.001 (94-96), MIN_JUDGED_CHARS 20 (72), MAX_PROMINENCE 4 (62), LAYER_DPI 100 / LAYER_BLANK_CONTRAST 12 / LAYER_BLANK_SHARE 0.01 / INK_CONTRAST 48 / GLYPH_* (116-123), ROUTING_VERSION 3 (127).
- 103-109 document rule; garbled pages do not move it: TRUE (pipe:1992-2021 passes no illegibility).
- 111-117 script-aware `text_weight`: TRUE (strategy:274-322; tests/test_strategy.py:65-70).
- 118-120 "six statements 290 EN / 260 ZH vs raw 339/91": UNVERIFIABLE (no fixture, test or commit text; `git log -S` finds only the doc).

**Layer 1 (122-150)**
- 125-126 each page measured once: TRUE (pipe:1922-1934), also without OCR (pipe:2097).
- 128-132 coverage = union of `get_image_info` boxes clipped to the CropBox, inline images included, clip paths ignored: TRUE (routing:281-334).
- 133-136 painted-text weight; invisible kept apart; quality: TRUE (routing:637-669).
- 137-143 uncaptured content (72 DPI, line art <2 pt excluded): TRUE (routing:57, 341-348, 398-458, 670-679). Unstated: ink is measured only when coverage <0.55 (routing:675-676).
- 145-150 log line: TRUE, exact format (pipe:2018-2020, INFO; computed only in OCR runs and by the Office route).

**Per-page routes (152-214)**
- 155-157 with OCR on; first rule wins: TRUE (pipe:2023-2037; strategy:514-540).
- `searchable_scan`, `illegible_text_layer`, `no_text_layer` (<50, ≥5 %, ≥0.1 %), `image_dominant_page` (≥0.825, <100), `dense_text_page` (<0.275, ≥300): TRUE (strategy:474-498, 527-539). Caveat: 50/100/300 are `text_weight` units (a CJK ideograph counts 3), not raw characters.
- 193-203 verbatim tail after the OCR: TRUE (strategy:145; pipe:2417-2431; routing:1100-1148). Unstated: removed running headers/footers/page numbers are not re-added (pipe:2424).
- 205-212 TC-deck example: UNVERIFIABLE (no fixture); consistent with the rules.
- 214 without OCR every page takes the text path: TRUE (routing is used only in `_ocr_jobs`, pipe:2237; image branch needs a render result, pipe:2406-2407).

**"image" route (216-242)**
- 219 single PNG at 150 DPI: TRUE (pipe:29, 2147-2149).
- 220-224 text layer not also emitted: TRUE, except the verbatim tail on override pages (pipe:2409-2432).
- 226-231: PARTLY. An *empty* answer (blank page, refusal) falls back to the page's text layer, without its pictures (pipe:2442; warning only if that layer has text, 2443-2445), adds the marker only when the render shows ink ≥0.1 % (2446-2449), and lists the page (1-based) in `ocr_images.unread_pages` (886-887). A *failed* render gets no answer (942-947, 951-952): no marker, not in `unread_pages`. The page takes the normal text route and its pictures show `[image: OCR unavailable]` (4326-4330). It is counted in `ocr_images.failed` (2333-2334) and `ocr_issues.failed` (usage:278-283). Probe P4 shows both cases.
- 233 renders request `page_markdown` synthesis: TRUE (pipe:2325; P4 kwargs).
- 235-237 batches of 32 (or 2×`max_concurrency`, also taken from env `OCR_MAX_CONCURRENCY`) and 128 MiB, released once answered: TRUE (pipe:48-49, 2285-2292, 2318-2336; ocrbase:36-55). Nuances: 128 MiB is a flush trigger (flush once ≥128 MiB is pending, so a batch can exceed it; context-PDF bytes count), and renders and pictures are batched separately (pipe:2314, 2371-2374).
- 238-241 400-page memory figures: UNVERIFIABLE. They appear only in the docstring of tests/e2e/test_images.py:291-295 (1.2 GB at 160 pages held, 2.7 GB at 400 extrapolated, about 0.26 GB streamed). The test itself asserts only peak <768 MB and batches ≤32 at 160 pages (299-304).
- 241-242 page order kept; identical renders are one request: TRUE (pipe:2338-2350). Caveat: with `context_pages>=1` a render's identity includes its window PDF, so identical renders on different pages are not merged (pipe:2244-2247).

**"text" route (244-256)**: TRUE (pipe:2451-2476, 4224-4268, 3795-3852, 2180-2216).

**Pictures on the text route (258-339)**: all TRUE.
- Placements: one per drawn place, deduplicated by (xref, box) (images:176-192).
- Tiles: 1 pt / 5 % / half side (images:49-50, 203-215); rendered at the tiles' own 150-300 DPI (265-268, 81-82) without the text drawn over them. Also capped at 16 MP (83, 436-455).
- Shown: whole, or ≥12 pt on both sides; <12 px images skipped (41-43, 108-123, 271-306). The clip measure needs TEXT_CLIP (130-134). A clip/image count mismatch warns once per document (145-155).
- Content: grey copy ≤1024 px; *plain* = <24 edge pixels at 16 levels after 80 % lines are dropped; *text* = ≥6 transitions at 32 levels (68-73, 403-420).
- Small = <10 % of the page in both directions or <48 pt on both sides. Small pictures are OCR'd only as *text*, larger ones unless *plain*; undecodable pixels count as *text* (74-75, 321-326, 458-482). The 130×75 chart / 40 pt icon example holds by the rule.
- Samples (stencil, soft mask) vs full resolution for OCR: images:332-378, pipe:2277-2283.
- One request per content, per page with `context_pages=2`: pipe:2259-2262, 2338-2365.
- Placeholder / empty / `ocr_images` keys: pipe:4326-4334, 2311-2312, 2377-2381, 886-887. `unread_pages` appears only when non-empty; `skipped` can be `{}` (P4).
- Shapes are OCR'd on a page without a text layer: pipe:2209-2212 (plain pictures are still skipped).
- Furniture pictures: first copy kept, later copies retyped (±4 pt, ≥3 pages and >half; header if the middle is in the top half): pipe:1104-1144.
- Routing counts only XObjects shown whole and not small: images:309-318, pipe:1978-1987.

**What is cached (341-356)**: PARTLY.
- Answers are cached, including empty ones and refusals; failed and firewall-unresolved answers are not: TRUE. Omitted: answers the `non_content_judge` could not screen are not cached either (cache:195-213).
- Provider refusal kept 10 min, not extended by hits: TRUE (cache:37-44, 216-220).
- `cache_dir` skipped for failed / unread / provider_refused, logged at INFO: TRUE (loader:733-742, 1290-1310). Also skipped when a judge could not answer every question (loader:737-738).

**Text-layer quality gate (358-399)**
- Detector rules, prominence `(size/body)^2` capped at 4^2, garbled = ≥3 glyphs and ≥10 %: TRUE (strategy:180-386).
- 388-389 "With an OCR provider": PARTLY. It needs OCR on (provider AND `ocr_images=True`). With `ocr_images=False` the page is kept, with action `kept` and a warning (pipe:2099-2120).
- 390-395: TRUE (P4 garbled.pdf).
- 396-399 test-table.pdf: TRUE (P5: 38.3 pt title reads `�…ations`, coverage 0.151, garbled 0.165/17 glyphs, route image/illegible_text_layer).

**Legibility judge (401-432)**: TRUE.
- Contract: strategy:408-443.
- ≥20 characters, not already flagged: strategy:72, 402-405.
- At most once per page: pipe:1936-1947.
- Only with OCR on: pipe:1930, 2033-2035, 2234.
- Only for pages that would keep their text: pipe:2033.
- Threshold 0.7: strategy:71, 490.
- `legibility_judge=` is a loader argument: loader:67, 183.

**Invisible text (434-478)**: TRUE (routing:488-598, 739-756; strategy:98-123; pipe:2122-2126). Minor: the doc says "older than 1.27" while the declared floor is 1.27.1 (compat:1-12).

**What the output records (480-501)**
- `ocr_routing`: TRUE, exact shape (pipe:2087-2094; P4).
- `text_layer_quality`: PARTLY. Entries are `{"page","legible":false,"garbage_ratio"(3 dp),"garbage_glyphs","judge_legibility","action"}` (pipe:2105-2112); the doc omits `page` and `legible`.
- `hidden_text`: TRUE, `[{"page","chars"}]` where `chars` counts non-whitespace characters (routing:668; pipe:2124; P4 chars=44).
- 495-497 empty-document warning: PARTLY. A document with no text at all gets one warning naming no pages (pipe:2128-2132; P4 notext.pdf). Page-naming warnings appear only when some text was emitted (2133-2145). With OCR, "shows content" means coverage ≥5 %, so a blank white scan is named too (P4).
- 499-501 cache key includes the judge and ROUTING_VERSION (=3): TRUE (loader:646-657), plus the boilerplate/non-content judge identities when set.
- Not listed in this section: `tables_count`, `ocr_issues`, `token_usage`, `judge` (see 5.15).

**Layer 2 (503-533)**
- All five functions still exist: `_maybe_route_image_dominant` office:314, `_is_image_dominant` 349, `_pptx_image_signals` 366, `_docx_image_signals` 401, `_process_as_image_dominant` 432. TRUE.
- 507-513 gating: TRUE. docx/pptx only; `ocr_images` AND `extract_images` (the loader sets the latter) AND a provider (office:332-336).
- 515-517 OOXML signals, no rendering, same `decide_doc_strategy`, plain character count: TRUE (office:349-364).
- 519-520 PPTX: PARTLY. Coverage is the union of every visible picture (background picture fill of slide, layout or master; grouped, placeholder and picture-filled shapes; layout/master pictures). Text counts text frames and table cells, grouped included; hidden shapes are excluded (office:366-399).
- 521-524 DOCX: FALSE (outdated). Anchored/floating pictures (`wp:anchor`) are counted as well as inline ones. Text is every rendered `w:t` in the body, tables, content controls, text boxes, headers and footers; deleted revisions and `mc:Fallback` are skipped (office:401-430).
- 526-531: PARTLY. A second gate applies: the converted PDF's own document route (`_pdf_document_route`, office:156-175) must be `image`. Otherwise the file stays native (office:447-460). With `image`, `routed_via='pdf'` is recorded (office:474).
- 531-533 "never raises; falls back cleanly": never raises and falls back, TRUE. The fallback is now recorded: `metadata.extra.routed_via="native"` plus `route_error` (exception text ≤500 chars, WARNING) or `route_reason` (`"converted PDF routes text"` / `"converted PDF route unavailable"`, INFO). Sources: office:337-347, 455-460, 301-302. Files the OOXML check passes over get no key.

**Layer 3 (535-627)**
- Default task `auto`; master rule; triple gate; four policies; real-table/code rule: TRUE (ocrbase:253, 103-138).
- 602-607 context gate (0.7 / "high"): TRUE (ocrbase:103-109). Unstated: without context every auto request carries a no-context clause forcing VERBATIM (ocrbase:142-150), and withholding without context is a violation (schema:1962-1965). With the default `context_pages=0`, nothing is ever withheld.
- 612-616 `router_invariants` "intended as a CI / eval assertion": PARTLY. The function still exists (schema:1969). Its withholding subset `withholding_violations` (schema:1931-1966) is enforced at run time on every structured result (openai.py:1048, vertex_ai.py:749); violators are redone verbatim at the cost of one extra call.
- 618-627 listed invariants: TRUE (schema:1931-2040).

**Layer 4 (629-657)**: TRUE.
- Renders only: pipe:2318-2325.
- `[see table]`: ocrbase:234.
- Token coverage (Latin/numeric words; CJK runs ≥2): schema:1903-1914.
- `_SYNTH_COVERAGE_MIN = 0.85`: schema:1900.
- `<!-- raw-verbatim-tail\n…\n-->` with missing tokens sorted: schema:1704-1707.
- Below 0.85, the verbatim rendering is used: schema:1709-1735.

---------------------------------------------------------------------------------------------------------------------

## 2. docs/formats.rst, PDF section (121-221)

- 124-125 advanced pipeline; tables always on: TRUE (pdf:84-96; `extract_tables` ignored).
- 126-127: FALSE.
  - The loader passes `ocr_images` as `PDFProcessor.process(use_ocr=)` (loader:677-680), which hands it to the pipeline as `ocr_images` (pdf:80-88). The pipeline has no `use_ocr`.
  - With a provider, `ocr_images=True` turns extraction on in the loader (loader:630-634) and in the pipeline (pipe:853-855). Without a provider it is ignored with a warning (pipe:849-852).
- 127-130 fallback to plain PyMuPDF: TRUE, the fallback still exists (pdf:126-129, 177-258): 300 DPI (pdf:219), and only pages without text are OCR'd (pdf:216). Probe P6 with numpy blocked logs "Advanced PDF pipeline not available, falling back to basic processing"; content starts `### Page 1`; `json_content` is None. Caveats:
  - It is caught for any ImportError during the advanced run, not only at import.
  - Output: `### Page N`, ALL-CAPS lines bolded, lines joined, OCR text in ```` ```xml <ocr_result> ```` fences (pdf:204, 226-230, 269-271); no tables, chrome handling or json_content.
  - It calls `self.ocr.process_image`, which only the loader's OCR wrappers define (usage:307, cache:991).
  - Blocking the import also sends the Office processor to basic parsing (P6).
- 132-134 `pymupdf>=1.27.1`: TRUE (pyproject.toml:47; compat:1-12).
- 135-140 fallbacks, said once per process: TRUE (compat:25-33; images:130-134; routing:716-721; pipe:4242-4245). P7 with PyMuPDF 1.26.4: one WARNING (TEXT_CLIP) and one INFO (TableFinder.textpage) across two conversions.
- 142-149 stdout: TRUE. The CLI calls `no_recommend_layout` and `set_messages`/`set_log` to the `pymupdf` logger (cli:16-21, 109, 425; compat:36-54). Levels: WARNING by default, DEBUG with -v, ERROR with -q (cli:24-41). The library leaves PyMuPDF alone: P1 printed "Consider using the pymupdf_layout package…" to stdout.
- 159-164 headings: TRUE (pipe:955-984, 1026-1055, 3760-3764, 3933-3947, 4545-4551; P2). Levels are capped at 6 (pipe:1055).
- 165-170 lists: TRUE (pipe:181-218, 343-425, 3963-4029; P2).
- 171-173 captions: TRUE (pipe:221-228, 3766-3779, 3831-3833, 4563-4567; P2).
- 174-177 superscripts/footnotes: TRUE (pipe:502-508, 554-585, 3060-3065, 3667-3681, 3815-3827, 4584-4593; P2). Caveat: the body mark stays `^1^`, never `[^1]`, so the `[^1]:` definition has no reference.
- 178-184 text (ligatures, hyphens, CJK, emphasis): TRUE (pipe:172, 428-499, 1003-1024, 3874-3907, 524-585; P2).
- 185-190 escaping; kept chrome copy escaped; blank line between list kinds: TRUE (md:1-125; pipe:2789-2797, 4003-4022; office_advanced_pipeline.py:3189-3259; P2).
- 194-196 /Rotate pages as displayed: TRUE (pipe:2511-2566).
- 197-202 columns, spanning items, figures without extraction, headers open / footnotes close: TRUE (layout:101-132, 297-352; pipe:2616-2651; P3). Edge items go to the start or end by the half of the page they sit in (layout:224-226).
- 203-204 sidebars read after: TRUE (layout:51-52, 472-480).
- 205-217 evidence rules: TRUE (layout:40-50, 272-294, 355-460). Omitted: pages with >400 items keep top-to-bottom order (layout:54, 113), and ordering moves whole MuPDF blocks, so columns MuPDF grouped into one block stay interleaved (P3 order.pdf).
- 218-220 sideways text is never a heading or title and does not set the largest size: TRUE (pipe:2694-2728, 3096, 3834). It can still be classified caption, list or footnote (pipe:3815-3843).

## 3. docs/tables.rst, PDF path (37-158)

- 40-42, 44-56 cell text, 3 pt word gap, glyph band from the baseline: TRUE (tables:50-51, 92-133, 278-301, 503-529).
- 58-79 overprint (0.8 size, baseline within 0.35 size, subsequence, ≥ half as long), kept overlaps, hidden layer (≥ half of glyphs): TRUE (tables:157-231, 304-340).
- 81-89 merged cells from drawn boxes (1 pt), shrunk: TRUE (tables:52, 390-442).
- 91-103 grids kept if ≥2×2 with text, no overlap, <85 % of the page, or ≥2 ruled edges (strokes / fills ≤2 pt): TRUE (tables:534-637).
- 105-112 text-strategy gate: TRUE. Gap ≥ max(8 pt, 0.8 × word-box height, about one font size); a column of pieces with ≥2 numbers making ≥60 % (tables:805-839).
- 113-128 3×3 (2 columns with booktabs rules), numeric column, no crossing, ≥50 % filled, ≤¼ single-cell rows, 70 % ≤40 characters, TOC: TRUE (tables:849-986).
- 129-132: PARTLY. The rule is still enforced (tables:1007-1022, 1078), but its stated reason, "The text output skips each text block that touches a table", is false since #17. The text path drops only spans whose anchor lies inside a table box (pipe:2763-2765, 2813-2822; CHANGELOG:93-96, 332-334).
- 134-136: TRUE (tables:894-918).
- 137-140 "Measured on 1,391 pages … 18 tables, all real": UNVERIFIABLE from the repo.
  - No committed corpus, script or result exists. The text entered only `docs/tables.rst`, in `057341d` (#15), and the commit message does not mention it.
  - Its source is the PR #15 body draft outside the repo, `/Users/haoliangwen/code/.executors/d2m-fix-pdftable.pr.md:49-52`: /tmp scripts over 311 local files / 1,379 pages plus adversarial layouts (= 1,391), re-measured at `1e03305` (review round 1).
  - `pdf_tables.py` is unchanged since #15 (`git log 057341d..HEAD -- doc2mark/pipelines/pdf_tables.py` is empty).
- 142-155 header rows; continuation (top/bottom 8 %, running-header test, header carry): TRUE (tables:54, 708-717, 779-788, 1088-1187).
- 157-158 table box (with header) is what the text path skips: TRUE (tables:670-672; pipe:2813-2822).

## 4. README.md "Clean PDFs" (403-412), plus the same claim at 35-36

- 405-408 "detects content that recurs in the top/bottom margin zone … and drops it automatically": PARTLY (misleading under verbatim first).
  - The zone is the top/bottom 12 % (pipe:63, 1551-1557).
  - Only bare page numbers and lines repeating a title or heading of the document leave every page (pipe:1432-1460).
  - Other qualifying running lines keep their first copy. Numbered labels and weaker repeats keep every copy (P1).
  - "Dropped" copies are retyped, not deleted.
- 410 "on by default for multi-page PDFs": PARTLY. It is on without configuration, but needs ≥3 pages (pipe:64, 1306-1308, 1390-1395). P1: a 2-page PDF keeps every copy of the header, the disclaimer and "Page N of 2"; exactly 3 pages works.
- 410-412 "applied before … markdown rendering and RAG chunking, so the repeated chrome never reaches your output or your vector store": PARTLY.
  - Detection runs before `pdf_to_markdown` (pipe:889-897). `pdf_to_markdown` (4533-4535) and `chunk_content` / `ProcessedDocument.get_chunks` (chunker:155-157; core/base.py:134-146) skip `text:header` / `text:footer`.
  - But first copies stay in Markdown and chunks by design, and every removed copy stays in `json_content` and in JSON output as a raw-text `text:header` / `text:footer` item (pipe:2789-2797; P1: 10 such items in 4 pages).
  - Chunk `content_types` can still list `text:header` / `text:footer` (chunker:233).

---------------------------------------------------------------------------------------------------------------------

## 5. FACT SHEET: PDF output (current code)

### 5.1 Flow and flags
- `UnifiedDocumentLoader.load(extract_images=False, ocr_images=False)` calls `PDFProcessor.process(extract_images, use_ocr=ocr_images)`, then `pdf_to_simple_json(ocr_images=use_ocr)`, then `pdf_to_markdown` (loader:578-584, 677-680; pdf:80-96).
- OCR on = a provider AND `ocr_images=True`. That implies `extract_images` (loader:630-634; pipe:849-855).
- Without OCR, `extract_images=True` gives one base64 `image` item per shown placement, written `![Image](data:<mime>;base64,…)` (pipe:4346-4368, 4595-4598). With OCR, pictures give their OCR text instead.
- Tables are always extracted. `table_style` defaults to `minimal_html` (pdf:90; tables.rst).
- `metadata`: `filename`, `format=pdf`, `size_bytes`, `page_count`, `word_count` (pdf:134-151). `word_count` counts every `text:*` item, including removed header/footer copies.

### 5.2 JSON items (`json_content`, in reading order)
- Every item has `type`, `content`, `page` (1-based) and `position_y`. `position_y` is the top of the PyMuPDF block; 0.0 for a page-render OCR item; the page height for the verbatim tail (pipe:2411-2431, 2478-2507).
- Types (pipe:806-835):
  - `text:title` (`level` 1) and `text:section` (`level` 2-6).
  - `text:normal`, `text:list`, `text:caption`, `text:footnote`.
  - `text:header` / `text:footer`: removed chrome copies, raw text.
  - `text:image_description`: `<image_ocr_result>…</image_ocr_result>`.
  - `table`: HTML with spans, or a pipe table.
  - `image`: base64 plus `mime_type`.
- Text content has no trailing newline (P2).

### 5.3 Markdown assembly (pipe:4506-4606)
- One block per item, separated by blank lines. `text:header` / `text:footer` are skipped.
- `<!-- page N -->` goes before the first emitted item of each later page.
- Headings are `#` × `level`. Captions are `*line*` per line. OCR text is unwrapped. Tables are written as-is.
- Footnotes become `[^N]: …`.

### 5.4 Headings
- A block can be a heading when it has ≤3 lines and ≤100 characters, is not sideways, does not start mid-row, is not side-by-side labels, and is not a bulleted list or ≥2 items (pipe:3834-3839, 3753-3758, 3909-3931).
- It must not look like body text: ≥2 letters; no sentence punctuation, form-field shape or trailing comma; ≤120 characters (pipe:4149-4156, 4179-4222).
- Layout strength (pipe:4128-4140):
  - 3: ≥1.15 × body size, or bold and ≥1.05 ×.
  - 2: bold at ≥0.95 ×, or ≥1.05 × and all caps or coloured.
  - Colour alone counts only with an outline number.
  - Length: ≤80 characters Latin, ≤24 CJK (pipe:4221).
- Body size is the character-weighted median of the page without superscripts, table cells or sideways text; pages with <300 characters use the document's running-text median (pipe:3086-3159).
- Title `#` (pipe:955-984, 1885-1920, 3760-3764, 4142-4147):
  - At most one, from the text layer.
  - Candidates are on the first page with text (chrome lines do not count), ≥0.85 × that page's largest size, and ≥1.15 × body (or bold / all caps and ≥1.05 ×), <120 characters, ≤3 lines.
  - The largest wins only if no other non-repeated heading of the document is within 0.25 pt of it; otherwise it becomes a section.
  - OCR'd page text can bring its own `#` (schema:1711-1715).
- Levels (pipe:1026-1055): sections are ranked by font-size tiers (0.25 pt apart) and decimal outline depth (`1.2` is deeper than `1`). Each is one level below the nearest open section that ranks above it: no skipped levels, maximum 6.
- A heading is one line with no emphasis; a trailing `#` is escaped (pipe:3943-3947; md:105-111).

### 5.5 Lists (pipe:181-218, 343-425, 3963-4029)
- Always list items: glyph bullets (`•◦▪▫●○■‣⁃∙·➢➤►▶❖◆◇✓✔➔`, Word PUA bullets) and `1.` / `1)` (1-2 digits).
- `-` `*` `+` `–` `—` count only next to another bullet.
- Letters, roman numerals, `(1)`, `一、` / `壹、` / `（一）` and `1.2` count only in a sequence with a neighbour. A lone `A. Smith` or `E. coli` stays text.
- Output:
  - Bullets become `- `; `-`, `*` and `+` keep their character.
  - Meaningful bullets (`✓✔➔➤►▶➢–—`, Wingdings ✓ ➢ ➔) stay after `- ` (`- ✓ Approved`).
  - Numbers keep their number and delimiter.
  - Enumerations stay in the text after `- ` (`- a) …`).
  - Nesting follows indentation (±2 pt). Continuation lines are indented under the item.
  - A blank line goes before a change of list kind.

### 5.6 Captions
- A numbered label (Figure/Fig./Table/Tab./Chart/Graph/Exhibit/Plate/Scheme/Image/Photo/Diagram/Illustration/Map + number, roman numeral or letter; 圖 / 图 / 表 / 附圖 / 附表 + number) makes a caption anywhere, up to 6 lines (pipe:221-226, 3831-3833). `Table 3 shows …` is not one.
- Otherwise text attached to an image or table is a caption when it has ≤4 lines, ≤300 characters and ≤1.02 × body size, and is italic, or ≤0.92 × body, or starts with Source / Note / Credit / Photo / 資料來源 / 註 / 說明 / 備註 …. It must be within max(14 pt, 1.5 × body) above or below, overlapping or centred within 100 pt (pipe:228, 3766-3779, 3043-3056).
- Written as `*line*` per line, with no inner emphasis (pipe:3948-3949, 4563-4567).

### 5.7 Superscripts and footnotes
- Superscripts (pipe:3060-3065, 3667-3681, 569-570):
  - A superscript is a PyMuPDF-flagged span of ≤6 characters, or a span of ≤6 characters at ≤0.8 × the line's size raised ≥0.2 × that size.
  - Written `^x^`, with `^` inside escaped as `\^`.
  - Ordinals `st/nd/rd/th` after a digit and `®™℠©` stay inline (pipe:237, 502-508).
- Footnotes (pipe:3815-3827, 4584-4593, 3950-3960):
  - A `text:footnote` block starts below 85 % of the page height and either is <0.9 × body and starts with digits / `*` / `†` / `‡` / `§` followed by `.`, `)` or a space, or starts with a raised number and is <0.95 × body.
  - It becomes `[^N]: …` when it matches `^(\d+)[.)\s]+`, with all its lines kept. Otherwise it is escaped plain text.
  - The body mark stays `^N^`.

### 5.8 Line breaks, hyphens, CJK, ligatures, emphasis
- Paragraph lines stay separate source lines (soft breaks), except for hyphen and CJK joins (pipe:428-452, 3933-3961; P2).
- Hyphen join (pipe:428-499, 3855-3872):
  - When: the line ends with `-`, U+2010 or U+00AD after a letter or digit; the next line wraps on and starts with a letter or digit; it is not a list marker; and the next word is not `and` / `or` / `to` / `nor`.
  - The lines are joined keeping `-` (U+00AD is written `-`).
  - The hyphen is removed only if the joined word appears unhyphenated elsewhere in text or tables and the hyphenated form appears only at such line ends (pipe:1003-1024). P2: `top-down`, `investment`.
- CJK join, without a space, when all of these hold (pipe:3874-3907, 449-451):
  - Both sides are CJK, and the next line is not a `label：`.
  - The previous line reaches the block's right edge within the next line's first token + 0.9 em.
  - The paragraph goes on: the next line also wraps, or ends a sentence, lead-in or bracket, or continues a joined run.
  - In a 2-line block the first line must be running text (≥30 units).
  - In headings CJK lines always join, except after a bare `第一章`-style number.
- Ligatures U+FB00-FB06 are expanded (pipe:172).
- Emphasis: `**`, `*` and `***` go around exactly the styled runs, never inside a Latin word or where CommonMark cannot close them (pipe:524-585).
  - Bold comes from the font flag, the synthetic-bold char flag, or a font name with bold / black / heavy / semibold / demibold.
  - Italic comes from the flag or a font name with italic / oblique (pipe:173-175, 3067-3076).

### 5.9 Escaping (`doc2mark.utils.markdown`: PDF text, Office text, kept chrome copies)
- `<` becomes `&lt;` before an ASCII letter, `/`, `!` or `?` (`x < 5` stays) (md:59-60).
- `&` becomes `&amp;` only before an entity-like `&name;`, `&#N;` or `&#xH;` (md:28, 61-62).
- `\` is doubled before ASCII punctuation, at the end, and before a line break (md:63-64).
- At a line start, these get a backslash (md:30-36, 77-90):
  - ATX `#`…`######` + space.
  - `>`.
  - `-` / `+` / `*` + space.
  - `N.` / `N)` + space, escaped as `1\.`.
  - Code fences.
  - `[x]:`.
  - Lines made only of `-=*_`.
- C0 controls are removed; CR, VT and FF become newlines (md:26-27, 41-43).
- Not escaped: inline `*`, `_`, backticks and `|` (md:20).
- Retyped `text:header` / `text:footer` items keep raw text (pipe:2793-2796).
- OCR text is escaped and sanitized at the OCR boundary; sanitized table HTML is the only live HTML (schema:1084-1160).

### 5.10 Reading order (layout:1-31; pipe:2511-2566)
- The default is top to bottom by block top. /Rotate pages are ordered as displayed.
- A page is read in columns only when all of these hold:
  - A gutter ≥ max(6 pt, 0.6 × size) separates items side by side (layout:272-294).
  - Running text (≥3 lines ≥10 ems wide) stands beside text on the other side (layout:40-41, 447-460).
  - Items across the gutter sit only between items; at most 2 pull-quote islands are allowed (layout:365-396).
  - The sides are not row-aligned: ≥50 % of a short side's items, or ≥75 % and ≥2 on both running sides, start level (layout:47-50, 413-444).
- Spanning items cut the page into bands: titles, captions, pictures and drawings (drawings clustered, ≥24 pt), also when images are not extracted (layout:297-352, 483-523; pipe:2616-2651).
- A sidebar (median line width <0.6 × the widest column, fewer lines) is read after its band (layout:472-480).
- Edge items (running headers and footers, including kept first copies, and footnotes) open or close the page by the half they sit in (layout:207-242; pipe:2539-2540, 2797).
- A picture with text over it is a background, not a column item (layout:183-195).
- Pages with >400 items keep top-to-bottom order (layout:54). A layout error also falls back to top-to-bottom (pipe:2563-2565). The result is always a permutation (layout:131-132).
- MuPDF blocks are never split (P3).
- Sideways blocks are never headings or the title and do not set the page's largest size (pipe:2694-2728).

### 5.11 Running headers/footers (verbatim first; pipe:1259-1517)
- Needs ≥3 pages (1306-1308, 1390-1395; P1).
- A candidate line has its centre in the top/bottom 12 % of the displayed page (63, 1551-1557). Its slot is the same distance from the page edge ±4 pt (66, 1619-1630). The slot must hold evidence on ≥3 pages and on >½ of all pages (1390-1395).
- Evidence, either (1343-1356, 1632-1696):
  - (a) The same normalised text (NFKC, case-folded, with a letter) on ≥3 pages, or on a page ≤2 away (per chapter). Heading-sized text (≥1.15 × body) must be on >½ of the pages.
  - (b) A page number: a masked template with one number per page following the page order at one offset on ≥3 pages, ≤1.5 × body size, and N ≤ M.
- The line must sit at the page edge (1698-1751):
  - Rows are peeled from the edge while every line in the row qualifies (numbered labels may ride along).
  - The innermost row needs a gap ≥ max(3 pt, 0.7 × its height) before the content; bare page numbers need none.
  - It must not be a table header row (≥3 cells aligned with one of the next 3 rows).
  - P1: a date on the same row keeps `ACME | Page N` on every page.
- Outcome:
  - **Every copy removed** (`text:header` / `text:footer` on all pages), 1397-1460:
    - Bare page numbers: only page words (page / pg / pp / p / seite / página / pagina / 第 / 頁 / 页 / of / von / de / sur / di / van / 共) around the number. Examples: `3`, `- 3 -`, `(3)`, `Page 3 of 12`, `3/12`, `p. 3`, `iv`, `第 3 頁`, `Seite 3 von 12`, and `1001`-style numbers that count with the pages. Also the same printed form on a cover or contents page outside the run.
    - Lines repeating a title or heading of the document: heading-sized on or before the first copy's page; a mid-page heading counts only on that page and the 2 before it. P1, title variant.
  - **First copy kept**, later copies retyped: every other qualifying line, for example a statement title, unit note, disclaimer, letterhead, `ACME | Page N` or a per-chapter header (P1). A body-sized first copy is plain escaped `text:normal`; a heading-sized one is classified like content (1502-1517, 2789-2797).
  - **Every copy kept**:
    - Numbered labels without a page word (`3 | ACME Corp`, `Lesson · 3`), as plain text.
    - Dates such as `15/03`.
    - Repeats on <3 pages; lines not at the edge or not set apart; repeated table header rows.
    - Everything in 1- and 2-page PDFs (P1).
- The same "first copy kept" rule retypes tables repeated in the band (same text, ±4 pt, ≥3 pages and >½; 1057-1102) and pictures with the same OCR text at the same place (1104-1144). Render OCR and placeholders are never retyped.
- An optional `boilerplate_judge` (≥0.5) can thin ambiguous repeats to their first copy; it never removes the last copy (609-652, 1370-1500).
- Chrome lines never become headings, list items or footnotes (2789-2797). The verbatim tail never re-adds them (2424).
- Retyped copies stay in `json_content`; `pdf_to_markdown` and the chunker skip them (4533-4535; chunker:155-157).

### 5.12 Hidden (invisible) text
- Invisible means render mode 3/7 (char_flags neither filled nor stroked) or alpha 0 (routing:107-115).
- Each invisible span is classified (routing:488-598):
  - A copy of the painted text on its line is dropped and not counted.
  - Over a picture it is the picture's text, unless <1 % of its pixels are >12 grey levels off the background at 100 DPI, with the page rendered without its text.
  - Over other ink it is text only if the ink is glyph-like: ≥5 % ink at 48 levels, spread over ≥30 % of rows and columns, ≥4 transitions per inked row, with crossing rules and grids erased.
  - Otherwise it is hidden.
- Hidden text is left out of paragraphs and table cells; the table finder reads a redacted copy (routing:696-756). It is recorded in `hidden_text` with a warning (pipe:2122-2126; P4).
- Text over a picture whose OCR returned text in this run is dropped: the OCR replaces it (routing:748-751; pipe:2056-2067).
- If a page cannot be checked, its invisible text is kept and a warning is logged (routing:545-548, 657-660).
- Without invisible-only redaction (PyMuPDF <1.27.1), hidden text touching painted text can reach table cells (routing:716-721).
- A searchable scan gives one text: the OCR with OCR on, the invisible layer without.

### 5.13 Per-page OCR routing (OCR on only; strategy:148-175, 514-540; pipe:1992-2037)
- Document route is `image` iff mean coverage ≥0.55 AND mean `text_weight` <200.
  - `text_weight` counts non-whitespace legible characters: CJK ideograph = 3, kana or hangul = 2, garbage = 0 (strategy:274-322).
- Page rules, first match wins:
  1. `searchable_scan` → image: invisible layer, coverage ≥0.55, painted weight <200.
  2. `illegible_text_layer` → image: ≥3 garbage glyphs and ≥10 % prominence-weighted, or judge <0.7.
  3. `no_text_layer` → image: weight <50 and uncaptured pictures ≥5 % or other ink ≥0.1 %.
  4. In a text document: `image_dominant_page` → image when coverage ≥0.825 and weight <100.
  5. In an image document: `dense_text_page` → text when coverage <0.275 and weight ≥300.
  6. Otherwise the document route.
- Image route (pipe:2405-2449):
  - A 150 DPI PNG goes to OCR with `page_markdown` synthesis; its answer is the page's content.
  - Override reasons 1-4 also append the legible painted lines the OCR missed as one `text:normal`.
  - An empty answer falls back to the text layer, plus `[page N: OCR returned no content]` and `unread_pages` if the render shows ink.
  - A failed render takes the text route with `[image: OCR unavailable]` pictures.
- Text route: text layer + tables + OCR of the pictures (5.14).
- Without OCR every page takes the text route. A garbled page is kept and reported `kept`, with a warning.

### 5.14 Pictures on the text route (images; pipe:2180-2275, 4289-4344)
- Shown means the whole picture is visible, or ≥12 pt on both sides. Images <12 px are skipped. Tiles (≤1 pt apart, ≤5 % overlap) are joined.
- Inline, tiled and cropped pictures are rendered at 150-300 DPI (≤16 MP) without their overlaid text.
- Classes and sending rules:
  - *plain* is never sent.
  - *text* is always sent.
  - *shapes* is sent only when not small (<10 % of the page both ways, or <48 pt on both sides), or when the page has no text layer.
- Each distinct content is one request, per page when `context_pages=2`. Its text is emitted at every place the picture shows.
- A failed or missing answer leaves `[image: OCR unavailable]`; an empty answer leaves nothing.
- Furniture repeats: see 5.11.

### 5.15 `metadata.extra` keys a PDF can carry

| Key | Present when | Exact shape |
|---|---|---|
| `ocr_routing` | OCR on (pipe:2087-2094) | `{"document_route": "image"\|"text", "overrides": [{"page": int, "route": "image"\|"text", "reason": "searchable_scan"\|"illegible_text_layer"\|"no_text_layer"\|"image_dominant_page"\|"dense_text_page"}]}`; only pages whose route differs |
| `ocr_images` | OCR on (pipe:867-869, 2311-2382, 886-887) | `{"ocr_requests","page_renders","batches","largest_batch","empty","failed": int, "skipped": {"not_shown": int, "no_content": int} or {}, "unread_pages": [int]}`; `unread_pages` only when non-empty (1-based) |
| `text_layer_quality` | any garbled page, OCR on or off (pipe:2099-2116) | `[{"page": int, "legible": false, "garbage_ratio": float(3 dp), "garbage_glyphs": int, "judge_legibility": float\|null, "action": "ocr"\|"kept"}]` |
| `hidden_text` | any hidden text (pipe:2122-2124) | `[{"page": int, "chars": int}]` (non-whitespace characters) |
| `tables_count` | ≥1 table item (pdf:104-110) | int |
| `ocr_issues` | via the loader, when any refused / failed / withheld / suspected image or an OCR call raised (usage:72-75, 174-208, 267-292; loader:713-718) | `{"refused","provider_refused","failed","withheld","suspected": int, "errors": [str≤5], "locations": [{"issue": "refused"\|"failed"\|"withheld"\|"suspected", "image": n, "page": p}]}`; `locations` omitted when empty; an engine error raises `ProcessingError` instead |
| `token_usage` | via the loader, when OCR reported tokens (loader:704-710) | `{"input_tokens","output_tokens","total_tokens": int}`; renamed `token_usage_cached` on a `cache_dir` replay (loader:1344-1358) |
| `judge` | via the loader, when a judge with `begin_document`/`end_document` (TypeSafe) asked something or failed (loader:536-563) | `{"name","model","asked","cached","fresh","failed","input_tokens","cost_usd"}` |
| `routed_via` (+`route_reason`/`route_error`) | DOCX/PPTX only (office:301-302, 340-347, 455-474) | `"pdf"`, or `"native"` with `route_reason: str` or `route_error: str` |

### 5.16 Warnings (pipe:2099-2145 unless noted)
- A garbled layer kept without OCR.
- Hidden text left out.
- An empty document: one warning, no page list.
- Some text, OCR off: pages that need OCR, named.
- Some text, OCR on: pages with coverage ≥5 %, uncaptured content or an invisible layer that emitted nothing, named.
- Render OCR empty while a text layer exists (2443-2445).
- Missing picture OCR result (4329).
- PyMuPDF capability fallbacks, once per process (compat:25-33).

### 5.17 PyMuPDF floor, fallbacks, stdout
- `pymupdf>=1.27.1` (pyproject.toml:47). With an older PyMuPDF (compat:1-54):
  - TEXT_CLIP missing: WARNING; pictures are measured without clips.
  - Invisible-only redaction missing: WARNING, only when hidden text touches visible text.
  - `TableFinder.textpage` missing: INFO.
- If the pipeline cannot import, basic `fitz` processing runs (pdf:126-129, 177-258; see section 2).
- The library leaves PyMuPDF's stdout prints alone. The CLI turns the `pymupdf_layout` recommendation off and logs MuPDF messages to stderr (cli:16-21).

---------------------------------------------------------------------------------------------------------------------

## 6. Probe evidence (scripts in /tmp/d2m-docs-audit-probe; PyMuPDF 1.27.2 unless noted)

- P0 constants and examples: `decide_doc_strategy(0.92,35)`, `(0.70,900)`, `(0.10,1200)` and `(0.95,900,0.5)` gave `image text text image`; the constants match section 1.
- P1 `probe_chrome.py`. 4-page PDF: header `ACME Annual Report 2025` (9 pt, y=40), disclaimer (y=790), `Page N of 4` (y=815).
  - p1: header and disclaimer are `text:normal`; `Page 1 of 4` is `text:footer`.
  - p2-p4: all three are `text:header` / `text:footer`.
  - Markdown counts: header 1, disclaimer 1, `Page N of` 0.
  - 3 pages: same result. 2 pages: everything is `text:normal`, Markdown keeps all copies.
  - Header equal to the title: `text:header` on every page, title kept once as `text:title`.
  - `N | ACME Corp`: `text:normal` on every page.
  - `ACME | Page N` alone: first copy kept, rest `text:footer`. `15/03`…`18/03` and `Lesson · N`: kept on every page.
  - `chunk_content(overlap=0)`: header 1, disclaimer 1; union of `content_types` includes `text:footer` and `text:header`; 10 header/footer items remain in `json_content`.
  - stdout showed "Consider using the pymupdf_layout package…".
- P2 `probe_md.py` Markdown output:
  - `# Quarterly Results`, `## 1 Introduction`, `### 1.2 Scope` (levels 1/2/3).
  - `top-down plan that needs investment … the investment case is\nset out below…`.
  - `- First item\n- Second item`, then `1. One step\n2. Two steps`.
  - `\# of patients rose, and &lt;img src=x> is printed text.`
  - `E = mc^2^ … $1.2bn^3^ … ranked 1st by ACME™.`
  - `*Figure 3: Revenue by region*`.
  - `[^1]: Source: company filings, audited.`
  - A CJK paragraph: the line 2→3 break joined; the 1→2 break kept, because line 1 stops 2 characters short of the right edge (by the rule).
- P3 `probe_order3.py`. Title, left column, right column, then sideways arXiv stamp (18 pt) as `text:normal` (not a heading), then the closing line. With columns written row by row (`probe_order.py`), MuPDF made one block and the rows stayed interleaved.
- P4 `probe_ocr.py` with a fake OCR provider:
  - Empty answers: `ocr_images={"ocr_requests":2,"page_renders":2,"batches":1,"largest_batch":2,"empty":2,"failed":0,"skipped":{},"unread_pages":[1]}`. Markdown is `[page 1: OCR returned no content]`. The blank page has no marker but is still warned as "shows content".
  - Failed answers: `failed:2`, no `unread_pages`, Markdown `[image: OCR unavailable]`.
  - Garbled + hidden, no OCR: `text_layer_quality=[{"page":1,"legible":false,"garbage_ratio":0.614,"garbage_glyphs":9,"judge_legibility":null,"action":"kept"}]`, `hidden_text=[{"page":1,"chars":44}]`, and the hidden line is absent from the Markdown.
  - Scan with no OCR: `extra={}`, one warning "no text could be extracted from its 2 page(s); …" naming no pages.
- P5 sample_documents/test-table.pdf: coverage 0.151, weight 1256, garbled (0.165, 17 glyphs), route `('image','illegible_text_layer')`, title span 38.3 pt `'�����������������ations'`.
- P6 `sys.modules['numpy']=None`: WARNING "Advanced PDF pipeline not available, falling back to basic processing" and "Advanced Office pipeline not available"; content `'### Page 1\n\nACME Annual Report 2025 Section 1 overview …'`; `json_content` is None.
- P7 PyMuPDF 1.26.4 (`teamsync-backend/.conda` python3), two conversions: one WARNING "has no TEXT_CLIP (PyMuPDF 1.27.1+) …" and one INFO "has no TableFinder.textpage …". `no_recommend_layout` is absent in 1.26.4.
