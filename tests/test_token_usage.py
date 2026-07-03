"""Document-level OCR token-usage aggregation.

Per-image OCR results carry token usage in ``OCRResult.metadata['token_usage']``
but the counts used to vanish inside the pipelines. These tests pin the new
behaviour: the loader sums usage across every OCR call made during one
``load()`` and stamps the total onto ``ProcessedDocument.metadata.extra
['token_usage']`` (keys ``input_tokens`` / ``output_tokens`` / ``total_tokens``,
the exact shape the teamsync backend consumes), while a no-OCR / usage-less load
is left byte-identical.
"""

import threading

import pytest

from doc2mark.ocr.base import BaseOCR, OCRResult
from doc2mark.ocr.cache import (
    FROM_CACHE_METADATA_KEY,
    CachedOCR,
    MemoryOCRCache,
    NoOpOCRCache,
    build_ocr_cache_key,
)
from doc2mark.ocr.usage import (
    UsageAggregatingOCR,
    merge_usage_into,
    new_usage_sink,
)


# --------------------------------------------------------------------------- #
# Fake providers                                                              #
# --------------------------------------------------------------------------- #
class _FakeUsageOCR(BaseOCR):
    """Provider stub returning one OCRResult per image, each with a fixed
    ``token_usage`` payload. Records how many images it was asked to OCR."""

    def __init__(self, usage_per_image, text="ocr text"):
        super().__init__()
        self.usage_per_image = usage_per_image
        self.text = text
        self.image_count = 0

    def batch_process_images(self, images, **kwargs):
        self.image_count += len(images)
        return [
            OCRResult(
                text=self.text,
                metadata={"model": "fake", "token_usage": dict(self.usage_per_image)},
            )
            for _ in images
        ]


class _NoUsageOCR(BaseOCR):
    """Provider stub with no token usage (mirrors Tesseract)."""

    def batch_process_images(self, images, **kwargs):
        return [OCRResult(text="plain", metadata={"model": "tesseract"}) for _ in images]


# --------------------------------------------------------------------------- #
# merge_usage_into — the summation primitive                                  #
# --------------------------------------------------------------------------- #
def test_merge_usage_langchain_shape():
    sink = new_usage_sink()
    merge_usage_into(sink, {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14})
    merge_usage_into(sink, {"input_tokens": 5, "output_tokens": 6, "total_tokens": 11})
    assert sink == {"input_tokens": 15, "output_tokens": 10, "total_tokens": 25}


def test_merge_usage_derives_total_when_absent():
    sink = new_usage_sink()
    merge_usage_into(sink, {"input_tokens": 8, "output_tokens": 3})
    assert sink == {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11}


def test_merge_usage_total_only_payload():
    # e.g. the structured path can report {"total_tokens": 7} with no split.
    sink = new_usage_sink()
    merge_usage_into(sink, {"total_tokens": 7})
    assert sink == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 7}


def test_merge_usage_openai_key_aliases():
    sink = new_usage_sink()
    merge_usage_into(sink, {"prompt_tokens": 4, "completion_tokens": 6})
    assert sink == {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10}


@pytest.mark.parametrize("usage", [None, {}, "not-a-dict", {"input_tokens": None}])
def test_merge_usage_ignores_missing_and_junk(usage):
    sink = new_usage_sink()
    merge_usage_into(sink, usage)
    assert sink == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


# --------------------------------------------------------------------------- #
# UsageAggregatingOCR — the wrapper                                           #
# --------------------------------------------------------------------------- #
def test_wrapper_accumulates_across_calls():
    ocr = UsageAggregatingOCR(_FakeUsageOCR({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}))
    ocr.begin_document_usage()
    ocr.batch_process_images([b"a", b"b"])          # 2 images
    ocr.process_image(b"c")                          # +1 image
    assert ocr.pop_document_usage() == {"input_tokens": 30, "output_tokens": 15, "total_tokens": 45}


