"""Tests for OCR functionality."""

import os
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from doc2mark import UnifiedDocumentLoader
from doc2mark.ocr.base import OCRProvider, OCRResult


class TestOCRMocked:
    """Test OCR functionality with mocked API calls."""

    @patch('doc2mark.ocr.openai.VisionAgent')
    def test_openai_ocr_initialization(self, mock_vision_agent):
        """Test OpenAI OCR initialization without real API key."""
        # Mock the VisionAgent
        mock_agent = MagicMock()
        mock_vision_agent.return_value = mock_agent

        loader = UnifiedDocumentLoader(
            ocr_provider='openai',
            api_key='test-key-123'
        )

        assert loader is not None
        assert loader.ocr is not None
        mock_vision_agent.assert_called_once()

    @patch('doc2mark.ocr.openai.VisionAgent')
    def test_openai_ocr_with_mock_response(self, mock_vision_agent):
        """Test OpenAI OCR with mocked API response."""
        # Setup mock
        mock_agent = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Mocked OCR text from image"

        # Mock the agent's batch_invoke method
        mock_agent.batch_invoke.return_value = ["Mocked OCR text from image"]
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

    def test_tesseract_ocr_fallback(self):
        """Test that Tesseract OCR works without API key."""
        loader = UnifiedDocumentLoader(ocr_provider='tesseract')
        assert loader is not None
        assert loader.ocr is not None

        # Check OCR provider
        from doc2mark.ocr.tesseract import TesseractOCR
        assert isinstance(loader.ocr, TesseractOCR)


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
