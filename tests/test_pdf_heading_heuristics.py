from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader


def make_loader():
    return PDFLoader.__new__(PDFLoader)


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


def classify(block, page_num=1, avg_font_size=10.0, max_font_size=12.0):
    loader = make_loader()
    return loader._convert_block_to_markdown_with_type(
        block,
        avg_font_size=avg_font_size,
        max_font_size=max_font_size,
        page_num=page_num,
        image_bboxes=[],
        table_bboxes=[],
    )


def test_page_continuation_body_text_is_not_title():
    block = make_block(
        [
            ("文為準。其因譯文有誤致生損害者，由提供譯文之一方負責賠償。", 12.0),
            ("3.契約所稱申請、報告、同意、指示、核准、通知、解釋及其他類似行為", 12.0),
        ]
    )

    markdown, text_type = classify(block, page_num=1)

    assert text_type == "text:normal"
    assert not markdown.lstrip().startswith("#")


def test_parenthesized_contract_clause_is_not_title():
    block = make_block(
        [
            ("(2)國際組織、外國政府或其授權機構、公會或商會所出具之文件。", 12.0),
            ("(3)其他經機關認定確有必要者。", 12.0),
        ]
    )

    markdown, text_type = classify(block, page_num=1)

    assert text_type == "text:normal"
    assert not markdown.lstrip().startswith("#")


def test_checkbox_contract_option_is_not_heading():
    block = make_block(
        [
            ("□總包價法。契約價金：________元(由機關填寫)。", 12.0),
        ]
    )

    markdown, text_type = classify(block, page_num=1)

    assert text_type == "text:normal"
    assert not markdown.lstrip().startswith("#")


def test_numbered_contract_option_is_not_heading():
    block = make_block(
        [
            ("1.服務成本加公費法之服務費用        元(由機關於決標後填寫)，", 12.0),
        ]
    )

    markdown, text_type = classify(block, page_num=1)

    assert text_type == "text:normal"
    assert not markdown.lstrip().startswith("#")


def test_large_body_text_line_does_not_emit_markdown_heading():
    block = make_block(
        [
            ("文為準。其因譯文有誤致生損害者，由提供譯文之一方負責賠償。", 14.0),
            ("普通正文", 10.0),
        ]
    )

    markdown, text_type = classify(block, page_num=1)

    assert text_type == "text:normal"
    assert "# 文為準" not in markdown


def test_explicit_contract_article_is_section():
    block = make_block([("第二條  履約標的", 12.0)])

    markdown, text_type = classify(block, page_num=1)

    assert text_type == "text:section"
    assert markdown.strip() == "第二條  履約標的"


def test_first_page_document_name_remains_title():
    block = make_block([("勞務採購契約範本", 12.0)])

    markdown, text_type = classify(block, page_num=0)

    assert text_type == "text:title"
    assert markdown.strip() == "勞務採購契約範本"


def test_english_structural_headings_remain_sections():
    for heading in [
        "Chapter 1 Introduction",
        "Section 2 Background",
        "Appendix A Data Dictionary",
    ]:
        markdown, text_type = classify(make_block([(heading, 12.0)]), page_num=1)

        assert text_type == "text:section"
        assert markdown.strip() == heading


def test_short_unmarked_heading_can_use_font_size_signal():
    block = make_block([("履約管理", 12.1)])

    markdown, text_type = classify(
        block,
        page_num=1,
        avg_font_size=10.0,
        max_font_size=14.0,
    )

    assert text_type == "text:section"
    assert markdown.strip() == "履約管理"
