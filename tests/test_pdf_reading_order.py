from pathlib import Path

import pymupdf
import pytest

from doc2mark import UnifiedDocumentLoader


def test_cjk_fallback_glyphs_keep_their_place_on_the_line():
    # Tagged LibreOffice PDF with two synthetic paragraphs. Writer painted the
    # fallback-font glyphs 值 and 查 after the surrounding text on each baseline.
    path = Path(__file__).parent / "fixtures" / "cjk-fallback-order.pdf"
    result = UnifiedDocumentLoader(ocr_provider=None).load(path, output_format="markdown")
    assert result.content == (
        "值班表中的緊急聯絡電話只供事件處理使用。\n\n"
        "連續兩次健康檢查失敗時，值班人員應聯絡 SRE。"
    )


@pytest.mark.parametrize("position", [(144, 48), (25, 48), (36, 63)], ids=["gutter", "overlap", "different-baseline"])
def test_separate_cjk_labels_stay_separate(tmp_path, position):
    path = tmp_path / "labels.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=260, height=160)
        page.insert_text(position, "班表", fontname="china-t", fontsize=12)
        page.insert_text((24, 48), "值", fontname="china-t", fontsize=12)
        doc.save(path)

    result = UnifiedDocumentLoader(ocr_provider=None).load(path, output_format="markdown")
    assert sorted(line for line in result.content.splitlines() if line) == ["值", "班表"]
