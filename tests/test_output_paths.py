"""Unit tests for the output-name planner and the tables/sections reader.

These run on file-system facts an E2E run on one machine cannot vary (Unicode normalisation, odd file names) or on
content items no converter emits on demand; everything else about them is covered in ``tests/e2e/test_formats_fixes.py``.
"""

import unicodedata
from pathlib import Path

from doc2mark.core.structure import tables_and_sections
from doc2mark.utils.output_paths import plan_output_names


def plan(names, suffixes=(".md",), out="/out", root="/in"):
    """``{input name: output name without extension}`` for files of one folder."""
    files = [Path(root) / name for name in names]
    relative = {path: path.relative_to(root) for path in files}
    planned = plan_output_names(files, relative, Path(out), suffixes)
    return {path.relative_to(root).as_posix(): base.as_posix() for path, base in planned.items()}


def test_files_without_a_clash_keep_their_stem_in_the_mirrored_tree():
    assert plan(["a.txt", "b.md", "sub/a.txt"]) == {"a.txt": "a", "b.md": "b", "sub/a.txt": "sub/a"}


def test_the_dots_of_a_file_name_are_part_of_its_stem():
    assert plan(["v1.2.txt", "v1.3.txt"]) == {"v1.2.txt": "v1.2", "v1.3.txt": "v1.3"}


def test_files_that_share_a_stem_get_their_whole_file_name_whatever_the_order():
    expected = {"report.csv": "report.csv", "report.md": "report.md", "report.txt": "report.txt"}

    assert plan(["report.txt", "report.md", "report.csv"]) == expected
    assert plan(["report.csv", "report.txt", "report.md"]) == expected


def test_names_that_differ_only_in_case_or_unicode_normalisation_clash():
    """On a case-insensitive or normalisation-insensitive file system (macOS) they are one output file."""
    nfc, nfd = unicodedata.normalize("NFC", "café"), unicodedata.normalize("NFD", "café")

    assert plan(["Report.txt", "report.md"]) == {"Report.txt": "Report.txt", "report.md": "report.md"}
    assert plan([f"{nfc}.txt", f"{nfd}.md"]) == {f"{nfc}.txt": f"{nfc}.txt", f"{nfd}.md": f"{nfd}.md"}


def test_a_fallback_name_that_is_taken_gets_a_number():
    planned = plan(["a.txt", "a.md", "a.txt.md"])

    assert len(set(planned.values())) == 3 and planned["a.txt.md"] == "a.txt"
    assert planned["a.md"] == "a.md" and planned["a.txt"] == "a.txt-2"


def test_an_output_that_would_replace_an_input_file_gets_its_whole_file_name():
    assert plan(["x.json"], suffixes=(".json",), out="/in") == {"x.json": "x.json"}
    assert plan(["x.json"], suffixes=(".json",), out="/elsewhere") == {"x.json": "x"}


def test_a_file_name_that_is_only_a_suffix_still_gets_a_usable_name():
    """``..txt`` has the stem ``.``, which is not a file name."""
    planned = plan(["..txt", "a.txt"])

    assert planned["a.txt"] == "a" and planned["..txt"] not in ("", ".", "..")


def test_sections_and_tables_come_from_the_content_items():
    items = [
        {"type": "text:title", "content": "Report", "page": 1},
        {"type": "text:normal", "content": "Intro"},
        {"type": "text:section", "content": "Scope\nand aims", "level": 3, "page": 1},
        {"type": "text:section", "content": "  ", "level": 2, "page": 1},
        {"type": "text:section", "content": "Numbered heading", "level": 2, "marker": "1.2", "page": 2},
        {"type": "table", "content": "<table><tr><td>1</td></tr></table>", "page": 2},
        {"type": "table", "content": "| a | b |\n|---|---|\n| 1 | 2 |", "page": 3},
    ]

    tables, sections = tables_and_sections(items)

    assert [(s["level"], s["title"], s["page"]) for s in sections] == [
        (1, "Report", 1), (3, "Scope and aims", 1), (2, "1.2 Numbered heading", 2)]
    assert [(t["format"], t["page"]) for t in tables] == [("html", 2), ("markdown", 3)]
    assert tables[1]["content"].startswith("| a | b |")
