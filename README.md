# doc2mark

[![PyPI version](https://img.shields.io/pypi/v/doc2mark.svg)](https://pypi.org/project/doc2mark/)
[![Python](https://img.shields.io/pypi/pyversions/doc2mark.svg)](https://pypi.org/project/doc2mark/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Turn any document into clean, RAG-ready Markdown — in one line.**

```python
from doc2mark import load

print(load("report.pdf").content)
```

doc2mark converts PDFs, Office files, images, HTML, and more into Markdown that's
faithful to the original — **merged table cells survive, headers/footers get
stripped, and scanned pages are read by a vision LLM into a structured schema**
instead of a flat text blob. It's built for the part everyone hits *after*
conversion: feeding clean, structured text to an LLM or a retrieval pipeline.

---

## Why doc2mark

Most "doc → markdown" tools are fine until the document gets real — a financial
statement with a merged-cell header, a scanned invoice, a slide deck with a
chart. doc2mark is built for exactly those:

- **🧩 Complex tables survive.** Merged cells (`rowspan`/`colspan`), multi-level
  headers, and group headers are preserved as clean HTML — not flattened into a
  mangled markdown grid. ([See below.](#complex-tables-the-part-others-flatten))
- **🧠 Structured OCR, not a text dump.** Scanned/image pages return an
  `OCRPage` with a hard wall between **verbatim transcription** and **model
  interpretation** (document type, summary, key-value fields, figures, entities).
- **🧹 Noise removed.** Repeated page headers, footers, and page numbers are
  detected and dropped before they pollute your markdown or your RAG chunks.
- **🔌 Bring your own model.** OpenAI, Google Gemini (Vertex AI), local Tesseract,
  or any OpenAI-compatible endpoint (Ollama, vLLM, LM Studio) via `base_url`.
- **🪶 No ML stack to host.** No multi-gigabyte model downloads — text parsing is
  local and deterministic; OCR calls a hosted vision model only when you ask.
- **📚 RAG out of the box.** Section-aware, token-budgeted chunking with page
  spans and heading hierarchy preserved.

### How it compares

doc2mark sits between the ultra-light converters and the heavy ML pipelines:
richer output than a pure text converter, far lighter to run than a local
model stack — and the only one of the three that returns an **LLM-interpreted**
structured page.

| | **doc2mark** | **markitdown** (Microsoft) | **Docling** (IBM) |
|---|:---:|:---:|:---:|
| Core approach | Vision-LLM + rule-based hybrid | Text/XML parsing | Local ML pipeline (layout + TableFormer) |
| Merged cells (`rowspan`/`colspan`) | ✅ in default markdown | ❌ flattened ([known issue](https://github.com/microsoft/markitdown/issues/1211)) | ✅ but HTML export only¹ |
| Structured OCR schema | ✅ raw + interpretation | ❌ markdown text only | ✅ structured doc model |
| LLM semantic layer (summary, fields, entities, figures) | ✅ | ❌ | ❌ (structure, not analysis) |
| Header/footer/page-number stripping | ✅ | ❌ | ✅ |
| RAG chunking built in | ✅ | ❌ | ✅ |
| Fully offline / no API cost | ⚠️ Tesseract (raw only) | ✅ | ✅ |
| Setup weight | 🪶 light (pip + API key) | 🪶🪶 very light | 🏋️ heavier (ML models) |

**Pick markitdown** when you want the lightest possible text extraction and
don't care about merged cells. **Pick Docling** when you need fully-offline,
best-in-class table-structure recognition from a self-hosted model stack and
can pay the setup/compute cost. **Pick doc2mark** when you want merged-cell
fidelity *and* an LLM-grade structured read of each page, with a one-line API
and no model hosting — using whichever vision model you already pay for.

> Honest note: Docling's TableFormer is a purpose-trained table model and is
> excellent at table *structure recovery*. doc2mark reaches comparable merged-cell
> fidelity through the vision model + a rule-based renderer, and adds a semantic
> interpretation layer those tools don't — but for fully air-gapped, no-API
> table extraction, Docling is the stronger fit.

---

## Complex tables: the part others flatten

Native Markdown can't express a merged cell. Tools that emit pure markdown
tables therefore *lose* `rowspan`/`colspan` — a group header spanning three
columns collapses, and the grid misaligns.

doc2mark keeps the real structure. Both the native Office/PDF table extractor
(`doc2mark/core/table.py`) and the vision-OCR table model emit clean HTML:

```html
<table>
  <tr><th rowspan="2">Region</th><th colspan="2">2024</th></tr>
  <tr><th>Q1</th><th>Q2</th></tr>
  <tr><td>EMEA</td><td>$1.2M</td><td>$1.5M</td></tr>
</table>
```

That `<th colspan="2">2024</th>` group header and the `rowspan="2">Region</th>`
corner cell are exactly what a flat markdown grid cannot represent. Choose the
rendering that fits your downstream consumer:

```python
loader = UnifiedDocumentLoader(
    table_style="minimal_html",     # clean HTML with rowspan/colspan (default)
    # table_style="markdown_grid",  # markdown with merge annotations
    # table_style="styled_html",    # full HTML with inline styles
)
```

For OCR'd tables, the vision model is explicitly instructed to reproduce merged
cells, and the resulting HTML is sanitized to a strict table-only allowlist
(`colspan`/`rowspan`/`scope`) before it's ever emitted — so a structured table
read from an image is both faithful and safe to embed.

### Measured: merged-cell fidelity

The same merged-cell table, authored in four formats (plus a real-world spec
sheet PDF), converted with doc2mark, Microsoft markitdown, and IBM Docling. The
number is `colspan`/`rowspan` attributes recovered. The result that matters:
**doc2mark preserves merged cells in the markdown you get by default.**

| Document | doc2mark (default) | markitdown | Docling `to_markdown` | Docling `to_html` |
|----------|:---:|:---:|:---:|:---:|
| `complex_table_test.docx` | ✅ 8 / 2 | ❌ 0 / 0 | ❌ 0 / 0 | ✅ 8 / 2 |
| `complex_table_test.pdf`  | ✅ 8 / 2 | ❌ 0 / 0 | ❌ 0 / 0 | ✅ 8 / 0 |
| `complex_table_test.pptx` | ✅ 8 / 2 | ❌ 0 / 0 | ❌ 0 / 0 | ✅ 8 / 2 |
| `complex_table_test.xlsx` | ✅ 9 / 2 | ❌ 0 / 0 | ❌ 0 / 0 | ✅ 9 / 2 |
| `test-table.pdf` (untagged PDF) | ⚠️ 7 / 0 (see note) | ❌ 0 / 0 | ❌ 0 / 0 | ✅ 8 / 0 |

Reading the table:

- **markitdown** emits no `<table>` at all — merges collapse into blank,
  misaligned cells (DOCX/PPTX/XLSX) or the table dissolves into loose text (PDF).
- **Docling** has excellent table-structure recovery (TableFormer) and *does*
  preserve spans — but **only through `export_to_html()` or its structured API**.
  Its `export_to_markdown()`, the usual doc→markdown path, flattens every span to
  zero, just like markitdown.
- **doc2mark** embeds span-preserving HTML tables directly in its default
  markdown output (`.content`), so the merges are there without choosing a
  special export mode.

So if your pipeline consumes markdown (most RAG/LLM pipelines do), doc2mark is
the only one of the three that hands you merged-cell tables out of the box for
documents that carry real table structure.

**Where doc2mark is weaker — be honest about it.** The first four documents
carry real table structure in their source (tagged Office/PDF), which doc2mark
reads directly. `test-table.pdf` is an *untagged* real-world spec sheet where
structure has to be inferred from text geometry. There, doc2mark's text path
over-segments columns and leaves group headers (`1.0 TSI/85 kW`) as a cell plus
empty padding instead of a true `colspan` — its 7 spans are only the section
divider rows, not the header groups. Docling's TableFormer correctly merges
those group headers (the `colspan="2"` on `1.0 TSI/85 kW`). If your inputs are
mostly untagged scanned/printed PDFs, Docling — or doc2mark's own vision-LLM OCR
path (`ocr_images=True`), which reads the table from the rendered image — is the
better choice for table fidelity than the default text extraction.

> ¹ Measured on the documents above with the current released versions, June 2026.
> Docling recovers spans in `export_to_html()` and its structured `DoclingDocument`,
> but `export_to_markdown()` flattens them. Your numbers may vary by version.

Reproduce it on your own files — no API key needed, this is the text path:

```python
from doc2mark import load
print(load("sample_documents/complex-tables/complex_table_test.docx").content)
```

---

## Install

```bash
pip install doc2mark
```

Optional extras unlock additional capabilities:

| Extra | Install command | Purpose |
|-------|----------------|---------|
| `[ocr]` | `pip install doc2mark[ocr]` | OpenAI and Tesseract OCR providers |
| `[vertex_ai]` | `pip install doc2mark[vertex_ai]` | Google Gemini / Vertex AI OCR provider |
| `[heif]` | `pip install doc2mark[heif]` | HEIC, HEIF, and AVIF image support |
| `[mime]` | `pip install doc2mark[mime]` | Improved MIME-type detection via `python-magic` |
| `[redis]` | `pip install doc2mark[redis]` | Redis backend for OCR result caching |
| `[tokenizers]` | `pip install doc2mark[tokenizers]` | Token-based RAG chunking via `tiktoken` |
| `[all]` | `pip install doc2mark[all]` | All of the above |

## Quick start

```python
from doc2mark import UnifiedDocumentLoader

loader = UnifiedDocumentLoader()
result = loader.load("document.pdf")
print(result.content)
```

`UnifiedDocumentLoader()` can process text-first documents without OCR credentials.
OCR providers are initialized only when OCR is requested.

## OCR

doc2mark includes an AI-powered OCR layer that returns **structured output** by
default. The `OCR` facade is the recommended entry point.

### Quick example

```python
from doc2mark import OCR

ocr = OCR("openai")                        # creds from OPENAI_API_KEY env var
results = ocr.read([image_bytes])           # List[bytes] -> List[OCRResult]
r = results[0]

# Structured output (the default)
r.document.raw.text                         # verbatim transcription
r.document.raw.tables                       # list of Table objects (HTML + flat view)
r.document.raw.fields                       # list of KeyValue pairs (forms, receipts)
r.document.interpretation.summary           # model's summary
r.document.interpretation.document_type     # "receipt", "form", "table", ...

# Back-compat markdown string (rendered from the structured data)
r.text
```

For a single image, use `read_one`:

```python
r = ocr.read_one(image_bytes)
print(r.document.raw.text)
```

### Structured output schema

Every result carries an `OCRPage` on `result.document` with a hard boundary
between **raw extraction** (verbatim transcription, no inference) and
**interpretation** (the model's analysis):

```python
OCRPage(
    raw=RawExtraction(
        text="WHOLE FOODS MARKET\n123 Main St\nOrganic Bananas  $2.49\n...",
        tables=[Table(
            caption="Line items",
            headers=["Item", "Price"],
            rows=[["Organic Bananas", "$2.49"], ["Almond Milk", "$4.99"]],
            # `html` is the authoritative view — it alone can carry merged cells
            # (colspan/rowspan). `headers`/`rows` are a best-effort flat view.
            html="<table><tr><th>Item</th><th>Price</th></tr>...</table>",
        )],
        fields=[
            KeyValue(label="Merchant", value="Whole Foods Market"),
            KeyValue(label="Subtotal", value="$7.48"),
            KeyValue(label="Tax", value="$0.62"),
            KeyValue(label="Total", value="$8.10"),
        ],
        detected_language="en",
        has_handwriting=False,
    ),
    interpretation=Interpretation(
        document_type="receipt",
        summary="A grocery receipt for two items totaling $8.10 including tax.",
        key_findings=["2 line items", "Total $8.10", "Tax rate ~8.3%"],
        self_confidence=0.93,
        legibility="high",
    ),
)
```

> A `Table` carries three views: **`html`** (preferred — the only one that
> encodes merged cells via `colspan`/`rowspan`), `headers`/`rows` (a flat
> best-effort grid for simple machine access), and `markdown` (a simple-table
> fallback). For anything with merged cells, read `html`.

### Tasks

Tasks replace the old prompt templates. Set a task at construction time or per
call:

```python
ocr = OCR("openai", task="receipt")          # all calls default to receipt
results = ocr.read(images, task="table")     # per-call override
```

For mixed batches, assign a task per image:

```python
results = ocr.read(images, tasks=["table", "receipt", "handwriting"])
```

Available tasks: `auto` (default), `table`, `document`, `form`, `receipt`,
`handwriting`, `code`.

### Raw and legacy modes

Skip the interpretation pass to save tokens:

```python
results = ocr.read(images, detail="raw")
# r.document.interpretation is None; r.document.raw is still populated
```

Disable structured output entirely for free-form markdown (legacy behaviour):

```python
results = ocr.read(images, structured=False)
# r.text contains free-form markdown; r.document is None
```

### Providers

#### OpenAI

Uses a GPT vision model (default `gpt-5.4-mini`). Requires an API key.

```bash
export OPENAI_API_KEY=sk-...
pip install "doc2mark[ocr]"
```

```python
ocr = OCR("openai")
ocr = OCR("openai", model="gpt-4o-mini")                        # different model
ocr = OCR("openai", base_url="http://localhost:11434/v1")       # Ollama / compatible
```

The `base_url` override points the OpenAI provider at any OpenAI-compatible
endpoint — Ollama, vLLM, LM Studio, or a self-hosted gateway — so you can run a
local or private vision model through the same API.

#### Google Gemini

Uses Gemini models via `langchain-google-genai`. Both `"vertex_ai"` and
`"gemini"` are accepted as provider names.

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
pip install "doc2mark[vertex_ai]"
```

```python
ocr = OCR("gemini")
ocr = OCR("vertex_ai", model="gemini-2.0-flash")
```

#### Tesseract (offline)

Local OCR, no API key. Returns `raw` only (`interpretation` is always `None`).

```bash
pip install "doc2mark[ocr]"
```

```python
ocr = OCR("tesseract", language="eng")
```

#### Provider comparison

| Provider | Requires | Best for | Install extra |
|----------|----------|----------|---------------|
| `openai` | `OPENAI_API_KEY` | Highest accuracy, complex layouts | `doc2mark[ocr]` |
| `vertex_ai` / `gemini` | GCP service account | Google Cloud, Gemini models | `doc2mark[vertex_ai]` |
| `tesseract` | Tesseract binary | Offline / air-gapped (raw only) | `doc2mark[ocr]` |

### Concurrency

Control how many images are OCR'd in parallel:

```python
ocr = OCR("openai", max_concurrency=32)
```

Or set the `OCR_MAX_CONCURRENCY` environment variable. When neither is set,
LangChain's default thread pool is used.

To cut Vision-API token cost on large scans, cap the longest image side before
upload by setting the `OCR_MAX_IMAGE_DIM` environment variable (off by default):

```bash
export OCR_MAX_IMAGE_DIM=1536   # downscale any image whose longest side exceeds 1536px
```

Images already within the bound are left untouched.

### Using OCR with the document loader

`UnifiedDocumentLoader` still works for document-level processing and uses the
OCR layer internally when `ocr_images=True`:

```python
from doc2mark import UnifiedDocumentLoader

loader = UnifiedDocumentLoader(ocr_provider="openai")
result = loader.load("scan.pdf", extract_images=True, ocr_images=True)
```

### Deprecation notice

The old `OCRConfig` fields `enhance_image`, `detect_tables`, `detect_layout`,
`timeout`, `max_retries`, and `extra` are **inert for LLM providers** (they
were only read by Tesseract or by nobody). Setting them now emits a
`DeprecationWarning` and they will be removed in a future release. Use `task`
and the structured output controls instead.

## Clean PDFs: headers, footers & page numbers removed

Large PDFs repeat the same header, footer, and page number on every page. Left
in, they pollute your markdown and bloat your RAG chunks with noise. doc2mark's
PyMuPDF pipeline detects content that recurs in the top/bottom margin zone
across the document and drops it automatically — no configuration required.

This is on by default for multi-page PDFs and is applied before both markdown
rendering and RAG chunking, so the repeated chrome never reaches your output or
your vector store.

## Supported formats

| Category | Formats |
|----------|---------|
| Office | DOCX, XLSX, PPTX |
| PDF | PDF (text + scanned) |
| Images | PNG, JPG, WEBP, TIFF, BMP, GIF, HEIC, HEIF, AVIF (requires `doc2mark[heif]`) |
| Text / Data | TXT, CSV, TSV, JSON, JSONL |
| Markup | HTML, XML, Markdown |
| Email | EML (`message/rfc822`) |
| Legacy | DOC, XLS, PPT, RTF, PPS (requires LibreOffice) |

## Common recipes

### Single file

```python
from doc2mark import load

# Text-only extraction (no OCR)
md = load("report.pdf").content

# With OCR for embedded images
md = load("report.pdf", extract_images=True, ocr_images=True).content
```

### Batch processing

```python
from doc2mark import UnifiedDocumentLoader

loader = UnifiedDocumentLoader(ocr_provider="openai")

loader.batch_process(
    input_dir="documents/",
    output_dir="converted/",
    extract_images=True,
    ocr_images=True,
    save_files=True,
    show_progress=True,
)
```

Process documents concurrently (opt-in) and track progress with a callback.
`max_workers` defaults to sequential; results preserve input order and per-file
error entries:

```python
loader.batch_process(
    input_dir="documents/",
    output_dir="converted/",
    max_workers=8,
    progress_callback=lambda done, total, path: print(f"{done}/{total} {path}"),
)
```

### Process specific files

```python
from doc2mark import batch_process_files

results = batch_process_files(
    ["invoice.pdf", "contract.docx", "receipt.png"],
    output_dir="output/",
    extract_images=True,
    ocr_images=True,
)
```

### OCR tasks

Use the `OCR` facade with a `task` to optimize extraction for specific content:

```python
from doc2mark import OCR

ocr = OCR("openai", task="receipt")     # receipt-optimized extraction
r = ocr.read_one(image_bytes)
print(r.document.raw.fields)            # KeyValue pairs: merchant, total, tax, ...
```

Available tasks: `auto`, `table`, `document`, `form`, `receipt`, `handwriting`,
`code`. See the [OCR section](#ocr) above for full details.

### Token usage tracking

Each OCR result includes token usage in its metadata when using OpenAI or
Gemini:

```python
from doc2mark import OCR

ocr = OCR("openai")
results = ocr.read([image_bytes])

usage = results[0].metadata.get("token_usage", {})
print(usage)
# {"input_tokens": 1234, "output_tokens": 567, "total_tokens": 1801}
```

### OCR result caching

doc2mark can cache OCR results so repeated processing of the same images skips
the OCR provider call. Two backends ship out of the box: an in-memory cache and
a Redis-backed cache.

```python
from doc2mark import load, create_ocr_cache, MemoryOCRCache

# In-memory cache (default settings)
cache = MemoryOCRCache(ttl_seconds=3600, max_entries=1024)
result = load("scan.pdf", extract_images=True, ocr_images=True, ocr_cache=cache)

# Or use the factory helper
cache = create_ocr_cache("memory", ttl_seconds=7200)
```

For production workloads, use Redis (requires `pip install doc2mark[redis]`):

```python
from doc2mark import create_ocr_cache, UnifiedDocumentLoader

cache = create_ocr_cache("redis", redis_url="redis://localhost:6379/0")

loader = UnifiedDocumentLoader(ocr_provider="openai", ocr_cache=cache)
result = loader.load("scan.pdf", extract_images=True, ocr_images=True)
```

See the [caching documentation](docs/caching.rst) for the full API reference.

### Chunking for RAG

Split a processed document into section-aware chunks suitable for
retrieval-augmented generation pipelines:

```python
from doc2mark import load, chunk_content, ChunkingConfig

result = load("report.pdf", output_format="json")

config = ChunkingConfig(
    max_chunk_size=1500,   # max characters per chunk
    overlap=200,           # overlap between consecutive chunks
    split_on_heading_level=2,  # split on h1 and h2
    keep_tables_whole=True,
    include_page_markers=False,
)

chunks = chunk_content(result.json_content, config)

for chunk in chunks:
    print(chunk.chunk_index, chunk.section_title, len(chunk.content))
```

Each `Chunk` carries `section_title`, `section_hierarchy`, `page_start`,
`page_end`, `content_types`, and `chunk_index` so downstream vector stores can
preserve document structure.

To size chunks by **tokens** instead of characters (matching an embedding
model's context budget), set `size_unit="tokens"` — this uses `tiktoken`
(`pip install doc2mark[tokenizers]`; falls back to character counting if it is
not installed):

```python
config = ChunkingConfig(
    max_chunk_size=512,        # tokens, not characters
    size_unit="tokens",
    encoding_name="cl100k_base",
)
```

## CLI

```bash
# Single file to stdout
doc2mark report.pdf

# Save to file
doc2mark report.pdf -o report.md

# Batch convert a directory
doc2mark documents/ -o converted/ -r

# With OpenAI OCR
doc2mark scan.pdf --ocr openai --ocr-images

# With Vertex AI OCR
doc2mark scan.pdf --ocr vertex_ai --ocr-images

# With Tesseract OCR
doc2mark scan.pdf --ocr tesseract --ocr-images

# Disable OCR entirely
doc2mark report.pdf --ocr none --no-ocr-images

# JSON output
doc2mark report.pdf --format json
```

CLI OCR is disabled by default. Use `--ocr-images` with an OCR provider to OCR
embedded images or image files.

## Documentation

Build the Python docs locally:

```bash
pip install -e ".[docs]"
python -m sphinx -b html -W --keep-going docs docs/_build/html
```

The repository includes `.github/workflows/docs.yml` for GitHub Pages. In the
GitHub repository settings, set Pages source to **GitHub Actions**.

## License

MIT -- see `LICENSE`.

## Acknowledgements & comparison

doc2mark stands on the shoulders of the document-AI community.
[markitdown](https://github.com/microsoft/markitdown) (Microsoft) pioneered the
lightweight "everything → markdown for LLMs" workflow, and
[Docling](https://github.com/docling-project/docling) (IBM) set the bar for
self-hosted, ML-driven table structure recovery with TableFormer. doc2mark
targets a different point in the design space — LLM-interpreted structured
output and merged-cell fidelity without a local model stack — and the
comparison above is meant to help you pick the right tool, not to diminish
excellent prior work.
