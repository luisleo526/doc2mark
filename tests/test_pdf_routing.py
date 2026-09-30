"""pdf_routing: what counts as painted text the render OCR missed, and the judge's cache identity."""
import functools

import pymupdf

from doc2mark import UnifiedDocumentLoader
from doc2mark.pipelines import pdf_routing


def _page_with_lines(tmp_path, lines):
    doc = pymupdf.open()
    page = doc.new_page()
    for n, line in enumerate(lines):
        page.insert_text((72, 100 + 20 * n), line, fontsize=11)
    path = tmp_path / "lines.pdf"
    doc.save(str(path))
    doc.close()
    return pymupdf.open(str(path))


def test_lines_the_ocr_reproduced_with_markup_are_not_missing(tmp_path):
    doc = _page_with_lines(tmp_path, ["Figure 3 - Site plan", "Revenue: $4.2M (FY2025)", "Phase 1 complete"])
    page = doc[0]
    measure = pdf_routing.measure_page(page)
    ocr = "**Figure 3** \u2013 Site plan\n\n**Revenue:** $4.2M (FY2025)\n\n- Phase 1 complete"

    assert pdf_routing.missing_painted_lines(page, measure, ocr) == []
    assert pdf_routing.missing_painted_lines(page, measure, "Figure 3 - Site plan") == [
        "Revenue: $4.2M (FY2025)", "Phase 1 complete"]


def _identity(judge):
    return UnifiedDocumentLoader(ocr_provider=None, legibility_judge=judge)._judge_identity()


def test_judge_identity_tells_configured_judges_apart():
    def judge(page_text, threshold):
        return threshold

    class Named:
        cache_key = "jev-legibility-v1"

        def __call__(self, page_text):
            return 0.9

    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("no attributes")

        def __call__(self, page_text):
            return None

    assert _identity(functools.partial(judge, threshold=0.2)) != _identity(functools.partial(judge, threshold=0.8))
    assert _identity(Named()) == "jev-legibility-v1"
    assert _identity(Broken())
