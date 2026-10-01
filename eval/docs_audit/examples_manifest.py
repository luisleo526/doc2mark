"""How each code block of README.md and docs/ is checked by docs_examples.py.

One entry per block, found by ``file`` and a substring ``contains`` that occurs in exactly one
block of that file. Modes and fields are described in docs_examples.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Entry:
    file: str
    contains: str
    mode: str = "python"          # python | bash | check | skip
    expect: List[str] = field(default_factory=list)
    expect_not: List[str] = field(default_factory=list)
    exit: int = 0
    prelude: str = ""
    check: str = ""
    compare: str = "exact"        # exact | lines (check mode)
    openai: bool = False
    env: Dict[str, str] = field(default_factory=dict)
    reason: str = ""
    note: str = ""
    timeout: int = 600


IMAGE = 'from pathlib import Path\nimage_bytes = Path("receipt.png").read_bytes()\n'
IMAGES = 'from pathlib import Path\nimages = [Path("receipt.png").read_bytes()] * 3\n'

MANIFEST: List[Entry] = []

INSTALL = "not run: the runner installs doc2mark with these extras (pip install -e \".[ocr,dev,docs,tokenizers,redis,typesafe]\")"
APT = "not run: tests/e2e/Dockerfile installs exactly these Debian packages, and every example runs in that image"
FULL_SUITE = "not run here: the full unit and E2E suites were run separately on the same commit (see the PR)"


def _add(*entries: Entry) -> None:
    MANIFEST.extend(entries)


# --------------------------------------------------------------------------- installation.rst
F = "docs/installation.rst"
_add(
    Entry(F, "pip install doc2mark", mode="skip", reason=INSTALL),
    Entry(F, 'pip install "doc2mark[ocr,tokenizers]"', mode="skip", reason=INSTALL),
    Entry(F, "tesseract-ocr-chi-tra", mode="skip", reason=APT),
    Entry(F, "libreoffice-writer libreoffice-calc", mode="skip", reason=APT),
    Entry(F, "doc2mark --help", mode="bash", expect=["usage: doc2mark", "re:^\\d+\\.\\d+\\.\\d+$"]),
)

# --------------------------------------------------------------------------- quickstart.rst
F = "docs/quickstart.rst"
_add(
    Entry(F, 'print(result.metadata.page_count)      # 2', expect=["DocumentFormat.PDF", "re:^2$"]),
    Entry(F, 'for name in ["report.pdf", "report.docx", "slides.pptx", "data.csv"]',
          expect=["report.pdf", "report.docx", "slides.pptx", "data.csv"]),
    Entry(F, 'for item in result.json_content[:4]', expect=["text:title 1 1"]),
    Entry(F, 'as_json = load("report.pdf", output_format="json").content',
          expect=["['content', 'images', 'json_content', 'metadata', 'sections', 'tables']"]),
    Entry(F, 'loader = UnifiedDocumentLoader(ocr_provider="tesseract")',
          expect=["INVOICE 2041", "{'document_route': 'image', 'overrides': []}"]),
    Entry(F, 'UnifiedDocumentLoader(ocr_provider="openai")      # reads OPENAI_API_KEY', openai=True),
    Entry(F, 'results = loader.batch_process("documents", output_dir="converted", max_workers=4)',
          expect=["success documents/report.pdf", "success documents/2025/summary.docx"]),
    Entry(F, "doc2mark report.pdf -o report.md", mode="bash", expect=["Output saved to: report.md", "Output saved to: scan.md"]),
)

# --------------------------------------------------------------------------- rag.rst
F = "docs/rag.rst"
_add(
    Entry(F, "def search(query, k=3):", expect=["re:^\\d+ chunks$", "report.pdf#0"]),
    Entry(F, 'print(result.metadata.extra.get("ocr_issues"))', expect=["INVOICE", "re:^None$"]),
)

# --------------------------------------------------------------------------- chunking.rst
F = "docs/chunking.rst"
_add(
    Entry(F, "ChunkingConfig(max_chunk_size=800, overlap=100)", expect=["re:^0 "]),
    Entry(F, 'size_unit="tokens"', expect=["chunks of at most about 512 tokens"]),
    Entry(F, 'print(result.metadata.extra["token_usage"])', openai=True,
          expect=["'input_tokens':", "'output_tokens':", "'total_tokens':"]),
)

# --------------------------------------------------------------------------- output.rst
F = "docs/output.rst"
_add(
    Entry(F, "from collections import Counter", expect=["Counter({", "'text:title'"]),
    Entry(F, 'print(extra["ocr_images"]["ocr_requests"], "image(s) sent to OCR")',
          expect=["'document_route': 'image'", "1 image(s) sent to OCR"], expect_not=["check before indexing"]),
)

# --------------------------------------------------------------------------- loading.rst
F = "docs/loading.rst"
_add(
    Entry(F, 'cache_dir=".doc2mark-cache",     # reuse converted documents across runs', expect=["Sample DOCX Document"]),
    Entry(F, 'result = loader.load("data.csv", encoding="utf-8")', expect=["re:^\\d+ \\d+$"]),
    Entry(F, 'markdown = document_to_markdown("report.docx", output_path="out/report.md")',
          expect=["Markdown saved to: out/report.md"]),
    Entry(F, 'failed = {path: info["error"]', expect=["4 files, 0 failed", "1/4 "]),
    Entry(F, '{"status": "success", "format": "pdf", "content_length": 8120', mode="check", compare="none", check='''
from doc2mark import UnifiedDocumentLoader
loader = UnifiedDocumentLoader(ocr_provider=None)
ok = loader.batch_process("documents", output_dir="converted", show_progress=False)["documents/report.pdf"]
assert sorted(ok) == ["content_length", "duration", "format", "metadata", "output_files", "status"], ok
assert sorted(ok["metadata"]) == ["images_extracted", "pages", "tables_found"], ok
assert ok["format"] == "pdf" and ok["output_files"] == ["converted/report.md"] and ok["metadata"]["pages"] == 2, ok
open("bad.pdf", "wb").write(b"not a pdf")
bad = loader.batch_process_files(["bad.pdf"], show_progress=False)["bad.pdf"]
assert sorted(bad) == ["error", "format", "status"] and bad["status"] == "failed" and bad["format"] == ".pdf", bad
assert bad["error"].startswith("Processing failed:"), bad
print(ok); print(bad)
'''),
    Entry(F, 'documents = loader.load_directory("documents", pattern="*.pdf")', expect=["['report.pdf']"]),
)

# --------------------------------------------------------------------------- cli.rst
F = "docs/cli.rst"
_add(
    Entry(F, "doc2mark report.pdf --format both -o report # write report.md and report.json", mode="bash",
          expect=["Output saved to: report.md", "Output saved to: report.json", "Output saved to: report.md and report.json",
                  "Output saved to: scan.md"]),
    Entry(F, "--ocr-lang eng+chi_tra -o scan.md", mode="bash"),
)

# --------------------------------------------------------------------------- development.rst
F = "docs/development.rst"
_add(
    Entry(F, "git clone https://github.com/luisleo526/doc2mark", mode="skip",
          reason="not run: clone and editable install are what the runner does with this checkout"),
    Entry(F, 'python -m pytest -m "not integration and not requires_api_key and not e2e" -q   # as CI runs them',
          mode="skip", reason=FULL_SUITE),
    Entry(F, "scripts/run_e2e_docker.sh -q -n 8              # the E2E suite", mode="skip", reason=FULL_SUITE),
    Entry(F, 'D2M_E2E_EXTRAS="ocr,dev,typesafe" D2M_REQUIRE_TYPESAFE=1', mode="skip",
          reason="not run: needs docker on the host and a TypeSafe key; the variables are read by scripts/run_e2e_docker.sh and tests/conftest.py"),
    Entry(F, "python -m pytest -m e2e -q", mode="skip", reason=FULL_SUITE),
    Entry(F, "def test_ocr_reads_scanned_page(run_cli, require_tool, e2e_dir):", mode="check", compare="none", check='''
import os, subprocess, sys
from pathlib import Path
root = Path(os.environ["DOCS_AUDIT_ROOT"])
test = root / "tests" / "e2e" / "test_docs_audit_example.py"
test.write_text(Path("block.txt").read_text())
try:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test)], cwd=root,
                          capture_output=True, text=True, env={**os.environ, "D2M_E2E_STRICT": "1"})
finally:
    test.unlink()
print(proc.stdout[-800:], proc.stderr[-400:])
sys.exit(proc.returncode)
''', expect=["1 passed"], note="run as a pytest test in tests/e2e"),
    Entry(F, "python -m sphinx -b html -W --keep-going docs docs/_build/html", mode="skip",
          reason="not run here: run_on_spark.sh runs exactly this build after the examples (sphinx.log), and so does the Docs workflow"),
)

# --------------------------------------------------------------------------- troubleshooting.rst
_add(Entry("docs/troubleshooting.rst", "logging.basicConfig(level=logging.INFO)", expect=["INFO:doc2mark"]))

# --------------------------------------------------------------------------- images.rst
F = "docs/images.rst"
_add(
    Entry(F, 'result = loader.load("receipt.png", ocr_images=True)', expect=["# Image: receipt.png", "## OCR Extracted Text", "ACME STORE"]),
    Entry(F, "- **Dimensions**: 1654 x 2339 pixels", mode="check", compare="exact", check='''
from doc2mark import UnifiedDocumentLoader
print(UnifiedDocumentLoader(ocr_provider="tesseract").load("receipt.png", ocr_images=True).content)
'''),
)

# --------------------------------------------------------------------------- caching.rst
F = "docs/caching.rst"
_add(
    Entry(F, "cache = MemoryOCRCache(ttl_seconds=3600, max_entries=1024)", expect=["re:^1 1$"]),
    Entry(F, 'off = create_ocr_cache("none")', expect=["re:^MemoryOCRCache (RedisOCRCache|MemoryOCRCache) None$"]),
    Entry(F, 'loader = UnifiedDocumentLoader(ocr_provider=None, cache_dir=".doc2mark-cache")', expect=["re:^True$"]),
)

# --------------------------------------------------------------------------- tables.rst
F = "docs/tables.rst"
TABLE_CODE = '''
from doc2mark.core.table import TableData, TableRenderer, TableStyle

rows = [
    ["", "", "Revenue (USD)", "", ""],
    ["Region", "Country", "2023", "2024", "2025"],
    ["Americas", "USA", "$4.2B", "$4.8B", "$5.1B"],
    ["", "Canada", "$0.9B", "$1.0B", "$1.1B"],
]
spans = {(0, 2): (1, 3), (2, 0): (2, 1)}
table = TableData.from_raw(rows, {"is_complex": True, "cell_spans": spans})
print(TableRenderer(TableStyle.MINIMAL_HTML).render(table))
print(TableRenderer(TableStyle.MARKDOWN_GRID).render(table))
'''
_add(
    Entry(F, "spans = {(0, 2): (1, 3), (2, 0): (2, 1)}      # (row, column): (rows, columns)", expect=['<th colspan="3">Revenue (USD)</th>']),
    Entry(F, "<!-- Merged: R1C3:1x3, R3C1:2x1 -->", mode="check", compare="exact", check=TABLE_CODE),
    Entry(F, 'print(loader.load("quarterly_report.docx").content[:1200])',
          expect=["<!-- Merged:", "Company Overview ⊕"]),
    Entry(F, 'table = TableData.from_raw([["Item", "Note"]', expect=["| Net<br>income |"]),
    Entry(F, "| Net<br>income | a \\| b", mode="check", compare="exact", check='''
from doc2mark.core.table import TableData, TableRenderer
table = TableData.from_raw([["Item", "Note"], ["Net\\nincome", "a | b <img src=x> &lt; C:\\\\*.txt"]])
print(TableRenderer().render(table))
'''),
    Entry(F, 'tables = [item for item in result.json_content if item["type"] == "table"]', expect=["re:^1 1$"]),
)

# --------------------------------------------------------------------------- pdf.rst
_add(Entry("docs/pdf.rst", 'if item["type"] in ("text:title", "text:section"):', expect=["re:^1 "]))

# --------------------------------------------------------------------------- api/convenience.rst
_add(Entry("docs/api/convenience.rst", '["reports/q1.pdf", "reports/q2.pdf", "notes/meeting.docx"]',
           expect=["'reports/q1.pdf': 'success'", "'notes/meeting.docx': 'success'"]))


# --------------------------------------------------------------------------- README.md
F = "README.md"
_add(
    Entry(F, 'print(load("report.pdf").content)', expect=["Sample DOCX Document"]),
    Entry(F, "pip install doc2mark                # PDF, Office", mode="skip", reason=INSTALL),
    Entry(F, "loader = UnifiedDocumentLoader()          # OCR is only used when you ask for it", expect=["re:^2$"]),
    Entry(F, 'loader = UnifiedDocumentLoader(ocr_provider="tesseract")   # or "openai", "vertex_ai"',
          expect=["INVOICE 2041", "'document_route': 'image'"]),
    Entry(F, 'doc2mark documents/ -r --pattern "*.pdf" -o converted/', mode="bash",
          expect=["Output saved to: report.md", "Output saved to: scan.md", "Processed 1 files to: converted"]),
    Entry(F, '<th colspan="3">Company Overview</th>', mode="check", compare="lines", check="""
