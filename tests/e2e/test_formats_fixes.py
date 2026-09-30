"""E2E: the format, CLI folder-run and loader-API fixes from the docs audit (PR #27, items 1-9, 22 and 24).

Every test drives what a user runs: the real ``doc2mark`` CLI or, where the CLI cannot express the call (the CSV
``delimiter=`` argument, ``batch_process``, the convenience functions, ``table_style`` validation), a small Python
program in a subprocess of its own that uses only the public API. Inputs are built at test time.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.e2e import builders_formats_fixes as b
from tests.e2e import builders_pdftable, pdfgen


def tree(root) -> list:
    """Every file under ``root`` as sorted POSIX paths relative to it."""
    return sorted(p.relative_to(root).as_posix() for p in Path(root).rglob("*") if p.is_file())


def read(path) -> str:
    return Path(path).read_bytes().decode("utf-8")


def table_rows(markdown: str) -> list:
    """The cells of every row of the pipe table in ``markdown`` (the separator row is left out)."""
    return [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in markdown.splitlines() if line.startswith("|") and line.strip("|- ")]


def run_api(e2e_dir, script, *args, timeout=300):
    """Run ``script`` the way a user's program would (``python -c``, public API only) and return the JSON it prints
    as its last line on stdout."""
    proc = subprocess.run([sys.executable, "-c", script, *map(str, args)], cwd=e2e_dir, capture_output=True,
                          stdin=subprocess.DEVNULL, timeout=timeout, check=False, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert proc.returncode == 0, f"the program exited with {proc.returncode}:\n{proc.stderr[-3000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --- item 1: .tsv always failed --------------------------------------------------------------------------------

def test_a_tsv_file_converts_to_a_table(run_cli, e2e_dir):
    """Item 1: every ``.tsv`` failed with "got multiple values for argument 'delimiter'". Commas and semicolons in
    the cells show that the tab, not a sniffed character, separates them."""
    tsv = b.write(e2e_dir / "prices.tsv", "item\tprice, USD\tnote\nwidget\t1,50\tsmall; red\ngadget\t12\t\n")

    result = run_cli(tsv, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert table_rows(result.markdown) == [["item", "price, USD", "note"], ["widget", "1,50", "small; red"],
                                           ["gadget", "12", ""]], result.describe()
    meta = result.json["metadata"]
    assert (meta["format"], meta["delimiter"], meta["row_count"], meta["column_count"]) == ("tsv", "\t", 3, 3)


# --- item 2: the CSV delimiter argument was ignored -------------------------------------------------------------

LOAD_TABLE = (
    "import json, sys\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "loader = UnifiedDocumentLoader(ocr_provider=None)\n"
    "delimiter = sys.argv[2] if len(sys.argv) > 2 else None\n"
    "try:\n"
    "    doc = loader.load(sys.argv[1], delimiter=delimiter)\n"
    "except Exception as exc:\n"
    "    print(json.dumps({'error': str(exc)}))\n"
    "else:\n"
    "    print(json.dumps({'content': doc.content, 'delimiter': doc.metadata.delimiter,\n"
    "                      'columns': doc.metadata.column_count}))\n"
)


def test_an_explicit_csv_delimiter_is_honoured_and_sniffing_stays_the_default(e2e_dir):
    """Item 2: ``load("semi.csv", delimiter=",")`` still reported ";": the delimiter was always sniffed."""
    semi = b.write(e2e_dir / "semi.csv", "a;b\n1;2\n")

    as_comma = run_api(e2e_dir, LOAD_TABLE, semi, ",")
    as_semicolon = run_api(e2e_dir, LOAD_TABLE, semi, ";")
    sniffed = run_api(e2e_dir, LOAD_TABLE, semi)

    assert (as_comma["delimiter"], as_comma["columns"]) == (",", 1), as_comma
    assert table_rows(as_comma["content"]) == [["a;b"], ["1;2"]], as_comma
    assert (as_semicolon["delimiter"], as_semicolon["columns"]) == (";", 2), as_semicolon
    assert (sniffed["delimiter"], sniffed["columns"]) == (";", 2), sniffed


def test_a_csv_delimiter_of_several_characters_is_rejected_clearly(e2e_dir):
    """Item 2: an explicit delimiter the ``csv`` module cannot use is an error that says so."""
    semi = b.write(e2e_dir / "semi.csv", "a;b\n1;2\n")

    result = run_api(e2e_dir, LOAD_TABLE, semi, ";;")

    assert "single character" in result.get("error", ""), result


def test_the_delimiter_argument_does_not_change_how_a_tsv_file_is_read(e2e_dir):
    """Item 1/2: a TSV is tab separated, whatever ``delimiter=`` a batch of mixed files was given."""
    tsv = b.write(e2e_dir / "t.tsv", "a\tb\n1\t2\n")

    result = run_api(e2e_dir, LOAD_TABLE, tsv, ",")

    assert (result.get("delimiter"), result.get("columns")) == ("\t", 2), result


# --- items 3-5: CLI folder runs ----------------------------------------------------------------------------------

def test_a_folder_run_converts_files_only_and_recursive_controls_descent(run_cli, e2e_dir):
    """Item 3: the default ``--pattern "*"`` also yielded sub-folders, which failed ("Cannot detect format for
    extension:") and stopped the run."""
    docs = e2e_dir / "docs"
    b.write(docs / "a.txt", "Alpha file text")
    b.write(docs / "sub" / "b.txt", "Beta file text")

    top = run_cli(docs, "-o", e2e_dir / "top", "-q", raw=True)
    deep = run_cli(docs, "-r", "-o", e2e_dir / "deep", "-q", raw=True)

    assert top.exit_code == 0, top.describe()
    assert tree(e2e_dir / "top") == ["a.md"], top.describe()
    assert deep.exit_code == 0, deep.describe()
    assert tree(e2e_dir / "deep") == ["a.md", "sub/b.md"], deep.describe()


def test_a_folder_run_mirrors_the_input_tree_under_the_output_folder(run_cli, e2e_dir):
    """Item 4: the output was flat by file stem, so ``2024/report.md`` and ``report.txt`` both wrote ``report.md``."""
    docs = e2e_dir / "docs"
    b.write(docs / "report.txt", "Plain report from the root folder")
    b.write(docs / "2024" / "report.md", "# Yearly report\n\nNumbers for 2024\n")
    b.write(docs / "2024" / "q1" / "report.csv", "k,v\nq1,10\n")
    out = e2e_dir / "converted"

    result = run_cli(docs, "-r", "--format", "both", "-o", out, "-q", raw=True)

    assert result.exit_code == 0, result.describe()
    assert tree(out) == ["2024/q1/report.json", "2024/q1/report.md", "2024/report.json", "2024/report.md",
                         "report.json", "report.md"], result.describe()
    assert "Plain report from the root folder" in read(out / "report.md")
    assert "Numbers for 2024" in read(out / "2024" / "report.md")
    assert table_rows(read(out / "2024" / "q1" / "report.md")) == [["k", "v"], ["q1", "10"]]
    assert json.loads(read(out / "2024" / "report.json"))["metadata"]["filename"] == "report.md"


@pytest.mark.parametrize("workers", [[], ["-p", "2"]], ids=["sequential", "parallel"])
def test_inputs_that_would_write_the_same_output_all_survive_under_distinct_names(run_cli, e2e_dir, workers):
    """Item 4: ``report.txt`` and ``report.md`` (and ``report.csv``) in one folder all map to ``report.md``. Each
    keeps its own output, named after the whole input file name, and the run says so."""
    docs = e2e_dir / "docs"
    b.write(docs / "report.txt", "Text version of the report")
    b.write(docs / "report.md", "# Markdown version\n\nof the report\n")
    b.write(docs / "report.csv", "k,v\na,1\n")
    out = e2e_dir / "converted"

    result = run_cli(docs, "-o", out, *workers, raw=True)

    assert result.exit_code == 0, result.describe()
    assert tree(out) == ["report.csv.md", "report.md.md", "report.txt.md"], result.describe()
    assert "Text version of the report" in read(out / "report.txt.md")
    assert "Markdown version" in read(out / "report.md.md")
    assert table_rows(read(out / "report.csv.md")) == [["k", "v"], ["a", "1"]]
    assert "report.txt.md" in result.stderr, result.describe()


def test_converting_a_folder_onto_itself_never_overwrites_a_source_file(run_cli, e2e_dir):
    """Item 4: with ``-o`` naming the input folder, the output of ``note.txt`` is ``note.md``: a source file."""
    docs = e2e_dir / "docs"
    source = "---\ntitle: Kept\n---\n# Source note\n\nThis source file must survive.\n"
    b.write(docs / "note.md", source)
    b.write(docs / "note.txt", "Text twin of the note")

    result = run_cli(docs, "-o", docs, "-q", raw=True)

    assert result.exit_code == 0, result.describe()
    assert read(docs / "note.md") == source
    assert read(docs / "note.txt") == "Text twin of the note"
    assert sorted(set(tree(docs)) - {"note.md", "note.txt"}) == ["note.md.md", "note.txt.md"], result.describe()


@pytest.mark.parametrize("workers", [[], ["-p", "2"]], ids=["sequential", "parallel"])
def test_timeout_stops_a_file_that_takes_too_long_and_the_run_goes_on(run_cli, e2e_dir, workers):
    """Item 5: ``--timeout`` was only passed to ``future.result()`` after the file had finished, so nothing ever
    timed out. Here the OCR provider never answers: ``scan.pdf`` is stopped after 3 s, ``a-notes.txt`` is done."""
    docs = e2e_dir / "docs"
    b.write(docs / "a-notes.txt", "Notes that convert at once")
    pdfgen.image_pdf(docs / "scan.pdf", "NEVER READ 4721")
    out = e2e_dir / "converted"

    with b.HangingOpenAI() as hanging:
        started = time.monotonic()
        result = run_cli(docs, "--ocr", "openai", "--ocr-images", "--timeout", "3", "--retry", "0", "--skip-errors",
                         "-o", out, *workers, env=hanging.env, timeout=60, raw=True)
        elapsed = time.monotonic() - started

    assert result.exit_code == 0, result.describe()
    assert "scan.pdf" in result.stderr and "timed out after 3 s" in result.stderr, result.describe()
    assert tree(out) == ["a-notes.md"], result.describe()
    assert elapsed < 40, f"the file was stopped after {elapsed:.0f} s, not after its 3 s timeout"


def test_a_file_that_times_out_stops_the_run_unless_errors_are_skipped(run_cli, e2e_dir):
    """Item 5: a timeout is a failed file: without ``--skip-errors`` the run stops with exit code 1 and says why."""
    docs = e2e_dir / "docs"
    docs.mkdir()
    pdfgen.image_pdf(docs / "scan.pdf", "NEVER READ 4721")

    with b.HangingOpenAI() as hanging:
        result = run_cli(docs, "--ocr", "openai", "--ocr-images", "--timeout", "3", "--retry", "0",
                         "-o", e2e_dir / "converted", env=hanging.env, timeout=60, raw=True)

    assert result.exit_code == 1, result.describe()
    assert "Failed to process" in result.stderr and "scan.pdf" in result.stderr, result.describe()
    assert "timed out after 3 s" in result.stderr, result.describe()


@pytest.mark.parametrize("limit", ["30", "0"])
def test_files_that_finish_in_time_are_unaffected_by_timeout_and_zero_means_no_limit(run_cli, e2e_dir, limit):
    """Item 5: a generous ``--timeout`` changes nothing, and ``--timeout 0`` switches the limit off."""
    docs = e2e_dir / "docs"
    b.write(docs / "a.txt", "Alpha file text")
    b.write(docs / "b.txt", "Beta file text")

    result = run_cli(docs, "--timeout", limit, "-o", e2e_dir / "converted", "-q", raw=True)

    assert result.exit_code == 0, result.describe()
    assert tree(e2e_dir / "converted") == ["a.md", "b.md"], result.describe()


def test_preserve_structure_is_accepted_with_a_deprecation_warning(run_cli, e2e_dir):
    """Item 5: ``--preserve-structure`` was parsed and never read. A folder run now always mirrors the input tree,
    so the flag has nothing left to do: it still parses, and says it is deprecated."""
    docs = e2e_dir / "docs"
    b.write(docs / "a.txt", "Alpha file text")
    b.write(docs / "sub" / "b.txt", "Beta file text")

    flagged = run_cli(docs, "-r", "--preserve-structure", "-o", e2e_dir / "flagged", raw=True)
    plain = run_cli(docs, "-r", "-o", e2e_dir / "plain", raw=True)

    assert flagged.exit_code == 0 and plain.exit_code == 0, flagged.describe() + plain.describe()
    assert tree(e2e_dir / "flagged") == tree(e2e_dir / "plain") == ["a.md", "sub/b.md"]
    assert "--preserve-structure" in flagged.stderr and "deprecated" in flagged.stderr, flagged.describe()
    assert "deprecated" not in plain.stderr, plain.describe()


# --- item 6: the --help text --------------------------------------------------------------------------------------

LIST_FORMATS = "import json\nfrom doc2mark import DocumentFormat\nprint(json.dumps([f.value for f in DocumentFormat]))\n"


def test_the_help_text_says_what_the_options_do(run_cli, e2e_dir):
    """Item 6: the examples promised "Process 10 largest files" for ``--sort size`` (it keeps the smallest) and
    "OpenAI GPT-4V OCR" (the default model is gpt-5.4-mini)."""
    help_text = run_cli(e2e_dir, "--help", raw=True).stdout

    assert "GPT-4V" not in help_text
    assert "gpt-5.4-mini" in help_text
    sort_line = next(line for line in help_text.splitlines() if "--max-files 10 --sort size" in line)
    assert "smallest" in sort_line and "largest" not in sort_line, sort_line


def test_max_files_with_sort_size_keeps_the_smallest_files(run_cli, e2e_dir):
    """Item 6: what the help example now says is what the option does."""
    docs = e2e_dir / "docs"
    for name, size in (("big.txt", 5000), ("medium.txt", 300), ("small.txt", 10)):
        b.write(docs / name, "x" * size)

    result = run_cli(docs, "--max-files", "2", "--sort", "size", "-o", e2e_dir / "converted", "-q", raw=True)

    assert result.exit_code == 0, result.describe()
    assert tree(e2e_dir / "converted") == ["medium.md", "small.md"], result.describe()


def test_the_help_lists_every_supported_format(run_cli, e2e_dir):
    """Item 6: the "Supported formats" list left out the images and EML."""
    help_text = run_cli(e2e_dir, "--help", raw=True).stdout
    listed = help_text[help_text.index("Supported formats:"):]

    missing = [ext for ext in run_api(e2e_dir, LIST_FORMATS) if not re.search(rf"\b{ext}\b", listed, re.IGNORECASE)]

    assert not missing, f"formats the loader accepts but the help does not list: {missing}\n{listed}"


# --- item 7: batch_process skipped upper-case extensions and .htm ---------------------------------------------------

BATCH_PROCESS = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "root = Path(sys.argv[1])\n"
    "loader = UnifiedDocumentLoader(ocr_provider=None)\n"
    "results = loader.batch_process(root, save_files=False, show_progress=False, recursive=sys.argv[2] == 'recursive')\n"
    "print(json.dumps({Path(key).relative_to(root).as_posix(): info['status'] for key, info in results.items()}))\n"
)