def test_wrapper_returns_results_unchanged():
    inner = _FakeUsageOCR({"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}, text="hello")
    ocr = UsageAggregatingOCR(inner)
    ocr.begin_document_usage()
    results = ocr.batch_process_images([b"x"])
    assert len(results) == 1
    assert results[0].text == "hello"
    assert results[0].metadata["token_usage"] == {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}


def test_wrapper_pop_without_usage_returns_none():
    ocr = UsageAggregatingOCR(_NoUsageOCR())
    ocr.begin_document_usage()
    ocr.batch_process_images([b"a", b"b"])
    assert ocr.pop_document_usage() is None


def test_wrapper_records_nothing_before_begin():
    # No active sink => calls still succeed, nothing accumulates.
    ocr = UsageAggregatingOCR(_FakeUsageOCR({"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}))
    results = ocr.batch_process_images([b"a"])
    assert len(results) == 1
    assert ocr.pop_document_usage() is None


def test_wrapper_transparent_delegation():
    inner = _FakeUsageOCR({"total_tokens": 1})
    inner.api_key = "sekret"
    ocr = UsageAggregatingOCR(inner)
    # Attributes and identity flow through to the wrapped provider.
    assert ocr.api_key == "sekret"
    assert ocr.config is inner.config
    assert ocr.provider_name == inner.provider_name
    assert ocr.image_count == 0  # delegated attribute access


def test_wrapper_sink_is_thread_local():
    """Concurrent loads (loader batch processing) must not cross-contaminate."""
    ocr = UsageAggregatingOCR(_FakeUsageOCR({"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}))
    results = {}
    barrier = threading.Barrier(2)

    def worker(name, n_images):
        ocr.begin_document_usage()
        barrier.wait()  # interleave the two threads' work
        ocr.batch_process_images([b"i"] * n_images)
        results[name] = ocr.pop_document_usage()

    t1 = threading.Thread(target=worker, args=("a", 1))
    t2 = threading.Thread(target=worker, args=("b", 3))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert results["a"] == {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}
    assert results["b"] == {"input_tokens": 6, "output_tokens": 3, "total_tokens": 9}


# --------------------------------------------------------------------------- #
# Loader end-to-end — usage reaches ProcessedDocument.metadata.extra          #
# --------------------------------------------------------------------------- #
def _make_loader(ocr):
    from doc2mark.core.loader import UnifiedDocumentLoader

    return UnifiedDocumentLoader(ocr_provider=ocr)


def test_loader_keeps_public_ocr_raw_and_wraps_for_processors():
    inner = _FakeUsageOCR({"total_tokens": 1})
    loader = _make_loader(inner)
    # Public attribute stays the raw provider (contract relied on by other tests);
    # the aggregating wrapper is the private one handed to processors.
    assert loader.ocr is inner
    assert isinstance(loader._usage_ocr, UsageAggregatingOCR)
    assert loader._usage_ocr.wrapped is inner


def test_loader_disabled_ocr_has_no_wrapper():
    loader = _make_loader(None)
    assert loader.ocr is None
    assert loader._usage_ocr is None


@pytest.mark.parametrize(
    "usage,per_image_expected",
    [
        ({"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}, (11, 7, 18)),
        ({"total_tokens": 9}, (0, 0, 9)),                       # structured total-only
        ({"prompt_tokens": 4, "completion_tokens": 6}, (4, 6, 10)),  # openai aliases
    ],
)
def test_loader_pdf_image_strategy_aggregates(tmp_path, usage, per_image_expected):
    fitz = pytest.importorskip("pymupdf")
    Image = pytest.importorskip("PIL.Image")
    import io

    def _png(size, color):
        buf = io.BytesIO()
        Image.new("RGB", size, color).save(buf, format="PNG")
        return buf.getvalue()

    # Three full-page images, no text layer -> image strategy -> one OCR render/page.
    doc = fitz.open()
    for color in ("navy", "darkgreen", "maroon"):
        page = doc.new_page(width=600, height=800)
        page.insert_image(page.rect, stream=_png((1200, 1600), color))
    pdf_path = tmp_path / "image_doc.pdf"
    doc.save(str(pdf_path))
    doc.close()

    inner = _FakeUsageOCR(usage)
    loader = _make_loader(inner)
    result = loader.load(pdf_path, extract_images=True, ocr_images=True)

    assert inner.image_count == 3  # one whole-page render per page
    inp, out, tot = per_image_expected
    assert result.metadata.extra["token_usage"] == {
        "input_tokens": inp * 3,
        "output_tokens": out * 3,
        "total_tokens": tot * 3,
    }


def test_loader_image_file_merges_without_clobbering_extra(tmp_path):
    Image = pytest.importorskip("PIL.Image")

    img_path = tmp_path / "pic.png"
    Image.new("RGB", (64, 48), "white").save(str(img_path))

    inner = _FakeUsageOCR({"input_tokens": 20, "output_tokens": 8, "total_tokens": 28})
    loader = _make_loader(inner)
    result = loader.load(img_path, extract_images=True, ocr_images=True)

    extra = result.metadata.extra
    assert extra["token_usage"] == {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}
    # The image processor's own extra keys are preserved (merge, not overwrite).
    assert extra["has_ocr"] is True
    assert extra["width"] == 64 and extra["height"] == 48


def test_loader_no_ocr_leaves_extra_untouched(tmp_path):
    Image = pytest.importorskip("PIL.Image")

    img_path = tmp_path / "pic.png"
    Image.new("RGB", (10, 10), "white").save(str(img_path))

    inner = _FakeUsageOCR({"input_tokens": 5, "output_tokens": 5, "total_tokens": 10})
    loader = _make_loader(inner)
    result = loader.load(img_path, extract_images=True, ocr_images=False)

    assert inner.image_count == 0
    assert "token_usage" not in (result.metadata.extra or {})


def test_loader_usageless_provider_leaves_extra_untouched(tmp_path):
    Image = pytest.importorskip("PIL.Image")

    img_path = tmp_path / "pic.png"
    Image.new("RGB", (10, 10), "white").save(str(img_path))

    loader = _make_loader(_NoUsageOCR())
    result = loader.load(img_path, extract_images=True, ocr_images=True)

    # OCR ran but reported no tokens -> no noise stamped onto the document.
    assert "token_usage" not in (result.metadata.extra or {})


def test_loader_text_file_never_touches_usage(temp_text_file):
    loader = _make_loader(_FakeUsageOCR({"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}))
    result = loader.load(temp_text_file)
    assert "token_usage" not in (result.metadata.extra or {})


# --------------------------------------------------------------------------- #
# Fresh-spend-only semantic — count ONLY actual provider calls this load       #
#                                                                              #
# The consumer bills money from ProcessedDocument.metadata.extra['token_usage'],#
# so a result that is NOT fresh provider spend (a cache hit, an intra-batch     #
# dedup fan-out copy, or a whole-document cache replay) must never add to the   #
# billed count.                                                                 #
# --------------------------------------------------------------------------- #
def test_cache_hit_is_not_counted_as_fresh_usage():
    """FINDING 1: UsageAggregatingOCR(CachedOCR(...)) must not re-bill a cache hit."""
    inner = _FakeUsageOCR({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
    ocr = UsageAggregatingOCR(CachedOCR(inner, MemoryOCRCache(ttl_seconds=60)))

    # First pass over the image is a real provider call -> counted.
    ocr.begin_document_usage()
    ocr.batch_process_images([b"img"])
    assert ocr.pop_document_usage() == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert inner.image_count == 1

    # Second pass over the SAME image is served from cache -> zero fresh spend.
    ocr.begin_document_usage()
    ocr.batch_process_images([b"img"])
    assert ocr.pop_document_usage() is None
    assert inner.image_count == 1  # provider was not called again


def test_cache_hit_not_counted_via_process_image():
    """FINDING 1 (single-image path): process_image cache hits are also skipped."""
    inner = _FakeUsageOCR({"input_tokens": 3, "output_tokens": 2, "total_tokens": 5})
    ocr = UsageAggregatingOCR(CachedOCR(inner, MemoryOCRCache(ttl_seconds=60)))

    ocr.begin_document_usage()
    ocr.process_image(b"one")
    assert ocr.pop_document_usage() == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}

    ocr.begin_document_usage()
    ocr.process_image(b"one")
    assert ocr.pop_document_usage() is None
    assert inner.image_count == 1


def test_intra_batch_dedup_counts_single_provider_spend():
    """FINDING 2: duplicate images in one batch are one provider call -> counted once.

    Fires even with a no-op cache, because CachedOCR dedups identical images
    before calling the provider regardless of the cache backend.
    """
    inner = _FakeUsageOCR({"input_tokens": 4, "output_tokens": 2, "total_tokens": 6})
    ocr = UsageAggregatingOCR(CachedOCR(inner, NoOpOCRCache()))

    ocr.begin_document_usage()
    results = ocr.batch_process_images([b"dup", b"dup", b"dup"])

    assert len(results) == 3
    assert all(r.text == "ocr text" for r in results)
    assert inner.image_count == 1  # deduped to a single provider call
    # Usage counted once, not three times.
    assert ocr.pop_document_usage() == {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}


def test_dedup_fanout_marks_only_copies_and_never_leaks_into_cache():
    """FINDING 2 mechanics: the fresh position is unmarked, dedup copies are marked,
    and the value stored in the cache stays clean (no marker round-trips into storage)."""
    inner = _FakeUsageOCR({"input_tokens": 4, "output_tokens": 2, "total_tokens": 6})
    cache = MemoryOCRCache(ttl_seconds=60)
    cached = CachedOCR(inner, cache)

    results = cached.batch_process_images([b"dup", b"dup"])

    assert FROM_CACHE_METADATA_KEY not in (results[0].metadata or {})       # fresh spend
    assert results[1].metadata.get(FROM_CACHE_METADATA_KEY) is True         # dedup copy
    # The stored value is clean — cache.get() itself never adds the marker.
    stored = cache.get(build_ocr_cache_key(inner, b"dup"))
    assert FROM_CACHE_METADATA_KEY not in (stored.metadata or {})


def test_document_cache_replay_demotes_token_usage(tmp_path):
    """FINDING 3: a whole-document cache replay must not re-present billable usage."""
    Image = pytest.importorskip("PIL.Image")
    from doc2mark.core.loader import UnifiedDocumentLoader

    img_path = tmp_path / "pic.png"
    Image.new("RGB", (32, 24), "white").save(str(img_path))

    inner = _FakeUsageOCR({"input_tokens": 12, "output_tokens": 6, "total_tokens": 18})
    loader = UnifiedDocumentLoader(ocr_provider=inner, cache_dir=str(tmp_path / "doccache"))

    # First load actually runs OCR -> usage billed once under the canonical key.
    first = loader.load(img_path, extract_images=True, ocr_images=True)
    assert first.metadata.extra["token_usage"] == {"input_tokens": 12, "output_tokens": 6, "total_tokens": 18}
    assert inner.image_count == 1

    # Second load hits the on-disk document cache: NO OCR runs, so the billing key
    # must be absent; the original count is preserved for diagnostics only.
    second = loader.load(img_path, extract_images=True, ocr_images=True)
    assert inner.image_count == 1  # provider not called again (document cache hit)
    extra = second.metadata.extra or {}
    assert "token_usage" not in extra
    assert extra["token_usage_cached"] == {"input_tokens": 12, "output_tokens": 6, "total_tokens": 18}


def test_loader_with_cache_counts_fresh_load_then_zero_on_ocr_cache_hit(tmp_path):
    """End-to-end FINDING 1 through the loader: an OCR-cache hit on a re-load bills nothing.

    Two separate loads of the same image share an OCR cache (no document cache),
    so the second load re-runs the processor but the OCR call is a cache hit ->
    the stamped document carries no billable token_usage.
    """
    Image = pytest.importorskip("PIL.Image")
    from doc2mark.core.loader import UnifiedDocumentLoader

    img_path = tmp_path / "pic.png"
    Image.new("RGB", (48, 32), "white").save(str(img_path))

    inner = _FakeUsageOCR({"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})
    loader = UnifiedDocumentLoader(ocr_provider=inner, ocr_cache=MemoryOCRCache(ttl_seconds=300))

    first = loader.load(img_path, extract_images=True, ocr_images=True)
    assert first.metadata.extra["token_usage"] == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
    assert inner.image_count == 1

    second = loader.load(img_path, extract_images=True, ocr_images=True)
    assert inner.image_count == 1  # OCR served from cache, no fresh provider call
    assert "token_usage" not in (second.metadata.extra or {})
