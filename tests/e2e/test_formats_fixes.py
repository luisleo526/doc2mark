"""E2E: the format, CLI folder-run and loader-API fixes from the docs audit (PR #27, items 1-9, 22 and 24).

Every test drives what a user runs: the real ``doc2mark`` CLI or, where the CLI cannot express the call (the CSV
``delimiter=`` argument, ``batch_process``, the convenience functions, ``table_style`` validation), a small Python
program in a subprocess of its own that uses only the public API. Inputs are built at test time.
"""

import json
import os
import re
import signal
import stat
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


def test_a_tsv_file_keeps_its_quote_characters_as_text(run_cli, e2e_dir):
    """Review: a TSV has no quoting, so ``5" pipe`` and ``"Best" seller`` are text, not CSV quoting. Read with CSV
    rules the quotes were silently dropped and an unclosed one swallowed the rows after it into one cell."""
    tsv = b.write(e2e_dir / "parts.tsv", 'id\tname\n1\t"Best" seller, 12" pipe\n2\t"Unclosed quote\n3\tlast row\n')

    result = run_cli(tsv, fmt="both")

    assert result.exit_code == 0, result.describe()
    assert table_rows(result.markdown) == [["id", "name"], ["1", '"Best" seller, 12" pipe'],
                                           ["2", '"Unclosed quote'], ["3", "last row"]], result.describe()
    assert result.json["metadata"]["row_count"] == 4


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


def test_a_folder_run_refuses_to_write_into_its_own_input_folder(run_cli, e2e_dir):
    """Item 4: with ``-o`` naming the input folder the output of ``note.txt`` is ``note.md``, a source file, and the
    next run reads the outputs of this one as inputs. The run stops before it writes anything."""
    docs = e2e_dir / "docs"
    source = "---\ntitle: Kept\n---\n# Source note\n\nThis source file must survive.\n"
    b.write(docs / "note.md", source)
    b.write(docs / "note.txt", "Text twin of the note")

    same = run_cli(docs, "-o", docs, "-q", raw=True)
    spelled_differently = run_cli(docs, "-o", docs / ".." / "docs", "-q", raw=True)

    for result in (same, spelled_differently):
        assert result.exit_code == 2 and "input folder" in result.stderr, result.describe()
    assert tree(docs) == ["note.md", "note.txt"]
    assert read(docs / "note.md") == source


def test_rerunning_a_folder_run_with_the_output_inside_the_input_changes_nothing(run_cli, e2e_dir):
    """Item 4: an output folder inside the input folder is not read as input on the next run, so a run can be
    repeated (before, the outputs of run 1 were converted again, each run adding copies of the text)."""
    docs = e2e_dir / "docs"
    b.write(docs / "a.txt", "Alpha file text")
    b.write(docs / "sub" / "b.txt", "Beta file text")
    out = docs / "md"

    first = run_cli(docs, "-r", "-o", out, raw=True)
    after_first = tree(docs)
    later = [run_cli(docs, "-r", "-o", out, raw=True) for _ in range(2)]

    assert first.exit_code == 0 and all(result.exit_code == 0 for result in later), first.describe()
    assert after_first == ["a.txt", "md/a.md", "md/sub/b.md", "sub/b.txt"], first.describe()
    assert tree(docs) == after_first
    assert read(out / "a.md").strip() == "Alpha file text"
    assert "warn" not in (first.stderr + "".join(result.stderr for result in later)).lower()


def test_a_folder_run_can_write_into_a_folder_above_its_input(run_cli, e2e_dir):
    """The output folder is only left out of the input files when it is inside the input folder: one above it (here
    the parent, which holds the input folder itself) is a normal output folder."""
    docs = e2e_dir / "work" / "docs"
    b.write(docs / "a.txt", "Alpha file text")
    b.write(docs / "sub" / "b.txt", "Beta file text")

    result = run_cli(docs, "-r", "-o", e2e_dir / "work", "-q", raw=True)

    assert result.exit_code == 0, result.describe()
    assert tree(e2e_dir / "work") == ["a.md", "docs/a.txt", "docs/sub/b.txt", "sub/b.md"], result.describe()


