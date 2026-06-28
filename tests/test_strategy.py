"""Shared content-based OCR strategy decision (core/strategy.py)."""
from doc2mark.core.strategy import (
    decide_doc_strategy, IMAGE_PAGE_COVERAGE, IMAGE_PAGE_TEXT_LIMIT,
)


def test_image_when_high_coverage_low_text():
    assert decide_doc_strategy(1.0, 80) == "image"
    assert decide_doc_strategy(0.55, 199) == "image"


def test_text_when_real_text_layer_even_with_figures():
    assert decide_doc_strategy(0.9, 1500) == "text"   # big figures but real text
    assert decide_doc_strategy(0.55, 200) == "text"   # at the text limit


def test_text_when_low_coverage():
    assert decide_doc_strategy(0.10, 50) == "text"
    assert decide_doc_strategy(0.54, 50) == "text"    # just under coverage


def test_thresholds_are_the_published_constants():
    assert IMAGE_PAGE_COVERAGE == 0.55
    assert IMAGE_PAGE_TEXT_LIMIT == 200
