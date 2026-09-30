"""E2E tests for the PDF text layer (lane pdftext): real text must not be silently lost, duplicated
or turned into bogus structure by table-overlap suppression, rotated pages, running header/footer
removal or overprinted glyphs.

Every input is built at test time (``builders_pdftext.py``, ported from the reviewers' probes) and
every assertion is about what the ``doc2mark`` CLI writes. Nothing is mocked: LibreOffice and
Tesseract are the real tools. Body lines carry unique ``BODY<page>x<line>`` tokens, so "every body
line exactly once" is checked alongside each defect.
"""

import re
import shutil

import pytest

from tests.e2e import builders_pdftext as B
from tests.e2e import pdfgen


def _plain(markdown: str) -> str:
    """The Markdown as plain text: backslash escapes and ``*`` emphasis removed, whitespace collapsed."""
    text = re.sub(r"\\(.)", r"\1", markdown).replace("*", "")
    return re.sub(r"\s+", " ", text)


def _count(markdown: str, phrase: str) -> int:
    """Occurrences of ``phrase`` as a whole token sequence in the plain text of ``markdown``."""
    return len(re.findall(rf"(?<!\w){re.escape(phrase)}(?!\w)", _plain(markdown)))


def _content_lines(markdown: str) -> list:
    """Non-empty lines without page markers, heading/list/quote markers, escapes or emphasis."""
    lines = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line or line.startswith("<!--"):
            continue
        line = re.sub(r"^(?:#{1,6}\s+|[-*+]\s+|>\s*)", "", line)
        line = re.sub(r"\\(.)", r"\1", line).replace("*", "").strip()
        if line:  # a line that was only emphasis markup carries no text
            lines.append(line)
    return lines


def _assert_body_intact(markdown: str, pages: int, lines: int = 10) -> None:
    missing_or_duplicated = {
        token: _count(markdown, token)
        for token in (f"BODY{p}x{i}" for p in range(1, pages + 1) for i in range(lines))
        if _count(markdown, token) != 1
    }
    assert not missing_or_duplicated, f"body lines lost or duplicated: {missing_or_duplicated}\n{markdown}"


def _tables(markdown: str) -> int:
    """Tables in ``markdown``: pipe tables (one ``| --- |`` separator line each) and HTML tables."""
    separators = [line for line in markdown.splitlines() if re.fullmatch(r"\|(?: ?:?-{3,}:? ?\|)+", line.strip())]
    return len(separators) + markdown.count("<table")


def _chrome_residue(markdown: str) -> list:
    """Lines of a BODY-only test document that are not body text, i.e. leftover page furniture."""
    return [line for line in _content_lines(markdown) if not line.startswith("BODY")]


# ------------------------------------------------------------------------------------------------
# H-F1 / T5: suppress only the text inside a table, not the whole PyMuPDF block that touches it
# ------------------------------------------------------------------------------------------------

def test_captions_and_notes_touching_word_tables_are_kept_once(run_cli, require_tool, e2e_dir):
    require_tool("soffice")
    pdf = B.docx_to_pdf(B.word_tables_with_captions(e2e_dir / "tables.docx"))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    for phrase in [
        "CAPTION-A Table 1: Revenue by region (USD m)",
        "NOTE-A Figures are unaudited and subject to change.",
        "BODY-A The next section discusses costs in more detail for each segment of the business.",
        "HEADING-B Cost Analysis",
        "NOTE-B Costs exclude one-off restructuring charges.",
        "CAPTION-C Revenue split by channel",
        "NOTE-C Source: internal management accounts.",
        "BODY-C Closing remarks for the quarter follow in the outlook section of this report.",
    ]:
        assert _count(result.markdown, phrase) == 1, f"{phrase!r}\n{result.describe()}"
    for label in "ABC":
        for row in range(1, 4):
            for col in range(3):
                cell = f"{label}R{row}C{col}"
                assert _count(result.markdown, cell) == 1, f"{cell!r}\n{result.describe()}"


def test_paragraph_below_table_in_sample_pdf_is_kept(run_cli, sample_documents_dir):
    result = run_cli(sample_documents_dir / "complex-tables" / "complex_table_test.pdf")

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, "This table demonstrates:") == 1, result.describe()


@pytest.mark.parametrize("variant, origin", [
    ("right-4", (174, 50)),
    ("right-16", (186, 50)),
    ("right-40", (210, 50)),
    ("below-4", (40, 76)),
])
def test_title_beside_a_logo_box_is_kept(run_cli, e2e_dir, variant, origin):
    title = f"Quarterly Risk Review {variant}"
    pdf = B.logo_and_title(e2e_dir / "logo.pdf", title, origin)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, title) == 1, result.describe()
    for i in range(12):
        assert _count(result.markdown, f"BODY1x{i}") == 1, result.describe()


