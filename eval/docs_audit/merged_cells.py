#!/usr/bin/env python3
"""Re-measure the README table "Measured: merged-cell fidelity".

Converts each FILE with each requested tool that is importable (the others are
skipped with a note), counts the colspan / rowspan attributes on the <td>/<th>
tags of the output and prints a Markdown table, tool versions, host and date.

A cell `C / R` is the number of colspan / rowspan attributes whose value is > 1
(one per merged cell). If an output also carries span attributes of value 1 (or
without a number), the counts of all span attributes follow as `[raw c / r]`.

Outputs measured, each tool's default call with no options:
  doc2mark      from doc2mark import load; load(path).content   (the README's call)
  markitdown    MarkItDown().convert(path).text_content
  docling:md    DocumentConverter().convert(path).document.export_to_markdown()
  docling:html  the same document's export_to_html()

  python eval/docs_audit/merged_cells.py --tools doc2mark,markitdown,docling \\
      --json OUT.json [--dump DIR] [--show REGEX] FILE...
  python eval/docs_audit/merged_cells.py --merge A.json B.json [--json OUT.json]

--dump writes every output to DIR/<output>/<file name>; --show prints the lines
of every output that match REGEX, each HTML <tr>...</tr> joined onto one line;
--merge prints (and with --json saves) the combined table of earlier runs.

On spark, from a checkout, with doc2mark and the competitors in separate
containers so they do not share an environment (results/merged_cells.md has the
exact runs). doc2mark, in the E2E image (scripts/run_e2e_docker.sh):

  FILES="sample_documents/complex-tables/complex_table_test.docx
    sample_documents/complex-tables/complex_table_test.pdf
    sample_documents/complex-tables/complex_table_test.pptx
    sample_documents/complex-tables/complex_table_test.xlsx
    sample_documents/test-table.pdf"
  mkdir -p out && docker run --rm -h "$(hostname)" -v "$PWD:/src:ro" -v "$PWD/out:/out" \\
    -v d2m-e2e-pip-cache:/pip-cache -e PIP_CACHE_DIR=/pip-cache -e FILES="$FILES" \\
    -e DOC2MARK_GIT_SHA="$(git rev-parse HEAD)" d2m-e2e:0a3d689002a4 bash -euc '
      mkdir /work && tar -C /src --exclude=.git -cf - . | tar -C /work -xf - && cd /work
      pip install -q -e ".[ocr]"
      python eval/docs_audit/merged_cells.py --tools doc2mark \\
        --json /out/merged_cells.doc2mark.json $FILES'

Competitors: the same command without DOC2MARK_GIT_SHA and with
`-v d2m-docs-audit-pip:/pip-cache -e HF_HOME=/pip-cache/hf`; instead of installing
the checkout, run `apt-get update && apt-get install -y libgl1 libglib2.0-0` (the
image is slim; without libGL, opencv-python fails to import, so Docling fails on
PDFs and finds no OCR engine), then `pip install -q "markitdown[all]" docling
--extra-index-url https://download.pytorch.org/whl/cpu` (CPU-only torch), then
`--tools markitdown,docling --json /out/merged_cells.competitors.json`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

CELL_TAG = re.compile(r"<t[dh]\b[^>]*>", re.I)
SPAN_ATTR = re.compile(r"\b(colspan|rowspan)\s*=\s*[\"']?([^\"'\s>]*)", re.I)
TABLE_ROW = re.compile(r"<tr\b.*?</tr>", re.I | re.S)
COLUMNS = {"doc2mark": "doc2mark (default)", "markitdown": "markitdown",
           "docling:md": "Docling `to_markdown`", "docling:html": "Docling `to_html`"}
TOOL_COLUMNS = {"doc2mark": ["doc2mark"], "markitdown": ["markitdown"],
                "docling": ["docling:md", "docling:html"]}
VERSIONED = ["doc2mark", "markitdown", "docling", "docling-core", "docling-ibm-models",
             "docling-parse", "torch"]


def doc2mark_converter():
    from doc2mark import load
    return lambda path: {"doc2mark": load(path).content}


def markitdown_converter():
    from markitdown import MarkItDown
    converter = MarkItDown()
    return lambda path: {"markitdown": converter.convert(path).text_content}


def docling_converter():
    from docling.document_converter import DocumentConverter
    converter = DocumentConverter()

    def convert(path):
        doc = converter.convert(path).document
        return {"docling:md": doc.export_to_markdown(), "docling:html": doc.export_to_html()}
    return convert


CONVERTERS = {"doc2mark": doc2mark_converter, "markitdown": markitdown_converter,
              "docling": docling_converter}


def count_spans(text: str) -> dict:
    counts = {"colspan": 0, "rowspan": 0, "colspan_raw": 0, "rowspan_raw": 0,
              "tables": len(re.findall(r"<table\b", text, re.I))}
    for tag in CELL_TAG.findall(text):
        for name, value in SPAN_ATTR.findall(tag):
            counts[name.lower() + "_raw"] += 1
            if value.isdigit() and int(value) > 1:
                counts[name.lower()] += 1
    return counts


def matching_lines(text: str, pattern: str) -> list[str]:
    text = TABLE_ROW.sub(lambda m: "\n" + " ".join(m.group(0).split()) + "\n", text)
    return [line.strip() for line in text.splitlines() if re.search(pattern, line)]


def versions() -> dict:
    found = {}
    for dist in VERSIONED:
        try:
            found[dist] = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            pass
    if "doc2mark" in found:
        import doc2mark
        found["doc2mark.__version__"] = doc2mark.__version__
        repo = Path(doc2mark.__file__).resolve().parent.parent
        sha = None
        if (repo / ".git").exists():
            sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip() or None
        found["doc2mark_git_sha"] = sha or os.environ.get("DOC2MARK_GIT_SHA")
    return found


def measure(args) -> dict:
    run = {"date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "host": {"node": platform.node(), "platform": platform.platform(),
                    "python": platform.python_version()},
           "command": sys.argv, "skipped": {}, "results": [], "shown": []}
    for tool in args.tools.split(","):
        try:
            convert = CONVERTERS[tool]()
        except ImportError as e:
            run["skipped"][tool] = f"not importable: {e}"
            print(f"note: skipping {tool}: {e}", file=sys.stderr)
            continue
        for path in args.files:
            start, error = time.perf_counter(), None
            try:
                outputs = convert(path)
            except Exception as e:  # a failing conversion is a result, not a crash
                outputs, error = dict.fromkeys(TOOL_COLUMNS[tool], ""), f"{type(e).__name__}: {e}"
                print(f"note: {tool} failed on {path}: {error}", file=sys.stderr)
            seconds = round(time.perf_counter() - start, 2)
            for column, text in outputs.items():
                run["results"].append({"file": path, "tool": tool, "column": column,
                                       "seconds": seconds, "error": error, "chars": len(text),
                                       **count_spans(text)})
                if args.dump and not error:
                    ext = ".html" if column == "docling:html" else ".md"
                    out = Path(args.dump, column.replace(":", "_"), Path(path).name + ext)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(text)
                if args.show and not error:
                    run["shown"].append({"file": path, "column": column,
                                         "lines": matching_lines(text, args.show)})
    run["versions"] = versions()
    return run


def cell(result: dict | None) -> str:
    if result is None:
        return "n/a"
    if result["error"]:
        return "error: " + result["error"].split(":")[0]
    text = f"{result['colspan']} / {result['rowspan']}"
    if (result["colspan_raw"], result["rowspan_raw"]) != (result["colspan"], result["rowspan"]):
        text += f" [raw {result['colspan_raw']} / {result['rowspan_raw']}]"
    return text


def report(runs: list[dict]) -> None:
    results = {(r["file"], r["column"]): r for run in runs for r in run["results"]}
    columns = [c for c in COLUMNS if any(column == c for _, column in results)]
    files = list(dict.fromkeys(f for f, _ in results))
    print("| Document | " + " | ".join(COLUMNS[c] for c in columns) + " |")
    print("|---|" + ":---:|" * len(columns))
    for f in files:
        print(f"| `{Path(f).name}` | " + " | ".join(cell(results.get((f, c))) for c in columns) + " |")
    print("\n`C / R` = colspan / rowspan attributes with a value > 1.")
    for run in runs:
        print(f"\nrun {run['date']} on {run['host']['node']} ({run['host']['platform']}, "
              f"Python {run['host']['python']})")
        print("versions: " + ", ".join(f"{k} {v}" for k, v in run["versions"].items()))
        for tool, why in run["skipped"].items():
            print(f"skipped {tool}: {why}")
        for shown in run.get("shown", []):
            print(f"\n--- {shown['file']} [{shown['column']}] lines matching --show:")
            for line in shown["lines"]:
                print("   ", line)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="*", metavar="FILE")
    parser.add_argument("--tools", default="doc2mark,markitdown,docling",
                        help="comma-separated subset of: " + ", ".join(CONVERTERS))
    parser.add_argument("--json", help="write the results (and run metadata) to this file")
    parser.add_argument("--dump", metavar="DIR", help="write every output under DIR")
    parser.add_argument("--show", metavar="REGEX", help="print output lines matching REGEX")
    parser.add_argument("--merge", nargs="+", metavar="JSON",
                        help="report earlier --json files together instead of converting")
    args = parser.parse_args()
    unknown = set(args.tools.split(",")) - set(CONVERTERS)
    if unknown or not (args.merge or args.files):
        parser.error(f"unknown tools {sorted(unknown)}" if unknown else "no FILE given")
    if args.merge:
        runs = [run for path in args.merge for run in json.loads(Path(path).read_text())["runs"]]
    else:
        runs = [measure(args)]
    report(runs)
    if args.json:
        Path(args.json).write_text(json.dumps({"runs": runs}, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
