"""Tests for OCR cache behavior."""

import fnmatch
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from doc2mark import RedisOCRCache as ExportedRedisOCRCache
from doc2mark import create_ocr_cache as exported_create_ocr_cache
from doc2mark.ocr import RedisOCRCache as OCRPackageRedisOCRCache
from doc2mark.ocr import create_ocr_cache as ocr_package_create_ocr_cache
from doc2mark.ocr.base import BaseOCR, OCRConfig, OCRResult
from doc2mark.ocr.cache import (
    CACHE_SCHEMA_VERSION,
    OCR_CACHE_VALUE_SCHEMA_VERSION,
    CachedOCR,
    MemoryOCRCache,
    NoOpOCRCache,
    RedisOCRCache,
    _deserialize_ocr_cache_entry,
    _serialize_ocr_cache_entry,
    build_ocr_cache_key,
    create_ocr_cache,
)


class FakeOCR(BaseOCR):
    def __init__(self, fail=False):
        super().__init__(api_key="fake-key", config=OCRConfig(language="en"))
        self.fail = fail
        self.calls = []
        self.model = "fake-model"
        self.temperature = 0
        self.max_tokens = 128
        self.prompt_template = "default"
        self.default_prompt = "Read the image"
        self.model_kwargs = {"top_p": 1.0}

    def batch_process_images(self, images, **kwargs):
        if self.fail:
            raise ValueError("provider failed")
        self.calls.append(list(images))
        language = kwargs.get("language", "none")
        return [
            OCRResult(
                text=f"{image.decode('utf-8')}:{language}",
                metadata={"size": len(image)},
            )
            for image in images
        ]

    def validate_api_key(self):
        return True

    def get_configuration_summary(self):
        return {"provider": "FakeOCR", "model": self.model}


class FakeWatchError(Exception):
    pass


class FakeRedisPipeline:
    def __init__(self, client):
        self.client = client
        self.commands = []
        self.in_multi = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.reset()
        return False

    def watch(self, key):
        self.watched_key = key

    def unwatch(self):
        self.watched_key = None

    def get(self, key):
        return self.client.get(key)

    def multi(self):
        self.in_multi = True

    def set(self, key, value, ex=None):
        if self.in_multi:
            self.commands.append(("set", key, value, ex))
            return None
        return self.client.set(key, value, ex=ex)

    def delete(self, *keys):
        if self.in_multi:
            self.commands.append(("delete", keys))
            return None
        return self.client.delete(*keys)

    def execute(self):
        results = []
        for command in self.commands:
            if command[0] == "set":
                _, key, value, ex = command
                results.append(self.client.set(key, value, ex=ex))
            elif command[0] == "delete":
                _, keys = command
                results.append(self.client.delete(*keys))
        self.reset()
        return results

    def reset(self):
        self.commands = []
        self.in_multi = False


class FakeRedisClient:
    def __init__(self):
        self.store = {}
        self.ping_called = False
        self.set_calls = []
        self.deleted_keys = []
        self.raise_on = set()

    def ping(self):
        if "ping" in self.raise_on:
            raise RuntimeError("ping failed")
        self.ping_called = True
        return True

    def get(self, key):
        if "get" in self.raise_on:
            raise RuntimeError("get failed")
        return self.store.get(key)

    def set(self, key, value, ex=None):
        if "set" in self.raise_on:
            raise RuntimeError("set failed")
        self.store[key] = value
        self.set_calls.append((key, value, ex))
        return True

    def delete(self, *keys):
        if "delete" in self.raise_on:
            raise RuntimeError("delete failed")
        deleted = 0
        for key in keys:
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            if key in self.store:
                deleted += 1
                self.deleted_keys.append(key)
                del self.store[key]
        return deleted

    def scan(self, cursor=0, match=None, count=None):
        if "scan" in self.raise_on:
            raise RuntimeError("scan failed")
        keys = [key for key in self.store if match is None or fnmatch.fnmatch(key, match)]
        return 0, keys

    def pipeline(self):
        return FakeRedisPipeline(self)


def install_fake_redis(monkeypatch, client=None, from_url_error=None):
    client = client or FakeRedisClient()

    def from_url(redis_url):
        if from_url_error is not None:
            raise from_url_error
        client.redis_url = redis_url
        return client

    fake_module = SimpleNamespace(
        from_url=from_url,
        WatchError=FakeWatchError,
        exceptions=SimpleNamespace(WatchError=FakeWatchError),
    )
    monkeypatch.setitem(sys.modules, "redis", fake_module)
    return client


