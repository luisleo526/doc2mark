# Merged-cell fidelity, re-measured on main becb74b

This re-measures the README table "Measured: merged-cell fidelity". The table was
added in 833271f (June 2026) and no script was committed with it.

- **Run:** 2026-09-30, 14:19-14:21 UTC, on spark2 (hostname `spark-1693`),
  linux/aarch64, CPU only. Docker image `d2m-e2e:0a3d689002a4`
  (python:3.12-slim-bookworm plus Tesseract, LibreOffice and CJK fonts), Python 3.12.14.
- **doc2mark:** main `becb74b` (becb74bc46b4aad3090232e5b6f6cee73de86cb6),
  `doc2mark.__version__` 0.6.1, installed with `pip install -e ".[ocr]"`.
  PyMuPDF 1.28.2, python-docx 1.2.0, python-pptx 1.0.2, openpyxl 3.1.5.
  No API key was set, so this is the text path.
- **Competitors, in a separate container:**
  - markitdown 0.1.8.
  - Docling 2.131.0 (docling-core 2.99.0, docling-ibm-models 4.0.3,
    docling-parse 7.22.1). OCR was auto-selected: RapidOCR 3.9.2 on onnxruntime 1.30.0.
    torch 2.14.0+cpu.
  - All of these were the latest PyPI releases on the run date.
- **Script:** `eval/docs_audit/merged_cells.py`.
- **Data:** `eval/docs_audit/results/merged_cells.json`. It merges both runs and
  holds versions, host, per-file counts and timings. Under `shown` it also holds
  every output row that carries a span or matches `TSI/` or `MT`.

## Measured table

| Document | doc2mark (default) | markitdown | Docling `to_markdown` | Docling `to_html` |
|---|:---:|:---:|:---:|:---:|
| `complex_table_test.docx` | 8 / 2 | 0 / 0 | 0 / 0 | 8 / 2 |
| `complex_table_test.pdf` | 8 / 2 | 0 / 0 | 0 / 0 | 6 / 0 |
| `complex_table_test.pptx` | 8 / 2 | 0 / 0 | 0 / 0 | 8 / 2 |
| `complex_table_test.xlsx` | 8 / 2 | 0 / 0 | 0 / 0 | 9 / 2 |
| `test-table.pdf` (untagged) | 48 / 0 | 0 / 0 | 0 / 0 | 8 / 0 |

**What `C / R` counts:** the number of `colspan` / `rowspan` attributes with a
value > 1 on the `<td>`/`<th>` tags of the output, which is one per merged cell.
No output had a span attribute of value 1, so the raw counts are the same.

**Why this matches the README's metric:** the complex table has exactly 8
column merges and 2 row merges:
- Column merges: Company Overview 3, First Half 2, Second Half 2,
  Combined Products 2, All Regions Total 2, Subtotal 3, Grand Total 4,
  Annual Total 3.
- Row merges: Division 2 rows, Technology 3 rows.

**Stability:** the final run and the earlier runs of the same day produced
byte-identical outputs for all four tools.

## Against the README, per cell

17 of the 20 cells hold. Three changed:

| Document | doc2mark | markitdown | Docling `to_markdown` | Docling `to_html` |
|---|:---:|:---:|:---:|:---:|
| `complex_table_test.docx` | holds | holds | holds | holds |
| `complex_table_test.pdf` | holds | holds | holds | **6 / 0 (README 8 / 0)** |
| `complex_table_test.pptx` | holds | holds | holds | holds |
| `complex_table_test.xlsx` | **8 / 2 (README 9 / 2)** | holds | holds | holds |
| `test-table.pdf` | **48 / 0 (README 7 / 0)** | holds | holds | holds |

### xlsx, doc2mark: 9 to 8

The sheet has 11 merged ranges:
- 10 belong to the table (8 colspans, 2 rowspans).
- `A1:G1` is the sheet title "Complex Table Test Spreadsheet".

Since #14 (47305d4), the XLSX loader turns leading single-cell title rows into
text above the table. The `XlsxLoader` docstring in
`doc2mark/pipelines/office_advanced_pipeline.py` says so. As a result, the
title is now a paragraph instead of a `colspan="7"` row, and all 8 / 2 table
merges are still present.

