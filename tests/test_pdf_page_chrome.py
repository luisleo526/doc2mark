"""Unit tests for PDF running header/footer handling that the CLI cannot reach.

The CLI has no way to pass a ``boilerplate_judge`` callable, and it cannot produce
``[image: OCR unavailable]`` placeholders deterministically without breaking a real OCR
provider, so those contracts are checked here on real PDFs built with PyMuPDF. The judge
passed in is the hook's own injection point, not a mock of the pipeline. The same goes for
the seams other lanes plug into: the page object text is read from (``_text_page``) and the
CropBox restored on that page's own document.
"""

from contextlib import contextmanager

import pymupdf
import pytest

from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader, pdf_to_markdown


def _heading_on_two_of_four_pages(path):
    """4 pages of unique body text; "Summary of Findings" tops pages 1 and 3 (too few pages to
    be a running header, so the deterministic rule keeps it: an ambiguous repeated line), and
    "ACME Corp" is a running header on every page (strong evidence: its first copy stays, the
    other three are chrome)."""
    doc = pymupdf.open()
    for p in range(1, 5):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 40), "ACME Corp", fontsize=9)
        if p in (1, 3):
            page.insert_text((72, 80), "Summary of Findings", fontsize=16, fontname="hebo")
        page.insert_text((72, 150), "\n".join(f"Body line {p}-{i} with ordinary words." for i in range(8)),
                         fontsize=10.5)
    doc.save(str(path))
    doc.close()
    return path


def _convert(path, judge=None):
    loader = PDFLoader(path, boilerplate_judge=judge)
    try:
        data = loader.convert_to_json(extract_images=False, show_progress=False)
    finally:
        loader.close()
    return data, pdf_to_markdown(data)


class _RecordingJudge:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, line_text, context):
        self.calls.append((line_text, context))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_without_a_judge_ambiguous_repeated_lines_are_kept(tmp_path):
    data, markdown = _convert(_heading_on_two_of_four_pages(tmp_path / "doc.pdf"))

    assert markdown.count("Summary of Findings") == 2
    assert markdown.count("ACME Corp") == 1  # only the running header's first copy
    headers = [(item["page"], item["content"]) for item in data["content"] if item["type"] == "text:header"]
    assert headers == [(2, "ACME Corp"), (3, "ACME Corp"), (4, "ACME Corp")]


def test_judge_is_asked_once_per_ambiguous_line_with_its_context(tmp_path):
    judge = _RecordingJudge(None)

    data, markdown = _convert(_heading_on_two_of_four_pages(tmp_path / "doc.pdf"), judge)

    contexts = {text: context for text, context in judge.calls}
    assert len(judge.calls) == 2 and set(contexts) == {"Summary of Findings", "ACME Corp"}
    heading = contexts["Summary of Findings"]
    assert heading["zone"] == "header"
    assert heading["pages"] == [1, 3]
    assert heading["page_count"] == 4
    assert heading["reason"] == "few_pages"
    assert heading["font_size"] > heading["body_font_size"]
    assert heading["repeated_on"] == [1, 3]
    assert contexts["ACME Corp"]["reason"] == "first_occurrence"
    assert contexts["ACME Corp"]["pages"] == [1]
    assert contexts["ACME Corp"]["repeated_on"] == [1, 2, 3, 4]
    assert markdown.count("Summary of Findings") == 2  # None keeps the lines
    assert markdown.count("ACME Corp") == 1


def test_judge_probability_at_or_above_half_removes_every_copy(tmp_path):
    data, markdown = _convert(_heading_on_two_of_four_pages(tmp_path / "doc.pdf"), _RecordingJudge(0.5))

    assert "Summary of Findings" not in markdown
    assert "ACME Corp" not in markdown  # the kept first copy goes too
    retyped = [(item["page"], item["type"]) for item in data["content"] if item["content"] == "Summary of Findings"]
    assert retyped == [(1, "text:header"), (3, "text:header")]  # retyped, not deleted