def test_slide_title_sharing_a_block_with_the_logo_is_kept(run_cli, e2e_dir):
    label, title, subtitle = "20 / 模組介紹：會議模組", "會議模組", "協助企業把會議從口頭討論轉為可管理的組織紀錄"
    pdf = B.slide_with_logo_block(e2e_dir / "slide.pdf", [
        ((1100, 62), label, 22),
        ((48, 150), title, 60),
        ((349, 145), subtitle, 30),
    ])

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    squashed = re.sub(r"\s+", "", _plain(result.markdown))
    assert squashed.count("模組介紹：會議模組") == 1, result.describe()
    assert squashed.count("會議模組") == 2, result.describe()  # once in the label, once as the title
    assert squashed.count(subtitle) == 1, result.describe()


@pytest.mark.parametrize("gap", [1.5, 2.0, 2.5])
def test_caption_just_above_a_table_rule_is_kept(run_cli, e2e_dir, gap):
    caption = f"Table 1: Quarterly results at {gap} pt"
    pdf = B.caption_just_above_table(e2e_dir / "caption.pdf", caption, gap)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, caption) == 1, result.describe()


# ------------------------------------------------------------------------------------------------
# T6: rotated pages compare table and text boxes in one coordinate space
# ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotated_page_emits_table_once_and_keeps_the_note(run_cli, e2e_dir, rotation):
    cells = [[f"R{rotation}H1", f"R{rotation}H2"],
             [f"R{rotation}V11", f"R{rotation}V12"],
             [f"R{rotation}V21", f"R{rotation}V22"]]
    note = f"NOTE{rotation} figures exclude VAT and are unaudited."
    pdf = B.rotated_table_with_note(e2e_dir / "rotated.pdf", rotation, cells, note)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, note) == 1, result.describe()
    for row in cells:
        for cell in row:
            assert _count(result.markdown, cell) == 1, f"{cell!r}\n{result.describe()}"


@pytest.mark.parametrize("rotation, cropbox, mediabox", [
    (90, "[30 42 570 792]", "[0 0 595 842]"),
    (180, "[30 42 570 792]", "[0 0 595 842]"),
    (270, "[30 42 570 792]", "[0 0 595 842]"),
    (90, "[-6 -6 601 848]", "[0 0 595 842]"),         # CropBox reaching outside the MediaBox
    (270, "[570 792 30 42]", "[0 0 595 842]"),        # corners listed the other way round
    (90, "[130 242 670 992]", "[100 200 695 1042]"),  # MediaBox away from the origin
    (180, "[0 0 595 842]", "[0 0 595 842]"),          # CropBox equal to the MediaBox
    (0, "[30 42 570 792]", "[0 0 595 842]"),
])
def test_rotated_cropped_page_emits_table_once_and_keeps_the_note(run_cli, e2e_dir, rotation, cropbox, mediabox):
    """PyMuPDF reports the table box of a rotated, cropped page shifted by the crop: the box must be
    brought into the text's frame, so the table's text is emitted once and the note beside it is kept."""
    cells = [[f"K{rotation}H1", f"K{rotation}H2"],
             [f"K{rotation}V11", f"K{rotation}V12"],
             [f"K{rotation}V21", f"K{rotation}V22"]]
    note = f"NOTE{rotation} figures exclude VAT and are unaudited."
    pdf = B.rotated_cropped_table(e2e_dir / "cropped.pdf", rotation, cells, note, cropbox, mediabox)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, note) == 1, result.describe()
    for row in cells:
        for cell in row:
            assert _count(result.markdown, cell) == 1, f"{cell!r}\n{result.describe()}"


@pytest.mark.parametrize("rotation, cropbox, parent_cropbox", [
    (90, "[30 42 570 792 0]", None),   # five numbers: MuPDF reads the first four
    (270, "[30 42 570 792 0]", None),
    (90, "[30 42 570 792]", "[0 0 595]"),  # a /Pages CropBox that is not a rectangle
])
def test_rotated_page_with_unreadable_boxes_still_hides_what_its_cropbox_hides(run_cli, e2e_dir, rotation,
                                                                                cropbox, parent_cropbox):
    """Page boxes this loader cannot read switch table suppression off (fail safe), but the page's own
    CropBox still comes back after table detection: text it hides stays hidden, as in any viewer."""
    cells = [["U-H1", "U-H2"], ["U-V11", "U-V12"], ["U-V21", "U-V22"]]
    note = f"NOTE{rotation} kept beside the table."
    pdf = B.rotated_cropped_table(e2e_dir / "unreadable.pdf", rotation, cells, note, cropbox,
                                  parent_cropbox=parent_cropbox, hidden="HIDDEN slug outside the crop")

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, "HIDDEN slug outside the crop") == 0, result.describe()
    assert _count(result.markdown, note) == 1, result.describe()
    _assert_body_intact(result.markdown, 1, lines=8)
    for row in cells:
        for cell in row:
            assert _count(result.markdown, cell) >= 1, f"{cell!r}\n{result.describe()}"


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_page_whose_cropbox_holds_an_indirect_number_keeps_the_note(run_cli, e2e_dir, rotation):
    """A CropBox array may hold indirect numbers (``[12 0 R 50 570 800]``); the page is still cropped."""
    cells = [["J-H1", "J-H2"], ["J-V11", "J-V12"], ["J-V21", "J-V22"]]
    note = f"NOTE{rotation} kept beside an indirectly cropped table."
    pdf = B.rotated_cropped_table(e2e_dir / "indirect.pdf", rotation, cells, note, "[30 42 570 792]",
                                  indirect_crop_x0=True)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, note) == 1, result.describe()
    for row in cells:
        for cell in row:
            assert _count(result.markdown, cell) == 1, f"{cell!r}\n{result.describe()}"


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_cropped_page_with_hidden_text_emits_its_table_once(run_cli, e2e_dir, rotation):
    """Hidden text on a page makes the text path read the page without it (PR #18). The table of a
    rotated page with its own CropBox is still found and suppressed in one frame: every cell once,
    the note beside the table once, the hidden line nowhere."""
    cells = [["HT-H1", "HT-H2"], ["HT-V11", "HT-V12"], ["HT-V21", "HT-V22"]]
    note = f"NOTE{rotation} beside the table."
    pdf = B.rotated_cropped_table(e2e_dir / "hidden.pdf", rotation, cells, note, "[30 42 570 792]",
                                  invisible="Hidden sentence nobody sees")

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, note) == 1, result.describe()
    assert _count(result.markdown, "Hidden sentence nobody sees") == 0, result.describe()
    for row in cells:
        for cell in row:
            assert _count(result.markdown, cell) == 1, f"{cell!r}\n{result.describe()}"


