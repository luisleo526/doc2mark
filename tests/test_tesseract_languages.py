"""Tesseract language resolution and per-image failures (the CLI E2E tests cover the
real --ocr-lang and engine-failure paths; these pin the pieces they cannot isolate)."""

import io
import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

from doc2mark.ocr.base import OCRConfig, OCREngineError
from doc2mark.ocr.cache import build_ocr_cache_key
from doc2mark.ocr.tesseract import TesseractOCR


@pytest.mark.parametrize("language, code", [
    (None, "eng"),
    ("eng", "eng"),
    ("deu", "deu"),
    ("chi_tra", "chi_tra"),
    ("eng+chi_tra", "eng+chi_tra"),
    (" eng + chi_sim ", "eng+chi_sim"),
    ("chinese_traditional", "chi_tra"),
    ("Chinese_Traditional", "chi_tra"),
    ("chinese", "chi_sim+chi_tra"),
    ("english+chinese", "eng+chi_sim+chi_tra"),
    ("chi_tra+chinese", "chi_tra+chi_sim"),
    ("script/Latin", "script/Latin"),
])
def test_language_codes_and_aliases(language, code):
    assert TesseractOCR(config=OCRConfig(language=language))._get_language_code() == code


@pytest.mark.parametrize("language", ["eng+", "chi tra", "+eng", "eng;rm -rf"])
def test_malformed_language_is_rejected(language):
    with pytest.raises(ValueError):
        TesseractOCR(config=OCRConfig(language=language))._get_language_code()


def test_malformed_language_fails_the_batch_as_an_engine_error():
    with pytest.raises(OCREngineError):
        TesseractOCR(config=OCRConfig(language="eng+")).batch_process_images([b"img"])


def test_a_judge_in_the_config_does_not_break_the_cache_key():
    judged = TesseractOCR(config=OCRConfig(non_content_judge=lambda text: None))
    plain = TesseractOCR(config=OCRConfig())
    assert build_ocr_cache_key(judged, b"img") == build_ocr_cache_key(plain, b"img")


def _page(text: str) -> bytes:
    image = Image.new("RGB", (1400, 300), "white")
    ImageDraw.Draw(image).text((40, 80), text, fill="black", font=ImageFont.load_default(size=90))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("images", [2, 3], ids=["sequential", "threaded"])
def test_an_unreadable_image_fails_alone(images):
    """One undecodable image is a per-image failure marker; the other images are read."""
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract binary not installed")
    batch = [_page("PAGE 1017"), b"\x89PNG not really an image"] + [_page("PAGE 2046")] * (images - 2)

    results = TesseractOCR(config=OCRConfig(language="eng")).batch_process_images(batch)

    assert "PAGE 1017" in results[0].text
    assert results[1].text == "" and results[1].metadata["failed"] is True and results[1].metadata["error"]
    if images == 3:
        assert "PAGE 2046" in results[2].text
