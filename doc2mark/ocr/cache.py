"""OCR cache helpers."""

import copy
import hashlib
import json
import logging
import math
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from doc2mark.ocr.base import BaseOCR, OCRResult


logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = "ocr-cache-v3"
OCR_CACHE_VALUE_SCHEMA_VERSION = "ocr-cache-value-v1"
DEFAULT_REDIS_KEY_PREFIX = f"doc2mark:ocr:{CACHE_SCHEMA_VERSION}"

_SENSITIVE_KEYS = {"api_key", "key", "secret", "password", "access_token", "refresh_token"}
_ADDRESS_REPR_PATTERN = re.compile(r"\bat 0x[0-9a-fA-F]+\b|0x[0-9a-fA-F]+")
_STAT_COUNTERS = (
    "hits",
    "misses",
    "sets",
    "refreshes",
    "refresh_skipped",
    "expired",
    "evictions",
    "deletes",
    "errors",
)


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


def _type_identity(value: Any) -> str:
    cls = value.__class__
    return f"{cls.__module__}.{cls.__qualname__}"


def _stable_value(value: Any, *, strict: bool = False) -> Any:
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
        return _stable_value(asdict(value), strict=strict)
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str.lower() in _SENSITIVE_KEYS:
                continue
            normalized[key_str] = _stable_value(item, strict=strict)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, set):
        return sorted(_stable_value(item, strict=strict) for item in value)
    if isinstance(value, (list, tuple)):
        return [_stable_value(item, strict=strict) for item in value]

    repr_value = repr(value)
    if _ADDRESS_REPR_PATTERN.search(repr_value):
        if strict:
            raise TypeError(
                f"Cannot build a stable OCR cache key from {_type_identity(value)}; "
                "provide JSON-stable model/config values instead."
            )
        return {"type": _type_identity(value)}
    return {"type": _type_identity(value), "repr": repr_value}


def _api_key_hash(provider: Any) -> Optional[str]:
    api_key = getattr(provider, "api_key", None)
    if not api_key:
        return None
    if isinstance(api_key, bytes):
        api_key_bytes = api_key
    else:
        api_key_bytes = str(api_key).encode("utf-8")
    return hashlib.sha256(api_key_bytes).hexdigest()


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
        "api_key_sha256": _api_key_hash(provider),
        "config": _stable_value(getattr(provider, "config", None), strict=True),
        "model": _stable_value(getattr(provider, "model", None), strict=True),
        "temperature": _stable_value(getattr(provider, "temperature", None), strict=True),
        "max_tokens": _stable_value(getattr(provider, "max_tokens", None), strict=True),
        "prompt_template": _stable_value(getattr(provider, "prompt_template", None), strict=True),
        "default_prompt_sha256": _prompt_hash(getattr(provider, "default_prompt", None)),
        "model_kwargs": _stable_value(getattr(provider, "model_kwargs", None), strict=True),
        "base_url": _stable_value(getattr(provider, "base_url", None), strict=True),
        "project": _stable_value(getattr(provider, "project", None), strict=True),
        "location": _stable_value(getattr(provider, "location", None), strict=True),
        "call_kwargs": _stable_value(kwargs or {}, strict=True),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class _SerializedCacheEntry:
    result: OCRResult
    created_at: float
    expires_at: float
    refresh_count: int = 0