def test_batch_process_finds_every_file_load_accepts(e2e_dir):
    """Item 7: ``batch_process`` globbed lower-case extensions only, and not ``.htm``, although ``load()`` takes
    ``upper.PDF`` and ``page.htm``. Unsupported files and a folder named like a PDF are not results."""
    docs = e2e_dir / "docs"
    docs.mkdir()
    pdfgen.text_pdf(docs / "upper.PDF", "Upper case extension")
    b.write(docs / "page.htm", "<h1>Hello htm</h1>")
    b.write(docs / "Notes.TXT", "Upper case text")
    b.write(docs / "plain.txt", "Plain text")
    b.write(docs / "Readme.MD", "# Readme\n")
    b.write(docs / "guide.Markdown", "# Guide\n")
    b.write(docs / "ignored.xyz", "not a document")
    b.write(docs / "folder.pdf" / "inner.txt", "Inside a folder named like a PDF")
    b.write(docs / "deep" / "Data.CSV", "a,b\n1,2\n")
    top = {"Notes.TXT", "Readme.MD", "guide.Markdown", "page.htm", "plain.txt", "upper.PDF"}

    recursive = run_api(e2e_dir, BATCH_PROCESS, docs, "recursive")
    flat = run_api(e2e_dir, BATCH_PROCESS, docs, "flat")

    assert recursive == {name: "success" for name in top | {"deep/Data.CSV", "folder.pdf/inner.txt"}}, recursive
    assert flat == {name: "success" for name in top}, flat