@pytest.mark.parametrize("workers", [[], ["-p", "2"]], ids=["sequential", "parallel"])
def test_timeout_stops_a_file_that_takes_too_long_and_the_run_goes_on(run_cli, e2e_dir, workers):
    """Item 5: ``--timeout`` was only passed to ``future.result()`` after the file had finished, so nothing ever
    timed out. Here the OCR provider never answers: ``scan.pdf`` is stopped after 5 s, ``a-notes.txt`` is done."""
    docs = e2e_dir / "docs"
    b.write(docs / "a-notes.txt", "Notes that convert at once")
    pdfgen.image_pdf(docs / "scan.pdf", "NEVER READ 4721")
    out = e2e_dir / "converted"

    with b.HangingOpenAI() as hanging:
        started = time.monotonic()
        result = run_cli(docs, "--ocr", "openai", "--ocr-images", "--timeout", "5", "--retry", "0", "--skip-errors",
                         "-o", out, *workers, env=hanging.env, timeout=60, raw=True)
        elapsed = time.monotonic() - started

    assert result.exit_code == 0, result.describe()
    assert "scan.pdf" in result.stderr and "timed out after 5 s" in result.stderr, result.describe()
    assert tree(out) == ["a-notes.md"], result.describe()
    assert elapsed < 40, f"the file was stopped after {elapsed:.0f} s, not after its 5 s timeout"


def test_a_file_that_times_out_stops_the_run_unless_errors_are_skipped(run_cli, e2e_dir):
    """Item 5: a timeout is a failed file: without ``--skip-errors`` the run stops with exit code 1 and says why."""
    docs = e2e_dir / "docs"
    docs.mkdir()
    pdfgen.image_pdf(docs / "scan.pdf", "NEVER READ 4721")

    with b.HangingOpenAI() as hanging:
        result = run_cli(docs, "--ocr", "openai", "--ocr-images", "--timeout", "5", "--retry", "0",
                         "-o", e2e_dir / "converted", env=hanging.env, timeout=60, raw=True)

    assert result.exit_code == 1, result.describe()
    assert "Failed to process" in result.stderr and "scan.pdf" in result.stderr, result.describe()
    assert "timed out after 5 s" in result.stderr, result.describe()


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


def test_a_negative_retry_count_is_a_usage_error(run_cli, e2e_dir):
    """Review of the folder worker: ``--retry -1`` made every file's outcome ``None`` and crashed the run."""
    docs = e2e_dir / "docs"
    b.write(docs / "a.txt", "Alpha file text")

    result = run_cli(docs, "--retry", "-1", "-o", e2e_dir / "converted", raw=True)

    assert result.exit_code == 2 and "--retry" in result.stderr, result.describe()


def test_a_document_that_cannot_be_written_is_a_failed_file_and_leaves_no_partial_output(run_cli, e2e_dir):
    """Review: ``--encoding ascii`` on a document with an accented letter ended the whole run, after leaving an empty
    ``.md`` behind. Writing is part of a file's conversion: a failure is reported (and skipped with
    ``--skip-errors``), and a file is only ever complete."""
    docs = e2e_dir / "docs"
    b.write(docs / "a.txt", "Plain ascii text")
    b.write(docs / "b.txt", "Caf\u00e9 with an accent")
    b.write(docs / "c.txt", "More plain text")
    out = e2e_dir / "converted"

    skipped = run_cli(docs, "--encoding", "ascii", "--skip-errors", "-o", out, raw=True)
    stopped = run_cli(docs, "--encoding", "ascii", "-o", e2e_dir / "stopped", raw=True)

    assert skipped.exit_code == 0 and "b.txt" in skipped.stderr, skipped.describe()
    assert tree(out) == ["a.md", "c.md"], skipped.describe()
    assert stopped.exit_code == 1 and "b.txt" in stopped.stderr, stopped.describe()
    assert all((e2e_dir / "stopped" / name).stat().st_size > 0 for name in tree(e2e_dir / "stopped"))


def test_a_file_whose_name_is_only_a_suffix_converts_like_any_other(run_cli, e2e_dir):
    """Review: ``..txt`` has the stem ``.``; building its output name raised and ended the run."""
    docs = e2e_dir / "docs"
    b.write(docs / "..txt", "Text of a file named ..txt")
    b.write(docs / "a.txt", "Alpha file text")

    result = run_cli(docs, "-o", e2e_dir / "converted", "-q", raw=True)

    assert result.exit_code == 0, result.describe()
    written = tree(e2e_dir / "converted")
    assert len(written) == 2 and "a.md" in written, result.describe()
    assert any("Text of a file named ..txt" in read(e2e_dir / "converted" / name) for name in written)


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


