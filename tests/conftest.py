"""Pytest configuration and fixtures."""

import pytest
from pathlib import Path
import tempfile
import shutil


@pytest.fixture(scope="session")
def sample_documents_dir():
    """Return the path to sample documents directory."""
    return Path(__file__).parent.parent / "sample_documents"


@pytest.fixture(scope="session")
def temp_output_dir():
    """Create a temporary directory for test outputs."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    # Cleanup after tests
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def sample_text_content():
    """Return sample text content for testing."""
    return """This is a sample text document.
It contains multiple lines.
And some basic formatting.

This is another paragraph.
"""


@pytest.fixture
def sample_csv_content():
    """Return sample CSV content for testing."""
    return """Name,Age,City
John Doe,30,New York
Jane Smith,25,Los Angeles
Bob Johnson,35,Chicago
"""


@pytest.fixture
def sample_json_content():
    """Return sample JSON content for testing."""
    return """{
    "name": "Test Document",
    "type": "sample",
    "data": [
        {"id": 1, "value": "first"},
        {"id": 2, "value": "second"}
    ]
}"""


@pytest.fixture
def sample_markdown_content():
    """Return sample Markdown content for testing."""
    return """# Sample Markdown Document

## Introduction

This is a **sample** markdown document with various formatting.

### Features

- Bullet point 1
- Bullet point 2
- Bullet point 3

### Code Example

```python
def hello_world():
    print("Hello, World!")
```

### Table

| Column 1 | Column 2 | Column 3 |
|----------|----------|----------|
| Data 1   | Data 2   | Data 3   |
| Data 4   | Data 5   | Data 6   |
"""


@pytest.fixture
def temp_text_file(tmp_path, sample_text_content):
    """Create a temporary text file."""
    file_path = tmp_path / "test.txt"
    file_path.write_text(sample_text_content)
    return file_path


@pytest.fixture
def temp_csv_file(tmp_path, sample_csv_content):
    """Create a temporary CSV file."""
    file_path = tmp_path / "test.csv"
    file_path.write_text(sample_csv_content)
    return file_path


@pytest.fixture
def temp_json_file(tmp_path, sample_json_content):
    """Create a temporary JSON file."""
    file_path = tmp_path / "test.json"
    file_path.write_text(sample_json_content)
    return file_path


@pytest.fixture
def temp_markdown_file(tmp_path, sample_markdown_content):
    """Create a temporary Markdown file."""
    file_path = tmp_path / "test.md"
    file_path.write_text(sample_markdown_content)
    return file_path
