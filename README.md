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

## Install

```bash
# Core (no OCR)
pip install doc2mark

# With OpenAI OCR
pip install doc2mark[ocr]

# With Google Gemini / Vertex AI OCR
pip install doc2mark[vertex_ai]

# Everything
pip install doc2mark[all]
```

## Quick start

```python
from doc2mark import UnifiedDocumentLoader

loader = UnifiedDocumentLoader()
result = loader.load("document.pdf")
print(result.content)
```

## OCR providers

doc2mark supports three OCR providers. Pass `ocr_provider` to `UnifiedDocumentLoader` to choose one.

### OpenAI (default)

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
| Images | PNG, JPG, WEBP, TIFF, BMP, GIF, HEIC, HEIF, AVIF |
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

When using OpenAI or Vertex AI, each OCR result includes token usage in its metadata:

```python
from doc2mark import UnifiedDocumentLoader

loader = UnifiedDocumentLoader(ocr_provider="openai")
result = loader.load("scan.pdf", extract_images=True, ocr_images=True)

# Token usage per OCR call is in result metadata
usage = result.metadata.get("token_usage", {})
print(usage)
# {"input_tokens": 1234, "output_tokens": 567, "total_tokens": 1801}
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

# With Tesseract OCR
doc2mark scan.pdf --ocr tesseract --ocr-images

# Disable OCR entirely
doc2mark report.pdf --ocr none --no-ocr-images

# JSON output
doc2mark report.pdf --format json
```

## License

MIT -- see `LICENSE`.