BATCH_SAVE = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "root, out = Path(sys.argv[1]), Path(sys.argv[2])\n"
    "results = UnifiedDocumentLoader(ocr_provider=None).batch_process(root, output_dir=out, show_progress=False)\n"
    "print(json.dumps({Path(key).name: [Path(f).relative_to(out).as_posix() for f in info['output_files']]\n"
    "                  for key, info in results.items()}))\n"
)


def test_batch_process_output_names_keep_the_dots_of_the_input_name(e2e_dir):
    """Item 4 (Python API): ``batch_process`` named its output with ``with_suffix`` on the stem, so ``v1.2.txt`` and
    ``v1.3.txt`` both wrote ``v1.md`` and one conversion was lost."""
    docs = e2e_dir / "docs"
    b.write(docs / "v1.2.txt", "Release 1.2 notes")
    b.write(docs / "v1.3.txt", "Release 1.3 notes")
    b.write(docs / "sub" / "plan.v2.final.txt", "Plan, second version")

    written = run_api(e2e_dir, BATCH_SAVE, docs, e2e_dir / "converted")

    assert written == {"v1.2.txt": ["v1.2.md"], "v1.3.txt": ["v1.3.md"],
                       "plan.v2.final.txt": ["sub/plan.v2.final.md"]}, written
    assert read(e2e_dir / "converted" / "v1.2.md").strip() == "Release 1.2 notes"
    assert read(e2e_dir / "converted" / "v1.3.md").strip() == "Release 1.3 notes"


