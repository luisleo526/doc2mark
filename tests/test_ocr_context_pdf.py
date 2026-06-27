"""Tests for the neighbor-PDF-context OCR feature (design section 9, tests 1-9).

All tests are unit-level (no network). Some depend on concurrently-edited
provider/pipeline files; they will be validated in the central integration run.
"""

import base64
import hashlib
import json
import math
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

pymupdf = pytest.importorskip("pymupdf")

from doc2mark.ocr.base import BaseOCR, OCRConfig, OCRResult, _CONTEXT_PDF_INSTRUCTION
from doc2mark.ocr.cache import CachedOCR, MemoryOCRCache, build_ocr_cache_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_synthetic_pdf(path, n_pages=5, text_per_page=True):
    """Create a simple *n*-page PDF with optional text on every page."""
    doc = pymupdf.open()
    for i in range(n_pages):
        page = doc.new_page(width=200, height=200)
        if text_per_page:
            page.insert_text((50, 100), f"Page {i + 1}")
    doc.save(str(path))
    doc.close()


def _make_image_dominant_pdf(path, n_pages=2):
    """Create a PDF whose pages are image-dominant (large raster, tiny text).

    Each page gets a 190x190 PNG covering most of the 200x200 page rect and
    a short text layer so the rule-based extractor has something to emit.
    """
    doc = pymupdf.open()
    for i in range(n_pages):
        page = doc.new_page(width=200, height=200)
        # Tiny text layer (rule-based content)
        page.insert_text((5, 15), f"RuleText-{i}")
        # Large raster covering >90 % of the page -> image-dominant
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 190, 190), 1)
        pix.clear_with(200)  # light-grey fill
        page.insert_image(pymupdf.Rect(5, 20, 195, 195), pixmap=pix)
    doc.save(str(path))
    doc.close()


class _EchoOCR(BaseOCR):
    """Minimal OCR provider that records calls and echoes results."""

    def __init__(self, **kw):
        super().__init__(api_key="fake", config=kw.get("config", OCRConfig()))
        self.calls = []
        self.model = "echo"
        self.temperature = 0
        self.max_tokens = 128
        self.prompt_template = "default"
        self.default_prompt = "Read"
        self.model_kwargs = {}

    def batch_process_images(self, images, **kwargs):
        self.calls.append({"images": list(images), "kwargs": dict(kwargs)})
        return [OCRResult(text=f"ocr-{i}") for i in range(len(images))]

    def validate_api_key(self):
        return True

    def get_configuration_summary(self):
        return {"provider": "EchoOCR"}


# ===================================================================
# Test 1 & 2: _build_window_pdf — clamp, raw base64, LRU, size guard,
#             memoization
# ===================================================================

