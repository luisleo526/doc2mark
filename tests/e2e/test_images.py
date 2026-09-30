"""E2E tests for image collection: every picture is collected once, OCR'd when it carries content, and never
silently lost.

Every test runs the installed ``doc2mark`` CLI on a PDF built at test time by ``builders_images`` and asserts only
on what the run writes: the Markdown, the JSON items and ``metadata.extra["ocr_images"]`` (what the run sent to the
OCR provider). OCR is real Tesseract. Two tests use the public Python API in a subprocess instead, because what
they cover has no CLI switch: an OCR cache (``ocr_cache=``) and a provider of your own (a ``BaseOCR`` subclass),
used to fail the first OCR run and to see how many images the provider receives per call.

IDs are findings of the routing review (``~/code/.executors/d2m-rv-routing.report.md``): F7 duplicate image jobs,
F9 the decorative-image filter, F11 image-route memory, F17 decorative backgrounds, F3 empty OCR results; "off-page"
was noted by the routing change (PR #18).
"""

import json
import re
import subprocess
import sys

import pytest

from tests.e2e import builders_images, pdfgen

PICTURE_OCR = "text:image_description"

BODY = [
    "Maintenance report for the Rotterdam pump stations, spring survey.",
    "Station 12 replaced all shaft seals on 3 June 2026 after the audit.",
    "Station 14 passed its pressure test at 16 bar without any leaks.",
    "Energy use fell to 412 MWh, down seven percent on the last year.",
]


def deck_slides(count, topic):
    """``count`` slides of five lines (about 280 characters: enough text for the text route)."""
    return [[f"Slide {n}: {topic} for depot {n} in the northern region",
             f"Pallets due this week: {n * 12}, trucks booked: {n + 4}",
             "Drivers report to the gate office before seven in the morning",
             "Loading bays three and four are closed for resurfacing works",
             f"Temperature logs for route {n} are signed by the shift supervisor"] for n in range(1, count + 1)]


def words(text):
    """``text`` with every run of whitespace collapsed to one space."""
    return " ".join((text or "").split())


def ocr_items(result, page=None):
    return [item for item in result.json["json_content"]
            if item["type"] == PICTURE_OCR and (page is None or item.get("page") == page)]


def ocr_stats(result):
    """What the run sent to the OCR provider (``metadata.extra["ocr_images"]``); empty when not reported."""
    return result.json["metadata"]["extra"].get("ocr_images") or {}


def run_api(e2e_dir, script, *args, timeout=600):
    """Run ``script`` with this interpreter (the public ``doc2mark`` Python API), in the test's scratch dir."""
    proc = subprocess.run([sys.executable, "-c", script, *map(str, args)], cwd=e2e_dir, capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout)
    assert proc.returncode == 0, f"exit {proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    return proc


def run_ocr(run_cli, pdf):
    result = run_cli(pdf, "--ocr", "tesseract", "--ocr-images", fmt="both")
    assert result.exit_code == 0, result.describe()
    return result


# ---------------------------------------------------------------------------------------------------------------
# F7 duplicate image jobs


def test_one_image_shown_three_times_is_ocrd_once_and_emitted_at_each_place(run_cli, require_tool, e2e_dir):
    """F7: one image XObject placed three times on a page was OCR'd and emitted once per listing per placement,
    3 x 3 = 9 times. It is one OCR request, and one item per place the page shows it (3). An image drawn once
    through a Form XObject that the page's resources also list (so ``get_images`` lists it twice, as on the real
    deck's page 4) is emitted once."""
    require_tool("tesseract")
    pdf = builders_images.repeated_picture_pdf(e2e_dir / "repeated.pdf", BODY, ["LABEL 5521"], ["FORM 6632"])

    result = run_ocr(run_cli, pdf)

    text = words(result.markdown)
    assert text.count("5521") == 3, result.describe()
    assert text.count("6632") == 1, result.describe()
    assert [len(ocr_items(result, page)) for page in (1, 2)] == [3, 1], result.describe()
    assert ocr_stats(result).get("ocr_requests") == 2, ocr_stats(result)
    for line in BODY:
        assert line in text, result.describe()


