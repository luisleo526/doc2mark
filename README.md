# doc2mark

[![PyPI version](https://img.shields.io/pypi/v/doc2mark.svg)](https://pypi.org/project/doc2mark/)
[![Python](https://img.shields.io/pypi/pyversions/doc2mark.svg)](https://pypi.org/project/doc2mark/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**doc2mark** converts any document to Markdown while preserving complex structures like tables, using AI-powered OCR when needed.

## ✨ Key Features

- **Universal Format Support**: PDF, DOCX, XLSX, PPTX, HTML, JSON, CSV, and more
- **Table Structure Preservation**: Maintains merged cells, multi-level headers, and complex layouts
- **AI-Powered OCR**: Uses GPT-4 Vision for accurate text extraction from images and scanned documents
- **Multiple Output Formats**: Markdown (default), JSON, or plain text

## 🚀 Quick Start

### Installation

```bash
# Basic installation
pip install doc2mark

# With OCR support
pip install doc2mark[ocr]
```

### Basic Usage

```python
from doc2mark import UnifiedDocumentLoader

# Initialize loader
loader = UnifiedDocumentLoader()

# Convert any document to markdown
result = loader.load('document.pdf')
print(result.content)
```

### With OCR for Scanned Documents

```python
# Enable OCR for scanned documents
loader = UnifiedDocumentLoader(
    ocr_provider='openai',
    api_key='your-openai-api-key'  # or set OPENAI_API_KEY env var
)

# Process scanned PDF
result = loader.load('scanned_document.pdf', ocr_images=True)
print(result.content)
```

## 📊 Handling Complex Tables

doc2mark excels at preserving complex table structures that other tools often destroy:

```python
# Load document with complex tables
result = loader.load('financial_report.xlsx')

# Tables maintain their structure:
# - Merged cells
# - Multi-level headers  
# - Formulas and formatting
# - Cross-sheet references
```

Example output:
```markdown
| Department      | Q1 2024        | Q2 2024        | H1 Total       |
|                 | Revenue | Cost | Revenue | Cost | Revenue | Cost |
|-----------------|---------|------|---------|------|---------|------|
| **Sales**       |         |      |         |      |         |      |
| - North Region  | 125.5   | 45.2 | 142.3   | 48.7 | 267.8   | 93.9 |
| - South Region  | 89.2    | 32.1 | 95.1    | 35.2 | 184.3   | 67.3 |
```

## 🔧 Common Use Cases

### 1. Extract Data from Multiple Formats

```python
from doc2mark import UnifiedDocumentLoader, OutputFormat

loader = UnifiedDocumentLoader()

# Process different file types with the same API
for file in ['report.pdf', 'data.xlsx', 'presentation.pptx']:
    result = loader.load(file)
    print(f"{file}: {len(result.content)} characters extracted")
```

### 2. Convert Scanned PDFs

```python
# Configure for OCR
loader = UnifiedDocumentLoader(
    ocr_provider='openai',
    model='gpt-4.1'
)

# Extract text from scanned documents
result = loader.load(
    'scanned_invoice.pdf',
    extract_images=True,
    ocr_images=True
)
```

### 3. JSON Output for Structured Processing

```python
# Get structured JSON output
result = loader.load('document.docx', output_format=OutputFormat.JSON)

import json
data = json.loads(result.content)
# Process structured data with metadata
```

### 4. Batch Processing

```python
from pathlib import Path

# Process all documents in a directory
docs_dir = Path('./documents')
for doc_path in docs_dir.glob('*.*'):
    try:
        result = loader.load(str(doc_path))
        # Save as markdown
        output_path = doc_path.with_suffix('.md')
        output_path.write_text(result.content)
    except Exception as e:
        print(f"Error processing {doc_path}: {e}")
```

## 📖 Supported Formats

- **PDF** - With text and scanned image support
- **Microsoft Office** - DOCX, XLSX, PPTX (and legacy DOC, XLS, PPT)
- **Plain Text** - TXT, CSV, TSV
- **Web** - HTML, XML
- **Data** - JSON, JSONL
- **Markdown** - MD files

## ⚙️ Configuration Options

### OCR Configuration

```python
from doc2mark import UnifiedDocumentLoader
from doc2mark.ocr.prompts import PromptTemplate

# Configure OCR settings
loader = UnifiedDocumentLoader(
    ocr_provider='openai',
    model='gpt-4.1',
    temperature=0,  # More deterministic output
    max_tokens=4096,  # Maximum response length
    prompt_template=PromptTemplate.TABLE_FOCUSED  # Optimized for tables
)
```

### Available Prompt Templates

- `DEFAULT` - General purpose text extraction
- `TABLE_FOCUSED` - Optimized for tabular data
- `DOCUMENT_FOCUSED` - Preserves document structure
- `FORM_FOCUSED` - Extract form fields and values
- `RECEIPT_FOCUSED` - Invoices and receipts
- `HANDWRITING_FOCUSED` - Handwritten text
- `CODE_FOCUSED` - Source code and technical docs
- `MULTILINGUAL` - Non-English documents

### Output Formats

```python
from doc2mark import OutputFormat

# Markdown format (default)
result = loader.load('file.pdf', output_format=OutputFormat.MARKDOWN)

# JSON format with metadata
result = loader.load('file.pdf', output_format=OutputFormat.JSON)

# Plain text without formatting
result = loader.load('file.pdf', output_format=OutputFormat.TEXT)
```

## 🌍 Language Support

doc2mark preserves the original language of documents:

```python
# Process documents in any language
result = loader.load(
    'document_chinese.pdf',
    language='Chinese',  # Helps optimize OCR
    prompt_template=PromptTemplate.MULTILINGUAL
)
```

## 🛠️ Advanced Features

### Custom OCR Instructions

```python
# Provide specific extraction instructions
result = loader.load(
    'form.pdf',
    instructions="""
    Extract all form fields as key-value pairs.
    Format dates as YYYY-MM-DD.
    Identify checked vs unchecked boxes.
    """
)
```

### Save Extracted Images

```python
# Extract and save images locally
result = loader.load(
    'document_with_images.pdf',
    extract_images=True,
    save_images_locally=True,
    local_image_dir='./extracted_images'
)
```

### Process Specific Excel Sheets

```python
# Load specific sheets from Excel files
result = loader.load(
    'workbook.xlsx',
    sheet_name='Financial Data'  # Future feature
)
```

## 🔍 Error Handling

```python
from doc2mark.core.base import ProcessingError, OCRError

try:
    result = loader.load('document.pdf')
except ProcessingError as e:
    print(f"Document processing failed: {e}")
except OCRError as e:
    print(f"OCR failed: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

## 📊 Integration with RAG Pipelines

doc2mark is perfect for RAG (Retrieval-Augmented Generation) pipelines that need to handle complex documents:

```python
from doc2mark import UnifiedDocumentLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.embeddings import OpenAIEmbeddings
from langchain.vectorstores import Chroma

# Extract content from documents
loader = UnifiedDocumentLoader()
documents = ['report.pdf', 'data.xlsx', 'analysis.docx']

texts = []
for doc in documents:
    result = loader.load(doc)
    texts.append(result.content)

# Split and embed for vector database
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000)
chunks = text_splitter.create_documents(texts)
embeddings = OpenAIEmbeddings()
vectorstore = Chroma.from_documents(chunks, embeddings)
```

## 📝 Requirements

- Python 3.8+
- OpenAI API key (for OCR features)
- Optional: Tesseract (for offline OCR)
- Optional: LibreOffice (for legacy format support)

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙋‍♂️ Support

- **Issues**: [GitHub Issues](https://github.com/luisleo526/doc2mark/issues)
- **Email**: luisleo52655@gmail.com

## ⚠️ Current Limitations

- Excel sheet selection is not yet implemented (processes all sheets)
- Legacy formats (DOC, XLS, PPT) require LibreOffice installation
- Large files may require adjusted timeout settings