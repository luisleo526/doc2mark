"""Fixture builders for the Markdown-structure E2E tests (``test_md.py``).

The inputs come from the text-structure review probes: synthetic PDFs drawn with
PyMuPDF at exact geometry, and realistic Word/PowerPoint files built with
python-docx / python-pptx and exported to PDF with LibreOffice (``office_to_pdf``),
which is how most real PDFs are made. Nothing from ``doc2mark`` is imported here.
Every builder writes to ``path`` and returns it as a ``Path``.
"""

import io
import random
import shutil
import subprocess
from pathlib import Path

import pymupdf
from PIL import Image

A4 = (595, 842)
LETTER = (612, 792)


def _new_page(doc, size=A4):
    return doc.new_page(width=size[0], height=size[1])


def _text(page, x, y, text, size=10.5, font="helv", **kwargs):
    """``insert_text`` with (x, y) as the baseline origin of the first line."""
    page.insert_text((x, y), text, fontsize=size, fontname=font, **kwargs)


def _save(doc, path) -> Path:
    doc.save(str(path))
    doc.close()
    return Path(path)


def _ruled_table(page, x0, y0, col_widths, row_height, rows, fontsize=9):
    """Draw a fully ruled table (one rectangle per cell) and return its bbox."""
    y = y0
    for row in rows:
        x = x0
        for width, value in zip(col_widths, row):
            rect = pymupdf.Rect(x, y, x + width, y + row_height)
            page.draw_rect(rect, color=(0, 0, 0), width=0.6)
            page.insert_text((rect.x0 + 3, rect.y0 + fontsize + 2), value, fontname="helv", fontsize=fontsize)
            x += width
        y += row_height
    return pymupdf.Rect(x0, y0, x0 + sum(col_widths), y)


