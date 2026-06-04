from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader


LONG_CJK_SENTENCE = "甲" * 12 + "，" + "乙" * 18 + "。"
LONG_CJK_COMMA_FRAGMENT = "甲" * 16 + "，" + "乙" * 20
NUMBERED_LONG_CJK_CLAUSE = "5. " + "甲" * 10 + "，" + "乙" * 20
PARENTHESIZED_LONG_CJK_CLAUSE = "(1) " + "甲" * 10 + "，" + "乙" * 20
CHECKBOX_FORM_FIELD = "□" + "甲" * 8 + "：" + "_" * 8 + "元。"
ARTICLE_CJK_HEADING = "第二條  甲乙事項"
SHORT_CJK_TITLE = "甲乙丙丁"
SHORT_CJK_HEADING = "甲乙事項"
CJK_OUTLINE_HEADING = "一、甲乙事項"
CJK_OUTLINE_HEADING_WITH_SEPARATORS = "陸、甲乙事項、丙丁事項"

LONG_LATIN_SENTENCE = "Alpha beta gamma delta epsilon zeta eta theta iota."
LONG_LATIN_COMMA_FRAGMENT = "Alpha beta gamma, delta epsilon zeta eta theta iota"
NUMBERED_LONG_LATIN_CLAUSE = "3. Alpha beta gamma, delta epsilon zeta eta theta iota"
SHORT_LATIN_HEADING = "Alpha Beta"
LATIN_COMMA_HEADING = "Alpha, Beta and Gamma"


def make_loader():
    return PDFLoader.__new__(PDFLoader)


class FakePage:
    def __init__(self, texts):
        self._texts = texts

    def get_text(self, *args, **kwargs):
        return {
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {
                            "spans": [
                                {
                                    "text": text,
                                    "size": 10.0,
                                    "flags": 0,
                                }
                            ]
                        }
                    ],
                }
                for text in self._texts
            ]
        }


class FakeDoc:
    def __init__(self, pages):
        self._pages = pages

    def __len__(self):
        return len(self._pages)

    def load_page(self, page_num):
        return self._pages[page_num]


def make_loader_with_doc(doc):
    loader = make_loader()
    loader.doc = doc
    return loader


def make_block(lines):
    return {
        "bbox": (72.0, 70.0, 520.0, 110.0),
        "lines": [
            {
                "spans": [
                    {
                        "text": text,
                        "size": size,
                        "flags": 0,
                    }
                ]
            }
            for text, size in lines
        ],
    }


def classify(block, page_num=1, avg_font_size=10.0, max_font_size=12.0, loader=None):
    loader = loader or make_loader()
    return loader._convert_block_to_markdown_with_type(
        block,
        avg_font_size=avg_font_size,
        max_font_size=max_font_size,
        page_num=page_num,
        image_bboxes=[],
        table_bboxes=[],
    )


def assert_not_markdown_heading(markdown):
    assert not markdown.lstrip().startswith("#")


def test_long_cjk_sentence_is_not_heading():
    markdown, text_type = classify(make_block([(LONG_CJK_SENTENCE, 12.1)]))

    assert text_type == "text:normal"
    assert_not_markdown_heading(markdown)


def test_long_cjk_comma_fragment_is_not_heading():
    markdown, text_type = classify(make_block([(LONG_CJK_COMMA_FRAGMENT, 12.1)]))

    assert text_type == "text:normal"
    assert_not_markdown_heading(markdown)


def test_long_latin_sentence_is_not_heading():
    markdown, text_type = classify(make_block([(LONG_LATIN_SENTENCE, 12.1)]))

    assert text_type == "text:normal"
    assert_not_markdown_heading(markdown)


def test_long_latin_comma_fragment_is_not_heading():
    markdown, text_type = classify(make_block([(LONG_LATIN_COMMA_FRAGMENT, 12.1)]))

    assert text_type == "text:normal"
    assert_not_markdown_heading(markdown)


def test_trailing_continuation_fragment_is_not_heading():
    markdown, text_type = classify(make_block([("Alpha beta gamma,", 12.1)]))

    assert text_type == "text:normal"
    assert_not_markdown_heading(markdown)


def test_checkbox_form_field_is_not_heading():
    markdown, text_type = classify(make_block([(CHECKBOX_FORM_FIELD, 12.1)]))

    assert text_type == "text:normal"
    assert_not_markdown_heading(markdown)


def test_numbered_long_cjk_clause_remains_list_not_heading():
    markdown, text_type = classify(
        make_block([(NUMBERED_LONG_CJK_CLAUSE, 12.1)]),
        avg_font_size=10.0,
        max_font_size=14.0,
    )

    assert text_type == "text:list"
    assert_not_markdown_heading(markdown)


def test_numbered_long_latin_clause_remains_list_not_heading():
    markdown, text_type = classify(
        make_block([(NUMBERED_LONG_LATIN_CLAUSE, 12.1)]),
        avg_font_size=10.0,
        max_font_size=14.0,
    )

    assert text_type == "text:list"
    assert_not_markdown_heading(markdown)


def test_parenthesized_long_clause_is_not_heading():
    markdown, text_type = classify(
        make_block([(PARENTHESIZED_LONG_CJK_CLAUSE, 12.1)]),
        avg_font_size=10.0,
        max_font_size=14.0,
    )

    assert text_type in {"text:list", "text:normal"}
    assert_not_markdown_heading(markdown)


