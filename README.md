# doc2mark

[![PyPI version](https://img.shields.io/pypi/v/doc2mark.svg)](https://pypi.org/project/doc2mark/)
[![Python](https://img.shields.io/pypi/pyversions/doc2mark.svg)](https://pypi.org/project/doc2mark/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Turn documents into clean, RAG-ready Markdown, in one line.**

```python
from doc2mark import load

print(load("report.pdf").content)
```

doc2mark converts PDFs, Word, Excel and PowerPoint files, images, HTML, e-mail and more into
Markdown that stays faithful to the source: **merged table cells survive, page furniture is
thinned without losing text, multi-column pages are read in order, and scanned pages are read
by a vision LLM into a structured schema** (or by local Tesseract). It is built for what comes
*after* conversion: feeding clean, structured text to an LLM or a retrieval pipeline.

Documentation: <https://luisleo526.github.io/doc2mark/>

---

## Why doc2mark

- **Complex tables survive.** Merged cells (`rowspan`/`colspan`) from Word, PowerPoint, Excel and
  ruled PDF tables are kept as a small HTML table inside the Markdown; other tables are ordinary
  Markdown tables. A line break inside a cell is `<br>`. ([Tables](#tables))
- **Verbatim first.** Text is kept unless there is strong evidence to change it. Bare page numbers
  are dropped; a running header or footer keeps its first copy instead of vanishing; hyphenated
  words keep their hyphen unless the document spells them without one elsewhere.
- **OCR only where it is needed.** With OCR on, each PDF page is routed on its own signals:
  scanned pages, garbled text layers and vector-outlined text are OCR'd from a render, pages with
  a good text layer keep it, and pictures are sent to OCR only when they carry something to read.
- **Structured OCR, not a text dump.** LLM OCR returns an `OCRPage` with a hard wall between the
  verbatim transcription (text, tables, fields) and the model's interpretation (document type,
  summary, entities, figures). Answers that are only a refusal ("I can't help with that") are
  recognised and not indexed as page text, and model output is escaped before it reaches your
  Markdown.
- **Bring your own model.** OpenAI (or any OpenAI-compatible endpoint via `base_url`), Google
  Gemini on Vertex AI, or local Tesseract.
- **No ML stack to host.** Text, tables and structure are parsed locally and deterministically;
  a hosted vision model is called only when you turn OCR on.
- **RAG out of the box.** Section-aware chunking with page spans and heading paths, in characters
  or tokens, and per-document reports (`metadata.extra`) of what was OCR'd, refused or left out.

## Install

```bash
pip install doc2mark                # PDF, Office, images, HTML, e-mail... no OCR
pip install "doc2mark[ocr]"         # + OpenAI and Tesseract OCR providers
pip install "doc2mark[all]"         # every optional feature
```

Python 3.10+. Other extras: `vertex_ai` (Gemini), `typesafe` (optional quality judge),
`tokenizers` (token-sized chunks), `redis` (shared OCR cache), `heif` (HEIC/HEIF images), `mime`.
Tesseract OCR needs the `tesseract` program and its language data; legacy `.doc`/`.xls`/`.ppt`/
`.rtf` files need LibreOffice. See [Installation](https://luisleo526.github.io/doc2mark/installation.html).

## Quick start

```python
from doc2mark import UnifiedDocumentLoader

loader = UnifiedDocumentLoader()          # OCR is only used when you ask for it
result = loader.load("report.pdf")

print(result.content)                     # Markdown
print(result.metadata.page_count)         # 2
chunks = result.get_chunks()              # section-aware chunks for RAG
print(chunks[0].section_title, chunks[0].page_start, chunks[0].page_end)
```

Scanned documents and pictures, with a local or a hosted OCR provider:

```python
from doc2mark import UnifiedDocumentLoader

loader = UnifiedDocumentLoader(ocr_provider="tesseract")   # or "openai", "vertex_ai"
result = loader.load("scan.pdf", ocr_images=True)
print(result.content)
print(result.metadata.extra["ocr_routing"])   # which route the document and its pages took
```

From the command line:

```bash
doc2mark report.pdf -o report.md
doc2mark scan.pdf --ocr tesseract --ocr-images -o scan.md
doc2mark documents/ -r --pattern "*.pdf" -o converted/
```

## Supported formats

| Category | Extensions |
|----------|-----------|
| PDF | `.pdf` (text layer, scans, mixed) |
| Office | `.docx`, `.xlsx`, `.pptx` |
| Legacy Office (via LibreOffice) | `.doc`, `.xls`, `.ppt`, `.pps`, `.rtf` |
| Images | `.png`, `.jpg`, `.jpeg`, `.webp`, `.tif`, `.tiff`, `.bmp`, `.gif`, `.heic`/`.heif` (with `doc2mark[heif]`), `.avif` (Pillow with AVIF support) |
| Text and data | `.txt`, `.csv`, `.json`, `.jsonl` (`.tsv` is recognised but currently fails to convert) |
| Markup | `.html`, `.htm`, `.xml`, `.md`, `.markdown` |
| E-mail | `.eml` |

The format comes from the file extension. Details per format: [Formats](https://luisleo526.github.io/doc2mark/formats.html).

## Tables

Native Markdown cannot express a merged cell, so converters that emit only pipe tables lose
`rowspan`/`colspan`: a group header spanning three columns collapses and the grid misaligns.
doc2mark reads the merges from the source (Word `gridSpan`/`vMerge`, PowerPoint cell spans,
Excel merged ranges, the drawn cell boxes of a PDF table) and writes such tables as HTML inside
the Markdown. The start of `sample_documents/complex-tables/complex_table_test.docx` as doc2mark
writes it:

```html
<table>
<tr>
<th colspan="3">Company Overview</th>
<th>Q1</th>
<th>Q2</th>
<th>Q3</th>
<th>Q4</th>
</tr>
<tr>
<td rowspan="2">Division</td>
<td>Region</td>
<td>Product</td>
<td colspan="2">First Half</td>
<td colspan="2">Second Half</td>
</tr>
...
```

Choose the rendering that fits your consumer:

```python
from doc2mark import UnifiedDocumentLoader

loader = UnifiedDocumentLoader(table_style="minimal_html")     # HTML with rowspan/colspan (default)
# loader = UnifiedDocumentLoader(table_style="markdown_grid")  # Markdown grid + merge annotations
# loader = UnifiedDocumentLoader(table_style="styled_html")    # HTML with inline styles
print(loader.load("sample_documents/complex-tables/complex_table_test.docx").content)
```

OCR'd tables come from the model as HTML and are cleaned to a table-only allowlist
(`colspan`/`rowspan`/`scope`, `<br>` in cells) with bounded spans before they are emitted.
More: [Tables](https://luisleo526.github.io/doc2mark/tables.html).

## How it compares: merged cells

The same table with 8 column merges and 2 row merges, authored as DOCX, PDF, PPTX and XLSX
(`sample_documents/complex-tables/`), plus an untagged real-world spec sheet
(`sample_documents/test-table.pdf`), converted with each tool's default Markdown output and with
Docling's HTML export. Each cell counts the `colspan` / `rowspan` attributes (value > 1) in the
output.

| Document | doc2mark (default Markdown) | markitdown | Docling `export_to_markdown` | Docling `export_to_html` |
|----------|:---:|:---:|:---:|:---:|
| `complex_table_test.docx` | 8 / 2 | 0 / 0 | 0 / 0 | 8 / 2 |
| `complex_table_test.pdf`  | 8 / 2 | 0 / 0 | 0 / 0 | 6 / 0 |
| `complex_table_test.pptx` | 8 / 2 | 0 / 0 | 0 / 0 | 8 / 2 |
| `complex_table_test.xlsx` | 8 / 2 | 0 / 0 | 0 / 0 | 9 / 2 |
| `test-table.pdf` (untagged) | 48 / 0 (see below) | 0 / 0 | 0 / 0 | 8 / 0 |

Measured 2026-09-30 on Linux/aarch64 (CPU) with doc2mark `becb74b`, markitdown 0.1.8 and
Docling 2.131.0, by [`eval/docs_audit/merged_cells.py`](eval/docs_audit/merged_cells.py); the
outputs are discussed in [`eval/docs_audit/results/merged_cells.md`](eval/docs_audit/results/merged_cells.md).

- **markitdown** writes pipe tables: merged cells become blank cells.
- **Docling** keeps spans in its HTML export (and its document model), not in its Markdown
  export. In this run its HTML moved the PDF's last row out of the table and gave that table no
  row spans; its XLSX count includes the sheet title as a one-cell table.
- **doc2mark** puts the spans in the Markdown it returns by default. On the untagged
  `test-table.pdf` (4 columns and 28 merged cells on the page) the raw count overstates it:
  doc2mark splits the grid into 7 columns, so it recovers 26 of the 28 merges as single cells and
  adds 22 spans that only bridge the extra columns (18) or pad empty cells (4); cell values stay
  whole. Docling's HTML recovers 8 of the 28 and splits some values across cells.

markitdown is the lightest option when merged cells do not matter. Docling runs local
layout and table-structure models (TableFormer) and suits fully offline pipelines that can host
them. doc2mark gives merged-cell tables in plain Markdown output without a model stack, and
hands scans and pictures to the OCR provider you choose.


## OCR

```python
from doc2mark import OCR

ocr = OCR("openai")                          # OPENAI_API_KEY; OCR("vertex_ai"), OCR("tesseract")
result = ocr.read_one(open("receipt.png", "rb").read(), task="receipt")

page = result.document                       # OCRPage
print(page.raw.text)                         # verbatim transcription
print([(f.label, f.value) for f in page.raw.fields])
print(page.interpretation.document_type if page.interpretation else None)
print(result.text)                           # the page as safe Markdown
```

- **Providers:** `openai` (default model `gpt-5.4-mini`; `base_url` for any OpenAI-compatible
  endpoint that supports JSON-schema output), `vertex_ai`/`gemini` (default
  `gemini-3.1-flash-lite-preview`, Application Default Credentials), `tesseract` (local,
  transcription only; language codes such as `eng`, `deu`, `chi_tra`, `eng+chi_tra`).
- **Tasks** steer what the model extracts: `auto` (default), `table`, `document`, `form`,
  `receipt`, `handwriting`, `code`. `detail="raw"` skips the interpretation;
  `structured=False` returns free-form Markdown.
- **In documents** the loader decides what to OCR page by page and reports it in
  `metadata.extra` (`ocr_routing`, `ocr_images`, `ocr_issues`), and token usage of LLM providers
  in `metadata.extra["token_usage"]`.
- **Caching:** an in-memory or Redis cache of OCR answers (`ocr_cache=`), and an on-disk cache of
  converted documents (`cache_dir=`).

More: [OCR](https://luisleo526.github.io/doc2mark/ocr.html),
[when pages are OCR'd](https://luisleo526.github.io/doc2mark/ocr_policy.html),
[caching](https://luisleo526.github.io/doc2mark/caching.html).

## Optional: quality judge (TypeSafe/Jev)

doc2mark's rules are deterministic, and when the evidence is weak they keep the text. Three of
those open decisions can be handed to an optional judge, [TypeSafe's Jev](https://docs.typesafe.ai),
which is asked only about the cases the rules leave open:

- **Is this PDF page's text layer legible?** A layer can be valid Unicode and still be nonsense
  (shifted letters, glyph IDs read as characters). A page the judge rates illegible is OCR'd from
  its render (OCR must be on).
- **Is this repeated header/footer line page chrome?** A line the rules kept on several pages is
  thinned, normally to its first copy; the judge never removes a line's last copy.
- **Is this OCR answer only a refusal or an error?** (LLM OCR providers.) Such an answer is re-read
  or dropped instead of being indexed as page text.

Accuracy of each decision on the labelled sets in `tests/data/judge`, rules alone → rules +
judge. TEST is held out from calibration; EXTERNAL (60 items) was written by a reviewer and never
used for calibration. The sets are small: read the numbers as a direction, not a guarantee.

| Decision | TEST | EXTERNAL |
|----------|:---:|:---:|
| Text layer legible? | 67.4 % → 100 % (46 items) | 68.8 % → 100 % (16 items) |
| Repeated line is page chrome? | 50.0 % → 95.2 % (42) | 66.7 % → 100 % (3 asked of 20) |
| OCR answer is a refusal or error? | 76.7 % → 97.8 % (90) | 62.5 % → 83.3 % (24) |

Measured for PR #22 and re-measured on 2026-09-30 at `becb74b` with a fresh verdict cache
(`eval/docs_audit/run_judge_eval.sh`, [results](eval/docs_audit/results/judge_eval.md)); all six
cells were the same.

It is **off by default**. To turn it on:

```bash
pip install "doc2mark[typesafe]"          # also part of doc2mark[all]
export TYPESAFE_API_KEY=...
doc2mark report.pdf --ocr tesseract --ocr-images --judge typesafe -o report.md
```

```python
from doc2mark import UnifiedDocumentLoader

loader = UnifiedDocumentLoader(ocr_provider="tesseract", judge="typesafe")  # or DOC2MARK_JUDGE=typesafe
result = loader.load("report.pdf", ocr_images=True)
print(result.metadata.extra.get("judge"))     # questions asked, cached, failed, tokens, cost
```

Without the extra or the key the output is the same as without a judge, and a question the
service cannot answer is decided by the rules. **With the judge on, document text is sent to a
third party** (TypeSafe, `api.typesafe.ai`): up to 1,500 characters of each judged PDF page (with
OCR on, nearly every text page), the judged header/footer lines and OCR answers of up to 600
characters. Do not enable it for documents your agreement with TypeSafe does not cover. Cost,
latency, privacy and the full evaluation: [Judge](https://luisleo526.github.io/doc2mark/judge.html).

## Documentation

- [Quickstart](https://luisleo526.github.io/doc2mark/quickstart.html) and a
  [minimal RAG pipeline](https://luisleo526.github.io/doc2mark/rag.html)
- User guide: [loading and batches](https://luisleo526.github.io/doc2mark/loading.html),
  [the result model](https://luisleo526.github.io/doc2mark/output.html),
  [PDF text structure](https://luisleo526.github.io/doc2mark/pdf.html),
  [chunking](https://luisleo526.github.io/doc2mark/chunking.html),
  [CLI](https://luisleo526.github.io/doc2mark/cli.html),
  [troubleshooting](https://luisleo526.github.io/doc2mark/troubleshooting.html)
- [API reference](https://luisleo526.github.io/doc2mark/api/index.html)

Build the docs locally with `pip install -e ".[docs]"` and
`python -m sphinx -b html -W --keep-going docs docs/_build/html`.

## Development

```bash
pip install -e ".[all,dev]"
python -m pytest -m "not integration and not requires_api_key and not e2e"   # unit tests, as CI runs them
scripts/run_e2e_docker.sh -q -n 8        # CLI end-to-end suite in Docker (Tesseract + LibreOffice)
```

See [Development](https://luisleo526.github.io/doc2mark/development.html) for the E2E harness and
its `D2M_E2E_*` settings. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

MIT -- see `LICENSE`.

## Acknowledgements

[markitdown](https://github.com/microsoft/markitdown) (Microsoft) pioneered the lightweight
"everything to Markdown for LLMs" workflow, and [Docling](https://github.com/docling-project/docling)
(IBM) set the bar for self-hosted, ML-driven table-structure recovery with TableFormer. doc2mark
targets a different point in the design space: merged-cell fidelity and LLM-interpreted
structured OCR without a local model stack. The comparison above is meant to help you pick the
right tool.