def assert_common_stats(stats, backend):
    assert stats["backend"] == backend
    for key in (
        "hits",
        "misses",
        "sets",
        "refreshes",
        "refresh_skipped",
        "expired",
        "evictions",
        "deletes",
        "errors",
        "entries",
        "ttl_seconds",
        "max_age_seconds",
        "max_refreshes",
    ):
        assert key in stats


def test_memory_cache_hit_miss_and_clear():
    cache = MemoryOCRCache(ttl_seconds=60, max_entries=10)

    assert cache.get("missing") is None
    cache.set("key", OCRResult(text="cached"))

    assert cache.get("key").text == "cached"
    cache.clear()
    assert cache.get("key") is None

    stats = cache.stats()
    assert_common_stats(stats, "memory")
    assert stats["hits"] == 1
    assert stats["misses"] == 2
    assert stats["entries"] == 0
    assert stats["deletes"] == 1


def test_memory_cache_ttl_refresh_respects_max_age():
    current = [1000.0]
    cache = MemoryOCRCache(
        ttl_seconds=10,
        max_age_seconds=25,
        max_entries=10,
        time_func=lambda: current[0],
    )

    cache.set("key", OCRResult(text="cached"))

    current[0] = 1009.0
    assert cache.get("key").text == "cached"

    current[0] = 1018.0
    assert cache.get("key").text == "cached"

    current[0] = 1025.0
    assert cache.get("key") is None
    assert cache.stats()["expired"] == 1


def test_memory_cache_max_refreshes_hits_without_extending_after_limit():
    current = [1000.0]
    cache = MemoryOCRCache(
        ttl_seconds=10,
        max_age_seconds=100,
        max_refreshes=1,
        time_func=lambda: current[0],
    )

    cache.set("key", OCRResult(text="cached"))

    current[0] = 1005.0
    assert cache.get("key").text == "cached"
    assert cache._entries["key"].expires_at == 1015.0
    assert cache._entries["key"].refresh_count == 1

    current[0] = 1012.0
    assert cache.get("key").text == "cached"
    assert cache._entries["key"].expires_at == 1015.0

    current[0] = 1016.0
    assert cache.get("key") is None

    stats = cache.stats()
    assert stats["hits"] == 2
    assert stats["refreshes"] == 1
    assert stats["refresh_skipped"] == 1
    assert stats["expired"] == 1


def test_memory_cache_max_entries_evicts_lru_item():
    cache = MemoryOCRCache(ttl_seconds=60, max_entries=2)
    cache.set("a", OCRResult(text="a"))
    cache.set("b", OCRResult(text="b"))

    assert cache.get("a").text == "a"
    cache.set("c", OCRResult(text="c"))

    assert cache.get("b") is None
    assert cache.get("a").text == "a"
    assert cache.get("c").text == "c"
    assert cache.stats()["evictions"] == 1


def test_noop_cache_stats_are_schema_compatible():
    cache = NoOpOCRCache()
    assert cache.get("missing") is None
    cache.set("key", OCRResult(text="ignored"))

    stats = cache.stats()
    assert_common_stats(stats, "noop")
    assert stats["entries"] == 0
    assert stats["ttl_seconds"] is None
    assert stats["max_age_seconds"] is None
    assert stats["max_refreshes"] is None


def test_serialization_round_trips_result_without_sensitive_metadata():
    payload = _serialize_ocr_cache_entry(
        OCRResult(
            text="cached",
            confidence=0.9,
            language="en",
            metadata={"api_key": "secret", "raw": b"image-bytes", "page": 3},
        ),
        created_at=1000.0,
        expires_at=1060.0,
        refresh_count=2,
    )

    assert "secret" not in payload
    assert "image-bytes" not in payload
    entry = _deserialize_ocr_cache_entry(payload)

    assert entry.result.text == "cached"
    assert entry.result.confidence == 0.9
    assert entry.result.language == "en"
    assert entry.result.metadata["page"] == 3
    assert "api_key" not in entry.result.metadata
    assert "bytes_sha256" in entry.result.metadata["raw"]
    assert entry.created_at == 1000.0
    assert entry.expires_at == 1060.0
    assert entry.refresh_count == 2


def test_deserialize_rejects_schema_mismatch_and_malformed_payload():
    with pytest.raises(ValueError):
        _deserialize_ocr_cache_entry("not-json")

    with pytest.raises(ValueError):
        _deserialize_ocr_cache_entry(
            {
                "schema": "old-schema",
                "result": {"text": "cached"},
                "created_at": 1000,
                "expires_at": 1060,
            }
        )