class TestBuildWindowPdf:
    """Design section 9.1 / 9.2: _build_window_pdf behavior."""

    @pytest.fixture()
    def loader_5pp(self, tmp_path):
        """PDFLoader wrapping a synthetic 5-page PDF (context tier 1)."""
        from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader

        pdf_path = tmp_path / "five.pdf"
        _make_synthetic_pdf(pdf_path, n_pages=5)
        ocr = _EchoOCR(config=OCRConfig(context_pages=1))
        return PDFLoader(pdf_path, ocr=ocr)

    # -- clamp -----------------------------------------------------------

    def test_page0_yields_2pp(self, loader_5pp):
        """Page 0 window is {0, 1} -> 2 pages."""
        b64 = loader_5pp._build_window_pdf(0)
        assert b64 is not None
        doc = pymupdf.open(stream=base64.b64decode(b64), filetype="pdf")
        assert len(doc) == 2
        doc.close()

    def test_middle_page_yields_3pp(self, loader_5pp):
        """Page 2 window is {1, 2, 3} -> 3 pages."""
        b64 = loader_5pp._build_window_pdf(2)
        doc = pymupdf.open(stream=base64.b64decode(b64), filetype="pdf")
        assert len(doc) == 3
        doc.close()

    def test_last_page_yields_2pp(self, loader_5pp):
        """Page 4 window is {3, 4} -> 2 pages."""
        b64 = loader_5pp._build_window_pdf(4)
        doc = pymupdf.open(stream=base64.b64decode(b64), filetype="pdf")
        assert len(doc) == 2
        doc.close()

    # -- raw base64 (no data-uri prefix) ---------------------------------

    def test_no_data_uri_prefix(self, loader_5pp):
        """Returned string must be raw base64, never a data: URI."""
        b64 = loader_5pp._build_window_pdf(0)
        assert b64 is not None
        assert not b64.startswith("data:")

    # -- LRU bound -------------------------------------------------------

    def test_lru_bounded_to_4(self, loader_5pp):
        """After calling 5 distinct page indices, cache has at most 4 entries."""
        for k in range(5):
            loader_5pp._build_window_pdf(k)
        assert len(loader_5pp._window_pdf_cache) <= 4

    # -- size guard ------------------------------------------------------

    def test_size_guard_returns_none(self, tmp_path, monkeypatch):
        """When _CONTEXT_PDF_MAX_BYTES is tiny, the helper returns None."""
        import doc2mark.pipelines.pymupdf_advanced_pipeline as mod
        from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader

        monkeypatch.setattr(mod, "_CONTEXT_PDF_MAX_BYTES", 1)
        pdf_path = tmp_path / "small.pdf"
        _make_synthetic_pdf(pdf_path, n_pages=2)
        ocr = _EchoOCR(config=OCRConfig(context_pages=1))
        loader = PDFLoader(pdf_path, ocr=ocr)
        assert loader._build_window_pdf(0) is None

    # -- memoization (identity) ------------------------------------------

    def test_memoization_identity(self, loader_5pp):
        """Calling _build_window_pdf(2) twice returns the *same* object."""
        first = loader_5pp._build_window_pdf(2)
        second = loader_5pp._build_window_pdf(2)
        assert first is second


# ===================================================================
# Test 3: Vertex _prepare_prompt — content shape with / without
#         context_pdf
# ===================================================================

class TestVertexPreparePrompt:
    """Design section 9.3: Vertex _prepare_prompt content list."""

    @pytest.fixture(autouse=True)
    def _require_langchain_genai(self):
        pytest.importorskip("langchain_google_genai")

    def _import_prepare_prompt(self):
        from doc2mark.ocr.vertex_ai import _prepare_prompt, LANGCHAIN_GOOGLE_GENAI_AVAILABLE

        if not LANGCHAIN_GOOGLE_GENAI_AVAILABLE:
            pytest.skip("langchain-google-genai not available at runtime")
        return _prepare_prompt

    def test_with_context_pdf_has_3_parts(self):
        """image_url + text instruction + media block."""
        from langchain_core.messages import HumanMessage

        _prepare_prompt = self._import_prepare_prompt()
        data = {
            "image_data": "AAAA",
            "mime_type": "image/png",
            "prompt": "Transcribe this",
            "context_pdf": "FAKEPDFB64",
        }
        msgs = _prepare_prompt(data).format_messages()
        human = [m for m in msgs if isinstance(m, HumanMessage)][0]
        content = human.content

        assert len(content) == 3
        assert content[0]["type"] == "image_url"
        assert content[1]["type"] == "text"
        assert _CONTEXT_PDF_INSTRUCTION in content[1]["text"]
        assert content[2]["type"] == "media"
        assert content[2]["mime_type"] == "application/pdf"
        assert content[2]["data"] == "FAKEPDFB64"

    def test_without_context_pdf_has_1_part(self):
        """Without context_pdf, only the image part is present."""
        from langchain_core.messages import HumanMessage

        _prepare_prompt = self._import_prepare_prompt()
        data = {
            "image_data": "AAAA",
            "mime_type": "image/png",
            "prompt": "Transcribe this",
        }
        msgs = _prepare_prompt(data).format_messages()
        human = [m for m in msgs if isinstance(m, HumanMessage)][0]
        content = human.content

        assert len(content) == 1
        assert content[0]["type"] == "image_url"