def _png(width, height, color=(90, 140, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


# --- list and outline markers ------------------------------------------------------------------

MARKERS_BODY = (
    "The committee reviewed the operating results for the period and noted that\n"
    "revenue developed in line with expectations across all regions while costs\n"
    "remained under control. Further details are provided in the sections below\n"
    "together with the relevant tables and the management commentary for each\n"
    "business segment and the outlook for the remainder of the financial year."
)

# (text, font size, font): body text is 10.5pt Helvetica; "hebo" is Helvetica Bold.
MARKER_CASES = [
    ("1. Introduction", 13, "hebo"),
    ("1. Buy milk", 10.5, "helv"),
    ("1.2.3 Scope of Work", 12, "hebo"),
    ("1.1. Definitions", 12, "hebo"),
    ("2.1. The Supplier shall deliver the goods within 30 days", 10.5, "helv"),
    ("(a) first option", 10.5, "helv"),
    ("I. Background", 13, "hebo"),
    ("I. Background", 10.5, "helv"),
    ("II. Methods", 10.5, "helv"),
    ("a) The lessee shall pay rent monthly", 10.5, "helv"),
    ("A. Smith and B. Jones reviewed the draft", 10.5, "helv"),
    ("E. coli contamination was found in 3 samples", 10.5, "helv"),
    ("Step 1: Install the package", 10.5, "helv"),
    ("2024 revenue grew 12% year over year", 10.5, "helv"),
    ("3.5% of revenue came from Asia", 10.5, "helv"),
    ("Graph Neural Networks", 13, "hebo"),
    ("Image Classification Results", 13, "hebo"),
    ("Tablets were distributed to all students", 10.5, "helv"),
    ("Chartered accountants signed off the accounts", 10.5, "helv"),
    ("Fighting fraud remains a top priority", 10.5, "helv"),
    ("What is Retrieval-Augmented Generation?", 13, "hebo"),
    ("Property, Plant and Equipment", 13, "hebo"),
    ("Part II: Management Discussion and Analysis", 13, "hebo"),
    ("Section 5 applies to all employees", 10.5, "helv"),
]


def markers_pdf(path: Path) -> Path:
    """Numbered/lettered/roman markers, caption-prefix words and question/comma/colon headings,
    one short line each between 10.5pt body paragraphs (probe ``p01_markers``). The first case
    sits right under a paragraph, so MuPDF puts the heading into the paragraph's block."""
    doc = pymupdf.open()
    page = _new_page(doc)
    y = 60
    for index, (text, size, font) in enumerate(MARKER_CASES):
        if index in (0, 12):
            _text(page, 72, y, MARKERS_BODY)
            y += 75
        _text(page, 72, y, text, size, font)
        y += 26
        if y > 800:
            page = _new_page(doc)
            y = 60
    _text(page, 72, y + 10, MARKERS_BODY)
    return _save(doc, path)


MIDPARAGRAPH_LINES = [
    "Routine testing of the municipal water supply found that three samples",
    "taken from the northern reservoir exceeded the limit for bacteria; in each",
    "E. coli was the dominant species and the source was traced to a broken",
    "pipe near the treatment plant, which was repaired within two days.",
]


def midparagraph_letter_pdf(path: Path) -> Path:
    """One paragraph whose third line starts with ``E. coli`` (probe ``p15``)."""
    doc = pymupdf.open()
    _text(_new_page(doc), 72, 100, "\n".join(MIDPARAGRAPH_LINES))
    return _save(doc, path)


LETTER_LIST_LINES = ["a) first option on the table", "b) second option on the table", "c) third option on the table"]
PAGE_REFERENCE_LINE = "p. 12 of the appendix lists all monitoring sites"


def letter_list_pdf(path: Path) -> Path:
    """A real lettered list (consecutive a) b) c) lines in one paragraph block) and a body line
    that starts with a page reference ``p. 12``."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, "The contract offers the following options to the lessee this year:")
    _text(page, 72, 100, "\n".join(LETTER_LIST_LINES))
    _text(page, 72, 180, PAGE_REFERENCE_LINE)
    _text(page, 72, 220, "The committee will decide between the options at the next meeting.")
    return _save(doc, path)


# --- font-size, weight and case signals (probe p02) ---------------------------------------------

SIGNALS_PARA = (
    "Operating income increased compared with the prior year, driven by higher\n"
    "volumes in the industrial segment and lower freight costs. The board expects\n"
    "demand to remain stable in the coming quarters, subject to the usual risks\n"
    "described in the risk management section of this report and the notes."
)

BOLD_BODY_HEADINGS = ["Introduction", "1. Scope", "Methods and Data"]


def bold_body_size_headings_pdf(path: Path) -> Path:
    """Scenario A: bold headings at the same 11pt size as the body text."""
    doc = pymupdf.open()
    page = _new_page(doc, LETTER)
    y = 80
    for heading in BOLD_BODY_HEADINGS:
        _text(page, 72, y, heading, 11, "hebo")
        y += 30
        _text(page, 72, y, SIGNALS_PARA, 11)
        y += 80
    return _save(doc, path)


CHART_BODY_LINES = [
    "Revenue grew in every region",
    "Management remains cautious about the outlook",
    "see appendix for the full reconciliation",
]


def chart_labels_pdf(path: Path) -> Path:
    """Scenario B: many 7pt chart tick labels and a legend on a page whose body is 10pt."""
    doc = pymupdf.open()
    page = _new_page(doc, LETTER)
    _text(page, 72, 80, SIGNALS_PARA, 10)
    for i in range(12):
        _text(page, 80, 300 - i * 15, f"{i * 10}", 7)
    for i, quarter in enumerate(["Q1", "Q2", "Q3", "Q4", "Q1", "Q2", "Q3", "Q4"]):
        _text(page, 110 + i * 50, 320, quarter, 7)
    for i, label in enumerate(["North", "South", "East", "West"]):
        _text(page, 520, 150 + i * 12, label, 7)
    _text(page, 72, 360, CHART_BODY_LINES[0], 10)
    _text(page, 72, 390, "Source: company filings and internal estimates", 10)
    _text(page, 72, 420, CHART_BODY_LINES[1], 10)
    _text(page, 72, 450, CHART_BODY_LINES[2], 10)
    _text(page, 72, 490, SIGNALS_PARA, 10)
    return _save(doc, path)


TWO_LINE_TITLE = ["Enterprise AI", "Operating System"]
THREE_LINE_TITLE = ["A Practical Guide", "to Retrieval Augmented", "Generation Systems"]


def multiline_titles_pdf(path: Path) -> Path:
    """Scenario D: a 2-line 26pt bold title and a 3-line 20pt bold heading, one block each."""
    doc = pymupdf.open()
    page = _new_page(doc, LETTER)
    _text(page, 72, 90, "\n".join(TWO_LINE_TITLE), 26, "hebo")
    _text(page, 72, 200, "\n".join(THREE_LINE_TITLE), 20, "hebo")
    _text(page, 72, 330, SIGNALS_PARA, 10)
    return _save(doc, path)


DROP_CAP_TITLE = "Operating System Review"
DROP_CAP_REST = (
    "he company delivered record results this year, with\n"
    "revenue up across all segments and margins expanding\n"
    "for the third consecutive year despite inflation."
)


def drop_cap_pdf(path: Path) -> Path:
    """Scenario E: an 18pt bold title, then a paragraph that starts with a 44pt drop cap ``T``."""
    doc = pymupdf.open()
    page = _new_page(doc, LETTER)
    _text(page, 72, 120, DROP_CAP_TITLE, 18, "hebo")
    _text(page, 72, 196, "T", 44, "tibo")
    _text(page, 104, 166, DROP_CAP_REST, 10)
    _text(page, 72, 230, SIGNALS_PARA, 10)
    return _save(doc, path)


def superscripts_pdf(path: Path) -> Path:
    """Scenario F: 6pt superscripts right after body text (PyMuPDF flags them as superscript), and
    a line that starts with a raised 6pt footnote number."""
    doc = pymupdf.open()
    page = _new_page(doc, LETTER)

    def run(x, y, text, size=10):
        _text(page, x, y, text, size)
        return x + pymupdf.get_text_length(text, "helv", size)

    run(run(72, 100, "Energy is E = mc"), 96, "2", 6)
    x = run(run(72, 130, "The sample contained 10"), 126, "6", 6)
    run(x + 4, 130, " cells per litre")
    run(run(72, 160, "Net revenue was $1.2bn"), 156, "3", 6)
    # a footnote whose raised number starts the line: PyMuPDF does not flag the first span
    run(run(72, 186, "1", 6) + 1, 190, "Source: company filings and internal estimates")
    _text(page, 72, 230, SIGNALS_PARA, 10)
    return _save(doc, path)


ALLCAPS_LABELS = ["TOTAL ASSETS", "Q1 2024", "USD MILLIONS", "EBITDA", "NOTE 12"]
CJK_ACRONYM_LINES = ["本系統整合ERP與CRM資料", "權限控管與API整合", "導入後的管理"]
CJK_BODY_MARKER_LINES = ["附件資料已上傳至系統", "第三條規定之罰則適用於本法"]


def allcaps_labels_pdf(path: Path) -> Path:
    """Page 2 holds uppercase labels and CJK lines with Latin acronyms, all at the 10pt body
    size and regular weight (probe ``p02b``), plus CJK body lines that begin like explicit
    heading markers (``附件…``, ``第三條…``)."""
    doc = pymupdf.open()
    _text(_new_page(doc, LETTER), 72, 100, SIGNALS_PARA, 10)
    page = _new_page(doc, LETTER)
    _text(page, 72, 80, SIGNALS_PARA, 10)
    y = 160
    for label in ALLCAPS_LABELS + ["WARNING: DO NOT OPERATE WITHOUT TRAINING"]:
        _text(page, 72, y, label, 10)
        y += 30
    for line in CJK_ACRONYM_LINES + CJK_BODY_MARKER_LINES:
        _text(page, 72, y, line, 10, "china-t")
        y += 30
    _text(page, 72, y + 10, SIGNALS_PARA, 10)
    return _save(doc, path)


BOLD_LEAD_INS = ["Payment obligations", "Renewal and termination"]


def footnote_density_pdf(path: Path) -> Path:
    """A legal-style page with 36 superscript footnote markers and 8pt footnotes, then two bold
    10pt lead-in lines at body size (probe ``p02c``)."""
    words = "the lessee shall pay rent under this agreement subject to clause notice period".split()
    doc = pymupdf.open()
    page = _new_page(doc)
    y = 90
    for line in range(18):
        rng = random.Random(line)
        first = " ".join(rng.choice(words) for _ in range(6))
        second = " ".join(rng.choice(words) for _ in range(6))
        x = 72
        _text(page, x, y, first, 10)
        x += pymupdf.get_text_length(first, "helv", 10)
        _text(page, x, y - 4, str(line * 2 + 1), 6)
        x += 8
        _text(page, x, y, " " + second, 10)
        x += pymupdf.get_text_length(" " + second, "helv", 10)
        _text(page, x, y - 4, str(line * 2 + 2), 6)
        y += 14
    _text(page, 72, y + 20, BOLD_LEAD_INS[0], 10, "hebo")
    _text(page, 72, y + 50, BOLD_LEAD_INS[1], 10, "hebo")
    for k in range(10):
        _text(page, 72, 700 + k * 11, f"{k + 1} See clause {k + 3} of the master agreement dated 1 March 2020", 8)
    return _save(doc, path)


FOOTNOTE_LINES = [
    "1 See clause 3 of the master agreement dated 1 March 2020, which remains",
    "in force until the parties agree otherwise in writing.",
]


def multiline_footnote_pdf(path: Path) -> Path:
    """A 10pt body paragraph and a two-line 8pt footnote at the bottom of the page."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(
        page,
        72,
        80,
        "Body text of the agreement continues here with enough words to be the body.\n"
        "Second line of the body paragraph that keeps the average font size at ten.",
        10,
    )
    _text(page, 72, 760, "\n".join(FOOTNOTE_LINES), 8)
    return _save(doc, path)


BOLD_PHRASE_LINES = [
    "The auditors reviewed the quarterly figures in detail and concluded that",
    "the revenue target for the northern region was",
    "not met because of supply issues in the third quarter of the year.",
]
BOLD_NUMBERED_ITEMS = ["1. Scope", "2. Definitions"]
BOLD_BULLET_ITEMS = ["Deadline extended", "Budget approved"]


def bold_text_in_body_pdf(path: Path) -> Path:
    """Bold text that is not a heading: a paragraph whose middle line is set in bold, a block of
    two bold numbered items and a block of two bold bullets, all at the 10.5pt body size."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY)
    _text(page, 72, 170, BOLD_PHRASE_LINES[0])
    _text(page, 72, 184, BOLD_PHRASE_LINES[1], 10.5, "hebo")
    _text(page, 72, 198, BOLD_PHRASE_LINES[2])
    _text(page, 72, 250, "\n".join(BOLD_NUMBERED_ITEMS), 10.5, "hebo")
    _text(page, 72, 310, "\n".join("\u2022 " + item for item in BOLD_BULLET_ITEMS), 10.5, "hebo")
    _text(page, 72, 370, MARKERS_BODY)
    return _save(doc, path)


HEADING_WITH_MARKUP = "Results <Draft> and Q&A"
MIXED_FONT_PARTS = [("If x ", "helv"), ("<", "tiro"), ("y and y", "helv"), (">", "tiro"), (" z holds for every sample.", "helv")]
LINK_DEFINITION_LINE = "[1]: https://example.com/ref"
BACKSLASH_LINE = "Copy C:\\temp\\*.txt to \\\\server\\share before the audit"
COMPOUND_LINES = [
    "The board approved a well-",
    "known strategy for the non-",
    "current assets and for short- and long-",
    "term liabilities in the next year.",
]
COMPOUND_LINES_2 = [
    "The committee reviewed the decision-",
    "making process for the year-",
    "end closing of the accounts.",
]


def escaping_edge_cases_pdf(path: Path) -> Path:
    """A 16pt bold heading with ``<`` and ``&``, a line whose ``<``/``>`` are set in another font,
    a link-reference-definition-shaped line, a line with backslashes and a paragraph whose line
    ends are hyphens of compound words."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, HEADING_WITH_MARKUP, 16, "hebo")
    x = 72
    for text, font in MIXED_FONT_PARTS:
        _text(page, x, 120, text, 10.5, font)
        x += pymupdf.get_text_length(text, font, 10.5)
    _text(page, 72, 160, LINK_DEFINITION_LINE)
    _text(page, 72, 200, BACKSLASH_LINE)
    _text(page, 72, 240, "\n".join(COMPOUND_LINES))
    _text(page, 72, 330, "\n".join(COMPOUND_LINES_2))
    return _save(doc, path)


OFFICE_LINE_BREAK_LIST = ["1. First step", "2. Second step", "3. Third step"]


def office_line_break_list_docx(path: Path) -> Path:
    """Word paragraph that holds a numbered list as lines separated by manual line breaks."""
    from docx import Document

    document = Document()
    paragraph = document.add_paragraph()
    for index, item in enumerate(OFFICE_LINE_BREAK_LIST):
        run = paragraph.add_run(item)
        if index + 1 < len(OFFICE_LINE_BREAK_LIST):
            run.add_break()
    document.add_paragraph("Closing paragraph of the memo.")
    document.save(str(path))
    return Path(path)


LEADIN_LINES = [
    "The plan covers two kinds of support:",
    "\u2022 Basic onboarding for new customers",
    "\u2022 Advanced coaching for existing customers",
    "Both tracks start in April.",
]


def leadin_bullets_pdf(path: Path) -> Path:
    """A lead-in sentence, two bullets and a closing sentence drawn as ONE text block, the way
    Word and LibreOffice exports often group a list with its lead-in (review probe f2b)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    _text(page, 72, 170, "\n".join(LEADIN_LINES), 10)
    _text(page, 72, 300, MARKERS_BODY, 10)
    return _save(doc, path)


RAISED_FOOTNOTE_TEXT = "See clause 3 of the master agreement dated 1 March 2020."


def raised_footnote_pdf(path: Path) -> Path:
    """A body sentence with a raised footnote reference and, at the bottom of the page, the
    footnote itself: a raised 5.5pt number and 8pt text, as Word lays out its footnote area
    (review probe f1b)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    _text(page, 72, 300, "The lease was renewed in 2020", 10)
    _text(page, 72 + pymupdf.get_text_length("The lease was renewed in 2020", "helv", 10), 296, "1", 6)
    _text(page, 72, 757, "1", 5.5)
    _text(page, 72 + pymupdf.get_text_length("1", "helv", 5.5), 760, " " + RAISED_FOOTNOTE_TEXT, 8)
    return _save(doc, path)


ORDINAL_LINES = ["Results of the 1st quarter and the 2nd half.", "ACME\u2122 and Brand\u00ae are registered marks."]
LITERAL_CARET_LINE = "The formula is x^2 + y^2 and a^b^c stays literal; also 5 * 3 = 15."


def ordinals_pdf(path: Path) -> Path:
    """Ordinal suffixes and trademark signs set raised and small (Word autoformat), and a line
    with literal carets (review probe f4b)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)

    def run(x, y, text, size=10):
        _text(page, x, y, text, size)
        return x + pymupdf.get_text_length(text, "helv", size)

    def mark(x, y, text, size=6):
        # insert_text writes U+2122 with a simple encoding that extracts as a middle dot; a
        # TextWriter embeds the real glyph
        writer = pymupdf.TextWriter(page.rect)
        writer.append((x, y), text, font=pymupdf.Font("helv"), fontsize=size)
        writer.write_text(page)
        return x + pymupdf.Font("helv").text_length(text, fontsize=size)

    x = run(72, 200, "Results of the 1")
    x = run(x, 196, "st", 6)
    x = run(x, 200, " quarter and the 2")
    x = run(x, 196, "nd", 6)
    run(x, 200, " half.")
    x = run(72, 230, "ACME")
    x = mark(x, 226, "\u2122")
    x = run(x, 230, " and Brand")
    x = mark(x, 226, "\u00ae")
    run(x, 230, " are registered marks.")
    run(72, 260, LITERAL_CARET_LINE)
    return _save(doc, path)


CJK_OUTLINE_LINES = [
    "本計畫之推動策略與方法說明如下，各項工作均依照時程辦理並定期檢討執行成果與效益",
    "二、預期導入對象為中小企業",
    "AI 工具導入後之成效追蹤",
]
CJK_LABEL_LINES = ["執行單位：中華民國資訊軟體服務商業同業公會", "提案單位：星蘊有限公司"]


def cjk_outline_and_labels_pdf(path: Path) -> Path:
    """A CJK paragraph line followed, in the same block, by an outline item and a Latin line
    (review probe f5), and two form label lines (``單位：…``) in one block."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    _text(page, 72, 200, "\n".join(CJK_OUTLINE_LINES), 10, "china-t")
    _text(page, 72, 300, "\n".join(CJK_LABEL_LINES), 10, "china-t")
    return _save(doc, path)


DENSE_COLUMNS, DENSE_ROWS = 30, 25


def dense_letter_grid_pdf(path: Path) -> Path:
    """A dense grid of 6pt ``x1`` labels with a 16pt ``A`` every seventh cell (review probe
    ``dense``): large single letters that are not drop caps."""
    doc = pymupdf.open()
    page = doc.new_page(width=1190, height=1684)
    for column in range(DENSE_COLUMNS):
        for row in range(DENSE_ROWS):
            letter = (column + row) % 7 == 0
            _text(page, 20 + column * 19, 30 + row * 32, "A" if letter else "x1", 16 if letter else 6)
    return _save(doc, path)


def dense_letter_count() -> int:
    return sum(1 for column in range(DENSE_COLUMNS) for row in range(DENSE_ROWS) if (column + row) % 7 == 0)


TITLE_WITH_TABLE = "Quarterly Report"


def title_and_bold_table_header_pdf(path: Path) -> Path:
    """A 14pt bold title, a ruled table whose header cells are also 14pt bold, and a second page
    with a 12pt bold section heading."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, TITLE_WITH_TABLE, 14, "hebo")
    _text(page, 72, 110, MARKERS_BODY, 10)
    rows = [["Region", "Revenue"], ["North", "120"], ["South", "95"]]
    y = 200
    for row_no, row in enumerate(rows):
        x = 72
        for value in row:
            rect = pymupdf.Rect(x, y, x + 150, y + 24)
            page.draw_rect(rect, color=(0, 0, 0), width=0.6)
            _text(page, x + 4, y + 17, value, 14 if row_no == 0 else 10, "hebo" if row_no == 0 else "helv")
            x += 150
        y += 24
    page = _new_page(doc)
    _text(page, 72, 80, "1 Scope", 12, "hebo")
    _text(page, 72, 110, MARKERS_BODY, 10)
    return _save(doc, path)


SKIPPED_DEPTH_HEADINGS = [("1 Introduction", 16), ("1.1.1 Detail of the scope", 12), ("2 Results", 16),
                          ("2.1 Method", 14)]
SIDE_BY_SIDE_LABELS = ["Market Share", "Customer Growth", "Retention Rate"]


def heading_levels_and_labels_pdf(path: Path) -> Path:
    """Numbered bold headings where a third-level number follows a first-level one directly and a
    14pt second-level heading follows the second chapter, and three 16pt bold labels side by
    side on one row (one MuPDF block)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    y = 80
    for text, size in SKIPPED_DEPTH_HEADINGS:
        _text(page, 72, y, text, size, "hebo")
        _text(page, 72, y + 30, MARKERS_BODY, 10)
        y += 110
    for x, label in zip((72, 250, 430), SIDE_BY_SIDE_LABELS):
        _text(page, x, y + 20, label, 16, "hebo")
    _text(page, 72, y + 60, MARKERS_BODY, 10)
    return _save(doc, path)


MEANINGFUL_BULLETS = ["\u2713 Contract signed by both parties", "\u2713 Budget approved by the board",
                      "\u2794 Next review in May"]


def meaningful_bullets_pdf(path: Path) -> Path:
    """A list whose bullets carry meaning (check marks, an arrow), drawn with an embedded font
    that has those glyphs."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    writer = pymupdf.TextWriter(page.rect)
    for index, line in enumerate(MEANINGFUL_BULLETS):
        writer.append((72, 170 + index * 14), line, font=pymupdf.Font("cjk"), fontsize=10)
    writer.write_text(page)
    _text(page, 72, 260, MARKERS_BODY, 10)
    return _save(doc, path)


# --- review round 1, second pass (structure found by the code review of the fixes) -------------

def _wrap(text: str, width: float, size: float = 10, first_width: float = None) -> list:
    """``text`` (Helvetica) broken into lines the way a word processor breaks them (first fit): a
    word moves to the next line only when it does not fit on the current one. ``first_width`` is
    the width of the first line."""
    lines, line = [], ""
    for word in text.split(" "):
        candidate = f"{line} {word}" if line else word
        limit = first_width if first_width is not None and not lines else width
        if line and pymupdf.get_text_length(candidate, "helv", size) > limit:
            lines.append(line)
            line = word
        else:
            line = candidate
    return lines + [line]


def _wrap_font(text: str, width: float, size: float, font: str) -> list:
    """``_wrap`` for another base-14 font."""
    lines, line = [], ""
    for word in text.split(" "):
        candidate = f"{line} {word}" if line else word
        if line and pymupdf.get_text_length(candidate, font, size) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    return lines + [line]


def _justified(page, x: float, y: float, lines: list, size: float = 10, leading: float = 12) -> None:
    """Draw ``lines`` as a justified paragraph: every line but the last is stretched to the width
    of the longest one. Drawn with a TextWriter, so dashes stay dashes."""
    font = pymupdf.Font("helv")
    width = max(font.text_length(line, fontsize=size) for line in lines)
    writer = pymupdf.TextWriter(page.rect)
    for index, line in enumerate(lines):
        words = line.split(" ")
        gap = font.text_length(" ", fontsize=size)
        if index + 1 < len(lines) and len(words) > 1:
            gap += (width - font.text_length(line, fontsize=size)) / (len(words) - 1)
        cursor = x
        for word in words:
            writer.append((cursor, y + index * leading), word, font=font, fontsize=size)
            cursor += font.text_length(word, fontsize=size) + gap
    writer.write_text(page)


WRAPPED_NUMBER_TEXT = ("At the end of the reporting period the number of branches in the network was 87. Management "
                       "expects the network to grow further during the next financial year as the new regional "
                       "strategy is rolled out across the remaining markets.")
WRAPPED_DASH_LINES = [
    "The region reported a strong year in which revenue growth outpaced the market",
    "\u2013 especially in the Asian markets \u2013 and operating margins improved further while",
    "costs remained under control in every segment and the balance sheet stayed solid",
    "\u2013 notably freight costs \u2013 declined for the first time since the pandemic began.",
]


def wrapped_marker_lines_pdf(path: Path) -> Path:
    """Two paragraphs whose wrapped lines start like list markers (review probes p1, p14): a
    left-aligned paragraph broken so that ``87. Management …`` starts a line, and a justified
    paragraph whose second and fourth lines start with a spaced en dash."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    width = pymupdf.get_text_length(WRAPPED_NUMBER_TEXT.split(" 87.")[0], "helv", 10) + 2
    _text(page, 72, 200, "\n".join(_wrap(WRAPPED_NUMBER_TEXT, width)), 10)
    _justified(page, 72, 300, WRAPPED_DASH_LINES)
    _text(page, 72, 400, MARKERS_BODY, 10)
    return _save(doc, path)


INDENTED_CLAUSES = [
    "1. The Supplier shall deliver the goods within thirty days of the order date and shall bear all costs of "
    "transport to the delivery address specified by the Purchaser in the purchase order.",
    "2. The Purchaser shall pay each invoice within thirty days of receipt and shall notify the Supplier of any "
    "disputed amount in writing within ten days of receipt of the invoice.",
]


def first_line_indent_clauses_pdf(path: Path) -> Path:
    """Numbered clauses in the Word legal style (review probe p7): the number indented like a
    first line (x=90) and the lines wrapped to the margin (x=72), right edge at x=470."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    y = 170
    for clause in INDENTED_CLAUSES:
        lines = _wrap(clause, 470 - 72, first_width=470 - 90)
        _text(page, 90, y, lines[0], 10)
        _text(page, 72, y + 12, "\n".join(lines[1:]), 10)
        y += 12 * len(lines)
    _text(page, 72, y + 40, MARKERS_BODY, 10)
    return _save(doc, path)


COLUMN_LIST_LINES = [
    "The plan covers two kinds of support:",
    "\u2022 Basic onboarding for new customers",
    "\u2022 Advanced coaching for existing ones",
    "Both tracks start in April next year.",
]
COLUMN_CLAUSES = [
    "1. The supplier shall deliver the goods within thirty days of the order date and shall bear all costs of "
    "transport to the delivery address specified by the customer in the order.",
    "2. The customer shall pay each invoice within thirty days of receipt and shall notify the supplier of any "
    "disputed amount in writing within ten days.",
]
COLUMN_RIGHT = ("The parties agree that the obligations set out in this\n"
                "clause survive the termination of the agreement and\n"
                "remain binding on any successor of either party.")
COLUMN_INTRO = ("This agreement sets out the terms on which the supplier provides services to the customer and the\n"
                "obligations of both parties during the term of the agreement and after its termination or expiry.")


def two_column_lists_pdf(path: Path) -> Path:
    """Two pages with two text columns and a paragraph in the right one: page 1 has a lead-in, two
    bullets and a closing line in one left-column block; page 2 a full-width intro and two
    numbered clauses wrapped at the number's margin, 240pt wide (review probes p6b, p6)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 50, 130, "\n".join(COLUMN_LIST_LINES), 9)
    _text(page, 310, 130, COLUMN_RIGHT, 9)
    page = _new_page(doc)
    _text(page, 50, 80, COLUMN_INTRO, 9)
    _text(page, 50, 130, "\n".join(line for clause in COLUMN_CLAUSES for line in _wrap(clause, 240, 9)), 9)
    _text(page, 310, 130, COLUMN_RIGHT, 9)
    return _save(doc, path)


TABBED_HEADINGS = [("1", "Scope of Work", 11, 108), ("1.1", "Background", 10.5, 108), ("2", "Payment Terms", 11, 108),
                   ("2.1", "Invoicing", 10.5, 144)]


def tabbed_heading_numbers_pdf(path: Path) -> Path:
    """Bold numbered headings whose number and title are a tab apart (the title half an inch or an
    inch from the number), under an 18pt title (review probes p4, p4b)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, "Annual Report", 18, "hebo")
    _text(page, 72, 120, MARKERS_BODY, 10)
    y = 220
    for number, title, size, x in TABBED_HEADINGS:
        _text(page, 72, y, number, size, "hebo")
        _text(page, x, y, title, size, "hebo")
        _text(page, 72, y + 20, MARKERS_BODY, 10)
        y += 110
    return _save(doc, path)


STACKED_CJK_LABELS = ["處理常見問題", "分類客訴", "建立工單", "整理互動紀錄"]


def stacked_cjk_labels_pdf(path: Path) -> Path:
    """Four short CJK labels stacked in one block between body paragraphs (review probe p16)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    _text(page, 72, 200, "\n".join(STACKED_CJK_LABELS), 12, "china-t")
    _text(page, 72, 320, MARKERS_BODY, 10)
    return _save(doc, path)


RUNNING_HEADER = "ACME Holdings Annual Report"
RUNNING_HEADER_HEADINGS = ["Letter to Shareholders"] + [f"{number} Operating Review Part {number}" for number in range(1, 5)]


def large_running_header_pdf(path: Path) -> Path:
    """Five pages whose largest text is a 14pt bold running header, with a 13pt bold heading on
    page 1 and 12pt bold chapter headings on the others (review probe p2)."""
    doc = pymupdf.open()
    for number, heading in enumerate(RUNNING_HEADER_HEADINGS):
        page = _new_page(doc)
        _text(page, 72, 50, RUNNING_HEADER, 14, "hebo")
        _text(page, 72, 140, heading, 13 if number == 0 else 12, "hebo")
        for block in range(4):
            _text(page, 72, 180 + block * 90, MARKERS_BODY, 10)
        _text(page, 290, 810, str(number + 1), 9)
    return _save(doc, path)


LONG_BULLET_LINES = [
    "The plan covers two kinds of support:",
    "\u2022 Basic onboarding for new customers in every region of the group network",
    "\u2022 Advanced coaching for existing customers with more than two active contracts",
    "Both tracks start in April.",
]


def long_bullets_pdf(path: Path) -> Path:
    """A lead-in, two bullets whose last one runs to the right edge of the block and a short
    closing line at the bullets' margin, in one block (review probe p10)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    _text(page, 72, 170, "\n".join(LONG_BULLET_LINES), 10)
    _text(page, 72, 300, MARKERS_BODY, 10)
    return _save(doc, path)


SPLIT_LETTER_BLOCKS = [["The lessee undertakes the following obligations:", "(a) to pay the rent monthly in advance;"],
                       ["(b) to keep the premises in good repair;"], ["(c) to insure the premises against fire."]]
SPLIT_DASH_BLOCKS = [["The supplier offers these services:", "- installation of the equipment"],
                     ["- maintenance of the equipment"]]


def split_sequence_lists_pdf(path: Path) -> Path:
    """A lettered and a dash list whose first item shares a block with the lead-in line while the
    other items sit in the next blocks (review probe p5)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    for y, block in zip((160, 196, 218), SPLIT_LETTER_BLOCKS):
        _text(page, 72, y, "\n".join(block), 10)
    for y, block in zip((300, 336), SPLIT_DASH_BLOCKS):
        _text(page, 72, y, "\n".join(block), 10)
    _text(page, 72, 400, MARKERS_BODY, 10)
    return _save(doc, path)


GROUPED_FOOTNOTES = ["See clause 3 of the master agreement dated 1 March 2020.",
                     "The index is the consumer price index published monthly."]


def grouped_raised_footnotes_pdf(path: Path) -> Path:
    """A sentence with two raised footnote references and, at the bottom of the page, two
    footnotes with raised 5.5pt numbers and 8pt text that MuPDF groups into one block (review
    probe p9)."""
    doc = pymupdf.open()
    page = _new_page(doc)

    def run(x, y, text, size=10):
        _text(page, x, y, text, size)
        return x + pymupdf.get_text_length(text, "helv", size)

    _text(page, 72, 80, MARKERS_BODY, 10)
    x = run(72, 300, "The lease was renewed in 2020")
    x = run(x, 296, "1", 6)
    x = run(x, 300, " and the rent was indexed")
    run(x, 296, "2", 6)
    for number, (y, note) in enumerate(zip((750, 760), GROUPED_FOOTNOTES), 1):
        run(run(72, y - 3, str(number), 5.5), y, " " + note, 8)
    return _save(doc, path)


CJK_BOLD_TAIL = ["本計畫將協助受輔導企業完成工具導入並建立日常使用習慣，重點在於導入後的", "資料治理、流程銜接、權限控管與跨系統執行。"]


def cjk_bold_tail_pdf(path: Path) -> Path:
    """A CJK paragraph whose wrapped second line is set in a bold CJK font, so the bold phrase
    starts right after the line break (the deck's page 4). The bold font is a second CJK base
    font renamed ``Heiti-Bold``, which is how a bold CJK face appears in the text layer."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    _text(page, 72, 200, CJK_BOLD_TAIL[0], 10, "china-t")
    _text(page, 72, 214, CJK_BOLD_TAIL[1], 10, "china-s")
    font = next(font[0] for font in page.get_fonts() if font[4] == "china-s")
    descendant = int(doc.xref_get_key(font, "DescendantFonts")[1].strip("[]").split()[0])
    descriptor = int(doc.xref_get_key(descendant, "FontDescriptor")[1].split()[0])
    for xref in (font, descendant):
        doc.xref_set_key(xref, "BaseFont", "/Heiti-Bold")
    doc.xref_set_key(descriptor, "FontName", "/Heiti-Bold")
    doc.xref_set_key(descriptor, "FontWeight", "700")
    _text(page, 72, 300, MARKERS_BODY, 10)
    return _save(doc, path)


# Word writes Wingdings/Symbol bullets into the private use area (U+F0xx) of the text layer.
WINGDINGS_BULLETS = [(0xFC, "Contract signed by both parties"), (0xFC, "Budget approved by the board"),
                     (0xD8, "Next review in May")]
WINGDINGS_ITEMS = ["\u2713 Contract signed by both parties", "\u2713 Budget approved by the board",
                   "\u27a2 Next review in May"]
_PUA_TO_UNICODE_CMAP = """/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Wingdings-UCS def
/CMapType 2 def
1 begincodespacerange
<00> <FF>
endcodespacerange
2 beginbfchar
<FC> <F0FC>
<D8> <F0D8>
endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end
"""


def wingdings_bullets_pdf(path: Path) -> Path:
    """A list whose bullets are Wingdings check marks and an arrow, as Word exports them: the
    bullet in its own font, a tab away from the text, read back as U+F0FC / U+F0D8 through the
    font's ToUnicode map."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    for index, (code, text) in enumerate(WINGDINGS_BULLETS):
        _text(page, 72, 170 + index * 14, chr(code), 10, "cour")
        _text(page, 90, 170 + index * 14, text, 10)
    font = next(font[0] for font in page.get_fonts() if font[3] == "Courier")
    cmap = doc.get_new_xref()
    doc.update_object(cmap, "<<>>")
    doc.update_stream(cmap, _PUA_TO_UNICODE_CMAP.encode())
    doc.xref_set_key(font, "ToUnicode", f"{cmap} 0 R")
    _text(page, 72, 260, MARKERS_BODY, 10)
    return _save(doc, path)


# --- review round 1, third pass (second code review of the fixes) ----------------------------------

CJK_WRAPPED_TITLE = "產業智慧化導入應用輔導提案計畫書"


def cjk_wrapped_title_pdf(path: Path) -> Path:
    """A 24pt CJK title wrapped after nine characters, inside the word 輔導, as on the cover of a
    Word proposal, then a CJK paragraph (review probe pT)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 120, CJK_WRAPPED_TITLE[:9] + "\n" + CJK_WRAPPED_TITLE[9:], 24, "china-t")
    body = "本計畫將協助受輔導企業完成工具導入並建立日常使用習慣，重點在於導入後的資料治理、流程銜接、權限控管與跨系統執行，並定期檢視成效與調整方向。"
    _text(page, 72, 220, "\n".join(body[index:index + 36] for index in range(0, len(body), 36)), 11, "china-t")
    return _save(doc, path)


FLUSH_LIST_LEAD_INS = ["The plan covers three kinds of support:",
                       "Customers can choose between the following two service plans for the coming year"]
FLUSH_FIRST_ITEM = ("1. Basic onboarding for new customers in every region of the group network including the partner "
                    "markets in Asia and Europe where the group operates through local agents")
FLUSH_NUMBERED_ITEMS = [FLUSH_FIRST_ITEM[3:], "Advanced coaching for existing customers", "Priority support for key accounts",
                        "Basic plan with email support", "Premium plan with a named account manager"]


def flush_numbered_lists_pdf(path: Path) -> Path:
    """Two numbered lists in one block each (review probes pA, pA2): one typed flush, whose first
    item wraps back to the number's margin with its last line running (nearly) to the right edge;
    one after a lead-in without a colon that is the widest line of its block."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    width = next(width for width in range(300, 520)
                 if len(_wrap(FLUSH_FIRST_ITEM, width)) == 2
                 and pymupdf.get_text_length(_wrap(FLUSH_FIRST_ITEM, width)[1], "helv", 10)
                 >= pymupdf.get_text_length(_wrap(FLUSH_FIRST_ITEM, width)[0], "helv", 10) - 6)
    lines = [FLUSH_LIST_LEAD_INS[0]] + _wrap(FLUSH_FIRST_ITEM, width) + ["2. " + FLUSH_NUMBERED_ITEMS[1],
                                                                         "3. " + FLUSH_NUMBERED_ITEMS[2]]
    _text(page, 72, 170, "\n".join(lines), 10)
    _text(page, 72, 300, "\n".join([FLUSH_LIST_LEAD_INS[1], "1. " + FLUSH_NUMBERED_ITEMS[3],
                                     "2. " + FLUSH_NUMBERED_ITEMS[4]]), 10)
    _text(page, 72, 400, MARKERS_BODY, 10)
    return _save(doc, path)


