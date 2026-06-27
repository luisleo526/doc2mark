# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Structured OCR output.** The OCR layer now returns an `OCRPage` (on
  `OCRResult.document`) with a hard boundary between `raw` (verbatim
  transcription, tables, key/value fields) and `interpretation` (summary,
  document type, key findings). Structured output is the default.
- **OCR table extraction with merged cells.** Each ``Table`` in the structured
  result now carries an ``html`` field; the model is guided to reproduce tables
  as clean HTML using ``colspan``/``rowspan`` for merged cells (which the flat
  ``headers``/``rows`` and markdown cannot represent). ``OCRPage.to_markdown()``
  prefers it.
- **Page-level OCR for image-dominant PDFs.** Scanned pages and slide decks
  exported as pictures (little/no text layer, images covering the page) are now
  rendered and OCR'd once per page instead of per embedded image — coherent
  per-page content, far fewer API calls, and decorative logos/icons skipped.
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