Docling's 9 / 2 includes the title as a separate one-cell table:
`<tr><th colspan="7">Complex Table Test Spreadsheet</th></tr>`. On the table
itself, both tools give 8 / 2.

### complex_table_test.pdf, Docling `to_html`: 8 to 6

Docling 2.131.0 leaves the last table row out of the table. It emits the row as
two paragraphs after the table (`<p>Grand Total (All Quarters)</p>`,
`<p>Annual Total: $382K</p>`), so the colspans 4 and 3 are lost.

Rowspans are still 0. "Division" and "Technology" each sit in one row, with
empty cells below:
`<tr><td></td><td>East</td><td colspan="2">Combined Products</td>...`

### test-table.pdf, doc2mark: 7 to 48

The likely source is the PDF table rework in #15 (057341d, "faithful PDF
tables"), which builds merged cells from drawn cell boxes. This run did not
bisect it. The next section breaks the 48 spans down.

## test-table.pdf: the README's two claims

The source table, as read from the rendered page:
- 4 columns: the label with its unit, then MT, AT and DSG.
- 34 rows.
- 28 merged cells, all of them colspans:
  - The group header "1.0 TSI/85 kW" and the 5 values under it span MT and AT (6 merges).
  - The 6 section bands span the full width (6 merges): Transmission, Chassis,
    Outside dimensions, Inside dimensions, Weights and Liquids.
  - 16 values span MT through DSG (16 merges): Wheel drive, the 8 chassis rows,
    the 5 outside dimensions, Storage capacity and Tank capacity.

The rows quoted below come from the tools' outputs (the `--dump` files). Rows
with a span are also in the JSON under `shown`. Each `<tr>` is joined onto one
line, and long rows are trimmed with `...`.

### Claim 1: doc2mark

The README says: "over-segments columns and leaves group headers (`1.0 TSI/85 kW`)
as a cell plus empty padding instead of a true `colspan` -- its 7 spans are only
the section divider rows".

- **"over-segments columns": still true.** doc2mark's grid has 7 columns: the
  label, MT, AT split into 2 columns, and DSG split into 3. The DSG header text
  is cut into two cells:

      <tr> <td>Engine</td> <td>MT</td> <td colspan="2">AT</td> <td colspan="2">D</td> <td>SG</td> </tr>

- **"group header as a cell plus empty padding": no longer true.** The group
  header is now a real colspan that covers exactly the MT and AT grid columns:

      <tr> <th>Technical Description</th> <th colspan="3">1.0 TSI/85 kW</th> <th colspan="3">1.5 TSI/110 kW</th> </tr>

  Empty padding is left in two other rows:

      <tr> <td>Chassis</td> <td colspan="6"></td> </tr>
      <tr> <td>Storage capacity [l]</td> <td colspan="2"></td> <td colspan="2">385 / 491 / 1 405</td> <td colspan="2"></td> </tr>

- **"its 7 spans are only the section divider rows": no longer true.** There
  are 48 spans now. The script's per-value count is 5 x colspan 7, 16 x colspan 6
  (1 of them empty), 17 x colspan 3 and 10 x colspan 2 (2 of them empty). They
  fall into three groups:
  - **26 are real merges recovered as one cell:**
    - 5 of the 6 section bands, e.g. `<td colspan="7">Transmission</td>`.
    - 15 of the 16 full-width values, e.g. `<td colspan="6">Front Wheel Drive</td>`.
    - The group header and the 5 values under it, e.g. `<td colspan="3">999</td>`.
  - **18 exist only because of the extra grid columns:**
    - The 11 single cells of the DSG column carry `colspan="3"`, e.g.
      `<td colspan="3">1 498</td>`.
    - The 6 cells of the AT column carry `colspan="2"`, e.g. `<td colspan="2">18.09</td>`.
    - The "D" fragment of the split DSG header.
  - **4 are the padding cells** of the Chassis and Storage capacity rows shown above.

  So on this file the raw count overstates fidelity: doc2mark recovers 26 of
  the 28 real merges, and cell values stay whole.

  The PDF's text layer also holds a second, stale copy of some values that the
  rendered page does not show (`1193-1248`, `385 / 1 405`). doc2mark emits
  only the shown values, e.g. `<td>1 193-1 248</td>`.