def _serialize_ocr_cache_entry(
    result: OCRResult,
    *,
    created_at: float,
    expires_at: float,
    refresh_count: int = 0,
) -> str:
    """Serialize an OCR cache value without request source data or secrets."""
    normalized = _normalize_result(result)
    payload = {
        "schema": OCR_CACHE_VALUE_SCHEMA_VERSION,
        "result": {
            "text": normalized.text,
            "confidence": normalized.confidence,
            "language": normalized.language,
            "metadata": _stable_value(normalized.metadata),
        },
        "created_at": float(created_at),
        "expires_at": float(expires_at),
        "refresh_count": int(refresh_count),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _deserialize_ocr_cache_entry(payload: Any) -> _SerializedCacheEntry:
    """Deserialize an OCR cache value and validate its private value schema."""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Malformed OCR cache value") from exc
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("Malformed OCR cache value") from exc
    if not isinstance(payload, dict):
        raise ValueError("Malformed OCR cache value")
    if payload.get("schema") != OCR_CACHE_VALUE_SCHEMA_VERSION:
        raise ValueError("Unsupported OCR cache value schema")

    result_payload = payload.get("result")
    if not isinstance(result_payload, dict) or "text" not in result_payload:
        raise ValueError("Malformed OCR cache result")

    try:
        created_at = float(payload["created_at"])
        expires_at = float(payload["expires_at"])
        refresh_count = int(payload.get("refresh_count", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Malformed OCR cache metadata") from exc

    return _SerializedCacheEntry(
        result=OCRResult(
            text=str(result_payload["text"]),
            confidence=result_payload.get("confidence"),
            language=result_payload.get("language"),
            metadata=result_payload.get("metadata"),
        ),
        created_at=created_at,
        expires_at=expires_at,
        refresh_count=refresh_count,
    )


def _new_stats() -> Dict[str, int]:
    return {key: 0 for key in _STAT_COUNTERS}


def _validate_cache_bounds(
    ttl_seconds: float,
    max_age_seconds: Optional[float],
    max_refreshes: Optional[int],
) -> None:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    if max_age_seconds is not None and max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive or None")
    if max_refreshes is not None and max_refreshes < 0:
        raise ValueError("max_refreshes must be non-negative or None")


def _is_entry_expired(entry: Any, now: float, max_age_seconds: Optional[float]) -> bool:
    if now >= entry.expires_at:
        return True
    if max_age_seconds is not None and now >= entry.created_at + max_age_seconds:
        return True
    return False


def _can_refresh(entry: Any, max_refreshes: Optional[int]) -> bool:
    return max_refreshes is None or entry.refresh_count < max_refreshes


def _refreshed_expiry(
    entry: Any,
    now: float,
    ttl_seconds: float,
    max_age_seconds: Optional[float],
) -> float:
    expires_at = now + ttl_seconds
    if max_age_seconds is not None:
        expires_at = min(expires_at, entry.created_at + max_age_seconds)
    return expires_at


def _redis_expires_in(expires_at: float, now: float) -> int:
    return max(1, int(math.ceil(expires_at - now)))


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
    refresh_count: int = 0


class MemoryOCRCache(OCRCache):
    """Thread-safe in-memory OCR cache with TTL, max-age, and refresh bounds."""

    def __init__(
        self,
        ttl_seconds: float = 3600,
        max_age_seconds: Optional[float] = 43200,
        max_entries: int = 1024,
        max_refreshes: Optional[int] = 10,
        time_func: Optional[Callable[[], float]] = None,
    ):
        _validate_cache_bounds(ttl_seconds, max_age_seconds, max_refreshes)
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")

        self.ttl_seconds = ttl_seconds
        self.max_age_seconds = max_age_seconds
        self.max_entries = max_entries
        self.max_refreshes = max_refreshes
        self._time = time_func or time.time
        self._entries: "OrderedDict[str, _MemoryCacheEntry]" = OrderedDict()
        self._lock = threading.RLock()
        self._stats = _new_stats()

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
                self._stats["deletes"] += 1
                return None

            entry.hits += 1
            self._stats["hits"] += 1
            if _can_refresh(entry, self.max_refreshes):
                entry.refresh_count += 1
                entry.expires_at = self._refreshed_expiry(entry, now)
                self._stats["refreshes"] += 1
            else:
                self._stats["refresh_skipped"] += 1
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
            expired_keys = [key for key, entry in self._entries.items() if self._is_expired(entry, now)]
            for key in expired_keys:
                del self._entries[key]
                removed += 1
            if removed:
                self._stats["expired"] += removed
                self._stats["deletes"] += removed
        return removed

    def clear(self) -> None:
        with self._lock:
            removed = len(self._entries)
            self._entries.clear()
            self._stats["deletes"] += removed

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
            stats.update(
                {
                    "backend": "memory",
                    "entries": len(self._entries),
                    "max_entries": self.max_entries,
                    "ttl_seconds": self.ttl_seconds,
                    "max_age_seconds": self.max_age_seconds,
                    "max_refreshes": self.max_refreshes,
                }
            )
            return stats

    def _is_expired(self, entry: _MemoryCacheEntry, now: float) -> bool:
        return _is_entry_expired(entry, now, self.max_age_seconds)

    def _refreshed_expiry(self, entry: _MemoryCacheEntry, now: float) -> float:
        return _refreshed_expiry(entry, now, self.ttl_seconds, self.max_age_seconds)

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
        stats: Dict[str, Any] = _new_stats()
        stats.update(
            {
                "backend": "noop",
                "entries": 0,
                "max_entries": 0,
                "ttl_seconds": None,
                "max_age_seconds": None,
                "max_refreshes": None,
            }
        )
        return stats


class RedisOCRCache(OCRCache):
    """Redis-backed OCR cache.

    Redis is imported lazily so doc2mark remains importable without the
    optional redis extra. The constructor verifies the connection with ping().
    """

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: float = 3600,
        max_age_seconds: Optional[float] = 43200,
        max_refreshes: Optional[int] = 10,
        key_prefix: str = DEFAULT_REDIS_KEY_PREFIX,
        time_func: Optional[Callable[[], float]] = None,
    ):
        if not redis_url:
            raise ValueError("redis_url is required")
        _validate_cache_bounds(ttl_seconds, max_age_seconds, max_refreshes)

        try:
            import redis
        except ImportError as exc:
            raise ImportError("RedisOCRCache requires the 'redis' optional dependency") from exc

        self.redis_url = redis_url
        self.ttl_seconds = ttl_seconds
        self.max_age_seconds = max_age_seconds
        self.max_refreshes = max_refreshes
        self.key_prefix = key_prefix.rstrip(":")
        self._time = time_func or time.time
        self._redis = redis.from_url(redis_url)
        self._redis.ping()
        self._watch_error = self._resolve_watch_error(redis)
        self._lock = threading.RLock()
        self._stats = _new_stats()

    def get(self, key: str) -> Optional[OCRResult]:
        redis_key = self._redis_key(key)
        try:
            value = self._redis.get(redis_key)
        except Exception:
            self._increment("errors")
            return None

        if value is None:
            self._increment("misses")
            return None

        try:
            entry = _deserialize_ocr_cache_entry(value)
        except ValueError:
            self._increment("misses")
            self._delete_key(redis_key)
            return None

        now = self._time()
        if self._is_expired(entry, now):
            self._increment("misses")
            self._increment("expired")
            self._delete_key(redis_key)
            return None

        result = _copy_result(entry.result)
        self._increment("hits")
        if _can_refresh(entry, self.max_refreshes):
            try:
                refreshed = self._refresh_key(redis_key, entry, now)
            except Exception:
                self._increment("errors")
            else:
                self._increment("refreshes" if refreshed else "refresh_skipped")
        else:
            self._increment("refresh_skipped")
        return result

    def set(self, key: str, result: OCRResult, ttl_seconds: Optional[float] = None) -> None:
        now = self._time()
        ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds must be positive")

        expires_at = now + ttl
        if self.max_age_seconds is not None:
            expires_at = min(expires_at, now + self.max_age_seconds)
        value = _serialize_ocr_cache_entry(
            result,
            created_at=now,
            expires_at=expires_at,
            refresh_count=0,
        )

        try:
            self._redis.set(self._redis_key(key), value, ex=_redis_expires_in(expires_at, now))
        except Exception:
            self._increment("errors")
            return
        self._increment("sets")

    def cleanup(self) -> int:
        return 0

    def clear(self) -> None:
        pattern = f"{self.key_prefix}:*"
        cursor: Any = 0
        deleted = 0
        try:
            while True:
                cursor, keys = self._redis.scan(cursor=cursor, match=pattern, count=1000)
                if keys:
                    deleted += int(self._redis.delete(*keys) or 0)
                if cursor in (0, "0", b"0"):
                    break
        except Exception:
            self._increment("errors")
            return None
        if deleted:
            self._increment("deletes", deleted)
        return None

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
        stats.update(
            {
                "backend": "redis",
                "entries": None,
                "max_entries": None,
                "ttl_seconds": self.ttl_seconds,
                "max_age_seconds": self.max_age_seconds,
                "max_refreshes": self.max_refreshes,
                "key_prefix": self.key_prefix,
            }
        )
        return stats

    def _redis_key(self, key: str) -> str:
        return f"{self.key_prefix}:{key}"

    def _is_expired(self, entry: _SerializedCacheEntry, now: float) -> bool:
        return _is_entry_expired(entry, now, self.max_age_seconds)

    def _refresh_key(self, redis_key: str, entry: _SerializedCacheEntry, now: float) -> bool:
        if not hasattr(self._redis, "pipeline"):
            current_value = self._redis.get(redis_key)
            if current_value is None:
                return False
            try:
                current_entry = _deserialize_ocr_cache_entry(current_value)
            except ValueError:
                return False
            if self._is_expired(current_entry, now) or not _can_refresh(current_entry, self.max_refreshes):
                return False
            value, redis_ttl = self._refreshed_value(current_entry, now)
            self._redis.set(redis_key, value, ex=redis_ttl)
            return True

        pipe = self._redis.pipeline()
        if hasattr(pipe, "__enter__"):
            with pipe as active_pipe:
                return self._refresh_with_pipeline(active_pipe, redis_key, now)
        try:
            return self._refresh_with_pipeline(pipe, redis_key, now)
        finally:
            if hasattr(pipe, "reset"):
                pipe.reset()

    def _refresh_with_pipeline(self, pipe: Any, redis_key: str, now: float) -> bool:
        try:
            if hasattr(pipe, "watch"):
                pipe.watch(redis_key)
            current_value = pipe.get(redis_key) if hasattr(pipe, "get") else self._redis.get(redis_key)
            if current_value is None:
                if hasattr(pipe, "unwatch"):
                    pipe.unwatch()
                return False
            try:
                current_entry = _deserialize_ocr_cache_entry(current_value)
            except ValueError:
                if hasattr(pipe, "unwatch"):
                    pipe.unwatch()
                return False
            if self._is_expired(current_entry, now) or not _can_refresh(current_entry, self.max_refreshes):
                if hasattr(pipe, "unwatch"):
                    pipe.unwatch()
                return False
            value, redis_ttl = self._refreshed_value(current_entry, now)
            if hasattr(pipe, "multi"):
                pipe.multi()
            pipe.set(redis_key, value, ex=redis_ttl)
            if hasattr(pipe, "execute"):
                pipe.execute()
            return True
        except Exception as exc:
            if self._watch_error is not None and isinstance(exc, self._watch_error):
                return False
            raise
        finally:
            if hasattr(pipe, "reset"):
                pipe.reset()

    def _refreshed_value(self, entry: _SerializedCacheEntry, now: float) -> Tuple[str, int]:
        refreshed = _SerializedCacheEntry(
            result=entry.result,
            created_at=entry.created_at,
            expires_at=_refreshed_expiry(entry, now, self.ttl_seconds, self.max_age_seconds),
            refresh_count=entry.refresh_count + 1,
        )
        value = _serialize_ocr_cache_entry(
            refreshed.result,
            created_at=refreshed.created_at,
            expires_at=refreshed.expires_at,
            refresh_count=refreshed.refresh_count,
        )
        return value, _redis_expires_in(refreshed.expires_at, now)

    def _delete_key(self, redis_key: str) -> int:
        try:
            deleted = int(self._redis.delete(redis_key) or 0)
        except Exception:
            self._increment("errors")
            return 0
        if deleted:
            self._increment("deletes", deleted)
        return deleted

    def _increment(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._stats[key] += amount

    @staticmethod
    def _resolve_watch_error(redis_module: Any) -> Optional[type]:
        watch_error = getattr(redis_module, "WatchError", None)
        if watch_error is not None:
            return watch_error
        exceptions = getattr(redis_module, "exceptions", None)
        if exceptions is not None:
            return getattr(exceptions, "WatchError", None)
        return None


def create_ocr_cache(
    provider: Optional[str] = "none",
    *,
    redis_url: Optional[str] = None,
    fallback: str = "memory",
    ttl_seconds: float = 3600,
    max_age_seconds: Optional[float] = 43200,
    max_refreshes: Optional[int] = 10,
    max_entries: int = 1024,
    key_prefix: str = DEFAULT_REDIS_KEY_PREFIX,
) -> Optional[OCRCache]:
    """Create an OCR cache backend from a small provider name."""
    normalized = _normalize_cache_provider(provider)
    if normalized in {"none", "off", "false", "disabled", ""}:
        return None
    if normalized in {"noop", "no-op"}:
        return NoOpOCRCache()
    if normalized in {"memory", "in-memory", "in_memory"}:
        return MemoryOCRCache(
            ttl_seconds=ttl_seconds,
            max_age_seconds=max_age_seconds,
            max_entries=max_entries,
            max_refreshes=max_refreshes,
        )
    if normalized == "redis":
        try:
            return RedisOCRCache(
                redis_url=redis_url or "",
                ttl_seconds=ttl_seconds,
                max_age_seconds=max_age_seconds,
                max_refreshes=max_refreshes,
                key_prefix=key_prefix,
            )
        except Exception as exc:
            return _fallback_cache(
                exc,
                fallback=fallback,
                ttl_seconds=ttl_seconds,
                max_age_seconds=max_age_seconds,
                max_refreshes=max_refreshes,
                max_entries=max_entries,
            )
    raise ValueError(f"Unknown OCR cache provider: {provider}")


def _normalize_cache_provider(provider: Optional[str]) -> str:
    if provider is None:
        return "none"
    return str(provider).strip().lower()


def _fallback_cache(
    exc: Exception,
    *,
    fallback: str,
    ttl_seconds: float,
    max_age_seconds: Optional[float],
    max_refreshes: Optional[int],
    max_entries: int,
) -> Optional[OCRCache]:
    normalized = _normalize_cache_provider(fallback)
    if normalized in {"memory", "in-memory", "in_memory"}:
        logger.warning("Redis OCR cache unavailable; falling back to MemoryOCRCache: %s", exc)
        return MemoryOCRCache(
            ttl_seconds=ttl_seconds,
            max_age_seconds=max_age_seconds,
            max_entries=max_entries,
            max_refreshes=max_refreshes,
        )
    if normalized in {"none", "off", "false", "disabled", ""}:
        logger.warning("Redis OCR cache unavailable; disabling OCR cache: %s", exc)
        return None
    if normalized == "raise":
        raise exc
    raise ValueError(f"Unknown OCR cache fallback: {fallback}") from exc


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
        if name == "wrapped":
            raise AttributeError(name)
        wrapped = self.__dict__.get("wrapped")
        if wrapped is None:
            raise AttributeError(name)
        return getattr(wrapped, name)

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
                for key, provider_result in zip(miss_keys, provider_results):
                    normalized = _normalize_result(provider_result)
                    self.cache.set(key, normalized)
                    for position in miss_positions[key]:
                        results[position] = _copy_result(normalized)
                raise RuntimeError("OCR provider returned a different number of results than requested")

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