# --- item 8: tables and sections were never filled -----------------------------------------------------------------

BATCH_TABLES = (
    "import json, sys\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "loader = UnifiedDocumentLoader(ocr_provider=None)\n"
    "batch = loader.batch_process_files(sys.argv[1:], save_files=False, show_progress=False)\n"
    "print(json.dumps([[info['status'], info['metadata']['tables_found']] for info in batch.values()]))\n"
)


def test_batch_results_count_the_tables_of_each_document(e2e_dir):
    """Item 8: ``tables_found`` was 0 for every document because nothing filled ``ProcessedDocument.tables``."""
    docx = b.report_docx(e2e_dir / "report.docx")
    table_pdf = builders_pdftable.table_pdf(e2e_dir / "table.pdf", [["Item", "Qty"], ["Bolt", "4"], ["Nut", "9"]])
    prose_pdf = pdfgen.text_pdf(e2e_dir / "prose.pdf", "Only prose here, no table at all.")
    notes = b.write(e2e_dir / "notes.txt", "Plain notes")

    counts = run_api(e2e_dir, BATCH_TABLES, docx, table_pdf, prose_pdf, notes)

    assert counts == [["success", 1], ["success", 1], ["success", 0], ["success", 0]], counts


def test_json_output_lists_the_tables_and_sections_of_a_document(run_cli, e2e_dir):
    """Item 8: ``--format json`` wrote ``"tables": null`` and ``"sections": null``. Tables come from the table items
    of the content, sections from its headings (the same levels as the ``#`` lines of the Markdown)."""
    result = run_cli(b.report_docx(e2e_dir / "report.docx"), fmt="json")

    assert result.exit_code == 0, result.describe()
    document = result.json
    assert document["tables"], result.describe()
    assert len(document["tables"]) == 1 and "Quarters" in document["tables"][0]["content"], document["tables"]
    assert document["tables"][0]["page"] == 1
    headings = [(len(m.group(1)), m.group(2)) for m in re.finditer(r"^(#+) (.+)$", document["content"], re.MULTILINE)]
    assert headings == [(1, "Quarterly report"), (2, "Details")], document["content"]
    assert [(s["level"], s["title"]) for s in document["sections"]] == headings, document["sections"]


