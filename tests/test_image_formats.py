"""Tests for expanded image format support (Phase 2).

Covers:
- ImageProcessor.can_process() for new extensions
- ImageProcessor.process() for TIFF, BMP, GIF (Pillow-native)
- AVIF loading (Pillow >= 10.1)
- HEIC/HEIF loading (requires pillow-heif)
- DocumentFormat enum entries
- MimeTypeMapper mappings for new image types
- Loader registration of new image formats
"""

import io
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from doc2mark import UnifiedDocumentLoader
from doc2mark.core.base import DocumentFormat
from doc2mark.core.mime_mapper import MimeTypeMapper
from doc2mark.formats.image import ImageProcessor


# ---------------------------------------------------------------------------
# Helpers — create minimal image files on disk
# ---------------------------------------------------------------------------

def _save_image(tmp_dir: Path, name: str, pil_format: str, mode: str = "RGB") -> Path:
    img = Image.new(mode, (4, 4), color="red")
    path = tmp_dir / name
    img.save(path, format=pil_format)
    return path


@pytest.fixture()
def tmp_dir(tmp_path):
    return tmp_path


# ---------------------------------------------------------------------------
# DocumentFormat enum
# ---------------------------------------------------------------------------

class TestDocumentFormatEnum:

    @pytest.mark.parametrize("value", [
        "tiff", "tif", "bmp", "gif", "heic", "heif", "avif",
    ])
    def test_new_image_formats_exist(self, value):
        assert DocumentFormat(value).value == value


# ---------------------------------------------------------------------------
# MimeTypeMapper
# ---------------------------------------------------------------------------

class TestMimeMapperImageFormats:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.mapper = MimeTypeMapper()

    @pytest.mark.parametrize("mime, expected_fmt", [
        ("image/tiff", DocumentFormat.TIFF),
        ("image/bmp", DocumentFormat.BMP),
        ("image/x-ms-bmp", DocumentFormat.BMP),
        ("image/gif", DocumentFormat.GIF),
        ("image/heic", DocumentFormat.HEIC),
        ("image/heif", DocumentFormat.HEIF),
        ("image/avif", DocumentFormat.AVIF),
    ])
    def test_mime_to_format(self, mime, expected_fmt):
        assert self.mapper.get_format_from_mime(mime) == expected_fmt

    @pytest.mark.parametrize("fmt, expected_mime", [
        (DocumentFormat.TIFF, "image/tiff"),
        (DocumentFormat.BMP, "image/bmp"),
        (DocumentFormat.GIF, "image/gif"),
        (DocumentFormat.HEIC, "image/heic"),
        (DocumentFormat.HEIF, "image/heif"),
        (DocumentFormat.AVIF, "image/avif"),
    ])
    def test_format_to_mime(self, fmt, expected_mime):
        assert self.mapper.get_mime_from_format(fmt) == expected_mime

    def test_suggest_format_for_unknown_image_types(self):
        assert self.mapper.suggest_format("image/x-tiff-special") == DocumentFormat.TIFF
        assert self.mapper.suggest_format("image/x-bmp-custom") == DocumentFormat.BMP
        assert self.mapper.suggest_format("image/x-gif-anim") == DocumentFormat.GIF


# ---------------------------------------------------------------------------
# ImageProcessor.can_process
# ---------------------------------------------------------------------------

class TestImageProcessorCanProcess:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.processor = ImageProcessor()

    @pytest.mark.parametrize("ext", [
        "png", "jpg", "jpeg", "webp",
        "tiff", "tif", "bmp", "gif",
        "heic", "heif", "avif",
    ])
    def test_can_process_all_supported(self, ext, tmp_path):
        dummy = tmp_path / f"test.{ext}"
        dummy.write_bytes(b"dummy")
        assert self.processor.can_process(dummy) is True

    def test_cannot_process_unsupported(self, tmp_path):
        dummy = tmp_path / "test.svg"
        dummy.write_bytes(b"dummy")
        assert self.processor.can_process(dummy) is False


# ---------------------------------------------------------------------------
# ImageProcessor.process — Pillow-native formats
# ---------------------------------------------------------------------------

class TestImageProcessorProcess:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.processor = ImageProcessor()

    def test_process_tiff(self, tmp_dir):
        path = _save_image(tmp_dir, "test.tiff", "TIFF")
        result = self.processor.process(path)
        assert result is not None
        assert result.metadata.format == DocumentFormat.TIFF
        assert "TIFF" in result.content or "tiff" in result.content.lower()

    def test_process_bmp(self, tmp_dir):
        path = _save_image(tmp_dir, "test.bmp", "BMP")
        result = self.processor.process(path)
        assert result is not None
        assert result.metadata.format == DocumentFormat.BMP

    def test_process_gif(self, tmp_dir):
        path = _save_image(tmp_dir, "test.gif", "GIF", mode="P")
        result = self.processor.process(path)
        assert result is not None
        assert result.metadata.format == DocumentFormat.GIF


# ---------------------------------------------------------------------------
# Loader integration — new formats registered
# ---------------------------------------------------------------------------

class TestLoaderNewFormats:

    @pytest.fixture(autouse=True)
    def setup(self):
        self.loader = UnifiedDocumentLoader(ocr_provider='tesseract')

    def test_new_formats_in_supported_formats(self):
        supported = self.loader.supported_formats
        for ext in ["tiff", "tif", "bmp", "gif", "heic", "heif", "avif"]:
            assert ext in supported, f"{ext} not in supported_formats"

    def test_load_tiff_file(self, tmp_path):
        path = _save_image(tmp_path, "photo.tiff", "TIFF")
        result = self.loader.load(path)
        assert result is not None
        assert result.metadata.format == DocumentFormat.TIFF

    def test_load_bmp_file(self, tmp_path):
        path = _save_image(tmp_path, "photo.bmp", "BMP")
        result = self.loader.load(path)
        assert result is not None
        assert result.metadata.format == DocumentFormat.BMP

    def test_load_gif_file(self, tmp_path):
        path = _save_image(tmp_path, "photo.gif", "GIF", mode="P")
        result = self.loader.load(path)
        assert result is not None
        assert result.metadata.format == DocumentFormat.GIF


# ---------------------------------------------------------------------------
# HEIC / AVIF — skip if library not available
# ---------------------------------------------------------------------------

class TestHEICSupport:

    @pytest.fixture(autouse=True)
    def check_heif(self):
        try:
            import pillow_heif  # noqa: F401
        except ImportError:
            pytest.skip("pillow-heif not installed")

    def test_process_heic(self, tmp_path):
        """Verify that a HEIC file can be loaded (requires pillow-heif)."""
        import pillow_heif
        pillow_heif.register_heif_opener()

        img = Image.new("RGB", (4, 4), color="blue")
        path = tmp_path / "test.heic"
        img.save(path, format="HEIF")

        processor = ImageProcessor()
        result = processor.process(path)
        assert result is not None
        assert result.metadata.format == DocumentFormat.HEIC


class TestAVIFSupport:

    def test_process_avif(self, tmp_path):
        """Verify that an AVIF file can be loaded (Pillow >= 10.1)."""
        img = Image.new("RGB", (4, 4), color="green")
        path = tmp_path / "test.avif"
        try:
            img.save(path, format="AVIF")
        except Exception:
            pytest.skip("Pillow does not support AVIF on this platform")

        processor = ImageProcessor()
        result = processor.process(path)
        assert result is not None
        assert result.metadata.format == DocumentFormat.AVIF
