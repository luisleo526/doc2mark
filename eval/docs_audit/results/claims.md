# Claims of the old README and docs: inventory and status

**Method.** Every old page (README.md and docs/*.rst, docs/api/*.rst at `becb74b`, read with `git show`) was read line by line, and each statement a user could rely on (a default, signature, flag, behaviour, number, metadata key or threshold) became a row; pure prose, navigation and badges were skipped, a fact stated twice on one page is one row listing both places, and adjacent correct statements of one paragraph share a row. Each row was checked against the evidence already collected: the four fact sheets (F-fmt = facts-formats.md, F-ocr = facts-ocr.md, F-pdf = facts-pdf.md, F-cct = facts-cache-chunk-table-judge.md, cited by section, sN = section N, and verdict), the example run (`ex: <block id> pass` = examples.md, run at 70f39da), probes.log (`probe:`), merged_cells.md (`mc`) and judge_eval.md (`je`). Claims no fact sheet covers (CLI, development workflow, the convenience functions and batch results) were checked by reading the code at `becb74b`, with two extra probes (the exception chain of a failed legacy conversion; `batch_process` saving extracted PDF pictures). Code references are relative to `doc2mark/` (`pipe` = pipelines/pymupdf_advanced_pipeline.py, `OP` = pipelines/office_advanced_pipeline.py, other pipelines/ files by base name); other repository files by path. Status: **true** = correct (caveats noted in the evidence) and still in the new docs, possibly reworded or moved (`kept X:N`); **fixed** = FALSE, PARTLY or materially incomplete, and the new docs state the corrected fact (`now X:N`); **removed** = no longer in the new docs (unverifiable numbers, obsolete or third-party statements, and a few correct details that were dropped); **verified-by-reading-only** = kept, but not runnable here (live model behaviour, a private document, TypeSafe's prices, limits and legal terms), checked by reading the code or the cited source. New-doc references (`X:N`; docs/ omitted, README = the new README.md) are to the working tree of branch d2m/docs-audit on 2026-09-30: `5a053b6` plus the uncommitted doc fixes then present in api/{convenience,judge,ocr,schema,types}.rst, caching, judge, loading, ocr, ocr_policy, output and tables. Two `fixed` rows note that a docstring rendered into the API reference still says the old thing; the docs pages state the fact, and the docstrings are listed as a code issue.

| | rows |
|---|---:|
| total claims | 531 |
| true | 286 |
| fixed | 206 |
| removed | 27 |
| verified-by-reading-only | 12 |
| of which still in a rendered docstring (code issue #32 of the PR) | 2 |

## README.md

| where | claim | status | evidence |
|---|---|---|---|
| README.md:15-17 | Converts PDFs, Office, images, HTML and more; merged table cells survive | true | F-cct B6; kept README:15-16,27-29 |
| README.md:16-17, 35-36 | Repeated headers, footers and page numbers are detected and dropped before Markdown/RAG | fixed | F-pdf s4 PARTLY; now README:30-32, pdf.rst:84-101 (page numbers dropped, first copy kept) |
| README.md:17-18 | Scanned pages are read by a vision LLM into a structured schema, not a text blob | true | F-ocr A6/A9 (LLM providers); kept README:17-18 (adds Tesseract) |
| README.md:29-31 | Merged cells, multi-level and group headers kept as clean HTML, not a Markdown grid | fixed | F-cct B6 PARTLY (only merged-cell tables are HTML); now README:27-29 |
| README.md:32-34 | Scanned/image pages return an OCRPage with a wall between verbatim and interpretation | true | F-ocr B5 (OCR facade); kept README:36-40, ocr.rst:116-127 |
| README.md:37-38 | OpenAI, Gemini, Tesseract, or any OpenAI-compatible endpoint (Ollama, vLLM) via base_url | fixed | F-ocr B5 PARTLY (key needed, JSON schema); now README:41-42,202-203, ocr.rst:27-30 |
| README.md:39-40 | No model downloads; parsing is local and deterministic; hosted model only on request | true | pyproject.toml deps (no ML models); kept README:43-44 |
| README.md:41-42 | Section-aware, token-budgeted chunking with page spans and heading hierarchy | fixed | F-cct A4 (soft size; title + one section); now chunking.rst:33-40,53-55 |
| README.md:46-49 | doc2mark is the only one of the three tools that returns an LLM-interpreted page | removed | third-party comparison, not measured; dropped |
| README.md:53 | Approach: doc2mark vision-LLM + rules, markitdown text/XML parsing, Docling local ML | removed | comparison table dropped; neutral summary README:181-184 |
| README.md:54 | Merged cells: doc2mark in default Markdown; markitdown flattened; Docling HTML only | true | mc: holds; kept as the measured table README:159-165 |
| README.md:55-60 | Other comparison cells (OCR schema, LLM layer, stripping, RAG, offline, setup weight) | removed | third-party features not verified; table dropped |
| README.md:62-67 | Pick-X advice: Docling best-in-class table structure; doc2mark merged cells + LLM read | removed | evaluative; replaced by neutral summary README:181-184 |
| README.md:69-73 | doc2mark reaches comparable fidelity via the vision model; Docling fits air-gapped best | removed | mc: doc2mark spans come from the rule path, no key; note dropped |
| README.md:79-81, 94-96 | Pipe-only Markdown cannot express merged cells; such tools lose rowspan/colspan | true | kept README:110-111 |
| README.md:83-84 | Native Office/PDF extractor and vision-OCR table model both emit clean HTML | fixed | F-cct B6 PARTLY; now README:112-114 (only tables with merges are HTML) |
| README.md:86-92 | Sample output: indented HTML with a second header row of th cells | fixed | F-cct C2 (1 tag per line, row 2 td); now README:117-134, ex: README.md:118 pass |
| README.md:98-104 | table_style minimal_html (default, spans), markdown_grid (annotated), styled_html | true | core/table.py:68-76; kept README:138-145, ex: README.md:139 pass |
| README.md:106-109 | OCR table HTML is sanitised to a table-only allowlist (colspan/rowspan/scope) | true | F-ocr A9, ocr/schema.py:391-451; kept README:147-148 |
| README.md:111-116 | Measure: colspan/rowspan attributes per tool; doc2mark keeps them in default Markdown | true | mc; kept README:153-157 |
| README.md:120, 122 | DOCX and PPTX rows: 8/2, 0/0, 0/0, 8/2 | true | mc: hold; kept README:161,163 |
| README.md:121 | PDF row: Docling to_html 8/0 | fixed | mc: 6/0 (last row left the table); now README:162,173-174 |
| README.md:123 | XLSX row: doc2mark 9/2 | fixed | mc: 8/2 (title row is text since #14); now README:164,174 |
| README.md:124 | test-table.pdf: doc2mark 7/0 | fixed | mc: 48/0, 26 of 28 real merges; now README:165,175-179 |
| README.md:128-129 | markitdown: no table; blank misaligned cells; PDF table dissolves into loose text | fixed | mc: PDF now a pipe table (0.1.8); now README:171 |
| README.md:130-133 | Docling keeps spans only via export_to_html/its model; export_to_markdown flattens them | true | mc: holds; kept README:172-174 |
| README.md:134-140 | doc2mark embeds span HTML in .content by default, the only one of the three to do so | true | mc: holds for Markdown output; kept README:175,183 (superlative dropped) |
| README.md:145-148 | test-table.pdf: columns over-segmented; group header a cell + padding; 7 spans = dividers | fixed | mc: header now a real colspan, 48 spans; now README:175-179 |
| README.md:148-149 | Docling TableFormer merges the 1.0 TSI/85 kW group header (colspan=2) | true | mc: holds (Docling 2.131.0); README:179 counts it, detail in mc |
| README.md:150-152 | For untagged PDFs, Docling or doc2mark's vision OCR beat the default text path | removed | mc: doc2mark 26/28 merges vs Docling 8/28; advice dropped |
| README.md:154-156 | Measured with the current releases, June 2026 | fixed | re-measured 2026-09-30 (mc); now README:167-169 with versions and script |
| README.md:158-163 | No API key needed: loading complex_table_test.docx is the text path | true | ex: README.md:139 pass (no key); kept README:136-145 |
| README.md:169-183 | pip install doc2mark; extras ocr (OpenAI+Tesseract), vertex_ai, redis, tokenizers, all | true | pyproject.toml extras; kept README:50-57, installation.rst:6-51 |
| README.md:179 | [heif] adds HEIC, HEIF and AVIF support | fixed | F-fmt 9.2 PARTLY; now installation.rst:42-44, README:101 (AVIF needs Pillow) |
| README.md:180 | [mime] improves MIME detection via python-magic | fixed | F-fmt 9.2 PARTLY (load() ignores it); now installation.rst:45-48 |
| README.md:187-195 | UnifiedDocumentLoader().load(...).content works for text documents without credentials | true | F-fmt 9.2; ex: README.md:64 pass; kept quickstart.rst:32-35 |
| README.md:196 | OCR providers are initialized only when OCR is requested | fixed | F-fmt 9.2 FALSE; now quickstart.rst:32-35 (built at init, key needed to OCR) |
| README.md:200-201 | OCR returns structured output by default; the OCR facade is the entry point | true | ocr/base.py:264; kept README:189-200, ocr.rst:10-11 |
| README.md:205-228 | OCR("openai") uses OPENAI_API_KEY; read(), read_one(); document.raw/interpretation; .text | true | F-ocr B5; kept README:189-200, ocr.rst:83-111 (ex: ocr.rst:85 pass) |
| README.md:232-234 | Every result carries an OCRPage on result.document | fixed | F-ocr B5 PARTLY; now ocr.rst:92,136-138 (None for free-form, failed Tesseract) |
| README.md:236-265 | OCRPage example (RawExtraction tables/fields, Interpretation) is a valid page | true | F-ocr B5 (validates); similar example api/schema.rst:277-335 (ex: :278 pass) |
| README.md:244-245, 267-270 | Table views: html (only one with spans), headers/rows flat, markdown fallback | true | F-ocr B5 (markdown rarely filled); kept tables.rst:273-278 |
| README.md:274-275 | Tasks replace the old prompt templates | fixed | F-ocr B5 PARTLY; now ocr.rst:70-73 (prompt_template shapes free-form answers) |
| README.md:277-289, 545-546 | task at construction or per call; tasks= per image; auto, table ... code | true | ocr/base.py:65-76; kept README:206-207, ocr.rst:108-111,148-164 |
| README.md:293-298 | detail="raw": interpretation None, raw still populated | fixed | F-ocr B5 PARTLY (OpenAI only asks); now ocr.rst:135-137 |
| README.md:300-305 | structured=False: free-form Markdown in r.text, r.document None | true | F-ocr B5; kept ocr.rst:136-137, README:208 |
| README.md:311-321 | OpenAI: default gpt-5.4-mini, key needed, [ocr] extra; model= and base_url= | true | F-ocr B5 (facade probe); kept README:202, ocr.rst:24-30 |
| README.md:324-326 | base_url reaches any OpenAI-compatible endpoint: Ollama, vLLM, LM Studio, gateways | fixed | F-ocr B5 PARTLY; now ocr.rst:27-30 (key still needed, JSON-schema output) |
| README.md:330-331, 339 | "vertex_ai" and "gemini" are both accepted; OCR("gemini") | true | ocr/vertex_ai.py:820-821; kept README:203, ocr.rst:31 |
| README.md:333-336 | Gemini setup: GOOGLE_APPLICATION_CREDENTIALS and doc2mark[vertex_ai] | fixed | F-ocr B5 UNVERIFIABLE, no project; now ocr.rst:32-34 (ADC, project/env var) |
| README.md:340 | OCR("vertex_ai", model="gemini-2.0-flash") selects that model | fixed | probe: model ignored by the facade; now ocr.rst:103-106, api/ocr.rst:27-28 |
| README.md:345, 352 | Tesseract: local, no key, raw only (interpretation None); OCR("tesseract", language="eng") | true | ocr/tesseract.py:226-232; kept ocr.rst:36-39,137-138,171-177 |
| README.md:347-349 | Tesseract install: pip install "doc2mark[ocr]" | fixed | F-ocr B5 PARTLY; now installation.rst:62-75 (tesseract program + language data) |
| README.md:359 | openai row: OPENAI_API_KEY, "highest accuracy, complex layouts", [ocr] | removed | accuracy claim unverifiable (F-ocr B5); provider table dropped |
| README.md:360 | vertex_ai/gemini row: needs a GCP service account | fixed | F-ocr B5 PARTLY (any ADC); now ocr.rst:33-34 |
| README.md:361 | tesseract row: Tesseract binary, offline/air-gapped, raw only, [ocr] | true | kept ocr.rst:36-39, installation.rst:64 |
| README.md:365-372 | max_concurrency caps parallel images; else OCR_MAX_CONCURRENCY; else LangChain pool | true | ocr/base.py:36-54; kept ocr.rst:271-274 |
| README.md:374-381 | OCR_MAX_IMAGE_DIM (off by default) downscales larger images; smaller ones untouched | true | utils/image_utils.py:163-306 (LLM providers); kept ocr.rst:275-276 |
| README.md:385-393 | The loader uses the OCR layer when ocr_images=True | true | core/loader.py:630-634; kept README:77-84 |
| README.md:397-401 | Old OCRConfig fields are inert for LLMs; setting them emits a DeprecationWarning | fixed | F-ocr B5 PARTLY (non-default only, filtered); now ocr.rst:292-296 |
| README.md:405-408 | Recurring top/bottom-margin content is detected and dropped automatically | fixed | F-pdf s4 PARTLY (verbatim first); now pdf.rst:84-101 |
| README.md:408 | No configuration required | removed | F-cct B6 TRUE (always on, no switch); not stated in the new docs |
| README.md:410 | On by default for multi-page PDFs | fixed | F-pdf s4 PARTLY (needs 3+ pages); now pdf.rst:88 |
| README.md:410-412 | Applied before Markdown and chunking: chrome never reaches output or vector store | fixed | F-pdf s4 PARTLY; now pdf.rst:94-98, rag.rst:55-57 (first copy kept, JSON keeps) |
| README.md:416-429 | Judge only for 3 open decisions: legibility (OCR on), chrome thinning, LLM refusals | true | F-cct B6; kept README:221-231 |
| README.md:431-440 | Labelled sets (TEST held out, EXTERNAL 60 items) and the 6-cell accuracy table | true | je: all six cells hold; kept README:233-245 |
| README.md:442-454 | Off by default; [typesafe] (in [all]); key; --judge typesafe / judge= / DOC2MARK_JUDGE | true | pyproject.toml; ex: README.md:250, :256 pass |
| README.md:456-457 | Without extra or key the output is unchanged; unanswered questions go to the rules | true | F-cct B6; kept README:263-264 |
| README.md:457-460 | Sent to api.typesafe.ai: 1,500 chars per judged page, header lines, short OCR answers | true | F-cct B5/B6; kept README:264-267 (OCR answers up to 600 chars) |
| README.md:468-469, 472-474 | Office, PDF (text + scanned), HTML/XML/Markdown, EML, legacy via LibreOffice | true | F-fmt 9.2; kept README:98-104 |
| README.md:470 | Images PNG ... AVIF "requires doc2mark[heif]" | fixed | F-fmt 9.2 PARTLY; now README:101 (heif for HEIC/HEIF, AVIF via Pillow) |
| README.md:471 | TXT, CSV, TSV, JSON, JSONL are supported | fixed | probe: .tsv always fails; now README:102, formats.rst:145-146 |
| README.md:480-488 | load(path).content without OCR; extract_images/ocr_images for OCR | true | ex: README.md:10 pass; kept README:9-13, quickstart.rst:75-99 |
| README.md:492-518 | batch_process options; max_workers opt-in, input order and per-file errors; callback | true | core/loader.py:798-1054; kept loading.rst:96-138 (ex: loading.rst:98 pass) |
| README.md:522-531 | batch_process_files can be imported from doc2mark | true | probe: importable, not in __all__; kept api/convenience.rst:28-30 |
| README.md:535-543 | OCR("openai", task="receipt"); raw.fields holds KeyValue pairs | true | F-ocr B5; kept README:192-197 |
| README.md:550-551 | Each OCR result carries metadata["token_usage"] with OpenAI or Gemini | true | F-ocr A3 (recovery tokens missing); kept ocr.rst:277-279 (says so) |
| README.md:553-561 | Printed usage dict has exactly input/output/total tokens | removed | F-ocr B5 PARTLY (per result may add *_details); example dropped |
| README.md:566-590 | Memory and Redis OCR caches; MemoryOCRCache defaults; create_ocr_cache; loader | true | F-cct B6; kept caching.rst:4-58 (ex: caching.rst:16, :47 pass) |
| README.md:596-612 | Chunking starts from load(..., output_format="json") | fixed | F-cct B6 (not needed for PDF/Office); now chunking.rst:15-16,21-24 |
| README.md:605 | max_chunk_size = max characters per chunk | fixed | F-cct B3 PARTLY (soft limit); now chunking.rst:33-40 |
| README.md:607 | split_on_heading_level=2 splits on h1 and h2 | fixed | F-cct B6 PARTLY (every section heading); now chunking.rst:29-32 |
| README.md:606, 618-632 | overlap; Chunk fields; size_unit="tokens" with tiktoken, char fallback | true | core/chunker.py:10-55; kept chunking.rst:38-71, api/chunking.rst:20-22 |
| README.md:638-639 | doc2mark report.pdf prints the document to stdout | fixed | cli.py:522-531 (cut at 1,000 chars); now cli.rst:21-23 |
| README.md:644-645 | doc2mark documents/ -o converted/ -r converts a folder | fixed | probe: flat by stem, "*" matches sub-folders; now cli.rst:28-30,87-89 |
| README.md:641-664 | -o file; OCR off by default; --ocr X --ocr-images; --ocr none; --format json | true | cli.py:207-210,438-531; kept cli.rst:9-16,42-46 |
| README.md:668-676 | Docs build with pip install -e ".[docs]" and sphinx -W; docs.yml publishes to Pages | true | .github/workflows/docs.yml; kept README:282-283, development.rst:114-120 |
| README.md:680-689 | Test commands as CI runs them; E2E runs the real CLI; CI runs it on every PR | true | .github/workflows/ci.yml; kept README:287-291, development.rst:14-47 |
| README.md:693 | MIT license | true | LICENSE; kept README:296-298 |

## docs/index.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/index.rst:4-6 | Converts PDF, Office, images, text/data, markup and legacy files into Markdown and objects | true | F-fmt s1; kept index.rst:4-5 |
| docs/index.rst:8-9 | Text-only use needs no OCR credentials; OCR providers are initialized only on request | fixed | F-fmt 9.2 (2nd half FALSE); now index.rst:6-8, quickstart.rst:32-35 |

## docs/quickstart.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/quickstart.rst:4-7 | Text-only extraction needs no keys; OCR providers are initialized only when asked | fixed | F-fmt 9.3 PARTLY; now quickstart.rst:32-35 (built at init, key needed to OCR) |
| docs/quickstart.rst:21-29, 39-46 | UnifiedDocumentLoader().load(path) or load(path); .content; metadata.filename | true | __init__.py:96-147; ex: quickstart.rst:9, :25 pass |
| docs/quickstart.rst:33-35 | content is Markdown by default; metadata has filename, format, size_bytes, page_count | true | core/base.py:111-119; kept quickstart.rst:48-52 |
| docs/quickstart.rst:36-37 | tables/images/sections/json_content are filled depending on format and options | fixed | F-fmt 9.3 PARTLY (tables, sections never); now output.rst:20-32 |
| docs/quickstart.rst:51-53 | output_format "markdown" (default), "json", "text" = OutputFormat members | true | core/loader.py:566-576; kept output.rst:44-45 |
| docs/quickstart.rst:64-68 | JSON: content is a JSON string; json_content is the list of {type, content} blocks | fixed | F-fmt 9.3 PARTLY; now quickstart.rst:67-69, output.rst:48-50 |
| docs/quickstart.rst:70-71 | "text" strips layout and Markdown markup | fixed | F-fmt 9.3 PARTLY (5 patterns; "C# is" -> "Cis"); now quickstart.rst:69-70, output.rst:34-37 |
| docs/quickstart.rst:73-74 | to_dict() is the payload the CLI writes for --format json | true | cli.py:45-47; kept quickstart.rst:67-69 |
| docs/quickstart.rst:79-81 | By default no OCR model is called and no credentials are needed | true | core/loader.py:578-584; kept quickstart.rst:16-19 |
| docs/quickstart.rst:83-95, 97 | OCR of embedded images needs both flags: ocr_images requires extract_images=True | fixed | F-fmt 9.3 FALSE (implied, core/loader.py:630-634); now quickstart.rst:97 |
| docs/quickstart.rst:97-99 | extract_images=True, ocr_images=False keeps images as base64 data | fixed | F-fmt 9.3 PARTLY (Office: bytes); now loading.rst:60-63, output.rst:23-29 |
| docs/quickstart.rst:104-107 | ocr_provider on loader or load(): openai (default), vertex_ai, tesseract; None = off | true | core/loader.py:270-272; kept loading.rst:26-29, quickstart.rst:34-35 |
| docs/quickstart.rst:111-121 | Defaults: gpt-5.4-mini + OPENAI_API_KEY; Vertex gemini-3.x flash + project; Tesseract | true | F-ocr A4/A5; kept loading.rst:30-32, ocr.rst:24-39 |
| docs/quickstart.rst:123-125 | model=, temperature= and other knobs go to the constructor | true | core/loader.py:34-70; kept ocr.rst:59-73 |
| docs/quickstart.rst:134-135 | doc2mark document.pdf: single file to stdout (Markdown) | fixed | cli.py:522-531 (cut at 1,000 chars); now cli.rst:21-23 |
| docs/quickstart.rst:137-144 | -o output.md; --format json -o output.json; --ocr-images implies --extract-images | true | cli.py:438-441,495-508; kept cli.rst:10-11,44-46 |
| docs/quickstart.rst:146-147 | doc2mark docs/ -r -p 4 -o out/ converts a folder, 4 files at a time | fixed | probe: "*" also matches sub-folders, which fail the run; now quickstart.rst:124, cli.rst:87-89 |
| docs/quickstart.rst:149-150 | CLI OCR is off by default (--ocr none) | true | cli.py:207-210; kept cli.rst:16,42-43 |
| docs/quickstart.rst:155-171 | batch_process(...) returns path -> result (status, output files, metadata) | true | core/loader.py:1018-1036; kept quickstart.rst:104-115, loading.rst:118-126 |
| docs/quickstart.rst:180 | contextual_ocr = "richer, structured OCR interpretation of images" | removed | page is neighbour-page PDF context; description dropped from Next steps |

## docs/cli.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/cli.rst:4-8 | doc2mark report.pdf converts a single file to Markdown on stdout | fixed | cli.py:522-531 (cut at 1,000 chars without -v); now cli.rst:21-23 |
| docs/cli.rst:10-14 | -o report.md writes the Markdown to a file | true | cli.py:495-502; kept cli.rst:10,25-27 |
| docs/cli.rst:16-20 | doc2mark documents/ -o converted/ -r converts a directory | fixed | probe: flat by stem, "*" matches sub-folders; now cli.rst:28-30,87-89 |
| docs/cli.rst:22-26 | JSON output is ProcessedDocument.to_dict() | true | cli.py:45-47; kept cli.rst:32-34 |
| docs/cli.rst:28-32 | --format both -o report writes Markdown and JSON | true | cli.py:509-521; kept cli.rst:12 |
| docs/cli.rst:34-40 | OCR is off by default; enable with --ocr openai/tesseract/vertex_ai --ocr-images | true | cli.py:207-210,438-441; kept cli.rst:16,42-46 |
| docs/cli.rst:42-48 | --pattern, --parallel, --exclude, --max-files, --include-metadata, --max-length | true | cli.py:50-75,486-492,579-615; kept cli.rst:75-95,126-128 |
| docs/cli.rst:50-54 | Judge needs [typesafe] + TYPESAFE_API_KEY, else rules decide; default none/DOC2MARK_JUDGE | true | cli.py:277-285, judge/__init__.py:28-54; kept cli.rst:61-62 |
| docs/cli.rst:56-59 | --judge typesafe; DOC2MARK_JUDGE=typesafe doc2mark deck.pdf | true | ex: docs/judge.rst:66 pass; kept cli.rst:129 (with OCR on) |

## docs/formats.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/formats.rst:4-8 | Every processor gives the same ProcessedDocument, so format never changes consumption | fixed | F-fmt 9.1 PARTLY; now formats.rst:47-49, output.rst:20-29 (shapes per format) |
| docs/formats.rst:12-17 | OCR only runs with extract_images=True together with ocr_images=True | fixed | F-fmt 9.1 PARTLY; now formats.rst:47, loading.rst:64-67 |
| docs/formats.rst:18-20 | Legacy .doc/.xls/.ppt/.pps/.rtf need a local LibreOffice (soffice) | true | formats/legacy.py:56-61; kept formats.rst:30-32 |
| docs/formats.rst:25-29 | Case-insensitive extension; .htm and .markdown aliases; unknown -> UnsupportedFormatError | true | core/loader.py:1272-1287; kept formats.rst:4-7, ex: api/types.rst:90 pass |
| docs/formats.rst:31-36 | loader.supported_formats starts ['docx', 'xlsx', 'pptx', 'doc', ...] | true | core/loader.py:1458-1465; ex: docs/formats.rst:11 pass |
| docs/formats.rst:41-45 | "Optional" = both flags + a provider; "Never" ignores the flags | fixed | F-fmt 9.1 PARTLY; now formats.rst:47 (ocr_images + provider) |
| docs/formats.rst:55-59 | Office row: OOXML parsing; images extracted as base64, optionally OCR'd | fixed | F-fmt 9.1 PARTLY (images holds bytes); now formats.rst:90-96 |
| docs/formats.rst:60-76 | PDF, legacy and image rows (processors; .heic/.heif need pillow-heif) | true | formats/pdf.py:84-129, formats/image.py:24-71; kept formats.rst:23-36 |
| docs/formats.rst:77-80 | txt/csv/tsv/json/jsonl: plain parsing, never OCR | fixed | F-fmt 9.1 PARTLY (.tsv always fails); now formats.rst:37-39,145-146 |
| docs/formats.rst:81-88 | Markup and .eml processors never call OCR | true | core/loader.py:462-464,688-689; kept formats.rst:40-45 |
| docs/formats.rst:93-95 | Office paragraphs, headings, lists, tables become Markdown with page/slide/sheet markers | fixed | F-fmt 9.1 PARTLY; now formats.rst:54-80 (markers, text boxes, headers) |
| docs/formats.rst:95-97 | Merged-cell Office tables respect the loader's table_style | fixed | F-fmt 9.1 PARTLY (PPTX always, legacy never); now formats.rst:76-77,126-127 |
| docs/formats.rst:99-100 | extract_images=True returns the media as base64 in ProcessedDocument.images | fixed | F-fmt 9.1 FALSE (raw bytes); now formats.rst:90-92, output.rst:23-29 |
| docs/formats.rst:100-101 | ocr_images=True runs OCR over those images, batched | fixed | F-fmt 9.1 PARTLY; now formats.rst:92-96 (one batch; OCR failed text) |
| docs/formats.rst:102-104 | Image-dominant docx/pptx decided from the OOXML, no rendering | fixed | F-fmt 9.1 PARTLY (pre-filter only); now formats.rst:98-106 |
| docs/formats.rst:104-109 | LibreOffice PDF is OCR'd, identity restored, routed_via "pdf"; needs OCR; never .xlsx | true | formats/office.py:331-335,451-475; kept formats.rst:99-106 |
| docs/formats.rst:107-108 | The route silently falls back to native parsing when anything is missing | fixed | F-fmt 9.1 PARTLY (routed_via native + reason/error); now formats.rst:104-106 |
| docs/formats.rst:111-116 | DOCX example prints DocumentFormat.DOCX and a page_count | true | ex: docs/formats.rst:109 pass |
| docs/formats.rst:118-119 | Without the advanced pipeline Office falls back to python-docx/openpyxl/python-pptx | removed | F-fmt 9.1 TRUE; now only mentioned for a bad table_style (tables.rst:81-83) |
| docs/formats.rst:124-125 | PDFs use the advanced pipeline; table extraction is always on | true | formats/pdf.py:84-96; kept formats.rst:25-26 |
| docs/formats.rst:126-127 | ocr_images maps to the pipeline's use_ocr and needs extract_images=True | fixed | F-pdf s2 FALSE; now loading.rst:64-67, quickstart.rst:97 |
| docs/formats.rst:127-130 | No pipeline import -> basic PyMuPDF; with OCR, textless pages rendered at 300 DPI | true | F-pdf s2 (P6); kept pdf.rst:132-134 (DPI detail dropped) |
| docs/formats.rst:132-134 | Needs pymupdf>=1.27.1, first with TEXT_CLIP, invisible-only redaction, textpage | true | pyproject.toml:47, pipelines/pymupdf_compat.py:1-12; kept pdf.rst:4-5 |
| docs/formats.rst:135-140 | Older PyMuPDF: each fallback said once per process (2 warnings, 1 INFO) | true | F-pdf s2 (P7); kept pdf.rst:130-132 |
| docs/formats.rst:142-149 | PyMuPDF prints to stdout; CLI mutes the advert, logs MuPDF on stderr; library does not | true | cli.py:16-41 (P1); kept pdf.rst:125-130, troubleshooting.rst:78-81 |
| docs/formats.rst:159-173 | Headings (title, levels, json level), lists (bullets, sequences) and captions | true | F-pdf s2 (P2); kept pdf.rst:14-35 |
| docs/formats.rst:174-177 | Superscripts ^x^; ordinals inline; raised footnote becomes [^1]: | true | F-pdf s2 (body mark stays ^1^); kept pdf.rst:36-40 (says so) |
| docs/formats.rst:178-190 | Ligatures, hyphen rule, CJK joins, emphasis, escaping, list-kind blank line | true | F-pdf s2 (P2), utils/markdown.py:1-125; kept pdf.rst:45-61 |
| docs/formats.rst:194-204 | /Rotate as displayed; columns read in order; spanning items; sidebars after | true | F-pdf s2 (P3), pipelines/pdf_layout.py; kept pdf.rst:66-73 |
| docs/formats.rst:205-217 | Column evidence; row layouts and mixed layouts keep order; nothing dropped or repeated | true | F-pdf s2 (omits >400 items); kept pdf.rst:74-79 (adds it) |
| docs/formats.rst:218-220 | Sideways text is never a heading or title and not the largest size | true | pipe:2694-2728; kept pdf.rst:21-22 |
| docs/formats.rst:225-250 | soffice converts doc/rtf->docx, ppt/pps->pptx, xls->xlsx, then OfficeProcessor | true | formats/legacy.py:67-91 (probe, all five); kept formats.rst:120-124 |
| docs/formats.rst:252-255 | Format and filename restored; converted_from/to; no LibreOffice -> ProcessingError | true | formats/legacy.py:57-102; kept formats.rst:123-127 |
| docs/formats.rst:255-256 | The same image extraction and OCR options apply to legacy files | fixed | F-fmt 9.1 PARTLY (table_style ignored); now formats.rst:126-127 |
| docs/formats.rst:261-267 | Pillow header (format, dimensions, mode, size); OCR text + text:image_description | true | formats/image.py:106-159,253-258; kept images.rst:9-32, output.rst:81-83 |
| docs/formats.rst:268-269 | extract_images=True (default for this processor) returns the PNG in images | fixed | F-fmt 9.1 PARTLY (load() default False); now images.rst:37-39 |
| docs/formats.rst:271-272 | Without pillow-heif, .heic/.heif cannot be opened | true | F-fmt s4 probe; kept images.rst:7-8, troubleshooting.rst:83 |
| docs/formats.rst:283-287 | Text files: stdlib, never OCR; .txt utf-8 default, short all-caps lines -> headings | true | formats/text.py:3-4,77-80,237-241; kept formats.rst:139-141 |
| docs/formats.rst:288-289 | .csv delimiter sniffed, overridable with the delimiter argument | fixed | probe: argument ignored; now formats.rst:142-144, loading.rst:69-71 |
| docs/formats.rst:290 | .tsv works like CSV with a tab delimiter | fixed | probe: .tsv always fails; now formats.rst:145-146 |
| docs/formats.rst:291-294 | .json nested Markdown (data_type, item_count); .jsonl per record, bad lines warned | true | formats/text.py:174-229; kept formats.rst:147-150 |
| docs/formats.rst:296-300 | load("data.csv", delimiter=";") sets the delimiter | fixed | probe: delimiter ignored; now loading.rst:69-71 (example without it) |
| docs/formats.rst:307-310 | HTML: BeautifulSoup + markdownify if available, else SimpleHTMLToMarkdown; counts | true | formats/markup.py:218-266; kept formats.rst:163-166 (not a dependency) |
| docs/formats.rst:311-316 | XML heading tree via defusedxml; Markdown as-is, front matter with PyYAML; counts | true | formats/markup.py:274-352; kept formats.rst:167-172 (adds --- caveat) |
| docs/formats.rst:321-327 | EML via stdlib email: From/To/Cc/Subject/Date; subject heading, headers, body | true | formats/email.py:23,72-81,163-178; kept formats.rst:177-181 |
| docs/formats.rst:323-325 | Prefers text/plain, else HTML with the converter "used for web pages" | fixed | F-fmt 9.1 PARTLY (all text parts joined); now formats.rst:178-180 |
| docs/formats.rst:327-328 | EML also supports TEXT and JSON output formats | removed | F-fmt 9.1 PARTLY (processor-only renderings); dropped |
| docs/formats.rst:330-334 | .eml is registered only when module and enum exist; loader works without it | removed | F-fmt 9.1 PARTLY (that fallback cannot happen); note dropped |
| docs/formats.rst:339-341 | No credentials for plain extraction; OCR per call via extract_images/ocr_images | fixed | F-fmt 9.1 PARTLY; now formats.rst:47, loading.rst:60-68 |

## docs/ocr.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/ocr.rst:4-7 | Structured output by default; text docs need no credentials; OCR only for images | true | F-ocr B1; kept ocr.rst:4-6 |
| docs/ocr.rst:17-46 | OCR(provider="openai", *, api_key=None, **config); OPENAI_API_KEY; read(), read_one() | true | ocr/__init__.py:82-132; kept ocr.rst:83-111 (ex: ocr.rst:85 pass) |
| docs/ocr.rst:33 | interpretation.summary is None for detail="raw" | fixed | F-ocr B1 PARTLY (Vertex: no interpretation); now ocr.rst:96-97,135-137 |
| docs/ocr.rst:48-53 | Provider names; kwargs go to OCRConfig; task coerced; detail validated (ValueError) | true | ocr/__init__.py:41-97; kept ocr.rst:99-103 (adds TypeError) |
| docs/ocr.rst:59-60 | One OCRResult per input image, in input order | true | ocr/openai.py:334, vertex_ai.py:258; kept ocr.rst:88 |
| docs/ocr.rst:60-61 | OCRResult.text is always populated | fixed | F-ocr B1 PARTLY ("" for refusals and failures); now api/ocr.rst:80 |
| docs/ocr.rst:61-63 | document: OCRPage for LLMs and for Tesseract (empty interpretation); None free-form | fixed | F-ocr B1 PARTLY (None; failed Tesseract image); now ocr.rst:137-138, api/ocr.rst:81-84 |
| docs/ocr.rst:65-67, 74 | raw is always present and always verbatim | fixed | F-ocr B1 PARTLY (recovery answer lands in raw.text); now api/schema.rst:17-20 |
| docs/ocr.rst:75-89 | raw fields (text ... has_handwriting); interpretation type/summary/confidence/legibility | true | ocr/schema.py:1197-1661; kept ocr.rst:119-127 |
| docs/ocr.rst:91-94 | interpretation is None for detail="raw", Tesseract, or a failed parse | fixed | F-ocr B1 PARTLY (OpenAI only asked); now ocr.rst:135-138 |
| docs/ocr.rst:96-99 | document_type is one of 16 values | true | ocr/schema.py:1537-1541 (probe: 16); kept ocr.rst:123-125 |
| docs/ocr.rst:101-110 | List of the other raw indexes and interpretation anchors | fixed | F-ocr B1 PARTLY (omits key_findings, visual_notes); now ocr.rst:123-127 |
| docs/ocr.rst:116-140 | task per construction or call; tasks one per image, same length, wins; task values | true | ocr/openai.py:817-825, base.py:70-76; kept ocr.rst:108-111,148-164 |
| docs/ocr.rst:142-144 | language is a config field with a per-call override; no multilingual task | fixed | F-ocr B1 PARTLY (Tesseract ignores the per-call value); now ocr.rst:110-111 |
| docs/ocr.rst:150-155 | detail="raw": interpretation None, raw fully populated | fixed | F-ocr B1 PARTLY; now ocr.rst:135-137 (Vertex drops it, OpenAI asks) |
| docs/ocr.rst:157-165 | structured=False: free-form Markdown, document None; both settable on facade and call | true | F-ocr B1 (probe); kept ocr.rst:99-111,136-137 |
| docs/ocr.rst:171-175 | html preferred; flat grid and markdown fallback remain populated | fixed | F-ocr B1 PARTLY (markdown never requested); now tables.rst:273-275, ocr.rst:120-121 |
| docs/ocr.rst:177-183 | Table has html, headers/rows, illustrative, row_count | true | ocr/schema.py:1205-1231; kept tables.rst:273-278 |
| docs/ocr.rst:192-194 | OCRResult.text is rendered safe; structured fields keep what the model returned | true | F-ocr B1 (next paragraph: Table.html cleaned); kept ocr.rst:301-303 |
| docs/ocr.rst:196-214 | Table.html cleaning: allowlist, removals, <br>, caption, pipe tables, bounded spans | true | F-ocr A9 (all reproduced); kept ocr.rst:305-323 |
| docs/ocr.rst:216-244 | Escaping of OCR text: <, controls, entities, code, links, line starts, Office cells | true | F-ocr B1 (probe); kept ocr.rst:325-353 |
| docs/ocr.rst:250-256 | Refusal signals: OpenAI message.refusal; Gemini SAFETY ... SPII | true | ocr/openai.py:186-195, vertex_ai.py:64-88; kept ocr.rst:196-198 |
| docs/ocr.rst:257-274 | Whole-answer refusal patterns in 7 languages; the examples kept or dropped | true | F-ocr A7 (24/24 examples); kept ocr.rst:199-216 |
| docs/ocr.rst:275-281 | No-content structured answer -> free-form recovery; ocr_refusal; page marker if ink | true | ocr/base.py:381-429,473-543; kept ocr.rst:217-223 |
| docs/ocr.rst:283-297 | non_content_judge via OCR() or OCRConfig; <= 600 chars; >= 0.5 drops; None keeps | true | ocr/refusal.py:367-390; kept ocr.rst:225-241 (adds 0.3 flag, no cache) |
| docs/ocr.rst:297-299 | OCR cache keys on the judge's qualified name and version | true | ocr/cache.py:152-165; kept caching.rst:28-29 |
| docs/ocr.rst:301-311 | ocr_issues: refused, provider_refused, failed, withheld, suspected, errors, locations | true | ocr/usage.py:72-75,174-295 (probe); kept ocr.rst:256-262, output.rst:125-131 |
| docs/ocr.rst:311-314 | Failed/withheld not cached; cache_dir skips failed docs; provider refusal 10 min | fixed | F-ocr B1 PARTLY (A12.8: an image file whose OCR raised is cached); now caching.rst:112-114 |
| docs/ocr.rst:314-315 | A per-image failure of any provider is flagged metadata["failed"] | true | ocr/openai.py:345-366, tesseract.py:295-303; kept ocr.rst:248-249 |
| docs/ocr.rst:315-318 | An engine that cannot run raises OCREngineError from load(); CLI exits non-zero | fixed | probe: ProcessingError, __cause__ OCREngineError; now ocr.rst:251-254 |
| docs/ocr.rst:327-345 | OpenAI: json_schema output, key + [ocr], gpt-5.4-mini, model=/base_url=/env var | true | ocr/openai.py:289-291,413-446; kept ocr.rst:24-30 |
| docs/ocr.rst:344-345 | Model knobs: constructor argument, then OCRConfig field, then default | fixed | F-ocr B1 PARTLY (loader passes its own); now ocr.rst:61-62, api/loader.rst:46-49 |
| docs/ocr.rst:350-361, 365 | vertex_ai = gemini; ADC; gemini-3.1-flash-lite-preview, global; env vars; OCR("gemini") | true | ocr/vertex_ai.py:164-178,296-329,820-821; kept ocr.rst:31-35,75-76 |
| docs/ocr.rst:366 | OCR("vertex_ai", project=..., model=...) | fixed | probe: TypeError (project not an OCRConfig field); now ocr.rst:103-106 |
| docs/ocr.rst:371-374, 382-385 | Tesseract raw only; language names mapped (chinese -> chi_sim+chi_tra); English default | true | ocr/tesseract.py:26-38,226-232; kept ocr.rst:137-138,168-187 |
| docs/ocr.rst:376-378 | Tesseract install: pip install "doc2mark[ocr]" | fixed | F-ocr B1 PARTLY; now installation.rst:62-75, ocr.rst:37 |
| docs/ocr.rst:391-396 | max_concurrency caps parallel image calls in batch_as_completed | true | ocr/openai.py:330-333; kept ocr.rst:271-274 |
| docs/ocr.rst:398-401 | config, then OCR_MAX_CONCURRENCY, then LangChain pool "typically ~12" | fixed | F-ocr B1 PARTLY (min(32, CPUs+4)); now ocr.rst:271-273 |
| docs/ocr.rst:407-426 | Loader uses OCR with ocr_images=True; ocr_provider=None or --ocr none turn it off | true | core/loader.py:270-272, cli.py:207-210; kept ocr.rst:8-9, cli.rst:42-43 |
| docs/ocr.rst:432-436 | Inert fields: one DeprecationWarning at construction when set to a non-default | fixed | F-ocr B1 PARTLY (hidden by default filters); now ocr.rst:292-296 |
| docs/ocr.rst:436-437 | enhance_image and detect_layout remain live for Tesseract | true | ocr/tesseract.py:170-174,490-495; kept ocr.rst:295-296 |
| docs/ocr.rst:437-440 | Live knobs: model, task, language, max_concurrency, structured controls | fixed | F-ocr B1 PARTLY (Vertex ignores model); now api/ocr.rst:18-73 per provider |

## docs/ocr_policy.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/ocr_policy.rst:4-9 | Routing is content-based and automatic; ocr_images implies extract_images | true | F-pdf s1 (with a provider); kept ocr_policy.rst:4-9 |
| docs/ocr_policy.rst:20-23 | Only a true image page is sent to an LLM vision model | fixed | F-pdf s1 PARTLY (text-route pictures too); now ocr_policy.rst:20-26 |
| docs/ocr_policy.rst:23-25 | Untrusted layers (garbled, invisible OCR layer, outlines) send that page to OCR | true | core/strategy.py:514-540; kept ocr_policy.rst:23-26 |
| docs/ocr_policy.rst:29-47 | Four layers; decide_doc_strategy/decide_page_route are the single source of truth | true | core/strategy.py:148-175,514-540; kept ocr_policy.rst:28-48 |
| docs/ocr_policy.rst:53-58 | decide_doc_strategy examples: image, text, text, image | true | F-pdf P0; ex: docs/ocr_policy.rst:52 pass |
| docs/ocr_policy.rst:63-109 | Constants (0.55, 200, 3.0/2.0, 0.3, 0.1/3, 0.5, 0.7); document rule; garbled pages | true | core/strategy.py:41-83; kept ocr_policy.rst:64-110 |
| docs/ocr_policy.rst:115-118 | text_weight: legible non-space chars; CJK ideograph 3, kana/hangul 2 | true | core/strategy.py:274-322; kept ocr_policy.rst:116-121 |
| docs/ocr_policy.rst:118-120 | Six statements measure ~290 EN and 260 ZH, raw counts 339 vs 91 | removed | F-pdf s1 UNVERIFIABLE (no fixture); replaced by a qualitative line |
| docs/ocr_policy.rst:125-150 | Per-page measures (coverage union, painted weight, quality, uncaptured ink); log line | true | pdf_routing.py:281-679, pipe:2018-2020; kept ocr_policy.rst:126-151 |
| docs/ocr_policy.rst:155-181 | First matching rule: searchable_scan, illegible_text_layer, no_text_layer (50, 5 %, 0.1 %) | true | core/strategy.py:474-540 (text_weight units); kept ocr_policy.rst:156-182 |
| docs/ocr_policy.rst:182-191, 205-206 | image_dominant_page (0.825, <100), dense_text_page (<0.275, >=300); hysteresis | true | core/strategy.py:527-539; kept ocr_policy.rst:183-207 |
| docs/ocr_policy.rst:193-203 | Override pages keep legible lines the OCR missed; garbled lines are not kept | true | pdf_routing.py:1100-1148; kept ocr_policy.rst:194-209 (adds chrome) |
| docs/ocr_policy.rst:206-212 | Traditional-Chinese deck example: ~160 units per page, densest slide stays image | removed | F-pdf s1 UNVERIFIABLE (no fixture); example dropped |
| docs/ocr_policy.rst:214 | Without OCR every page takes the text path | true | pipe:2237,2406-2407; kept ocr_policy.rst:211 |
| docs/ocr_policy.rst:219-224, 233 | Image route: one 150 DPI PNG, text layer not also emitted; page_markdown requested | true | pipe:29,2147-2149,2325,2409-2432; kept ocr_policy.rst:216-234 |
| docs/ocr_policy.rst:226-231 | Empty OCR (blank, refusal, failure): text layer + warning; marker if ink; unread_pages | fixed | F-pdf s1 PARTLY (failed render: text route); now ocr_policy.rst:223-232 |
| docs/ocr_policy.rst:235-237 | Batches of <= 32 (or 2x max_concurrency) and 128 MiB, released when answered | fixed | F-pdf s1 PARTLY (128 MiB triggers a flush); now ocr_policy.rst:236-241 |
| docs/ocr_policy.rst:238-240 | 400-page scan: 2.7 GB peak if held, 0.26 GB streamed | removed | F-pdf s1 UNVERIFIABLE; replaced by E2E bound (160 pages < 768 MB), :239-240 |
| docs/ocr_policy.rst:240-242 | Page order kept; identical renders are one request | fixed | F-pdf s1 PARTLY (not with context_pages >= 1); now ocr_policy.rst:241-244 |
| docs/ocr_policy.rst:247-256 | Text route: table finder + renderer, block classification, pictures OCR'd once | true | pipe:2451-2476,4224-4268; kept ocr_policy.rst:249-258 |
| docs/ocr_policy.rst:261-283 | Placements, tiles (1 pt, 5 %, 150-300 DPI), shown (12 pt, clip-aware, 12 px) | true | pdf_images.py:41-306; kept ocr_policy.rst:263-285 |
| docs/ocr_policy.rst:284-310 | plain/text/shapes classes, small-picture rule, one request per content | true | pdf_images.py:68-75,403-482, pipe:2259-2365; kept ocr_policy.rst:286-312 |
| docs/ocr_policy.rst:312-339 | Placeholders and ocr_images keys; shapes OCR'd w/o text layer; furniture; routing count | true | pipe:1104-1144,2209-2212,4326-4334; kept ocr_policy.rst:314-341 |
| docs/ocr_policy.rst:341-348 | Every answer cached; only failed and withholding results are asked again | fixed | F-pdf s1 PARTLY (unjudged answers too); now ocr_policy.rst:343-350 |
| docs/ocr_policy.rst:348-350 | Provider refusal replayed for refusal_ttl_seconds (10 min); hits don't extend it | true | ocr/cache.py:37-44,216-220; kept ocr_policy.rst:350-352 |
| docs/ocr_policy.rst:351-356 | cache_dir skipped for failed OCR, unread pages, provider refusals; INFO log | fixed | F-pdf s1 PARTLY (also judge failures); now ocr_policy.rst:353-358 |
| docs/ocr_policy.rst:361-386 | Garbage glyph rules; prominence (size/body)^2 capped at 4^2; garbled = >=3 and >=10 % | true | core/strategy.py:180-386; kept ocr_policy.rst:363-388 |
| docs/ocr_policy.rst:388-389 | With an OCR provider, a garbled page is OCR'd from its render | fixed | F-pdf s1 PARTLY (needs ocr_images=True too); now ocr_policy.rst:390-391 |
| docs/ocr_policy.rst:390-399 | Without OCR kept + text_layer_quality + warning; pages decide alone; test-table.pdf | true | pipe:2099-2120, F-pdf P5; kept ocr_policy.rst:392-402 |
| docs/ocr_policy.rst:408-432 | Legibility judge contract: OCR on, once per page, >= 20 chars, < 0.7, bad values kept | true | core/strategy.py:71-72,402-443; kept ocr_policy.rst:411-437 |
| docs/ocr_policy.rst:437-470 | Invisible-text classes, in-doubt keep, OCR'd picture replaces, hidden_text + warning | true | pdf_routing.py:488-756 (P4); kept ocr_policy.rst:442-475 |
| docs/ocr_policy.rst:472-478 | Limits; "PyMuPDF older than 1.27" can leak hidden text into cells | fixed | F-pdf s1 nit (capability arrived in 1.27.1, pymupdf_compat.py:1-12); now ocr_policy.rst:480 |
| docs/ocr_policy.rst:486-488, 492-493 | ocr_routing shape (differing pages only); hidden_text pages + lengths | true | pipe:2087-2094, pdf_routing.py:668 (P4); kept ocr_policy.rst:491-498 |
| docs/ocr_policy.rst:489-491 | text_layer_quality entries: garbage_ratio, garbage_glyphs, judge_legibility, action | fixed | F-pdf s1 PARTLY (omits page, legible); now ocr_policy.rst:494-496 |
| docs/ocr_policy.rst:495-497 | A text-less document's warning points at the pages that need OCR | fixed | F-pdf s1 PARTLY (one warning, no pages); now ocr_policy.rst:500-503 |
| docs/ocr_policy.rst:499-501 | cache_dir key includes the legibility judge and ROUTING_VERSION | true | core/loader.py:646-657; kept ocr_policy.rst:505-507 |
| docs/ocr_policy.rst:507-517 | Office route: .docx/.pptx only; needs ocr_images + provider; OOXML signals, no render | true | formats/office.py:330-364; kept ocr_policy.rst:512-523 |
| docs/ocr_policy.rst:519-520 | PPTX signals: mean picture-shape coverage and text per slide | fixed | F-pdf s1 PARTLY (backgrounds, groups, layouts); now ocr_policy.rst:525-529 |
| docs/ocr_policy.rst:521-524 | DOCX signals: inline pictures only; floating images undercounted on purpose | fixed | F-pdf s1 FALSE (floating counted); now ocr_policy.rst:530-533 |
| docs/ocr_policy.rst:526-531 | "image" -> LibreOffice PDF -> PDF pipeline; identity restored; routed_via "pdf" | fixed | F-pdf s1 PARTLY (PDF must also route image); now ocr_policy.rst:535-540 |
| docs/ocr_policy.rst:531-533 | Never raises; any failure falls back cleanly to native extraction | fixed | F-pdf s1 PARTLY (routed_via native + reason); now ocr_policy.rst:540-545 |
| docs/ocr_policy.rst:538-558 | auto job-router: classify then act; verbatim master rule; screenshot triple gate | verified-by-reading-only | ocr/base.py:103-139 prompt; model behaviour not run; kept :550-570 |
| docs/ocr_policy.rst:560-597 | Four policies (VERBATIM, SCREENSHOT, DESCRIBE, SKIP); real tables and code rule | verified-by-reading-only | ocr/base.py:111-139 prompt; kept ocr_policy.rst:572-609 |
| docs/ocr_policy.rst:602-607 | With context: non-verbatim only at self_confidence >= 0.7 and high legibility | verified-by-reading-only | ocr/base.py:103-109,142-150; kept :614-621 (adds no-context clause) |
| docs/ocr_policy.rst:612-616 | router_invariants: after-the-fact CI/eval assertion guaranteeing no withholding | fixed | F-pdf s1 PARTLY (withholding checked live, redo); now ocr_policy.rst:626-634 |
| docs/ocr_policy.rst:618-627 | Listed invariants (screenshot only, 0.7/high, fidelity, primary_date, substrings) | true | ocr/schema.py:1931-2040; kept ocr_policy.rst:636-645 |
| docs/ocr_policy.rst:632-657 | page_markdown for renders; [see table]; coverage >= 0.85 else verbatim; hidden tail | true | ocr/base.py:220-237, schema.py:1697-1735,1900; kept ocr_policy.rst:650-675 |
| docs/ocr_policy.rst:662-681 | Only enable OCR: deck -> image, report -> text + per-page OCR, pptx image route | true | F-pdf s1; kept ocr_policy.rst:680-700 (ex: ocr_policy.rst:684 pass) |

## docs/judge.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/judge.rst:4-9 | Three hooks, asked only about open cases; without a judge the rules decide as before | true | F-cct B5; kept judge.rst:4-9 |
| docs/judge.rst:14-23 | Legibility: unflagged layers, whole text <= 1,500 chars else 3 x 500; OCR on only | true | judge/questions.py:110-134 (probe); kept judge.rst:14-23 |
| docs/judge.rst:25-35 | Boilerplate: asked about rule-kept repeats only; thinned to first copy; never the last | true | F-cct A7 (probe); kept judge.rst:25-37 |
| docs/judge.rst:35-36 | Titles, per-page labels, unit notes and disclaimers keep every copy | fixed | F-cct B5 PARTLY (question framing only); now judge.rst:35-37 |
| docs/judge.rst:40-43 | The deterministic check fires only on a whole-answer first-person refusal | fixed | F-cct B5 PARTLY (also "no text" statements); now judge.rst:40-43 |
| docs/judge.rst:43-51 | <= 600-char answers; >= 0.95 no content; 0.90-0.95 suspected, counted with locations | true | ocr/refusal.py:53,367-390, usage.py:287-292; kept judge.rst:43-51 |
| docs/judge.rst:51-52 | The threshold sits above the measured overlap of refusals and real answers | true | je (overlap ~0.85-0.95; only >= 0.95 acts); kept judge.rst:51-52,280 |
| docs/judge.rst:55-56 | None, an exception or a value outside [0, 1] leaves the rule's decision (all hooks) | fixed | F-cct B5 PARTLY (non_content not range-checked); now judge.rst:55-59, api/judge.rst:9-11 |
| docs/judge.rst:61-73 | pip install, key, --judge typesafe; judge="typesafe"; extra.get("judge") | true | ex: docs/judge.rst:66, :72 pass |
| docs/judge.rst:75-76 | DOC2MARK_JUDGE=typesafe when no judge=/--judge; "none" switches off; default none | true | judge/__init__.py:28-54; kept judge.rst:78-79 |
| docs/judge.rst:78-91 | judge= object hooks wired, explicit hooks win; TypeSafeJudge(hooks, cache_dir, 2.0, 4) | true | core/loader.py:183-184,337-344; ex: docs/judge.rst:89 pass |
| docs/judge.rst:96-104 | No SDK import without extra; one warning; 401/403 off; failures -> rules + one warning | true | judge/typesafe.py:272-293,434-451, core/loader.py:559-562; kept :100-108 |
| docs/judge.rst:105-106 | 2 s per attempt, one retry in a 4 s budget: at most about 4 s per question | fixed | F-cct B5 PARTLY (~4.5 s, up to ~6 s); now judge.rst:109-111 |
| docs/judge.rst:106-107 | All questions of a document are asked concurrently | fixed | F-cct B5 PARTLY (header lines one by one); now judge.rst:111-112 |
| docs/judge.rst:107-109 | Three failures in a row -> one-minute pause; once per process with --parallel | true | judge/typesafe.py:58-62,434-451, cli.py:585; kept judge.rst:112-115 |
| docs/judge.rst:111-115 | Never fails a conversion; failures not cached; an unavailable judge keyed as none | true | F-cct B5 (bad hooks raise at construction); kept judge.rst:117-121 |
| docs/judge.rst:120-125 | Noul over named states, numbers in words; pinned jev-1.13.0; versioned questions | true | judge/questions.py:19,48-174; kept judge.rst:126-131 |
| docs/judge.rst:127 | Jev varies about +-0.04 run to run near 0.5 | fixed | je: most items ~0.01, two moved 0.08 and 0.14; now judge.rst:133-134 |
| docs/judge.rst:127-130 | Verdict cache: $DOC2MARK_JUDGE_CACHE else ~/.cache/doc2mark/judge; False = memory | fixed | F-cct B5 PARTLY ($XDG_CACHE_HOME first); now judge.rst:135-137 |
| docs/judge.rst:130-132 | A re-run sends nothing; OCR/document caches key on model, question and threshold | true | F-cct B5 (probe: 0 calls); kept judge.rst:137-139 |
| docs/judge.rst:134-136 | Pages and batch answers asked concurrently; timeout 2 s; one retry within 4 s | fixed | F-cct B5 PARTLY (retry only starts within 4 s); now judge.rst:141-143 |
| docs/judge.rst:138-143 | SDK wire log at DEBUG/INFO; doc2mark drops DEBUG; -v shows INFO; TYPESAFE_LOG_LEVEL | true | judge/typesafe.py:100-117, cli.py:25-31; kept judge.rst:145-150 |
| docs/judge.rst:148-151 | Sent to api.typesafe.ai: <= 1,500 chars per page, judged lines, short OCR answers | true | F-cct B5; kept judge.rst:155-159 (adds TYPESAFE_BASE_URL) |
| docs/judge.rst:151-153 | Jev not trained on customer data; DPA applies; zero retention only for enterprise | verified-by-reading-only | external legal terms (F-cct B5 UNVERIFIABLE); kept judge.rst:159-161 |
| docs/judge.rst:154 | doc2mark never logs the API key | true | judge/typesafe.py:434-443; kept judge.rst:162 |
| docs/judge.rst:159 | Priced $0.042 per million input tokens, output free | verified-by-reading-only | price constant judge/questions.py:22; service price external (je); kept :167 |
| docs/judge.rst:159-160 | About 300 tokens of fixed overhead per question | fixed | je: about 420 to 470 tokens; now judge.rst:167-168 |
| docs/judge.rst:160-162 | Averages: page-text 687 tokens ($0.000029), header line 573, OCR answer 446 | fixed | je: 684 on the public sets, others hold; now judge.rst:168-170 |
| docs/judge.rst:162-163 | Laptop latency p50 ~210 ms, p95 270-300 ms (one run 530 ms) | fixed | je: p50 holds, p95 247-269 ms (spark2); now judge.rst:170-171 |
| docs/judge.rst:164-165 | extra["judge"] holds asked, cached, fresh, failed, input_tokens, cost_usd | fixed | F-cct B5 (+name, model; only if asked; replayed); now judge.rst:172-174 |
| docs/judge.rst:167-191 | Whole docs 12-page/8-slide/3-page: questions, tokens, cost; +1.3/+1.2/+0.6 s | fixed | je: counts/tokens/cost hold, +1.16/+0.99/+0.39 s; now judge.rst:176-202 |
| docs/judge.rst:192-196 | 30-page TC deck: 2 header lines, 1,135 tokens, $0.00005, +0.6 s | verified-by-reading-only | je: private deck, not re-measurable; kept with provenance :176-178,203-207 |
| docs/judge.rst:198-199 | A second conversion is answered from the verdict cache: no request | true | je: 0 requests on the re-run; kept judge.rst:209-210 |
| docs/judge.rst:204-213 | Labelled sets TRAIN/TEST/EXTERNAL (60); non-content policy fixed; positive class | true | je; kept judge.rst:215-224 |
| docs/judge.rst:221-238 | Legibility and boilerplate rows of the accuracy table (18 figures each) | true | je: all figures hold; kept judge.rst:232-249 |
| docs/judge.rst:239-240, 242-247 | non_content rows except TRAIN rule + Jev | true | je: hold; kept judge.rst:250-258 |
| docs/judge.rst:241 | non_content TRAIN rule + Jev: 95.6 / 97.6 / 93.2 % | fixed | je: 94.5 / 97.6 / 90.9 % (one refusal at 0.94); now judge.rst:252,267-270 |
| docs/judge.rst:249-254 | Thresholds 0.8/0.7/0.95/0.90; 17 of 20 and 50 rule-decided lines; TEST 62/0/2 | true | je: all hold (--calibrate picks them); kept judge.rst:260-265 |
| docs/judge.rst:256-259 | TC deck: brand line 29 -> 1 copies; logo text asked; garbled variants all caught | verified-by-reading-only | je: private deck; kept judge.rst:272-274 without the logo-text clause |
| docs/judge.rst:261-262 | Legibility margins: garbage <= 0.77, legible >= 0.87, EXTERNAL lorem 0.89 | fixed | je: 0.79, 0.87, 0.87; now judge.rst:276-277 |
| docs/judge.rst:263-264 | Boilerplate content lines score up to 0.68 on TRAIN | fixed | je: 0.66; now judge.rst:278-279 |
| docs/judge.rst:264-265 | Refusals and real answers overlap between 0.87 and 0.96 | fixed | je: about 0.85 to 0.95; now judge.rst:280 |
| docs/judge.rst:265-270 | TEST 1 real answer lost; EXTERNAL 4 of 9 kept (3 flagged); no undo of false alarms | true | je: all hold; kept judge.rst:280-286 |
| docs/judge.rst:272-274 | TypeSafe limits: 100K tokens, 40 rps, 32K state; English primary, CJK weaker | verified-by-reading-only | external service docs (je: not measurable); kept judge.rst:288-290 |

## docs/tables.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/tables.rst:9-13, 21-27 | Digital docs: rule-based tables from PDF geometry and OOXML merge attributes | true | pdf_tables.py:390-442, OP:1585-2694; kept tables.rst:4-6,129-140 |
| docs/tables.rst:29-35 | Native extraction reproduces every span; OCR flattens narrow merges; never via vision | removed | no committed measurement; garbled pages route to OCR (F-pdf P5); dropped |
| docs/tables.rst:40-56 | find_tables -> TableData; chars once per page to the smallest cell; 3 pt; baseline height | fixed | pdf_tables.py:50-133 TRUE; nit: textpage is 1.27.1+, now tables.rst:151; rest kept :145-161 |
| docs/tables.rst:58-79 | Overprint duplicates dropped; other overlaps kept by word; hidden layer over text dropped | true | pdf_tables.py:157-231,304-340; kept tables.rst:163-184 |
| docs/tables.rst:81-103 | Merged cells from drawn boxes (> 1 pt); which grids are tables (2x2, 85 %, ruled) | true | pdf_tables.py:52,390-442,534-637; kept tables.rst:186-208 |
| docs/tables.rst:105-128 | Text-strategy search gate and candidate checks (3x3, numbers, crossings, fill, TOC) | true | pdf_tables.py:805-986; kept tables.rst:210-233 |
| docs/tables.rst:129-132 | Touching blocks must lie inside, since the text output skips every touching block | fixed | F-pdf s3 PARTLY (false since #17); now tables.rst:234-237 (would be split) |
| docs/tables.rst:134-136 | Wrapped cell lines stay; prose, TOCs, key/value blocks, sidebars stay text | true | pdf_tables.py:894-918; kept tables.rst:239-241 |
| docs/tables.rst:137-140 | 1,391 pages measured: 18 tables, all real; adversarial pages keep every word | verified-by-reading-only | F-pdf s3 UNVERIFIABLE corpus; kept with provenance :242-246; code unchanged |
| docs/tables.rst:142-158 | Header rows above ruled cells; page continuation (8 % bands); bbox skipped by text | true | pdf_tables.py:708-788,1088-1187, pipe:2813-2822; kept tables.rst:248-264 |
| docs/tables.rst:163-174 | Office merges read from markup: DOCX gridSpan/vMerge (restart/continue), PPTX spans | true | OP:650-666,1585-1640,2242-2340; kept tables.rst:129-134 |
| docs/tables.rst:175-177 | XLSX merged_cells.ranges; spans re-clamped when columns are dropped | fixed | F-cct B4 PARTLY (rows too; empty origin ignored); now tables.rst:135-138 |
| docs/tables.rst:179-196 | All paths build TableData: pad, clamp, never hide a value (shrink), warn, is_complex | true | core/table.py:138-280; kept tables.rst:123-127 |
| docs/tables.rst:198-202 | minimal_html: tr per row, th for the first physical row, spans > 1, no continuations | true | core/table.py:425-451 (row 0 flagged by from_raw); kept tables.rst:15-16,79-80 |
| docs/tables.rst:202-203 | Span-free tables render as ordinary pipe Markdown | fixed | F-cct B4 PARTLY (PPTX always in the style); now tables.rst:11-13 |
| docs/tables.rst:205-222 | Cell escaping: breaks -> <br>, controls dropped, HTML and Markdown escapes | true | core/table.py:24-65 (probe); kept tables.rst:95-118 (ex: tables.rst:109 pass) |
| docs/tables.rst:211-212 | Full-width indentation stays in Markdown cells | removed | F-cct B4 PARTLY (first line loses U+3000); dropped |
| docs/tables.rst:227-240 | table_style maps to TableStyle; CLI --table-style markdown_grid | true | core/loader.py:181, cli.py:302-307; kept tables.rst:11-13,85-90 |
| docs/tables.rst:242 | The three values are accepted as string or enum | fixed | F-cct B4 PARTLY (exact lower case only); now tables.rst:80-83 |
| docs/tables.rst:244-254 | minimal_html default; markdown_grid comment + markers; styled_html inline styles | true | core/table.py:425-536; kept tables.rst:15-22 (ex: tables.rst:26 pass) |
| docs/tables.rst:276-278 | Office gridSpan/vMerge (or PDF geometry) yield colspan=3 and rowspan=2 here | removed | F-cct B4 (Office TRUE, PDF unverifiable); example now built from TableData |
| docs/tables.rst:278-323 | Exact minimal_html output; corner th kept; Canada row has no first cell | true | F-cct C1 (only the trailing blank line differs); ex: tables.rst:43 pass |
| docs/tables.rst:328-347 | Image tables go through OCR into raw.tables; Table views html ... row_count | true | ocr/schema.py:1205-1239; kept tables.rst:269-278 |
| docs/tables.rst:349-359 | Sanitiser: table tags only, colspan/rowspan/scope, integer spans, dangerous tags gone | true | ocr/schema.py:38-46,391-451; kept tables.rst:280-282, api/schema.rst:341 |
| docs/tables.rst:360 | The sanitiser tolerates a ```html code fence | removed | ocr/schema.py:95,407 (TRUE); detail dropped |
| docs/tables.rst:361-362, 367-377 | Fails closed ("" on bad input); to_markdown uses html, else markdown, else grid | true | ocr/schema.py:405-418,1715-1717; kept tables.rst:286-288 |
| docs/tables.rst:382-385 | Tables are inline in content and json_content items of type "table" | true | F-cct B4 (probe); kept tables.rst:293-294 (ex: tables.rst:298 pass) |
| docs/tables.rst:387-396 | Each json table item is "the <table>...</table> string with spans" | fixed | F-cct B4 (HTML only when merged); now tables.rst:293-294 (HTML or pipe table) |
| docs/tables.rst:398-415 | Vision path: OCR(...).read_one(); document.raw.tables; html/headers/rows; result.text | true | F-cct B4 (API probed; live output needs OpenAI); kept ocr.rst:83-97 |

## docs/contextual_ocr.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/contextual_ocr.rst:8-11, 21-22, 145-146 | Window {k-1, k, k+1} attached as context; the image is the only target | true | pipe:2151-2178, ocr/base.py:208-215; kept contextual_ocr.rst:8-22,141-142 |
| docs/contextual_ocr.rst:13-16 | Context makes terms consistent, keeps the language, informs the AUTO router | verified-by-reading-only | F-ocr B4 UNVERIFIABLE (model behaviour); kept contextual_ocr.rst:13-16 |
| docs/contextual_ocr.rst:23-25 | Both providers prepend _CONTEXT_PDF_INSTRUCTION | fixed | F-ocr B4 PARTLY (text part after image, before PDF); now contextual_ocr.rst:23-25 |
| docs/contextual_ocr.rst:27-39 | Quoted instruction; _ROUTER_CONFIDENCE_CLAUSE (>= 0.7 and "high") | true | ocr/base.py:103-109,208-215; kept contextual_ocr.rst:27-39 |
| docs/contextual_ocr.rst:44-65 | context_pages: int = 0 tier: 0 off (byte-identical), 1 renders, 2 pictures per page | true | ocr/base.py:276, pipe:924-927,2244-2261 (probe); kept contextual_ocr.rst:44-65 |
| docs/contextual_ocr.rst:67-73 | Tier read once from the OCR config; renders gated at >= 1, pictures at >= 2 | true | pipe:680,2244,2254; kept contextual_ocr.rst:67-69 (code line dropped) |
| docs/contextual_ocr.rst:78-89 | Clamped window via insert_pdf; tobytes(deflate=True, garbage=3) | true | pipe:2160-2165; kept contextual_ocr.rst:78-85 |
| docs/contextual_ocr.rst:91-94 | LRU of 4 by page; overlapping windows on adjacent pages do not rebuild work | fixed | F-ocr B4 PARTLY (each page builds its own); now contextual_ocr.rst:87-90 |
| docs/contextual_ocr.rst:96-98 | 18 MB guard chosen to stay under Gemini's ~20 MB inline cap | fixed | F-ocr B4 PARTLY (raw bytes; base64 adds a third); now contextual_ocr.rst:92-95 |
| docs/contextual_ocr.rst:98-113 | Failure -> None, OCR without context; raw base64; context_pdfs only when some has it | true | pipe:916-927,2168-2174; kept contextual_ocr.rst:95-109 |
| docs/contextual_ocr.rst:115-143 | OpenAI file block behind the gpt-4o/gpt-4.1/gpt-5/o1 gate; Gemini media part | true | ocr/openai.py:78-84,161-172, vertex_ai.py:102-114; ex: :116, :135 pass |
| docs/contextual_ocr.rst:151-157 | Context is additive; the rule-based layer stays verbatim | true | F-ocr B4 (read); kept contextual_ocr.rst:147-153 |
| docs/contextual_ocr.rst:162-184 | PDF sources only; OCRConfig(context_pages=1) on the loader; 2 for figures | true | F-ocr B4 (probe); ex: contextual_ocr.rst:164 pass |
| docs/contextual_ocr.rst:189-197 | One PDF per render (tier 1) and per figure (tier 2); capped at 18 MB | true | F-ocr B4 (18 MiB of raw bytes); kept contextual_ocr.rst:185-196 |

## docs/caching.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/caching.rst:4-16 | Opt-in ocr_cache skips provider calls; Memory (LRU, TTL), Redis ([redis]), NoOp | true | ocr/cache.py:440-589, __init__.py:103-327; kept caching.rst:4-40 |
| docs/caching.rst:18-20 | Every answer is cached, including empty and refusal/"no readable text" answers | true | ocr/cache.py:197-215; kept caching.rst:76-77 |
| docs/caching.rst:20-24 | Only failed and withholding results are asked again; such entries miss; INFO log | fixed | F-cct B1 PARTLY (unjudged answers too); now caching.rst:78-81 |
| docs/caching.rst:24-28 | Provider refusal kept refusal_ttl_seconds (600), not extended; recovery refusal too | fixed | F-cct B1 PARTLY (min(refusal TTL, ttl)); now caching.rst:68-71 |
| docs/caching.rst:29-31 | Key version ocr-cache-v6; entries of earlier versions are never read | true | ocr/cache.py:23-25,250-251; kept caching.rst:30-31 |
| docs/caching.rst:31-35 | cache_dir never expires; skips failed OCR, unread pages and provider refusals | true | core/loader.py:1290-1310 (+judge failures); kept caching.rst:103-114 |
| docs/caching.rst:42-103 | Memory quick start; factory (fallback memory/none/raise; "none" = off); loader wiring | true | ocr/cache.py:839-920; kept caching.rst:14-58 (ex: caching.rst:16, :47 pass) |
| docs/caching.rst:121-123 | A hit extends the expiry by ttl_seconds, up to max_refreshes times | fixed | F-cct B1 PARTLY (resets to now + ttl, capped); now caching.rst:63-65 |
| docs/caching.rst:125-141 | max_age (None = none), LRU max_entries, max_refreshes, refusal_ttl_seconds 600 | true | ocr/cache.py:375-398,443-451,849; kept caching.rst:36,63-71 |
| docs/caching.rst:146-187 | Redis example; ping() raises; native EX; cleanup() no-op; parameters and defaults | true | ocr/cache.py:599-695 (probe); kept caching.rst:37-39, autodoc api/cache.rst:18 |
| docs/caching.rst:192-200 | stats() on every backend; counters hits ... errors | true | ocr/cache.py:59-69; kept caching.rst:83-85 |

## docs/development.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/development.rst:8, 23 | pip install -e ".[all,dev,docs]"; sphinx -b html -W --keep-going | true | pyproject.toml extras, docs.yml; kept development.rst:7,114 |
| docs/development.rst:14-17 | python -m pytest also collects E2E; -m "not e2e" leaves them out | true | tests/e2e/conftest.py:25-30; kept development.rst:16-17 |
| docs/development.rst:28-34 | E2E drives the CLI and checks only outputs; auto e2e marker; Tesseract + soffice | true | tests/e2e/conftest.py, tests/e2e/Dockerfile:11-17; kept development.rst:17-29 |
| docs/development.rst:39-53 | Docker runner: hashed image, read-only copy, .[ocr,dev], strict, -m e2e, exit 90 | true | scripts/run_e2e_docker.sh; kept development.rst:35-47 (now D2M_E2E_EXTRAS) |
| docs/development.rst:55-62 | D2M_E2E_PASS_ENV forwards variable names only | true | scripts/run_e2e_docker.sh:44-51; kept development.rst:55-57 |
| docs/development.rst:67-76 | Local run; a missing tool skips, or fails with D2M_E2E_STRICT=1 | true | tests/e2e/conftest.py:148-160; kept development.rst:69-79 |
| docs/development.rst:81-109 | run_cli, e2e_dir, require_tool fixtures; pdfgen text_pdf/image_pdf; example test | true | tests/e2e/conftest.py:81-160, pdfgen.py:31-88; ex: development.rst:100 pass |
| docs/development.rst:114-116 | Docs workflow publishes to GitHub Pages; set the Pages source to GitHub Actions | true | .github/workflows/docs.yml; kept development.rst:119-120 (hint dropped) |

## docs/api/index.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/api/index.rst:4-5 | The API reference is complete for the public doc2mark API | fixed | old pages missed judge params and doc2mark.judge; now api/index.rst:4-19 + api/judge.rst |

## docs/api/convenience.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/api/convenience.rst:4-26 | Five module-level helpers wrap UnifiedDocumentLoader for one call | true | __init__.py:96-380; kept api/convenience.rst:4-30 |
| docs/api/convenience.rst:53-64, 124-136, 202-215, 270-285, 354-368 | Signatures of load, document_to_markdown and the three batch helpers | true | __init__.py:96-331; autodoc api/convenience.rst:50-58 |
| docs/api/convenience.rst:71-74 | output_format accepts "markdown", "html" or "text" | fixed | OutputFormat is markdown/json/text ("html" -> ValueError); now output.rst:44-45 |
| docs/api/convenience.rst:76-78, 147-148, 226-227, 299-300, 383-384 | extract_images extracts images as base64 data | fixed | F-fmt 9.1 (Office: bytes); now api/convenience.rst:35-37 (flags the docstrings), output.rst:23-29 |
| docs/api/convenience.rst:80-83, 150-152, 229-230, 302-303, 386-387 | ocr_images works only together with extract_images=True | fixed | implied (core/loader.py:630-634); now api/convenience.rst:34-35, loading.rst:64-67 |
| docs/api/convenience.rst:85-87, 154-155 | ocr_provider accepts "openai" or "vertex_ai" | fixed | also tesseract, gemini, None, instances; now loading.rst:26-29,85-88 |
| docs/api/convenience.rst:89-91, 157-158 | api_key None -> the provider's own environment lookup | true | ocr/openai.py:413; kept api/loader.rst:43-44 |
| docs/api/convenience.rst:93-95 | ocr_cache gives request-scoped caching of OCR results | fixed | F-cct B2 FALSE; now api/convenience.rst:36-37 (flags the docstrings), caching.rst:4-9 |
| docs/api/convenience.rst:97-99, 166-168 | **kwargs are forwarded to UnifiedDocumentLoader.load() | true | __init__.py:141-147 (probe: table_style= raises); kept loading.rst:85-91 |
| docs/api/convenience.rst:113-114, 143-145, 163-164 | document_to_markdown writes output_path (makes parents), prints unless show_progress=False | true | __init__.py:197-206; kept api/convenience.rst:18-20 |
| docs/api/convenience.rst:184-185 | batch_convert_to_markdown optionally saves .md files to output_dir | fixed | always save_files=True (__init__.py:257); now api/convenience.rst:21-24 |
| docs/api/convenience.rst:187-198, 253-254 | Result dicts carry at least a "success" key (info.get("success")) | fixed | core/loader.py:1018-1036 uses "status"; now loading.rst:118-126 |
| docs/api/convenience.rst:222-224 | batch_convert_to_markdown without output_dir does not save files | fixed | written next to the inputs (core/loader.py:938); now api/convenience.rst:22-24 |
| docs/api/convenience.rst:232-233, 305-306 | recursive defaults to True | true | __init__.py:214,268; autodoc api/convenience.rst:54-56 |
| docs/api/convenience.rst:244-245, 317-318, 398-399 | show_progress prints progress messages during batch processing | removed | batch progress goes to logger.info (core/loader.py:853-880); dropped |
| docs/api/convenience.rst:247-249, 323-325, 404-406 | **kwargs forwarded to batch_process / batch_process_files | true | __init__.py:249-259,306-316,371-380; kept api/convenience.rst:4-8 |
| docs/api/convenience.rst:263-266 | batch_process_documents gives HTML or plain-text output, or in-memory results | fixed | no HTML output format; now api/convenience.rst:25-27, output.rst:44-45 |
| docs/api/convenience.rst:292-293, 320-321, 375-377, 401-402 | output_dir ignored when save_files=False; save_files defaults to True | true | core/loader.py:988-996,1119-1124; kept loading.rst:112-116 |
| docs/api/convenience.rst:339-341, 410 | batch_process_files takes an explicit list and returns path -> result | true | core/loader.py:1056-1180; kept api/convenience.rst:28-29 (ex: :37 pass) |

## docs/api/loader.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/api/loader.rst:4-8 | Detects format by extension and dispatches to processors, including "optional email" | removed | F-fmt 9.1 (email always registered); intro dropped, formats.rst:4-45 lists all |
| docs/api/loader.rst:8-11 | One instance converts files with load() or in batches with opt-in threads, progress | true | core/loader.py:798-1180; kept api/loader.rst:4-5, loading.rst:93-138 |
| docs/api/loader.rst:22-48 | Constructor signature and defaults | fixed | F-ocr B6 PARTLY (no judge params); now api/loader.rst:9-36 (ex: api/loader.rst:9 pass) |
| docs/api/loader.rst:50-63 | Provider built eagerly; None/"none"/"disabled" = off; api_key or OPENAI_API_KEY | true | core/loader.py:149,245-335; kept api/loader.rst:38-44 |
| docs/api/loader.rst:64-66 | Without ocr_config a default config keeps the structured defaults | true | core/loader.py:233 (OpenAI knobs overridden); kept api/loader.rst:45-49 |
| docs/api/loader.rst:67-69 | cache_dir caches converted documents keyed by path, mtime, size and options | true | core/loader.py:1397-1407 (OCR model not keyed); kept caching.rst:99-104 (says so) |
| docs/api/loader.rst:70-71 | ocr_cache is a request-scoped cache for OCR image calls | fixed | F-cct B2 FALSE; now api/loader.rst:50-51, caching.rst:4-9 |
| docs/api/loader.rst:72-74 | table_style minimal_html (default), markdown_grid, styled_html | true | core/loader.py:181; kept api/loader.rst:68-69 |
| docs/api/loader.rst:79-81 | model gpt-5.4-mini; for vertex_ai the default is swapped for a Gemini model | fixed | F-ocr B6 PARTLY ("gemini" ignores model); now api/loader.rst:52-55 |
| docs/api/loader.rst:82-83 | temperature defaults to 0 | true | core/loader.py:43 (not sent to gpt-5); kept api/loader.rst:16, ocr.rst:61-63 |
| docs/api/loader.rst:84-85 | max_tokens defaults to 4096 | fixed | F-ocr B6 FALSE (8192); now api/loader.rst:17, ocr.rst:61 |
| docs/api/loader.rst:86-89, 119-124 | max_workers is the OCR-internal concurrency (5), separate from batch max_workers | fixed | F-ocr B6 FALSE (never used); now api/loader.rst:66-67, loading.rst:135-137 |
| docs/api/loader.rst:90-92 | prompt_template selects a built-in prompt preset | fixed | F-ocr B6 PARTLY (free-form answers only); now api/loader.rst:62-65 |
| docs/api/loader.rst:93-94 | timeout/max_retries apply to OCR requests | fixed | F-ocr B6 PARTLY (OpenAI only); now api/loader.rst:56-57, ocr.rst:65 |
| docs/api/loader.rst:95-96 | top_p, frequency_penalty, presence_penalty are OpenAI sampling controls | fixed | probe: never reach the request; now api/loader.rst:66-67, ocr.rst:77-78 |
| docs/api/loader.rst:97-98 | base_url targets OpenAI-compatible endpoints | true | ocr/openai.py:445-446; kept api/loader.rst:58-59 |
| docs/api/loader.rst:99-101 | project (GOOGLE_CLOUD_PROJECT) and location ("global") for Vertex AI | fixed | F-ocr B6 PARTLY (ignored for "gemini"); now ocr.rst:75-76, api/loader.rst:60-61 |
| docs/api/loader.rst:102-103 | default_prompt overrides the built-in templates | fixed | F-ocr B6 FALSE (never used); now api/loader.rst:66-67 |
| docs/api/loader.rst:108-117 | task/structured/detail override the config; None keeps it; structured default True | true | core/loader.py:218-243; kept api/loader.rst:45-49 |
| docs/api/loader.rst:131-147 | load() signature; FileNotFoundError; output_format strings normalised | true | core/loader.py:566-622; kept loading.rst:55-59, output.rst:170-172 |
| docs/api/loader.rst:148-150 | extract_images is only meaningful for Office and PDF inputs | fixed | F-fmt 9.1 (image files too); now api/loader.rst:78-79 (note); the load() docstring rendered there still says so: code issue #32 |
| docs/api/loader.rst:151-153 | ocr_images requires extract_images=True and a provider | fixed | F-ocr B6 FALSE (implied); now api/loader.rst:78-79, loading.rst:64-67 |
| docs/api/loader.rst:154-159 | show_progress; encoding utf-8; delimiter auto-detected when None | fixed | probe: delimiter argument ignored; now loading.rst:69-71, api/loader.rst:79-80 |
| docs/api/loader.rst:161-169 | Raises UnsupportedFormatError / ProcessingError; DOCX example | true | core/loader.py:624-628,746-748; kept output.rst:170-180; ex: formats.rst:109 pass |
| docs/api/loader.rst:173-185 | OCR example with prompt_template="table_focused" | fixed | F-ocr B6 PARTLY (no effect on structured output); now ocr.rst:70-73 |
| docs/api/loader.rst:207-209 | batch_process finds every supported file; keys in input order | fixed | probe: skips .htm and upper-case extensions; now loading.rst:110-118 |
| docs/api/loader.rst:212 | A missing input_dir raises FileNotFoundError | removed | core/loader.py:941-942 (TRUE); not stated in the new docs |
| docs/api/loader.rst:213-216 | output_dir defaults to input_dir; recursive defaults to True | true | core/loader.py:884-938; kept loading.rst:111-116 |
| docs/api/loader.rst:217-218 | save_files writes .md/.json and any extracted images to output_dir | fixed | new probe: a PDF's extracted pictures fail its save; now loading.rst:114-115 (known issue) |
| docs/api/loader.rst:219-227 | max_workers > 1 uses threads (capped at file count); progress_callback(done, total, path) | true | core/loader.py:830-880; kept loading.rst:135-138 |
| docs/api/loader.rst:236-251 | Success dict incl. images_extracted, tables_found (e.g. 1), pages | fixed | probe: tables_found is always 0; now loading.rst:122-129 |
| docs/api/loader.rst:253-261 | Failure dict: status "failed", error, format (suffix) | true | core/loader.py:1031-1036; kept loading.rst:126 |
| docs/api/loader.rst:263-267, 307-321 | batch_process_files: no recursive, no pages; order kept; no output_dir -> nothing written | true | core/loader.py:1056-1155; kept loading.rst:131-133 |
| docs/api/loader.rst:269-286, 323-335 | Batch examples with max_workers and progress callbacks | true | ex: docs/loading.rst:98 pass |

## docs/api/ocr.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/api/ocr.rst:5-16 | One facade over interchangeable providers; structured by default; text always filled | true | F-ocr B2 (text can be ""); kept api/ocr.rst:4-5, ocr.rst:80-92 |
| docs/api/ocr.rst:35-63 | OCR(provider="openai", *, api_key=None, **config_kwargs); example; key source per provider | true | ocr/__init__.py:82-132 (Vertex ignores api_key); kept ocr.rst:20-39,83-103 |
| docs/api/ocr.rst:65-77 | Any OCRConfig field; string task coerced; detail validated; ValueError lists options | true | ocr/__init__.py:41-62 (non-field -> TypeError); kept ocr.rst:99-103 |
| docs/api/ocr.rst:84-94 | read(images, *, task, tasks, language, structured, detail); read_one = read([x])[0] | true | ocr/__init__.py:99-132; kept ocr.rst:108-111 |
| docs/api/ocr.rst:96-99 | A tasks list of the wrong length raises ValueError; tasks wins over task | fixed | F-ocr B2 PARTLY (Vertex OCRError, Tesseract no check); now ocr.rst:109-111 |
| docs/api/ocr.rst:101-111 | language appended to the prompt; structured=False free-form; detail="raw" skips interp. | true | F-ocr B2 (LLM providers); kept ocr.rst:135-137,177 |
| docs/api/ocr.rst:115-164 | Task str enum: members, values, Task("table") is Task.TABLE; no multilingual task | true | ocr/base.py:65-76 (probe); autodoc api/ocr.rst:86-88, ocr.rst:148-151 |
| docs/api/ocr.rst:181-195 | Live LLM knobs table (model, temperature, max_tokens, base_url, response_model ...) | fixed | F-ocr B2 PARTLY; now api/ocr.rst:18-73 ("Used by" per provider) |
| docs/api/ocr.rst:197-202 | enhance_image/detect_layout read only by Tesseract; detect_tables not consumed | true | ocr/tesseract.py:94,170,490; kept api/ocr.rst:66-73 |
| docs/api/ocr.rst:204-211 | Deprecated fields: single DeprecationWarning; deprecated_llm_overrides() | fixed | F-ocr B2 PARTLY (hidden by default filters); now ocr.rst:292-296, api/ocr.rst:70-73 |
| docs/api/ocr.rst:213-221 | max_concurrency: LangChain default pool "typically around 12" | fixed | F-ocr B2 PARTLY (min(32, CPUs+4)); now ocr.rst:271-274 |
| docs/api/ocr.rst:223-227 | OCR_MAX_IMAGE_DIM resizes every image; an invalid value leaves images untouched | fixed | F-ocr B2 PARTLY (larger ones only, PNG, LLMs); now ocr.rst:275-276, images.rst:73-74 |
| docs/api/ocr.rst:229-236, 261-283 | Examples: env var + max_concurrency=32; reading raw and interpretation | true | F-ocr B2; kept ocr.rst:83-97,281-287 (ex: ocr.rst:282 pass) |
| docs/api/ocr.rst:246-249 | document for LLMs and for Tesseract "with an empty interpretation" | fixed | F-ocr B2 PARTLY (interpretation None); now api/ocr.rst:80-84, ocr.rst:137-138 |
| docs/api/ocr.rst:251-259 | confidence = self-confidence or Tesseract average; metadata model, tokens, batch index | fixed | F-ocr B2 PARTLY (free-form 1.0; Tesseract None, no model); now ocr.rst:140-141, api/ocr.rst:80-84 |
| docs/api/ocr.rst:293-311 | OCRProvider members; GEMINI alias; OCR("gemini") behaves like OCR("vertex_ai") | true | ocr/vertex_ai.py:820-821 (facade); kept api/ocr.rst:90-98 |
| docs/api/ocr.rst:320-324 | OCRFactory registry, case-insensitive lookup, unknown name -> ValueError | true | ocr/base.py:653-682; kept api/ocr.rst:97-99 |
| docs/api/ocr.rst:330 | list_providers() gives ['openai', 'vertex_ai', 'gemini', 'tesseract'] | fixed | F-ocr B2 PARTLY (other order); now api/ocr.rst:105 sorted (ex: api/ocr.rst:102 pass) |
| docs/api/ocr.rst:331-344 | create(...).batch_process_images(...); BaseOCR needs only batch_process_images | true | ocr/base.py:319-333; kept api/ocr.rst:97-114 |
| docs/api/ocr.rst:357-363 | OpenAIOCR: "GPT-4V-class"; model precedence constructor -> OCRConfig -> default | fixed | precedence now api/loader.rst:46-49; the OpenAIOCR docstring rendered by autodoc still says "GPT-4V": code issue #32 |
| docs/api/ocr.rst:369-371 | OCR("openai", model="gpt-5.4-mini", detail="full") example | true | F-ocr B2; kept ocr.rst:83-97 |
| docs/api/ocr.rst:380-385 | VertexAIOCR: ADC; set GOOGLE_CLOUD_PROJECT or pass project=; default model, location | fixed | F-ocr B2 PARTLY (no project= via OCR()); now ocr.rst:33-35,103-106 |
| docs/api/ocr.rst:391-393 | OCR("gemini", project="my-gcp-project") | fixed | probe: TypeError; now ocr.rst:103-106 |
| docs/api/ocr.rst:402-417 | TesseractOCR raw only, enhance_image/detect_layout, language mapping; example | true | F-ocr B2 (fake engine); kept ocr.rst:137-138,168-187 |

## docs/api/schema.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/api/schema.rst:4-7 | Every image becomes a structured OCRPage on OCRResult.document | fixed | F-ocr B3 PARTLY; now api/schema.rst:4-7 (None for free-form, failed Tesseract) |
| docs/api/schema.rst:9-15 | raw = what is on the page (text, tables, fields, indexes); interpretation = reading | true | ocr/schema.py:1669-1671; kept api/schema.rst:10-15 |
| docs/api/schema.rst:16-18 | raw is always present and verbatim, the auditable BM42 record | fixed | F-ocr B3 PARTLY (recovery answer in raw.text); now api/schema.rst:17-20 |
| docs/api/schema.rst:18-24 | interpretation None for detail="raw", Tesseract, or a failed parse | fixed | F-ocr B3 PARTLY (OpenAI only asked); now api/schema.rst:21-27 |
| docs/api/schema.rst:26-42 | BaseModels, every field defaulted, anyOf [T, null]; import path; to_markdown -> text | true | F-ocr B3 (probe: 13 models, 0 required); kept api/schema.rst:29-45 |
| docs/api/schema.rst:48-53 | The interpretation never invents or moves a printed value | verified-by-reading-only | F-ocr B3 UNVERIFIABLE (field descriptions only); kept api/schema.rst:51-56 |
| docs/api/schema.rst:55-57 | router_invariants mechanically enforces this, as a CI/eval assertion | fixed | F-ocr B3 PARTLY (only withholding runs live); now api/schema.rst:58-61 |
| docs/api/schema.rst:57-62 | Checks: figure strings, Section.heading, primary_date, entities/relations, illustrative | true | ocr/schema.py:1986-2054; kept api/schema.rst:62-67 |
| docs/api/schema.rst:64-67 | With detail="raw" or a Tesseract backend the interpretation will be None | fixed | F-ocr B3 PARTLY (OpenAI only asks); now api/schema.rst:71-73 |
| docs/api/schema.rst:78-82 | OCRPage(raw=RawExtraction, interpretation=None) signature | true | ocr/schema.py:1669-1671; ex: api/schema.rst:85 pass |
| docs/api/schema.rst:84-87 | to_markdown prefers structured tables/fields over the flat text dump | fixed | F-ocr B3 FALSE (raw.text first, fields never); now api/schema.rst:90-96 |
| docs/api/schema.rst:87-92 | page_markdown replaces raw.text when it covers it; hidden verbatim tail | true | ocr/schema.py:1697-1707,1900 (>= 0.85); kept api/schema.rst:96-99 |
| docs/api/schema.rst:102-155 | RawExtraction indexes; Table (sanitised html, illustrative, row_count); KeyValue; Metric | true | ocr/schema.py:1197-1317; kept api/schema.rst:109-162 |
| docs/api/schema.rst:165-170 | Nested models: depth 4, no recursion, no unions, all defaulted | true | F-ocr B3 (probe: depth 4); kept api/schema.rst:172-177 |
| docs/api/schema.rst:170-172 | Verbatim mirroring is enforced by router_invariants | fixed | F-ocr B3 PARTLY (a checker); now api/schema.rst:177-179 ("checks") |
| docs/api/schema.rst:174-205 | Figure kinds, DataPoint, DiagramNode/Edge by label, flat Section, Entity, Relation | true | ocr/schema.py:1326-1661; kept api/schema.rst:181-211 |
| docs/api/schema.rst:183-210 | Model-side guarantees: verbatim copies, never pixel-estimated, full page_markdown | verified-by-reading-only | field descriptions only (model behaviour); kept api/schema.rst:190-217 |
| docs/api/schema.rst:248-257 | 16 document_type values and the anchor fields | true | ocr/schema.py:1537-1661; kept api/schema.rst:255-264 |
| docs/api/schema.rst:264-328 | Receipt example validates and its asserts pass | true | ex: docs/api/schema.rst:278 pass |

## docs/api/cache.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/api/cache.rst:4-5 | doc2mark provides a request-scoped OCR result cache | fixed | F-cct B2 FALSE (per instance, shared, Redis persists); now caching.rst:4-9 |
| docs/api/cache.rst:6 | Cached values use the cache v4 schema | fixed | F-cct B2 FALSE (key ocr-cache-v6, value ocr-cache-value-v2); now caching.rst:30-31 |
| docs/api/cache.rst:7-10 | Values store the full OCRPage next to the text, so hits are complete | removed | ocr/cache.py:290-297 (TRUE); not stated in the new docs |
| docs/api/cache.rst:12-16, 290-319 | Wired via ocr_cache on load() and the loader; factory or classes; wiring examples | true | F-cct B2; kept api/cache.rst:4-6, caching.rst:14-58 |
| docs/api/cache.rst:25-49 | OCRCache: get, set(key, result, ttl_seconds=None), cleanup() -> int, clear(), stats() | true | ocr/cache.py:405-427; autodoc api/cache.rst:10-12 |
| docs/api/cache.rst:59-61 | MemoryOCRCache is what create_ocr_cache returns for provider="memory" | true | ocr/cache.py:839-920 (factory default "none"); kept caching.rst:54-56 |
| docs/api/cache.rst:76-78, 130-131 | ttl_seconds: a hit extends the entry's deadline, up to max_refreshes times | fixed | F-cct B1/B2 PARTLY (resets to now + ttl); now caching.rst:63-65 |
| docs/api/cache.rst:80-94 | max_age_seconds, max_entries (LRU), max_refreshes, time_func defaults | true | ocr/cache.py:443-465; autodoc signature api/cache.rst:14, caching.rst:36,63-67 |
| docs/api/cache.rst:104-110 | Redis: lazy import, ping(); create_ocr_cache falls back transparently | true | F-cct B2 TRUE (fallback logs a WARNING); kept caching.rst:37-58 (says so) |
| docs/api/cache.rst:126-149 | redis_url required ("" -> ValueError); ttl, max_age, refreshes, prefix; cleanup no-op | true | ocr/cache.py:599-695; kept caching.rst:37-39, autodoc api/cache.rst:18 |
| docs/api/cache.rst:159-167 | NoOpOCRCache never stores; get() always returns None | true | ocr/cache.py:561-589; kept caching.rst:40 |
| docs/api/cache.rst:177-185 | CachedOCR: intercepts both calls, dedups a batch; the loader creates it | true | ocr/cache.py:991-1098, core/loader.py:362-369; kept caching.rst:25-26 |
| docs/api/cache.rst:189-197 | CachedOCR(wrapped=OCRFactory.create(...), cache=...).process_image(bytes) example | removed | F-cct B2 TRUE (API); example dropped (autodoc api/cache.rst:26-28) |
| docs/api/cache.rst:201-211 | wrapped, cache (None -> NoOp), cache_version "ocr-cache-v6" invalidates when changed | true | ocr/cache.py:926-934; autodoc signature api/cache.rst:26 |
| docs/api/cache.rst:224-254, 264-282 | Factory names and aliases; tuning params forwarded; returns OCRCache or None | true | ocr/cache.py:839-920 (probe); kept caching.rst:42-58, autodoc :8 |
| docs/api/cache.rst:256-262 | redis_url; fallback "memory" degrades silently, "none" disables, "raise" re-raises | fixed | F-cct B2 PARTLY (both log a WARNING); now caching.rst:56-58 |

## docs/api/chunking.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/api/chunking.rst:4-9, 150-154, 195, 218 | Chunking needs json_content from load(..., output_format="json") | fixed | F-cct B3 PARTLY (PDF/Office always have it); now chunking.rst:21-24 |
| docs/api/chunking.rst:11-18, 65-77 | Char or token size units; tiktoken with char fallback + warning; encoding_name | true | core/chunker.py:34-55; kept chunking.rst:64-71 |
| docs/api/chunking.rst:30-40 | ChunkingConfig defaults (1500, 200, 2, True, False, "chars", "cl100k_base") | true | core/chunker.py:22-31; kept api/chunking.rst:12-15 |
| docs/api/chunking.rst:45-47 | max_chunk_size is the maximum size of a chunk | fixed | F-cct B3 PARTLY (soft limit); now chunking.rst:33-40 |
| docs/api/chunking.rst:49-51 | overlap = trailing units of the previous chunk, prepended | fixed | F-cct B3 PARTLY (word-trimmed, crosses sections); now chunking.rst:38-40 |
| docs/api/chunking.rst:53-56, 170-172 | split_on_heading_level is a heading depth (title 1, section 2) | fixed | F-cct B3 PARTLY (every section is level 2); now chunking.rst:29-32 |
| docs/api/chunking.rst:58-60 | keep_tables_whole keeps a table in one chunk instead of splitting it | fixed | F-cct B3 FALSE (tables never split); now chunking.rst:35-37 |
| docs/api/chunking.rst:62-63 | include_page_markers is reserved for future use | true | core/chunker.py:29 (never read); kept chunking.rst:49 |
| docs/api/chunking.rst:89-91 | Chunk is an immutable result object | removed | F-cct B3 FALSE (plain mutable dataclass); claim dropped |
| docs/api/chunking.rst:96-101, 107-112 | content; section_title None before a heading; page_start/page_end from item pages | true | core/chunker.py:10-19 (slides, sheet index); kept chunking.rst:51-52 |
| docs/api/chunking.rst:103-105 | section_hierarchy = ordered list of ancestor headings | fixed | F-cct B3 PARTLY (title + current section); now chunking.rst:53-55 |
| docs/api/chunking.rst:114-116 | content_types = set of item types present in the chunk | fixed | F-cct B3 PARTLY (may name header/footer); now chunking.rst:52-53 |
| docs/api/chunking.rst:118-120, 163-165 | chunk_index runs from 0 | true | core/chunker.py:111-113; kept api/chunking.rst:22 |
| docs/api/chunking.rst:132-158 | chunk_content(json_content, config=None); defaults when None | true | core/chunker.py:58-72; autodoc api/chunking.rst:7 |
| docs/api/chunking.rst:173-179 | Items packed until the next would overflow; overlap at a word boundary; token slicing | true | core/chunker.py:213-360; kept chunking.rst:33-40 |
| docs/api/chunking.rst:180-182 | Footnotes go to the chunks that reference them, the rest to the last chunk | fixed | F-cct B3 PARTLY (first reference; PDF all last); now chunking.rst:41-44 |
| docs/api/chunking.rst:183 | Empty json_content returns an empty list | removed | core/chunker.py:71-72 (TRUE); not in the new docs |
| docs/api/chunking.rst:184-186 | get_chunks() just delegates to chunk_content | fixed | F-cct B3 PARTLY (one chunk without json_content); now chunking.rst:21-24 |
| docs/api/chunking.rst:188-228 | Character-mode and token-mode examples | true | F-cct B3 (API); ex: docs/chunking.rst:13, :62 pass |

## docs/api/types.rst

| where | claim | status | evidence |
|---|---|---|---|
| docs/api/types.rst:4-7 | Every load() call returns a ProcessedDocument | true | core/loader.py:578-588; kept api/types.rst:4-14 |
| docs/api/types.rst:16-59 | DocumentFormat values are lowercase extensions; members by category | true | core/base.py:11-54; autodoc api/types.rst:66-68 |
| docs/api/types.rst:67-86 | OutputFormat MARKDOWN (default) / JSON / TEXT with their values | true | core/base.py:57-61; autodoc api/types.rst:70-72 |
| docs/api/types.rst:78-79 | TEXT = plain text with formatting stripped | fixed | F-fmt 9.4 PARTLY (5 patterns); now output.rst:34-37,51 |
| docs/api/types.rst:94-117 | TableStyle (doc2mark.core.table) for merged-cell tables; 3 members; default() | true | core/table.py:68-76 (PPTX always styled); kept tables.rst:8-22 |
| docs/api/types.rst:131-138 | ProcessedDocument dataclass fields; content Markdown by default; metadata | true | core/base.py:111-119; kept output.rst:6-19 |
| docs/api/types.rst:140-142 | images is None when extraction is not requested | fixed | F-cct B7 PARTLY ([] for Office); now output.rst:23-29 |
| docs/api/types.rst:144-148 | tables is None when no tables; sections filled when the format supports them | fixed | F-cct B7 FALSE (always None); now output.rst:30-32, api/types.rst:13-14 |
| docs/api/types.rst:150-153 | json_content None when the output format lacks it; UnifiedMarkdownLoader compat | fixed | F-cct B7 PARTLY (input format decides); now output.rst:20-22,48-50 |
| docs/api/types.rst:157-158, 163-165 | markdown aliases content; to_dict() JSON-safe (enums, bytes -> base64) | true | core/base.py:98-151 (probe); kept output.rst:34-39 |
| docs/api/types.rst:160-161 | text = content with common Markdown formatting stripped | fixed | F-cct B7 PARTLY (C# is -> Cis); now output.rst:34-37 |
| docs/api/types.rst:167-170 | get_chunks(config=None) returns section-aware chunks | fixed | F-cct B7 PARTLY (one chunk without json_content); now chunking.rst:21-24 |
| docs/api/types.rst:172-192 | Result example: content, metadata, text, to_dict(), get_chunks() | true | F-cct B7 (API); ex: docs/quickstart.rst:9 pass |
| docs/api/types.rst:202-215, 293-295 | filename, format, size_bytes always set; others None unless given; extra dict {} | true | F-fmt 9.4; kept api/types.rst:21-23 |
| docs/api/types.rst:219-220 | page_count for PDF, DOCX, PPTX | fixed | F-fmt 9.4 PARTLY (also XLSX, images, legacy); now api/types.rst:31-33 |
| docs/api/types.rst:222-223 | word_count is approximate | true | F-fmt 9.4; kept api/types.rst:34-35 |
| docs/api/types.rst:225-226 | language holds the detected language code | fixed | F-fmt 9.4 FALSE (never set); now api/types.rst:59-61 |
| docs/api/types.rst:228-235 | creation_date, modification_date, author come from file metadata | fixed | F-fmt 9.4 PARTLY (fallbacks only); now api/types.rst:59-61 |
| docs/api/types.rst:237-238 | title comes from file metadata | fixed | F-fmt 9.4 PARTLY (HTML <title>); now api/types.rst:36-37 |
| docs/api/types.rst:242-243 | sheet_names is set for XLSX | fixed | F-fmt 9.4 FALSE (None, probe); now api/types.rst:59-61, output.rst:15-18 |
| docs/api/types.rst:245-246 | slide_count is set for PPTX | fixed | probe: unreliable (counts "Slide "); now api/types.rst:38-40 |
| docs/api/types.rst:248-249 | line_count for text files | fixed | F-fmt 9.4 PARTLY (TXT and MD only); now api/types.rst:41-42 |
| docs/api/types.rst:251-255, 269-282, 290-291 | header/link/record/row/column/element counts, root_tag, item_count per format | true | F-fmt 9.4 TRUE; kept api/types.rst:43-58 |
| docs/api/types.rst:257-258 | image_count = images found in the document | fixed | F-fmt 9.4 PARTLY (not PDF); now api/types.rst:45-47 |
| docs/api/types.rst:260-261 | total_cells is set for XLSX | fixed | F-fmt 9.4 FALSE (fallback only); now api/types.rst:59-61 |
| docs/api/types.rst:263-264 | encoding = the detected character encoding | fixed | F-fmt 9.4 FALSE (argument echoed); now api/types.rst:48-50 |
| docs/api/types.rst:266-267 | delimiter for CSV and TSV | fixed | F-fmt 9.4 PARTLY (TSV never converts); now api/types.rst:51-52 |
| docs/api/types.rst:284-285 | frontmatter = parsed YAML front matter | fixed | F-fmt 9.4 PARTLY (needs PyYAML, may be a string); now api/types.rst:43-44 |
| docs/api/types.rst:287-288 | data_type = top-level JSON type descriptor | fixed | F-fmt 9.4 PARTLY (Python type name); now api/types.rst:55-56 |
| docs/api/types.rst:297-307 | XLSX example: meta.sheet_names == ["Sheet1", "Summary"] | fixed | probe: None; example dropped, api/types.rst:59-61 |
| docs/api/types.rst:317-319 | All exceptions inherit ProcessingError; one except catches every failure | fixed | F-fmt 9.4 PARTLY (FileNotFoundError, ValueError); now api/types.rst:81-87 |
| docs/api/types.rst:321-362 | Hierarchy; ProcessingError catches load("data.bin"); 7z -> UnsupportedFormatError | true | core/base.py:186-203; ex: docs/api/types.rst:90 pass |
| docs/api/types.rst:371-382 | load() raises OCRError on OCR failures (bad key, timeout, bad image) | fixed | F-fmt 9.4 FALSE (placeholders + ocr_issues); now output.rst:172-180 |
| docs/api/types.rst:391-401 | load() raises ConversionError when .doc -> .docx conversion fails | fixed | probe: ProcessingError chain ending in ConversionError; now output.rst:175-177, api/types.rst:84-85 |
