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


# --- Signals: script-aware text weight and the text-layer garbage detector ------------------------------------

from doc2mark.core.strategy import (  # noqa: E402
    LEGIBILITY_JUDGE_THRESHOLD,
    PageSignals,
    TextLayerStats,
    decide_page_route,
    document_signals,
    judge_text_layer,
    legible_lines,
    text_layer_stats,
    text_weight,
)


def test_text_weight_ignores_whitespace_and_weights_cjk_scripts():
    assert text_weight("ab  c\n\t d") == 4
    assert text_weight("季度業績") == 12        # ideographs count 3 each
    assert text_weight("カタカナ") == 8         # kana syllables count 2 each
    assert text_weight("한국어") == 6           # hangul syllables count 2 each
    assert text_weight("AB\ufffd\ufffd") == 2   # undecodable glyphs are not text


def test_clean_text_layer_is_not_garbled():
    stats = text_layer_stats([("Invoice total EUR 2,340.00 due on 14 March 2026.", 11.0)] * 10)
    assert stats.garbage_glyphs == 0 and not stats.garbled


def test_unreadable_title_over_a_legible_body_is_garbled():
    # test-table.pdf's shape: a 38pt title of 17 U+FFFD + "ations" over ~1,250 legible body glyphs at 9.7pt.
    body = [("Direct Injection Engine with Active Cylinder Technology", 9.7)] * 25
    stats = text_layer_stats([("\ufffd" * 17 + "ations", 38.3)] + body)
    assert stats.garbage_glyphs == 17 and stats.garbled


def test_one_decorative_glyph_does_not_garble_a_page():
    stats = text_layer_stats([("\ufffd", 48.0), ("Supply Agreement", 24.0)] + [("Clause 4.2 delivery terms", 11.0)] * 20)
    assert not stats.garbled


def test_garbage_classes():
    body = [("Legible body text line", 11.0)] * 3
    for garbage in ("(cid:12)(cid:40)(cid:77)(cid:3)", "\x03\x0f\x16\x17\x13", "\u00c3\u00b6\u00c3\u00a9\u00c3\u00a4\u00c3\u00b6", "\ue049\ue06e\ue076\ue06f"):
        assert text_layer_stats([(garbage * 4, 11.0)] + body).garbled, garbage


def test_accented_letters_before_punctuation_are_not_mojibake():
    # e-acute + registered sign, e-acute + trade mark, guillemets, low-9 quotes, apostrophe, ellipsis, dash
    brands = "Nestl\u00e9\u00ae Caf\u00e9\u2122 \u00abperch\u00e9\u00bb \u201eCaf\u00e9\u201c "
    brands += "Jos\u00e9\u2019s caf\u00e9\u2026 caf\u00e9\u2014 Prezzi 2026"
    assert text_layer_stats([(brands, 11.0)] * 3).garbage_glyphs == 0
    # the same characters as real mojibake: UTF-8 bytes of a right single quote and of CJK, read as cp1252
    assert text_layer_stats([("don\u00e2\u20ac\u2122t \u00e6\u2014\u00a5\u00e6\u0153\u00ac", 11.0)]).garbage_glyphs == 9


def test_lone_private_use_icons_and_french_spacing_are_not_garbage():
    bullets = [("\uf0b7 Apples and pears for the canteen", 11.0)] * 30
    assert text_layer_stats(bullets).garbage_glyphs == 0
    french = [("Qualit\u00e9\u00a0: livraison le 3 mai, libert\u00e9\u00a0!", 11.0)] * 10
    assert text_layer_stats(french).garbage_glyphs == 0


def test_one_mojibake_like_match_among_clean_text_is_punctuation():
    # e-acute, ellipsis, right double quote are the cp1252 bytes E9 85 94: one valid UTF-8 sequence
    quote = "C'\xe9tait ferm\xe9\N{HORIZONTAL ELLIPSIS}\N{RIGHT DOUBLE QUOTATION MARK} disait-il"
    assert text_layer_stats([(quote, 12.0)]).garbage_glyphs == 0
    again = "Il \xe9tait arriv\xe9\N{HORIZONTAL ELLIPSIS}\N{RIGHT DOUBLE QUOTATION MARK}"
    assert text_layer_stats([(quote, 12.0), (again, 12.0)]).garbage_glyphs == 0
    # a typical sequence (a Latin-1 letter read back as two characters), or two distinct ones, is mojibake
    assert text_layer_stats([("R\xc3\xa9sum\xc3\xa9", 12.0)]).garbage_glyphs == 4
    assert text_layer_stats([("\xe6\N{EM DASH}\xa5\xe6\N{LATIN SMALL LIGATURE OE}\xac", 12.0)]).garbage_glyphs == 6


def test_icon_font_glyphs_and_short_private_use_rows_are_icons():
    stars = chr(0xE02A) * 5
    cards = [("Pump P-200", 18.0), (stars, 14.0), ("Rated 4.8 by 312 customers", 11.0)] * 2
    assert text_layer_stats(cards).garbage_glyphs == 0
    icons = [(chr(0xF005) * 12, 14.0, "FontAwesome5Free-Solid"), ("Rated by 312 customers", 11.0, "Helvetica")]
    assert text_layer_stats(icons).garbage_glyphs == 0
    # a long private-use run in a text font, or a row on a page with (almost) no letters, is a broken layer
    assert text_layer_stats([(chr(0xE02A) * 12, 14.0, "Tiro"), ("Rated by 312 customers", 11.0)]).garbage_glyphs == 12
    assert text_layer_stats([(stars, 14.0), ("4.8", 11.0)]).garbage_glyphs == 5