@pytest.mark.parametrize("rotation", [90, 270])
def test_rotated_cropped_borderless_statement_is_one_table(run_cli, e2e_dir, rotation):
    """A borderless statement is found by PyMuPDF's text strategy, a second find_tables() call (PR #15).
    On a landscape page stored sideways with its own CropBox both calls read the page in the same
    frame: one table, every label and figure once, the title and the note under it once."""
    note = f"RS-NOTE{rotation}: figures are audited."
    pdf = B.rotated_cropped_statement(e2e_dir / "statement.pdf", rotation, "[20 30 575 812]", note)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _tables(result.markdown) == 1, result.describe()
    for row in B.ROTATED_STATEMENT_ROWS:
        for token in row:
            assert _count(result.markdown, token) == 1, f"{token!r}\n{result.describe()}"
    assert _count(result.markdown, "Statement of income (USD thousands)") == 1, result.describe()
    assert _count(result.markdown, note) == 1, result.describe()


@pytest.mark.parametrize("rotation", [90, 270])
def test_rotated_page_with_inherited_cropbox_keeps_the_notes_around_its_table(run_cli, e2e_dir, rotation):
    cells = [["I-H1", "I-H2"], ["I-A1", "I-A2"], ["I-B1", "I-B2"]]
    pdf = B.rotated_table_with_inherited_cropbox(e2e_dir / "inherited.pdf", rotation, cells)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    for _, _, note in B.SIDE_NOTES:
        assert _count(result.markdown, note) == 1, f"{note!r}\n{result.describe()}"
    for row in cells:
        for cell in row:
            assert _count(result.markdown, cell) >= 1, f"{cell!r}\n{result.describe()}"


@pytest.mark.parametrize("mediabox", ["[0 -842 595 0]", "[100 200 695 1042]"])
def test_rotated_page_with_offset_mediabox_emits_table_once(run_cli, e2e_dir, mediabox):
    cells = [["M-H1", "M-H2"], ["M-V11", "M-V12"], ["M-V21", "M-V22"]]
    pdf = B.rotated_table_on_offset_mediabox(e2e_dir / "offset.pdf", cells, "NOTE-X keep me once", mediabox)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, "NOTE-X keep me once") == 1, result.describe()
    for row in cells:
        for cell in row:
            assert _count(result.markdown, cell) == 1, f"{cell!r}\n{result.describe()}"


@pytest.mark.parametrize("rotation", [90, 270])
def test_sideways_landscape_table_is_emitted_once(run_cli, e2e_dir, rotation):
    cells = [[f"S{rotation}H{c}" for c in range(3)],
             [f"S{rotation}A{c}" for c in range(3)],
             [f"S{rotation}B{c}" for c in range(3)]]
    pdf = B.sideways_landscape_table(e2e_dir / "landscape.pdf", rotation, cells)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    for row in cells:
        for cell in row:
            assert _count(result.markdown, cell) == 1, f"{cell!r}\n{result.describe()}"


# ------------------------------------------------------------------------------------------------
# H-F10: page numbers and running headers are recognised and never become structure
# ------------------------------------------------------------------------------------------------

# Footers that carry text besides the page number keep their first copy (verbatim first). A
# number labelled without a page word ("3 | ACME Corp") is not a page number by rule: see
# test_labels_numbered_like_the_pages_are_kept_on_every_page.
MIXED_FOOTER_FORMS = {"ACME | Page N"}


