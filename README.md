# doc2mark

[![PyPI version](https://img.shields.io/pypi/v/doc2mark.svg)](https://pypi.org/project/doc2mark/)
[![Python](https://img.shields.io/pypi/pyversions/doc2mark.svg)](https://pypi.org/project/doc2mark/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Turn any document into clean Markdown -- in one line.

## Features

- Converts PDFs, DOCX/XLSX/PPTX, images, HTML, CSV/JSON, and more
- AI-powered OCR via **OpenAI**, **Google Gemini (Vertex AI)**, or **Tesseract**
- Preserves complex tables (merged cells, rowspan/colspan)
- One unified API + CLI for single files or entire directories
- Batch processing with parallel execution
- Per-call token usage tracking for OpenAI and Vertex AI providers
- Sphinx documentation with GitHub Pages deployment workflow

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
r.document.raw.tables                       # list of Table objects (headers + rows)
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

Uses GPT-4.1 vision. Requires an API key.

```bash
export OPENAI_API_KEY=sk-...
pip install "doc2mark[ocr]"
```

```python
ocr = OCR("openai")
ocr = OCR("openai", model="gpt-4o-mini")                       # cheaper model
ocr = OCR("openai", base_url="http://localhost:11434/v1")       # Ollama / compatible
```

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

## Supported formats

| Category | Formats |
|----------|---------|
| Office | DOCX, XLSX, PPTX |
| PDF | PDF (text + scanned) |
| Images | PNG, JPG, WEBP, TIFF, BMP, GIF, HEIC, HEIF, AVIF (requires `doc2mark[heif]`) |
| Text / Data | TXT, CSV, TSV, JSON, JSONL |
| Markup | HTML, XML, Markdown |
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

### Table output styles

Control how complex tables (with merged cells) are rendered:

```python
loader = UnifiedDocumentLoader(
    table_style="minimal_html",     # clean HTML with rowspan/colspan (default)
    # table_style="markdown_grid",  # markdown with merge annotations
    # table_style="styled_html",    # full HTML with inline styles
)
```

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
