# doc2mark

[![PyPI version](https://img.shields.io/pypi/v/doc2mark.svg)](https://pypi.org/project/doc2mark/)
[![Python](https://img.shields.io/pypi/pyversions/doc2mark.svg)](https://pypi.org/project/doc2mark/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**doc2mark** is a powerful Python library that converts 20+ document formats to clean, structured Markdown. Built with a unified API that handles everything from PDFs and Office documents to images and data files, with AI-powered OCR for text extraction from scanned documents and images.

## 📖 Supported Formats

| Category | Formats | Notes |
|----------|---------|-------|
| **PDF** | `.pdf` | Text extraction + OCR for scanned content |
| **Microsoft Office** | `.docx`, `.xlsx`, `.pptx` | Full support with image extraction |
| **Legacy Office** | `.doc`, `.xls`, `.ppt`, `.rtf`, `.pps` | Requires LibreOffice |
| **Images** | `.png`, `.jpg`, `.jpeg`, `.webp` | OCR text extraction with OpenAI GPT-4 Vision |
| **Text/Data** | `.txt`, `.csv`, `.tsv`, `.json`, `.jsonl` | Direct processing |
| **Web/Markup** | `.html`, `.xml`, `.md`, `.markdown` | Structure preservation |

## ✨ Key Features

- **Universal Format Support**: 20+ document formats listed above
- **AI-Powered OCR**: Extract text from scanned documents and images using OpenAI GPT-4 Vision or Tesseract
- **Image Processing**: Process standalone images just like embedded images in documents
- **Batch Processing**: Convert entire directories with progress tracking
- **Table Preservation**: Maintains complex table structures with merged cells
- **Custom API Support**: Use OpenAI-compatible endpoints with base_url parameter

## 🚀 Quick Start

### Installation

```bash
# Basic installation
pip install doc2mark

# With OCR support
pip install doc2mark[ocr]

# With MIME type content detection (optional)
pip install doc2mark[mime]

# With all dependencies
pip install doc2mark[all]
```

### Basic Usage

```python
from doc2mark import UnifiedDocumentLoader

# Initialize loader with OpenAI
loader = UnifiedDocumentLoader(ocr_provider='openai')

# Convert any supported document to markdown
result = loader.load('document.pdf')
print(result.content)

# Process images with OCR
result = loader.load('screenshot.png', ocr_images=True)
print(result.content)

# Batch process multiple files
results = loader.batch_process(
    input_dir='documents/',
    output_dir='output/',
    ocr_images=True
)
```

### Image Processing

```python
# Process single images just like any other document
loader = UnifiedDocumentLoader(
    ocr_provider='openai',
    model='gpt-4o-mini'  # Cost-effective for OCR
)

# Extract text from image
result = loader.load(
    'screenshot.png',
    extract_images=True,  # Include image data
    ocr_images=True       # Extract text via OCR
)

```

### Custom API Endpoints

```python
# Use OpenAI-compatible APIs (Ollama, Azure, etc.)
loader = UnifiedDocumentLoader(
    ocr_provider='openai',
    base_url='https://your-api.com/v1',
    api_key='your-api-key'
)
```

## 🔧 OCR Providers

### OpenAI GPT-4.1 (Recommended)

```python
# Full OpenAI configuration
loader = UnifiedDocumentLoader(
    ocr_provider='openai',
    api_key='your-openai-api-key',  # or set OPENAI_API_KEY env var
    model='gpt-4o',
    temperature=0,
    max_tokens=4096,
    max_workers=5,
    prompt_template=PromptTemplate.TABLE_FOCUSED,
    # Additional OpenAI parameters
    top_p=1.0,
    frequency_penalty=0.0,
    presence_penalty=0.0
)
```

### Tesseract (Offline)

```python
# Use Tesseract for offline processing
loader = UnifiedDocumentLoader(
    ocr_provider='tesseract'
)
```

## 📊 Advanced Batch Processing

### Process Entire Directories

```python
# Batch process with full configuration
results = loader.batch_process(
    input_dir='./documents',
    output_dir='./processed',
    output_format='markdown',
    extract_images=True,
    ocr_images=True,
    recursive=True,
    show_progress=True,
    save_files=True
)

# Check results
for file_path, result in results.items():
    if result['status'] == 'success':
        print(f"✅ {file_path}: {result['content_length']} chars")
    else:
        print(f"❌ {file_path}: {result['error']}")
```

### Process Specific Files

```python
# Process a list of specific files
files = ['report.pdf', 'data.xlsx', 'presentation.pptx']
results = loader.batch_process_files(
    file_paths=files,
    output_dir='./output',
    extract_images=True,
    ocr_images=True,
    show_progress=True
)
```

### Using Convenience Functions

```python
from doc2mark import batch_process_documents, batch_process_files

# High-level batch processing
results = batch_process_documents(
    input_dir='./docs',
    output_format='json',
    ocr_provider='openai',
    extract_images=True,
    ocr_images=True
)
```

## 🎯 Specialized Prompt Templates

doc2mark includes 8 specialized prompt templates optimized for different content types:

```python
from doc2mark.ocr.prompts import PromptTemplate

# Available templates
templates = {
    PromptTemplate.DEFAULT: "General purpose text extraction",
    PromptTemplate.TABLE_FOCUSED: "Optimized for tabular data",
    PromptTemplate.DOCUMENT_FOCUSED: "Preserves document structure", 
    PromptTemplate.FORM_FOCUSED: "Extract form fields and values",
    PromptTemplate.RECEIPT_FOCUSED: "Invoices and receipts",
    PromptTemplate.HANDWRITING_FOCUSED: "Handwritten text",
    PromptTemplate.CODE_FOCUSED: "Source code and technical docs",
    PromptTemplate.MULTILINGUAL: "Non-English documents"
}

# Use specific template
loader = UnifiedDocumentLoader(
    prompt_template=PromptTemplate.TABLE_FOCUSED
)
```

## ⚙️ Dynamic Configuration

Update OCR settings without reinitializing:

```python
# Initial setup
loader = UnifiedDocumentLoader(ocr_provider='openai')

# Update configuration dynamically
loader.update_ocr_configuration(
    model='gpt-4o-mini',
    temperature=0.3,
    prompt_template='table_focused',
    max_workers=10
)

# Validate setup
validation = loader.validate_ocr_setup()
print(f"OCR Status: {'✅ Valid' if not validation['errors'] else '❌ Issues found'}")

# Get available templates
templates = loader.get_available_prompt_templates()
for name, description in templates.items():
    print(f"  {name}: {description}")
```

## 🔍 Output Formats

### Markdown (Default)

```python
result = loader.load('document.pdf')
print(result.content)
# Returns clean Markdown with preserved formatting
```

### JSON with Metadata

```python
from doc2mark import OutputFormat

result = loader.load('document.pdf', output_format=OutputFormat.JSON)
data = json.loads(result.content)
# Structured data with metadata
```

### Plain Text

```python
result = loader.load('document.pdf', output_format=OutputFormat.TEXT)
# Clean text without formatting
```

## 🌍 Language Support

Automatic language detection and preservation:

```python
# Multilingual documents
result = loader.load(
    'chinese_document.pdf',
    prompt_template=PromptTemplate.MULTILINGUAL
)

# The output preserves the original language
```

## 🛠️ Advanced Features

### MIME Type Detection and Support

doc2mark includes a comprehensive MIME type mapper for detecting and handling document formats:

```python
from doc2mark.core import MimeTypeMapper, check_mime_support, DocumentFormat

# Create a mapper instance
mapper = MimeTypeMapper()

# Check if a MIME type is supported and get its format
supported, doc_format = mapper.check_support('application/pdf')
print(f"PDF supported: {supported}, Format: {doc_format}")
# Output: PDF supported: True, Format: DocumentFormat.PDF

# Check multiple MIME types
mime_types = [
    'application/pdf',
    'text/csv',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/unknown'
]

for mime_type in mime_types:
    supported, fmt = mapper.check_support(mime_type)
    if supported:
        print(f"✅ {mime_type} -> {fmt}")
    else:
        print(f"❌ {mime_type} -> Not supported")

# Using convenience function
from doc2mark.core import check_mime_support

supported, fmt = check_mime_support('text/plain')
if supported:
    print(f"Can process text files as: {fmt}")
```

#### MIME Type Features

```python
# Get MIME type for a format
mime = mapper.get_mime_from_format(DocumentFormat.PDF)
print(f"PDF MIME type: {mime}")

# Get all MIME types for a format
all_mimes = mapper.get_mime_from_format(DocumentFormat.PDF, primary_only=False)
print(f"All PDF MIME types: {all_mimes}")

# Detect format from file (uses MIME type detection)
from doc2mark.core import detect_format_from_file

doc_format = detect_format_from_file('document.pdf')
print(f"Detected format: {doc_format}")

# Register custom MIME types
mapper.register_mime_type('application/x-custom-doc', DocumentFormat.DOCX)
supported, fmt = mapper.check_support('application/x-custom-doc')
print(f"Custom MIME registered: {supported}")

# Get detailed MIME information
info = mapper.get_mime_info('application/pdf')
print(f"MIME Info: {info}")
# Output: {'mime_type': 'application/pdf', 'supported': True, 
#          'format': DocumentFormat.PDF, 'extensions': ['.pdf'], ...}

# Suggest format for unknown MIME types
suggested = mapper.suggest_format('text/x-custom-script')
print(f"Suggested format for custom script: {suggested}")
# Output: DocumentFormat.TXT
```

#### Integration with Document Loader

The UnifiedDocumentLoader automatically uses MIME type detection for better format recognition:

```python
from doc2mark import UnifiedDocumentLoader

loader = UnifiedDocumentLoader(ocr_provider='openai')

# The loader automatically detects format using MIME types
# This is especially useful for files with incorrect or missing extensions
result = loader.load('document_without_extension')

# Format detection order:
# 1. MIME type detection (if enabled)
# 2. File extension matching
# 3. Special cases (e.g., .markdown -> .md)
```

### Image Extraction and OCR

```python
# Extract images without OCR
result = loader.load(
    'document.pdf',
    extract_images=True,
    ocr_images=False  # Keep as base64 data
)

# Extract images with OCR processing
result = loader.load(
    'document.pdf', 
    extract_images=True,
    ocr_images=True  # Convert images to text descriptions
)

# Access extracted images
if result.images:
    print(f"Extracted {len(result.images)} images")
```

### Progress Tracking

```python
# Show detailed progress during processing
result = loader.load(
    'large_document.pdf',
    show_progress=True
)

# Batch processing with progress
results = loader.batch_process(
    'documents/',
    show_progress=True
)
```

### Caching

```python
# Enable caching for repeated processing
loader = UnifiedDocumentLoader(
    cache_dir='./cache'
)

# Subsequent calls to the same file will use cached results
```

### Error Handling

```python
from doc2mark.core.base import ProcessingError, UnsupportedFormatError

try:
    result = loader.load('document.pdf')
except UnsupportedFormatError as e:
    print(f"Format not supported: {e}")
except ProcessingError as e:
    print(f"Processing failed: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

## 📊 Integration Examples

### RAG Pipeline Integration

```python
from doc2mark import UnifiedDocumentLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter

# Process documents for RAG
loader = UnifiedDocumentLoader(
    prompt_template=PromptTemplate.DOCUMENT_FOCUSED
)

documents = ['report.pdf', 'data.xlsx', 'analysis.docx']
texts = []

for doc in documents:
    result = loader.load(doc, extract_images=True, ocr_images=True)
    texts.append(result.content)

# Split for vector database
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000)
chunks = text_splitter.create_documents(texts)
```

### Automated Document Processing Pipeline

```python
import os
from pathlib import Path

def process_document_pipeline(input_dir, output_dir):
    """Complete document processing pipeline."""
    
    loader = UnifiedDocumentLoader(
        ocr_provider='openai',
        model='gpt-4o',
        prompt_template=PromptTemplate.DOCUMENT_FOCUSED
    )
    
    # Validate OCR setup
    validation = loader.validate_ocr_setup()
    if validation['errors']:
        raise RuntimeError(f"OCR setup issues: {validation['errors']}")
    
    # Process all documents
    results = loader.batch_process(
        input_dir=input_dir,
        output_dir=output_dir,
        extract_images=True,
        ocr_images=True,
        show_progress=True,
        save_files=True
    )
    
    # Generate summary report
    successful = sum(1 for r in results.values() if r['status'] == 'success')
    failed = len(results) - successful
    
    print(f"📊 Processing Complete:")
    print(f"   ✅ Successful: {successful}")
    print(f"   ❌ Failed: {failed}")
    
    return results

# Usage
results = process_document_pipeline('./input_docs', './processed_docs')
```

## 🔧 Configuration Reference

### UnifiedDocumentLoader Parameters

```python
loader = UnifiedDocumentLoader(
    # OCR Provider
    ocr_provider='openai',  # 'openai' or 'tesseract'
    api_key=None,  # Auto-detects from OPENAI_API_KEY env var
    
    # OpenAI Model Configuration
    model='gpt-4o',  # OpenAI model to use
    temperature=0.0,  # Response randomness (0.0-2.0)
    max_tokens=4096,  # Maximum response length
    max_workers=5,  # Concurrent processing workers
    timeout=30,  # Request timeout in seconds
    max_retries=3,  # Retry attempts for failed requests
    
    # Advanced OpenAI Parameters
    top_p=1.0,  # Nucleus sampling parameter
    frequency_penalty=0.0,  # Reduce repetition (-2.0 to 2.0)
    presence_penalty=0.0,  # Encourage new topics (-2.0 to 2.0)
    
    # Prompt Configuration
    prompt_template=PromptTemplate.DEFAULT,  # Specialized prompt
    default_prompt=None,  # Custom prompt override
    
    # System Configuration
    cache_dir=None,  # Enable caching
    ocr_config=None  # Additional OCR configuration
)
```

### Processing Parameters

```python
result = loader.load(
    file_path='document.pdf',
    output_format=OutputFormat.MARKDOWN,  # Output format
    extract_images=False,  # Extract images from document
    ocr_images=False,  # Perform OCR on extracted images
    show_progress=False,  # Show processing progress
    encoding='utf-8',  # Text file encoding
    delimiter=None  # CSV delimiter (auto-detect if None)
)
```

## 📝 Requirements

- **Python**: 3.8+
- **Required**: `pathlib`, `logging`, `typing`
- **OCR (OpenAI)**: `openai`, `langchain`, `langchain-openai`
- **OCR (Tesseract)**: `pytesseract`, `Pillow`
- **Office Formats**: `python-docx`, `openpyxl`, `python-pptx`
- **PDF**: `PyMuPDF`
- **MIME Detection** (optional): `python-magic`, `python-magic-bin` (Windows)
- **Legacy Formats**: LibreOffice (system dependency)

## 🚀 Performance Tips

1. **Use appropriate prompt templates** for your content type
2. **Enable caching** for repeated processing of the same files
3. **Adjust max_workers** based on your system and API limits
4. **Use batch processing** for multiple files to leverage parallel processing
5. **Set appropriate timeouts** for large documents

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙋‍♂️ Support

- **Issues**: [GitHub Issues](https://github.com/luisleo526/doc2mark/issues)
- **Email**: <luisleo52655@gmail.com>
- **Documentation**: See inline docstrings and examples above

## 🔄 Recent Updates

- ✅ **NEW**: MIME type mapper for advanced format detection and support checking
- ✅ **NEW**: `check_support()` method to verify MIME type support with format detection
- ✅ **NEW**: Custom MIME type registration for proprietary formats
- ✅ Enhanced OCR configuration with 8 specialized prompt templates
- ✅ Advanced batch processing with progress tracking and error handling
- ✅ Dynamic configuration updates without reinitialization
- ✅ Comprehensive validation and setup checking
- ✅ Support for both OpenAI GPT-4o and Tesseract OCR
- ✅ Improved caching and performance optimizations
- ✅ Better error handling and logging

## ⚠️ Current Limitations

- Legacy formats (DOC, XLS, PPT) require LibreOffice installation
- Large files may require adjusted timeout settings
- OpenAI OCR requires API key and internet connection
- Batch processing performance depends on OCR provider rate limits