@pytest.mark.parametrize("form", [form for form in B.PAGE_NUMBER_FORMS if form != "N | ACME"])
def test_page_number_footer_is_dropped_and_never_restructured(run_cli, e2e_dir, form):
    render = B.PAGE_NUMBER_FORMS[form]
    pdf = B.paged_document(e2e_dir / "footer.pdf", 6, B.footer_decorator(render))

    result = run_cli(pdf, fmt="both")

    assert result.exit_code == 0, result.describe()
    kept = [render(1, 6)] if form in MIXED_FOOTER_FORMS else []
    assert _chrome_residue(result.markdown) == kept, result.describe()
    assert [line for line in result.markdown.splitlines() if line.strip() and not line.startswith(("BODY", "<!--"))] \
        == kept, result.describe()  # the kept copy is plain text: no list, heading or [^N]: markup
    _assert_body_intact(result.markdown, 6)
    footers = {item["page"]: item["content"].strip()
               for item in result.json["json_content"] if item["type"] == "text:footer"}
    first_footer_page = 2 if kept else 1
    assert footers == {p: render(p, 6) for p in range(first_footer_page, 7)}, result.describe()


@pytest.mark.parametrize("form, kept_pages", [("ACME Corp | Page {p}", [1]), ("{p} | ACME Corp", range(1, 7))])
def test_heading_sized_footer_with_a_number_stays_plain_text(run_cli, e2e_dir, form, kept_pages):
    """A page number with a page word keeps only its first copy; one labelled without it is kept on
    every page. Either way the kept lines are plain text, never headings."""
    pdf = B.paged_document(e2e_dir / "big-footer.pdf", 6, B.footer_decorator(
        lambda p, n: form.format(p=p), font="hebo", size=13, x=230, y=812))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    kept = [re.sub(r"\\(.)", r"\1", line) for line in result.markdown.splitlines() if "ACME" in line]
    assert kept == [form.format(p=p) for p in kept_pages], result.describe()
    _assert_body_intact(result.markdown, 6)


def test_bold_page_numbers_are_dropped(run_cli, e2e_dir):
    pdf = B.paged_document(e2e_dir / "bold.pdf", 6,
                           B.footer_decorator(B.PAGE_NUMBER_FORMS["bare"], font="hebo", x=290, y=815))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _chrome_residue(result.markdown) == [], result.describe()
    _assert_body_intact(result.markdown, 6)


def test_running_header_with_page_number_on_one_line_keeps_only_its_first_copy(run_cli, e2e_dir):
    pdf = B.paged_document(e2e_dir / "header.pdf", 6, B.footer_decorator(
        lambda p, n: f"ACME Corp Annual Report 2024          Page {p}", x=72, y=50))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _chrome_residue(result.markdown) == ["ACME Corp Annual Report 2024          Page 1"], result.describe()
    _assert_body_intact(result.markdown, 6)


@pytest.mark.parametrize("form", list(B.EUROPEAN_PAGE_NUMBER_FORMS))
def test_european_page_number_footer_is_dropped(run_cli, e2e_dir, form):
    pdf = B.paged_document(e2e_dir / "footer.pdf", 6, B.footer_decorator(B.EUROPEAN_PAGE_NUMBER_FORMS[form]))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _chrome_residue(result.markdown) == [], result.describe()
    _assert_body_intact(result.markdown, 6)


@pytest.mark.parametrize("first", [1124, 245])
def test_page_numbers_of_an_excerpt_far_from_one_are_dropped(run_cli, e2e_dir, first):
    """A journal article or book excerpt printed with its original page numbers (1124, 1125, ...)."""
    pdf = B.paged_document(e2e_dir / "excerpt.pdf", 12, B.footer_decorator(lambda p, n: f"{first + p - 1}", x=290, y=815))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _chrome_residue(result.markdown) == [], result.describe()
    _assert_body_intact(result.markdown, 12)


def test_page_numbers_right_under_the_text_or_higher_on_the_first_page_are_dropped(run_cli, e2e_dir):
    """Full pages exported by Word or LibreOffice leave 0-5 pt between the last line and the page
    number, and a first page may print its number higher than the others (fail-1.docx)."""
    gaps = [None, 0.5, 1.5, 2.5, 3.5, 5.0, None, None]
    pdf = B.tight_footer_document(e2e_dir / "tight.pdf", gaps)

    result = run_cli(pdf, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert _chrome_residue(result.markdown) == [], result.describe()
    _assert_body_intact(result.markdown, len(gaps), lines=12)
    footers = {item["page"]: item["content"].strip()
               for item in result.json["json_content"] if item["type"] == "text:footer"}
    assert footers == {p: str(p) for p in range(1, len(gaps) + 1)}, result.describe()


@pytest.mark.parametrize("label, page", [("(1)", 1), ("[1]", 1), ("1", 1), ("(5)", 5)])
def test_number_ending_a_page_without_page_number_is_kept(run_cli, e2e_dir, label, page):
    """A page with no page number of its own may end with a number that equals its page number, such
    as an equation number at the right margin: it does not continue the document's page numbering."""
    pdf = B.equation_number_page(e2e_dir / "equation.pdf", label, page)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, label) == 2, result.describe()  # the sentence citing it, and the number
    others = {str(p) for p in range(1, 9) if p != page}
    assert [line for line in _content_lines(result.markdown) if line in others] == [], result.describe()
    _assert_body_intact(result.markdown, 8, lines=12)