FLUSH_BULLETS = [
    "We will open new offices during the next financial year in the following regions: Asia, Europe and the "
    "Americas, subject to board approval.",
    "The migration of the customer portal to the new platform is planned for the second quarter of 2025 and will "
    "be completed by the end of the year.",
    "Training for all sales staff will be organised with the help of the external consultants from KPMG and "
    "Deloitte in every region.",
]
FLUSH_BULLET_CLOSING = "Both tracks start in April."
FLUSH_CJK_BULLETS = ["本計畫將協助受輔導企業完成工具導入並建立日常使用習慣，重點在於導入後的資料治理、流程銜接、權限控管與跨系統執行。",
                     "專案團隊每月召開進度會議並提交書面報告。"]


def flush_bullets_pdf(path: Path) -> Path:
    """Bullets typed flush, their wrapped lines back at the bullet's margin (review probes pB, pC):
    Latin bullets wrapped first-fit at 400pt whose wrapped lines start with capitals, then a
    closing line; CJK bullets wrapped every 30 characters on page 2."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    lines = [line for item in FLUSH_BULLETS for line in _wrap("\u2022 " + item, 400)]
    _text(page, 72, 160, "\n".join(["Next year the group plans the following steps:"] + lines + [FLUSH_BULLET_CLOSING]), 10)
    _text(page, 72, 330, MARKERS_BODY, 10)
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    cjk = [text[index:index + 30] for item in FLUSH_CJK_BULLETS for text in ["\u25cf " + item]
           for index in range(0, len(text), 30)]
    _text(page, 72, 200, "\n".join(["本計畫的主要工作如下："] + cjk), 11, "china-t")
    _text(page, 72, 330, MARKERS_BODY, 10)
    return _save(doc, path)


NUMBERED_CLOSING_LINES = [
    "The plan covers two kinds of support:",
    "1. Basic onboarding for new customers in every region of the group network",
    "2. Advanced coaching for existing customers with more than two active contracts",
    "Both tracks start in April.",
]


def numbered_closing_line_pdf(path: Path) -> Path:
    """A lead-in, two one-line numbered items and a closing line at the numbers' margin, in one
    block (review probe pH)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    _text(page, 72, 170, "\n".join(NUMBERED_CLOSING_LINES), 10)
    _text(page, 72, 300, MARKERS_BODY, 10)
    return _save(doc, path)


