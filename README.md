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

## OCR providers

doc2mark supports three OCR providers. Pass `ocr_provider` to `UnifiedDocumentLoader` to choose one.

### OpenAI

Uses GPT-4.1 vision. Requires an API key.

```bash
export OPENAI_API_KEY=sk-...
```

```python
loader = UnifiedDocumentLoader(ocr_provider="openai")

result = loader.load(
    "scanned_doc.pdf",
    extract_images=True,
    ocr_images=True,
)
```

Customize the model or use an OpenAI-compatible endpoint:

```python
loader = UnifiedDocumentLoader(
    ocr_provider="openai",
    model="gpt-4o-mini",                     # cheaper model
    base_url="http://localhost:11434/v1",     # self-hosted / Ollama
    api_key="any-string",
)
```

### Google Gemini / Vertex AI

Uses Gemini models via Google Cloud. Authenticates with [Application Default Credentials](https://cloud.google.com/docs/authentication/application-default-credentials).

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
```

```python
loader = UnifiedDocumentLoader(
    ocr_provider="vertex_ai",
    project="my-gcp-project",          # or set GOOGLE_CLOUD_PROJECT
)

result = loader.load("scan.pdf", extract_images=True, ocr_images=True)
```

Override model and region:

```python
loader = UnifiedDocumentLoader(
    ocr_provider="vertex_ai",
    project="my-gcp-project",
    model="gemini-2.0-flash",          # default: gemini-3.1-flash-lite-preview
    location="us-central1",            # default: global
)
```

### Tesseract (offline)

Local OCR, no API key needed. Requires [Tesseract](https://github.com/tesseract-ocr/tesseract) installed on your system.

```python
from doc2mark.ocr.base import OCRConfig

loader = UnifiedDocumentLoader(
    ocr_provider="tesseract",
    ocr_config=OCRConfig(language="chinese"),   # optional language hint
)

result = loader.load("scan.png", extract_images=True, ocr_images=True)
```

### Provider comparison

| Provider | Requires | Best for | Install extra |
|----------|----------|----------|---------------|
| `openai` | `OPENAI_API_KEY` | Highest accuracy, complex layouts | `pip install doc2mark[ocr]` |
| `vertex_ai` | GCP service account | Google Cloud workflows, Gemini models | `pip install doc2mark[vertex_ai]` |
| `tesseract` | Tesseract binary | Offline / air-gapped environments | `pip install doc2mark[ocr]` |

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

### OCR prompt templates

doc2mark includes specialized prompts for different content types:

```python
loader = UnifiedDocumentLoader(
    ocr_provider="openai",
    prompt_template="table_focused",    # optimized for tables
)
```

Available templates: `default`, `table_focused`, `document_focused`, `multilingual`, `form_focused`, `receipt_focused`, `handwriting_focused`, `code_focused`.

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

When using OpenAI or Vertex AI directly, each OCR result includes token usage in
its metadata:

```python
from doc2mark.ocr.openai import OpenAIOCR

ocr = OpenAIOCR()
results = ocr.batch_process_images([image_bytes])

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
