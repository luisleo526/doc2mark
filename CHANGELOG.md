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
- **`OCR` facade.** New ergonomic entry point: `OCR("openai")` with `.read()`
  and `.read_one()` methods, replacing direct provider construction.
- **`Task` enum.** Replaces the eight free-form `PromptTemplate` variants with
  intent names (`auto`, `table`, `document`, `form`, `receipt`, `handwriting`,
  `code`). Supports per-call (`task=`) and per-image (`tasks=[]`) overrides.
- **`gemini` provider alias.** `OCR("gemini")` is now accepted alongside
  `"vertex_ai"`.
- **`detail="raw"` mode.** Skips the interpretation pass to save 10-30% output
  tokens while still returning structured `raw` extraction.

### Changed
- OCR cache schema bumped to v4 to store the new structured `document` field.
  Existing v3 cache entries are invalidated on first access (one-time re-OCR).
- Cache key no longer includes inert config fields, reducing spurious misses.

### Deprecated
- `OCRConfig` fields `enhance_image`, `detect_tables`, `detect_layout`,
  `timeout`, `max_retries`, and `extra` are inert for LLM providers and now
  emit a `DeprecationWarning` when set to non-default values. Use `task` and
  the structured output controls instead.

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