def test_legible_lines_judges_each_line_in_the_context_of_its_page():
    title = [(chr(0xFFFD) * 15, 24.0)]
    body = [[("Invoice total EUR 2340 due on 14 March 2026", 11.0)], [("Delivery to the Rotterdam depot", 11.0)]]
    assert legible_lines([title] + body) == [False, True, True]
    # one broken ligature does not make a line unreadable
    assert legible_lines([[("the ef" + chr(0xFFFD) + "cient pump runs at 40 m3/h", 11.0)]]) == [True]
    # mojibake is judged per page: with two sequences on the page, each mangled line is unreadable
    menu = [[("Caf\xc3\xa9 au lait", 11.0)], [("Cr\xc3\xa8me br\xc3\xbbl\xc3\xa9e", 11.0)], [("Plain tea", 11.0)]]
    assert legible_lines(menu) == [False, False, True]


# --- The optional legibility judge -----------------------------------------------------------------------------

LEGIBLE = TextLayerStats(chars=400, weight=400.0)


def test_judge_contract():
    calls = []

    def judge(text):
        calls.append(text)
        return 0.2

    assert judge_text_layer(judge, LEGIBLE, "page text") == 0.2
    assert calls == ["page text"]
    assert judge_text_layer(None, LEGIBLE, "page text") is None
    assert judge_text_layer(judge, TextLayerStats(chars=5, weight=5.0), "short") is None      # too little text
    garbled = TextLayerStats(chars=400, weight=0.0, garbage_glyphs=400, garbage_ratio=1.0)
    assert judge_text_layer(judge, garbled, "\ufffd" * 400) is None                             # detector decided
    assert calls == ["page text"]


def test_judge_failures_mean_cannot_judge():
    def boom(text):
        raise RuntimeError("judge down")

    class Unconvertible:
        def __float__(self):
            raise ArithmeticError("no number")

    assert judge_text_layer(boom, LEGIBLE, "text") is None
    assert judge_text_layer(lambda text: 1.7, LEGIBLE, "text") is None
    assert judge_text_layer(lambda text: "high", LEGIBLE, "text") is None
    assert judge_text_layer(lambda text: None, LEGIBLE, "text") is None
    assert judge_text_layer(lambda text: 10 ** 400, LEGIBLE, "text") is None
    assert judge_text_layer(lambda text: Unconvertible(), LEGIBLE, "text") is None


# --- Page routes -----------------------------------------------------------------------------------------------

def _page(coverage=0.0, text=0.0, **kwargs):
    return PageSignals(image_coverage=coverage, visible=TextLayerStats(chars=int(text), weight=text), **kwargs)


def test_pages_follow_the_document_unless_they_clearly_disagree():
    assert decide_page_route(_page(0.95, 0.0), "text") == ("image", "image_dominant_page")    # scanned page
    assert decide_page_route(_page(0.70, 0.0), "text") == ("text", "document_route")         # not clearly
    assert decide_page_route(_page(0.95, 150.0), "text") == ("text", "document_route")       # has real text
    assert decide_page_route(_page(0.0, 2500.0), "image") == ("text", "dense_text_page")     # text appendix
    assert decide_page_route(_page(0.0, 250.0), "image") == ("image", "document_route")      # not clearly
    assert decide_page_route(_page(0.74, 470.0), "image") == ("image", "document_route")     # slide over artwork


def test_quality_and_layer_overrides_win_in_any_document():
    garbled = TextLayerStats(chars=700, weight=100.0, garbage_glyphs=600, garbage_ratio=0.85)
    for document_route in ("text", "image"):
        assert decide_page_route(PageSignals(visible=garbled), document_route) == ("image", "illegible_text_layer")
    judged = _page(0.0, 700.0, judge_legibility=LEGIBILITY_JUDGE_THRESHOLD - 0.01)
    assert decide_page_route(judged, "text") == ("image", "illegible_text_layer")
    assert decide_page_route(_page(0.0, 700.0, judge_legibility=0.9), "text") == ("text", "document_route")
    assert decide_page_route(_page(0.0, 0.0, uncaptured_ink=0.02), "text") == ("image", "no_text_layer")
    assert decide_page_route(_page(0.0, 0.0, uncaptured_ink=0.0005), "text") == ("text", "document_route")


def test_an_invisible_layer_over_a_page_covering_scan_is_a_searchable_scan():
    layer = TextLayerStats(chars=800, weight=800.0)
    scan = PageSignals(image_coverage=0.95, invisible=layer)
    assert scan.searchable_scan and decide_page_route(scan, "text") == ("image", "searchable_scan")
    assert scan.text_layer is layer
    assert not PageSignals(image_coverage=0.95, hidden_chars=800).searchable_scan       # hidden text only
    assert not PageSignals(image_coverage=0.1, invisible=layer).searchable_scan         # not page-covering
    assert not PageSignals(image_coverage=0.95, invisible=layer,
                           visible=TextLayerStats(chars=900, weight=900.0)).searchable_scan  # a real text page


def test_pictures_the_text_route_cannot_ocr_send_a_textless_page_to_ocr():
    assert decide_page_route(_page(0.6, 0.0, uncaptured_raster=0.6), "text") == ("image", "no_text_layer")
    assert decide_page_route(_page(0.6, 0.0, uncaptured_raster=0.01), "text") == ("text", "document_route")
    assert decide_page_route(_page(0.6, 300.0, uncaptured_raster=0.6), "text") == ("text", "document_route")


def test_document_illegibility_is_a_share_of_pages_not_the_worst_page():
    garbled = TextLayerStats(chars=300, weight=30.0, garbage_glyphs=40, garbage_ratio=0.5)
    pages = [PageSignals(image_coverage=1.0, visible=garbled)] + [_page(1.0, 500.0)] * 19
    coverage, text, illegible = document_signals(pages)
    assert illegible == 1 / 20
    assert decide_doc_strategy(coverage, text, illegible) == "text"