def test_large_body_text_line_does_not_emit_markdown_heading():
    block = make_block(
        [
            (LONG_CJK_SENTENCE, 14.0),
            (SHORT_CJK_HEADING, 10.0),
        ]
    )

    markdown, text_type = classify(block)

    assert text_type == "text:normal"
    assert_not_markdown_heading(markdown)


def test_cjk_article_marker_is_section():
    markdown, text_type = classify(make_block([(ARTICLE_CJK_HEADING, 12.0)]))

    assert text_type == "text:section"
    assert markdown.strip() == ARTICLE_CJK_HEADING


def test_first_page_short_text_with_title_layout_is_title():
    markdown, text_type = classify(make_block([(SHORT_CJK_TITLE, 12.0)]), page_num=0)

    assert text_type == "text:title"
    assert markdown.strip() == SHORT_CJK_TITLE


def test_title_can_appear_on_first_text_page_after_blank_page():
    loader = make_loader_with_doc(FakeDoc([FakePage([]), FakePage(["甲乙丙丁"])]))

    markdown, text_type = classify(
        make_block([(SHORT_CJK_TITLE, 12.0)]),
        page_num=1,
        loader=loader,
    )

    assert text_type == "text:title"
    assert markdown.strip() == SHORT_CJK_TITLE


def test_second_page_title_layout_is_not_title_when_first_page_has_text():
    loader = make_loader_with_doc(FakeDoc([FakePage(["封面文字"]), FakePage(["甲乙丙丁"])]))

    markdown, text_type = classify(
        make_block([(SHORT_CJK_TITLE, 12.0)]),
        page_num=1,
        loader=loader,
    )

    assert text_type != "text:title"
    assert markdown.strip() == SHORT_CJK_TITLE


def test_english_explicit_headings_remain_sections():
    for heading in [
        "Chapter 1 Alpha",
        "Section 2 Beta",
        "Appendix A Gamma",
    ]:
        markdown, text_type = classify(make_block([(heading, 12.0)]))

        assert text_type == "text:section"
        assert markdown.strip() == heading


def test_short_unmarked_heading_requires_layout_signal():
    markdown, text_type = classify(
        make_block([(SHORT_CJK_HEADING, 12.1)]),
        avg_font_size=10.0,
        max_font_size=14.0,
    )

    assert text_type == "text:section"
    assert markdown.strip() == SHORT_CJK_HEADING


def test_short_unmarked_text_without_layout_signal_is_normal():
    markdown, text_type = classify(
        make_block([(SHORT_CJK_HEADING, 10.0)]),
        avg_font_size=10.0,
        max_font_size=14.0,
    )

    assert text_type == "text:normal"
    assert markdown.strip() == SHORT_CJK_HEADING


def test_structured_headings_can_use_layout_signal():
    for heading in [
        "1. Alpha",
        "1) Alpha",
        "1.1 Alpha Beta",
        "(1) Alpha",
        "1.甲乙事項",
        CJK_OUTLINE_HEADING,
        "（一）甲乙事項",
    ]:
        markdown, text_type = classify(
            make_block([(heading, 12.1)]),
            avg_font_size=10.0,
            max_font_size=14.0,
        )

        assert text_type == "text:section"
        assert markdown.strip() == heading


def test_structured_marker_without_layout_signal_remains_list():
    markdown, text_type = classify(
        make_block([("1. Alpha beta", 10.0)]),
        avg_font_size=10.0,
        max_font_size=14.0,
    )

    assert text_type == "text:list"
    assert markdown.strip() == "1. Alpha beta"


def test_decimal_and_abbreviation_shapes_are_not_list_markers():
    for text in ["1.5x faster", "2.0release", "A.D."]:
        markdown, text_type = classify(
            make_block([(text, 10.0)]),
            avg_font_size=10.0,
            max_font_size=14.0,
        )

        assert text_type == "text:normal"
        assert markdown.strip() == text


def test_bare_structured_heading_is_preserved_in_section_block():
    markdown, text_type = classify(
        make_block([("3.1.2", 12.1)]),
        avg_font_size=10.0,
        max_font_size=14.0,
    )

    assert text_type == "text:section"
    assert markdown.strip() == "3.1.2"


def test_short_latin_heading_can_use_font_size_signal():
    markdown, text_type = classify(
        make_block([(SHORT_LATIN_HEADING, 12.1)]),
        avg_font_size=10.0,
        max_font_size=14.0,
    )

    assert text_type == "text:section"
    assert markdown.strip() == SHORT_LATIN_HEADING


def test_short_latin_heading_with_comma_can_use_font_size_signal():
    markdown, text_type = classify(
        make_block([(LATIN_COMMA_HEADING, 12.1)]),
        avg_font_size=10.0,
        max_font_size=14.0,
    )

    assert text_type == "text:section"
    assert markdown.strip() == LATIN_COMMA_HEADING


def test_cjk_outline_heading_with_multiple_separators_can_be_section():
    markdown, text_type = classify(
        make_block([(CJK_OUTLINE_HEADING_WITH_SEPARATORS, 12.1)]),
        avg_font_size=10.0,
        max_font_size=14.0,
    )

    assert text_type == "text:section"
    assert markdown.strip() == CJK_OUTLINE_HEADING_WITH_SEPARATORS