WRAPPED_NUMBER_PARAGRAPH = ("At the end of the reporting period the number of branches in the network was 87. Management "
                            "expects the network to grow further during the next financial year as the new regional "
                            "strategy is rolled out across the remaining markets.")
WRAPPED_NUMBER_ITEMS = [WRAPPED_NUMBER_PARAGRAPH.split(" as the new")[0] + ".",
                        "The group also opened two new regional offices in the north."]


def wrapped_number_layouts_pdf(path: Path) -> Path:
    """The wrapped ``87. Management …`` line in three more layouts (review probes pD, pG, pF):
    double-spaced 12pt Times on page 1 (one PyMuPDF block per line); on page 2 a paragraph with a
    first-line indent, and a numbered item with a hanging indent."""
    doc = pymupdf.open()
    page = _new_page(doc)
    prefix = WRAPPED_NUMBER_PARAGRAPH.split(" 87.")[0]
    width = pymupdf.get_text_length(prefix, "tiro", 12) + 3
    lines, y = [], 80
    for line in _wrap_font(WRAPPED_NUMBER_PARAGRAPH, width, 12, "tiro"):
        _text(page, 72, y, line, 12, "tiro")
        y += 24
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    width = pymupdf.get_text_length(prefix, "helv", 10) + 3
    lines = _wrap(WRAPPED_NUMBER_PARAGRAPH, width + 18, first_width=width)
    _text(page, 90, 170, lines[0], 10)
    _text(page, 72, 182, "\n".join(lines[1:]), 10)
    item = _wrap(WRAPPED_NUMBER_ITEMS[0], width)
    y = 170 + 12 * len(lines) + 40
    _text(page, 72, y, "1.", 10)
    _text(page, 90, y, "\n".join(item), 10)
    _text(page, 72, y + 12 * len(item), "2.", 10)
    _text(page, 90, y + 12 * len(item), WRAPPED_NUMBER_ITEMS[1], 10)
    _text(page, 72, y + 12 * len(item) + 60, MARKERS_BODY, 10)
    return _save(doc, path)