@pytest.mark.parametrize("answer", [0.49, 0.0, None, 1.5, -0.1, float("nan"), True, "yes", RuntimeError("down")])
def test_judge_answers_other_than_a_probability_of_at_least_half_keep_the_line(tmp_path, answer):
    data, markdown = _convert(_heading_on_two_of_four_pages(tmp_path / "doc.pdf"), _RecordingJudge(answer))

    assert markdown.count("Summary of Findings") == 2
    assert markdown.count("ACME Corp") == 1


def test_judge_may_answer_with_numpy_floats(tmp_path):
    import numpy

    data, markdown = _convert(_heading_on_two_of_four_pages(tmp_path / "doc.pdf"), _RecordingJudge(numpy.float32(0.9)))

    assert "Summary of Findings" not in markdown


def test_judge_is_not_asked_about_unique_lines_or_bare_page_numbers(tmp_path):
    doc = pymupdf.open()
    for p in range(1, 7):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 40), f"Report section {p}", fontsize=9)
        page.insert_text((72, 150), "\n".join(f"Body line {p}-{i}." for i in range(8)), fontsize=10.5)
        page.insert_text((280, 800), f"Page {p} of 6", fontsize=9)
    path = tmp_path / "doc.pdf"
    doc.save(str(path))
    doc.close()
    judge = _RecordingJudge(1.0)

    data, markdown = _convert(path, judge)

    assert judge.calls == []
    assert "Page 1 of 6" not in markdown
    assert all(f"Report section {p}" in markdown and f"Body line {p}-0." in markdown for p in range(1, 7))


def test_footer_with_a_literal_hash_does_not_switch_detection_off(tmp_path):
    """'# | 7' (a literal '#') and '4 | 7' mask to the same template with one and two numbers."""
    doc = pymupdf.open()
    for p in range(1, 7):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 40), "ACME Corp", fontsize=9)
        page.insert_text((72, 150), "\n".join(f"Body line {p}-{i}." for i in range(8)), fontsize=10.5)
        page.insert_text((280, 800), "# | 7" if p <= 3 else f"{p} | 7", fontsize=9)
    path = tmp_path / "doc.pdf"
    doc.save(str(path))
    doc.close()

    data, markdown = _convert(path)

    assert markdown.count("ACME Corp") == 1  # detection ran: the running header's repeats are chrome


def _blank_loader(path, pages):
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return PDFLoader(path)


def test_ocr_text_and_placeholders_at_the_top_of_most_pages_are_never_retyped(tmp_path):
    """R-F16: page renders sit at y=0 and failed images leave a placeholder; both repeat."""
    loader = _blank_loader(tmp_path / "blank.pdf", 4)
    placeholder = "<image_ocr_result>[image: OCR unavailable]</image_ocr_result>"
    scan = "<image_ocr_result>CLAIM FORM Policy 55-201-7788</image_ocr_result>"
    document = {"pages": 4, "content": []}
    for p in range(1, 5):
        document["content"].append({"type": "text:image_description", "content": placeholder,
                                    "page": p, "position_y": 0.0})
        document["content"].append({"type": "text:image_description", "content": scan,
                                    "page": p, "position_y": 0.0})

    loader._detect_repeated_content(document)
    loader.close()

    assert {item["type"] for item in document["content"]} == {"text:image_description"}
    markdown = pdf_to_markdown(document)
    assert markdown.count("[image: OCR unavailable]") == 4
    assert markdown.count("CLAIM FORM Policy 55-201-7788") == 4


