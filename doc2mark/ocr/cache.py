"""Request-scoped OCR cache helpers."""

import copy
import hashlib
import json
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from doc2mark.ocr.base import BaseOCR, OCRResult


CACHE_SCHEMA_VERSION = "ocr-cache-v1"
_SENSITIVE_KEYS = {"api_key", "key", "secret", "password", "access_token", "refresh_token"}


def _copy_result(result: OCRResult) -> OCRResult:
    """Copy cached OCR results so callers cannot mutate shared cache state."""
    return copy.deepcopy(result)


def _normalize_result(result: Any) -> OCRResult:
    if isinstance(result, OCRResult):
        return result
    if hasattr(result, "text"):
        return OCRResult(
            text=result.text,
            confidence=getattr(result, "confidence", None),
            language=getattr(result, "language", None),
            metadata=getattr(result, "metadata", None),
        )
    return OCRResult(text=str(result))


def _stable_value(value: Any) -> Any:
    """Convert values into JSON-stable, non-secret cache key components."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {
            "bytes_sha256": hashlib.sha256(value).hexdigest(),
            "length": len(value),
        }
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _stable_value(asdict(value))
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str.lower() in _SENSITIVE_KEYS:
                continue
            normalized[key_str] = _stable_value(item)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, set):
        return sorted(_stable_value(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    return repr(value)


def _prompt_hash(prompt: Any) -> Optional[str]:
    if prompt is None:
        return None
    prompt_text = str(prompt)
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()


def build_ocr_cache_key(
    provider: Any,
    image: bytes,
    kwargs: Optional[Dict[str, Any]] = None,
    cache_version: str = CACHE_SCHEMA_VERSION,
) -> str:
    """Build a stable cache key for an OCR request."""
    payload = {
        "schema": cache_version,
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "provider": f"{provider.__class__.__module__}.{provider.__class__.__qualname__}",
        "config": _stable_value(getattr(provider, "config", None)),
        "model": _stable_value(getattr(provider, "model", None)),
        "temperature": _stable_value(getattr(provider, "temperature", None)),
        "max_tokens": _stable_value(getattr(provider, "max_tokens", None)),
        "prompt_template": _stable_value(getattr(provider, "prompt_template", None)),
        "default_prompt_sha256": _prompt_hash(getattr(provider, "default_prompt", None)),
        "model_kwargs": _stable_value(getattr(provider, "model_kwargs", None)),
        "call_kwargs": _stable_value(kwargs or {}),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class OCRCache(ABC):
    """Interface for OCR result caches."""

    @abstractmethod
    def get(self, key: str) -> Optional[OCRResult]:
        """Return a cached OCR result, or None on miss."""

    @abstractmethod
    def set(self, key: str, result: OCRResult, ttl_seconds: Optional[float] = None) -> None:
        """Store an OCR result."""

    @abstractmethod
    def cleanup(self) -> int:
        """Remove expired entries and return the number removed."""

    @abstractmethod
    def clear(self) -> None:
        """Clear all cached entries."""

    @abstractmethod
    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""


@dataclass
class _MemoryCacheEntry:
    result: OCRResult
    created_at: float
    expires_at: float
    hits: int = 0


class MemoryOCRCache(OCRCache):
    """Thread-safe in-memory OCR cache with TTL and max-age bounds."""

    def __init__(
        self,
        ttl_seconds: float = 3600,
        max_age_seconds: Optional[float] = 43200,
        max_entries: int = 1024,
        time_func: Optional[Callable[[], float]] = None,
    ):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_age_seconds is not None and max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive or None")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")

        self.ttl_seconds = ttl_seconds
        self.max_age_seconds = max_age_seconds
        self.max_entries = max_entries
        self._time = time_func or time.time
        self._entries: "OrderedDict[str, _MemoryCacheEntry]" = OrderedDict()
        self._lock = threading.RLock()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "evictions": 0,
            "expired": 0,
        }

    def get(self, key: str) -> Optional[OCRResult]:
        now = self._time()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._stats["misses"] += 1
                return None

            if self._is_expired(entry, now):
                del self._entries[key]
                self._stats["expired"] += 1
                self._stats["misses"] += 1
                return None

            entry.hits += 1
            self._stats["hits"] += 1
            entry.expires_at = self._refreshed_expiry(entry, now)
            self._entries.move_to_end(key)
            return _copy_result(entry.result)

    def set(self, key: str, result: OCRResult, ttl_seconds: Optional[float] = None) -> None:
        now = self._time()
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")

        with self._lock:
            self.cleanup()
            expires_at = now + ttl
            if self.max_age_seconds is not None:
                expires_at = min(expires_at, now + self.max_age_seconds)

            self._entries[key] = _MemoryCacheEntry(
                result=_copy_result(_normalize_result(result)),
                created_at=now,
                expires_at=expires_at,
            )
            self._entries.move_to_end(key)
            self._stats["sets"] += 1
            self._evict_over_limit()

    def cleanup(self) -> int:
        now = self._time()
        removed = 0
        with self._lock:
            expired_keys = [
                key for key, entry in self._entries.items()
                if self._is_expired(entry, now)
            ]
            for key in expired_keys:
                del self._entries[key]
                removed += 1
            if removed:
                self._stats["expired"] += removed
        return removed

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
            stats.update({
                "entries": len(self._entries),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl_seconds,
                "max_age_seconds": self.max_age_seconds,
            })
            return stats

    def _is_expired(self, entry: _MemoryCacheEntry, now: float) -> bool:
        if now >= entry.expires_at:
            return True
        if self.max_age_seconds is not None and now >= entry.created_at + self.max_age_seconds:
            return True
        return False

    def _refreshed_expiry(self, entry: _MemoryCacheEntry, now: float) -> float:
        expires_at = now + self.ttl_seconds
        if self.max_age_seconds is not None:
            expires_at = min(expires_at, entry.created_at + self.max_age_seconds)
        return expires_at

    def _evict_over_limit(self) -> None:
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
            self._stats["evictions"] += 1


class NoOpOCRCache(OCRCache):
    """Cache implementation that always misses."""

    def get(self, key: str) -> Optional[OCRResult]:
        return None

    def set(self, key: str, result: OCRResult, ttl_seconds: Optional[float] = None) -> None:
        return None

    def cleanup(self) -> int:
        return 0

    def clear(self) -> None:
        return None

    def stats(self) -> Dict[str, Any]:
        return {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "evictions": 0,
            "expired": 0,
            "entries": 0,
            "max_entries": 0,
        }


class CachedOCR(BaseOCR):
    """OCR provider wrapper that checks a cache before calling the provider."""

    def __init__(
        self,
        wrapped: BaseOCR,
        cache: Optional[OCRCache],
        cache_version: str = CACHE_SCHEMA_VERSION,
    ):
        object.__setattr__(self, "wrapped", wrapped)
        object.__setattr__(self, "cache", cache or NoOpOCRCache())
        object.__setattr__(self, "cache_version", cache_version)

    @property
    def api_key(self) -> Optional[str]:
        return getattr(self.wrapped, "api_key", None)

    @api_key.setter
    def api_key(self, value: Optional[str]) -> None:
        setattr(self.wrapped, "api_key", value)

    @property
    def config(self) -> Any:
        return getattr(self.wrapped, "config", None)

    @config.setter
    def config(self, value: Any) -> None:
        setattr(self.wrapped, "config", value)

    @property
    def provider_name(self) -> str:
        return getattr(self.wrapped, "provider_name", type(self.wrapped).__name__)

    @property
    def requires_api_key(self) -> bool:
        return getattr(self.wrapped, "requires_api_key", True)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.wrapped, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"wrapped", "cache", "cache_version"} or name.startswith("_"):
            object.__setattr__(self, name, value)
        elif hasattr(self.wrapped, name):
            setattr(self.wrapped, name, value)
        else:
            object.__setattr__(self, name, value)

    def validate_api_key(self) -> bool:
        return self.wrapped.validate_api_key()

    def get_configuration_summary(self) -> Dict[str, Any]:
        if hasattr(self.wrapped, "get_configuration_summary"):
            return self.wrapped.get_configuration_summary()
        return {
            "provider": type(self.wrapped).__name__,
            "api_key_configured": bool(getattr(self.wrapped, "api_key", None)),
            "config": _stable_value(getattr(self.wrapped, "config", None)),
        }

    def preprocess_image(self, image_data: bytes) -> bytes:
        return self.wrapped.preprocess_image(image_data)

    def process_image(self, image: bytes, **kwargs) -> OCRResult:
        results = self.batch_process_images([image], **kwargs)
        return results[0] if results else OCRResult(text="")

    def batch_process_images(self, images: List[bytes], **kwargs) -> List[OCRResult]:
        if not images:
            return []

        results: List[Optional[OCRResult]] = [None] * len(images)
        miss_images: List[bytes] = []
        miss_keys: List[str] = []
        miss_positions: Dict[str, List[int]] = {}

        for index, image in enumerate(images):
            key = build_ocr_cache_key(
                self.wrapped,
                image,
                kwargs=kwargs,
                cache_version=self.cache_version,
            )
            cached = self.cache.get(key)
            if cached is not None:
                results[index] = cached
                continue

            if key not in miss_positions:
                miss_positions[key] = []
                miss_keys.append(key)
                miss_images.append(image)
            miss_positions[key].append(index)

        if miss_images:
            provider_results = self.wrapped.batch_process_images(miss_images, **kwargs)
            if len(provider_results) != len(miss_images):
                raise RuntimeError(
                    "OCR provider returned a different number of results than requested"
                )

            for key, provider_result in zip(miss_keys, provider_results):
                normalized = _normalize_result(provider_result)
                self.cache.set(key, normalized)
                for position in miss_positions[key]:
                    results[position] = _copy_result(normalized)

        final_results: List[OCRResult] = []
        for result in results:
            if result is None:
                raise RuntimeError("OCR cache wrapper failed to populate all OCR results")
            final_results.append(_copy_result(result))
        return final_results
