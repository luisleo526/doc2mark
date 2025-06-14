"""Tests for text-based format processing."""

import pytest
import json

from doc2mark import UnifiedDocumentLoader
from doc2mark.core.base import OutputFormat, DocumentFormat, ProcessedDocument


class TestTextFormats:
    """Test text-based document formats."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test loader."""
        self.loader = UnifiedDocumentLoader(ocr_provider='tesseract')

    def test_txt_file_loading(self, temp_text_file):
        """Test loading a TXT file."""
        result = self.loader.load(temp_text_file)

        assert isinstance(result, ProcessedDocument)
        assert result.metadata.format == DocumentFormat.TXT
        assert "sample text document" in result.content
        assert result.metadata.filename == "test.txt"

    def test_txt_output_formats(self, temp_text_file):
        """Test different output formats for TXT files."""
        # Test Markdown output (default)
        result_md = self.loader.load(temp_text_file, output_format=OutputFormat.MARKDOWN)
        assert isinstance(result_md.content, str)
        assert len(result_md.content) > 0

        # Test JSON output
        result_json = self.loader.load(temp_text_file, output_format=OutputFormat.JSON)
        assert isinstance(result_json.content, str)
        json_data = json.loads(result_json.content)
        assert 'content' in json_data

        # Test TEXT output
        result_text = self.loader.load(temp_text_file, output_format=OutputFormat.TEXT)
        assert isinstance(result_text.content, str)
        assert "sample text document" in result_text.content

    def test_csv_file_loading(self, temp_csv_file):
        """Test loading a CSV file."""
        result = self.loader.load(temp_csv_file)

        assert isinstance(result, ProcessedDocument)
        assert result.metadata.format == DocumentFormat.CSV
        assert "Name" in result.content
        assert "John Doe" in result.content

    def test_csv_delimiter_detection(self, tmp_path):
        """Test CSV delimiter detection."""
        # Create CSV with semicolon delimiter
        csv_semicolon = tmp_path / "test_semicolon.csv"
        csv_semicolon.write_text("Name;Age;City\nJohn;30;NYC\n")

        result = self.loader.load(csv_semicolon)
        assert "Name" in result.content
        assert "John" in result.content

    def test_json_file_loading(self, temp_json_file):
        """Test loading a JSON file."""
        result = self.loader.load(temp_json_file)

        assert isinstance(result, ProcessedDocument)
        assert result.metadata.format == DocumentFormat.JSON
        assert "Test Document" in result.content
        assert "sample" in result.content

    def test_markdown_file_loading(self, temp_markdown_file):
        """Test loading a Markdown file."""
        result = self.loader.load(temp_markdown_file)

        assert isinstance(result, ProcessedDocument)
        assert result.metadata.format == DocumentFormat.MARKDOWN
        assert "Sample Markdown Document" in result.content
        assert "Introduction" in result.content

    def test_encoding_parameter(self, tmp_path):
        """Test encoding parameter for text files."""
        # Create file with specific encoding
        file_path = tmp_path / "test_utf8.txt"
        content = "Hello, 世界! Café résumé"
        file_path.write_text(content, encoding='utf-8')

        result = self.loader.load(file_path, encoding='utf-8')
        assert "世界" in result.content
        assert "Café" in result.content

    def test_jsonl_file_loading(self, tmp_path):
        """Test loading a JSONL file."""
        jsonl_file = tmp_path / "test.jsonl"
        jsonl_content = '{"id": 1, "name": "First"}\n{"id": 2, "name": "Second"}\n'
        jsonl_file.write_text(jsonl_content)

        result = self.loader.load(jsonl_file)

        assert isinstance(result, ProcessedDocument)
        assert result.metadata.format == DocumentFormat.JSONL
        assert "First" in result.content
        assert "Second" in result.content

    def test_html_file_loading(self, tmp_path):
        """Test loading an HTML file."""
        html_file = tmp_path / "test.html"
        html_content = """
        <html>
        <head><title>Test Page</title></head>
        <body>
            <h1>Hello World</h1>
            <p>This is a test paragraph.</p>
        </body>
        </html>
        """
        html_file.write_text(html_content)

        result = self.loader.load(html_file)

        assert isinstance(result, ProcessedDocument)
        assert result.metadata.format == DocumentFormat.HTML
        assert "Hello World" in result.content
        assert "test paragraph" in result.content

    def test_xml_file_loading(self, tmp_path):
        """Test loading an XML file."""
        xml_file = tmp_path / "test.xml"
        xml_content = """<?xml version="1.0"?>
        <root>
            <item id="1">
                <name>First Item</name>
                <value>100</value>
            </item>
            <item id="2">
                <name>Second Item</name>
                <value>200</value>
            </item>
        </root>
        """
        xml_file.write_text(xml_content)

        result = self.loader.load(xml_file)

        assert isinstance(result, ProcessedDocument)
        assert result.metadata.format == DocumentFormat.XML
        assert "First Item" in result.content
        assert "Second Item" in result.content

    def test_empty_file_handling(self, tmp_path):
        """Test handling of empty files."""
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")

        result = self.loader.load(empty_file)

        assert isinstance(result, ProcessedDocument)
        assert result.metadata.format == DocumentFormat.TXT
        assert result.content == "" or result.content == "\n"

    def test_large_file_handling(self, tmp_path):
        """Test handling of large text files."""
        large_file = tmp_path / "large.txt"
        # Create a file with 1000 lines
        content = "\n".join([f"Line {i}: " + "x" * 100 for i in range(1000)])
        large_file.write_text(content)

        result = self.loader.load(large_file)

        assert isinstance(result, ProcessedDocument)
        assert len(result.content) > 100000  # Should be quite large
        assert "Line 0:" in result.content
        assert "Line 999:" in result.content
