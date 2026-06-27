# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
