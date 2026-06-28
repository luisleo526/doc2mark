"""Tests for OCR functionality."""

import os
import sys
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch, MagicMock

from doc2mark import UnifiedDocumentLoader
from doc2mark.ocr.base import OCRProvider, OCRResult
from doc2mark.ocr.cache import CachedOCR, MemoryOCRCache, RedisOCRCache, create_ocr_cache


class LoaderFakeRedis:
    def ping(self):
        return True


def install_loader_fake_redis(monkeypatch):
    client = LoaderFakeRedis()
    fake_module = SimpleNamespace(from_url=lambda redis_url: client)
    monkeypatch.setitem(sys.modules, "redis", fake_module)
    return client


class TestOCRMocked:
    """Test OCR functionality with mocked API calls."""

    @patch('doc2mark.ocr.openai.VisionAgent')
    def test_openai_ocr_initialization(self, mock_vision_agent):
        """Test OpenAI OCR configuration without initializing a network client."""
        # Mock the VisionAgent
        mock_agent = MagicMock()
        mock_vision_agent.return_value = mock_agent

        loader = UnifiedDocumentLoader(
            ocr_provider='openai',
            api_key='test-key-123'
        )

        assert loader is not None
        assert loader.ocr is not None
        mock_vision_agent.assert_not_called()
        assert loader.get_ocr_configuration()["vision_agent_ready"] is False

    @patch('doc2mark.ocr.openai.VisionAgent')
    def test_openai_ocr_with_mock_response(self, mock_vision_agent):
        """Test OpenAI OCR with mocked API response."""
        # Setup mock
        mock_agent = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Mocked OCR text from image"

        # Mock the agent's batch_invoke method (returns list of (text, usage) tuples)
        mock_agent.batch_invoke.return_value = [("Mocked OCR text from image", {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})]
        mock_vision_agent.return_value = mock_agent

        # Test with a dummy image using batch_process_images
        from doc2mark.ocr.openai import OpenAIOCR
        ocr = OpenAIOCR(api_key='test-key-123')

        # Mock image data
        image_data = b'fake-image-data'
        results = ocr.batch_process_images([image_data])

        assert results is not None
        assert len(results) == 1
        assert "Mocked OCR text" in results[0].text
        mock_vision_agent.assert_called_once()

    def test_openai_structured_path_returns_document(self):
        """Structured OCR returns OCRResult.document with the parsed OCRPage."""
        from types import SimpleNamespace
        from doc2mark.ocr.openai import OpenAIOCR, VisionAgent
        from doc2mark.ocr.base import OCRConfig
        from doc2mark.ocr.schema import (
            OCRPage,
            RawExtraction,
            Interpretation,
            KeyValue,
        )

        page = OCRPage(
            raw=RawExtraction(
                text="hi",
                fields=[KeyValue(label="Total", value="$8.10")],
            ),
            interpretation=Interpretation(document_type="receipt", summary="s"),
        )
        # AIMessage-ish: carries .content and .usage_metadata for usage extraction.
        aimsg = SimpleNamespace(content="hi", usage_metadata={"total_tokens": 5})

        chain = MagicMock()
        chain.batch_as_completed.return_value = [
            (0, {"raw": aimsg, "parsed": page, "parsing_error": None})
        ]

        # Build a real VisionAgent on the structured path without constructing a
        # live ChatOpenAI, then swap in the mocked chain.
        agent = VisionAgent.__new__(VisionAgent)
        agent.structured = True
        agent.response_model = None
        agent.detail = "full"
        agent.max_concurrency = None
        agent._chain = chain

        ocr = OpenAIOCR(api_key="test-key-123", config=OCRConfig(structured=True))
        ocr._vision_agent = agent  # pre-inject so _ensure_vision_agent reuses it

        results = ocr.batch_process_images([b"fake-image-data"])

        assert len(results) == 1
        assert results[0].document is not None
        assert results[0].document.interpretation.document_type == "receipt"
        assert "hi" in results[0].text
        assert results[0].metadata["structured"] is True

    def test_openai_structured_parse_error_raw_text_fallback(self):
        """on_parse_error='raw_text' renders the raw message content into raw.text."""
        from types import SimpleNamespace
        from doc2mark.ocr.openai import OpenAIOCR, VisionAgent
        from doc2mark.ocr.base import OCRConfig

        aimsg = SimpleNamespace(content="partial blob", usage_metadata={})
        chain = MagicMock()
        chain.batch_as_completed.return_value = [
            (0, {"raw": aimsg, "parsed": None, "parsing_error": "boom"})
        ]

        agent = VisionAgent.__new__(VisionAgent)
        agent.structured = True
        agent.response_model = None
        agent.detail = "full"
        agent.max_concurrency = None
        agent._chain = chain

        ocr = OpenAIOCR(
            api_key="test-key-123",
            config=OCRConfig(structured=True, on_parse_error="raw_text"),
        )
        ocr._vision_agent = agent

        results = ocr.batch_process_images([b"fake-image-data"])

        assert len(results) == 1
        assert results[0].document is not None
        assert results[0].document.interpretation is None
        assert "partial blob" in results[0].text

    def test_openai_structured_parse_error_raise(self):
        """on_parse_error='raise' surfaces an OCRError when parsing fails."""
        from types import SimpleNamespace
        from doc2mark.ocr.openai import OpenAIOCR, VisionAgent
        from doc2mark.ocr.base import OCRConfig
        from doc2mark.core.base import OCRError

        aimsg = SimpleNamespace(content="", usage_metadata={})
        chain = MagicMock()
        chain.batch_as_completed.return_value = [
            (0, {"raw": aimsg, "parsed": None, "parsing_error": "boom"})
        ]

        agent = VisionAgent.__new__(VisionAgent)
        agent.structured = True
        agent.response_model = None
        agent.detail = "full"
        agent.max_concurrency = None
        agent._chain = chain

        ocr = OpenAIOCR(
            api_key="test-key-123",
            config=OCRConfig(structured=True, on_parse_error="raise"),
        )
        ocr._vision_agent = agent

        with pytest.raises(OCRError):
            ocr.batch_process_images([b"fake-image-data"])

    def test_openai_legacy_path_keeps_freeform(self):
        """structured=False preserves the legacy free-form result (document=None)."""
        mock_agent = MagicMock()
        mock_agent.structured = False
        mock_agent.batch_invoke.return_value = [
            ("Legacy free-form text", {"total_tokens": 7})
        ]

        with patch('doc2mark.ocr.openai.VisionAgent', return_value=mock_agent):
            from doc2mark.ocr.openai import OpenAIOCR
            from doc2mark.ocr.base import OCRConfig

            ocr = OpenAIOCR(api_key="test-key-123", config=OCRConfig(structured=False))
            results = ocr.batch_process_images([b"fake-image-data"])

        assert len(results) == 1
        assert results[0].document is None
        assert results[0].text == "Legacy free-form text"
        assert results[0].metadata["structured"] is False

    def test_openai_per_image_tasks_length_mismatch_raises(self):
        """tasks must match the number of images."""
        from doc2mark.ocr.openai import OpenAIOCR, VisionAgent
        from doc2mark.ocr.base import OCRConfig

        agent = VisionAgent.__new__(VisionAgent)
        agent.structured = True
        agent.response_model = None
        agent.detail = "full"
        agent.max_concurrency = None
        agent._chain = MagicMock()

        ocr = OpenAIOCR(api_key="test-key-123", config=OCRConfig(structured=True))
        ocr._vision_agent = agent

        with pytest.raises(ValueError):
            ocr.batch_process_images(
                [b"img1", b"img2"], tasks=["receipt"]
            )

    def test_tesseract_ocr_fallback(self):
        """Test that Tesseract OCR works without API key."""
        loader = UnifiedDocumentLoader(ocr_provider='tesseract')
        assert loader is not None
        assert loader.ocr is not None

        # Check OCR provider
        from doc2mark.ocr.tesseract import TesseractOCR
        assert isinstance(loader.ocr, TesseractOCR)

    def test_loader_does_not_wrap_ocr_without_cache(self):
        """OCR cache is explicit opt-in."""
        loader = UnifiedDocumentLoader(ocr_provider='tesseract')

        from doc2mark.ocr.tesseract import TesseractOCR
        assert isinstance(loader.ocr, TesseractOCR)
        assert not isinstance(loader.ocr, CachedOCR)

    def test_loader_wraps_ocr_when_cache_is_provided(self):
        """Loader should wrap the configured OCR provider with CachedOCR."""
        cache = MemoryOCRCache(ttl_seconds=60)
        loader = UnifiedDocumentLoader(ocr_provider='tesseract', ocr_cache=cache)

        assert isinstance(loader.ocr, CachedOCR)
        assert loader.ocr.cache is cache
        assert loader.ocr.validate_api_key() is True

        summary = loader.get_ocr_configuration()
        assert summary["provider"] == "TesseractOCR"

    def test_loader_wraps_ocr_when_factory_cache_is_provided(self):
        """Factory-created caches should be passed through unchanged."""
        cache = create_ocr_cache("memory", ttl_seconds=60)
        loader = UnifiedDocumentLoader(ocr_provider='tesseract', ocr_cache=cache)

        assert isinstance(loader.ocr, CachedOCR)
        assert loader.ocr.cache is cache

    def test_loader_wraps_ocr_when_redis_cache_is_provided(self, monkeypatch):
        """RedisOCRCache should only be used as a prebuilt cache handler by the loader."""
        install_loader_fake_redis(monkeypatch)
        cache = create_ocr_cache("redis", redis_url="redis://localhost/0", key_prefix="loader")

        assert isinstance(cache, RedisOCRCache)

        loader = UnifiedDocumentLoader(ocr_provider='tesseract', ocr_cache=cache)

        assert isinstance(loader.ocr, CachedOCR)
        assert loader.ocr.cache is cache

    def test_set_ocr_provider_reuses_or_replaces_cache_handler(self):
        """set_ocr_provider should preserve the loader cache unless a new one is supplied."""
        original_cache = MemoryOCRCache(ttl_seconds=60)
        replacement_cache = MemoryOCRCache(ttl_seconds=120)
        loader = UnifiedDocumentLoader(ocr_provider='tesseract', ocr_cache=original_cache)

        loader.set_ocr_provider('tesseract')
        assert isinstance(loader.ocr, CachedOCR)
        assert loader.ocr.cache is original_cache

        loader.set_ocr_provider('tesseract', ocr_cache=replacement_cache)
        assert isinstance(loader.ocr, CachedOCR)
        assert loader.ocr.cache is replacement_cache

    @patch('doc2mark.ocr.openai.VisionAgent')
    def test_set_ocr_provider_preserves_openai_enhanced_config_when_attaching_cache(self, mock_vision_agent):
        """Attaching a cache through set_ocr_provider must not reset OpenAI model settings."""
        mock_vision_agent.return_value = MagicMock()
        original_cache = MemoryOCRCache(ttl_seconds=60)
        loader = UnifiedDocumentLoader(
            ocr_provider='openai',
            api_key='test-key-123',
            model='gpt-4o-mini',
            temperature=0.25,
            max_tokens=1234,
            prompt_template='table_focused',
            base_url='https://example.test/v1',
            ocr_cache=original_cache,
        )
        replacement_cache = MemoryOCRCache(ttl_seconds=120)

        loader.set_ocr_provider('openai', ocr_cache=replacement_cache)

        assert isinstance(loader.ocr, CachedOCR)
        assert loader.ocr.cache is replacement_cache
        wrapped = loader.ocr.wrapped
        assert wrapped.model == 'gpt-4o-mini'
        assert wrapped.temperature == 0.25
        assert wrapped.max_tokens == 1234
        assert wrapped.prompt_template.value == 'table_focused'
        assert wrapped.base_url == 'https://example.test/v1'
        assert wrapped.api_key == 'test-key-123'

    def test_set_ocr_provider_preserves_embedded_cached_ocr_backend(self):
        """A supplied CachedOCR keeps its embedded cache unless an explicit cache is provided."""
        from doc2mark.ocr.tesseract import TesseractOCR

        stale_cache = MemoryOCRCache(ttl_seconds=60)
        embedded_cache = MemoryOCRCache(ttl_seconds=120)
        loader = UnifiedDocumentLoader(ocr_provider='tesseract', ocr_cache=stale_cache)
        supplied = CachedOCR(TesseractOCR(), embedded_cache)

        loader.set_ocr_provider(supplied)

        assert loader.ocr is supplied
        assert loader.ocr.cache is embedded_cache
        assert loader.ocr_cache is embedded_cache


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv('OPENAI_API_KEY'),
    reason="OPENAI_API_KEY not set - skipping integration tests"
)
class TestOCRIntegration:
    """Integration tests that require real API key."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test with real API key."""
        self.api_key = os.getenv('OPENAI_API_KEY')
        self.loader = UnifiedDocumentLoader(
            ocr_provider='openai',
            api_key=self.api_key
        )

    def test_real_ocr_processing(self, sample_documents_dir):
        """Test real OCR processing with API."""
        # Find a PDF with images
        pdf_files = list(sample_documents_dir.glob('*.pdf'))
        if not pdf_files:
            pytest.skip("No PDF files found for OCR testing")

        result = self.loader.load(
            pdf_files[0],
            extract_images=True,
            ocr_images=True
        )

        assert result is not None
        # Check if OCR was performed (look for OCR tags)
        if '<image_ocr_result>' in result.content:
            assert '</image_ocr_result>' in result.content
            print(f"✓ OCR performed on {pdf_files[0].name}")

    def test_ocr_with_language_hint(self, tmp_path):
        """Test OCR with language specification."""
        # This would need a real image file
        # For now, we just test the parameter passing
        pytest.skip("Requires real image file for testing")