def test_repeated_background_with_a_logo_is_ocrd_once_for_the_whole_deck(run_cli, require_tool, e2e_dir):
    """F7 / F17: a deck whose slides all show the same background picture (one image XObject) with a logo in its
    corner sent that picture to OCR once per slide. It is OCR'd once; its text is still shown with every slide."""
    require_tool("tesseract")
    slides = deck_slides(6, "quarterly figures")
    pdf = builders_images.themed_deck_pdf(e2e_dir / "logo_deck.pdf", slides, logo=["NORTHWIND"])

    result = run_ocr(run_cli, pdf)

    assert ocr_stats(result).get("ocr_requests") == 1, ocr_stats(result)
    assert "NORTHWIND" in result.markdown, result.describe()
    text = words(result.markdown)
    for lines in slides:
        assert all(line in text for line in lines), result.describe()


# ---------------------------------------------------------------------------------------------------------------
# F9 decorative-image filter, F17 decorative backgrounds


def test_small_chart_with_printed_numbers_on_a_wide_slide_is_ocrd(run_cli, require_tool, e2e_dir):
    """F9: a 130 x 75 pt chart with printed numbers on a 1440 x 810 pt slide is under 10 % of the slide in both
    directions, so it was dropped as "decorative" with no OCR and no trace. Its numbers are content."""
    require_tool("tesseract")
    pdf = builders_images.small_chart_slide_pdf(e2e_dir / "chart.pdf", BODY, ["Q1 12.4", "Q2 15.1"])

    result = run_ocr(run_cli, pdf)

    text = words(result.markdown)
    assert "12.4" in text and "15.1" in text, result.describe()
    assert all(line in text for line in BODY), result.describe()


def test_figure_made_of_small_tiles_is_read_as_one_picture(run_cli, require_tool, e2e_dir):
    """F9: a figure cut into 8 x 6 image tiles, each under 10 % of the page, on a page with a real text layer:
    every tile was "decorative", so the figure vanished. Tiles that together cover a region are one picture,
    OCR'd once as a whole (not tile by tile, which would cut every word apart)."""
    require_tool("tesseract")
    figure = ["TILED FIGURE 3", "FLOW 4410 M3"]
    pdf = builders_images.tiled_figure_pdf(e2e_dir / "tiles.pdf", BODY, figure)

    result = run_ocr(run_cli, pdf)

    text = words(result.markdown)
    assert "TILED FIGURE 3" in text and "4410" in text, result.describe()
    assert ocr_stats(result).get("ocr_requests") == 1, ocr_stats(result)
    assert len(ocr_items(result)) == 1, result.describe()
    assert all(line in text for line in BODY), result.describe()


def test_picture_on_a_rotated_page_is_ocrd(run_cli, require_tool, e2e_dir):
    """F9: on a page shown rotated, the picture's unrotated size was compared with the rotated page, so an
    80 x 55 pt picture counted as under 10 % of the page and was dropped."""
    require_tool("tesseract")
    pdf = builders_images.rotated_page_picture_pdf(e2e_dir / "rotated.pdf", BODY, ["AX-7731"])

    result = run_ocr(run_cli, pdf)

    assert "7731" in result.markdown, result.describe()
    assert all(line in words(result.markdown) for line in BODY), result.describe()


def test_plain_backgrounds_and_icons_are_not_sent_to_ocr(run_cli, require_tool, e2e_dir):
    """F17: a themed deck with a live text layer, a plain gradient background picture and a small icon on every
    slide sent the background to OCR on every slide (one call per slide, and a language model then describes
    "a light-blue background"). Pictures with nothing to read (a plain background, an icon without text) are not
    OCR'd and leave nothing in the output; the text layer is intact."""
    require_tool("tesseract")
    slides = deck_slides(10, "delivery schedule")
    pdf = builders_images.themed_deck_pdf(e2e_dir / "themed_deck.pdf", slides)

    result = run_ocr(run_cli, pdf)

    stats = ocr_stats(result)
    assert stats.get("ocr_requests") == 0, stats
    assert ocr_items(result) == [], result.describe()
    text = words(result.markdown)
    for lines in slides:
        assert all(line in text for line in lines), result.describe()