STACKED_CJK_ITEMS = ["提升客戶服務效率與品質並降低營運成本", "建立跨部門協作流程與資料共享機制",
                     "導入智慧客服系統處理常見問題與客訴", "整合銷售與行銷數據以支援決策分析"]


def stacked_cjk_items_pdf(path: Path) -> Path:
    """Four CJK items of 16-18 characters stacked in one block, their bullets drawn as circles, as
    on a slide with picture bullets (review probe pE)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    _text(page, 90, 200, "\n".join(STACKED_CJK_ITEMS), 12, "china-t")
    for index in range(len(STACKED_CJK_ITEMS)):
        page.draw_circle((80, 196 + index * 12), 2.5, fill=(0, 0.3, 0.6), color=None)
    _text(page, 72, 320, MARKERS_BODY, 10)
    return _save(doc, path)


CJK_SYMBOL_BOLD_LINES = [("本季營運重點如下，機房溫度需維持在", "30\u2103", "以下並每日檢查。"),
                         ("本季營運重點如下，請先閱讀", "\u2605重點說明", "再填寫申請表格。")]


def cjk_symbol_bold_pdf(path: Path) -> Path:
    """CJK lines with a bold run that starts or ends with a symbol (``30℃``, ``★重點說明``), the bold
    font being a CJK base font renamed ``Heiti-Bold`` as in ``cjk_bold_tail_pdf`` (review probe pK)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, MARKERS_BODY, 10)
    for y, parts in zip((200, 230), CJK_SYMBOL_BOLD_LINES):
        x = 72
        for index, part in enumerate(parts):
            _text(page, x, y, part, 11, "china-s" if index == 1 else "china-t")
            x += 11 * len(part)  # the CJK base fonts advance every character by 1 em
    font = next(font[0] for font in page.get_fonts() if font[4] == "china-s")
    descendant = int(doc.xref_get_key(font, "DescendantFonts")[1].strip("[]").split()[0])
    descriptor = int(doc.xref_get_key(descendant, "FontDescriptor")[1].split()[0])
    for xref in (font, descendant):
        doc.xref_set_key(xref, "BaseFont", "/Heiti-Bold")
    doc.xref_set_key(descriptor, "FontName", "/Heiti-Bold")
    doc.xref_set_key(descriptor, "FontWeight", "700")
    _text(page, 72, 330, MARKERS_BODY, 10)
    return _save(doc, path)