### Claim 2: Docling

The README says: "Docling's TableFormer correctly merges those group headers
(the `colspan="2"` on `1.0 TSI/85 kW`)".

**Still true** with Docling 2.131.0:

    <tr><th>Technical Description</th><td></td><th colspan="2">1.0 TSI/85 kW</th><th>1.5 TSI/110 kW</th></tr>

Docling's 8 spans are:
- the group header,
- the 5 values under it (e.g. `<td colspan="2">999</td>`),
- 2 full-width rows: `Wheels` and `Tyres` (`colspan="3"`).

That is 8 of the 28 real merges, with no spurious spans. Docling's grid has 5
columns because it puts the unit in its own column (`<td>[cm 3 ]</td>`). The
other merges are lost:

- The 6 section bands become a label followed by four empty cells:

      <tr><td>Transmission</td><td></td><td></td><td></td><td></td></tr>

- The other 14 full-width values are either split mid-value across two cells or
  pushed into one column with empty padding:

      <tr><td>Wheel drive</td><td></td><td></td><td>Front</td><td>Wheel Drive</td></tr>
      <tr><td>Length</td><td>[mm]</td><td></td><td>4</td><td>225</td></tr>
      <tr><td>Tank capacity</td><td>[l]</td><td></td><td>50</td><td>Litres</td></tr>
      <tr><td>Brake - front</td><td></td><td></td><td></td><td>Disc</td></tr>

- It emits both the shown values and the stale copies from the text layer:

      <tr><td>Kerb weight</td><td>[kg]</td><td>1193-1248 1 193-1 248</td>...
      <tr><td>Storage capacity</td><td>[l]</td><td></td><td>385 / 385 /</td><td>1 405 491 / 1 405</td></tr>

### What this means for the README paragraph

The paragraph "Where doc2mark is weaker" recommends Docling for untagged PDFs,
calling it the better choice for table fidelity. This file no longer supports
that on current versions:
- **doc2mark** recovers 26 of 28 merges and keeps values whole. It still
  over-segments columns, which adds 18 spurious spans and splits "DSG".
- **Docling** recovers 8 of 28 and splits values across cells.

## Other statements in the same README section

These were checked on the same outputs:

- **"markitdown emits no `<table>` at all": holds.** None of the five outputs
  has a `<table>` tag.
- **"...or the table dissolves into loose text (PDF)": outdated for
  markitdown 0.1.8.** It now emits a pipe table for `complex_table_test.pdf`,
  where merges become blank cells:
  `| Company Overview | | | Q1 | Q2 | Q3 | Q4 |`.
  For `test-table.pdf` the output mixes 31 pipe-table lines with 19 loose text
  lines, because multi-line cells escape the table.
- **"`export_to_markdown()` ... flattens every span": holds.** Docling repeats
  the text in every covered cell:
  `| Company Overview | Company Overview | Company Overview | Q1 | ...`.
- **Incidental:** no tool can read the page title of `test-table.pdf`,
  "Technical Specifications", because its text layer has no usable character
  mapping:
  - doc2mark emits `# ` followed by 17 U+FFFD replacement characters and `ations`.
  - Docling emits `## b` followed by mojibake and `ations`.
  - markitdown emits a run of `(cid:NN)` codes and `ations`.

## Commands

Both sides ran in parallel on spark2, each in its own `docker run --rm`
container of `d2m-e2e:0a3d689002a4`.

- `SRC` is the read-only main worktree `~/code/doc2mark-d2m-docs-audit-becb74b`.
  Each container copies it to `/work`.
- The script is mounted at `/opt/merged_cells.py`, because that worktree
  predates it. From a checkout that contains it, call
  `eval/docs_audit/merged_cells.py` instead. Its docstring gives the plain
  docker command.

