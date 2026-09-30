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


def test_figure_stored_as_thin_bands_and_a_thin_text_line_are_ocrd(run_cli, require_tool, e2e_dir):
    """Review round (images lane): a figure stored as 8 pt bands (as printer drivers write images) and a line
    of text stored as an image 11 pt tall are pictures, not slivers: the bands are joined into the figure
    first, and the line is OCR'd."""
    require_tool("tesseract")
    pdf = builders_images.banded_figure_pdf(e2e_dir / "bands.pdf", BODY, ["BANDED FIGURE 5", "PUMP 7702"],
                                            ["Signed for receipt 3318"])

    result = run_ocr(run_cli, pdf)

    text = words(result.markdown)
    assert "BANDED FIGURE 5" in text and "7702" in text and "3318" in text, result.describe()
    assert ocr_stats(result).get("ocr_requests") == 2, ocr_stats(result)


def test_large_picture_reaches_ocr_at_full_resolution(run_cli, require_tool, e2e_dir):
    """Review round (images lane): the content check must not shrink the picture OCR reads. A 3000 x 1500 px
    screenshot with 26 px type is legible at its own resolution only."""
    require_tool("tesseract")
    lines = ["Invoice 55120 approved by the controller", "Total amount due 18,940.00 EUR"]
    pdf = builders_images.screenshot_pdf(e2e_dir / "screenshot.pdf", BODY, lines)

    result = run_ocr(run_cli, pdf)

    text = words(result.markdown)
    assert "55120" in text and "18,940.00" in text, result.describe()


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
    "def peak_mb():\n"
    "    # This process's own peak RSS. On Linux ru_maxrss also carries the parent's peak across fork+exec,\n"
    "    # so VmHWM (per address space) is read instead; macOS reports ru_maxrss in bytes.\n"
    "    try:\n"
    "        with open('/proc/self/status') as status:\n"
    "            for line in status:\n"
    "                if line.startswith('VmHWM:'):\n"
    "                    return int(line.split()[1]) / 1024\n"
    "    except OSError:\n"
    "        pass\n"
    "    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
    "    return peak / (1024 * 1024 if sys.platform == 'darwin' else 1024)\n"
    "print(json.dumps({'batches': ocr.batches, 'peak_mb': peak_mb(), 'pages': result.content.count('read ')}))\n"
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
# F3 failed OCR results: never cached as an answer; what could not be read leaves a marker