def test_page_numbers_of_a_word_report_never_reach_the_text(run_cli, require_tool, sample_documents_dir, e2e_dir):
    require_tool("soffice")
    docx = e2e_dir / "fail-1.docx"
    shutil.copyfile(sample_documents_dir / "fail-1.docx", docx)
    pdf = B.docx_to_pdf(docx)

    result = run_cli(pdf, fmt="both")

    assert result.exit_code == 0, result.describe()
    items = result.json["json_content"]
    leaked = [(item["page"], item["type"]) for item in items
              if item["type"] not in ("text:header", "text:footer")
              and str(item["page"]) in {line.strip() for line in item["content"].splitlines()}]
    assert leaked == [], f"page numbers left in the text: {leaked}\n{result.describe()}"
    # Most pages print their number in the footer; LibreOffice may put the first pages' at the top.
    numbered = {item["page"] for item in items
                if item["type"] in ("text:header", "text:footer") and item["content"].strip().isdigit()}
    assert len(numbered) >= 0.9 * max(item["page"] for item in items), result.describe()


@pytest.mark.parametrize("form", list(B.CJK_PAGE_NUMBER_FORMS))
def test_cjk_page_number_footer_is_dropped(run_cli, e2e_dir, form):
    pdf = B.paged_document(e2e_dir / "cjk.pdf", 6,
                           B.footer_decorator(B.CJK_PAGE_NUMBER_FORMS[form], font="china-t", x=260),
                           cjk_body=True)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _chrome_residue(result.markdown) == [], result.describe()
    _assert_body_intact(result.markdown, 6)


def test_per_chapter_running_headers_are_dropped_and_chapter_headings_kept(run_cli, e2e_dir):
    chapters = [("Introduction", 3), ("Market Review", 3), ("Financial Statements", 3), ("Risk Management", 3)]
    pdf = B.chaptered_document(e2e_dir / "book.pdf", chapters)

    result = run_cli(pdf, fmt="both")

    assert result.exit_code == 0, result.describe()
    # Each running header keeps its first copy (as plain text); every "Page N of M" goes.
    expected = []
    for k, (title, _) in enumerate(chapters, 1):
        expected += [f"Chapter {k} - {title}", f"{k} {title}"]
    assert _chrome_residue(result.markdown) == expected, result.describe()
    _assert_body_intact(result.markdown, 12)
    headers = [item for item in result.json["json_content"] if item["type"] == "text:header"]
    assert sorted(item["page"] for item in headers) == [2, 3, 5, 6, 8, 9, 11, 12], result.describe()


def test_title_after_a_cover_with_only_page_chrome_is_the_document_title(run_cli, e2e_dir):
    pdf = B.report_with_chrome_only_cover(e2e_dir / "cover.pdf", "Sustainability Review 2024")

    result = run_cli(pdf, fmt="both")

    assert result.exit_code == 0, result.describe()
    titles = [item["content"] for item in result.json["json_content"] if item["type"] == "text:title"]
    assert any("Sustainability Review 2024" in title for title in titles), result.describe()
    assert _chrome_residue(result.markdown) == ["ACME Corp - Annual Report", "Sustainability Review 2024"], \
        result.describe()


@pytest.mark.parametrize("rotation, cropbox", [
    (90, None),
    (90, "[20 30 575 812]"),
    (270, "[20 30 575 812]"),
    (90, "[0 0 595.5 842]"),  # CropBox reaching (just) outside the MediaBox, as real files do
    (90, "[-6 -6 601 848]"),
])
def test_page_chrome_on_rotated_pages_is_found_with_or_without_a_cropbox(run_cli, e2e_dir, rotation, cropbox):
    pdf = B.rotated_report(e2e_dir / "rotated-report.pdf", rotation, cropbox=cropbox)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _chrome_residue(result.markdown) == ["ACME Corp Annual Report 2024"], result.describe()
    _assert_body_intact(result.markdown, 5, lines=8)


def test_slide_footer_and_slide_number_sharing_a_block_are_dropped(run_cli, e2e_dir):
    pdf = B.slides_with_footer_block(e2e_dir / "deck.pdf", 8)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, "ACME Confidential") == 1, result.describe()  # first copy only
    assert [line for line in _content_lines(result.markdown) if line.isdigit()] == [], result.describe()
    for s in range(1, 9):
        assert _count(result.markdown, f"Slide {s} topic") == 1, result.describe()
        for i in range(3):
            assert _count(result.markdown, f"BODY{s}x{i}") == 1, result.describe()


def test_letterhead_table_repeated_on_every_page_is_kept_once(run_cli, e2e_dir):
    pdf = B.paged_document(e2e_dir / "letterhead.pdf", 6, B.letterhead_table)

    result = run_cli(pdf, fmt="both")

    assert result.exit_code == 0, result.describe()
    for row in B.LETTERHEAD_ROWS:
        for cell in row:
            assert _count(result.markdown, cell) == 1, f"{cell!r}\n{result.describe()}"
    _assert_body_intact(result.markdown, 6)
    tables = [item for item in result.json["json_content"] if "Doc ID: QMS-042 Rev 3" in item["content"]]
    assert len(tables) == 6, result.describe()  # the copies stay in the JSON, typed as page header


