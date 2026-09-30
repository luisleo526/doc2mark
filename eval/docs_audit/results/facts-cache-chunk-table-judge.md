# Fact sheet and claim verdicts: caching, document cache, chunking, tables, judge

Code audited: `/Users/haoliangwen/code/doc2mark-d2m-docs-audit` at HEAD `becb74b` (== origin/main). `doc2mark/` is unchanged vs HEAD.
**Doc line numbers are those of HEAD `becb74b`** (`git show HEAD:<file>`). During this audit another agent was editing
`README.md`, `docs/caching.rst`, `docs/tables.rst`, `docs/api/cache.rst` and `docs/api/chunking.rst` in the worktree. I did not touch them.

Probes: from `/tmp/d2m-docs-audit-probe` with `PYTHONPATH=<worktree>`. Every probe printed `doc2mark.__file__` = the worktree.
Every probe ran with `OPENAI_API_KEY`/`TYPESAFE_API_KEY` unset and `HTTP(S)_PROXY=http://127.0.0.1:9`, so there was no network access and no API calls.
Two interpreters were used:
- `python3` (Py 3.11; PyMuPDF 1.26.4, typesafe_sdk 0.7.0, fakeredis) for the cache, SDK, table and escape probes;
- `~/miniconda3/bin/python3` (Py 3.13; PyMuPDF 1.27.2 = project floor) for the PDF, chunk, loader, doc-cache and judge-loader probes.

Scripts: `/tmp/d2m-docs-audit-probe/cct_*.py`. Fixtures: `/tmp/d2m-docs-audit-probe/cct_fixtures/`.
The TypeSafe service was never called. Judge behaviour was checked with an injected stub `client=`.

Verdict key: **TRUE**, **FALSE**, **PARTLY** (true but missing or inaccurate in a way that matters), **UNVERIFIABLE** (external, or needs a live API).

---

## Part A. Fact sheet (code truth)

### A1. OCR result cache (`doc2mark/ocr/cache.py`)

**Versions and constants**
- Key schema `CACHE_SCHEMA_VERSION = "ocr-cache-v6"` (cache.py:25).
- Value schema `OCR_CACHE_VALUE_SCHEMA_VERSION = "ocr-cache-value-v2"` (cache.py:26).
- Default Redis prefix `doc2mark:ocr:ocr-cache-v6` (cache.py:27).
- `REFUSAL_TTL_SECONDS = 600.0` (cache.py:44).
- v6 exists so that v5 refusals, which had no TTL of their own, are never read (cache.py:23-24).

**Cache key.** `build_ocr_cache_key` (cache.py:243-268) is the sha256 of canonical JSON containing:
- the schema and the sha256 of the image bytes;
- the provider class qualname and the sha256 of its API key;
- the config signature, the provider's `model`, `temperature`, `max_tokens` and `prompt_template`, and the sha256 of `default_prompt`;
- `model_kwargs`, `base_url`, `project`, `location`, and the call kwargs.

Other key rules:
- **Config signature for LLM providers** is only `language, max_concurrency, structured, detail, response_model` (name) and `non_content_judge` identity (cache.py:168-182).
- **Config signature for Tesseract** is the full `OCRConfig` minus `non_content_judge` (cache.py:50, 57, 185-194).
- Dict keys named `api_key/key/secret/password/access_token/refresh_token` are dropped (cache.py:52, 124).
- A value whose repr holds a memory address raises `TypeError` (strict mode; cache.py:133-139).
- With neighbour-PDF context, `context_pdf_sha256` is added per image (cache.py:1050-1059).
- Judge identity is `{"name": module.qualname, "version": judge.version}`. It is `None` when `judge.available is False`, so an unavailable judge is keyed like no judge (cache.py:152-165).

**Key gaps (probe `cct_misc`/key probe):**
- `OCRConfig.task` is **not** in the key, although it selects the structured prompt (`TASK_PROMPTS[config.task]`, ocr/openai.py:817-833).
  Two OpenAIOCR instances that differ only in `config.task` (auto vs receipt) produce the **same key**.
- A per-call `task=` kwarg *is* keyed.
- `max_concurrency` *is* keyed, so changing it causes misses.

**Interface `OCRCache`** (cache.py:405-427): `get(key)`, `set(key, result, ttl_seconds=None)`, `cleanup()->int`, `clear()`, `stats()->dict`. An explicit `ttl_seconds` is never extended by hits (cache.py:413-415, 393-394).

**MemoryOCRCache** (cache.py:440-558)
- Defaults: `ttl_seconds=3600`, `max_age_seconds=43200`, `max_entries=1024`, `max_refreshes=10`, `time_func=None`, `refusal_ttl_seconds=600` (cache.py:443-451).
- Validation raises `ValueError`: ttl>0, refusal>0, max_age>0 or None, max_refreshes>=0 or None, max_entries>0 (cache.py:359-372, 453-454).
- `get` on an expired entry: deletes it and counts `expired`, `misses`, `deletes`.
- `get` on a hit:
  - increments `hits`;
  - if `refresh_count < max_refreshes`, sets expiry to **now + ttl** (sliding, not added to the old expiry), capped at `created_at + max_age`, and counts `refreshes`; otherwise counts `refresh_skipped`;
  - moves the entry to the MRU end and returns a deep copy (cache.py:466-490, 387-398).
- `set` runs `cleanup()` first, sets expiry = now + ttl (capped at now + max_age), then evicts from the LRU end while over `max_entries` (cache.py:492-512, 555-558). Thread-safe (RLock).
- Probe `cct_cache` (fake clock; ttl 100, max_age 250):
  - hits at +90 s and +180 s were served; +270 s → miss (max_age);
  - with `max_entries=2`, after `get(b)` + `set(d)`, entry `c` was evicted;
  - an entry stored with its own TTL of 600 s was gone at 650 s despite a hit at 500 s.

**NoOpOCRCache**: `get` always returns None; `set`, `cleanup` and `clear` are no-ops; stats have `backend: "noop"` and `entries: 0` (cache.py:561-589).

**RedisOCRCache** (cache.py:592-836)
- Construction:
  - `redis_url` is required; `""` → `ValueError` (609-610);
  - `redis` is imported lazily; if missing, `ImportError` "requires the 'redis' optional dependency";
  - `redis.from_url(...)` then `.ping()`; a failure raises (e.g. `ConnectionError`) (613-626);
  - a trailing `:` on `key_prefix` is stripped (623).
- Storage:
  - key `"{prefix}:{sha256}"`, e.g. probe: `b'doc2mark:ocr:ocr-cache-v6:k1'`;
  - value is JSON `{schema, result{text,confidence,language,metadata,document}, created_at, expires_at, refresh_count[, ttl_seconds]}` (cache.py:280-305);
  - `SET ... EX=ceil(expires_at-now)` (min 1). Probe: TTL 100.
- Reads:
  - a read error is counted in `errors` and treated as a miss;
  - a malformed or wrong-schema value is deleted and treated as a miss;
  - a refresh uses WATCH/MULTI and never raises out of `get`/`set` (631-692, 738-812).
- Maintenance: `cleanup()` → 0 (694-695). `clear()` does SCAN `prefix:*` + DEL (697-713).
- `stats()` adds `key_prefix`; `entries` and `max_entries` are `None` (715-730).

**stats() keys (all backends)**
- Counters: `hits, misses, sets, refreshes, refresh_skipped, expired, evictions, deletes, errors` (cache.py:59-69).
- Settings: `backend, entries, max_entries, ttl_seconds, refusal_ttl_seconds, max_age_seconds, max_refreshes` (+ `key_prefix` for Redis) (533-547, 576-589, 715-730).

**create_ocr_cache** (cache.py:839-920)
- Signature: `provider="none"`, keyword-only `redis_url=None, fallback="memory", ttl_seconds=3600, max_age_seconds=43200, max_refreshes=10, max_entries=1024, key_prefix=DEFAULT, refusal_ttl_seconds=600`.
- Names are case- and whitespace-insensitive:
  - `none/off/false/disabled/""` and `None` → `None`;
  - `noop/no-op` → NoOp;
  - `memory/in-memory/in_memory` → Memory;
  - `redis` → Redis;
  - anything else → `ValueError`.
- Any exception while building Redis, including a missing `redis_url`, triggers the fallback:
  - `memory` (and aliases) → WARNING "Redis OCR cache unavailable; falling back to MemoryOCRCache: ...";
  - `none` (and aliases) → WARNING + `None`;
  - `raise` → re-raise;
  - any other fallback value → `ValueError`.
- Probe confirmed all of these; `create_ocr_cache("redis")` without a URL gave a MemoryOCRCache with the warning "redis_url is required".

**CachedOCR** (cache.py:923-1105)
- `CachedOCR(wrapped, cache, cache_version="ocr-cache-v6")`; `cache=None` becomes NoOp (933).
- `process_image` sends a batch of one (991-993). `BaseOCR` itself declares only `batch_process_images` (ocr/base.py:319-333). Attributes proxy to `wrapped`.
- Per batch:
  - a cached entry that is not an answer counts as a miss (1069);
  - misses are deduplicated **by cache key**, so the provider sees each unique key once;
  - dedup copies and cache hits are flagged `metadata["doc2mark_from_cache"]=True` on the returned copy only (36, 1028-1032, 1072).