# --- item 9: convenience functions and loader options ---------------------------------------------------------------

CONVENIENCE = r'''
import json, sys
from pathlib import Path
import doc2mark

docx, workdir = Path(sys.argv[1]), Path(sys.argv[2])
cache = workdir / "cache"
batch_in = workdir / "batch-in"
batch_in.mkdir()
(batch_in / docx.name).write_bytes(docx.read_bytes())
(batch_in / "notes.txt").write_text("Plain notes")
out = {}


def attempt(name, call):
    try:
        out[name] = call()
    except Exception as exc:  # recorded, so the test names the call that failed
        out[name] = f"{type(exc).__name__}: {exc}"


attempt("load_table_style", lambda: doc2mark.load(docx, ocr_provider=None, table_style="markdown_grid").content)
attempt("load_default_provider", lambda: doc2mark.load(docx, table_style="markdown_grid").content)
attempt("load_cache_dir", lambda: (doc2mark.load(docx, ocr_provider=None, cache_dir=str(cache)).content,
                                   sorted(p.name[-5:] for p in cache.glob("*.json"))))
attempt("document_to_markdown", lambda: doc2mark.document_to_markdown(docx, ocr_provider=None,
                                                                      table_style="markdown_grid"))
attempt("batch_convert", lambda: doc2mark.batch_convert_to_markdown(
    batch_in, workdir / "converted", ocr_provider=None, table_style="markdown_grid", show_progress=False) and
    (workdir / "converted" / "report.md").read_text())
attempt("batch_documents", lambda: sorted(info["status"] for info in doc2mark.batch_process_documents(
    batch_in, ocr_provider=None, table_style="markdown_grid", save_files=False, show_progress=False,
    max_workers=2).values()))
attempt("batch_files", lambda: [info["status"] for info in doc2mark.batch_process_files(
    [docx], ocr_provider=None, table_style="markdown_grid", save_files=False, show_progress=False).values()])
attempt("load_encoding", lambda: doc2mark.load(batch_in / "notes.txt", ocr_provider=None, encoding="utf-8").content)
attempt("load_unknown", lambda: doc2mark.load(docx, ocr_provider=None, no_such_option=1))
attempt("batch_unknown", lambda: doc2mark.batch_process_documents(batch_in, ocr_provider=None, no_such_option=1))
print(json.dumps(out))
'''