def test_word_report_keeps_repeated_table_header_and_drops_running_chrome(run_cli, require_tool, e2e_dir):
    require_tool("soffice")
    pdf = B.docx_to_pdf(B.word_report_with_chapter_headers(e2e_dir / "report.docx"))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    table_pages = [page for page in re.split(r"<!-- page \d+ -->", result.markdown) if "INV-" in page]
    assert table_pages, result.describe()
    for page in table_pages:  # the header row Word repeats on every page of the table stays on each
        for label in ("Date", "Description", "Amount", "Balance"):
            assert _count(page, label) == 1, f"table header {label!r} lost\n{result.describe()}"
    for k, chapter in enumerate(["Introduction", "Market Review", "Financial Statements", "Risk Management"], 1):
        assert _count(result.markdown, f"Chapter {k} - {chapter}") == 1, result.describe()  # first copy only
    assert not re.search(r"Page \d+ of \d+", result.markdown), result.describe()
    paragraphs = re.findall(r"Paragraph (\d+):", result.markdown)
    assert sorted(map(int, paragraphs)) == list(range(38)), result.describe()
    # LibreOffice may wrap the cell after "INV-", so allow a line break inside the number.
    assert len(set(re.findall(r"INV-?\s*(\d+)", result.markdown))) == 240, result.describe()


# ------------------------------------------------------------------------------------------------
# H-F2: only strong evidence removes a line; titles, headings, numbers and content stay
# ------------------------------------------------------------------------------------------------

def test_numbers_and_labels_in_the_margin_band_are_kept(run_cli, e2e_dir):
    pdf = B.paged_document(e2e_dir / "kpi.pdf", 6, B.kpi_tiles_and_chart)

    result = run_cli(pdf, fmt="both")

    assert result.exit_code == 0, result.describe()
    for p in range(1, 7):
        assert _count(result.markdown, str(120 + p)) == 1, result.describe()
        assert _count(result.markdown, str(37 + p)) == 1, result.describe()
    for token in ("2024", "new customers", "open tickets", "fiscal year"):
        assert _count(result.markdown, token) == 6, f"{token!r}\n{result.describe()}"
    page_numbers = {str(p) for p in range(1, 7)}
    assert [line for line in _content_lines(result.markdown) if line in page_numbers] == [], result.describe()
    _assert_body_intact(result.markdown, 6)
    # The chart's year labels repeat at the bottom of every page but are not page numbers:
    # they must stay content (the page numbers below them are the only footer).
    footers = [item["content"].strip() for item in result.json["json_content"]
               if item["type"] in ("text:header", "text:footer")]
    assert sorted(footers) == sorted(page_numbers), result.describe()


# A number alone that counts up with the pages is indistinguishable from a page number and is
# removed as one (see the excerpt test above). A label other than a page word keeps the line: it
# may be an ID, a per-page label or a page number, which only a boilerplate_judge can tell.
@pytest.mark.parametrize("first", [1001, 1])
@pytest.mark.parametrize("label", ["Invoice: {n}", "Receipt No: {n}", "Ticket | {n}", "Certificate · {n}"])
def test_sequential_ids_at_the_top_of_each_page_are_not_page_numbers(run_cli, e2e_dir, label, first):
    pdf = B.paged_document(e2e_dir / "ids.pdf", 6, B.footer_decorator(
        lambda p, n: label.format(n=first + p - 1), x=400, y=50, size=10))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    for p in range(1, 7):
        assert _count(result.markdown, label.format(n=first + p - 1)) == 1, result.describe()
    _assert_body_intact(result.markdown, 6)


@pytest.mark.parametrize("form, y, size", [
    ("Lesson · {p}", 50, 12),  # the reviewer's pr_lesson.pdf
    ("Ticket | {p}", 50, 10),
    ("Case · {p}", 50, 10),
    ("{p} | ACME Corp", 800, 9),
])
def test_labels_numbered_like_the_pages_are_kept_on_every_page(run_cli, e2e_dir, form, y, size):
    """A number labelled with anything but a page word may be a per-page label ("Lesson 3") as
    well as a page number ("3 | ACME Corp"): without a judge every copy is kept, as plain text."""
    pdf = B.paged_document(e2e_dir / "labels.pdf", 6, B.footer_decorator(
        lambda p, n: form.format(p=p), x=72, y=y, size=size))

    result = run_cli(pdf, fmt="both")

    assert result.exit_code == 0, result.describe()
    lines = [re.sub(r"\\(.)", r"\1", line) for line in result.markdown.splitlines()
             if line.strip() and not line.startswith(("BODY", "<!--"))]
    assert lines == [form.format(p=p) for p in range(1, 7)], result.describe()  # plain: no heading/list/footnote
    _assert_body_intact(result.markdown, 6)
    chrome = [item for item in result.json["json_content"] if item["type"] in ("text:header", "text:footer")]
    assert chrome == [], result.describe()