def test_transparent_picture_is_read_as_the_page_shows_it(run_cli, require_tool, e2e_dir):
    """A transparent PNG (black letters on a transparent background, how charts and logos are often exported)
    reached OCR as its colour channels alone, a solid black rectangle, so its words were lost. OCR reads it
    composited onto white, as the page shows it."""
    require_tool("tesseract")
    pdf = builders_images.transparent_picture_pdf(e2e_dir / "transparent.pdf", BODY, ["NET 4410 EUR"])

    result = run_ocr(run_cli, pdf)

    assert "4410" in result.markdown, result.describe()
    assert all(line in words(result.markdown) for line in BODY), result.describe()


# ---------------------------------------------------------------------------------------------------------------
# Off-page pictures


def test_pictures_the_page_does_not_show_are_not_ocrd(run_cli, require_tool, e2e_dir):
    """Off-page (noted by the routing change): a picture placed entirely above the page and one of which only a
    6 pt strip reaches onto the page were OCR'd on the text route, so their words appeared as page content."""
    require_tool("tesseract")
    pdf = builders_images.offpage_pictures_pdf(e2e_dir / "offpage.pdf", BODY, ["OFFPAGE 4410"], ["SLIVER 8830"])

    result = run_ocr(run_cli, pdf)

    assert "OFFPAGE" not in result.markdown and "SLIVER" not in result.markdown, result.describe()
    assert ocr_stats(result).get("ocr_requests") == 0, ocr_stats(result)
    assert all(line in words(result.markdown) for line in BODY), result.describe()


# ---------------------------------------------------------------------------------------------------------------
# F11 image-route memory: bounded OCR batches, output in page order


def test_long_scan_is_ocrd_in_bounded_batches_in_page_order(run_cli, require_tool, e2e_dir):
    """F11: every page render of the image route was held (base64 plus a decoded copy) until ONE OCR call for
    the whole document, about 4.4 MB a page (2,000 pages: about 8.7 GB). Renders now go to the provider in
    bounded batches and are released after each; the pages still come out in order."""
    require_tool("tesseract")
    pages = 70
    pdf = builders_images.long_scan_pdf(e2e_dir / "long_scan.pdf", pages)

    result = run_ocr(run_cli, pdf)

    text = words(result.markdown)
    found = [re.search(rf"\bSHEET {number}\b", text) for number in range(1, pages + 1)]
    assert all(found), [number for number, match in enumerate(found, 1) if not match]
    positions = [match.start() for match in found]
    assert positions == sorted(positions), positions
    stats = ocr_stats(result)
    assert stats.get("page_renders") == pages and stats.get("ocr_requests") == pages, stats
    assert 0 < stats.get("largest_batch", pages) <= 32 and stats.get("batches", 0) >= 3, stats


MEMORY_SCRIPT = (
    "import json, resource, sys\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "from doc2mark.ocr.base import BaseOCR, OCRResult\n"
    "class CountingOCR(BaseOCR):\n"
    "    def __init__(self):\n"
    "        super().__init__(api_key=None)\n"
    "        self.batches = []\n"
    "    def batch_process_images(self, images, **kwargs):\n"
    "        self.batches.append(len(images))\n"
    "        return [OCRResult(text=f'read {len(self.batches)}.{i}') for i in range(len(images))]\n"
    "    def process_image(self, image, **kwargs):\n"
    "        return self.batch_process_images([image], **kwargs)[0]\n"
    "    def validate_api_key(self):\n"
    "        return True\n"
    "ocr = CountingOCR()\n"
    "result = UnifiedDocumentLoader(ocr_provider=ocr).load(sys.argv[1], ocr_images=True)\n"
    "peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KiB on Linux, bytes on macOS\n"
    "peak_mb = peak / (1024 * 1024 if sys.platform == 'darwin' else 1024)\n"
    "print(json.dumps({'batches': ocr.batches, 'peak_mb': peak_mb, 'pages': result.content.count('read ')}))\n"
)