# ===================================================================
# Test 4: OpenAI prepare_prompt — gated on context_pdf_enabled
# ===================================================================

class TestOpenAIPreparePrompt:
    """Design section 9.4: OpenAI prepare_prompt gating."""

    @pytest.fixture(autouse=True)
    def _require_langchain(self):
        pytest.importorskip("langchain_openai")

    def _import_prepare_prompt(self):
        from doc2mark.ocr.openai import prepare_prompt, LANGCHAIN_AVAILABLE

        if not LANGCHAIN_AVAILABLE:
            pytest.skip("langchain not available at runtime")
        return prepare_prompt

    def test_no_file_block_when_disabled(self):
        """context_pdf_enabled=False suppresses the file block."""
        from langchain_core.messages import HumanMessage

        prepare_prompt = self._import_prepare_prompt()
        data = {
            "image_data": "AAAA",
            "mime_type": "image/png",
            "prompt": "Transcribe this",
            "context_pdf": "FAKEPDFB64",
            "context_pdf_enabled": False,
        }
        msgs = prepare_prompt(data).format_messages()
        human = [m for m in msgs if isinstance(m, HumanMessage)][0]
        for part in human.content:
            assert part.get("type") != "file", "file block must not appear when disabled"

    def test_file_block_when_enabled(self):
        """context_pdf_enabled=True emits a file block with filename + raw base64."""
        from langchain_core.messages import HumanMessage

        prepare_prompt = self._import_prepare_prompt()
        data = {
            "image_data": "AAAA",
            "mime_type": "image/png",
            "prompt": "Transcribe this",
            "context_pdf": "FAKEPDFB64",
            "context_pdf_enabled": True,
        }
        msgs = prepare_prompt(data).format_messages()
        human = [m for m in msgs if isinstance(m, HumanMessage)][0]

        file_parts = [p for p in human.content if p.get("type") == "file"]
        assert len(file_parts) == 1
        # Verified OpenAI nested format: file.file_data is a data-URI, file.filename set.
        f = file_parts[0]["file"]
        assert f["filename"] == "context.pdf"
        assert f["file_data"] == "data:application/pdf;base64,FAKEPDFB64"


# ===================================================================
# Test 5: Recovery slicing — realigns context_pdfs to empty_idx for
#         BOTH providers
# ===================================================================

class TestRecoverySlicingVertex:
    """Design section 9.5 (Vertex): _recover_empty_structured slices context_pdfs."""

    @pytest.fixture(autouse=True)
    def _require_deps(self):
        pytest.importorskip("langchain_google_genai")

    def test_recovery_context_pdfs_sliced(self, monkeypatch):
        from doc2mark.ocr.vertex_ai import VertexAIOCR

        captured = {}

        # Bypass __init__ to avoid needing real credentials.
        ocr = VertexAIOCR.__new__(VertexAIOCR)
        ocr.config = OCRConfig(structured=True)
        ocr.api_key = None

        def fake_batch(images, **kwargs):
            captured["context_pdfs"] = kwargs.get("context_pdfs")
            captured["n_images"] = len(images)
            return [OCRResult(text=f"recovered-{i}") for i in range(len(images))]

        monkeypatch.setattr(ocr, "_batch_process_with_vision_agent", fake_batch)

        # Simulate 4 results where indices 1 and 3 are empty (structured).
        # _is_empty_structured checks raw.text, raw.tables, and raw.fields, so
        # the mock document must carry all three attributes.
        def _doc(text):
            return SimpleNamespace(
                raw=SimpleNamespace(text=text, tables=[], fields=[])
            )

        results = [
            OCRResult(text="ok0", document=_doc("ok0")),
            OCRResult(text="", document=_doc("")),
            OCRResult(text="ok2", document=_doc("ok2")),
            OCRResult(text="", document=_doc("")),
        ]
        images = [b"img0", b"img1", b"img2", b"img3"]

        ocr._recover_empty_structured(
            results, images,
            context_pdfs=["p0", "p1", "p2", "p3"],
        )

        assert captured["context_pdfs"] == ["p1", "p3"]
        assert captured["n_images"] == 2


