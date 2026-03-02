"""Tests for doc2mark.utils.image_utils — format detection and conversion."""

import io
import pytest
from PIL import Image

from doc2mark.utils.image_utils import (
    detect_image_format,
    convert_image_to_supported_format,
    normalize_image_to_png,
    get_mime_type,
    FORMAT_PNG,
    FORMAT_JPEG,
    FORMAT_GIF,
    FORMAT_WEBP,
    FORMAT_TIFF,
    FORMAT_BMP,
    FORMAT_ICO,
    FORMAT_EMF,
    FORMAT_WMF,
    FORMAT_UNKNOWN,
)


# ---------------------------------------------------------------------------
# Helpers to create minimal valid image bytes for each format
# ---------------------------------------------------------------------------

def _make_png_bytes(width=2, height=2):
    img = Image.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg_bytes(width=2, height=2):
    img = Image.new("RGB", (width, height), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_gif_bytes(width=2, height=2):
    img = Image.new("P", (width, height))
    buf = io.BytesIO()
    img.save(buf, format="GIF")
    return buf.getvalue()


def _make_webp_bytes(width=2, height=2):
    img = Image.new("RGB", (width, height), color="green")
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def _make_tiff_bytes(width=2, height=2):
    img = Image.new("RGB", (width, height), color="yellow")
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    return buf.getvalue()


def _make_bmp_bytes(width=2, height=2):
    img = Image.new("RGB", (width, height), color="cyan")
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def _make_emf_header():
    """Minimal bytes that match the EMF magic-byte pattern."""
    header = bytearray(48)
    header[0:4] = b'\x01\x00\x00\x00'
    header[40:44] = b' EMF'
    return bytes(header)


def _make_wmf_placeable_header():
    """Minimal bytes that match the WMF Aldus placeable header."""
    header = bytearray(24)
    header[0:4] = b'\xd7\xcd\xc6\x9a'
    return bytes(header)


# ---------------------------------------------------------------------------
# detect_image_format
# ---------------------------------------------------------------------------

class TestDetectImageFormat:

    def test_detect_png(self):
        assert detect_image_format(_make_png_bytes()) == FORMAT_PNG

    def test_detect_jpeg(self):
        assert detect_image_format(_make_jpeg_bytes()) == FORMAT_JPEG

    def test_detect_gif(self):
        assert detect_image_format(_make_gif_bytes()) == FORMAT_GIF

    def test_detect_webp(self):
        assert detect_image_format(_make_webp_bytes()) == FORMAT_WEBP

    def test_detect_tiff(self):
        assert detect_image_format(_make_tiff_bytes()) == FORMAT_TIFF

    def test_detect_bmp(self):
        assert detect_image_format(_make_bmp_bytes()) == FORMAT_BMP

    def test_detect_emf(self):
        assert detect_image_format(_make_emf_header()) == FORMAT_EMF

    def test_detect_wmf(self):
        assert detect_image_format(_make_wmf_placeable_header()) == FORMAT_WMF

    def test_detect_unknown_for_random_bytes(self):
        assert detect_image_format(b'\xDE\xAD\xBE\xEF' * 10) == FORMAT_UNKNOWN

    def test_detect_unknown_for_short_data(self):
        assert detect_image_format(b'\x00') == FORMAT_UNKNOWN

    def test_detect_ico(self):
        data = b'\x00\x00\x01\x00' + b'\x00' * 20
        assert detect_image_format(data) == FORMAT_ICO


# ---------------------------------------------------------------------------
# get_mime_type
# ---------------------------------------------------------------------------

class TestGetMimeType:

    @pytest.mark.parametrize("fmt, expected_mime", [
        (FORMAT_PNG, "image/png"),
        (FORMAT_JPEG, "image/jpeg"),
        (FORMAT_GIF, "image/gif"),
        (FORMAT_WEBP, "image/webp"),
        (FORMAT_TIFF, "image/tiff"),
        (FORMAT_BMP, "image/bmp"),
        (FORMAT_EMF, "image/emf"),
        (FORMAT_WMF, "image/wmf"),
    ])
    def test_known_formats(self, fmt, expected_mime):
        assert get_mime_type(fmt) == expected_mime

    def test_unknown_defaults_to_png(self):
        assert get_mime_type(FORMAT_UNKNOWN) == "image/png"
        assert get_mime_type("nonexistent") == "image/png"


# ---------------------------------------------------------------------------
# normalize_image_to_png
# ---------------------------------------------------------------------------

class TestNormalizeImageToPng:

    def _assert_valid_png(self, data: bytes):
        assert data[:8] == b'\x89PNG\r\n\x1a\n'
        img = Image.open(io.BytesIO(data))
        assert img.format == "PNG"

    def test_convert_jpeg_to_png(self):
        result = normalize_image_to_png(_make_jpeg_bytes())
        self._assert_valid_png(result)

    def test_convert_bmp_to_png(self):
        result = normalize_image_to_png(_make_bmp_bytes())
        self._assert_valid_png(result)

    def test_convert_tiff_to_png(self):
        result = normalize_image_to_png(_make_tiff_bytes())
        self._assert_valid_png(result)

    def test_convert_gif_to_png(self):
        result = normalize_image_to_png(_make_gif_bytes())
        self._assert_valid_png(result)

    def test_png_passthrough(self):
        original = _make_png_bytes()
        result = normalize_image_to_png(original)
        self._assert_valid_png(result)

    def test_cmyk_mode_conversion(self):
        img = Image.new("CMYK", (2, 2))
        buf = io.BytesIO()
        img.save(buf, format="TIFF")
        result = normalize_image_to_png(buf.getvalue())
        self._assert_valid_png(result)

    def test_invalid_data_raises(self):
        with pytest.raises(ValueError):
            normalize_image_to_png(b"not an image at all")


# ---------------------------------------------------------------------------
# convert_image_to_supported_format
# ---------------------------------------------------------------------------

class TestConvertImageToSupportedFormat:

    def test_png_passthrough(self):
        original = _make_png_bytes()
        result_bytes, mime = convert_image_to_supported_format(original)
        assert result_bytes == original
        assert mime == "image/png"

    def test_jpeg_passthrough(self):
        original = _make_jpeg_bytes()
        result_bytes, mime = convert_image_to_supported_format(original)
        assert result_bytes == original
        assert mime == "image/jpeg"

    def test_tiff_converted_to_png(self):
        """TIFF is not in the default supported set, so it should be converted."""
        original = _make_tiff_bytes()
        result_bytes, mime = convert_image_to_supported_format(original)
        assert mime == "image/png"
        assert result_bytes[:8] == b'\x89PNG\r\n\x1a\n'

    def test_bmp_converted_to_png(self):
        original = _make_bmp_bytes()
        result_bytes, mime = convert_image_to_supported_format(original)
        assert mime == "image/png"
        assert result_bytes[:8] == b'\x89PNG\r\n\x1a\n'

    def test_custom_supported_formats(self):
        """If we pass tiff as supported, it should NOT convert."""
        original = _make_tiff_bytes()
        result_bytes, mime = convert_image_to_supported_format(
            original, supported_formats={"tiff", "png"},
        )
        assert result_bytes == original
        assert mime == "image/tiff"

    def test_emf_returns_original_when_no_converter(self):
        """EMF conversion requires wand; without it, original bytes are returned."""
        emf_data = _make_emf_header()
        result_bytes, mime = convert_image_to_supported_format(emf_data)
        # Should return *something* (original or converted) without raising
        assert isinstance(result_bytes, bytes)
        assert isinstance(mime, str)


# ---------------------------------------------------------------------------
# Backward compatibility: openai.py wrapper still works
# ---------------------------------------------------------------------------

class TestOpenAIBackwardCompat:

    def test_openai_detect_image_format_exists(self):
        from doc2mark.ocr.openai import detect_image_format as oai_detect
        assert oai_detect(_make_png_bytes()) == "png"
        assert oai_detect(_make_jpeg_bytes()) == "jpeg"

    def test_openai_convert_image_exists(self):
        from doc2mark.ocr.openai import convert_image_to_supported_format as oai_convert
        result_bytes, mime = oai_convert(_make_png_bytes())
        assert mime == "image/png"