def invoice_batch_pdf(path: Path) -> Path:
    """Five invoices, one per page, each headed by a 20pt bold ``INVOICE`` at the top of the page,
    so that the header/footer pass sees it on every page (the headings review's probe p03_H3)."""
    doc = pymupdf.open()
    for number in range(1, 6):
        page = _new_page(doc)
        _text(page, 72, 55, "INVOICE", 20, "hebo")
        _text(page, 72, 84, f"Invoice No: INV-2024-000{number}\nBill To: Customer {'ABCDE'[number - 1]} Ltd", 10)
        _text(page, 72, 150, MARKERS_BODY, 10.5)
        _text(page, 72, 770, "Payment terms: 30 days net", 9)
    return _save(doc, path)


# --- Markdown injection (probes p05, p05b) --------------------------------------------------------

INJECTION_LINES = [
    "# of patients enrolled: 120",
    "> 65 years of age were excluded",
    "+ 20% bonus for early payment",
    "* marked fields are mandatory",
    "Terms marked with * are mandatory; see *Annex* for details",
    "Call __init__ before use_the_api_v2",
    "<img src=x onerror=alert(1)>",
    "```",
    "[click here](javascript:alert(1))",
    "<!-- page 99 -->",
]
SIGNATORY_LINES = ["Authorised signatory", "----------------------------", "John Smith, CFO"]
CLOSING_LINES = [
    "Closing paragraph line one after the separator",
    "Closing paragraph line two after the separator",
    "Closing paragraph line three after the separator",
]


def markdown_injection_pdf(path: Path) -> Path:
    """Body lines that look like Markdown/HTML syntax, then a paragraph whose second line is a
    row of hyphens and a closing paragraph (probe ``p05``)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(
        page,
        72,
        60,
        "Intro paragraph line one of the clinical protocol\n"
        "Intro paragraph line two of the clinical protocol\n"
        "Intro paragraph line three of the protocol",
    )
    y = 140
    for line in INJECTION_LINES:
        _text(page, 72, y, line)
        y += 30
    _text(page, 72, y + 10, "\n".join(SIGNATORY_LINES))
    _text(page, 72, y + 80, "\n".join(CLOSING_LINES))
    return _save(doc, path)


# --- heading markup (probe p06) -------------------------------------------------------------------

REPORT_BODY = (
    "Body text line one of the report introduction section\n"
    "Body text line two of the report introduction section\n"
    "Body text line three of the introduction section"
)


def mixed_size_heading_blocks_pdf(path: Path) -> Path:
    """Blocks whose first line is a large heading and whose second line is small text:
    ``Annual Report`` (30pt) + ``2024 edition`` (11pt) and ``Market Overview`` (22pt) +
    ``Q3 update`` (10pt)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    writer = pymupdf.TextWriter(page.rect)
    writer.append((72, 90), "Annual Report", font=pymupdf.Font("helv"), fontsize=30)
    writer.append((72, 108), "2024 edition", font=pymupdf.Font("helv"), fontsize=11)
    writer.write_text(page)
    _text(page, 72, 200, REPORT_BODY)
    page = _new_page(doc)
    _text(page, 72, 80, REPORT_BODY)
    writer = pymupdf.TextWriter(page.rect)
    writer.append((72, 200), "Market Overview", font=pymupdf.Font("helv"), fontsize=22)
    writer.append((72, 214), "Q3 update", font=pymupdf.Font("helv"), fontsize=10)
    writer.write_text(page)
    _text(page, 72, 300, "Body text line one of the market section\nBody text line two of the market section")
    return _save(doc, path)


# --- ligatures, hyphenation, CJK line joins (probe p08, r3) --------------------------------------

LIGATURE_LINE = "The \ufb01nancial statements show cash \ufb02ow ef\ufb01ciency improved."
HYPHENATED_LINES = [
    "The committee approved the new invest-",
    "ment policy after a long discus-",
    "sion of the risks involved. The state-of-the-",
    "art model was trained on data from 2019-",
    "2020 and on the COVID-",
    "19 cohort described by Smith-",
    "Jones in the appendix.",
]


HYPHEN_EVIDENCE_LINE = "Every investment needs a discussion first."


def ligatures_hyphenation_pdf(path: Path) -> Path:
    """A line drawn with the fi/fl ligature glyphs (the text layer maps them to U+FB01/U+FB02),
    a paragraph with end-of-line hyphens, some of which are real hyphens, and a line that uses
    two of the hyphenated words unhyphenated (evidence that their line-end hyphens are
    hyphenation)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    writer = pymupdf.TextWriter(page.rect)
    writer.append((72, 100), LIGATURE_LINE, font=pymupdf.Font("helv"), fontsize=11)
    writer.write_text(page)
    _text(page, 72, 140, "\n".join(HYPHENATED_LINES), 11)
    _text(page, 72, 300, HYPHEN_EVIDENCE_LINE, 11)
    return _save(doc, path)


# Line-end hyphens of compound words (review probe f3): the first part ends a line, the second
# part starts the next one. Nothing else in the document spells them without the hyphen.
HYPHEN_PAIRS = [
    ("co-", "operate with the regulator"), ("follow-", "up meetings were held"), ("state-", "of-the-art systems"),
    ("e-", "mail addresses were collected"), ("pre-", "trained models were used"),
    ("re-", "entry permits were issued"), ("anti-", "inflammatory drugs are listed"),
    ("multi-", "modal retrieval was tested"), ("check-", "in desks were open"), ("X-", "ray images were stored"),
    ("invest-", "ment income rose sharply"), ("long-", "term debt was repaid"),
]
HYPHEN_PAIR_LEAD = "The board noted that the teams agreed to "
HYPHEN_PAIR_TAIL = " during the year under review."


def line_end_hyphen_pairs_pdf(path: Path) -> Path:
    """One two-line paragraph per pair in ``HYPHEN_PAIRS``."""
    doc = pymupdf.open()
    page = _new_page(doc)
    y = 80
    for first, second in HYPHEN_PAIRS:
        _text(page, 72, y, HYPHEN_PAIR_LEAD + first + "\n" + second + HYPHEN_PAIR_TAIL, 10)
        y += 50
    return _save(doc, path)


LO_COMPOUNDS = [
    "co-operate", "top-down", "on-premises", "mid-sized", "large-scale", "sign-off", "follow-up", "check-in",
    "pre-trained", "re-entry", "anti-inflammatory", "multi-modal", "cross-border", "end-to-end", "real-time",
    "well-known", "long-term", "year-end", "decision-making", "non-current",
]
LO_COMPOUND_ROUNDS = 3


def hyphenated_compounds_docx(path: Path) -> Path:
    """Word document whose paragraphs each hold one hyphenated compound at a varying position,
    so that LibreOffice's line breaking puts several of the hyphens at a line end (Word and
    LibreOffice break after an existing hyphen; they do not hyphenate by default)."""
    from docx import Document

    words = "team asked reviewed in progress and to committee the approach our report back detail".split()
    rng = random.Random(7)
    document = Document()
    document.add_heading("Annual Operations Review", level=1)
    for _ in range(LO_COMPOUND_ROUNDS):
        for compound in LO_COMPOUNDS:
            before = " ".join(rng.choice(words) for _ in range(rng.randint(6, 18)))
            after = " ".join(rng.choice(words) for _ in range(12))
            document.add_paragraph(f"{before.capitalize()} {compound} {after}.")
    document.save(str(path))
    return Path(path)


CJK_PARAGRAPH_LINES = [
    "本公司於本年度持續投資核心平台並維持營運成本穩定管理階層認為現行策略",
    "能使企業在下一個規劃週期中具備競爭優勢惟總體經濟環境仍具不確定性部分",
    "地區客戶需求於下半年轉弱因此本報告將逐項說明各事業部門之營運成果。",
]


def cjk_paragraph_pdf(path: Path) -> Path:
    """One CJK paragraph drawn line by line with generous leading, the way Word/LibreOffice
    exports CJK text: MuPDF puts every line in its own block."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 80, "營運概況", 16, "china-t")
    y = 120
    for line in CJK_PARAGRAPH_LINES:
        _text(page, 72, y, line, 11, "china-t")
        y += 22
    _text(page, 72, y + 30, "以上內容僅供內部參考。", 11, "china-t")
    return _save(doc, path)