def test_page_number_sharing_its_row_with_a_numbered_label_is_dropped(run_cli, e2e_dir):
    """A footer row with a per-page label and the page number: the number goes, the label stays."""
    pdf = B.paged_document(e2e_dir / "label-row.pdf", 6, B.label_and_number_footer)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _chrome_residue(result.markdown) == [f"Lesson · {p}" for p in range(1, 7)], result.describe()
    _assert_body_intact(result.markdown, 6)


@pytest.mark.parametrize("first_day", [15, 1])
def test_dates_at_the_top_of_each_page_are_not_page_numbers(run_cli, e2e_dir, first_day):
    """Day headers such as 15/03 ... 20/03 have the N/M shape of "3/12", but a page number is never
    larger than the page count printed after it."""
    days = [f"{first_day + k:02d}/03" for k in range(6)]
    pdf = B.paged_document(e2e_dir / "days.pdf", 6, B.footer_decorator(
        lambda p, n: days[p - 1], x=72, y=50, size=10))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _chrome_residue(result.markdown) == days, result.describe()
    _assert_body_intact(result.markdown, 6)


def test_titles_repeated_on_consecutive_slides_are_kept(run_cli, e2e_dir):
    titles = ["Welcome", "Agenda", "Product Overview", "Product Overview", "Product Overview",
              "Customer Case Study", "Customer Case Study", "Customer Case Study", "Roadmap", "Questions"]
    pdf = B.slides_with_titles(e2e_dir / "slides.pdf", titles)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    for title in set(titles):
        assert _count(result.markdown, title) == titles.count(title), f"{title!r}\n{result.describe()}"
    _assert_body_intact(result.markdown, 10, lines=4)


@pytest.mark.parametrize("title_y, header_from, pages", [
    (80, 2, 6),   # title in the top margin band
    (130, 2, 6),  # title just below it
    (130, 4, 8),  # running header only from page 4 on (contents and summary pages before it)
])
def test_title_that_is_also_the_running_header_is_kept_once(run_cli, e2e_dir, title_y, header_from, pages):
    pdf = B.paged_document(e2e_dir / "title.pdf", pages,
                           B.title_then_running_header("Quarterly Risk Review", title_y, header_from))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, "Quarterly Risk Review") == 1, result.describe()
    _assert_body_intact(result.markdown, pages)


def test_hidden_title_does_not_take_the_running_header_with_it(run_cli, e2e_dir):
    """An invisible title over nothing the page shows is hidden text: the text path leaves it out
    (PR #18), and so must the running header/footer pass. The 9pt running header that repeats it then
    repeats no title of the document, so its first copy stays (verbatim first)."""
    pdf = B.paged_document(e2e_dir / "hidden-title.pdf", 6,
                           B.title_then_running_header("Quarterly Risk Review", hidden=True))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, "Quarterly Risk Review") == 1, result.describe()
    _assert_body_intact(result.markdown, 6)


def test_statement_title_is_kept_when_the_contents_page_lists_it(run_cli, e2e_dir):
    """A contents entry is not the statement's title: the running header's first copy stays."""
    pdf = B.statement_after_contents(e2e_dir / "report.pdf", 4)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, "Consolidated Balance Sheet") == 2, result.describe()  # contents + title
    assert _count(result.markdown, "ACME CORPORATION") == 1, result.describe()
    assert _count(result.markdown, "(in thousands of USD)") == 1, result.describe()
    _assert_body_intact(result.markdown, 5)


def test_statement_title_and_unit_note_repeated_on_every_page_are_kept_once(run_cli, e2e_dir):
    pdf = B.statement_pages(e2e_dir / "statement.pdf", 4)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    for line in B.STATEMENT_HEADER:
        assert _count(result.markdown, line) == 1, f"{line!r}\n{result.describe()}"
    _assert_body_intact(result.markdown, 4)
    assert [line for line in _content_lines(result.markdown) if line.isdigit()] == [], result.describe()


def test_title_repeated_at_the_top_of_every_page_stays_a_title_once(run_cli, e2e_dir):
    pdf = B.paged_document(e2e_dir / "title-every-page.pdf", 6, B.title_on_every_page("Sustainability Review 2024"))

    result = run_cli(pdf, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, "Sustainability Review 2024") == 1, result.describe()
    titles = [item["page"] for item in result.json["json_content"]
              if item["type"] == "text:title" and "Sustainability Review 2024" in item["content"]]
    assert titles == [1], result.describe()
    _assert_body_intact(result.markdown, 6)


def test_one_string_table_header_row_repeated_on_every_page_is_kept(run_cli, e2e_dir):
    pdf = B.statement_with_one_string_rows(e2e_dir / "account.pdf", 6)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    pages = re.split(r"<!-- page \d+ -->", result.markdown)
    assert len(pages) == 6, result.describe()
    for page in pages:
        assert _count(page, "Description") == 1, result.describe()
    assert len(set(re.findall(r"TXN\d{4}", result.markdown))) == 150, result.describe()
    assert _count(result.markdown, "Account Statement - ACME Bank") == 1, result.describe()