The runner was started like this:

```bash
ssh spark2 'tmux -L executors new -d -s d2m-docs-audit-merged \
    "bash -l ~/code/.executors/d2m-docs-audit-merged/final.sh"'
```

This is `~/code/.executors/d2m-docs-audit-merged/final.sh`, verbatim:

```bash
#!/usr/bin/env bash
# d2m-docs-audit-merged, final clean run: both sides in parallel, into $D/final.
# doc2mark: E2E image + `pip install -e ".[ocr]"` of the read-only main worktree.
# Competitors: same image + libgl1/libglib2.0-0 (opencv-python needs libGL) +
# `pip install "markitdown[all]" docling` (CPU torch). files.pythonhosted.org and
# deb.debian.org ran at ~0.1 MB/s from spark2 (attempt 1 stalled in pip for 15 min),
# so PyPI and Debian come from the Tsinghua mirror (same files and hashes).
set -uo pipefail
D="$HOME/code/.executors/d2m-docs-audit-merged"
OUT="$D/final"; mkdir -p "$OUT"
SRC="$HOME/code/doc2mark-d2m-docs-audit-becb74b"
SHA="$(git -C "$SRC" rev-parse HEAD)"
IMAGE=d2m-e2e:0a3d689002a4
FILES="sample_documents/complex-tables/complex_table_test.docx sample_documents/complex-tables/complex_table_test.pdf sample_documents/complex-tables/complex_table_test.pptx sample_documents/complex-tables/complex_table_test.xlsx sample_documents/test-table.pdf"
SHOW='TSI/|\bMT\b|colspan|rowspan'
COPY='mkdir /work && tar -C /src --exclude=.git --exclude=.venv --exclude=.omc --exclude=__pycache__ --exclude="*.egg-info" -cf - . | tar -C /work -xf - && cd /work'
common=(--rm --init -h "$(hostname)" -v "$SRC:/src:ro" -v "$OUT:/out" -v "$D/merged_cells.py:/opt/merged_cells.py:ro"
        -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PIP_ROOT_USER_ACTION=ignore
        -e FILES="$FILES" -e SHOW="$SHOW" -e HOST_IDS="$(id -u):$(id -g)")

doc2mark_side() {
    docker run "${common[@]}" --name d2m-docs-audit-merged-doc2mark \
        -v d2m-e2e-pip-cache:/pip-cache -e PIP_CACHE_DIR=/pip-cache -e DOC2MARK_GIT_SHA="$SHA" \
        "$IMAGE" bash -euo pipefail -c "trap 'chown -R \$HOST_IDS /out' EXIT; $COPY"'
            echo "container $(hostname) $(uname -m) $(python --version)"
            s=$SECONDS; pip install -q -e ".[ocr]"; echo "pip install took $((SECONDS-s)) s"
            pip freeze > /out/doc2mark.pip-freeze.txt
            s=$SECONDS
            python /opt/merged_cells.py --tools doc2mark --json /out/merged_cells.doc2mark.json \
                --dump /out/dump --show "$SHOW" $FILES
            echo "measurement took $((SECONDS-s)) s"'
}

competitor_side() {
    docker run "${common[@]}" --name d2m-docs-audit-merged-competitors \
        -v d2m-docs-audit-pip:/cache -e PIP_CACHE_DIR=/cache/pip -e HF_HOME=/cache/hf \
        -e EASYOCR_MODULE_PATH=/cache/easyocr \
        "$IMAGE" bash -euo pipefail -c "trap 'chown -R \$HOST_IDS /out' EXIT; $COPY"'
            echo "container $(hostname) $(uname -m) $(python --version)"
            s=$SECONDS
            sed -i "s|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g" /etc/apt/sources.list.d/debian.sources
            apt-get update -qq && apt-get install -y -qq --no-install-recommends libgl1 libglib2.0-0 > /dev/null
            echo "apt install libgl1 libglib2.0-0 took $((SECONDS-s)) s"
            s=$SECONDS
            timeout 900 pip install -q "markitdown[all]" docling \
                --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
                --extra-index-url https://download.pytorch.org/whl/cpu
            echo "pip install took $((SECONDS-s)) s"
            pip freeze > /out/competitors.pip-freeze.txt
            s=$SECONDS
            timeout 1200 python /opt/merged_cells.py --tools markitdown,docling \
                --json /out/merged_cells.competitors.json --dump /out/dump --show "$SHOW" $FILES
            echo "measurement took $((SECONDS-s)) s"'
}

echo "start $(date -u +%FT%TZ) sha $SHA" > "$OUT/run.log"
( s=$SECONDS; doc2mark_side > "$OUT/doc2mark.log" 2>&1; rc=$?
  echo "wall $((SECONDS-s)) s, exit $rc" >> "$OUT/doc2mark.log"; echo $rc > "$OUT/doc2mark.exit" ) &
( s=$SECONDS; competitor_side > "$OUT/competitors.log" 2>&1; rc=$?
  echo "wall $((SECONDS-s)) s, exit $rc" >> "$OUT/competitors.log"; echo $rc > "$OUT/competitors.exit" ) &
wait
python3 "$D/merged_cells.py" --merge "$OUT/merged_cells.doc2mark.json" "$OUT/merged_cells.competitors.json" \
    --json "$OUT/merged_cells.json" > "$OUT/merged.log" 2>&1
echo "merge exit $?" >> "$OUT/run.log"
echo "end $(date -u +%FT%TZ)" >> "$OUT/run.log"
echo "$(cat "$OUT/doc2mark.exit") $(cat "$OUT/competitors.exit")" > "$OUT/run.exit"
```