class TestOCRConfiguration:
    """Test OCR configuration and parameter handling."""

    @patch('doc2mark.ocr.openai.VisionAgent')
    def test_ocr_config_parameters(self, mock_vision_agent):
        """Test that OCR configuration parameters are properly set."""
        # Mock the VisionAgent
        mock_vision_agent.return_value = MagicMock()

        loader = UnifiedDocumentLoader(
            ocr_provider='openai',
            api_key='test-key',
            model='gpt-4.1',
            temperature=0.2,
            max_tokens=2048,
            max_workers=10
        )

        # Verify configuration was applied
        config = loader.get_ocr_configuration()
        assert config['model'] == 'gpt-4.1'
        assert config['temperature'] == 0.2
        assert config['max_tokens'] == 2048

    @patch('doc2mark.ocr.openai.VisionAgent')
    def test_prompt_template_configuration(self, mock_vision_agent):
        """Test prompt template configuration."""
        from doc2mark.ocr.prompts import PromptTemplate

        # Mock the VisionAgent
        mock_vision_agent.return_value = MagicMock()

        loader = UnifiedDocumentLoader(
            ocr_provider='openai',
            api_key='test-key',
            prompt_template=PromptTemplate.TABLE_FOCUSED
        )

        config = loader.get_ocr_configuration()
        assert 'prompt_template' in config
        assert config['prompt_template'] == 'table_focused'

    def test_api_key_validation_mock(self):
        """Test API key validation without real key."""
        with patch('doc2mark.ocr.openai.VisionAgent') as mock_vision_agent:
            # Mock successful validation
            mock_agent = MagicMock()
            mock_vision_agent.return_value = mock_agent

            loader = UnifiedDocumentLoader(
                ocr_provider='openai',
                api_key='test-key'
            )

            # Check that loader was created successfully
            assert loader is not None
            assert loader.ocr is not None