from doc2mark import UnifiedDocumentLoader
print(UnifiedDocumentLoader(ocr_provider=None).load("sample_documents/complex-tables/complex_table_test.docx").content)
"""),
    Entry(F, 'loader = UnifiedDocumentLoader(table_style="minimal_html")     # HTML with rowspan/colspan (default)',
          expect=['<th colspan="3">Company Overview</th>']),
    Entry(F, 'result = ocr.read_one(open("receipt.png", "rb").read(), task="receipt")', openai=True,
          expect=["ACME STORE", "('Total', '$9.50')", "receipt"]),
    Entry(F, 'pip install -e ".[all,dev]"', mode="skip", reason=FULL_SUITE),
)

# --------------------------------------------------------------------------- index.rst
_add(Entry("docs/index.rst", "chunks = result.get_chunks()                 # section-aware chunks with page spans"))

# --------------------------------------------------------------------------- formats.rst
F = "docs/formats.rst"
_add(
    Entry(F, "print(loader.supported_formats)", expect=["['docx', 'xlsx', 'pptx', 'doc', 'xls', 'ppt', 'rtf', 'pps', 'pdf',"]),
    Entry(F, 'doc = loader.load("report.docx")', expect=["DocumentFormat.DOCX"]),
    Entry(F, 'doc = UnifiedDocumentLoader(ocr_provider=None).load("legacy.doc")', expect=["DocumentFormat.DOC doc docx"]),
    Entry(F, 'doc = UnifiedDocumentLoader(ocr_provider=None).load("data.csv")', expect=["re:^, \\d+ \\d+$", "re:^\\| "]),
)

# --------------------------------------------------------------------------- ocr.rst
F = "docs/ocr.rst"
_add(
    Entry(F, 'openai_loader = UnifiedDocumentLoader(ocr_provider="openai", model="gpt-5.4-mini")',
          expect=["gpt-5.4-mini gemini-3.1-flash-lite-preview"]),
    Entry(F, 'results = ocr.read([open("receipt.png", "rb").read()])     # one OCRResult per image, in order', openai=True,
          expect=["ACME STORE", "receipt A grocery receipt", "[('Items', '<table>"]),
    Entry(F, 'mixed = ocr.read(images, tasks=["receipt", "handwriting"])    # one per image', openai=True, expect=["re:^None 2$"]),
    Entry(F, 'ocr = OCR("tesseract", language="eng+chi_tra")', expect=["ACME STORE", "INVOICE 2041"]),
    Entry(F, "def non_content_judge(ocr_text):", openai=True, expect=["ACME STORE"]),
    Entry(F, 'ocr = OCR("openai", max_concurrency=16)', openai=True, expect=["re:^gpt-5.4-mini \\d+$"]),
)

# --------------------------------------------------------------------------- ocr_policy.rst
F = "docs/ocr_policy.rst"
_add(
    Entry(F, "decide_doc_strategy(0.92, 35)", mode="check", compare="none", check="""