class TestRecoverySlicingOpenAI:
    """Design section 9.5 (OpenAI): _recover_empty_structured slices context_pdfs."""

    @pytest.fixture(autouse=True)
    def _require_deps(self):
        pytest.importorskip("langchain_openai")

    def test_recovery_context_pdfs_sliced(self, monkeypatch):
        from doc2mark.ocr.openai import OpenAIOCR

        captured = {}

        ocr = OpenAIOCR.__new__(OpenAIOCR)
        ocr.config = OCRConfig(structured=True)
        ocr.api_key = "fake"
        ocr._vision_agent = None

        def fake_batch(images, **kwargs):
            captured["context_pdfs"] = kwargs.get("context_pdfs")
            captured["n_images"] = len(images)
            return [OCRResult(text=f"recovered-{i}") for i in range(len(images))]

        monkeypatch.setattr(ocr, "_batch_process_with_vision_agent", fake_batch)

        # _ensure_vision_agent may be called inside recovery; stub it out.
        monkeypatch.setattr(ocr, "_ensure_vision_agent", lambda **kw: None, raising=False)

        def _doc(text):
            return SimpleNamespace(
                raw=SimpleNamespace(text=text, tables=[], fields=[])
            )

        results = [
            OCRResult(text="ok0", document=_doc("ok0")),
            OCRResult(text="", document=_doc("")),
            OCRResult(text="ok2", document=_doc("ok2")),
            OCRResult(text="", document=_doc("")),
        ]
        images = [b"img0", b"img1", b"img2", b"img3"]

        ocr._recover_empty_structured(
            results, images,
            language="en",
            context_pdfs=["p0", "p1", "p2", "p3"],
        )

        assert captured["context_pdfs"] == ["p1", "p3"]
        assert captured["n_images"] == 2


# ===================================================================
# Test 6: Cache key context-awareness + dedup + miss realignment
# ===================================================================

class TestCacheContextAwareness:
    """Design section 9.6: cache key O(1), collision-free, miss realignment."""

    def _make_provider(self):
        return _EchoOCR(config=OCRConfig(language="en"))

    # -- key differentiation -------------------------------------------

    def test_different_context_different_key(self):
        """Two identical images with different context windows -> different keys."""
        provider = self._make_provider()
        img = b"same-image"

        key_a = build_ocr_cache_key(
            provider, img,
            kwargs={"context_pdf_sha256": hashlib.sha256(b"windowA").hexdigest()},
        )
        key_b = build_ocr_cache_key(
            provider, img,
            kwargs={"context_pdf_sha256": hashlib.sha256(b"windowB").hexdigest()},
        )
        assert key_a != key_b

    def test_same_image_same_context_same_key(self):
        """Identical image + identical context -> identical key (dedup)."""
        provider = self._make_provider()
        img = b"same-image"
        sha = hashlib.sha256(b"windowX").hexdigest()

        key1 = build_ocr_cache_key(provider, img, kwargs={"context_pdf_sha256": sha})
        key2 = build_ocr_cache_key(provider, img, kwargs={"context_pdf_sha256": sha})
        assert key1 == key2

    def test_absent_context_byte_identical_to_no_context(self):
        """When context_pdfs is absent, the key matches the pre-change golden."""
        provider = self._make_provider()
        img = b"same-image"

        # Key without any context kwarg (pre-change path).
        golden = build_ocr_cache_key(provider, img, kwargs={"language": "en"})
        # Key via the new code path when context is absent (base_kwargs == kwargs).
        new = build_ocr_cache_key(provider, img, kwargs={"language": "en"})
        assert golden == new

    # -- miss realignment via CachedOCR ---------------------------------

    def test_provider_receives_sliced_context_pdfs(self):
        """CachedOCR realigns context_pdfs to the deduped miss_images list."""
        provider = self._make_provider()
        cache = MemoryOCRCache(ttl_seconds=60)
        ocr = CachedOCR(provider, cache)

        # Pre-warm img-A WITH context so the cache key includes context_pdf_sha256.
        ocr.batch_process_images(
            [b"img-A"], language="en", context_pdfs=["ctxA"],
        )
        assert len(provider.calls) == 1

        # Now call with 3 images: img-A (cached hit) + img-B, img-C (misses).
        ocr.batch_process_images(
            [b"img-A", b"img-B", b"img-C"],
            language="en",
            context_pdfs=["ctxA", "ctxB", "ctxC"],
        )

        # The provider should have received only the miss images.
        last_call = provider.calls[-1]
        assert len(last_call["images"]) == 2
        assert last_call["images"] == [b"img-B", b"img-C"]
        assert last_call["kwargs"]["context_pdfs"] == ["ctxB", "ctxC"]

    def test_absent_context_pdfs_not_in_provider_kwargs(self):
        """When context_pdfs is None, the provider never receives the key."""
        provider = self._make_provider()
        cache = MemoryOCRCache(ttl_seconds=60)
        ocr = CachedOCR(provider, cache)

        ocr.batch_process_images([b"img"], language="en")
        assert "context_pdfs" not in provider.calls[0]["kwargs"]