@pytest.mark.parametrize("date", ["2024-02-30", "2024-13-01", "2024-01-01 25:00:00"])
def test_front_matter_with_an_impossible_date_is_kept_as_written(run_cli, e2e_dir, date):
    """Review: PyYAML raises ``ValueError`` for a timestamp that is not a date. That ended the conversion of the whole
    file ("day is out of range for month"), which before this change was converted verbatim."""
    text = f"---\ntitle: Notes\ndate: {date}\n---\n# Heading\n\nBody text\n"

    result = run_cli(b.write(e2e_dir / "notes.md", text), fmt="both")

    assert result.exit_code == 0, result.describe()
    assert result.markdown == text, result.describe()
    assert result.json["metadata"]["frontmatter"] is None


def test_front_matter_with_a_yaml_set_writes_json(run_cli, e2e_dir):
    """Review: a ``!!set`` in front matter is a Python set, which JSON cannot hold."""
    text = "---\ntitle: Tagged\ntags: !!set {beta, alpha}\n---\n# Heading\n"

    result = run_cli(b.write(e2e_dir / "tagged.md", text), fmt="both")

    assert result.exit_code == 0, result.describe()
    assert result.json["metadata"]["frontmatter"] == {"title": "Tagged", "tags": ["alpha", "beta"]}


# --- review round: the API's batch outputs, cached documents, stopping workers ------------------------------------

BATCH_SAVE_TWICE = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "root, out, files = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]\n"
    "loader = UnifiedDocumentLoader(ocr_provider=None)\n"
    "def written(results, base):\n"
    "    return {Path(key).name + ':' + Path(key).parent.name: sorted(Path(f).relative_to(base).as_posix()\n"
    "            for f in info['output_files']) for key, info in results.items()}\n"
    "batch = loader.batch_process(root, output_dir=out / 'tree', show_progress=False)\n"
    "listed = loader.batch_process_files(files, output_dir=out / 'list', show_progress=False)\n"
    "print(json.dumps({'batch': written(batch, out), 'files': written(listed, out)}))\n"
)


def test_batch_outputs_to_an_output_folder_never_share_a_name(e2e_dir):
    """Review (item 4 in the Python API): ``report.txt`` and ``report.csv`` both wrote ``report.md`` (the CSV table was
    lost), and ``batch_process_files`` wrote flat by stem, so ``a/q1.txt`` and ``b/q1.txt`` were one file. With an
    output folder, names that would clash get their whole file name, as in the CLI."""
    docs = e2e_dir / "docs"
    b.write(docs / "report.txt", "Text version of the report")
    b.write(docs / "report.csv", "k,v\na,1\n")
    b.write(docs / "sub" / "notes.txt", "Notes in a sub-folder")
    first = b.write(e2e_dir / "a" / "q1.txt", "Quarter one from a")
    second = b.write(e2e_dir / "b" / "q1.txt", "Quarter one from b")

    out = run_api(e2e_dir, BATCH_SAVE_TWICE, docs, e2e_dir / "converted", first, second)

    assert out["batch"] == {"report.txt:docs": ["tree/report.txt.md"], "report.csv:docs": ["tree/report.csv.md"],
                            "notes.txt:sub": ["tree/sub/notes.md"]}, out
    assert out["files"] == {"q1.txt:a": ["list/q1.txt.md"], "q1.txt:b": ["list/q1.txt-2.md"]}, out
    assert "Text version of the report" in read(e2e_dir / "converted" / "tree" / "report.txt.md")
    assert table_rows(read(e2e_dir / "converted" / "tree" / "report.csv.md")) == [["k", "v"], ["a", "1"]]


CACHED_STRUCTURE = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "docx, text, cache = sys.argv[1:4]\n"
    "loader = UnifiedDocumentLoader(ocr_provider=None, cache_dir=cache)\n"
    "out = {}\n"
    "for label, path, fmt in (('docx', docx, 'markdown'), ('text_json', text, 'json')):\n"
    "    runs = [loader.load(path, output_format=fmt) for _ in range(2)]  # the second one is read from the cache\n"
    "    out[label] = [[len(doc.tables) if doc.tables is not None else None,\n"
    "                   len(doc.sections) if doc.sections is not None else None] for doc in runs]\n"
    "out['cache_files'] = len(list(Path(cache).glob('*.json')))\n"
    "print(json.dumps(out))\n"
)