from doc2mark.core.strategy import decide_doc_strategy
got = [decide_doc_strategy(0.92, 35), decide_doc_strategy(0.70, 900), decide_doc_strategy(0.10, 1200),
       decide_doc_strategy(0.95, 900, 0.5)]
print(got)
assert got == ["image", "text", "text", "image"], got
""", note="the commented results are what the calls return"),
    Entry(F, '"image"  iff  mean_image_coverage >= 0.55', mode="check", compare="none", check="""
from doc2mark.core import strategy
print(strategy.IMAGE_PAGE_COVERAGE, strategy.IMAGE_PAGE_TEXT_LIMIT)
assert (strategy.IMAGE_PAGE_COVERAGE, strategy.IMAGE_PAGE_TEXT_LIMIT) == (0.55, 200)
assert strategy.decide_doc_strategy(0.55, 199.9) == "image" and strategy.decide_doc_strategy(0.5499, 10) == "text"
assert strategy.decide_doc_strategy(0.9, 200) == "text"
"""),
    Entry(F, "Document OCR strategy: image (mean coverage 0.94", mode="check", compare="none", check="""
import logging
logging.basicConfig(level=logging.INFO)
from doc2mark import UnifiedDocumentLoader
UnifiedDocumentLoader(ocr_provider="tesseract").load("scan.pdf", ocr_images=True)
""", expect=["re:Document OCR strategy: image \\(mean coverage [0-9.]+, mean legible text \\d+/page, garbled text pages \\d+%\\)"]),
    Entry(F, "def judge(page_text: str) -> float | None:"),
    Entry(F, 'scan = loader.load("scan.pdf", ocr_images=True)',
          expect=["{'document_route': 'image', 'overrides': []}", "{'document_route': 'text', 'overrides': []}", "re:^None$"]),
)

# --------------------------------------------------------------------------- contextual_ocr.rst
F = "docs/contextual_ocr.rst"
_add(
    Entry(F, "context_pages: int = 0", mode="check", compare="none", check="""