# --- captions next to tables and images (T17, probe S6) ------------------------------------------

TABLE_ROWS = [["Region", "Revenue"], ["North", "120"], ["South", "95"]]
BODY_BEFORE_TABLE = "Body text paragraph before the table, long enough to be normal text for the reader."
HEADING_BEFORE_TABLE = "2. Regional Revenue"
HEADING_AFTER_TABLE = "3. Results and Discussion"
BODY_AFTER_HEADING = "More body text after the heading explains how the results were measured this year."


def headings_around_table_pdf(path: Path) -> Path:
    """A 16pt bold numbered heading 20pt above a ruled table and another one 25pt below it, with
    body text around them (probe S6)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 50, 60, BODY_BEFORE_TABLE, 10)
    _text(page, 50, 110, HEADING_BEFORE_TABLE, 16, "hebo")
    table = _ruled_table(page, 50, 130, [120, 120], 18, TABLE_ROWS)
    _text(page, 50, table.y1 + 25, HEADING_AFTER_TABLE, 16, "hebo")
    _text(page, 50, table.y1 + 55, BODY_AFTER_HEADING, 10)
    return _save(doc, path)


FIGURE_CAPTION = "Figure 2: Revenue by region, 2020 to 2024"
TABLE_CAPTION = "Table 1: Operating costs by quarter"
TWO_LINE_CAPTION = ["Figure 3: Customer growth in the northern region", "(thousands of accounts, fiscal years)"]


def captions_pdf(path: Path) -> Path:
    """An image with a ``Figure 2:`` caption below it, a ruled table with a ``Table 1:`` caption
    above it, and a second image with a 2-line caption."""
    doc = pymupdf.open()
    page = _new_page(doc)
    _text(page, 72, 60, "The following figure summarises the regional revenue development.", 10)
    page.insert_image(pymupdf.Rect(72, 80, 372, 230), stream=_png(300, 150))
    _text(page, 72, 244, FIGURE_CAPTION, 9)
    _text(page, 72, 290, "Operating costs remained under control during the year.", 10)
    _text(page, 72, 322, TABLE_CAPTION, 9)
    _ruled_table(page, 72, 330, [150, 150], 18, [["Quarter", "Cost"], ["Q1", "40"], ["Q2", "42"]])
    page.insert_image(pymupdf.Rect(72, 430, 372, 580), stream=_png(300, 150, (200, 120, 60)))
    _text(page, 72, 594, "\n".join(TWO_LINE_CAPTION), 9)
    _text(page, 72, 650, "The closing paragraph of the page follows the figures and tables.", 10)
    return _save(doc, path)


# --- running header lines -----------------------------------------------------------------------

def running_header_pdf(path: Path) -> Path:
    """Two pages with a body-size running header ``Chapter 1 - Introduction`` at the top, a 16pt
    bold chapter heading and body paragraphs."""
    doc = pymupdf.open()
    for page_no in range(2):
        page = _new_page(doc)
        _text(page, 72, 40, "Chapter 1 - Introduction", 10.5)
        if page_no == 0:
            _text(page, 72, 100, "1 Introduction", 16, "hebo")
        _text(page, 72, 140, MARKERS_BODY)
    return _save(doc, path)


REPEATED_TITLE = "Quarterly Risk Review"


def title_repeated_as_running_header_pdf(path: Path, bold: bool) -> Path:
    """Six pages: a 20pt title at the top of page 1 whose text is also the 9pt running header of
    pages 2-6, and a body paragraph on every page (probe ``p03f``)."""
    words = "alpha beta gamma delta sigma omega river stone cloud metal paper glass forest ocean".split()
    doc = pymupdf.open()
    for page_no in range(1, 7):
        page = _new_page(doc)
        if page_no == 1:
            _text(page, 72, 80, REPEATED_TITLE, 20, "hebo" if bold else "helv")
        else:
            _text(page, 72, 40, REPEATED_TITLE, 9)
        rng = random.Random(page_no)
        _text(page, 72, 150, "\n".join(" ".join(rng.choice(words) for _ in range(12)) for _ in range(10)))
    return _save(doc, path)


# --- scanned page with an invisible OCR text layer (probe p07) -----------------------------------

SANDWICH_HEADINGS = ["1 Introduction", "1.1 Background", "2 Outlook"]
SANDWICH_PARAGRAPH = (
    "The group continued to invest in its core platforms during the year while keeping\n"
    "operating costs flat. Management believes the current strategy positions the business\n"
    "well for the next planning cycle, although macroeconomic conditions remain uncertain."
)


def scanned_sandwich_pdf(path: Path) -> Path:
    """A report page (26pt title, 14pt/13pt bold headings, 11pt body) rasterised to a full-page
    image and overlaid with the same text as an invisible, non-bold layer at the original sizes
    and positions: what OCR tools (Tesseract, OCRmyPDF) produce for scanned documents."""
    source = pymupdf.open()
    page = _new_page(source)
    _text(page, 72, 90, "Annual Operations Report", 26, "hebo")
    y = 150
    for heading, size in zip(SANDWICH_HEADINGS, (14, 13, 14)):
        _text(page, 72, y, heading, size, "hebo")
        _text(page, 72, y + 24, SANDWICH_PARAGRAPH, 11)
        y += 120
    out = pymupdf.open()
    scanned = _new_page(out)
    scanned.insert_image(scanned.rect, stream=page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY).tobytes("png"))
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip():
                    scanned.insert_text(
                        span["origin"], span["text"], fontsize=span["size"], fontname="helv", render_mode=3
                    )
    source.close()
    return _save(out, path)


# --- realistic Office documents exported to PDF with LibreOffice ---------------------------------

REPORT_LOREM = (
    "The group continued to invest in its core platforms during the year while keeping "
    "operating costs flat. Management believes the current strategy positions the business "
    "well for the next planning cycle, although macroeconomic conditions remain uncertain and "
    "customer demand in several regions softened in the second half of the period."
)
REPORT_HEADINGS = [  # (text, Word heading level)
    ("1 Introduction", 1),
    ("1.1 Background", 2),
    ("1.1.1 Scope of Work", 3),
    ("What is Retrieval-Augmented Generation?", 2),
    ("Property, Plant and Equipment", 2),
    ("2 Shopping list", 1),
    ("3 Results", 1),
    ("3.1 Regional performance", 2),
]
REPORT_NUMBERED_ITEMS = ["Buy milk", "Buy eggs", "Call the bank about the loan"]
REPORT_BULLET_ITEMS = ["First bullet point", "Second bullet point"]


def _docx_field(paragraph, instruction):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), instruction)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "1"
    run.append(text)
    field.append(run)
    paragraph._p.append(field)


def report_docx(path: Path) -> Path:
    """Word report with the default template (probe ``mk_docx_report``): Title, Heading 1/2/3,
    ``List Number`` / ``List Bullet`` paragraphs, one bold phrase inside a paragraph, a running
    header and a ``Page X of Y`` footer."""
    from docx import Document

    document = Document()
    section = document.sections[0]
    section.header.paragraphs[0].text = "ACME Corporation - Annual Report 2024"
    footer = section.footer.paragraphs[0]
    footer.add_run("Page ")
    _docx_field(footer, "PAGE")
    footer.add_run(" of ")
    _docx_field(footer, "NUMPAGES")
    document.add_heading("ACME Corporation Annual Report", level=0)
    document.add_paragraph("Prepared by the Finance Department")
    for text, level in REPORT_HEADINGS:
        document.add_heading(text, level=level)
        if text == "1 Introduction":
            paragraph = document.add_paragraph("This report covers the fiscal year. ")
            paragraph.add_run("Revenue grew 12%").bold = True
            paragraph.add_run(" while operating margin was stable. " + REPORT_LOREM)
        elif text == "2 Shopping list":
            for item in REPORT_NUMBERED_ITEMS:
                document.add_paragraph(item, style="List Number")
            for item in REPORT_BULLET_ITEMS:
                document.add_paragraph(item, style="List Bullet")
        else:
            document.add_paragraph(REPORT_LOREM)
    document.save(str(path))
    return Path(path)


WORD2013_H1 = ["Introduction", "Methodology", "Results"]
WORD2013_H2 = ["Background", "Data Sources"]
WORD2013_H3 = "Scope and Limitations"


def word2013_docx(path: Path) -> Path:
    """Word 2013+ look (probe ``mk_docx_word2013``): Normal 11pt; Heading 1/2/3 at 16/13/12pt,
    not bold, coloured."""
    from docx import Document
    from docx.shared import Pt, RGBColor

    words = "growth margin customer region product platform investment strategy capital supply demand pricing".split()
    document = Document()
    document.styles["Normal"].font.size = Pt(11)
    document.styles["Normal"].font.name = "Calibri"
    for name, size in [("Heading 1", 16), ("Heading 2", 13), ("Heading 3", 12)]:
        style = document.styles[name]
        style.font.size = Pt(size)
        style.font.bold = False
        style.font.name = "Calibri Light"
        style.font.color.rgb = RGBColor(0x2F, 0x54, 0x96)
    counter = 0

    def paragraph():
        nonlocal counter
        rng = random.Random(counter)
        counter += 1
        document.add_paragraph(" ".join(rng.choice(words) for _ in range(60)) + ".")

    for h1 in WORD2013_H1:
        document.add_heading(h1, level=1)
        paragraph()
        for h2 in WORD2013_H2:
            document.add_heading(h2, level=2)
            paragraph()
            document.add_heading(WORD2013_H3, level=3)
            paragraph()
    document.save(str(path))
    return Path(path)


ZH_TITLE = "企業級 AI 協同作業系統導入計畫書"
ZH_HEADINGS = [  # (text, Word heading level), in document order
    ("第一章 總則", 1),
    ("第一節 計畫目的", 2),
    ("一、導入背景", 3),
    ("（一）現況分析", 3),
    ("壹、組織架構", 2),
    ("第１條 適用範圍", 3),
    ("第一条 总则", 3),
]
ZH_BODY_LINES = [
    "本系統整合ERP與CRM資料",
    "權限控管與API整合",
    "支援SSO單一登入",
    "導入後的管理",
    "附件資料已上傳至系統",
    "第三條規定之罰則適用於本法",
    "2024年營收成長12%",
    "3.5%客戶來自海外",
]
_ZH_POOL = (
    "本公司於年度持續投資核心平台並維持營運成本穩定管理階層認為現行策略能使企業在下一個規劃週期中具備"
    "競爭優勢惟總體經濟環境仍具不確定性部分地區客戶需求轉弱因此報告將逐項說明各事業部門之成果與未來展望"
)


def zh_report_docx(path: Path) -> Path:
    """Traditional-Chinese Word document (probe ``mk_docx_zh``): Title, chapter/section/article
    headings with CJK outline markers, short body lines with Latin acronyms, long paragraphs."""
    from docx import Document
    from docx.oxml.ns import qn

    counter = 0

    def body():
        nonlocal counter
        counter += 1
        rng = random.Random(counter)
        first = "".join(rng.choice(_ZH_POOL) for _ in range(40))
        second = "".join(rng.choice(_ZH_POOL) for _ in range(60))
        return f"第{counter}段：{first}，{second}。"

    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "Noto Sans CJK TC")
    document.sections[0].header.paragraphs[0].text = "數辰創藝科技 內部文件"
    document.add_heading(ZH_TITLE, level=0)
    for text, level in ZH_HEADINGS:
        document.add_heading(text, level=level)
        document.add_paragraph(body())
    document.add_heading("第二章 系統功能", level=1)
    for line in ZH_BODY_LINES:
        document.add_paragraph(line)
    for _ in range(3):
        document.add_paragraph(body())
    document.save(str(path))
    return Path(path)


TABLES_ADJACENT_HEADINGS = ["Quarterly Results", "HEADING-B Cost Analysis"]
TABLES_ADJACENT_BODY = [
    "BODY-A The next section discusses costs in more detail for each segment of the business.",
    "BODY-C Closing remarks for the quarter follow in the outlook section of this report.",
]


def tables_adjacent_docx(path: Path) -> Path:
    """Word document with ruled tables right under headings and body paragraphs around them
    (probe ``mk_docx_tables_adjacent``)."""
    from docx import Document
    from docx.shared import Pt

    def table(document):
        grid = document.add_table(rows=4, cols=3)
        grid.style = "Table Grid"
        for r in range(4):
            for c in range(3):
                grid.cell(r, c).text = ["Region", "Revenue", "Growth"][c] if r == 0 else f"R{r}C{c}"

    document = Document()
    document.add_heading(TABLES_ADJACENT_HEADINGS[0], level=1)
    document.add_paragraph("CAPTION-A Table 1: Revenue by region (USD m)")
    table(document)
    document.add_paragraph("NOTE-A Figures are unaudited and subject to change.")
    document.add_paragraph(TABLES_ADJACENT_BODY[0])
    document.add_heading(TABLES_ADJACENT_HEADINGS[1], level=2)
    table(document)
    document.add_paragraph("NOTE-B Costs exclude one-off restructuring charges.")
    paragraph = document.add_paragraph("CAPTION-C Revenue split by channel")
    paragraph.paragraph_format.space_after = Pt(0)
    table(document)
    paragraph = document.add_paragraph("NOTE-C Source: internal management accounts.")
    paragraph.paragraph_format.space_before = Pt(0)
    document.add_paragraph(TABLES_ADJACENT_BODY[1])
    document.save(str(path))
    return Path(path)


DECK_SLIDES = [  # (title, bullets); a leading "  " makes a level-1 sub-bullet
    ("Market Overview", ["Demand for AI copilots keeps growing", "Enterprise buyers want governance",
                         "  Audit trails and permissions", "  Data residency in APAC", "Competition is fragmented"]),
    ("Product Roadmap", ["Q4: SSO and SCIM", "Q1: Workflow builder", "  Drag-and-drop RPA steps", "Q2: Mobile app"]),
]


def bullet_deck_pptx(path: Path) -> Path:
    """Slide deck with two-level bullet lists (probe ``mk_pptx_deck``)."""
    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    presentation.slide_width = Inches(13.333)
    presentation.slide_height = Inches(7.5)
    for title, bullets in DECK_SLIDES:
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = title
        frame = slide.placeholders[1].text_frame
        frame.text = bullets[0]
        for bullet in bullets[1:]:
            paragraph = frame.add_paragraph()
            paragraph.text = bullet.strip()
            paragraph.level = 1 if bullet.startswith("  ") else 0
    presentation.save(str(path))
    return Path(path)


OFFICE_INJECTION_PARAGRAPHS = [
    "# of units sold: 120",
    "> 65 years of age were excluded",
    "+ 20% bonus for early payment",
    "<img src=x onerror=alert(1)>",
    "2024. The year the plant opened",
    "<!-- page 99 -->",
]
OFFICE_HEADING_WITH_HASH = "Ticket #"


def office_injection_docx(path: Path) -> Path:
    """Word document whose body paragraphs look like Markdown/HTML syntax, and a heading that
    ends with ``#``."""
    from docx import Document

    document = Document()
    document.add_heading(OFFICE_HEADING_WITH_HASH, level=1)
    for text in OFFICE_INJECTION_PARAGRAPHS:
        document.add_paragraph(text)
    document.add_paragraph("Closing paragraph of the memo.")
    document.save(str(path))
    return Path(path)


