"""Shared content-based OCR strategy decision (core/strategy.py)."""
from doc2mark.core.strategy import (
    decide_doc_strategy, IMAGE_PAGE_COVERAGE, IMAGE_PAGE_TEXT_LIMIT,
    ILLEGIBLE_TEXT_RATIO,
)


def test_image_when_high_coverage_low_text():
    assert decide_doc_strategy(1.0, 80) == "image"
    assert decide_doc_strategy(0.55, 199) == "image"


def test_image_when_image_dominant_and_text_layer_illegible():
    # A full-bleed designed/print page (coverage high) whose text layer is partly
    # unmappable (subset fonts w/o ToUnicode -> U+FFFD in the headline) must route
    # to image even though it carries a dense text layer: the render is authoritative.
    # Regression guard for the Skoda spec-sheet PDF (headline_illegibility ~= 0.74).
    assert decide_doc_strategy(1.0, 1531, 0.74) == "image"
    assert decide_doc_strategy(0.55, 1531, ILLEGIBLE_TEXT_RATIO) == "image"


def test_text_when_image_dominant_but_text_layer_legible():
    # de27455 intent preserved: image-dominant + text-rich + CLEAN text -> text.
    assert decide_doc_strategy(0.9, 1500, 0.0) == "text"
    assert decide_doc_strategy(1.0, 1531, ILLEGIBLE_TEXT_RATIO - 0.01) == "text"


def test_illegibility_gated_behind_coverage():
    # A low-coverage text doc with a flaky glyph here and there must NOT be forced
    # into whole-doc OCR; the quality gate only applies to image-dominant pages.
    assert decide_doc_strategy(0.10, 1531, 0.9) == "text"
    assert decide_doc_strategy(0.54, 1531, 0.9) == "text"   # just under coverage


def test_text_when_real_text_layer_even_with_figures():
    assert decide_doc_strategy(0.9, 1500) == "text"   # big figures but real text
    assert decide_doc_strategy(0.55, 200) == "text"   # at the text limit


def test_text_when_low_coverage():
    assert decide_doc_strategy(0.10, 50) == "text"
    assert decide_doc_strategy(0.54, 50) == "text"    # just under coverage


def test_thresholds_are_the_published_constants():
    assert IMAGE_PAGE_COVERAGE == 0.55
    assert IMAGE_PAGE_TEXT_LIMIT == 200