def test_a_document_read_from_the_cache_has_the_tables_and_sections_of_a_fresh_one(e2e_dir):
    """Review: a replay filled ``tables``/``sections`` with ``[]`` for a text file that has none (fresh: ``None``)."""
    docx = b.report_docx(e2e_dir / "report.docx")
    text = b.write(e2e_dir / "notes.txt", "Plain notes")

    out = run_api(e2e_dir, CACHED_STRUCTURE, docx, text, e2e_dir / "cache")

    assert out["cache_files"] == 2, out
    assert out["docx"] == [[1, 2], [1, 2]], out
    assert out["text_json"] == [[None, None], [None, None]], out


FAKE_SOFFICE = "#!/bin/sh\necho $$ > \"$FAKE_SOFFICE_PIDFILE\"\nexec sleep 600\n"

CLI_WITH_FAKE_SOFFICE = (
    "import sys\n"
    "import doc2mark.utils.libreoffice as libreoffice\n"
    "libreoffice._CANDIDATE_PATHS = (sys.argv[1],)\n"
    "from doc2mark.cli import main\n"
    "sys.argv = ['doc2mark', *sys.argv[2:]]\n"
    "main()\n"
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _gone(pid: int, seconds: float = 15) -> bool:
    """Whether process ``pid`` is gone within ``seconds``."""
    deadline = time.monotonic() + seconds
    while _alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
    return True


def _clean_up(proc, pidfile: Path) -> None:
    """Whatever a failing test leaves behind: the CLI and the stand-in LibreOffice (it has a session of its own)."""
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
    if pidfile.exists() and pidfile.read_text().strip() and _alive(int(pidfile.read_text())):
        os.kill(int(pidfile.read_text()), signal.SIGKILL)


def _start_cli_on_a_hanging_libreoffice(e2e_dir, *args):
    """Start the real CLI on a folder with one ``.doc``, whose LibreOffice is a stand-in that never finishes (a real
    one cannot be made to hang on demand; the stand-in only takes the place of the binary). Returns
    ``(process, path of the file that receives the stand-in's pid)``."""
    docs = e2e_dir / "docs"
    b.write(docs / "old.doc", "not really a Word file: LibreOffice is never asked to read it")
    fake = e2e_dir / "soffice"
    fake.write_text(FAKE_SOFFICE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    pidfile = e2e_dir / "soffice.pid"
    proc = subprocess.Popen(
        [sys.executable, "-c", CLI_WITH_FAKE_SOFFICE, str(fake), str(docs), *map(str, args)], cwd=e2e_dir,
        env={**os.environ, "FAKE_SOFFICE_PIDFILE": str(pidfile), "PYTHONIOENCODING": "utf-8"},
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
        start_new_session=True)
    return proc, pidfile


def _soffice_pid(pidfile: Path, proc) -> int:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if pidfile.exists() and pidfile.read_text().strip():
            return int(pidfile.read_text())
        assert proc.poll() is None, f"the CLI ended before LibreOffice started: {proc.stderr.read()}"
        time.sleep(0.2)
    raise AssertionError("the stand-in LibreOffice was never started")


def test_a_timeout_takes_the_libreoffice_of_the_stopped_file_with_it(e2e_dir):
    """Review: LibreOffice runs in a session of its own, so killing the worker left it running (and its profile
    folder in the temp directory) for ever after "timed out"."""
    proc, pidfile = _start_cli_on_a_hanging_libreoffice(e2e_dir, "--timeout", "5", "--skip-errors", "-o",
                                                       e2e_dir / "converted")
    try:
        stdout, stderr = proc.communicate(timeout=60)
        assert proc.returncode == 0 and "timed out after 5 s" in stderr, stdout + stderr
        pid = int(pidfile.read_text())
        assert _gone(pid), f"LibreOffice (pid {pid}) is still running after its file timed out"
    finally:
        _clean_up(proc, pidfile)


def test_terminating_the_cli_stops_its_workers_and_their_libreoffice(e2e_dir):
    """Review: workers run in a process group of their own, so SIGTERM to the CLI (a CI cancel, ``kill``, closing the
    terminal) left them, and a hung LibreOffice, running for ever."""
    proc, pidfile = _start_cli_on_a_hanging_libreoffice(e2e_dir, "-o", e2e_dir / "converted")
    try:
        pid = _soffice_pid(pidfile, proc)
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=30)
        assert proc.returncode != 0
        assert _gone(pid), f"LibreOffice (pid {pid}) is still running after the CLI was terminated"
    finally:
        _clean_up(proc, pidfile)


CLI_START_METHOD = (
    "import multiprocessing, sys\n"
    "multiprocessing.set_start_method(sys.argv[1])\n"
    "from doc2mark.cli import main\n"
    "sys.argv = ['doc2mark', *sys.argv[2:]]\n"
    "main()\n"
)


@pytest.mark.parametrize("method", ["spawn", "forkserver"])
def test_a_folder_run_works_with_every_multiprocessing_start_method(e2e_dir, method):
    """Review: Python 3.14 starts workers with ``forkserver`` on Linux (macOS and Windows use ``spawn``). There the
    parent of a worker is the fork server, not the CLI, so a watchdog that compared ``os.getppid()`` with the CLI's
    pid ended every worker at start ("a conversion worker process did not start")."""
    docs = e2e_dir / "docs"
    b.write(docs / "a.txt", "Alpha file text")
    b.write(docs / "b.txt", "Beta file text")
    out = e2e_dir / "converted"

    proc = subprocess.run([sys.executable, "-c", CLI_START_METHOD, method, str(docs), "-p", "2", "-o", str(out), "-q"],
                          cwd=e2e_dir, capture_output=True, stdin=subprocess.DEVNULL, timeout=120, check=False,
                          encoding="utf-8")

    assert proc.returncode == 0, proc.stderr
    assert tree(out) == ["a.md", "b.md"], proc.stderr


BATCH_NESTED = (
    "import sys\n"
    "from pathlib import Path\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "docs = Path(sys.argv[1])\n"
    "loader = UnifiedDocumentLoader(ocr_provider=None)\n"
    "for _ in range(3):\n"
    "    loader.batch_process(docs, output_dir=docs / 'md', show_progress=False)\n"
)


def test_batch_process_does_not_read_an_output_folder_inside_its_input_folder(e2e_dir):
    """Review (same class as the CLI's re-runs): ``batch_process("docs", output_dir="docs/md")`` converted the outputs
    of the run before (``recursive=True`` is the default), so every run added copies of the text."""
    docs = e2e_dir / "docs"
    b.write(docs / "a.txt", "Alpha file text")
    b.write(docs / "sub" / "b.txt", "Beta file text")

    subprocess.run([sys.executable, "-c", BATCH_NESTED, str(docs)], cwd=e2e_dir, check=True, capture_output=True,
                   stdin=subprocess.DEVNULL, timeout=120)

    assert tree(docs) == ["a.txt", "md/a.md", "md/sub/b.md", "sub/b.txt"]


BATCH_IN_PLACE = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "docs = Path(sys.argv[1])\n"
    "loader = UnifiedDocumentLoader(ocr_provider=None)\n"
    "runs = [loader.batch_process(docs, show_progress=False) for _ in range(2)]\n"
    "print(json.dumps([{Path(k).name: [Path(f).name for f in v['output_files']] for k, v in run.items()}\n"
    "                  for run in runs]))\n"
)


def test_batch_process_next_to_the_inputs_never_rewrites_a_source_with_its_own_conversion(e2e_dir):
    """Review: a Markdown file converted into its own folder wrote its conversion over itself, which dropped its front
    matter. Next to the inputs a run can still be repeated: ``a.md``, the output of ``a.txt``, is an input of the
    second run and is left alone."""
    docs = e2e_dir / "docs"
    source = "---\ntitle: Kept\ntags: [a, b]\n---\n# Post\n\nBody text\n"
    b.write(docs / "post.md", source)
    b.write(docs / "a.txt", "Alpha file text")

    first, second = run_api(e2e_dir, BATCH_IN_PLACE, docs)

    assert read(docs / "post.md") == source
    assert tree(docs) == ["a.md", "a.txt", "post.md"]
    assert first["post.md"] == [] and second["post.md"] == [] and first["a.txt"] == ["a.md"], (first, second)
    assert read(docs / "a.md").strip() == "Alpha file text"
