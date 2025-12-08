"""Setup script for doc2mark package."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="doc2mark",
    version="0.4.3",
    author="HaoLiangWen",
    author_email="luisleo52655@gmail.com",
    description="AI-powered universal document processor with GPT-4V OCR",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/luisleo526/doc2mark",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Text Processing :: General",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.8",
    install_requires=[
        # Core dependencies
        "pymupdf>=1.23.0",  # PDF processing
        "python-docx>=0.8.11",  # DOCX processing
        "openpyxl>=3.0.0",  # XLSX processing
        "python-pptx>=0.6.21",  # PPTX processing
        "beautifulsoup4>=4.10.0",  # HTML/XML parsing

        # Optional but recommended
        "openai>=1.0.0",  # For GPT-4V OCR
        "pillow>=9.0.0",  # Image processing
        "pdfplumber>=0.9.0",  # Better PDF table extraction
        "markdownify>=0.11.0",  # HTML to Markdown conversion
        "markdown>=3.3.0",  # Markdown parsing
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "black>=22.0.0",
            "ruff>=0.0.260",
            "mypy>=1.0.0",
            "pre-commit>=3.0.0",
        ],
        "ocr": [
            "pytesseract>=0.3.10",  # Tesseract OCR
        ],
        "all": [
            "pytesseract>=0.3.10",
            "pyyaml>=6.0",  # For markdown frontmatter
        ]
    },
    entry_points={
        "console_scripts": [
            "doc2mark=doc2mark.cli:main",
        ],
    },
)