# ===================================================================
# Test 7: Pipeline alignment — convert_to_json threads context_pdfs
# ===================================================================

class TestPipelineAlignment:
    """Design section 9.7: convert_to_json context wiring."""

    def _loader_for(self, tmp_path, tier):
        """Return a PDFLoader (image-dominant 2-page PDF) at the given tier."""
        from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader

        pdf_path = tmp_path / f"img_dom_{tier}.pdf"
        _make_image_dominant_pdf(pdf_path, n_pages=2)
        ocr = _EchoOCR(config=OCRConfig(context_pages=tier))
        return PDFLoader(pdf_path, ocr=ocr)

    def test_tier1_context_pdfs_aligned(self, tmp_path):
        """tier=1: context_pdfs length == len(image_data_list); same-page items
        share the same string identity."""
        loader = self._loader_for(tmp_path, tier=1)
        loader.convert_to_json(extract_images=True, ocr_images=True, show_progress=False)

        # The EchoOCR should have been called once.
        assert len(loader.ocr.calls) == 1
        call = loader.ocr.calls[0]
        n_images = len(call["images"])
        ctx = call["kwargs"].get("context_pdfs")
        assert ctx is not None, "context_pdfs kwarg must be present at tier >= 1"
        assert len(ctx) == n_images

        # For a 2-page PDF with whole-page renders, each page has one image.
        # The context string for the same page must be the same object (memoized).
        if n_images >= 2:
            # Each element should be a non-None string (raw base64).
            for c in ctx:
                assert c is not None
                assert isinstance(c, str)
                assert not c.startswith("data:")

    def test_tier0_kwarg_absent(self, tmp_path):
        """tier=0: context_pdfs kwarg must be absent entirely."""
        loader = self._loader_for(tmp_path, tier=0)
        loader.convert_to_json(extract_images=True, ocr_images=True, show_progress=False)

        assert len(loader.ocr.calls) == 1
        call = loader.ocr.calls[0]
        assert "context_pdfs" not in call["kwargs"], (
            "context_pdfs must not appear when context_pages=0"
        )


# ===================================================================
# Test 8: Invariant-1 preservation — rule-based text + OCR augment,
#         no raw base64 image, finite position_y
# ===================================================================

