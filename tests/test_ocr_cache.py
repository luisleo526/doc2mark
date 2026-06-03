"""Tests for request-scoped OCR cache behavior."""

import pytest

from doc2mark.ocr.base import BaseOCR, OCRConfig, OCRResult
from doc2mark.ocr.cache import CachedOCR, MemoryOCRCache


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


def test_memory_cache_hit_miss_and_clear():
    cache = MemoryOCRCache(ttl_seconds=60, max_entries=10)

    assert cache.get("missing") is None
    cache.set("key", OCRResult(text="cached"))

    assert cache.get("key").text == "cached"
    cache.clear()
    assert cache.get("key") is None

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 2
    assert stats["entries"] == 0


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