def test_cache_key_includes_provider_backend_identity():
    provider = FakeOCR()
    image = b"same-image"

    provider.base_url = "https://endpoint-a.example"
    endpoint_a_key = build_ocr_cache_key(provider, image)

    provider.base_url = "https://endpoint-b.example"
    endpoint_b_key = build_ocr_cache_key(provider, image)

    assert endpoint_a_key != endpoint_b_key

    provider.base_url = None
    provider.project = "project-a"
    provider.location = "global"
    project_a_key = build_ocr_cache_key(provider, image)

    provider.project = "project-b"
    provider.location = "asia-east1"
    project_b_key = build_ocr_cache_key(provider, image)

    assert project_a_key != project_b_key


def test_cache_key_schema_and_api_key_identity_are_credential_scoped():
    provider = FakeOCR()
    image = b"same-image"

    provider.api_key = "tenant-a-secret"
    tenant_a_key = build_ocr_cache_key(provider, image)

    provider.api_key = "tenant-b-secret"
    tenant_b_key = build_ocr_cache_key(provider, image)

    assert CACHE_SCHEMA_VERSION == "ocr-cache-v3"
    assert tenant_a_key != tenant_b_key
    assert "tenant-a-secret" not in tenant_a_key
    assert "tenant-b-secret" not in tenant_b_key


def test_cache_key_rejects_address_based_unstable_values():
    class CustomClient:
        pass

    provider = FakeOCR()
    provider.model_kwargs = {"client": CustomClient()}

    with pytest.raises(TypeError, match="stable OCR cache key"):
        build_ocr_cache_key(provider, b"image")


def test_cache_value_serialization_does_not_embed_object_addresses():
    class CustomClient:
        pass

    payload = _serialize_ocr_cache_entry(
        OCRResult(text="cached", metadata={"client": CustomClient()}),
        created_at=1000,
        expires_at=1060,
    )

    assert "0x" not in payload
    entry = _deserialize_ocr_cache_entry(payload)
    assert entry.result.metadata["client"]["type"].endswith("CustomClient")


def test_redis_constructor_imports_lazily_and_pings(monkeypatch):
    client = install_fake_redis(monkeypatch)

    cache = RedisOCRCache("redis://localhost/0", key_prefix="test")

    assert cache.redis_url == "redis://localhost/0"
    assert client.redis_url == "redis://localhost/0"
    assert client.ping_called is True
    assert_common_stats(cache.stats(), "redis")


def test_redis_constructor_raises_when_package_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "redis", None)

    with pytest.raises(ImportError):
        RedisOCRCache("redis://localhost/0")


def test_redis_cache_set_get_refreshes_ttl(monkeypatch):
    current = [1000.0]
    client = install_fake_redis(monkeypatch)
    cache = RedisOCRCache(
        "redis://localhost/0",
        ttl_seconds=10,
        max_age_seconds=25,
        max_refreshes=2,
        key_prefix="test",
        time_func=lambda: current[0],
    )

    cache.set("key", OCRResult(text="cached"))
    assert client.set_calls[-1][0] == "test:key"
    assert client.set_calls[-1][2] == 10

    current[0] = 1005.0
    assert cache.get("key").text == "cached"

    entry = _deserialize_ocr_cache_entry(client.store["test:key"])
    assert entry.expires_at == 1015.0
    assert entry.refresh_count == 1

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["sets"] == 1
    assert stats["refreshes"] == 1
    assert stats["entries"] is None


def test_redis_cache_miss_and_expired_value_delete(monkeypatch):
    current = [1000.0]
    client = install_fake_redis(monkeypatch)
    cache = RedisOCRCache("redis://localhost/0", ttl_seconds=10, key_prefix="test", time_func=lambda: current[0])

    assert cache.get("missing") is None
    cache.set("key", OCRResult(text="cached"))

    current[0] = 1011.0
    assert cache.get("key") is None
    assert "test:key" not in client.store

    stats = cache.stats()
    assert stats["misses"] == 2
    assert stats["expired"] == 1
    assert stats["deletes"] == 1


def test_redis_cache_max_refreshes_returns_hit_without_extending(monkeypatch):
    current = [1000.0]
    client = install_fake_redis(monkeypatch)
    cache = RedisOCRCache(
        "redis://localhost/0",
        ttl_seconds=10,
        max_age_seconds=100,
        max_refreshes=1,
        key_prefix="test",
        time_func=lambda: current[0],
    )

    cache.set("key", OCRResult(text="cached"))

    current[0] = 1005.0
    assert cache.get("key").text == "cached"
    assert _deserialize_ocr_cache_entry(client.store["test:key"]).expires_at == 1015.0

    current[0] = 1012.0
    assert cache.get("key").text == "cached"
    assert _deserialize_ocr_cache_entry(client.store["test:key"]).expires_at == 1015.0

    current[0] = 1016.0
    assert cache.get("key") is None

    stats = cache.stats()
    assert stats["hits"] == 2
    assert stats["refreshes"] == 1
    assert stats["refresh_skipped"] == 1
    assert stats["expired"] == 1