Notes on the competitor container:
- **Mirrors.**
  - From spark2, `files.pythonhosted.org` ran at 0.05-0.5 MB/s, measured on the
    host, in containers and by pip itself, while the PyTorch index ran at about
    30 MB/s. So PyPI came from the Tsinghua mirror.
  - Its wheels for docling 2.131.0, markitdown 0.1.8 and docling-ibm-models
    4.0.3 have the same sha256 as on PyPI.
  - The resolved versions were the latest PyPI releases: docling 2.131.0 of
    2026-09-29 and markitdown 0.1.8 of 2026-09-21.
  - Switching Debian to the mirror was only a precaution: `deb.debian.org` was
    not measured, whatever the runner's header comment says.
  - On a normal network, drop the `--index-url` and `sed` lines.
- **libGL.** `libgl1` is required in slim images. Docling's PDF pipeline and its
  RapidOCR engine import `opencv-python`, which needs `libGL.so.1`.
- **Docling options.** Docling ran with its defaults (`DocumentConverter()`).
  torch is the CPU wheel, and no GPU was passed to the container.

## Timing and failures

- **doc2mark side:** 20 s wall. pip install took 17 s from the warm cache; the
  five conversions took 0.55 s in total.
- **Competitor side, final run:** 145 s wall.
  - apt took 14 s and pip took 112 s.
  - The conversions took 17 s, including Docling's model load. Docling needed
    5.8 s and 8.0 s for the two PDFs and under 0.1 s per Office file; markitdown
    needed under 0.2 s per file.
- **Competitor side, total:** about 25 min wall on 2026-09-30, including two
  failed attempts:
  1. **Attempt 1** stalled in `pip install "markitdown[all]" docling` for 15 min.
     `files.pythonhosted.org` was running at about 0.1 MB/s, and the
     SpeechRecognition wheel alone is 33 MB. It was killed, with exit 137.
  2. **Attempt 2** used the mirror, and pip finished in 133 s. Docling then
     failed on both PDFs with
     `ImportError: libGL.so.1: cannot open shared object file` and logged
     "No OCR engine found": RapidOCR could not import cv2 either. Its
     DOCX/PPTX/XLSX and markitdown numbers equalled the final ones.
  3. **Attempt 3 and the final run** added `libgl1 libglib2.0-0` and succeeded.
- **Raw outputs and logs** stay on spark2 under
  `~/code/.executors/d2m-docs-audit-merged/final/`: `dump/<output>/<file>`,
  both logs, and `pip freeze` for each container. They are not committed.
