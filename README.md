# doc2mark

[![PyPI version](https://img.shields.io/pypi/v/doc2mark.svg)](https://pypi.org/project/doc2mark/)
[![Python](https://img.shields.io/pypi/pyversions/doc2mark.svg)](https://pypi.org/project/doc2mark/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Turn any document into clean Markdown – in one line.

## Why doc2mark?

- Converts PDFs, DOCX/XLSX/PPTX, images, HTML, CSV/JSON, and more
- AI OCR for scans and screenshots (OpenAI)
- Preserves complex tables (merged cells, headers) and basic layout
- One simple API + CLI for single files or whole folders

## Install

```bash
pip install doc2mark[all]
```

## Try it in 30 seconds

```python
from doc2mark import UnifiedDocumentLoader

loader = UnifiedDocumentLoader(ocr_provider='openai')  # or None
result = loader.load('sample_documents/sample_pdf.pdf', extract_images=True, ocr_images=True)
print(result.content)
```

CLI:

```bash
# single file → stdout
doc2mark sample_documents/sample_document.docx

# directory → save files (recursively)
doc2mark sample_documents -o output -r

# enable OCR with OpenAI
export OPENAI_API_KEY=sk-...        # Windows: set OPENAI_API_KEY=...
doc2mark sample_documents/sample_pdf.pdf --ocr openai --ocr-images
```

## Supported formats

- PDF • DOCX • XLSX • PPTX • Images (PNG/JPG/WEBP) • TXT/CSV/TSV/JSON/JSONL • HTML/XML/MD
- Legacy Office (DOC/XLS/PPT/RTF/PPS) via LibreOffice (optional)

## Common recipes

```python
from doc2mark import UnifiedDocumentLoader

loader = UnifiedDocumentLoader(ocr_provider='openai')

# 1) Single file → Markdown string
print(loader.load('document.pdf').content)

# 2) Image with OCR
print(loader.load('screenshot.png', extract_images=True, ocr_images=True).content)

# 3) Batch a folder and save outputs
loader.batch_process(
    input_dir='documents/',
    output_dir='converted/',
    extract_images=True,
    ocr_images=True,
    show_progress=True,
    save_files=True
)
```

## OpenAI OCR (optional)

```bash
export OPENAI_API_KEY=your_key   # Windows: set OPENAI_API_KEY=your_key
```

```python
loader = UnifiedDocumentLoader(ocr_provider='openai')
# Need a cheaper model? Use model='gpt-4o-mini'
```

Use OpenAI‑compatible endpoints (self‑hosted/offline VLM):

```python
# Example: point to an OpenAI‑compatible server (must support vision)
loader = UnifiedDocumentLoader(
    ocr_provider='openai',
    base_url='http://localhost:11434/v1',  # your OpenAI‑compatible endpoint
    api_key='your-key-or-any-string',      # some servers require a token
    model='gpt-4o-mini'
)
```

## Tips

- Use `extract_images=True, ocr_images=True` to convert images to text
- `batch_process(..., save_files=True)` writes `.md` (and `.json` when requested)
- Sample files live in `sample_documents/` — perfect for a quick test

## License

MIT — see `LICENSE`.