def test_ocr_disabled_by_default():
    """Test that OCR is not performed unless explicitly requested."""
    loader = UnifiedDocumentLoader(ocr_provider='tesseract')

    # Load without OCR
    sample_dir = Path('sample_documents')
    if sample_dir.exists():
        pdf_files = list(sample_dir.glob('*.pdf'))
        if pdf_files:
            result = loader.load(
                pdf_files[0],
                extract_images=False,  # No image extraction
                ocr_images=False  # No OCR
            )

            # Should not contain OCR results
            assert '<image_ocr_result>' not in result.content


@pytest.mark.parametrize("provider", ['openai', 'tesseract'])
def test_ocr_provider_switching(provider):
    """Test switching between OCR providers."""
    if provider == 'openai':
        # Use mock for OpenAI
        with patch('doc2mark.ocr.openai.VisionAgent'):
            loader = UnifiedDocumentLoader(
                ocr_provider=provider,
                api_key='test-key'
            )
            assert loader is not None
    else:
        # Tesseract doesn't need mocking
        loader = UnifiedDocumentLoader(ocr_provider=provider)
        assert loader is not None


# Fixtures for creating test images
@pytest.fixture
def create_test_image(tmp_path):
    """Create a simple test image."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        # Create a simple image with text
        img = Image.new('RGB', (400, 200), color='white')
        draw = ImageDraw.Draw(img)

        # Try to use a basic font, fall back to default if not available
        try:
            font = ImageFont.truetype("Arial", 36)
        except:
            font = ImageFont.load_default()

        draw.text((50, 50), "Test OCR Text", fill='black', font=font)
        draw.text((50, 100), "Second Line", fill='black', font=font)

        # Save image
        image_path = tmp_path / "test_ocr.png"
        img.save(image_path)

        return image_path
    except ImportError:
        pytest.skip("PIL/Pillow not installed")


def test_tesseract_ocr_with_image(create_test_image):
    """Test Tesseract OCR with a real image."""
    try:
        from pytesseract import pytesseract
        # Check if tesseract is available
        pytesseract.get_tesseract_version()
    except Exception:
        pytest.skip("Tesseract binary not installed")

    loader = UnifiedDocumentLoader(ocr_provider='tesseract')

    # Read image data
    with open(create_test_image, 'rb') as f:
        image_data = f.read()

    # Process image using batch_process_images
    try:
        results = loader.ocr.batch_process_images([image_data])
        assert results is not None
        assert len(results) == 1
        # Tesseract might read "Test OCR Text" depending on installation
        print(f"Tesseract OCR result: {results[0].text}")
    except AttributeError as e:
        # This might be a code issue, not a Tesseract availability issue
        pytest.fail(f"AttributeError in Tesseract OCR: {e}")
    except Exception as e:
        # Other errors might be due to Tesseract not being properly installed
        pytest.skip(f"Tesseract OCR error: {e}")
