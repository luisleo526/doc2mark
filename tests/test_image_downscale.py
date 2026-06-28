"""Tests for image downscaling feature in doc2mark.utils.image_utils."""

import io
import os

import pytest
from PIL import Image

from doc2mark.utils.image_utils import (
    convert_image_to_supported_format,
    downscale_image,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png_bytes(width, height, color="red"):
    """Create a PNG image of the given size."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_bmp_bytes(width, height, color="blue"):
    """Create a BMP image of the given size (needs conversion by the pipeline)."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


def _open_image(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


# ---------------------------------------------------------------------------
# downscale_image — direct unit tests
# ---------------------------------------------------------------------------

class TestDownscaleImage:

    def test_large_landscape_image_is_downscaled(self):
        """A 2000x1000 image with max_dim=500 becomes 500x250."""
        data = _make_png_bytes(2000, 1000)
        result = downscale_image(data, max_dim=500)
        img = _open_image(result)
        assert img.size == (500, 250)

    def test_large_portrait_image_is_downscaled(self):
        """A 1000x2000 image with max_dim=500 becomes 250x500."""
        data = _make_png_bytes(1000, 2000)
        result = downscale_image(data, max_dim=500)
        img = _open_image(result)
        assert img.size == (250, 500)

    def test_large_square_image_is_downscaled(self):
        """A 1200x1200 image with max_dim=600 becomes 600x600."""
        data = _make_png_bytes(1200, 1200)
        result = downscale_image(data, max_dim=600)
        img = _open_image(result)
        assert img.size == (600, 600)

    def test_small_image_returned_unchanged(self):
        """An image already within bounds is returned as the original bytes."""
        data = _make_png_bytes(100, 80)
        result = downscale_image(data, max_dim=500)
        assert result is data  # exact same object, not a copy

    def test_exact_max_dim_returned_unchanged(self):
        """An image whose longest side equals max_dim is not re-encoded."""
        data = _make_png_bytes(500, 300)
        result = downscale_image(data, max_dim=500)
        assert result is data

    def test_non_image_bytes_returned_as_is(self):
        """Random non-image bytes are returned unchanged."""
        garbage = b"this is definitely not an image file at all"
        result = downscale_image(garbage, max_dim=100)
        assert result is garbage

    def test_result_is_valid_png(self):
        """Downscaled output is a valid PNG."""
        data = _make_png_bytes(3000, 1500)
        result = downscale_image(data, max_dim=800)
        assert result[:8] == b'\x89PNG\r\n\x1a\n'
        img = _open_image(result)
        assert img.format == "PNG"

    def test_never_enlarges(self):
        """Even if max_dim is larger than the image, size stays the same."""
        data = _make_png_bytes(50, 30)
        result = downscale_image(data, max_dim=9999)
        assert result is data


# ---------------------------------------------------------------------------
# Environment variable gating via convert_image_to_supported_format
# ---------------------------------------------------------------------------

class TestEnvVarGating:

    def test_no_env_var_no_downscale(self):
        """Without env var or max_dim, large images are not downscaled."""
        data = _make_png_bytes(2000, 1000)
        result_bytes, mime = convert_image_to_supported_format(data)
        # PNG passthrough, no downscale
        assert result_bytes is data
        assert mime == "image/png"

    def test_env_var_triggers_downscale(self, monkeypatch):
        """Setting OCR_MAX_IMAGE_DIM activates downscaling."""
        monkeypatch.setenv("OCR_MAX_IMAGE_DIM", "400")
        data = _make_png_bytes(2000, 1000)
        result_bytes, _ = convert_image_to_supported_format(data)
        img = _open_image(result_bytes)
        assert max(img.size) <= 400

    def test_env_var_small_image_unchanged(self, monkeypatch):
        """Env var set but image is already small enough: no change."""
        monkeypatch.setenv("OCR_MAX_IMAGE_DIM", "400")
        data = _make_png_bytes(100, 80)
        result_bytes, mime = convert_image_to_supported_format(data)
        assert result_bytes is data
        assert mime == "image/png"

    def test_env_var_invalid_ignored(self, monkeypatch):
        """Non-numeric env var is silently ignored (no downscale)."""
        monkeypatch.setenv("OCR_MAX_IMAGE_DIM", "not_a_number")
        data = _make_png_bytes(2000, 1000)
        result_bytes, mime = convert_image_to_supported_format(data)
        assert result_bytes is data
        assert mime == "image/png"

    def test_env_var_zero_ignored(self, monkeypatch):
        """Zero env var is ignored (must be positive)."""
        monkeypatch.setenv("OCR_MAX_IMAGE_DIM", "0")
        data = _make_png_bytes(2000, 1000)
        result_bytes, mime = convert_image_to_supported_format(data)
        assert result_bytes is data

    def test_env_var_negative_ignored(self, monkeypatch):
        """Negative env var is ignored."""
        monkeypatch.setenv("OCR_MAX_IMAGE_DIM", "-100")
        data = _make_png_bytes(2000, 1000)
        result_bytes, mime = convert_image_to_supported_format(data)
        assert result_bytes is data

    def test_explicit_max_dim_overrides_env_var(self, monkeypatch):
        """An explicit max_dim argument takes precedence over the env var."""
        monkeypatch.setenv("OCR_MAX_IMAGE_DIM", "9999")
        data = _make_png_bytes(2000, 1000)
        result_bytes, _ = convert_image_to_supported_format(data, max_dim=300)
        img = _open_image(result_bytes)
        assert max(img.size) <= 300


# ---------------------------------------------------------------------------
# Integration: downscale during format conversion
# ---------------------------------------------------------------------------

class TestDownscaleDuringConversion:

    def test_bmp_converted_and_downscaled(self):
        """BMP (not in default supported set) is converted to PNG AND downscaled."""
        data = _make_bmp_bytes(2000, 1000)
        result_bytes, mime = convert_image_to_supported_format(data, max_dim=500)
        assert mime == "image/png"
        img = _open_image(result_bytes)
        assert max(img.size) <= 500

    def test_png_passthrough_with_downscale(self):
        """PNG in supported set but over max_dim: downscaled, mime updated."""
        data = _make_png_bytes(3000, 2000)
        result_bytes, mime = convert_image_to_supported_format(data, max_dim=1000)
        img = _open_image(result_bytes)
        assert max(img.size) <= 1000
        assert mime == "image/png"

    def test_jpeg_passthrough_with_downscale(self):
        """JPEG in supported set and over max_dim: downscaled, mime becomes PNG."""
        img_obj = Image.new("RGB", (2000, 1000), color="green")
        buf = io.BytesIO()
        img_obj.save(buf, format="JPEG")
        data = buf.getvalue()

        result_bytes, mime = convert_image_to_supported_format(data, max_dim=500)
        img = _open_image(result_bytes)
        assert max(img.size) <= 500
        # downscale_image re-encodes as PNG
        assert mime == "image/png"