def test_heading_at_the_top_of_two_of_four_pages_is_kept(run_cli, e2e_dir):
    pdf = B.paged_document(e2e_dir / "heading.pdf", 4, B.heading_on_pages("Summary of Findings", (1, 3)))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, "Summary of Findings") == 2, result.describe()
    _assert_body_intact(result.markdown, 4)


def test_disclaimer_sentence_repeated_in_the_footer_is_kept_once(run_cli, e2e_dir):
    disclaimer = "Past performance is not a reliable indicator of future results."
    pdf = B.paged_document(e2e_dir / "disclaimer.pdf", 6,
                           B.footer_decorator(lambda p, n: disclaimer, x=72, y=790, size=8))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, disclaimer) == 1, result.describe()
    _assert_body_intact(result.markdown, 6)


def test_line_repeated_in_the_body_band_is_kept_on_every_page(run_cli, e2e_dir):
    pdf = B.paged_document(e2e_dir / "body-band.pdf", 6,
                           B.footer_decorator(lambda p, n: "Reviewed by the audit committee", x=72, y=420, size=10.5))

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    assert _count(result.markdown, "Reviewed by the audit committee") == 6, result.describe()
    _assert_body_intact(result.markdown, 6)


# ------------------------------------------------------------------------------------------------
# H-F6: overprinted / fake-bold text is emitted once; real repeats stay
# ------------------------------------------------------------------------------------------------

def test_overprinted_text_is_emitted_once_and_real_repeats_are_kept(run_cli, e2e_dir):
    pdf = B.overprinted_text(e2e_dir / "overprint.pdf")

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    markdown = result.markdown
    for phrase in ("Quarterly Revenue Summary", "Shadowed Heading Text", "資料治理與流程銜接", "Triple printed line",
                   "TeamSync AI focuses on data governance, and more", "data governance,",
                   "This is important text here.", "important", "Votes: No No", "Scores: 10 10"):
        assert _count(markdown, phrase) == 1, f"{phrase!r}\n{result.describe()}"
    assert [line for line in _content_lines(markdown) if line == "No"] == ["No", "No"], result.describe()
    for token in [f"BODY1x{i}" for i in range(3)] + [f"BODY2x{i}" for i in range(3)]:
        assert _count(markdown, token) == 1, result.describe()


def test_overlapping_clipped_cells_are_not_taken_for_overprint(run_cli, e2e_dir):
    pdf = B.clipped_spreadsheet_cells(e2e_dir / "cells.pdf")

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    for left, right, _ in B.CLIPPED_CELLS:
        assert _count(result.markdown, left) == 1, f"{left!r}\n{result.describe()}"
        assert _count(result.markdown, right) == 1, f"{right!r}\n{result.describe()}"


@pytest.mark.parametrize("rotation", [0, 90])
def test_fonts_with_tall_metrics_keep_neighbouring_lines_and_find_page_chrome(run_cli, e2e_dir, rotation):
    """Word's Cambria-like font metrics make PyMuPDF report boxes ~5x taller than the text;
    geometry must come from the baselines, or neighbouring lines look like overprints."""
    pdf = B.tall_metrics_document(e2e_dir / "tall.pdf", 6, rotation)

    result = run_cli(pdf)

    assert result.exit_code == 0, result.describe()
    lines = _content_lines(result.markdown)
    assert lines.count("No") == 2, result.describe()
    assert "Total" in lines and "Total revenue grew" in lines, result.describe()
    expected = ["ACME Corp - Annual Report", "No", "No", "Total", "Total revenue grew"]
    if not rotation:
        expected.append("Table 7: Tall metrics caption")
        for cell in ("TH1", "TV11", "TV22"):
            assert _count(result.markdown, cell) == 1, f"{cell!r}\n{result.describe()}"
    residue = [line for line in _chrome_residue(result.markdown) if not line.startswith("|")]  # tables aside
    assert sorted(residue) == sorted(expected), result.describe()  # reading order on rotated pages is out of scope
    _assert_body_intact(result.markdown, 6)


# ------------------------------------------------------------------------------------------------
# R-F16: OCR text of page renders (at y=0) is page content, never a running header
# ------------------------------------------------------------------------------------------------

def test_identical_scanned_pages_keep_their_ocr_text(run_cli, require_tool, e2e_dir):
    require_tool("tesseract")
    pdf = pdfgen.image_pdf(e2e_dir / "scans.pdf", [
        "CLAIM FORM\nPOLICY 55201",
        "CLAIM FORM\nPOLICY 55201",
        "SECOND NOTICE 4711",
        "FINAL NOTICE 9034",
    ])

    result = run_cli(pdf, "--ocr", "tesseract", "--ocr-images")

    assert result.exit_code == 0, result.describe()
    plain = " ".join(result.markdown.split())
    assert plain.count("CLAIM FORM") == 2, result.describe()
    assert plain.count("POLICY 55201") == 2, result.describe()
    assert "SECOND NOTICE 4711" in plain and "FINAL NOTICE 9034" in plain, result.describe()