def test_table_repeated_at_the_top_of_most_pages_keeps_its_first_copy(tmp_path):
    loader = _blank_loader(tmp_path / "blank.pdf", 5)
    letterhead = "| ACME | Doc ID 42 |\n| --- | --- |\n"
    document = {"pages": 5, "content": []}
    for p in range(1, 6):
        document["content"].append({"type": "table", "content": letterhead, "page": p, "position_y": 30.0})
        document["content"].append({"type": "table", "content": f"| Q{p} | {p * 10} |\n| --- | --- |\n",
                                    "page": p, "position_y": 400.0})

    loader._detect_repeated_content(document)
    loader.close()

    letterheads = [item["type"] for item in document["content"] if item["content"] == letterhead]
    assert letterheads == ["table"] + ["text:header"] * 4
    assert [item["type"] for item in document["content"] if item["position_y"] == 400.0] == ["table"] * 5


def _numbered_labels(path, pages=6):
    """``pages`` pages topped by "Lesson · N" (a per-page label that counts with the pages, 12pt)."""
    doc = pymupdf.open()
    for p in range(1, pages + 1):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 50), f"Lesson · {p}", fontsize=12)
        page.insert_text((72, 150), "\n".join(f"Body line {p}-{i} with ordinary words." for i in range(8)),
                         fontsize=10.5)
    doc.save(str(path))
    doc.close()
    return path


class _ReasonJudge(_RecordingJudge):
    """Answers ``answers[reason]`` (None for other reasons)."""

    def __call__(self, line_text, context):
        self.calls.append((line_text, context))
        return self.answer.get(context["reason"])


def test_judge_is_asked_about_numbered_labels_that_the_rule_keeps(tmp_path):
    judge = _RecordingJudge(None)

    data, markdown = _convert(_numbered_labels(tmp_path / "doc.pdf"), judge)

    assert all(f"Lesson · {p}" in markdown for p in range(1, 7))
    assert [(text, context["reason"], context["pages"], context["repeated_on"], context["zone"])
            for text, context in judge.calls] == [("Lesson · 1", "numbered_label", [1, 2, 3, 4, 5, 6],
                                                   [1, 2, 3, 4, 5, 6], "header")]


def test_numbered_labels_judged_chrome_keep_their_first_copy(tmp_path):
    judge = _ReasonJudge({"numbered_label": 0.9})

    data, markdown = _convert(_numbered_labels(tmp_path / "doc.pdf"), judge)

    assert "Lesson · 1" in markdown
    assert not any(f"Lesson · {p}" in markdown for p in range(2, 7))
    headers = [(item["page"], item["content"]) for item in data["content"] if item["type"] == "text:header"]
    assert headers == [(p, f"Lesson · {p}") for p in range(2, 7)]
    # The kept first copy is then asked about like the first copy of any running header.
    assert [(text, context["reason"]) for text, context in judge.calls] == [
        ("Lesson · 1", "numbered_label"), ("Lesson · 1", "first_occurrence")]


def test_numbered_labels_judged_chrome_but_not_at_the_page_edge_stay_plain_text(tmp_path):
    """A line that differs on every page sits above the labels, so the rule cannot take them off the
    page edge: the judge's answer about the group does not turn them into headings, and it is asked once."""
    doc = pymupdf.open()
    for p in range(1, 7):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 30), f"Printed copy for reader {'ABCDEF'[p - 1]}{'XYZUVW'[p - 1]}", fontsize=8)
        page.insert_text((72, 60), f"Lesson · {p}", fontsize=13)
        page.insert_text((72, 150), "\n".join(f"Body line {p}-{i} with ordinary words." for i in range(8)),
                         fontsize=10.5)
    path = tmp_path / "doc.pdf"
    doc.save(str(path))
    doc.close()
    judge = _ReasonJudge({"numbered_label": 0.9})

    data, markdown = _convert(path, judge)

    assert [line for line in markdown.splitlines() if "Lesson" in line] == [f"Lesson · {p}" for p in range(1, 7)]
    assert [(text, context["reason"]) for text, context in judge.calls] == [("Lesson · 1", "numbered_label")]