def test_convenience_functions_route_loader_options_to_the_loader(e2e_dir):
    """Item 9: ``load(path, table_style=...)`` and ``cache_dir=`` raised ``TypeError`` ("unexpected keyword
    argument") because every extra keyword went to ``loader.load()``, although the docstrings call them
    "Additional options". Loader settings reach the loader, load and batch options still reach their call, and an
    unknown name is a ``TypeError`` that names it."""
    docx = b.report_docx(e2e_dir / "report.docx")
    work = e2e_dir / "work"
    work.mkdir()

    out = run_api(e2e_dir, CONVENIENCE, docx, work)

    for name in ("load_table_style", "load_default_provider", "document_to_markdown", "batch_convert"):
        assert "<table" not in out[name] and "Merged" in out[name], f"{name}: {out[name]}"
    content, cache_files = out["load_cache_dir"]
    assert "Quarters" in content and cache_files == [".json"], out["load_cache_dir"]
    assert out["batch_documents"] == ["success", "success"] and out["batch_files"] == ["success"], out
    assert out["load_encoding"].strip() == "Plain notes", out
    for name in ("load_unknown", "batch_unknown"):
        assert out[name].startswith("TypeError") and "no_such_option" in out[name], f"{name}: {out[name]}"


# --- item 22: table_style validation and legacy files -----------------------------------------------------------------

TABLE_STYLES = r'''
import json, sys
from doc2mark import TableStyle, UnifiedDocumentLoader, load

docx = sys.argv[1]
out = {}


def content(style):
    return UnifiedDocumentLoader(ocr_provider=None, table_style=style).load(docx).content


def attempt(name, call):
    try:
        out[name] = call()
    except Exception as exc:
        out[name] = f"{type(exc).__name__}: {exc}"


grid = content("markdown_grid")
attempt("upper", lambda: content("MARKDOWN_GRID") == grid)
attempt("mixed", lambda: content("Markdown_Grid") == grid)
attempt("enum", lambda: content(TableStyle.MARKDOWN_GRID) == grid)
attempt("unknown_loader", lambda: content("grid"))
attempt("unknown_dashed", lambda: content("markdown-grid"))
attempt("unknown_convenience", lambda: load(docx, ocr_provider=None, table_style="grid").content)
print(json.dumps(out))
'''


def test_table_style_is_validated_once_at_the_loader(e2e_dir):
    """Item 22: a misspelt ``table_style`` made PDF conversion fail and sent Word files to the basic converter
    without a word. Valid names are accepted in any case; an unknown one is a ``ValueError`` that lists the valid
    ones."""
    out = run_api(e2e_dir, TABLE_STYLES, b.report_docx(e2e_dir / "report.docx"))

    assert out["upper"] is True and out["mixed"] is True and out["enum"] is True, out
    for name in ("unknown_loader", "unknown_dashed", "unknown_convenience"):
        assert out[name].startswith("ValueError"), f"{name}: {out[name]}"
        assert all(valid in out[name] for valid in ("minimal_html", "markdown_grid", "styled_html")), out[name]