import dataclasses
from doc2mark import OCRConfig
field = {f.name: f for f in dataclasses.fields(OCRConfig)}["context_pages"]
print(field.type, field.default)
assert field.default == 0 and field.type in (int, "int")
"""),
    Entry(F, '"filename": "context.pdf",', mode="check", compare="none", check="""
import os
from pathlib import Path
src = (Path(os.environ["DOCS_AUDIT_ROOT"]) / "doc2mark/ocr/openai.py").read_text()
for needle in ('"type": "file"', '"filename": "context.pdf"', 'f"data:application/pdf;base64,{context_pdf}"'):
    assert needle in src, needle
print("excerpt matches doc2mark/ocr/openai.py")
"""),
    Entry(F, '"mime_type": "application/pdf",', mode="check", compare="none", check="""
import os
from pathlib import Path
src = (Path(os.environ["DOCS_AUDIT_ROOT"]) / "doc2mark/ocr/vertex_ai.py").read_text()
for needle in ('"type": "media"', '"mime_type": "application/pdf"', '"data": context_pdf'):
    assert needle in src, needle
print("excerpt matches doc2mark/ocr/vertex_ai.py")
"""),
    Entry(F, "ocr_config=OCRConfig(context_pages=1),  # context on whole-page renders", openai=True),
)

# --------------------------------------------------------------------------- judge.rst
F = "docs/judge.rst"
_add(
    Entry(F, "pip install 'doc2mark[typesafe]'", mode="bash", expect=["Sample DOCX Document"],
          note="calls TypeSafe when TYPESAFE_API_KEY is set in the run"),
    Entry(F, 'loader = UnifiedDocumentLoader(ocr_provider="tesseract", judge="typesafe")'),
    Entry(F, 'judge = TypeSafeJudge(hooks=("legibility", "non_content"),   # or $DOC2MARK_JUDGE_HOOKS'),
)

# --------------------------------------------------------------------------- api pages
_add(
    Entry("docs/api/loader.rst", "UnifiedDocumentLoader(\n    ocr_provider='openai',", mode="check", compare="exact", check="""
