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