def test_redis_cache_refresh_respects_max_age_cap(monkeypatch):
    current = [1000.0]
    client = install_fake_redis(monkeypatch)
    cache = RedisOCRCache(
        "redis://localhost/0",
        ttl_seconds=10,
        max_age_seconds=12,
        key_prefix="test",
        time_func=lambda: current[0],
    )

    cache.set("key", OCRResult(text="cached"))

    current[0] = 1009.0
    assert cache.get("key").text == "cached"
    assert _deserialize_ocr_cache_entry(client.store["test:key"]).expires_at == 1012.0

    current[0] = 1012.0
    assert cache.get("key") is None
    assert cache.stats()["expired"] == 1


def test_redis_cache_deletes_malformed_value(monkeypatch):
    client = install_fake_redis(monkeypatch)
    cache = RedisOCRCache("redis://localhost/0", key_prefix="test")
    client.store["test:key"] = "not-json"

    assert cache.get("key") is None
    assert "test:key" not in client.store

    stats = cache.stats()
    assert stats["misses"] == 1
    assert stats["deletes"] == 1


def test_redis_runtime_errors_fail_open(monkeypatch):
    client = install_fake_redis(monkeypatch)
    cache = RedisOCRCache("redis://localhost/0", key_prefix="test")

    client.raise_on.add("get")
    assert cache.get("key") is None

    client.raise_on.remove("get")
    client.raise_on.add("set")
    cache.set("key", OCRResult(text="cached"))

    stats = cache.stats()
    assert stats["errors"] == 2
    assert stats["misses"] == 0
    assert stats["sets"] == 0


def test_redis_clear_scans_only_configured_prefix(monkeypatch):
    client = install_fake_redis(monkeypatch)
    cache = RedisOCRCache("redis://localhost/0", key_prefix="test")
    client.store["test:a"] = _serialize_ocr_cache_entry(
        OCRResult(text="a"),
        created_at=1000,
        expires_at=1060,
    )
    client.store["other:b"] = _serialize_ocr_cache_entry(
        OCRResult(text="b"),
        created_at=1000,
        expires_at=1060,
    )

    cache.clear()

    assert "test:a" not in client.store
    assert "other:b" in client.store
    assert cache.stats()["deletes"] == 1


def test_create_ocr_cache_provider_aliases(monkeypatch):
    assert create_ocr_cache(None) is None
    assert create_ocr_cache("none") is None
    assert isinstance(create_ocr_cache("noop"), NoOpOCRCache)
    assert isinstance(create_ocr_cache("memory", ttl_seconds=5), MemoryOCRCache)
    assert isinstance(create_ocr_cache("in-memory", ttl_seconds=5), MemoryOCRCache)

    install_fake_redis(monkeypatch)
    cache = create_ocr_cache("redis", redis_url="redis://localhost/0", key_prefix="test")
    assert isinstance(cache, RedisOCRCache)


def test_create_ocr_cache_redis_fallbacks(monkeypatch):
    install_fake_redis(monkeypatch, from_url_error=RuntimeError("connection failed"))

    memory_cache = create_ocr_cache(
        "redis",
        redis_url="redis://localhost/0",
        fallback="memory",
        ttl_seconds=5,
        max_entries=3,
    )
    assert isinstance(memory_cache, MemoryOCRCache)
    assert memory_cache.ttl_seconds == 5
    assert memory_cache.max_entries == 3

    assert create_ocr_cache("redis", redis_url="redis://localhost/0", fallback="none") is None

    with pytest.raises(RuntimeError, match="connection failed"):
        create_ocr_cache("redis", redis_url="redis://localhost/0", fallback="raise")


def test_create_ocr_cache_falls_back_when_redis_package_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "redis", None)

    cache = create_ocr_cache("redis", redis_url="redis://localhost/0", fallback="memory")

    assert isinstance(cache, MemoryOCRCache)