import inspect
from doc2mark import UnifiedDocumentLoader
lines = ["UnifiedDocumentLoader("]
for p in list(inspect.signature(UnifiedDocumentLoader.__init__).parameters.values())[1:]:
    d = p.default
    d = getattr(d, "value", d)
    lines.append(f"    {p.name}={d!r},")
lines.append(")")
print("\\n".join(lines))
"""),
    Entry("docs/api/ocr.rst", "print(sorted(OCRFactory.list_providers()))",
          expect=["['gemini', 'openai', 'tesseract', 'vertex_ai']", "ACME STORE"]),
    Entry("docs/api/schema.rst", "class OCRPage(BaseModel):", mode="check", compare="none", check="""
from doc2mark.ocr.schema import OCRPage, RawExtraction
fields = OCRPage.model_fields
assert set(fields) == {"raw", "interpretation"}, set(fields)
assert fields["raw"].default_factory is RawExtraction and fields["interpretation"].default is None
print("OCRPage fields:", sorted(fields))
"""),
    Entry("docs/api/schema.rst", "from doc2mark.ocr.schema import (", expect=["ACME STORE", "Total"]),
    Entry("docs/api/types.rst", 'for name in ["archive.7z", "legacy.doc", "report.pdf"]:',
          expect=["archive.7z unsupported: Cannot detect format for extension: 7z", "legacy.doc ok", "report.pdf ok"]),
)

_add(
    Entry("README.md", 'doc2mark report.pdf --ocr tesseract --ocr-images --judge typesafe -o report.md', mode="bash",
          expect=["Output saved to: report.md"], note="calls TypeSafe when TYPESAFE_API_KEY is set in the run"),
    Entry("README.md", 'loader = UnifiedDocumentLoader(ocr_provider="tesseract", judge="typesafe")  # or DOC2MARK_JUDGE=typesafe',
          note="prints the judge record when TYPESAFE_API_KEY is set, None otherwise"),
)