- Store rules (1016-1027, 197-222):
  - **not stored**: `failed`, `router_fallback=="unresolved"`, `non_content_unjudged`, or a judge whose availability changed during the batch (1087-1091). Each is logged at INFO "OCR result not cached (...)";
  - **provider refusal** (`metadata["non_content"]=="provider_refusal"`): stored with `ttl = min(cache.refusal_ttl_seconds or 600, cache.ttl_seconds)`, not extended by hits;
  - **everything else**, including empty answers and pattern or judge refusals: stored with the cache TTL.
- If the provider returns the wrong number of results, CachedOCR stores what it got, then raises `RuntimeError` (1092-1095).
- Probe (stub provider, ttl 300):
  - batch `[img1,img1,img2,fail,refuse,unjudged]` → provider saw `[img1,img2,fail,refuse,unjudged]`, and the second img1 was flagged from_cache;
  - second batch → provider saw only `[fail, unjudged]`;
  - the refusal entry had `ttl_seconds == 300`, i.e. min(600, 300).

**Loader wiring**
- `ocr_cache=` wraps the provider in `CachedOCR`, or swaps the cache of an existing `CachedOCR`.
- With OCR off, the cache is kept for a later `set_ocr_provider` (loader.py:354-378).
- Every processor then gets a `UsageAggregatingOCR` around that instance (loader.py:429-434).
- The convenience functions `load`, `document_to_markdown`, `batch_convert_to_markdown`, `batch_process_documents` and `batch_process_files` all take `ocr_cache` (__init__.py:103,157,217,271,327). `batch_process_files` is not in `__all__`.

### A2. Loader document cache `cache_dir` (`doc2mark/core/loader.py`)
- **Enabling it**: only `UnifiedDocumentLoader(cache_dir=...)`; the directory is created at init (loader.py:39, 175-178).
  The convenience `load()` forwards `**kwargs` to `loader.load()`, which rejects them.
  Probe: `load(f, cache_dir=...)` → `TypeError: ...load() got an unexpected keyword argument 'cache_dir'`. The same applies to `table_style=` and `judge=` (__init__.py:136-147, loader.py:578-588).
- **File path**: `<cache_dir>/<sha256(json{path resolved, mtime_ns, size, output_format, options})>.json` (loader.py:1397-1407).
- **`options`** (loader.py:637-658):
  - `output_format`, `extract_images` (after `ocr_images` implies it, 632-634), `ocr_images`, `encoding`, `delimiter`, `table_style`;
  - `ocr_provider` = **class name** of the unwrapped provider, or None;
  - `legibility_judge` identity and `routing_version` (= 3, core/strategy.py:127);
  - plus `boilerplate_judge` / `non_content_judge` identities only when set and available.
- **Judge identity**: the `cache_key` attribute if present, else `module.qualname` (+ args of a `functools.partial`). It is `None` when `available is False` (loader.py:500-523).
- **Not in the key**: OCR model, prompt template/default prompt, task/structured/detail/language, API key, base_url, doc2mark version (other than routing_version).
  Probe `cct_doccache`: stub provider class with model "A" loaded first; the same class with model "B" then got "Using cached result" and **content still said "model A"**.
- **Payload**: `{"schema": "doc2mark-document-cache-v1", "source", "output_format", "document": {content, metadata, images, tables, sections, json_content}}`.
  Bytes are stored as `{"__bytes__": b64}`. Writes are atomic (`.tmp` + replace); a read error logs a WARNING and counts as a miss (loader.py:1312-1341, 1360-1395, 1410-1456; probe printed these keys).
- **Lifetime**: never expires, no eviction, no size cap. A file is invalidated only by a change of path, mtime, size or options. Probe: touching the file → miss.
- **Never written** (INFO "Not caching <file>: <reason>; the next run converts it again"; loader.py:733-742, 1290-1310):
  - OCR failed on any image (`extra["ocr_images"]["failed"]` / `["ocr_issues"]["failed"]`);
  - `unread_pages`;
  - `ocr_issues["provider_refused"] > 0`;
  - the judge failed a question or became unavailable during the document (loader.py:549, 737-738).
  Probes: failed stub OCR → 0 files; failing TypeSafe stub → 0 files.