def test_large_scan_keeps_memory_bounded(e2e_dir):
    """F11 at scale: 160 pages that each render to a large, distinct PNG (about 2.9 MB). With every render held
    until one OCR call, the peak memory grew by about 6.4 MB a page (1.2 GB here, 2.7 GB at 400 pages);
    bounded batches keep it flat (about 0.26 GB at 40 or 400 pages). The provider (your own ``BaseOCR``) sees
    the batch sizes."""
    pages = 160
    pdf = builders_images.noisy_scan_pdf(e2e_dir / "noisy_scan.pdf", pages)

    proc = run_api(e2e_dir, MEMORY_SCRIPT, pdf)

    output = json.loads(proc.stdout.strip().splitlines()[-1])
    assert sum(output["batches"]) == pages and output["pages"] == pages, output
    assert max(output["batches"]) <= 32, output
    assert output["peak_mb"] < 768, output


# ---------------------------------------------------------------------------------------------------------------
# F3 empty OCR results: never cached as an answer; a dropped page leaves a marker

FLAKY_SCRIPT = (
    "import json, sys\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "from doc2mark.ocr.base import BaseOCR, OCRResult\n"
    "from doc2mark.ocr.cache import MemoryOCRCache\n"
    "from doc2mark.ocr.tesseract import TesseractOCR\n"
    "class FlakyOCR(BaseOCR):\n"
    "    '''Returns no text on its first call (an outage, a refusal), then reads with Tesseract.'''\n"
    "    def __init__(self):\n"
    "        super().__init__(api_key=None)\n"
    "        self.engine = TesseractOCR()\n"
    "        self.calls = 0\n"
    "    def batch_process_images(self, images, **kwargs):\n"
    "        self.calls += 1\n"
    "        if self.calls == 1:\n"
    "            return [OCRResult(text='') for _ in images]\n"
    "        return self.engine.batch_process_images(images, **kwargs)\n"
    "    def process_image(self, image, **kwargs):\n"
    "        return self.batch_process_images([image], **kwargs)[0]\n"
    "    def validate_api_key(self):\n"
    "        return True\n"
    "ocr = FlakyOCR()\n"
    "cache = {'ocr_cache': MemoryOCRCache()} if sys.argv[2] == 'ocr_cache' else {'cache_dir': sys.argv[3]}\n"
    "loader = UnifiedDocumentLoader(ocr_provider=ocr, **cache)\n"
    "runs = [loader.load(sys.argv[1], ocr_images=True).content for _ in range(2)]\n"
    "print(json.dumps({'runs': runs, 'calls': ocr.calls}))\n"
)


@pytest.mark.parametrize("cache", ["ocr_cache", "cache_dir"])
def test_empty_ocr_result_is_not_cached_and_the_dropped_page_is_marked(require_tool, e2e_dir, cache):
    """F3: a scanned page whose OCR came back empty (a failing provider) was dropped without a trace, and the
    empty answer was cached (by an OCR cache, ``ocr_cache=``, or with the whole converted document, ``cache_dir=``),
    so a re-run with a healthy provider dropped the page again. The empty answer is not cached, the second run
    reads the page, and the first run marks the page it could not read."""
    require_tool("tesseract")
    pdf = pdfgen.image_pdf(e2e_dir / "scan.pdf", "INVOICE 8812\nTOTAL EUR 912")

    proc = run_api(e2e_dir, FLAKY_SCRIPT, pdf, cache, e2e_dir / "document-cache")

    output = json.loads(proc.stdout.strip().splitlines()[-1])
    first, second = (words(run) for run in output["runs"])
    assert output["calls"] == 2, output
    assert "8812" not in first and re.search(r"page 1\b.*OCR returned no content", first), output
    assert "INVOICE 8812" in second and "TOTAL EUR 912" in second, output
