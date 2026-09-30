"""The loader's result cache must not answer a load that uses a different legibility judge."""
import pymupdf

from doc2mark import UnifiedDocumentLoader
from doc2mark.ocr.base import BaseOCR, OCRResult


class _BlankOCR(BaseOCR):
    """An OCR provider that reads nothing (the judge is consulted only when OCR runs)."""

    def batch_process_images(self, images, **kwargs):
        return [OCRResult(text="") for _ in images]


def _text_pdf(path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A plain page of legible text, long enough for a judge to look at.", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def test_cached_result_is_not_reused_for_a_different_judge(tmp_path):
    pdf = _text_pdf(tmp_path / "doc.pdf")
    cache = tmp_path / "cache"
    UnifiedDocumentLoader(ocr_provider=_BlankOCR(), cache_dir=str(cache)).load(pdf, ocr_images=True)
    seen = []

    def judge(page_text):
        seen.append(page_text)
        return 0.9

    UnifiedDocumentLoader(ocr_provider=_BlankOCR(), cache_dir=str(cache), legibility_judge=judge).load(
        pdf, ocr_images=True)

    assert len(seen) == 1 and "legible text" in seen[0]