def line_end_hyphen_breaks(pdf: Path) -> int:
    """How many lines of ``pdf`` end with a letter and a hyphen and are followed, in the same
    block, by a line that starts with a letter: the case line-end hyphen handling is about."""
    count = 0
    with pymupdf.open(str(pdf)) as doc:
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                lines = ["".join(span["text"] for span in line["spans"]).strip() for line in block.get("lines", [])]
                count += sum(1 for line, following in zip(lines, lines[1:])
                             if len(line) > 1 and line[-1] == "-" and line[-2].isalpha() and following[:1].isalpha())
    return count


def line_texts(pdf: Path) -> list:
    """The text of every line of the first page of ``pdf``, stripped."""
    with pymupdf.open(str(pdf)) as doc:
        return ["".join(span["text"] for span in line["spans"]).strip()
                for block in doc[0].get_text("dict")["blocks"] for line in block.get("lines", [])]


def block_tops(pdf: Path) -> dict:
    """PyMuPDF's bbox top of every text block of the first page, keyed by the block's text."""
    with pymupdf.open(str(pdf)) as doc:
        return {"\n".join("".join(span["text"] for span in line["spans"]) for line in block["lines"]).strip():
                block["bbox"][1] for block in doc[0].get_text("dict")["blocks"] if block.get("type") == 0}


def office_to_pdf(source: Path, out_dir: Path, timeout: int = 180) -> Path:
    """Export a .docx/.pptx to PDF with LibreOffice headless (its own profile dir, so parallel
    runs cannot collide) and return the PDF path."""
    soffice = shutil.which("soffice")
    if soffice is None:
        raise FileNotFoundError("soffice is not on PATH")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = out_dir / f"lo-profile-{Path(source).stem}"
    subprocess.run(
        [soffice, f"-env:UserInstallation={profile.resolve().as_uri()}", "--headless",
         "--convert-to", "pdf", "--outdir", str(out_dir), str(source)],
        check=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
    )
    pdf = out_dir / f"{Path(source).stem}.pdf"
    if not pdf.exists():
        raise FileNotFoundError(f"LibreOffice did not write {pdf}")
    return pdf