FLAKY_SCRIPT = (
    "import json, sys\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "from doc2mark.ocr.base import BaseOCR, OCRResult\n"
    "from doc2mark.ocr.cache import MemoryOCRCache\n"
    "from doc2mark.ocr.tesseract import TesseractOCR\n"
    "class FlakyOCR(BaseOCR):\n"
    "    '''Fails every image of its first call (an outage, flagged failed as the built-in providers flag a\n"
    "    per-image timeout or error), then reads with Tesseract.'''\n"
    "    def __init__(self):\n"
    "        super().__init__(api_key=None)\n"
    "        self.engine = TesseractOCR()\n"
    "        self.calls = 0\n"
    "    def batch_process_images(self, images, **kwargs):\n"
    "        self.calls += 1\n"
    "        if self.calls == 1:\n"
    "            return [OCRResult(text='', metadata={'failed': True, 'error': 'timeout'}) for _ in images]\n"
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
@pytest.mark.parametrize("shown_as", ["scanned page", "picture on a text page"])
def test_failed_ocr_result_is_not_cached_and_the_unread_picture_is_marked(require_tool, e2e_dir, cache, shown_as):
    """F3: a scanned page whose OCR failed (an outage) was dropped without a trace, and the failed answer was
    cached (by an OCR cache, ``ocr_cache=``, or with the whole converted document, ``cache_dir=``), so a re-run
    with a healthy provider dropped the page again. A failed answer is not cached, the second run reads the page,
    and the first run marks what it could not read. The same for a picture on a text page. (Review round 1: a
    failure is an answer the provider flags ``failed``; an answer with no text is an answer, see
    ``test_real_empty_answers_are_cached_and_only_failed_ones_are_retried``.)"""
    require_tool("tesseract")
    if shown_as == "scanned page":
        pdf = pdfgen.image_pdf(e2e_dir / "scan.pdf", "INVOICE 8812\nTOTAL EUR 912")
    else:
        pdf = builders_images.transparent_picture_pdf(e2e_dir / "picture.pdf", BODY, ["INVOICE 8812"])

    proc = run_api(e2e_dir, FLAKY_SCRIPT, pdf, cache, e2e_dir / "document-cache")

    output = json.loads(proc.stdout.strip().splitlines()[-1])
    first, second = (words(run) for run in output["runs"])
    assert output["calls"] == 2, output
    assert "8812" not in first and "[image: OCR unavailable]" in first, output
    if shown_as == "scanned page":
        assert "INVOICE 8812" in second and "TOTAL EUR 912" in second, output
    else:
        assert "INVOICE 8812" in second and all(line in second for line in BODY), output


# ---------------------------------------------------------------------------------------------------------------
# Review round 1 (PR #21): B1 failures vs real empty answers, M1 textless pages of small pictures, M2 clip paths,
# m1 a picture repeated on every page

CACHE_SCRIPT = (
    "import json, sys\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "from doc2mark.ocr.base import BaseOCR, OCRResult\n"
    "from doc2mark.ocr.cache import MemoryOCRCache\n"
    "from doc2mark.ocr.tesseract import TesseractOCR\n"
    "class CountingOCR(BaseOCR):\n"
    "    '''Tesseract, counting the images it is sent. The very first image fails (a timeout, flagged failed).'''\n"
    "    def __init__(self):\n"
    "        super().__init__(api_key=None)\n"
    "        self.engine = TesseractOCR()\n"
    "        self.sent = 0\n"
    "    def batch_process_images(self, images, **kwargs):\n"
    "        first = self.sent == 0\n"
    "        self.sent += len(images)\n"
    "        results = self.engine.batch_process_images(images, **kwargs)\n"
    "        if first:\n"
    "            results[0] = OCRResult(text='', metadata={'failed': True, 'error': 'timeout'})\n"
    "        return results\n"
    "    def process_image(self, image, **kwargs):\n"
    "        return self.batch_process_images([image], **kwargs)[0]\n"
    "    def validate_api_key(self):\n"
    "        return True\n"
    "mode = sys.argv[2]\n"
    "cache = {}\n"
    "if mode in ('ocr_cache', 'both'):\n"
    "    cache['ocr_cache'] = MemoryOCRCache()\n"
    "if mode in ('cache_dir', 'both'):\n"
    "    cache['cache_dir'] = sys.argv[3]\n"
    "ocr = CountingOCR()\n"
    "loader = UnifiedDocumentLoader(ocr_provider=ocr, **cache)\n"
    "runs = []\n"
    "for _ in range(3):\n"
    "    before = ocr.sent\n"
    "    content = loader.load(sys.argv[1], ocr_images=True).content\n"
    "    runs.append({'sent': ocr.sent - before, 'content': content})\n"
    "print(json.dumps(runs))\n"
)


@pytest.mark.parametrize("cache", ["ocr_cache", "cache_dir", "both"])
def test_real_empty_answers_are_cached_and_only_failed_ones_are_retried(require_tool, e2e_dir, cache):
    """B1: an answer with no text (a blank duplex back side, a photo without words) is a real answer. It was
    treated like a failure: never cached by the OCR cache, and it kept the whole document out of ``cache_dir``,
    so every run re-sent every image of the document. Only an answer the provider flags ``failed`` (a timeout, an
    error) is retried; everything else is served from the caches."""
    require_tool("tesseract")
    sheets = ["SHEET 1 ALPHA", "SHEET 2 BRAVO", "SHEET 3 CHARLIE"]
    pdf = builders_images.scan_with_blank_sheet_pdf(e2e_dir / "scan.pdf", sheets, 2, BODY)

    proc = run_api(e2e_dir, CACHE_SCRIPT, pdf, cache, e2e_dir / "document-cache")

    runs = json.loads(proc.stdout.strip().splitlines()[-1])
    sent = [run["sent"] for run in runs]
    assert sent[0] >= 4, sent
    assert "SHEET 1" not in words(runs[0]["content"]) and "OCR unavailable" in runs[0]["content"], runs[0]
    for run in runs[1:]:
        assert "SHEET 1 ALPHA" in words(run["content"]) and "SHEET 3 CHARLIE" in words(run["content"]), run
    if cache == "cache_dir":
        assert sent[1:] == [sent[0], 0], sent  # no OCR cache: run 2 reads everything again, then it is stored
    else:
        assert sent[1:] == [1, 0], sent  # only the failed image is asked again


def test_textless_page_of_small_labelled_pictures_is_read(run_cli, require_tool, e2e_dir):
    """M1: a catalogue sheet without a text layer made of 20 small labelled thumbnails (58 x 84 pt, a photo area over
    a printed SKU and price) counted as "read one by one" by the text route, so the page kept the text route; each
    thumbnail then read as "shapes" (the photo area outweighs the label) and was skipped: the page emitted nothing.
    Small pictures on a page without a text layer leave the page to its render, which is OCR'd."""
    require_tool("tesseract")
    labels = [(f"SKU {4100 + n}", f"EUR {10 + n}") for n in range(20)]
    pdf = builders_images.thumbnail_sheet_pdf(e2e_dir / "thumbs.pdf", labels, [BODY] * 4)

    result = run_ocr(run_cli, pdf)

    overrides = result.json["metadata"]["extra"]["ocr_routing"]["overrides"]
    assert [(entry["page"], entry["route"]) for entry in overrides] == [(1, "image")], overrides
    page_one = words(" ".join(item["content"] for item in ocr_items(result, 1)))
    read = [sku for sku, _ in labels if sku in page_one]
    assert len(read) >= 15, (read, page_one)
    assert all(line in words(result.markdown) for line in BODY), result.describe()


def test_picture_cropped_by_a_clip_path_is_read_as_the_page_shows_it(run_cli, require_tool, e2e_dir):
    """M2: a 1920 x 1080 screenshot drawn at 1200 x 675 pt and cropped by a clip path to its top-left 250 x 230 pt
    showed 7.7 % of itself on the slide, and the share rule judged it "not shown": its numbers were lost. What the
    page shows is measured with the clip applied, and a partly shown picture is OCR'd as the page shows it (the
    visible part, rendered): its shown numbers are read, and the text the crop hides is not."""
    require_tool("tesseract")
    slide = ["Quarterly results for the northern region", "Key numbers are in the cropped screenshot",
             "Figures are unaudited and in thousands"]
    pdf = builders_images.clipped_screenshot_pdf(e2e_dir / "crop.pdf", slide, ["Revenue 2025: 48,210",
                                                                               "Margin: 31.4 %"], "HIDDEN 9999")

    result = run_ocr(run_cli, pdf)

    text = words(result.markdown)
    assert "48,210" in text and "31.4" in text, result.describe()
    assert "9999" not in text, result.describe()
    assert all(line in text for line in slide), result.describe()


def test_picture_repeated_at_the_same_place_on_every_page_is_kept_once(run_cli, require_tool, e2e_dir):
    """m1: a lettered logo at the same place on every page (a letterhead, a slide template) put its text on every
    page. Like a running header, the first copy stays and the later copies are typed text:header (left out of the
    Markdown, kept in the JSON). A picture on two pages only, at different places, stays where it is."""
    require_tool("tesseract")
    pdf = builders_images.repeated_logo_pdf(e2e_dir / "logo.pdf", [BODY] * 6, "NORTHWIND 2026", "APPROVED 5521",
                                            [2, 5])

    result = run_ocr(run_cli, pdf)

    text = words(result.markdown)
    assert text.count("NORTHWIND") == 1 and text.count("5521") == 2, result.describe()
    logos = [item for item in result.json["json_content"] if "NORTHWIND" in item["content"]]
    assert [(item["page"], item["type"]) for item in logos] == (
        [(1, PICTURE_OCR)] + [(page, "text:header") for page in range(2, 7)]), logos