- **On replay**:
  - `extra["token_usage"]` is renamed `extra["token_usage_cached"]` (loader.py:1344-1358; probe);
  - `extra["judge"]` (incl. `cost_usd`) is replayed **unchanged** (probe `cct_judge_loader`: a later run with a failing judge but the same key was served the first run's `judge` stats).

### A3. Document-level token usage and OCR issues (`doc2mark/ocr/usage.py`, loader.py:691-718)
- `extra["token_usage"] = {"input_tokens","output_tokens","total_tokens"}` is summed per `load()` in a thread-local sink over **fresh** results only (results flagged `doc2mark_from_cache` are skipped) (usage.py:247-295).
  - Input aliases accepted: `prompt_tokens`, camelCase.
  - `total` = the reported total, else input+output (usage.py:78-92).
  - Stamped only when a count is >0 (none for Tesseract or no OCR), before output-format conversion and caching (loader.py:703-710; usage.py:236-245).
- `extra["ocr_issues"]` contains:
  - counters `refused, provider_refused, failed, withheld, suspected`;
  - `errors` (≤5 distinct);
  - `locations` (≤100 items `{"issue","image"}` plus `page`/`slide`/`sheet` when the pipeline labels them); omitted when empty.
  It is present only when a counter or error is non-zero (usage.py:72-75, 174-222). An `OCREngineError` is re-raised, so `load()` raises `ProcessingError` (usage.py:200-201, loader.py:746-748).

### A4. Chunking (`doc2mark/core/chunker.py`, `ProcessedDocument.get_chunks` in core/base.py)

**Dataclasses**
- `Chunk(content, section_title=None, section_hierarchy=[], page_start=None, page_end=None, content_types=[], chunk_index=0)` is a plain, mutable `@dataclass` (chunker.py:10-19). Probe: `frozen=False`, and assigning to a field works.
- `ChunkingConfig(max_chunk_size=1500, overlap=200, split_on_heading_level=2, keep_tables_whole=True, include_page_markers=False, size_unit="chars", encoding_name="cl100k_base")` (chunker.py:22-31). Values are not validated; probe: `size_unit="words"` is silently treated as chars (chunker.py:42-55).

**Algorithm (`chunk_content`, 58-115)**
1. Empty input → `[]` (71-72).
2. Footnotes are pulled out (79-92):
   - only items whose content matches `^\[\^(\w+)\]:` are keyed by id;
   - any other `text:footnote` is keyed by its **first 20 characters**, so two footnotes starting alike collide and one is **silently dropped**. Probe: "1 PDF-style footnote text without the label syntax" was lost.
3. Sections are formed at headings (122-128, 170-210):
   - `text:title` = level 1 and `text:section` = level 2, whatever the item's own `level` (2–6); `split_on_heading_level` ≥2 all behave the same; 1 splits only at titles; 0 never splits;
   - hierarchy: a title resets it to `[title]`; a section becomes `hierarchy[:1] + [section]`. So H3 is never nested under H2 (probe: `['Doc Title','H3 Subsection']`).
   - **Bug**: with no `text:title`, the first section is kept as the "ancestor" of every later one (probe: `S2` → `['S1','S2']`).
4. Sections become chunks (213-300), split only **at item boundaries**; an item is never split.
   - Probe: a 5000-char paragraph with `max=1500` → one 4999-char chunk.
   - A table that would overflow:
     - `keep_tables_whole=True` → appended to the current chunk (the chunk exceeds the max);
     - `False` → the current chunk is flushed and the table starts its own (still whole, still over the max).
     - Probe (`max=300`, 706-char table): True → `[para+table 827]`, `[text]`; False → `[para 119]`, `[table 706]`, `[text]`. **Tables are never split in either mode.**
   - A heading can end up alone in a chunk (token probe: `'## S1'` = 3 tokens).
5. Overlap is applied after sizing, across **all** consecutive chunks including section boundaries (103-105, 323-360):
   - it takes the last `overlap` units of the previous chunk's *final* content, then drops everything up to the first space;
   - if the previous chunk is ≤ `overlap`, the whole previous chunk (with its own overlap) is prepended.
   - Probe: a section S2 chunk started with 199 chars of section S1; four tiny sections → the 4th chunk contained T0..T3.
   - Chunks can therefore exceed `max_chunk_size` by the overlap plus 2.
6. Footnotes are attached after the overlap (107-109, 303-320): each `[^id]` definition goes to the **first** chunk that references it; all others go to the last chunk. `content_types` never contains `text:footnote`.
7. `chunk_index` is 0..n-1 (111-113).

**Rendering and metadata (131-159)**
- Rendered text: title → `# `, section → `## ` (the `level` is ignored), caption → `*...*`, image → `![Image](data:image/png;base64,...)`, `text:image_description` → wrapper tags stripped, `text:header`/`text:footer` → skipped.
- `content_types` is `list(set(...))`, so order is arbitrary. On the single-chunk path it includes types that render nothing (probe: `['text:header','text:normal']` with content `'Body'`) (232-233).
- `page_start`/`page_end` are the min/max of the items' `page`: PDF page, PPTX slide, XLSX **sheet index**; none for text formats.

**Size units**
- `"tokens"` uses `tiktoken.get_encoding(encoding_name)`. If tiktoken is missing: one WARNING and `len` is used; overlap falls back to char slicing (34-55, 323-341). Probe: lengths were chars.
- An unknown `encoding_name` raises `ValueError` ("Unknown encoding"); it does not fall back (probe).
- `include_page_markers` is never read anywhere (grep: only chunker.py:29, tests, docs).

**`get_chunks(config=None)`** (base.py:134-146): if `json_content` is falsy, returns a **single** `Chunk(content=self.content)` with no size limit. Otherwise `chunk_content(json_content, config)`.

**Where `json_content` comes from** (probe `cct_loader`)
- PDF, DOCX, XLSX and PPTX fill it in **both** Markdown and JSON output modes.
- TXT, MD, CSV and JSON have `json_content=None` in Markdown mode. In JSON mode the loader sets one `text:normal` item holding the whole content (loader.py:727-728).
- For text formats, then, chunking always yields **one** chunk (probe: `sample_text.txt` → 1 chunk of 1489 chars with max 500).
- XLSX: one `table` item per sheet, so a sheet is one chunk (probe: 1408 chars with max 500).

### A5. Tables (`doc2mark/core/table.py` + office pipeline)

**`TableStyle`**: `MINIMAL_HTML="minimal_html"`, `MARKDOWN_GRID="markdown_grid"`, `STYLED_HTML="styled_html"`; `default()` → MINIMAL_HTML (table.py:68-76). The loader default is `"minimal_html"` (loader.py:181).

**Accepted `table_style` values**
- Exact lowercase string or enum; no validation at loader construction.
- Conversion via `TableStyle(str)` happens in the pipelines (office_advanced_pipeline.py:627-633, pymupdf_advanced_pipeline.py:683-689).
- An invalid value such as `"MARKDOWN_GRID"` or `"bogus"`:
  - PDF → `ProcessingError: ... 'MARKDOWN_GRID' is not a valid TableStyle`;
  - Office → WARNING "Advanced pipeline processing failed ..." and a **silent fallback to basic extraction**: merged text repeated per cell, `json_content=None` (formats/office.py:532-546; probes).
- The CLI restricts `--table-style` to the three values (cli.py:302-307).

**`TableData` validators** (table.py:138-280)
- Pad ragged rows; clamp spans to the table bounds.
- A span may absorb cells that are empty or repeat its own text. Otherwise it is shrunk: widest free run in its first row, then rows free across that width. One WARNING is logged per process, DEBUG after that (264-269).
- Covered positions become continuation cells; orphan continuations become empty.
- `is_complex` = True if any span remains, recomputed only when a span was shrunk. A caller's `is_complex=True` with no spans is kept (the fast path at 186-193 skips normalisation for uniform, span-free grids).
- `Cell.text` is `str(v).strip()`, which also strips U+3000 (89-98). `from_raw` marks **row 0** `is_header` (341-375). `from_2d_array` does the same (321-338).

**`TableRenderer.render`** (391-397): no cells → `""`; `is_complex` → the style renderer; else a pipe table (`| a | b |` + `| --- | --- |`) **in every style**.
- `minimal_html` (425-451): one element per line, no indentation; `<th>` iff `cell.is_header`; `rowspan`/`colspan` only when >1; continuation cells skipped.
- `markdown_grid` (453-501):
  - first line `<!-- Merged: R{row}C{col}:{rowspan}x{colspan}, ... -->` (1-based);
  - `⊕` appended to a merged origin, `→` for positions covered to the right, `↓` for positions below;
  - columns space-padded to width ≥3; dashed separator after row 1.
- `styled_html` (503-536): the line `<!-- Complex table converted to HTML for better structure preservation -->`, then `<table border="1" style="border-collapse: collapse; width: 100%;">`, 2/4-space indentation, and inline `style` on every `th`/`td` (`th` adds background `#f0f0f0` + bold).
- Every output ends with `"\n\n"`.

**Cell escaping** (table.py:24-65; probe `cct_escape`)
- Line breaks: CRLF, CR, LF, VT, FF, NEL U+0085, U+2028 and U+2029 all become `<br>`.
- Removed: C0 controls except tab, and DEL 0x7F.
- Markdown cells:
  - lines stripped of ASCII space **and tab**; blank edge lines dropped; U+3000 kept on lines after the first, lost on the first via `Cell` strip (probe);
  - `|` → `\|`;
  - `<` escaped only before `[A-Za-z/!?]`;
  - `&` escaped only when it starts an entity;
  - `\` doubled before ASCII punctuation or at the end of a line.
- HTML cells: `& < >` escaped (plus `"` in styled_html); spaces around breaks are kept.

**Which pipelines produce what**
- DOCX (office_advanced_pipeline.py:1571-1640): a single `w:tbl` walk.
  - `w:gridSpan` → colspan; `w:vMerge` without `val="restart"` continues the cell above, **only if it starts in the same column with the same width**;
  - legacy `w:hMerge` extends left; `w:gridBefore`/`w:gridAfter` shift the row; deleted rows are skipped;
  - text in a continuation cell is appended to the origin; rendered with `render()`.
- PPTX (2242-2366): `tc.gridSpan`; rowspan = count of `vMerge` cells below. `hMerge`/`rowSpan` attributes are not read.
  **Every PPTX table is marked complex and rendered with `_render_html`**, so a span-free PPTX table is `<table>` (minimal/styled) or a padded pipe grid (markdown_grid) (probe).
- XLSX (2640-2694):
  - only `merged_cells.ranges` whose top-left cell has text are used (2650);
  - spans count only the rows and columns kept in the table grid; rows and columns with no content are dropped (bisect, 2683-2686);
  - one `table` item per sheet after a `text:title` "Sheet: <name>" (2612-2619).
- All three use `TableData.from_raw(...)`, and so does the PDF path (pdf_tables.py:693-695). So in every built-in path, **only the first physical row is `<th>`**.

**Where tables end up in results**
- Inline in `doc.content`, and as `json_content` items `{"type":"table","content": <rendered>, "page": n}` (probe: 1 per DOCX/XLSX table, 2 for the 2-slide PPTX).
- `ProcessedDocument.tables` and `.sections` are **never populated by any processor** (grep: only the cache restore at loader.py:1453-1454; probe: `tables=None`).
- PDF also sets `metadata.extra["tables_count"]` (formats/pdf.py:105-110).

### A6. Judge add-on (`doc2mark/judge/*.py` and the hooks)

**Selection** (judge/__init__.py:28-63)
- `resolve_judge(judge)`:
  - `None` → reads `$DOC2MARK_JUDGE`;
  - off-values `"" none off false 0 no` (case-insensitive) → None;
  - `"typesafe"` → `TypeSafeJudge()`;
  - an unknown **env** value → WARNING + None; an unknown **explicit** string → `ValueError`;
  - any object → returned as is (probe).
- `judge_hooks` returns only the *callable* attributes `legibility_judge`, `boilerplate_judge`, `non_content_judge`.

**Loader wiring** (loader.py:144-146, 171-172, 183-184, 337-344)
- An explicit `legibility_judge=`/`boilerplate_judge=` wins over the object's (probe).
- `non_content_judge` is attached through `dataclasses.replace(config, non_content_judge=...)` unless the provider's `OCRConfig` already has one, or OCR is off.
- The legibility and boilerplate hooks only reach `PDFProcessor` (loader.py:437-440). `non_content_judge` is used only by the LLM providers; `ocr/tesseract.py` never screens (grep).

**`TypeSafeJudge`** (keyword-only; judge/typesafe.py:239-278)
- Parameters: `api_key=None` (else `$TYPESAFE_API_KEY`), `model="jev-1.13.0"` (questions.py:19), `hooks=None` (else `$DOC2MARK_JUDGE_HOOKS`, else all three).
- `cache_dir=None`:
  - default dir is `$DOC2MARK_JUDGE_CACHE`, else `$XDG_CACHE_HOME/doc2mark/judge`, else `~/.cache/doc2mark/judge` (judge/cache.py:53-59; probe);
  - `False` → in-memory;
  - `cache=` overrides it.
- `timeout=2.0`, `max_workers=4`, `thresholds`, `suspect_thresholds` (each must be strictly within (0, 1), else `ValueError`), `client=` (stub; the SDK is not imported).
- `hooks` string/iterable: spaces removed, `-`→`_`, unknown → `ValueError` at construction (typesafe.py:120-130). A malformed `DOC2MARK_JUDGE_HOOKS` therefore makes `judge="typesafe"` raise, while a malformed `DOC2MARK_JUDGE` only warns.
- `name = "typesafe"`.
- Attributes are `_Hook` objects or None. Each hook has:
  - `.version` = `.cache_key`: `typesafe:jev-1.13.0:legibility-v2:t0.8`, `typesafe:jev-1.13.0:boilerplate-v2:t0.7`, `typesafe:jev-1.13.0:non-content-v1:t0.95:s0.9` (probe);
  - `.available`; `.prefetch(items)`.
- Other methods: `probability()`, `verdict()`, `usage()` (totals + latency p50/p95 + cache stats), `close()`, `begin_document()`/`end_document()`.

**Availability**
- Built without `client=` → `import typesafe_sdk`. Missing SDK → unavailable "typesafe-sdk is not installed (pip install 'doc2mark[typesafe]')". No key → "TYPESAFE_API_KEY is not set".
- Either case logs **one WARNING per judge instance** "TypeSafe judge unavailable: ...; the deterministic rules decide (same output as without a judge)". Hooks still exist but return None, with `available=False` (typesafe.py:272-275, 285-293; probes in both envs).

**Thresholds and rescaling**
- Raw Jev thresholds: `legibility 0.8`, `boilerplate 0.7`, `non_content 0.95`; suspect `non_content 0.9` (questions.py:101-104).
- Pipeline thresholds for plain hooks:
  - legibility garbled `< 0.7` (strategy.py:71);
  - boilerplate chrome `>= 0.5` (pymupdf_advanced_pipeline.py:70);
  - non-content `>= 0.5`, suspected `>= 0.3` (ocr/refusal.py:55-58).
- `_Hook` maps raw to pipeline with a monotone piecewise-linear rescale so each raw threshold lands exactly on the pipeline one (typesafe.py:85-97, 164-177). Probe: 0.8→0.7, 0.79→0.691, 0.7→0.5, 0.95→0.5, 0.9→0.3, 0.89→0.297.

**Requests**
- SDK client options: `timeout=2.0` per HTTP attempt, `RetryPolicy(max_retries=1, timeout=4.0)` (typesafe.py:51-53, 299-308).
- SDK 0.7.0 semantics:
  - `RetryPolicy.timeout` is a budget that stops a retry from **starting** once elapsed + backoff ≥ 4 s (tenacity `stop_before_delay`; SDK `_core/retry.py:82-86, 118-120`);
  - retries only 408/429/5xx, connection errors and timeouts (retry.py:64);
  - the httpx timeout is per operation.
  So a question that times out twice costs ≈ 2 + ~0.5 backoff + 2 ≈ 4.5 s, and up to ~6 s with a Retry-After.
- Failure handling:
  - 401/403 (or the SDK's auth/permission errors) → `unavailable` for the rest of the process;
  - HTTP 400/404/413/422 do not count toward the breaker;
  - 3 consecutive other failures → pause 60 s, during which questions return None immediately (typesafe.py:58-62, 434-451).
  - Probe: 6 questions against a failing stub made **3** client calls; 5×HTTP 400 made 5 calls (no pause); after a 401 there were no further calls.
- An invalid answer (no probability) → None; it neither counts toward nor resets the breaker (typesafe.py:369-374).
- A failed request is remembered for 60 s so the next hook call does not re-ask (typesafe.py:267-269, 354-357).

**Concurrency**
- `_prefetch` asks all pending questions on a `ThreadPoolExecutor(max_workers)`, and only if ≥2 are pending (typesafe.py:393-406).
- Callers:
  - PDF legibility pages, with OCR on (pymupdf_advanced_pipeline.py:1956-1976);
  - OCR answers of one structured batch (ocr/base.py:421-423 → refusal.py:400-415).
- **Boilerplate lines are asked one by one** (pymupdf_advanced_pipeline.py:1384, 1496).

**Verdict cache** (judge/cache.py)
- Key = sha256 of canonical JSON `{schema "judge-verdict-v1", model, question version, state}` (29, 46-50).
- One JSON file per verdict at `<dir>/<key[:2]>/<key>.json`, written atomically, with a memory layer in front. It never raises; failures are never cached (113-171).
- Probe:
  - prefetch 4 + ask 4 → `{asked:4, cached:4, fresh:4}`; `cached` counts cache reads by hook calls, including verdicts prefetched moments earlier in the same document;
  - a re-run from disk → `fresh:0`, 0 client calls.

**What is sent** (questions.py)
- `{"page_text"}`: whole text ≤1500 chars, else beginning, middle and end ≤500 chars each, cut at nearby line breaks, joined by `"\n[...]\n"` (probe: 1514 chars for a 5000-char single line) (26-27, 110-134).
- `{"line","where","font"}` + optional `"note"` for numbered labels. Numbers become words; `where` includes "on N of the M pages" (151-169).
- `{"ocr_answer"}`: stripped, ≤600 chars (MAX_JUDGE_CHARS, refusal.py:53, 377-378).
- Legibility is asked only for layers ≥20 chars not flagged by the detector (strategy.py:72, 402-405), with OCR on, for pages that would keep their text route (pymupdf_advanced_pipeline.py:2023-2037, 2087-2091, 2234-2237).
- Endpoint: SDK default `https://api.typesafe.ai`, overridable with `$TYPESAFE_BASE_URL` (SDK `constants.py:6,15`).
- Only exception names and HTTP status are logged; the key is never logged (typesafe.py:434-443).

**Logging**
- The SDK logs request/response headers and bodies at DEBUG and request, retry and response lines at INFO (SDK `_core/transport.py:59-95`). It applies `TYPESAFE_LOG_LEVEL` (`debug/info/warn/warning/error/off`) at import (`_core/logging.py:19-26, 55-66`).
- doc2mark installs `_NoWireLog`, which drops SDK records below INFO unless the `typesafe_sdk` logger has its own level. It is installed **only when doc2mark builds the client** (`_get_client`), not with `client=` (typesafe.py:100-117, 300).
- CLI: `-v` = DEBUG, default = WARNING (cli.py:25-31). `--parallel N` uses a `ProcessPoolExecutor`, so there is one judge (and one breaker) per worker process (cli.py:585).

**Per document**
- `extra["judge"] = {"name","model","asked","cached","fresh","failed","input_tokens","cost_usd"}` is stamped only when `asked` or `failed` > 0, and only for judges with `begin_document`/`end_document`. A plain hook object gets no stamp (probe) (loader.py:536-563).
- Probe with stub 0.9 on the 6-page PDF: `{'name':'typesafe','model':'jev-1.13.0','asked':2,'cached':0,'fresh':2,'failed':0,'input_tokens':240,'cost_usd':1.008e-05}`.
- If any question failed: one WARNING "<file>: N judge question(s) got no answer (<reason>); the deterministic rules decided those cases", and the document is not written to `cache_dir` (probe).
- `cost_usd = input_tokens × $0.042/M` (questions.py:22).

**Hook contracts: range handling differs between hooks**
- Legibility and boilerplate treat None, an exception or a value outside [0,1] as "keep" (strategy.py:427-443; pymupdf_advanced_pipeline.py:1802-1816).
- `non_content_judge` (refusal.py:367-390) has **no range check**. Probe: returning `1.5` or `7` → the answer is dropped as no content; `-1`/`NaN` → kept, not flagged, cacheable.
- For `non_content_judge`, `None`, non-numbers and bools set `unanswered` (`non_content_unjudged`), so the result is **not OCR-cached**.

### A7. PDF running headers, footers and page numbers (`pymupdf_advanced_pipeline.py:58-95, 1244-1517`)

**Scope**
- Always on; no option. Documents with <2 pages are skipped (1307-1308).
- Evidence needs ≥3 pages (`_CHROME_MIN_PAGES`) in a slot carrying evidence on more than half the pages, within the top/bottom 12% band (63-66, 1390-1395). In practice ≥3-page PDFs.

**What happens to a qualifying line (verbatim first)**
- **Every copy removed**: bare page numbers (`Page N of M`, `N/M`, `- N -`, roman, `第 N 頁`, ...) and lines repeating a title or heading of the document.
- **First copy kept** (as plain text, unless heading-sized), later copies typed `text:header`/`text:footer`: every other running header or footer.
- **Every copy kept** without a judge: lines with weaker evidence (few pages, attached to content, table header rows) and page-order numbers with a non-page label (`3 | ACME Corp`, `Lesson · 3`).
- The optional `boilerplate_judge` is asked only about the kept repeats, once per line or numbered group. A verdict `>= 0.5` thins the line to its first copy (or to the copy in the page body, e.g. a cover). It never removes the last copy.

**Output**
- `pdf_to_markdown` skips `text:header`/`text:footer` (4531-4535), and the chunker renders them as `""` (chunker.py:155-157). The typed items **remain in `json_content`** and JSON output.

**Probe `cct_chrome` (6-page synthetic PDF, PyMuPDF 1.27.2)**

| Line | No judge | Plain judge object answering 0.9 |
|---|---|---|
| Running header "ACME Corp - Quarterly Report" | Markdown ×1 (first copy); 5 `text:header` items | ×1 (not asked) |
| "Page N of 6" | Markdown ×0; 6 `text:footer` items | ×0 |
| 2-page line "Draft for internal review" | ×2 | ×1 (asked, `few_pages`) |
| "Lesson · N" on all 6 pages | ×6 | ×1 (asked, `numbered_label`) |

- Chunk overlap repeated the kept header in 3 chunks (short leading chunks).
- The plain judge object produced no `extra["judge"]`.

### A8. `ProcessedDocument` (`core/base.py:111-165`)

**Fields**: `content`, `metadata`, `images`, `tables`, `sections`, `json_content`.
- `images` is `None` for PDF and TXT but `[]` for DOCX, PPTX and XLSX when `extract_images=False` (probe).
- `tables` and `sections` are always None (A5).

**Methods and properties**
- `to_dict()`: `asdict(metadata)` with `format` as its value, and `_json_safe` converting Enum → value, bytes → base64, dict/list/tuple recursively. Other types (set, Path, datetime) pass through unchanged. Probe: `json.dumps(to_dict())` succeeded for the PDF and DOCX samples with images (base.py:98-132).
- `markdown` returns `content`, whatever the output format: JSON text in JSON mode, "text" content in TEXT mode (base.py:148-151; probe).
- `text` runs 5 regexes: `#+ ` (anywhere), `**x**`, `*x*`, `[x](y)`, `` `x` `` (base.py:153-165).
  Probe: `"C# is **bold** and _it_ ![alt](img.png) [link](http://x)"` → `"Cis bold and _it_ !alt link"`. `_x_`, list markers, HTML tables and `<!-- page N -->` stay.

---

## Part B. Claim verdicts

### B1. docs/caching.rst (all)
- L4-7 caching skips the provider call and is opt-in via `ocr_cache` on `load`, `UnifiedDocumentLoader` and the batch helpers: **TRUE** (A1, __init__.py:103-327). Nuance: a hit needs the same image bytes *and* the same provider settings (key, A1).
- L11 MemoryOCRCache thread-safe in-process LRU with TTL: **TRUE** (cache.py:440-558).
- L12-13 Redis requires `doc2mark[redis]`: **TRUE** (pyproject.toml:79-81; cache.py:613-616).
- L15-16 NoOp accepts writes and always misses: **TRUE** (cache.py:561-574).
- L18-20 every answer cached, incl. empty and refusal/"no text" answers: **TRUE** (cache.py:197-215, 1016-1025).
- L20-24 "Only a failed answer ... and a result that still withholds values ... are asked again": **PARTLY**.
  Also never cached: answers flagged `non_content_unjudged`, and a batch during which the judge became unavailable (cache.py:213-214, 1018-1019). The INFO log and the miss for such stored entries are TRUE (1027, 1069).
- L24-28 provider refusal cached for `refusal_ttl_seconds` only (default 600), not extended by hits: **PARTLY**.
  The TTL is `min(refusal_ttl_seconds, cache ttl_seconds)` (cache.py:1021-1022; probe 300 with ttl 300). Non-extension is TRUE (393-394).
  The "empty structured answer whose recovery the provider refused" part: **TRUE** (ocr/base.py:520-521).
- L29-31 key version moved to `ocr-cache-v6`, older entries never read: **TRUE** (cache.py:23-25, 250-251).
- L31-35 `cache_dir` never expires and does not store documents with failed OCR, unread pages or provider refusals: **TRUE** (loader.py:1290-1310, 733-742). It also skips judge-incomplete documents, which is not mentioned (loader.py:737-738).
- L42-56 Quick start (params and comments; `load(..., ocr_cache=cache)`): **TRUE** (cache.py:443-451; __init__.py:96-147).
- L61-62 factory handles Redis fallback: **TRUE** (cache.py:866-920).
- L69, L72-80 examples; `key_prefix` default; fallback values `"memory"`, `"none"`, `"raise"`: **TRUE**. Aliases are also accepted, and fallback to memory logs a WARNING (cache.py:905-920).
- L82-83 `create_ocr_cache("none")` disables caching (returns None): **TRUE** (cache.py:854-855).
- L88-98 loader example: **TRUE** (loader.py:354-378).
- L100-103 the same `ocr_cache` on `load`, `document_to_markdown`, `batch_convert_to_markdown`, `batch_process_documents`: **TRUE** (also `batch_process_files`).
- L121-123 "A cache hit extends the expiry by this amount (up to max_refreshes times)": **PARTLY**. A hit *resets* the expiry to now + ttl, capped at created + max_age (cache.py:387-398).
- L125-127 max_age absolute, None = no limit: **TRUE** (375-380).
- L129-131 max_entries LRU eviction: **TRUE** (489, 555-558; probe).
- L133-135 max_refreshes, None = unlimited: **TRUE** (383-384; probe).
- L137-141 `refusal_ttl_seconds` default 600, explicit `ttl_seconds` never extended, accepted by `create_ocr_cache`: **TRUE** (cache.py:450, 849).
- L146-162 Redis install/example: **TRUE**.
- L164-166 ping on construction raises on failure; native EX; `cleanup()` no-op: **TRUE** (626, 688, 694-695; probe ConnectionError).
- L170-187 Redis params and defaults: **TRUE** (599-608). `time_func` is not listed.
- L192-197 all backends have `stats()`, example shape: **TRUE**.
- L199-200 counter list: **TRUE** (cache.py:59-69).

### B2. docs/api/cache.rst (all)
- L4-5 "request-scoped OCR result cache": **FALSE**. Memory caches live as long as the instance and are shared across loads and loaders; Redis persists across processes up to `max_age` (cache.py:440-465, 592-629). Nothing scopes a cache to a request. The loader docstring repeats the phrase (loader.py:80).
- L6 "Cached values use the **cache v4** schema": **FALSE**. The key schema is `ocr-cache-v6` and the value schema `ocr-cache-value-v2` (cache.py:25-26).
- L7-10 values store the full `OCRPage` alongside the text, so consumers always get complete results: **TRUE** (cache.py:290-297, 505). Nuance: replayed copies carry `metadata["doc2mark_from_cache"]=True`.
- L12-16 wired through `ocr_cache` on `load`/`UnifiedDocumentLoader`; factory or direct class: **TRUE**.
- L25-49 `OCRCache` abstract methods and their descriptions: **TRUE** (cache.py:405-427). An explicit `set(..., ttl_seconds)` entry is never extended; not mentioned.
- L59-61 MemoryOCRCache "is the default backend returned by create_ocr_cache when provider='memory'": **TRUE** literally. Misleading: the factory's default `provider="none"` returns None.
- L76-94 Memory params (`ttl_seconds` extended on access, `max_age_seconds`, `max_entries` LRU, `max_refreshes`, `time_func`): **TRUE** (cache.py:443-465). `refusal_ttl_seconds` is missing here, for Redis, and for `create_ocr_cache`.
- L104-110 Redis lazy import, ping, fallback via the factory "transparently": **TRUE**. The fallback logs a WARNING.
- L126-128 `redis_url` required, `""` → `ValueError`: **TRUE** (609-610).
- L130-144 Redis `ttl`/`max_age`/`max_refreshes`/`key_prefix`/`time_func`: **TRUE**. A trailing `:` on the prefix is stripped (623).
- L148-149 Redis `cleanup()` no-op via EX: **TRUE**.
- L159-167 NoOp never stores, `get` always None: **TRUE**.
- L177-181 CachedOCR intercepts `process_image` and `batch_process_images` and dedups within a batch: **TRUE** (991-993, 1034-1098; probe). Dedup is per cache key.
- L183-185 the loader creates CachedOCR when `ocr_cache` is passed: **TRUE** (loader.py:362-369).
- L189-197 example (`OCRFactory.create("openai", api_key=...)`, `CachedOCR(wrapped=..., cache=...)`, `.process_image(image_bytes)`): **TRUE** (API).
  Probe: builds an `OpenAIOCR` offline, and `CachedOCR.process_image` exists.
  "like any BaseOCR instance" is loose: `BaseOCR` declares only `batch_process_images`.
- L201-211 CachedOCR params; `cache_version` default v6; changing it invalidates entries: **TRUE** (926-934, 250).
- L224-242 factory description and examples; `create_ocr_cache("none")  # returns None`: **TRUE** (probe).
- L246-254 recognized provider names: **TRUE**. Case- and whitespace-insensitive; `None` also returns None; unknown → `ValueError`.
- L256-257 `redis_url`: **TRUE**. Without it, `"redis"` silently takes the fallback (WARNING "redis_url is required"; probe).
- L259-262 fallback: `"memory"` "silently degrades", `"none"` disables, `"raise"` re-raises: **PARTLY**. Both degrade paths log a WARNING (cache.py:907, 916); `raise` is TRUE (919).
- L264-282 tuning params forwarded; returns `OCRCache` or None: **TRUE** (844-886).
- L290-319 wiring examples: **TRUE** (API).

### B3. docs/api/chunking.rst (all)
- L4-9 "operates on the `json_content` ... produced by `load` (with `output_format="json"`), not on raw Markdown": **PARTLY**.
  `json_content` is already filled in Markdown mode for PDF and Office. For TXT, MD, CSV and JSON it is None in Markdown mode, and a single whole-document `text:normal` item in JSON mode. Those formats yield one unsplittable chunk (A4 probe).
- L11-18 two modes; tiktoken fallback; `doc2mark[tokenizers]` extra: **TRUE** (chunker.py:42-55; pyproject.toml:82-84). An unknown `encoding_name` raises `ValueError` rather than falling back.
- L30-40 config example and defaults: **TRUE** (chunker.py:22-31).
- L45-47 `max_chunk_size` "Maximum size of a single chunk": **PARTLY**. It is a soft limit: items (paragraphs, tables) are never split, and overlap and footnotes are added after sizing (probe: 4999 chars with max 1500).
- L49-51 overlap "trailing units from the previous chunk prepended": **PARTLY**.
  - It is ≤`overlap` units, trimmed to after the first space.
  - It is taken from the previous chunk's *final* text, so it can include that chunk's own overlap; a short previous chunk is prepended whole.
  - It crosses section boundaries (probe).
- L53-56 split level: "Level 1 = `text:title`, level 2 = `text:section`": **TRUE** (chunker.py:122-128). "Heading depth": **PARTLY**. Every `text:section` counts as level 2 whatever its `level` (2–6), so values ≥2 behave the same and h3+ always split (probe).
- L58-60 `keep_tables_whole` "kept intact in a single chunk rather than being split across two": **FALSE**.
  A table is never split in either mode. `True` appends an oversized table to the current chunk; `False` flushes the current chunk so the table starts its own (chunker.py:259-266; probe).
- L62-63 `include_page_markers` "Reserved for future use": **TRUE**. It is never read (grep). Chunks never carry the `<!-- page N -->` markers that `doc.content` has.
- L65-72 `size_unit` chars/tokens, warning and char fallback: **TRUE**. Any other value is silently treated as chars (probe `"words"`).
- L74-77 `encoding_name` and example encodings: **TRUE** (tiktoken names; not doc2mark-specific).
- L89-91 `Chunk` "Immutable result object": **FALSE**. It is a plain mutable dataclass, and the chunker itself mutates `content` (chunker.py:10, 315, 320, 360; probe `frozen=False`).
- L96-101 `content`, `section_title` (None before the first heading): **TRUE**.
- L103-105 `section_hierarchy` "ordered list of ancestor headings": **PARTLY**.
  Only title plus the current section; h3 is not nested under h2. Without a `text:title`, the first section is wrongly kept as every later section's ancestor (chunker.py:192-196; probe `['S1','S2']`).
- L107-112 `page_start`/`page_end`: **TRUE**. The "page" is the slide for PPTX and the **sheet index** for XLSX; None for text formats.
- L114-116 `content_types` "Set of item type strings present": **PARTLY**.
  It is a list in arbitrary order. It can include `text:header`/`text:footer` that render nothing, and never includes `text:footnote` (chunker.py:233; probe).
- L118-120, L163-165 `chunk_index` sequential from 0: **TRUE** (111-113).
- L132-135, L142-158 signature and params: **TRUE**. "Pass ... `load(..., output_format="json").json_content`": **PARTLY** (see L4-9).
- L170-172 grouping by title/section up to `split_on_heading_level`: **TRUE** (with the level caveat above).
- L173-175 items accumulate until the next would exceed the max, then flush: **TRUE**, except for tables when `keep_tables_whole=True` and for single oversized items.
- L176-179 overlap prepended, word boundary, token mode encodes/slices/decodes: **TRUE** (323-360). Cross-section behaviour is not mentioned.
- L180-182 footnotes appended to referencing chunks, unreferenced ones to the last chunk: **PARTLY**.
  - Only `[^id]: ...` definitions match `[^id]` references (DOCX emits these, office_advanced_pipeline.py:1055-1056, 1162).
  - PDF footnote items are plain "1 text", so every PDF footnote goes to the last chunk.
  - Non-`[^id]` footnotes sharing their first 20 characters are silently dropped (chunker.py:86-90; probe).
  - Each definition goes only to the *first* referencing chunk.
- L183 empty list → empty list: **TRUE** (71-72).
- L184-186 `get_chunks` delegates to `chunk_content`: **PARTLY**. When `json_content` is empty or None it returns one chunk holding the whole `content`, with no size limit (base.py:144-145; probe).
- L188-228 examples: **TRUE** (API). `load("report.pdf", ...)` uses the default openai provider, which works without a key when no OCR is requested (probe).

### B4. docs/tables.rst (requested sections)

**The Office path (L160-179)**
- L163-164 merge map read from the markup, not guessed from blanks: **TRUE** (office_advanced_pipeline.py:650-666).
- L166-171 DOCX `w:gridSpan` colspan; `w:vMerge` restart/continue/bare; second pass counts continuations; O(n·m): **TRUE** (1585-1640).
  Unstated conditions: a continuation extends the origin only when it starts in the same column with the same width; legacy `w:hMerge`, `gridBefore`/`gridAfter` and deleted rows are handled; continuation text is appended.
- L172-174 PPTX `gridSpan`/`vMerge`, blank continuations, origin accumulates spans: **TRUE** (2242-2340). Unstated: every PPTX table is rendered as complex (2346-2360).
- L175-177 XLSX `merged_cells.ranges` with span `max-min+1`, re-clamped when columns are dropped: **PARTLY**.
  Rows are dropped and re-clamped too (bisect over kept rows and columns), and a merge whose top-left cell is empty is ignored (2650, 2680-2686).
- L179 all converge on `TableData`: **TRUE** (`from_raw` in all three and in pdf_tables.py:693-695).

**From TableData to clean HTML (L181-222)**
- L184-196 validator bullets (pad, clamp, no hidden values with widest-then-tallest shrink, one warning the first time, continuations, `is_complex` True on spans and False when all shrunk): **TRUE** (table.py:186-280).
  The warning is once per *process*. A caller's `is_complex=True` without spans is kept.
- L198-202 minimal HTML: one `<tr>` per row, `<th>` for the first physical row and `<td>` elsewhere, spans only >1, continuations skipped: **PARTLY**.
  The renderer writes `<th>` for `is_header` cells. Every built-in path flags only row 0 (`from_raw`, table.py:370), so the observed output matches. Spans and continuations: TRUE (425-451).
- L202-203 "Simple (span-free) tables render as ordinary pipe-delimited Markdown instead": **PARTLY**. TRUE for DOCX, XLSX and PDF (table.py:395-397). **FALSE for PPTX**: span-free tables become `<table>` in minimal/styled (probe).
- L205-222 escaping rules: **TRUE** (probe `cct_escape`: every example reproduced). Minor: tab is stripped around breaks too; NEL and DEL are handled.
  "full-width indentation stays": **PARTLY**. True for lines after the first; the first line's U+3000 is lost because `Cell.text` is `.strip()`ped (table.py:96).

**Choosing the output style (L224-254)**
- L227-234 loader `table_style` maps to `TableStyle`; example: **TRUE** (loader.py:65, 181).
- L236-240 CLI `--table-style markdown_grid`: **TRUE** (cli.py:302-307).
- L242 "The three accepted values (string or enum)": **PARTLY**. Only the exact lowercase strings or enum members work. Anything else: `ProcessingError` for PDF; a silent downgrade for Office (warning, merges and `json_content` lost; A5 probe).
- L244-246 `minimal_html` default, only `rowspan`/`colspan`: **TRUE**.
- L248-251 `markdown_grid` comment and `⊕`/`→`/`↓` markers: **TRUE** (format `R{r}C{c}:{rows}x{cols}`, 1-based).
- L253-254 `styled_html` inline border/style: **TRUE** (plus a leading HTML comment line).
- Missing from the section: a style applies only to tables with merged cells (and to all PPTX tables); other tables are pipe tables.

**Worked example (L256-323)**
- L276-278 Office `gridSpan`/`vMerge` (and XLSX merges) yield `colspan=3` and `rowspan=2`: **TRUE** (probe on generated DOCX, XLSX and PPTX). The PDF geometry case is **UNVERIFIABLE** here; it is out of scope.
- L278-315 "doc2mark emits exactly" this HTML: **TRUE** except that the renderer appends one trailing blank line (`"\n\n"`).
  Byte-identical after `rstrip("\n")`, both via `TableData.from_raw` and from real DOCX, XLSX and PPTX loads (probe). Outputs are in Part C.
- L317-323 corner `<th></th>` kept, one `<th colspan="3">`, `<td rowspan="2">`, Canada row omits its first cell, only the first physical row is `<th>`: **TRUE** (probe; row-0 flag from `from_raw`).

**Reading tables from a result (L379-419)**
- L382-385 table HTML inline in `doc.content`, plus a `json_content` item `type=="table"`: **TRUE** (probe: `item["content"] in doc.content`).
  In `OutputFormat.JSON` mode, `doc.content` is the JSON dump (loader.py:721-728). `doc.tables` stays None.
- L389-396 example: **TRUE**. `item["content"]` is HTML only for tables with spans; span-free DOCX, XLSX and PDF tables are pipe Markdown.
- L398-415 vision path API (`OCR("openai")`, `read_one(bytes)->OCRResult`, `result.document`, `page.raw.tables`, `Table.caption/html/headers/rows`, `result.text`): **TRUE** for the API surface (signatures and fields probed: `Table` has caption, headers, rows, html, markdown, illustrative, row_count).
  Runtime output is **UNVERIFIABLE** (needs the OpenAI API). `result.document` can be None in free-form mode (`structured=False`).
- L417-419 summary: **TRUE** (with the PPTX and simple-table caveats above).

### B5. docs/judge.rst (non-numeric claims)
- L4-9 three hooks, asked only where the rule leaves a case open, and the rules decide as before without a judge: **TRUE** (A6).
- L14-23 legibility: input is the page text of layers the detector did not flag, whole ≤1500, else beginning, middle and end (500 each); only with OCR on; illegible → OCR'd from its render: **TRUE** (questions.py:110-134; strategy.py:402-443; pipeline 2023-2037).
  Minor: pieces are ≤500 and cut at line breaks, joined by `\n[...]\n`; layers <20 chars are never asked.
- L25-37 boilerplate hook (A7):
  - verbatim first; running header keeps its first copy; weak lines keep every copy; judge asked only about those; thinned to the first copy or the body copy; never the last copy; not asked about a running header's first copy: **TRUE** (pipeline 1259-1517; probe).
  - "Titles, per-page labels (`Lesson 3`), unit notes and disclaimers keep every copy": **PARTLY**. This is what the question asks the model (questions.py:73-77), not code. A judge that says yes thins them (probe: `Lesson · N` → 1 copy). Also, a running header that repeats the document title loses every copy by rule.
- L39-43 deterministic check fires only on a whole-answer "first-person refusal"; the example answer is kept; the judge gets ≤600-char answers the patterns keep: **PARTLY**.
  The patterns also fire on non-first-person "no readable text" statements (probe: "No text found." → True). The quoted example is kept (probe: False). ≤600: TRUE (refusal.py:53, 377-378).
- L44-48 from raw 0.95 → no content; structured path re-read by recovery (screened the same way); free-form path dropped and flagged `ocr_refusal`: **TRUE** (rescale probe; ocr/base.py:400-428, 449-466, 503-521).
- L48-51 0.90–0.95 kept, flagged `non_content_suspected`, counted in `ocr_issues["suspected"]` with page/slide/sheet in `locations`: **TRUE** (refusal.py:390; usage.py:287-292, 210-222).
- L55-56 each hook returns P(yes) or None; "None, an exception or a value outside [0, 1] leaves the rule's decision in place": **PARTLY**.
  TRUE for legibility and boilerplate. **FALSE for `non_content_judge`**: there is no range check, so `1.5` drops the answer (refusal.py:382-390; probe). None marks the result unjudged, so it is not OCR-cached.
- L61-73 install/CLI/Python example; `extra.get("judge")` holds asked/cached/failed/tokens/cost: **TRUE** (cli.py:277-285; loader.py:554-558). It is absent when nothing was asked.
- L75-76 `DOC2MARK_JUDGE=typesafe` when neither `judge=` nor `--judge` is given; `none` switches off; default none: **TRUE** (judge/__init__.py:34-54; cli.py:279-280 default None). Off aliases: `off/false/0/no/""`.
- L78-82 `judge=` object attributes are wired; explicit hooks and an `OCRConfig.non_content_judge` win: **TRUE** (loader.py:183-184, 337-344; probe). Only callable attributes count; legibility and boilerplate apply to PDFs only.
- L86-91 `TypeSafeJudge(hooks=..., cache_dir=..., timeout=2.0, max_workers=4)`: **TRUE** (typesafe.py:239-243; the defaults are exactly those values).
- L96-98 without the extra the SDK is never imported; without a judge nothing is sent: **TRUE**. The import is attempted only when a `TypeSafeJudge` is built (typesafe.py:285-289).
- L100-101 no extra or no key → one warning with the quoted prefix, same output as `--judge none`: **TRUE** (one per judge instance; probes in both envs).
- L102 401/403 → switches off for the run: **TRUE** (typesafe.py:444-446; probe).
- L103-105 network, timeout, 429/529, invalid answers → rule decides; one warning per document with the count: **TRUE** (loader.py:559-562; probe).
- L105-107 "Each HTTP attempt times out after 2 s ... one retry within a 4 s budget, so ... at most about 4 s": **PARTLY**.
  The budget only prevents a retry from *starting* after 4 s, so two timeouts ≈ 4.5 s (≤~6 s with Retry-After) (SDK retry.py:82-86, 118-120; tenacity `stop_before_delay`).
- L107 "the questions of a document are asked concurrently": **PARTLY**. Legibility pages and OCR answers of a batch are prefetched concurrently (≥2 pending); header/footer lines are asked sequentially (pipeline 1384, 1496).
- L107-109 pause for a minute after 3 failures in a row; questions then return at once; once per process with `--parallel`: **TRUE** (typesafe.py:58-59, 355-357, 447-451; cli.py:585; probe). HTTP 400/404/413/422 and invalid answers do not count.
- L111-115 a conversion never fails because of the judge: **TRUE** (hooks catch everything, typesafe.py:187-199). Exception: a bad `DOC2MARK_JUDGE_HOOKS` or threshold raises `ValueError` at construction (typesafe.py:127-130, 250-252).
  The rest (judge failure → not written to `cache_dir`; unscreened OCR answer not OCR-cached; unavailable judge keyed as no judge in both caches): **TRUE** (loader.py:549, 737-738; cache.py:152-165, 213-214; probes).
- L120-125 Noul over named states (`page_text`; `line`/`where`/`font` in words; `ocr_answer`); pinned `jev-1.13.0`; question versions: **TRUE** (questions.py:19, 48-94, 110-174). A `note` key is added for numbered labels.
- L127-130 verdicts cached on disk keyed by model id, question version and state hash; `$DOC2MARK_JUDGE_CACHE`, else `~/.cache/doc2mark/judge`; `cache_dir=False` → memory: **PARTLY**. `$XDG_CACHE_HOME/doc2mark/judge` is checked before `~/.cache` (judge/cache.py:53-59; probe). The rest is TRUE.
- L130-132 re-run sends nothing; OCR and document caches key on model, question version and threshold: **TRUE** (probe re-run: 0 calls; hook version string includes all three; loader.py:500-523).
- L134-136 pages asked concurrently (≤`max_workers` in flight), OCR answers of a batch too; `timeout` default 2; retry within 4 s: **PARTLY**. Same retry caveat as L105-107. Concurrency: TRUE for legibility pages and OCR answers.
- L138-143 SDK logs bodies at DEBUG and request lines at INFO; doc2mark drops DEBUG; `-v` shows INFO; default WARNING shows none; `TYPESAFE_LOG_LEVEL=debug` or a logger level shows the wire log: **TRUE** (SDK transport.py:59-95, logging.py:55-66; typesafe.py:100-117; cli.py:25-31).
  Caveat: the filter is installed only when doc2mark creates the SDK client, not with `client=`.
- L148-151 what is sent (≤1500 chars per judged page, judged header/footer lines, short OCR answers) to `api.typesafe.ai`: **TRUE** (questions.py). The sample is ≤1514 chars including separators. The endpoint is the SDK default and can be overridden with `TYPESAFE_BASE_URL` (SDK constants.py:6, 15).
- L151-153 TypeSafe's training, DPA and zero-retention terms: **UNVERIFIABLE** (external legal docs).
- L154 "doc2mark never logs the API key": **TRUE** (only exception class and status are logged, typesafe.py:434-443).
- L164-165 `extra["judge"]` keys `asked, cached, fresh, failed, input_tokens, cost_usd`: **TRUE** but incomplete.
  It also has `name` and `model`. It is present only when something was asked or failed. `cached` includes verdicts prefetched in the same run. It is replayed unchanged from `cache_dir` (A2, A6).

### B6. README.md sections
**Complex tables (L77-109)**
- L83-84 "Both the native Office/PDF table extractor ... emit clean HTML": **PARTLY**. Only tables with merged cells (and every PPTX table) become HTML; others are pipe Markdown (A5).
- L86-92 HTML sample: **PARTLY** (illustrative). The native renderer puts one element per line with no indentation and writes the second row as `<td>Q1</td><td>Q2</td>`, since only row 0 is `<th>`. Actual output is in Part C.
- L98-104 snippet `table_style` values and comments (minimal default with rowspan/colspan; markdown with merge annotations; full HTML with inline styles): **TRUE** (table.py:68-76, 425-536; loader.py:115-118, 181).
- L106-109 OCR table HTML sanitized to a table-only allowlist: out of my scope (OCR path); not verified.

**Clean PDFs (L403-412)** (A7)
- L405-408 "detects content that recurs in the top/bottom margin zone ... and drops it automatically": **PARTLY** (contradicts CHANGELOG [Unreleased] L78-92, L247-254 "verbatim first").
  Only bare page numbers and lines repeating a document title or heading lose every copy. Other running headers and footers keep their **first copy**. Weaker repeats and labelled per-page numbers keep **every copy** unless a judge thins them (probe).
- L408 "no configuration required": **TRUE** (always on; no switch).
- L410 "on by default for multi-page PDFs": **PARTLY**. Detection needs ≥3 pages of evidence on more than half the pages (`_CHROME_MIN_PAGES=3`, `_CHROME_MIN_SHARE=0.5`), so 2-page PDFs are never cleaned.
- L410-412 applied before Markdown and chunking, so the repeated chrome "never reaches your output or your vector store": **PARTLY**.
  The removed copies are typed `text:header`/`text:footer`, which Markdown and the chunker skip (pipeline 4531-4535; chunker.py:155-157), but they remain in `json_content` and JSON output. One copy of most running headers stays by design, and chunk overlap can repeat it (probe: 3 chunks).

**Optional: quality judge (L414-462)** (non-numeric)
- L416-419 rules keep text; three decisions; judge asked only about open cases: **TRUE**.
- L421-423 legibility: illegible page OCR'd from its render; OCR must be on: **TRUE**.
- L424-426 thinned "normally to one copy"; never removes the last copy: **TRUE** (probe).
- L427-429 "LLM OCR providers only, not Tesseract"; answer re-read or dropped: **TRUE** (tesseract.py never screens; ocr/base.py:400-466).
- L442 off by default: **TRUE**.
- L444-448 install (`doc2mark[typesafe]`, also in `[all]`), key, CLI: **TRUE** (pyproject.toml:87-102).
- L450-454 Python example; `DOC2MARK_JUDGE=typesafe` alternative: **TRUE**.
- L456-457 no extra or key → same output; unanswerable question → rules: **TRUE**.
- L457-461 third-party data flow (`api.typesafe.ai`, ≤1500 chars per judged page, header/footer lines, short OCR answers): **TRUE** (see B5 L148-151).

**OCR result caching (L564-592)**
- L566-568 two backends (in-memory, Redis): **TRUE** (NoOp also exists).
- L573-575 "In-memory cache (default settings)" `MemoryOCRCache(ttl_seconds=3600, max_entries=1024)`, `load(..., ocr_cache=cache)`: **TRUE** (these are the defaults).
- L577-578 `create_ocr_cache("memory", ttl_seconds=7200)`: **TRUE**.
- L581-590 Redis for production (`doc2mark[redis]`), loader example: **TRUE**. If Redis is unreachable the factory silently (WARNING) falls back to memory.
- L592 "caching documentation ... for the full API reference": **PARTLY**. The API reference is `docs/api/cache.rst`.

**Chunking for RAG (L594-633)**
- L602 `load("report.pdf", output_format="json")`: **TRUE**, but JSON mode is unnecessary for PDF and Office (B3 L4-9).
- L605 `# max characters per chunk`: **PARTLY** (soft limit; B3 L45-47).
- L606 `# overlap between consecutive chunks`: **TRUE**. Up to 200 chars, word-trimmed, crosses sections.
- L607 `split_on_heading_level=2,  # split on h1 and h2`: **PARTLY**. It splits on the title and **every** section heading, h2–h6 (B3 L53-56).
- L608-609 `keep_tables_whole`, `include_page_markers`: shown without claims; `include_page_markers` is inert.
- L618-620 Chunk metadata fields: **TRUE** (chunker.py:10-19).
- L622-625 token mode via tiktoken, `doc2mark[tokenizers]`, character fallback: **TRUE** (warning logged). In the fallback case `max_chunk_size=512` silently means 512 characters.
- L628-632 `max_chunk_size=512  # tokens`: **TRUE** when tiktoken is installed.

### B7. docs/api/types.rst: TableStyle and ProcessedDocument
- L97-98 TableStyle selects the rendering of merged-cell tables and lives in `doc2mark.core.table`: **TRUE**. Exception: PPTX tables always use it.
- L100-102 `MINIMAL_HTML` bare `<table>` without inline styles; default via `TableStyle.default()`: **TRUE** (table.py:74-76).
- L104-106 `MARKDOWN_GRID` pipe table with merge markers and an HTML comment of spans: **TRUE**.
- L108-110 `STYLED_HTML` borders, padding, header background: **TRUE** (plus a leading HTML comment).
- L112-117 example: **TRUE**.
- L131-132 dataclass with these fields: **TRUE** (base.py:111-119).
- L134-135 `content` "Markdown by default": **TRUE**. It is the JSON dump in JSON mode and plain-ish text in TEXT mode (loader.py:721-731).
- L137-138 `metadata`: **TRUE**.
- L140-142 `images` "None when image extraction is not requested": **PARTLY**. None for PDF and TXT, but `[]` for DOCX, PPTX and XLSX (probe).
- L144-145 `tables` "Extracted table data. None when no tables are present": **FALSE**. It is always None; tables are in `content`, `json_content` and (PDF) `extra["tables_count"]` (A5).
- L147-148 `sections` "when the format supports them": **FALSE**. It is always None.
- L150-153 `json_content` "None when the output format does not produce it": **PARTLY**. The *input* format decides: PDF and Office always have it; text formats have it only in JSON mode, as a single item (A4).
- L157-158 `markdown` alias for `content`: **TRUE** (returns JSON text in JSON mode).
- L160-161 `text` "content with common Markdown formatting stripped": **PARTLY**. Only 5 regexes, and `#+ ` is not anchored, so `C# is` → `Cis`. `_x_`, list markers, HTML tables and comments are kept; images become `!alt` (probe).
- L163-165 `to_dict()` fully JSON-serializable (enums → strings, bytes → base64): **TRUE** for observed documents (probe `json.dumps` OK). Other types (sets, Paths, datetimes) are not converted (base.py:98-108).
- L167-170 `get_chunks(config=None)` section-aware chunks, defaults when None: **PARTLY**. It falls back to one unsized chunk when `json_content` is empty (base.py:144-145).
- L172-192 example: **TRUE** (API; runs without an OpenAI key when no OCR is requested).

---

## Part C. Probe outputs

### C1. tables.rst worked example (`cct_table_example.py`)
Built with `TableData.from_raw(rows, {"is_complex": True, "cell_spans": {(0,2):(1,3), (2,0):(2,1)}})` and rendered with `TableRenderer(style).render(t)`.
Building it from explicit `Cell`s (spans only on the anchors) gives identical output. Loading generated DOCX, XLSX and PPTX files with these merges produced **the same three outputs** (`cct_office_tables.py`).

**minimal_html.** `out.rstrip("\n") == doc HTML` → **True**; exact including the trailing `"\n\n"` → False:
```
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

```
**markdown_grid**
```
<!-- Merged: R1C3:1x3, R3C1:2x1 -->
|            |         | Revenue (USD) ⊕ | →     | →     |
| ---------- | ------- | --------------- | ----- | ----- |
| Region     | Country | 2023            | 2024  | 2025  |
| Americas ⊕ | USA     | $4.2B           | $4.8B | $5.1B |
| ↓          | Canada  | $0.9B           | $1.0B | $1.1B |
| EMEA       | Germany | $2.1B           | $2.3B | $2.5B |

```
**styled_html**
```
<!-- Complex table converted to HTML for better structure preservation -->
<table border="1" style="border-collapse: collapse; width: 100%;">
  <tr>
    <th style="background-color: #f0f0f0; font-weight: bold; padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd"></th>
    <th style="background-color: #f0f0f0; font-weight: bold; padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd"></th>
    <th colspan="3" style="background-color: #f0f0f0; font-weight: bold; padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">Revenue (USD)</th>
  </tr>
  <tr>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">Region</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">Country</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">2023</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">2024</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">2025</td>
  </tr>
  <tr>
    <td rowspan="2" style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">Americas</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">USA</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">$4.2B</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">$4.8B</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">$5.1B</td>
  </tr>
  <tr>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">Canada</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">$0.9B</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">$1.0B</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">$1.1B</td>
  </tr>
  <tr>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">EMEA</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">Germany</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">$2.1B</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">$2.3B</td>
    <td style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd">$2.5B</td>
  </tr>
</table>

```
**Span-free table**: every style gives `'| A | B |\n| --- | --- |\n| 1 | 2 |\n\n'` via `render()`. A 2×2 span-free **PPTX** table loaded through the loader gives `<table><tr><th>A</th>...` (minimal_html).

### C2. README L86-92 sample through the real renderer
Spans `(0,0):(2,1)` and `(0,1):(1,2)`:
```
<table>
<tr>
<th rowspan="2">Region</th>
<th colspan="2">2024</th>
</tr>
<tr>
<td>Q1</td>
<td>Q2</td>
</tr>
<tr>
<td>EMEA</td>
<td>$1.2M</td>
<td>$1.5M</td>
</tr>
</table>
```

---

## Part D. Code issues found while auditing (not doc claims; for the supervisor)
1. **OCR cache key ignores `OCRConfig.task`** while the task selects the structured prompt (cache.py:168-182 vs ocr/openai.py:817-833). A shared cache can replay an answer made under another task. `max_concurrency`, a throughput knob, *is* keyed.
2. **`cache_dir` key ignores the OCR model, prompt, task, detail, language and API key** (only the provider class name; loader.py:645). A changed model replays stale OCR text (probe).
3. **Chunker**:
   - `text:footnote` items without `[^id]:` syntax are keyed by their first 20 characters, so colliding footnotes are silently lost (chunker.py:86-90);
   - every PDF footnote goes to the last chunk;
   - `section_hierarchy` keeps the first section as a fake ancestor when there is no `text:title` (192-196);
   - `level` is ignored, so all sub-headings are `##` and split at level 2.
4. **`non_content_judge` has no [0, 1] check**, so out-of-range values act (refusal.py:382-390). The other two hooks validate.
5. **Invalid `table_style` on Office files silently degrades** to the basic converter (merges and `json_content` lost; formats/office.py:532-546); PDFs raise instead.
6. **`extra["judge"]` (incl. `cost_usd`) is replayed verbatim from `cache_dir`**, while `token_usage` is renamed `token_usage_cached` (loader.py:1344-1358).
7. `create_ocr_cache("redis")` without `redis_url` silently returns a MemoryOCRCache (WARNING only).
8. `ProcessedDocument.text` strips `#+ ` anywhere (`C# is` → `Cis`); `tables` and `sections` fields are dead (always None).
