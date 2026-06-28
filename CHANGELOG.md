# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
