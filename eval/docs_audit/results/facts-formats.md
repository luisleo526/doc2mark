# doc2mark non-PDF formats: fact sheet and claim verdicts

Audited code: `doc2mark-d2m-docs-audit` at `becb74b` (== origin/main). Code citations are
`file:line` relative to `doc2mark/` (e.g. `core/loader.py:620`); `OP` = `pipelines/office_advanced_pipeline.py`.
Doc citations are the committed HEAD versions (`git show HEAD:<file>`); the worktree gained uncommitted
docs edits from another process during this audit (README, quickstart, new docs/*.rst): not mine, not audited.

Evidence tags: **(code)** read only; **(probe)** confirmed by running the worktree from
`/tmp/d2m-docs-audit-probe` with `PYTHONPATH=<worktree>` and an assertion on `doc2mark.__file__`.
Probe environment: Python 3.11.14, python-docx 1.2.0, python-pptx 1.0.2, openpyxl 3.1.5, Pillow 11.3.0
(native AVIF), bs4 4.14.2, markdownify and PyYAML installed (blocked via `sys.modules[...] = None` to
simulate a base install), pillow-heif absent, python-magic unusable (no libmagic), LibreOffice 26.2 at
`/Applications/LibreOffice.app` (hidden by monkeypatching `find_libreoffice` for the "missing" cases),
PyMuPDF 1.26.4 (below the 1.27.1 floor; only touched by the Office image route). OCR paths used a
network-free stub `BaseOCR` subclass (modes ok / empty / per-call `OCRError` / `OCREngineError`), plus
`OpenAIOCR` without a key, and `TesseractOCR` without pytesseract. Fixtures were built in /tmp.

---

## 1. Cross-cutting loader behaviour

**Detection.** `load()` calls `_detect_format(file_path)` without `use_mime` (`core/loader.py:624`), so
only the extension counts: `suffix.lower().lstrip('.')` matched against `DocumentFormat` values
(`core/loader.py:1272-1277`), aliases `markdown` -> MARKDOWN and `htm` -> HTML (`:1280-1283`), else
`UnsupportedFormatError("Cannot detect format for extension: <ext>")` (`:1285-1287`). Case-insensitive
(probe `PAGE.HTML`). No extension -> same error with an empty ext (probe). Content is never sniffed: a
wrong extension goes to the wrong processor (probe: plain text named `garbage.doc` converts "fine").
Rejected near-misses (probe): `.odt .xlsm .ppsx .ndjson .xhtml .jfif .msg`.
`MimeTypeMapper` (maps ODT/ODS/ODP MIME to DOCX/XLSX/PPTX, `.ndjson` to JSONL: `core/mime_mapper.py:20-23,158-159`)
and python-magic (`core/mime_mapper.py:250-257`, only with `use_content=True`) are never used by `load()`;
the `[mime]` extra (`pyproject.toml:71-74`) has no effect on loading.
`supported_formats` = every `DocumentFormat` value in enum order (`core/loader.py:1458-1465`, `core/base.py:11-54`):
docx xlsx pptx doc xls ppt rtf pps pdf txt csv tsv json jsonl html xml md eml png jpg jpeg webp tiff tif bmp gif heic heif avif.

**Registry** (`core/loader.py:417-498`):

| Extensions | Processor | Kwargs the loader forwards (`core/loader.py:671-689`) |
|---|---|---|
| docx xlsx pptx | `OfficeProcessor(ocr, table_style)` | extract_images, ocr_images |
| doc xls ppt rtf pps | `LegacyProcessor(ocr)` (no table_style: `:443`) | extract_images, ocr_images |
| pdf | `PDFProcessor` | extract_images, use_ocr=ocr_images, extract_tables=True |
| png jpg jpeg webp tiff tif bmp gif heic heif avif | `ImageProcessor(ocr)` | extract_images, ocr_images |
| txt csv tsv json jsonl | `TextProcessor()` | encoding, delimiter (if truthy) |
| html htm xml md markdown | `MarkupProcessor()` | encoding |
| eml | `EmailProcessor(ocr)` (`:487-498`) | nothing |

`output_format` and `show_progress` are never forwarded to any processor.

**`ocr_images` implies `extract_images`** when `self.ocr is not None` (`core/loader.py:630-634`). With
`ocr_provider=None` the flag does nothing for Office/legacy; image files print a note (section 5).

**Output formats** are applied by the loader after the processor ran (`core/loader.py:720-731`):
JSON -> `content = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)`, and if the processor set no
`json_content`, `json_content = [{"type": "text:normal", "content": <markdown>}]` (set after `to_dict()`, so
the JSON string itself carries `"json_content": null`; probe CSV). TEXT -> `content = result.text`
(`core/base.py:153-165`: removes `#+ ` anywhere in a line, `**x**`, `*x*`, `[x](url)`, `` `x` `` only).
Probe: `C# is` -> `Cis`, `\# esc` -> `\esc`; tables, `<!-- page 1 -->`, list markers, `_x_` kept.
String names are case-insensitive; anything else -> `ValueError` (`core/loader.py:566-576`, probe).

**Exceptions from `load()`** (probe for all four):
- (a) missing file -> `FileNotFoundError("File not found: …")` (`core/loader.py:620-621`); not a `ProcessingError`.
- (b) unknown/no extension -> `UnsupportedFormatError` raised before the `try`, so unwrapped (`:624-628`, `:1285-1287`).
- (c) any processing failure -> `ProcessingError("Processing failed: <msg>") from e` (`:746-748`); the cause
  is often itself a `ProcessingError` (e.g. "DOCX processing failed: Package not found at …").
- (d) OCR engine that cannot run -> the usage wrapper re-raises `OCREngineError` inside the `try`
  (`ocr/usage.py:174-201`, `core/loader.py:711-713`) -> `ProcessingError` with `__cause__` `OCREngineError`.
  Real case: `ocr_provider='tesseract'` without pytesseract constructs fine and fails at load with
  "Processing failed: pytesseract is not installed…" (probe).
- `OCRError` (`core/base.py:196`) is raised only inside providers (`ocr/openai.py:676,986,1025`,
  `ocr/vertex_ai.py:515,660,738`, `ocr/tesseract.py:256`); `OCREngineError(OCRError)` (`ocr/base.py:30`)
  by Tesseract (`ocr/tesseract.py:254,420,430,454,470`). Per-image failures inside `load()` never raise:
  they become placeholders plus `metadata.extra["ocr_issues"]` (probe). `except OCRError` around `load()` never fires.
- `ConversionError` (`core/base.py:201`) is raised only by `utils/libreoffice.py:90,100,128,138`; legacy
  re-raises it as `ProcessingError("Legacy format processing failed: …")` (`formats/legacy.py:106-108`) and the
  loader wraps again (probe chain ProcessingError -> ProcessingError -> ConversionError); the Office image
  route swallows it into `extra["route_error"]`. `except ConversionError` around `load()` never fires.

**Default constructor.** `ocr_provider='openai'` (`core/loader.py:36`) builds `OpenAIOCR` eagerly
(`:149-170`, `:285-308`); only its LangChain `VisionAgent` is lazy (`ocr/openai.py:485-487`, `:498-543`).
`ocr/openai.py` imports LangChain in `try/except` (`:40-48`) and never imports `openai`, so import and
construction work on a base install (probe: openai/langchain blocked, no `OPENAI_API_KEY` ->
`loader.ocr` is `OpenAIOCR`, `api_key None`). OCR then fails at call time with `ImportError("LangChain is
required for OpenAI OCR. Install it with: pip install doc2mark[ocr]")` (`:512-517`) or
`RuntimeError("OpenAI OCR requires an API key…")` (`:519-523`), absorbed as placeholders + `ocr_issues`.
Construction also looks up LibreOffice once (`formats/legacy.py:31`; `utils/libreoffice.py:44-60`,
logs `WARNING LibreOffice not found`) and tries to register pillow-heif (`formats/image.py:24-39,52`).

**Loader-added `metadata.extra` keys (any format):** `token_usage` {input_tokens, output_tokens,
total_tokens} when an LLM OCR reported tokens (`core/loader.py:703-710`); `ocr_issues` {refused,
provider_refused, failed, withheld, suspected, errors[, locations]} (`:713-718`; probe e.g.
`errors: ['OCRError: stub per-call failure']`); `judge` (`:552-558`); `token_usage_cached` on a
`cache_dir` replay (`:1343-1358`).

**`ProcessedDocument` fields in practice:** `tables` and `sections` are never set by any processor (only
restored from cache, `core/loader.py:1452-1455`). `images`: Office returns `[]` when nothing was extracted
(`formats/office.py:503-509`), image files `None` (`formats/image.py:276`), text/markup/email `None`.
`json_content` depends on the processor, not the output format (sections below).

**Directory mode:** `batch_process()` globs `*.<enum value>` plus `*.markdown` (`core/loader.py:960-971`):
`.htm` and upper-case extensions (`b.HTML`) are skipped (probe), although `load()` accepts them.

---

## 2. Office: DOCX, PPTX, XLSX (`formats/office.py`, `OP`)

**Path.** `OfficeProcessor.process` (`formats/office.py:245-309`): image route first (section 2.4), else
`UniversalOfficeLoader.load` (`OP:3085-3131`) -> `office_to_markdown` (`OP:3185-3276`). If the pipeline
cannot be imported (`formats/office.py:18-29`) or raises (`:532-546`), a basic python-docx/openpyxl/python-pptx
converter runs (`:548-747`): no `json_content`, markers `## Slide N` / `## <sheet>`, bold/italic runs,
images only if `extract_images` and an OCR provider exists (dicts `filename/size/text`, no bytes, and OCR runs
even without `ocr_images`: `:577-578,648-664,716-732,812-984`). Everything below is the default pipeline.

**Markdown conventions** (`OP:3185-3276`): a marker `<!-- page N -->` / `<!-- slide N -->` / `<!-- sheet N -->`
(label from the file name, `:3204-3220`) whenever `item["page"]` changes; `text:title`/`text:section` with
`level` -> that many `#` (+ list number if any, `:3239-3242`), without `level` -> `#` / `##` (`:3244-3249`);
`text:list` with `marker` -> 4-space nesting, one blank line between items (`:3227-3237`), without marker ->
plain line (`:3250-3252`); `text:caption` -> italic per line (`:3253-3256`); `text:image_description` ->
OCR text with the `<image_ocr_result>` wrapper stripped (`:3260-3266`); `table` verbatim; `image` ->
`![Image](data:image/png;base64,…)` always labelled PNG (`:3270-3272`; probe JPEG bytes `/9j/…`);
`text:footnote` (`:3273-3274`). Other types (`text:header`, `text:footer`) are not rendered. Text is
escaped (`utils/markdown.py:93-120`; probe `&lt;b>`, `2024\.`).

**json_content** is always populated (`formats/office.py:511`), same list for markdown/text/json output.

**Metadata** (`formats/office.py:513-528`): `page_count` = pipeline `pages`; `image_count` = number of
`image` items kept as base64 (0 when not extracted or when OCR'd); DOCX `word_count` = `len(markdown.split())`
(counts markers/table markup); PPTX `slide_count` = `content.count('Slide ')`, min 1 (wrong, 2.2);
XLSX: nothing more (`sheet_names`, `total_cells` only in the basic fallback, `:667-672`). No title,
author or dates (core properties are only read by the basic fallback, `:581-589,734-741`). `extra` gets
route keys only when the route was tried (`:301-302`).

**Images** (`extract_images=True`, no OCR): `ProcessedDocument.images` = `[{'data': <raw bytes>, 'page': n}]`
(`formats/office.py:503-509`); base64 only in json_content `image` items and in `content`; `to_dict()`
base64-encodes the bytes (`core/base.py:98-108`). Probe: `images: [{'data': b'\x89PNG…', 'page': 1}]`.
**With OCR** (one `batch_process_images` call per document: `OP:919-938,1745-1764,2600-2607`): pictures become
`text:image_description` items (plain paragraphs) or `[Image: <text>]` in table cells; `images == []`,
`image_count 0`. OCR with no text -> the picture disappears (`OP:672-681`), `[Image]` in cells. Per-call OCR
failure -> the literal text `OCR failed` in body and `[Image: OCR failed]` in cells (`OP:683-711`) plus
`ocr_issues` (probe). Duplicate pictures (same SHA-256) are emitted once in DOCX (`OP:1347-1353`).

### 2.1 DOCX (`OP:845-1712`)
- Reads the XML tree: content controls, tracked insertions, fields, smart tags, custom XML, hyperlinks and
  nested tables (flattened into the cell) are kept; deleted text is not (`OP:103-164`).
- No inline formatting and no link targets: probe `Plain **bold** … link` -> `Plain bold and italic then link text`.
- Headings: outline level decides (0-8 -> level 1-9, capped at 6; 9 = body text); Title -> 1, Subtitle -> 2,
  `Heading N` by name when no outline level (`OP:1168-1210`). Level 1 -> `text:title`, else `text:section`,
  with `level` (probe `### Sub Heading 3`).
- Lists: markers from numbering.xml (`1.`, `-`, `(1)`, roman, letters, CJK, `OP:250-451`), `list_level`
  nesting (probe `1.`, `2.`, `-`, nested `    -`). Paragraphs without numbering are typed by style
  (`caption`, `list`, `bullet`) or content regexes (`OP:713-753`): any paragraph starting with
  Figure/Fig/Table/Tbl/Chart/Graph/Image/Plate/Scheme or `Source:`/`Note:` becomes an italic caption
  (probe `*Tablets are popular devices this year.*`); `2024. The year…` becomes an unmarked `text:list`.
- Pages: +1 for `w:br type=page`, `w:lastRenderedPageBreak`, or a nextPage/odd/even section break
  (`OP:940-1008`); the paragraph holding the break is counted on the new page (probe "Before break" on page 2).
- Tables: pipe table when no merges, `table_style` HTML/grid when `gridSpan`/`vMerge` merges exist
  (`OP:1569-1645`); cells escape `|` (probe `pipe \| cell`); pictures in cells -> `[Image]` / `[Image: …]`
  only with `extract_images` (`OP:1663-1698`), never added to `images`.
- Headers/footers: paragraphs of every section's header/footer appended at the end as `text:header` /
  `text:footer` items without `page` (`OP:1020-1048`) -> JSON only, not in Markdown; header/footer tables
  are dropped; header pictures stay type `image` and are rendered at the end (`OP:1030-1033`) (code). Probe:
  header text, footer text only in JSON, header table text nowhere.
- Text boxes (`wps:txbx` / `v:textbox`): not extracted anywhere (runs are read only for direct `w:t`
  children, `OP:131-149`); probe `TEXTBOX SECRET CONTENT` absent from content and JSON.
- Footnotes/endnotes: `[^id]` appended at the end of the referencing paragraph (`OP:1147-1164`), definitions
  `[^id]: text` (endnotes `en<id>`) as `text:footnote` items on the last page (`OP:1050-1060,1073-1116`) (code).
- Item keys: `type, content, page` (+ `level`, `marker`, `list_level`); header/footer items lack `page`.
- OCR batch also sends every image relationship of the part, used or not (`OP:1474-1486`) (code).

### 2.2 PPTX (`OP:1715-2543`)
- Per slide: own background picture (with `extract_images`, first on the slide, `OP:1870-1879,2370-2384`),
  placeholders (`OP:1930-2059`), other shapes and groups (`OP:2061-2181`), then slide-layout text, sorted by
  (top, left) in EMU (`OP:1919-1925`); notes last as `text:normal` `"[Slide N Notes]\n…"` (`OP:2224-2240`).
- Placeholder typing: title/center title -> `text:title` (`#`), subtitle -> `text:section` (`##`), body ->
  content regexes, date/footer/header/slide number -> `text:caption` (`OP:2030-2043`). No `level` key.
- **Layout prompt text leaks into every slide**: every layout text shape under 100 chars not already
  captured is added as `text:caption` with `_top: 1000` EMU ("Put at bottom" comment, but 1000 EMU is the
  top) (`OP:1892-1917`). Probe `sample_presentation.pptx`: `*Click to edit Master title style*`,
  `*6/13/25*`, `*‹#›*` above each slide's content; converted PPS: `*&lt;date/time>*`, `*&lt;footer>*`.
- **Text of AutoShapes is emitted twice**: once via `_extract_all_text_from_shape` (`OP:2094-2101`) and again
  by the "unknown shape type" fallback (AUTO_SHAPE is not in the allow-list, `OP:2112-2129`). Probe: a
  rounded rectangle's text printed twice; also seen in LibreOffice-converted PPT/PPS.
- Soft line breaks (`\x0b`) -> `\n` (`OP:459-461`): a single newline in the Markdown source (CommonMark renders
  it as a space), `<br>` inside table cells (probe). Paragraphs of one shape are one item joined by `\n`;
  bullets are not turned into Markdown lists (literal `•` stays).
- Tables always go through the complex renderer (`OP:2346-2366`): `<table>` in the default style even with no
  merges (probe), `markdown_grid`/`styled_html` honoured.
- Charts: `Chart: <title>`, `X-axis: …`, `Y-axis: …` captions (`OP:2471-2508`); group text/pictures included.
- Pictures: shapes, placeholders, groups, own background (layout/master backgrounds not extracted).
- `slide_count` counts the substring `Slide ` in the Markdown (`formats/office.py:527-528`): probe 5-slide
  sample -> 1; crafted 3-slide deck -> 3 only by coincidence; converted 3-slide PPS -> 5. `page_count` is right.
- Item keys: `type, content, page`.

### 2.3 XLSX (`OP:2545-3082`, `utils/number_format.py`)
- Per sheet: `text:title` `"Sheet: <name>"` -> `# Sheet: <name>` (`OP:2613-2617`), title rows as `text:normal`,
  then one `table`; `page` = sheet index. Empty sheet -> heading only (probe). `page_count` = number of sheets.
- Values as displayed (`OP:2736-2744`, `utils/number_format.py:37-67`); probe: `10.0`->`10`, `0.25` `0%`->`25%`,
  `"$"#,##0.00`->`$1,234.50`, `yyyy-mm-dd`->`2026-03-31`, `#,##0` 1234.5678->`1,235`,
  `#,##0.00;[Red]…` -1234.5->`-1,234.50`; openpyxl's default datetime format shows `2026-03-31 0:00:00`;
  a formula saved without cached value shows `=B6*2` (`OP:2821-2873`); cached values after LibreOffice.
- Merges only from `merged_cells.ranges` (`OP:2649-2650`); no merges -> pipe table, merges -> `table_style`
  HTML/grid (`OP:2687-2693`). Fully empty rows/columns dropped (probe col E, row 5). A single-cell row above
  the table becomes a paragraph only if merged across the table or separated by a blank row (`OP:2697-2725`).
- Pictures (only with `extract_images`): anchored inside the used range -> `[Image]` appended to the cell
  text (`10 [Image]`), `[Image: <OCR text>]` with OCR (`OP:2746-2791`); without OCR the picture is also
  emitted after the table; pictures outside the range are emitted after the table (`OP:2875-2931`). If openpyxl
  finds no pictures in the workbook, every `/media/` file is attached to sheet 1 (`OP:2932-2972,3006-3082`).
  In-cell (rich value) pictures are read from the package (`OP:551-601,2770-2774`).
- Cells escape `|` (probe `x\|y`). Item keys: `type, content, page`.

### 2.4 Office image route (DOCX/PPTX only)
- Gate (`formats/office.py:330-347`): extension docx/pptx (xlsx never, `:331`); `self.ocr` not None; `ocr_images`
  and `extract_images` both true (the loader supplies `extract_images`, `core/loader.py:630-634`).
- OOXML pre-filter (`formats/office.py:349-430`, `core/strategy.py:41-42,148-175`): "image" when mean picture
  coverage >= 0.55 and mean text < 200 chars. PPTX: per slide, union of picture shapes (groups, placeholders,
  picture fills, own/layout/master background picture, layout/master non-placeholder pictures; hidden shapes
  excluded) and text of frames and table cells. DOCX: total picture extent (body, headers, footers, text boxes)
  vs one page, total `w:t` text.
- Flagged -> LibreOffice converts to PDF (timeout 300 s, own profile, one retry: `formats/office.py:451-452`,
  `utils/libreoffice.py:63-138`); the converted PDF's own route decides (`PDFLoader._document_image_strategy`,
  `formats/office.py:156-175,453-460`); only `image` routes: `PDFProcessor(ocr, table_style)` (no judges) runs
  with `extract_images`, `use_ocr` (`:461-467`).
- Routed result (probe): PDF-pipeline Markdown (no `<!-- page -->` marker in the probe), `format`/`filename`/
  `size_bytes` restored (`:469-471`), `page_count`/`word_count` from the PDF, no `image_count`/`slide_count`,
  json_content = PDF items (`content, page, position_y, type`), `images` = PDF style `[{'text': …, 'type': 'ocr'}]`,
  `extra = {'ocr_routing': {'document_route': 'image', 'overrides': []}, 'ocr_images': {…}, 'routed_via': 'pdf'}`.
- Fallbacks (native extraction continues): exception (incl. missing LibreOffice) -> `routed_via: 'native'`,
  `route_error: <message ≤500 chars>` (`:340-347`), probe `'LibreOffice is required for this conversion but
  was not found. Install it from https://www.libreoffice.org/'`; PDF answers text -> `route_reason:
  'converted PDF routes text'`, no answer -> `'converted PDF route unavailable'` (`:454-460`, probe via
  monkeypatch). Files the pre-filter passes over get no key. A warning is logged on errors.
- Side effect: with the default `UnifiedDocumentLoader()` (keyless `OpenAIOCR`), `ocr_images=True` on an
  image-dominant file still runs the conversion; the page OCR then yields `[image: OCR unavailable]` (probe).

---

## 3. Legacy: DOC, XLS, PPT, PPS, RTF (`formats/legacy.py`, `utils/libreoffice.py`)
- doc->docx, rtf->docx, ppt->pptx, pps->pptx, xls->xlsx (`formats/legacy.py:67-73`); soffice `--headless
  --norestore --convert-to <fmt>` with a throwaway profile, 60 s default timeout, one retry
  (`utils/libreoffice.py:63-138`); converted file handed to `OfficeProcessor.process(**kwargs)` (`formats/legacy.py:80-91`).
- Metadata: `format` (DOC/XLS/PPT/PPS/RTF), `filename`, `size_bytes` restored; `extra['converted_from'] = 'doc'`,
  `extra['converted_to'] = 'docx'` (`:93-102`); everything else as for the Office type (probe all five).
- Same image/OCR options and image route (docx/pptx after conversion, a second LibreOffice run).
- `table_style` is ignored: `LegacyProcessor(ocr=ocr)` (`core/loader.py:443`) builds `OfficeProcessor(ocr=self.ocr)`
  (`formats/legacy.py:38`) -> always minimal_html (probe: `markdown_grid` honoured for .docx, not .doc;
  `styled_html` for .pptx, not .ppt).
- LibreOffice lookup: well-known paths then `which libreoffice/soffice` (`utils/libreoffice.py:28-60`), once at
  loader construction (`formats/legacy.py:31`); installing it later needs a new loader.
- Missing -> `ProcessingError("LibreOffice is required to process legacy formats. Please install LibreOffice
  from https://www.libreoffice.org/")` (`:56-61`), seen as `Processing failed: …` (probe). Conversion failure ->
  `Processing failed: Legacy format processing failed: Converted file not found: random.pptx` (probe).
- Converted decks carry LibreOffice's own layout prompts (`&lt;date/time>`, `Click to edit the title text
  format`) and duplicated AutoShape text (probe .ppt/.pps; causes in 2.2).

---

## 4. Images (`formats/image.py`)
- Extensions png jpg jpeg webp tiff tif bmp gif heic heif avif (`:67-71`); `metadata.format` from the extension
  (`.jpeg` -> JPEG, `.tif` -> TIF; `:211-220`).
- Content (`:120-125`): `# Image: <file>`, then `- **Format**: PNG`, `- **Dimensions**: W x H pixels`,
  `- **Mode**: RGB`, `- **Size**: N bytes`. OCR (`:131-173`): `## OCR Extracted Text` + text; empty ->
  `## OCR Extraction` / `*No text detected in image*`; failure -> `*OCR extraction failed: <err>*`;
  `ocr_images` without provider -> `*OCR requested but no OCR provider configured*` (probe all four).
- json_content always (`:246-271`): `text:title` `"Image: <file>"`, `text:normal` `"Format: PNG, Dimensions: 40x20,
  Size: 108 bytes"`, `text:image_description` `<image_ocr_result>…</image_ocr_result>` (only non-empty OCR),
  `image` (base64 PNG) when extracted; keys `type, content`, no `page`.
- Metadata (`:222-241`): `page_count 1`, `image_count 1`, `word_count` (OCR text only), `extra = {'width': 40,
  'height': 20, 'mode': 'RGB', 'format': 'PNG', 'has_ocr': True|False}` (`has_ocr` False when OCR failed or no
  provider).
- `images` (only if `extract_images`, or `ocr_images` with a provider; loader default False,
  `core/loader.py:582,681-683`): `[{'type': 'image', 'content': <base64 PNG>, 'format': 'png', 'width', 'height',
  'original_format': 'JPEG', 'filename'}]` (`:178-205`); nothing is embedded in `content`.
- OCR sends the original file bytes (`:136,143`); the RGB conversion at `:111-113` only affects the extracted PNG.
- HEIC/HEIF: `pillow_heif.register_heif_opener()` if importable (`:24-39`). Without it: `ProcessingError("Processing
  failed: Not a valid image file: x.heic")` (`:280-281`; probe), no install hint.
- AVIF: not covered by doc2mark's pillow-heif call (`register_heif_opener` accepts HEIF brands only: pillow-heif
  0.22 `as_plugin.py:146-173`, AVIF needed `register_avif_opener`, gone in 1.8). AVIF opens through Pillow's own
  plugin (probe: Pillow 11.3.0, no pillow-heif, `.avif` OK). Declared floor `Pillow>=9.0.0` (`pyproject.toml:56`).

---

## 5. Text and data (`formats/text.py`): no json_content, no images, no OCR
- **TXT** (`:73-99,231-244`): read with `encoding` (default utf-8, no detection; latin-1 bytes -> `ProcessingError`,
  probe); lines with `str.isupper()` and < 100 chars -> `## <line>` (probe `## Q1: $10,000`); rest verbatim,
  unescaped. Metadata: `word_count`, `line_count` (= `len(content.split('\n'))`), `encoding` (the argument).
- **CSV** (`:101-137,246-287`): `csv.Sniffer` on the first 1024 chars, fallback `,`; the `delimiter` argument is
  ignored (probe: `delimiter=','` and `'|'` on a `;` file -> still `;`). Pipe table, first row = header, cells
  not escaped (probe: `b|x` splits a column; embedded newline breaks the row). Metadata: `row_count` (incl.
  header), `column_count` (first row), `delimiter`, `encoding`; empty file -> `""`, counts 0.
- **TSV**: always fails. `_process_tsv` sets `kwargs['delimiter']` then passes `'\t'` positionally too
  (`:139-144`) -> `TypeError … got multiple values for argument 'delimiter'` -> `ProcessingError` (probe; no test
  covers TSV). Also listed as failed by `batch_process` (probe).
- **JSON** (`:174-195,289-333`): dict -> `**key**: value` lines, nested 2-space indent; list -> `- value` /
  `- Item N:`; scalar -> fenced ```` ```json ```` block. Metadata: `encoding`, `data_type` = Python type name
  (`dict`, `list`, `str`, …), `item_count` (len of list/dict, else 1).
- **JSONL** (`:197-229`): `# JSONL Data (N records)`, `## Record i` + record; invalid lines skipped with a log
  warning only. Metadata: `encoding`, `record_count` (valid records).

## 6. Markup (`formats/markup.py`): no json_content, no images, no OCR
- **HTML/HTM** (`:218-272`): bs4 `html.parser`; `markdownify(str(soup), heading_style="ATX", bullets="-",
  code_language="python")` when importable (`:239-247`), else `SimpleHTMLToMarkdown` (`:21-120,254-258`).
  markdownify is not a declared dependency or extra (`pyproject.toml:46-102`), so a base install uses the
  fallback and logs a warning per file (`:146-158`). Probe, markdownify path: `<title>` text is the first line,
  script/style dropped, pipe tables, every code block tagged `python`. Fallback path: `My Pagebody{color:red}var x
  = 1;`, `Hello**world**and[a link](…)`, a table flattened to `AB12`, an `<a>` without href leaves `[`.
  Metadata: `title` (`<title>` text), `word_count` (bs4 `get_text().split()`, adjacent tags glue words),
  `link_count` (all `<a>`, href or not), `image_count` (`<img>`), `encoding`.
- **XML** (`:274-299,354-385`): `defusedxml` parse (the `encoding` argument is only recorded); root `# tag`,
  children `##`… capped at 6, `**Attributes:**` + `- key: value`, text and tail kept; namespaced tags as
  `{uri}local` (probe). Metadata: `root_tag`, `element_count` (descendants, root excluded), `encoding`.
- **MD/MARKDOWN** (`:301-352`): content verbatim. If it starts with `---` and splits into 3 parts, PyYAML
  `safe_load` of part 1 -> `frontmatter`, content = part 2 stripped. PyYAML is not a dependency (arrives only via
  langchain-core in `[ocr]`/`[vertex_ai]`/`[all]`); without it the front matter stays in `content` and
  `frontmatter` is None (probe). A file starting with a `---` rule loses the text up to the next `---` from
  `content` and gets it as a *string* `frontmatter` (probe). Metadata: `word_count`, `line_count`,
  `header_count` (lines starting with `#`, code fences included), `link_count` (regex; images with alt text count
  too), `image_count`, `encoding`, `frontmatter`.

## 7. EML (`formats/email.py`): no json_content, no images, no OCR
- stdlib `email` with `policy.default` (`:72-73`); headers From, To, Cc, Subject, Date (`:23,78-81`; Bcc,
  Reply-To ignored).
- Body (`:103-136`): all `text/plain` parts joined, including text attachments (probe: `notes.txt` content in the
  body); if none, `text/html` parts via `SimpleHTMLToMarkdown` (`:26-37`, never markdownify; probe: style text
  leaks, table flattened). Body text unescaped (probe `# not a heading` becomes a heading).
- Markdown (`:163-178`): `# <Subject or (no subject)>`, `**From:** …  ` lines (two trailing spaces), `---`, body.
- Metadata: only `filename`, `format`, `size_bytes` (`:90-95`); subject/from/date are not in metadata.
- `process(output_format=…)` has its own TEXT/JSON renderings (`:54-59,146-161`), unreachable through `load()`.

## 8. Optional dependencies

| Package | Declared? | Used for | Missing -> |
|---|---|---|---|
| markdownify | no | HTML conversion | fallback converter, warning per file (section 6) |
| PyYAML | no (transitive via langchain-core) | MD front matter | front matter left in content, warning |
| pillow-heif | `[heif]` | HEIC/HEIF | "Not a valid image file" |
| Pillow AVIF plugin | Pillow wheel (>= 11.2) | AVIF | cannot open (not fixable by `[heif]`) |
| python-magic | `[mime]` | `MimeTypeMapper(use_content=True)` only | no effect on `load()` |
| LibreOffice (binary) | no | legacy formats, Office image route | `ProcessingError` / route_error |
| chardet | core dep | nothing (unused anywhere in doc2mark) | n/a |
| openai/langchain | `[ocr]` | OpenAI OCR | OCR placeholders + `ocr_issues` |

---

## 9. Verdicts on existing claims

### 9.1 docs/formats.rst (HEAD)
| Lines | Claim | Verdict | Evidence |
|---|---|---|---|
| 4-8 | every processor yields the same ProcessedDocument, so format never changes consumption | PARTLY | same dataclass; `images`/`json_content`/metadata shapes differ per format (sections 2-7) |
| 12-17 | OCR only runs with `extract_images=True` together with `ocr_images=True` | PARTLY | `ocr_images=True` alone suffices with a provider (`core/loader.py:630-634`) |
| 18-20 | legacy formats need LibreOffice | TRUE | `formats/legacy.py:56-61` |
| 25 | extension-based, case-insensitive | TRUE | `core/loader.py:1272`; probe `PAGE.HTML`; no content/MIME sniffing (`:624`) |
| 25-26 | strip dot, lowercase, match DocumentFormat | TRUE | `core/loader.py:1272-1277` |
| 27-28 | `.htm`->HTML, `.markdown`->MARKDOWN (value "md") | TRUE | `core/loader.py:1280-1283`, `core/base.py:38`; but `batch_process` skips `.htm` (probe) |
| 28-29 | unknown extension raises UnsupportedFormatError | TRUE | `core/loader.py:1285-1287`, unwrapped; probe `.7z`, no extension |
| 35-36 | `supported_formats` -> `['docx','xlsx','pptx','doc',…]` | TRUE | `core/loader.py:1458-1465`; lists `md`, not `htm`/`markdown`; includes broken `tsv` |
| 41-45 | "Optional" = both flags + provider; "Never" ignores flags | PARTLY | first half as 12-17; "Never" TRUE (`core/loader.py:684-689`) |
| 55-59 | Office: OOXML parsing; images extracted as base64, optionally OCR'd | PARTLY | `images` holds raw bytes (`formats/office.py:503-509`); base64 in content/json |
| 66-70 | legacy row | TRUE | `formats/legacy.py:67-91` |
| 71-76 | image row, `.heic`/`.heif` need pillow-heif | TRUE | `formats/image.py:24-39,67-71`; AVIF via Pillow, not pillow-heif |
| 77-80 | txt/csv/tsv/json/jsonl, parsing only | PARTLY | `.tsv` always fails (`formats/text.py:139-144`; probe) |
| 81-84 | markup row, never OCR | TRUE | `core/loader.py:462-464,688-689` |
| 85-88 | eml row, never OCR | TRUE | `formats/email.py:43-44` (ocr unused) |
| 93-95 | paragraphs, headings, lists, tables to Markdown "with page / slide / sheet markers preserved" | PARTLY | markers are `<!-- … N -->` (`OP:3204-3220`); DOCX pages inferred from breaks (`OP:940-1008`); PPTX has no list structure; DOCX text boxes lost, headers/footers JSON-only |
| 95-97 | merged-cell tables respect `table_style` | PARTLY | DOCX/XLSX yes; PPTX tables always complex-rendered (`OP:2346-2366`); legacy ignores it (probe) |
| 99-100 | `extract_images=True` returns media as base64 in `ProcessedDocument.images` | FALSE | raw bytes `{'data','page'}` (`formats/office.py:503-509`); base64 in `content` data URIs and json items |
| 100-101 | `ocr_images=True` OCRs those images, batched | PARTLY | one batch per document (`OP:919-938`); OCR'd pictures then leave `images`; empty OCR drops the picture; failure prints `OCR failed` (probe) |
| 102-104 | image-dominant docx/pptx decided from OOXML, no rendering | PARTLY | OOXML is a pre-filter; final decision is the LibreOffice-rendered PDF's route (`formats/office.py:432-460`) |
| 104-106 | LibreOffice -> PDF, PDF OCRs pages, identity restored, `routed_via == 'pdf'` | TRUE | `formats/office.py:451-475`; probe |
| 106-107 | gated on OCR being requested | TRUE | `formats/office.py:333-335` (needs a provider; default loader always has one) |
| 107-108 | silently falls back when anything is unavailable | PARTLY | falls back, but records `routed_via: 'native'` + `route_error`/`route_reason` and logs (`:340-347,454-460`; probe) |
| 108-109 | `.xlsx` never routes | TRUE | `formats/office.py:331` |
| 111-116 | example prints DOCX format and page_count | TRUE | probe; page_count is break-based estimate |
| 118-119 | basic python-docx/openpyxl/python-pptx fallback | TRUE | `formats/office.py:270-284,532-546` (also when the pipeline raises) |
| 225-227 | soffice converts, then OfficeProcessor | TRUE | `formats/legacy.py:80-91` |
| 229-250 | doc/rtf->docx, ppt/pps->pptx, xls->xlsx | TRUE | `formats/legacy.py:67-73`; probe all five |
| 252-254 | format and filename restored; `converted_from`/`converted_to` | TRUE | `formats/legacy.py:93-102`; values `'doc'`/`'docx'` |
| 254-255 | no LibreOffice -> ProcessingError with installation guidance | TRUE | `formats/legacy.py:57-61`; probe (wrapped "Processing failed: …") |
| 255-256 | same image extraction / OCR options apply | PARTLY | TRUE for flags; `table_style` is not applied (`formats/legacy.py:38`; probe) |
| 261-262 | Pillow + header (format, dimensions, mode, size) | TRUE | `formats/image.py:106-125` |
| 265-267 | OCR text under "OCR Extracted Text", mirrored as `text:image_description` | TRUE | `formats/image.py:156-159,253-258`; probe |
| 268-269 | `extract_images=True` (processor default) returns PNG base64 in `images` | PARTLY | true for the processor (`:78,178-205`); `load()` defaults it to False (`core/loader.py:582,681-683`) |
| 271-272 | without pillow-heif `.heic`/`.heif` cannot be opened | TRUE | probe: "Not a valid image file: fake.heic" |
| 283-284 | stdlib only, no OCR | TRUE | `formats/text.py:3-4` |
| 286-287 | `.txt` utf-8 default, all-caps short lines -> headings | TRUE | `formats/text.py:77-80,237-241` (`##`; `isupper()` also catches `Q1: $10,000`) |
| 288-289 | `.csv` sniffed; "override with the `delimiter` argument"; rows -> Markdown table | PARTLY | sniff TRUE (`:106-114`); override FALSE (argument ignored; probe) |
| 290 | `.tsv` like CSV with tab | FALSE | always `ProcessingError` (TypeError, `:139-144`; probe) |
| 291-292 | `.json` nested Markdown; `data_type`, `item_count` | TRUE | `:174-195`; `data_type` is a Python type name |
| 293-294 | `.jsonl` per line, invalid lines skipped with warning, own heading | TRUE | `:197-229` (log warning only) |
| 296-300 | `load("data.csv", delimiter=";")` example | PARTLY | runs, but `delimiter` has no effect |
| 307-310 | HTML via BeautifulSoup + markdownify when available, else SimpleHTMLToMarkdown; title/word/link/image counts | TRUE | `formats/markup.py:218-266`; markdownify is not installed by any doc2mark install (`pyproject.toml:46-102`) |
| 311-313 | XML via defusedxml, heading tree, `root_tag`/`element_count` | TRUE | `formats/markup.py:8,274-299,354-385` |
| 314-316 | MD largely as-is; front matter (with PyYAML) -> `metadata.frontmatter`; heading/link/image counts | TRUE | `formats/markup.py:301-352`; PyYAML undeclared; leading `---` rule misparsed (probe) |
| 321-322 | stdlib `email`, no OCR | TRUE | `formats/email.py:3-4,72-73` |
| 322-323 | From/To/Cc/Subject/Date | TRUE | `formats/email.py:23,78-81` (content only, not metadata) |
| 323-325 | prefers text/plain, else HTML via the SimpleHTMLToMarkdown "used for web pages" | PARTLY | all text/plain parts incl. attachments joined (probe); web pages use markdownify when present |
| 325-327 | subject heading, other headers, body | TRUE | `formats/email.py:163-178` |
| 327-328 | TEXT and JSON output formats supported | PARTLY | processor-level only (`:146-161`); `load()` never forwards `output_format` (`core/loader.py:671-700`) |
| 330-334 | EML registered only when module and enum member exist, loader works without it | PARTLY | code exists (`core/loader.py:487-498`) but `formats/__init__.py:3` imports email unconditionally and `EML` always exists (`core/base.py:41`): the fallback cannot happen |
| 339 | plain extraction needs no credentials | TRUE | probe (legacy still needs LibreOffice) |
| 339-341 | OCR enabled per call via `extract_images` / `ocr_images` | PARTLY | `ocr_images` alone enables it; `extract_images` alone never OCRs |

### 9.2 README.md (HEAD)
| Lines | Claim | Verdict | Evidence |
|---|---|---|---|
| 195 | `UnifiedDocumentLoader()` processes text-first documents without OCR credentials | TRUE | probe: no key, openai/langchain blocked; CSV and DOCX load |
| 196 | OCR providers are initialized only when OCR is requested | FALSE | `OpenAIOCR` built in `__init__` (`core/loader.py:36,149-170,285-308`); only the LangChain agent is lazy (`ocr/openai.py:485-487,498-543`). Base install still works (no `openai` import); OCR requests fail softly |
| 179 | `[heif]`: HEIC, HEIF, and AVIF support | PARTLY | AVIF is not enabled by it (section 4) |
| 180 | `[mime]`: improved MIME detection | PARTLY | never used by `load()` (`core/loader.py:624,1260`) |
| 468 | Office DOCX, XLSX, PPTX | TRUE | section 2 |
| 469 | PDF (text + scanned) | PDF reviewer | scanned needs `ocr_images=True` + provider |
| 470 | Images PNG … AVIF (requires `doc2mark[heif]`) | PARTLY | all open; `.jpeg`/`.tif` also accepted; `[heif]` needed for HEIC/HEIF only, AVIF needs Pillow's plugin |
| 471 | TXT, CSV, TSV, JSON, JSONL | PARTLY | TSV always fails |
| 472 | HTML, XML, Markdown | TRUE | section 6 (HTML fallback quality without markdownify) |
| 473 | EML (`message/rfc822`) | TRUE | detection is by `.eml` extension only |
| 474 | DOC, XLS, PPT, RTF, PPS (requires LibreOffice) | TRUE | section 3 |

### 9.3 docs/quickstart.rst (HEAD)
| Lines | Claim | Verdict | Evidence |
|---|---|---|---|
| 5-7 | text-only needs no keys; OCR providers initialized only when asked | PARTLY | first half TRUE, second FALSE (as README 196) |
| 36-37 | `tables`/`images`/`sections`/`json_content` populated depending on format and options | PARTLY | `tables`/`sections` never populated; `json_content` depends on format only |
| 51-53 | `output_format` markdown/json/text | TRUE | `core/loader.py:566-576`; case-insensitive, else ValueError |
| 64-68 | JSON: `content` is a JSON string; `json_content` the list of `{"type","content"}` blocks | PARTLY | `content = json.dumps(to_dict())` TRUE (`:724-726`); text/markup/email get a 1-item fallback list that is not inside the JSON string (`:727-728`; probe) |
| 70-71 | TEXT: layout and Markdown markup stripped | PARTLY | only 5 regexes (`core/base.py:153-165`); tables, markers, lists stay; `C#` -> `C` (probe) |
| 73-74 | `to_dict()` = CLI `--format json` payload | TRUE | `cli.py:45-47,449` |
| 97 | `ocr_images=True` requires `extract_images=True` | FALSE | implied since #18 (`core/loader.py:630-634`); stale also in `__init__.py:115` docstrings |
| 97-99 | `extract_images=True, ocr_images=False` keeps images as base64 data | PARTLY | Office: base64 in `content` data URIs + json items, raw bytes in `images`, `[Image]` in DOCX/XLSX cells; image files: base64 PNG in `images[i]['content']` + json item, nothing in content |

### 9.4 docs/api/types.rst (HEAD)
ProcessedDocument (140-170): `images` "None when not requested" PARTLY (Office returns `[]`); `tables` "None
when no tables" FALSE in spirit (never populated); `sections` never populated; `json_content` "None when the
output format does not produce it" PARTLY (set by Office/PDF/image processors for every output format, None
for text/markup/email except the JSON fallback); `text` PARTLY (5 regexes); `to_dict()` TRUE (`core/base.py:98-132`).

DocumentMetadata (202-295) — who really sets each field (default pipelines unless noted):

| Field | Doc says | Actually set by | Verdict |
|---|---|---|---|
| filename, format, size_bytes | always | every processor | TRUE |
| page_count | PDF, DOCX, PPTX | PDF, DOCX (break estimate), PPTX (slides), XLSX (sheets), images (1), legacy | PARTLY |
| word_count | approximate | TXT, MD, HTML, DOCX (Markdown tokens), PDF, image (OCR only), routed Office | TRUE (not PPTX/XLSX/CSV/JSON/XML/EML) |
| language | detected language | nobody | FALSE |
| creation_date, modification_date, author | file metadata | only DOCX/PPTX basic fallback and PyMuPDF PDF fallback (`formats/office.py:581-589,734-741`, `formats/pdf.py:240-250`) | PARTLY |
| title | file metadata | HTML `<title>`; the same fallbacks | PARTLY |
| sheet_names | XLSX | only the XLSX basic fallback (`formats/office.py:669`); None by default (probe) | FALSE |
| slide_count | PPTX | PPTX via `content.count('Slide ')` (wrong; probe 5 slides -> 1); absent when routed | FALSE (value unreliable) |
| line_count | text files | TXT, MD | PARTLY |
| header_count | Markdown | MD | TRUE |
| link_count | HTML, Markdown | HTML (all `<a>`), MD (regex) | TRUE |
| image_count | images found | Office (base64 items kept), HTML `<img>`, MD `![]`, image files (1); not PDF | PARTLY |
| total_cells | XLSX | only the XLSX basic fallback | FALSE |
| encoding | detected encoding (text files) | echo of the `encoding` argument for TXT/CSV/JSON/JSONL/HTML/XML/MD; no detection | FALSE ("detected") |
| delimiter | CSV, TSV | CSV (sniffed); TSV never returns | PARTLY |
| record_count | JSONL | JSONL | TRUE |
| row_count, column_count | CSV | CSV (row_count includes header) | TRUE |
| element_count, root_tag | XML | XML | TRUE |
| frontmatter | parsed YAML (Markdown) | MD, only with PyYAML; can be a non-dict | PARTLY |
| data_type | JSON type descriptor | Python type name | PARTLY |
| item_count | JSON | JSON | TRUE |
| extra | catch-all | see sections 1-7 | TRUE |

Example 297-307: `meta.sheet_names  # ["Sheet1", "Summary"]` FALSE (None; probe).

Exceptions (314-404):
- 317-319 "all exceptions inherit from ProcessingError; one except catches every library-specific failure":
  PARTLY — missing file is `FileNotFoundError`, bad `output_format` `ValueError`/`TypeError` (`core/loader.py:566-576,620-621`).
- 321-327 hierarchy: TRUE (`core/base.py:186-203`); `OCREngineError(OCRError)` exists (`ocr/base.py:30`) but is not exported from `doc2mark`.
- 336-343 `load("data.bin")` caught by `ProcessingError`: TRUE (UnsupportedFormatError subclass).
- 352-362 UnsupportedFormatError for unknown extension, `load("archive.7z")`: TRUE (probe), raised unwrapped.
- 371-382 OCRError on OCR failure, `except OCRError` around `load(…ocr_images=True)`: FALSE — never escapes
  `load()`; per-image failures -> placeholders + `extra["ocr_issues"]`; engine failures -> `ProcessingError`
  (cause `OCREngineError`).
- 391-401 ConversionError when `.doc` -> `.docx` fails, `except ConversionError` around `load("legacy.doc")`:
  FALSE — surfaces as `ProcessingError` (probe chain); route failures go to `extra["route_error"]`.

## 10. Read-only (not probed)
DOCX footnotes/endnotes, header pictures rendered at the end, unused-relationship images sent to OCR, XLSX
zip-fallback and in-cell rich-value pictures, basic Office fallback output, `ImageProcessor` alpha loss when OCR
and extraction combine (`formats/image.py:111-113,184-191`). Everything else above was probed.
