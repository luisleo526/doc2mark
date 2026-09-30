"""Run every code example in README.md and docs/ and compare what it does with what the docs say.

Every ``python``, ``bash`` and ``text`` code block (and every ``::`` literal block) in README.md,
docs/*.rst and docs/api/*.rst is found and must be claimed by exactly one entry of
``examples_manifest.MANIFEST``; an unclaimed block, or an entry that matches no block, fails the
run. An entry says how its block is checked:

- ``python``: the block is written to a file and run as written with ``python`` in a scratch
  workspace whose files carry the names the docs use (``report.pdf``, ``scan.pdf``, ``slides.pptx``,
  ``receipt.png``, ``documents/`` ...: copies of ``sample_documents/`` and a few generated fixtures,
  see ``build_workspace``). ``prelude`` may define the inputs a fragment takes as given
  (``image_bytes = Path("receipt.png").read_bytes()``); nothing in the block itself is changed.
- ``bash``: the block runs with ``bash -e`` in the same workspace; ``pip install`` lines are
  skipped (the runner installs the extras before the run) and so are ``export X=<placeholder>``
  lines (the entry's ``env`` provides working values).
- ``check``: a text block that shows output (or a signature) is compared with what ``check``
  (Python code run in the workspace) prints: exactly, as a set of lines that must all appear
  (``compare="lines"``; ``...`` lines are ignored), or not at all (``compare="none"``: the check
  script asserts what it needs and its exit code decides).
- ``skip``: not run; ``reason`` says why and how the block was verified instead.

``expect`` (substrings or ``re:`` regexes) must all appear in stdout+stderr, ``expect_not`` must
not, and the exit code must be ``exit`` (default 0). ``openai=True`` runs the block against the
local stand-in for the OpenAI API from the E2E suite (``tests/e2e/fake_openai.py``): the real
provider, LangChain and openai SDK code runs and talks HTTP to it; only the model is replaced, so
such a run checks the API shapes and code paths, not what a real model reads.

Run it inside the E2E image (Tesseract + LibreOffice), from the repository root::

    eval/docs_audit/run_on_spark.sh          # docker wrapper, see that file
    python eval/docs_audit/docs_examples.py --report /tmp/examples.md [--only README.md] [-k text]

Exit code: 0 when every block passed or was skipped with a reason, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from examples_manifest import MANIFEST, Entry  # noqa: E402

DOC_FILES = ["README.md"] + sorted(str(p.relative_to(ROOT)) for p in (ROOT / "docs").rglob("*.rst"))


@dataclass
class Block:
    file: str
    line: int
    lang: str
    code: str
    entry: Optional[Entry] = None
    status: str = "unclaimed"
    detail: str = ""
    output: str = ""
    seconds: float = 0.0

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


# --------------------------------------------------------------------------- block extraction

_FENCE = re.compile(r"^(```+|~~~+)\s*([\w+-]*)")
_DIRECTIVE = re.compile(r"^(\s*)\.\. (?:code-block|code|sourcecode)::\s*(\S*)")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def markdown_blocks(path: Path) -> List[Block]:
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks, i = [], 0
    while i < len(lines):
        m = _FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        fence, lang = m.group(1), (m.group(2) or "text").lower()
        j = i + 1
        while j < len(lines) and not lines[j].startswith(fence):
            j += 1
        blocks.append(Block(str(path.relative_to(ROOT)), i + 2, lang, "\n".join(lines[i + 1:j])))
        i = j + 1
    return blocks


def rst_blocks(path: Path) -> List[Block]:
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks = []
    for i, line in enumerate(lines):
        m = _DIRECTIVE.match(line)
        stripped = line.strip()
        if m:
            base, lang = len(m.group(1)), (m.group(2) or "text").lower()
        elif stripped.endswith("::") and not stripped.startswith(".."):
            base, lang = _indent(line), "literal"
        else:
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip().startswith(":") and _indent(lines[j]) > base:
            j += 1  # directive options
        while j < len(lines) and not lines[j].strip():
            j += 1
        start, body = j, []
        while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) > base):
            body.append(lines[j])
            j += 1
        while body and not body[-1].strip():
            body.pop()
        if body:
            blocks.append(Block(str(path.relative_to(ROOT)), start + 1, lang, textwrap.dedent("\n".join(body))))
    return blocks


def all_blocks(only: Optional[str] = None) -> List[Block]:
    blocks: List[Block] = []
    for name in DOC_FILES:
        if only and only not in name:
            continue
        path = ROOT / name
        blocks += markdown_blocks(path) if name.endswith(".md") else rst_blocks(path)
    return blocks


def claim(blocks: List[Block]) -> List[str]:
    """Attach each manifest entry to the one block it matches; return the problems found."""
    problems = []
    for entry in MANIFEST:
        hits = [b for b in blocks if b.file == entry.file and entry.contains in b.code]
        if len(hits) != 1:
            problems.append(f"manifest entry {entry.file} / {entry.contains!r} matches {len(hits)} blocks")
            continue
        if hits[0].entry is not None:
            problems.append(f"{hits[0].where} is claimed twice ({entry.contains!r})")
            continue
        hits[0].entry = entry
    return problems


# --------------------------------------------------------------------------- workspace

def build_workspace(work: Path) -> None:
    """Files under the names the docs use: copies of sample_documents/ plus generated fixtures."""
    from tests.e2e import pdfgen

    samples = ROOT / "sample_documents"
    shutil.copytree(samples, work / "sample_documents")
    copies = {
        "report.pdf": "sample_pdf.pdf",
        "document.pdf": "sample_pdf.pdf",
        "report.docx": "sample_document.docx",
        "slides.pptx": "sample_presentation.pptx",
        "spreadsheet.xlsx": "sample_spreadsheet.xlsx",
        "data.csv": "sample_data.csv",
        "legacy.doc": "sample_legacy_document.doc",
        "notes.md": "sample_document.md",
        "quarterly_report.pdf": "complex-tables/complex_table_test.pdf",
        "quarterly_report.docx": "complex-tables/complex_table_test.docx",
        "spec-sheet.pdf": "test-table.pdf",
    }
    for name, source in copies.items():
        shutil.copy(samples / source, work / name)
    pdfgen.image_pdf(work / "scan.pdf", ["INVOICE 2041\nTotal due 1,250.00"])
    (work / "receipt.png").write_bytes(pdfgen.text_png("ACME STORE\nTotal 9.50"))
    (work / "archive.7z").write_bytes(b"7z\xbc\xaf\x27\x1c")
    docs = work / "documents"
    (docs / "2025").mkdir(parents=True)
    for name, source in (("report.pdf", "sample_pdf.pdf"), ("notes.md", "sample_document.md"),
                         ("data.csv", "sample_data.csv"), ("2025/summary.docx", "sample_document.docx")):
        shutil.copy(samples / source, docs / name)
    (work / "reports").mkdir()
    for name in ("q1.pdf", "q2.pdf"):
        shutil.copy(samples / "sample_pdf.pdf", work / "reports" / name)
    (work / "notes").mkdir()
    shutil.copy(samples / "sample_document.docx", work / "notes" / "meeting.docx")


# --------------------------------------------------------------------------- fake OpenAI

def start_fake_openai():
    from tests.e2e import fake_openai as fo

    fake = fo.FakeOpenAI().__enter__()
    table = fo.table(html="<table><tr><th>Item</th><th>Price</th></tr><tr><td>Apples</td><td>$3.00</td></tr></table>",
                     headers=["Item", "Price"], rows=[["Apples", "$3.00"]], caption="Items")
    fake.script(
        structured=[fo.page(
            "ACME STORE\nApples $3.00\nTotal $9.50",
            tables=[table],
            fields=[{"label": "Total", "value": "$9.50"}],
            interpretation=fo.interpretation(document_type="receipt", summary="A grocery receipt totalling $9.50.",
                                             self_confidence=0.9, legibility="high"),
        )],
        free_form=[fo.text("ACME STORE\n\nApples $3.00\n\nTotal $9.50")],
    )
    return fake


# --------------------------------------------------------------------------- running

def _matches(needle: str, haystack: str) -> bool:
    if needle.startswith("re:"):
        return re.search(needle[3:], haystack, re.S | re.M) is not None
    return needle in haystack


def _run(cmd: List[str], cwd: Path, env: Dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def _bash_script(code: str) -> str:
    kept = []
    for line in code.splitlines():
        s = line.strip()
        if s.startswith("pip install") or re.match(r"export \w+=(sk-|\.\.\.|/path/|my-)", s):
            kept.append(f"# (skipped by docs_examples.py) {line}")
        else:
            kept.append(line)
    return "\n".join(kept)


def run_block(block: Block, root_work: Path, base_env: Dict[str, str], fake_env: Dict[str, str]) -> None:
    entry = block.entry
    assert entry is not None
    if entry.mode == "skip":
        block.status, block.detail = "skip", entry.reason
        return
    work = Path(tempfile.mkdtemp(prefix="ex-", dir=root_work))
    shutil.copytree(root_work / "_base", work, dirs_exist_ok=True)
    env = dict(base_env)
    if entry.openai:
        env.update(fake_env)
    env.update(entry.env)
    start = time.time()
    try:
        if entry.mode == "python":
            (work / "example.py").write_text((entry.prelude + "\n" if entry.prelude else "") + block.code, encoding="utf-8")
            proc = _run([sys.executable, "example.py"], work, env, entry.timeout)
        elif entry.mode == "bash":
            (work / "example.sh").write_text(_bash_script(block.code), encoding="utf-8")
            proc = _run(["bash", "-e", "example.sh"], work, env, entry.timeout)
        elif entry.mode == "check":
            (work / "block.txt").write_text(block.code + "\n", encoding="utf-8")
            (work / "check.py").write_text(entry.check, encoding="utf-8")
            proc = _run([sys.executable, "check.py"], work, env, entry.timeout)
        else:
            raise ValueError(f"unknown mode {entry.mode}")
    except subprocess.TimeoutExpired:
        block.status, block.detail, block.seconds = "FAIL", f"timed out after {entry.timeout}s", time.time() - start
        return
    block.seconds = time.time() - start
    out = proc.stdout + ("\n--- stderr ---\n" + proc.stderr if proc.stderr.strip() else "")
    block.output = out
    problems = []
    if proc.returncode != entry.exit:
        problems.append(f"exit code {proc.returncode}, expected {entry.exit}")
    if entry.mode == "check":
        shown = block.code.strip("\n")
        got = proc.stdout.strip("\n")
        if entry.compare == "exact" and got != shown:
            problems.append("output differs from the block:\n" + got)
        elif entry.compare == "lines":
            missing = [ln for ln in shown.splitlines()
                       if ln.strip() and ln.strip() not in ("...", "\u2026") and ln.strip() not in got]
            if missing:
                problems.append(f"lines not in the output: {missing[:5]}")
    for needle in entry.expect:
        if not _matches(needle, out):
            problems.append(f"expected {needle!r} in the output")
    for needle in entry.expect_not:
        if _matches(needle, out):
            problems.append(f"did not expect {needle!r} in the output")
    block.status = "FAIL" if problems else "pass"
    block.detail = "; ".join(problems) if problems else (entry.note or "")


def summary_line(block: Block) -> str:
    head = (block.output.strip().splitlines() or [""])[0][:100]
    return head.replace("|", "\\|")


def write_report(blocks: List[Block], problems: List[str], path: Path, meta: Dict[str, str]) -> None:
    rows = ["| block | lang | mode | status | detail / first output line |", "|---|---|---|---|---|"]
    for b in blocks:
        mode = b.entry.mode + (" (fake OpenAI)" if b.entry and b.entry.openai else "") if b.entry else "-"
        detail = (b.detail or summary_line(b)).replace("\n", " ").replace("|", "\\|")[:300]
        rows.append(f"| `{b.where}` | {b.lang} | {mode} | {b.status} | {detail} |")
    counts: Dict[str, int] = {}
    for b in blocks:
        counts[b.status] = counts.get(b.status, 0) + 1
    text = ["# Docs examples run", ""] + [f"- {k}: {v}" for k, v in meta.items()]
    text += [f"- results: {json.dumps(counts, sort_keys=True)}", ""]
    if problems:
        text += ["## Manifest problems", ""] + [f"- {p}" for p in problems] + [""]
    text += ["## Blocks", ""] + rows + [""]
    fails = [b for b in blocks if b.status in ("FAIL", "unclaimed")]
    if fails:
        text += ["## Failures", ""]
        for b in fails:
            text += [f"### {b.where}", "", "```", b.code, "```", "", b.detail, "", "```", b.output[-3000:], "```", ""]
    path.write_text("\n".join(text), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", type=Path, default=Path("docs_examples.md"))
    parser.add_argument("--json", type=Path, default=None, help="write every block's result and output here")
    parser.add_argument("--only", default=None, help="only files whose path contains this")
    parser.add_argument("-k", default=None, help="only blocks whose code contains this")
    args = parser.parse_args()

    blocks = all_blocks(args.only)
    problems = claim(blocks) if not args.only else [p for p in claim(blocks) if args.only in p]
    root_work = Path(tempfile.mkdtemp(prefix="d2m-docs-examples-"))
    build_workspace(root_work / "_base")
    fake = start_fake_openai()
    host, port = fake._server.server_address[:2]
    fake_env = {"OPENAI_BASE_URL": f"http://{host}:{port}/v1", "OPENAI_API_KEY": "sk-e2e-fake-not-a-real-key"}
    base_env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "OPENAI_BASE_URL")}
    base_env["PYTHONPATH"] = str(ROOT)
    base_env["DOCS_AUDIT_ROOT"] = str(ROOT)
    for block in blocks:
        if args.k and args.k not in block.code:
            block.status = "not selected"
            continue
        if block.entry is None:
            problems.append(f"{block.where}: no manifest entry ({block.lang}: {block.code.splitlines()[0][:60] if block.code else ''!r})")
            continue
        run_block(block, root_work, base_env, fake_env)
        print(f"{block.status:5} {block.where} {block.detail[:120]}", flush=True)
    fake.__exit__(None, None, None)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    import doc2mark
    meta = {"doc2mark": f"{doc2mark.__version__} ({doc2mark.__file__})", "git": sha or os.environ.get("D2M_SHA", "?"),
            "host": os.environ.get("D2M_HOST", os.uname().nodename), "date": time.strftime("%Y-%m-%d %H:%M %Z"),
            "python": sys.version.split()[0]}
    write_report(blocks, problems, args.report, meta)
    if args.json:
        args.json.write_text(json.dumps([{"where": b.where, "lang": b.lang, "status": b.status, "detail": b.detail,
                                          "mode": b.entry.mode if b.entry else None, "output": b.output[-4000:]}
                                         for b in blocks], indent=1, ensure_ascii=False), encoding="utf-8")
    bad = [b for b in blocks if b.status in ("FAIL", "unclaimed")] or problems
    print(f"{len(blocks)} blocks; problems: {len(problems)}; failed: {sum(b.status == 'FAIL' for b in blocks)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