def test_a_legacy_file_honours_the_table_style(run_cli, require_tool, e2e_dir):
    """Item 22: the converted copy of a ``.doc`` was read with the default table style, whatever ``--table-style``
    said: merged cells came out as HTML."""
    require_tool("soffice")
    legacy = b.to_legacy(b.report_docx(e2e_dir / "report.docx"), e2e_dir / "legacy", "doc")

    default = run_cli(legacy)
    grid = run_cli(legacy, "--table-style", "markdown_grid")

    assert default.exit_code == 0 and grid.exit_code == 0, default.describe() + grid.describe()
    assert "<table" in default.markdown and "colspan" in default.markdown, default.describe()
    assert "<table" not in grid.markdown and "Merged" in grid.markdown, grid.describe()
    assert "Quarters" in grid.markdown, grid.describe()


# --- item 24: Markdown front matter -------------------------------------------------------------------------------------

NOT_FRONT_MATTER = {
    "prose between two rules": "---\nIntro paragraph\n---\n# Title\n\nBody\n",
    "list between two rules": "---\n- first\n- second\n---\nText after\n",
    "no closing rule": "---\ntitle: Draft\n\n# Title\n\nBody\n",
    "rule followed by text": "--- not a fence\nBody text\n---\nMore text\n",
    "empty block": "---\n---\n# Title\n",
    "invalid yaml": "---\ntitle: [unclosed\n---\n# Title\n",
}


@pytest.mark.parametrize("text", NOT_FRONT_MATTER.values(), ids=NOT_FRONT_MATTER.keys())
def test_a_markdown_file_that_only_starts_with_a_rule_is_kept_as_written(run_cli, e2e_dir, text):
    """Item 24: a file starting with a ``---`` rule lost its first block to ``metadata.frontmatter`` (a string).
    Only a block of YAML between two ``---`` lines that parses to a mapping is front matter."""
    result = run_cli(b.write(e2e_dir / "rule.md", text), fmt="both")

    assert result.exit_code == 0, result.describe()
    assert result.markdown == text, result.describe()
    assert result.json["metadata"]["frontmatter"] is None, result.describe()


def test_valid_front_matter_moves_to_metadata(run_cli, e2e_dir):
    """Item 24: what is front matter keeps working: the mapping goes to ``metadata.frontmatter``, the body stays."""
    text = "---\ntitle: Release notes\ntags: [alpha, beta]\n---\n# Heading\n\nBody text\n\n---\n\nAfter a rule\n"

    result = run_cli(b.write(e2e_dir / "post.md", text), fmt="both")

    assert result.exit_code == 0, result.describe()
    assert result.json["metadata"]["frontmatter"] == {"title": "Release notes", "tags": ["alpha", "beta"]}
    assert result.markdown == "# Heading\n\nBody text\n\n---\n\nAfter a rule\n", result.describe()


def test_the_text_after_front_matter_is_kept_as_written(run_cli, e2e_dir):
    """Item 24: only the front matter block is taken out; an indented first line of the body keeps its indent."""
    text = "---\ntitle: Code first\n---\n\n    indented code line\n\nAfter the code\n"

    result = run_cli(b.write(e2e_dir / "code.md", text), fmt="both")

    assert result.exit_code == 0, result.describe()
    assert result.markdown == "    indented code line\n\nAfter the code\n", result.describe()


def test_front_matter_with_a_date_writes_json(run_cli, e2e_dir):
    """Item 24: YAML reads ``date: 2024-05-01`` as a ``datetime.date``, which ``--format json`` could not write
    ("Object of type date is not JSON serializable"), so every Markdown file with a dated front matter failed."""
    text = "---\ntitle: Dated post\ndate: 2024-05-01\nupdated: 2024-05-02 10:30:00\n---\n# Heading\n"

    result = run_cli(b.write(e2e_dir / "dated.md", text), fmt="both")

    assert result.exit_code == 0, result.describe()
    frontmatter = result.json["metadata"]["frontmatter"]
    assert frontmatter["title"] == "Dated post" and frontmatter["date"] == "2024-05-01", frontmatter
    assert frontmatter["updated"].startswith("2024-05-02"), frontmatter