def _rotated_cropped_table(path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for r in range(3):
        for c in range(2):
            rect = pymupdf.Rect(150 + c * 100, 330 + r * 20, 250 + c * 100, 350 + r * 20)
            page.draw_rect(rect, width=0.6)
            page.insert_text((rect.x0 + 3, rect.y0 + 12), f"C{r}{c}", fontsize=10)
    page.set_rotation(90)
    doc.xref_set_key(page.xref, "CropBox", "[30 42 570 792]")
    doc.save(str(path))
    doc.close()
    return path


def test_cropbox_is_restored_on_the_page_it_is_given_not_on_the_loaders_copy(tmp_path):
    """A lane may read a page's text and tables from a second handle on the file (hidden text
    removed); find_tables() deletes the CropBox of that handle's rotated page, which must come back."""
    path = _rotated_cropped_table(tmp_path / "doc.pdf")
    loader = PDFLoader(path)
    second = pymupdf.open(path)
    try:
        page = second[0]
        page.find_tables()
        assert second.xref_get_key(page.xref, "CropBox")[0] == "null"  # PyMuPDF's side effect

        loader._restore_cropbox(page)

        assert second.xref_get_key(page.xref, "CropBox") == ("array", "[30 42 570 792]")
    finally:
        second.close()
        loader.close()


def _hidden_title_then_running_header(path):
    """Page 1 holds the title "Quarterly Risk Review" only as invisible text (render mode 3, as in
    hidden text); pages 2-6 have it as their 9pt running header."""
    doc = pymupdf.open()
    for p in range(1, 7):
        page = doc.new_page(width=595, height=842)
        if p == 1:
            page.insert_text((72, 80), "Quarterly Risk Review", fontsize=20, render_mode=3)
        else:
            page.insert_text((72, 40), "Quarterly Risk Review", fontsize=9)
        page.insert_text((72, 150), "\n".join(f"Body line {p}-{i} with ordinary words." for i in range(8)),
                         fontsize=10.5)
    doc.save(str(path))
    doc.close()
    return path


class _CleanTextLoader(PDFLoader):
    """Reads every page's text from a second handle on the file whose page 1 lost its invisible
    title, as a lane that removes hidden text does."""

    @contextmanager
    def _text_page(self, page, *args, **kwargs):
        second = pymupdf.open(self.pdf_path)
        try:
            source = second[page.number]
            if page.number == 0:
                source.add_redact_annot(pymupdf.Rect(60, 50, 400, 90))
                source.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
            yield source
        finally:
            second.close()


def test_running_headers_are_read_from_the_page_the_text_comes_from(tmp_path):
    """The running header/footer pass reads the same page object as text extraction: text the
    extracted page does not have (hidden text) is not "content elsewhere", so the running header's
    first copy stays and the title is not lost."""
    loader = _CleanTextLoader(_hidden_title_then_running_header(tmp_path / "doc.pdf"))
    try:
        data = loader.convert_to_json(extract_images=False, show_progress=False)
    finally:
        loader.close()

    kept = [(item["page"], item["type"]) for item in data["content"] if "Quarterly Risk Review" in item["content"]
            and item["type"] not in ("text:header", "text:footer")]
    assert kept == [(2, "text:normal")]


class _SwitchingPage:
    """Like PR #18's VisibleTextPage: delegates to a page and, once its tables are found, reads
    from a full copy of the page appended to a second handle on the file (so ``number`` and
    ``xref`` change), finding the tables again there."""

    def __init__(self, page, second):
        self._page, self._second = page, second

    def __getattr__(self, name):
        return getattr(self._page, name)

    def find_tables(self, *args, **kwargs):
        self._page.find_tables(*args, **kwargs)
        self._second.fullcopy_page(self._page.number)
        self._page = self._second[-1]
        return self._page.find_tables(*args, **kwargs)


class _SwitchingLoader(PDFLoader):
    @contextmanager
    def _text_page(self, page, *args, **kwargs):
        second = pymupdf.open(self.pdf_path)
        try:
            yield _SwitchingPage(page, second)
        finally:
            second.close()


def _rotated_cropped_pages_with_a_table(path, pages=4):
    """``pages`` pages displayed sideways (/Rotate 90) with their own CropBox, each with the running
    header "ACME Quarterly Report" at the displayed top, body lines and its bare page number at the
    displayed bottom; page 1 also holds a ruled table with a note beside it."""
    doc = pymupdf.open()
    for p in range(1, pages + 1):
        page = doc.new_page(width=595, height=842)
        if p == 1:
            for r in range(3):
                for c in range(2):
                    rect = pymupdf.Rect(150 + c * 100, 330 + r * 20, 250 + c * 100, 350 + r * 20)
                    page.draw_rect(rect, width=0.6)
                    page.insert_text((rect.x0 + 3, rect.y0 + 12), f"C{r}{c}", fontsize=10)
            page.insert_textbox(pymupdf.Rect(360, 332, 440, 400), "NOTE beside the table.", fontsize=8)
        page.set_rotation(90)
        to_page = page.derotation_matrix  # displayed -> unrotated page coordinates

        def put(x, y, text, size):
            page.insert_text(pymupdf.Point(x, y) * to_page, text, fontsize=size, rotate=90)

        put(72, 70, "ACME Quarterly Report", 9)
        for i in range(6):
            put(72, 150 + 14 * i, f"Body line {p}-{i} with ordinary words.", 10.5)
        put(400, 545, f"{p}", 9)
        doc.xref_set_key(page.xref, "CropBox", "[30 42 570 792]")
    doc.save(str(path))
    doc.close()
    return path


def test_page_state_follows_the_page_index_when_text_is_read_from_a_copy(tmp_path):
    """When the page object the text is read from switches to a copy with another number and
    xref (PR #18), the page keeps its own CropBox, its own running header and page number, and its
    note; tables found on the copy are not mapped with the frame of the page that was uncropped."""
    loader = _SwitchingLoader(_rotated_cropped_pages_with_a_table(tmp_path / "doc.pdf"))
    try:
        data = loader.convert_to_json(extract_images=False, show_progress=False)
        first = loader.doc[0]
        assert loader.doc.xref_get_key(first.xref, "CropBox") == ("array", "[30 42 570 792]")
    finally:
        loader.close()

    footers = [(item["page"], item["content"]) for item in data["content"] if item["type"] == "text:footer"]
    assert footers == [(p, str(p)) for p in range(1, 5)]
    text = " ".join(pdf_to_markdown(data).split())
    assert text.count("ACME Quarterly Report") == 1
    assert text.count("NOTE beside the table.") == 1
    assert all(f"C{r}{c}" in text for r in range(3) for c in range(2))
    assert all(f"Body line {p}-{i} with ordinary words." in text for p in range(1, 5) for i in range(6))


class _SingleBlockLoader(PDFLoader):
    """The loader's own text source (PR #18's, which discards its shared page copies when a block
    ends), refusing to be entered while another block is open."""

    _open = False

    @contextmanager
    def _text_page(self, page, *args, **kwargs):
        assert not self._open, "_text_page entered while another block is open"
        self._open = True
        try:
            with super()._text_page(page, *args, **kwargs) as text_page:
                yield text_page
        finally:
            self._open = False


def test_text_page_blocks_are_never_nested(tmp_path):
    """The document-wide passes (running headers, first text page) read every page through
    ``_text_page`` before a page's own block opens, never inside it."""
    path = _heading_on_two_of_four_pages(tmp_path / "doc.pdf")
    loader = _SingleBlockLoader(path)
    try:
        data = loader.convert_to_json(extract_images=False, show_progress=False)
    finally:
        loader.close()

    headers = [(item["page"], item["content"]) for item in data["content"] if item["type"] == "text:header"]
    assert headers == [(2, "ACME Corp"), (3, "ACME Corp"), (4, "ACME Corp")]