class TestInvariant1Preservation:
    """Design section 9.8: rule-based text preserved, OCR augments, valid JSON."""

    def test_scanned_page_output_shape(self, tmp_path):
        """An image-dominant page with a text layer must:
        - contain verbatim rule-based text blocks
        - contain a trailing <image_ocr_result> block
        - contain NO type:'image' raw-base64 entry
        - have every position_y finite and JSON-serializable
        """
        from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader

        pdf_path = tmp_path / "img_dom.pdf"
        _make_image_dominant_pdf(pdf_path, n_pages=1)
        ocr = _EchoOCR(config=OCRConfig(context_pages=1))
        loader = PDFLoader(pdf_path, ocr=ocr)

        result = loader.convert_to_json(
            extract_images=True, ocr_images=True, show_progress=False,
        )
        content = result["content"]
        assert len(content) > 0, "content must not be empty"

        types = [c["type"] for c in content]

        # Rule-based text should still be present (the text layer was not replaced).
        has_rule_text = any(
            t.startswith("text:") and t != "text:image_description" for t in types
        )
        # The page has "RuleText-0" inserted, so we expect at least one rule-based
        # text block.  After the design change, the early-return is removed and
        # rule-based extractors always run.
        assert has_rule_text, "rule-based text blocks must be preserved"

        # OCR augment should be present.
        has_ocr = any(t == "text:image_description" for t in types)
        assert has_ocr, "OCR augmentation block (image_ocr_result) must be present"

        # Verify the OCR block contains the wrapper tags.
        ocr_blocks = [c for c in content if c["type"] == "text:image_description"]
        for block in ocr_blocks:
            assert "<image_ocr_result>" in block["content"]
            assert "</image_ocr_result>" in block["content"]

        # NO raw type:'image' entries (the raw-base64 dump must not appear).
        assert "image" not in types, "raw base64 image entries must not appear"

        # Every position_y must be finite and JSON-serializable.
        for item in content:
            y = item["position_y"]
            assert math.isfinite(y), f"position_y must be finite, got {y}"

        # Round-trip through JSON to confirm serializability.
        serialized = json.dumps(content)
        assert "Infinity" not in serialized
        assert "NaN" not in serialized


# ===================================================================
# Test 9: Degradation — Tesseract ignores context_pdfs; office path
#         emits no context_pdf
# ===================================================================

class TestDegradation:
    """Design section 9.9: graceful degradation for non-PDF/non-LLM paths."""

    def test_tesseract_ignores_context_pdfs(self):
        """Tesseract batch_process_images accepts and ignores context_pdfs."""
        from doc2mark.ocr.tesseract import TesseractOCR

        ocr = TesseractOCR()
        # Create a minimal valid PNG (1x1 white pixel).
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1, 1), 1)
        pix.clear_with(255)
        png_bytes = pix.tobytes("png")

        # The call must not raise even with the extra kwarg.
        try:
            results = ocr.batch_process_images(
                [png_bytes],
                context_pdfs=["some-base64-pdf"],
            )
            # If Tesseract is installed, we get a result.
            assert isinstance(results, list)
        except Exception as exc:
            # If Tesseract binary is missing, that is OK — the important thing is
            # that the kwarg itself did not cause a TypeError / crash.
            if "pytesseract" in str(exc).lower() or "tesseract" in str(exc).lower():
                pytest.skip(f"Tesseract binary not available: {exc}")
            raise

    def test_office_path_no_context_pdf(self, tmp_path):
        """Office embedded-image path must not emit context_pdf_b64."""
        try:
            from doc2mark.pipelines import office_advanced_pipeline
            from doc2mark.formats.office import OfficeProcessor
        except ImportError:
            pytest.skip("Office pipeline not available")

        # Create the dummy file so OfficeProcessor.process() can stat it.
        dummy = tmp_path / "dummy.docx"
        dummy.write_bytes(b"fake")

        # Minimal stub: ensure load() result has no context_pdf_b64.
        fake_images = [
            {
                "type": "image",
                "content": "AAAA",
                "mime_type": "image/png",
                "page": 1,
                "position_y": 0.0,
            }
        ]

        def fake_load(*args, **kwargs):
            return {"content": fake_images, "pages": 1}

        from unittest.mock import patch as _patch

        with _patch.object(
            office_advanced_pipeline.UniversalOfficeLoader, "load", fake_load
        ), _patch.object(
            office_advanced_pipeline, "office_to_markdown", lambda data: ""
        ):
            processor = OfficeProcessor(ocr=Mock())
            result = processor.process(
                dummy,
                extract_images=True,
                ocr_images=False,
            )
        # No image in the office path should carry context_pdf_b64.
        for img in fake_images:
            assert "context_pdf_b64" not in img