def test_cached_ocr_dedupes_duplicate_images_within_batch():
    provider = FakeOCR()
    cache = MemoryOCRCache(ttl_seconds=60)
    ocr = CachedOCR(provider, cache)

    results = ocr.batch_process_images(
        [b"same", b"same", b"other"],
        language="en",
    )

    assert [result.text for result in results] == ["same:en", "same:en", "other:en"]
    assert provider.calls == [[b"same", b"other"]]
    assert cache.stats()["sets"] == 2


def test_cached_ocr_reuses_result_across_batches():
    provider = FakeOCR()
    cache = MemoryOCRCache(ttl_seconds=60)
    ocr = CachedOCR(provider, cache)

    first = ocr.batch_process_images([b"same"], language="en")
    second = ocr.batch_process_images([b"same"], language="en")

    assert first[0].text == "same:en"
    assert second[0].text == "same:en"
    assert len(provider.calls) == 1
    assert cache.stats()["hits"] == 1


def test_cached_ocr_uses_call_kwargs_in_cache_key():
    provider = FakeOCR()
    cache = MemoryOCRCache(ttl_seconds=60)
    ocr = CachedOCR(provider, cache)

    english = ocr.batch_process_images([b"same"], language="en")
    chinese = ocr.batch_process_images([b"same"], language="zh")

    assert english[0].text == "same:en"
    assert chinese[0].text == "same:zh"
    assert len(provider.calls) == 2
    assert cache.stats()["sets"] == 2


def test_cached_ocr_does_not_cache_provider_failures():
    provider = FakeOCR(fail=True)
    cache = MemoryOCRCache(ttl_seconds=60)
    ocr = CachedOCR(provider, cache)

    with pytest.raises(ValueError, match="provider failed"):
        ocr.batch_process_images([b"same"], language="en")

    assert cache.stats()["entries"] == 0
    assert cache.get("anything") is None


def test_cached_ocr_caches_aligned_prefix_on_count_mismatch():
    class ShortResultOCR(FakeOCR):
        def batch_process_images(self, images, **kwargs):
            self.calls.append(list(images))
            return [OCRResult(text="first")]

    provider = ShortResultOCR()
    cache = MemoryOCRCache(ttl_seconds=60)
    ocr = CachedOCR(provider, cache)

    with pytest.raises(RuntimeError, match="different number of results"):
        ocr.batch_process_images([b"first", b"second"], language="en")

    first_key = build_ocr_cache_key(provider, b"first", kwargs={"language": "en"})
    second_key = build_ocr_cache_key(provider, b"second", kwargs={"language": "en"})
    assert cache.get(first_key).text == "first"
    assert cache.get(second_key) is None


def test_cached_ocr_getattr_does_not_recurse_without_wrapped():
    ocr = CachedOCR.__new__(CachedOCR)

    with pytest.raises(AttributeError):
        getattr(ocr, "wrapped")

    with pytest.raises(AttributeError):
        getattr(ocr, "model")


def test_cached_ocr_process_image_uses_cache():
    provider = FakeOCR()
    cache = MemoryOCRCache(ttl_seconds=60)
    ocr = CachedOCR(provider, cache)

    first = ocr.process_image(b"single", language="en")
    second = ocr.process_image(b"single", language="en")

    assert first.text == "single:en"
    assert second.text == "single:en"
    assert len(provider.calls) == 1


def test_memory_cache_stores_results_without_image_bytes():
    cache = MemoryOCRCache(ttl_seconds=60)
    cache.set("key", OCRResult(text="cached", metadata={"note": "no bytes"}))

    entry = cache._entries["key"]
    assert entry.result.text == "cached"
    assert not hasattr(entry, "image")
    assert not hasattr(entry.result, "image")


def test_public_exports_are_available_without_instantiating_redis(monkeypatch):
    monkeypatch.setitem(sys.modules, "redis", None)

    assert ExportedRedisOCRCache is RedisOCRCache
    assert OCRPackageRedisOCRCache is RedisOCRCache
    assert exported_create_ocr_cache is create_ocr_cache
    assert ocr_package_create_ocr_cache is create_ocr_cache


def test_packaging_declares_redis_extra():
    project_root = Path(__file__).resolve().parents[1]
    pyproject_text = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    setup_text = (project_root / "setup.py").read_text(encoding="utf-8")

    assert "redis = [" in pyproject_text
    assert '"redis>=5.0.0"' in pyproject_text
    assert '"doc2mark[ocr,heif,mime,vertex_ai,redis]"' in pyproject_text

    assert '"redis": [' in setup_text
    assert '"redis>=5.0.0",' in setup_text
    assert '"all": [' in setup_text


def test_value_schema_version_is_private_value_schema():
    assert OCR_CACHE_VALUE_SCHEMA_VERSION == "ocr-cache-value-v1"
