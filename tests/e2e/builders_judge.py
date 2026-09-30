"""PDF builders for the judge E2E tests (``tests/e2e/test_judge.py``). PyMuPDF only; nothing
from ``doc2mark`` is imported.

``deck_pdf`` is shaped like the real Traditional-Chinese company deck the page-chrome review
used: 16:9 slides whose header row holds the logo text, a small brand line under it
("by <company>") and a numbered per-slide label ("03 / Pricing") at the right, above a big
slide title. The brand line repeats on every slide but touches the logo text and the label
row, so the rule cannot take it off the page edge and keeps it on every slide.
"""

from pathlib import Path
from typing import Sequence, Tuple

import pymupdf

SLIDE = (1440, 810)
BRAND_LINE = "by Contoso Labs"
LOGO_TEXT = "CONTOSO"

DECK_SLIDES: Sequence[Tuple[str, Sequence[str]]] = (
    ("Why teams adopt agents", ["Support tickets closed 38 percent faster", "Answers cite the source document",
                                "Every action is logged for audit"]),
    ("Product overview", ["One workspace for documents, flows and agents", "Runs on premises or in the cloud",
                          "Connectors for ERP, CRM and mail"]),
    ("Knowledge hub", ["Indexes 1.2 million pages per hour", "Keeps access rights of every file",
                       "Finds answers across 14 languages"]),
    ("Workflow engine", ["Drag and drop approval chains", "Calls internal APIs with retries",
                         "Escalates to a person after 2 failures"]),
    ("Governance", ["Role based permissions per folder", "Full audit trail kept for 7 years",
                    "Data never leaves the tenant"]),
    ("Manufacturing SPC", ["Control charts for 240 machines", "Alerts when a process drifts",
                           "Maintenance tickets opened automatically"]),
    ("Pricing", ["Starter plan from EUR 490 per month", "Enterprise plan with SSO and SLA",
                 "Volume discount above 500 seats"]),
    ("Customers and partners", ["200 signed customers", "10 enterprise groups",
                                "Partners in retail and manufacturing"]),
)


def deck_pdf(path: Path, slides: Sequence[Tuple[str, Sequence[str]]] = DECK_SLIDES) -> Path:
    """A slide deck (see the module docstring): per slide the header row (logo mark, ``LOGO_TEXT``,
    ``BRAND_LINE`` under it, the ``NN / title`` label at the right), the title and three body lines."""
    doc = pymupdf.open()
    width, height = SLIDE
    for number, (title, body) in enumerate(slides, 1):
        page = doc.new_page(width=width, height=height)
        page.draw_rect(pymupdf.Rect(40, 30, 88, 74), color=(0.1, 0.3, 0.6), fill=(0.1, 0.3, 0.6))
        page.insert_text((98, 56), LOGO_TEXT, fontname="helv", fontsize=19)
        page.insert_text((98, 71), BRAND_LINE, fontname="helv", fontsize=8.6)
        label = f"{number:02d} / {title}"
        label_width = pymupdf.get_text_length(label, fontname="helv", fontsize=20)
        page.insert_text((width - 52 - label_width, 62), label, fontname="helv", fontsize=20)
        page.insert_text((60, 190), title, fontname="helv", fontsize=48)
        y = 290
        for line in body:
            page.insert_text((60, y), line, fontname="helv", fontsize=20)
            y += 44
    doc.save(str(path))
    doc.close()
    return Path(path)
