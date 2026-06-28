"""Robustness and edge-case characterization tests for doc2mark.

These tests lock the **current** behaviour of the loader against various
boundary inputs so that future refactors cannot silently change error
handling or graceful-degradation paths.

Every assertion was derived by running the code and observing what it
actually produces -- NOT by asserting aspirational behaviour.
"""

import os
import pytest
from pathlib import Path

from doc2mark import UnifiedDocumentLoader
from doc2mark.core.base import (
    ProcessingError,
    UnsupportedFormatError,
    ProcessedDocument,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _loader(**kwargs):
    """Create a loader with OCR disabled unless the caller overrides."""
    kwargs.setdefault("ocr_provider", None)
    return UnifiedDocumentLoader(**kwargs)


# ===========================================================================
# 1. Empty files with known extensions
# ===========================================================================

class TestEmptyFiles:
    """Loading a zero-byte file with a recognised extension."""

    def test_empty_txt_returns_empty_content(self, tmp_path):
        """An empty .txt file should load successfully with empty content."""
        p = tmp_path / "empty.txt"
        p.write_text("")
        loader = _loader()
        result = loader.load(p)

        assert isinstance(result, ProcessedDocument)
        assert result.content == ""
        assert result.metadata.word_count == 0
        # A completely empty string split on newline yields [''], i.e. 1 line.
        assert result.metadata.line_count == 1
        assert result.metadata.size_bytes == 0

    def test_empty_csv_returns_empty_content(self, tmp_path):
        """An empty .csv file should load successfully with empty content."""
        p = tmp_path / "empty.csv"
        p.write_text("")
        loader = _loader()
        result = loader.load(p)

        assert isinstance(result, ProcessedDocument)
        assert result.content == ""

    def test_empty_json_raises_processing_error(self, tmp_path):
        """An empty .json file should raise ProcessingError (invalid JSON)."""
        p = tmp_path / "empty.json"
        p.write_text("")
        loader = _loader()

        with pytest.raises(ProcessingError, match="JSON processing failed"):
            loader.load(p)

    def test_empty_docx_raises_processing_error(self, tmp_path):
        """A zero-byte .docx is not a valid zip archive."""
        p = tmp_path / "empty.docx"
        p.write_bytes(b"")
        loader = _loader()

        with pytest.raises(ProcessingError, match="DOCX processing failed"):
            loader.load(p)

    def test_empty_pdf_raises_processing_error(self, tmp_path):
        """A zero-byte .pdf cannot be opened by pdfium/PyMuPDF."""
        p = tmp_path / "empty.pdf"
        p.write_bytes(b"")
        loader = _loader()

        with pytest.raises(ProcessingError, match="PDF processing failed"):
            loader.load(p)


# ===========================================================================
# 2. Corrupted / garbage files with valid extensions
# ===========================================================================

class TestCorruptedFiles:
    """Loading random bytes disguised as a known format."""

    _GARBAGE = b"\x00\x01\x02\x03\xde\xad\xbe\xef not a real file"

    def test_corrupted_docx_raises_processing_error(self, tmp_path):
        p = tmp_path / "garbage.docx"
        p.write_bytes(self._GARBAGE)
        loader = _loader()

        with pytest.raises(ProcessingError, match="DOCX processing failed"):
            loader.load(p)

    def test_corrupted_xlsx_raises_processing_error(self, tmp_path):
        p = tmp_path / "garbage.xlsx"
        p.write_bytes(self._GARBAGE)
        loader = _loader()

        with pytest.raises(ProcessingError, match="XLSX processing failed"):
            loader.load(p)

    def test_corrupted_pptx_raises_processing_error(self, tmp_path):
        p = tmp_path / "garbage.pptx"
        p.write_bytes(self._GARBAGE)
        loader = _loader()

        with pytest.raises(ProcessingError, match="PPTX processing failed"):
            loader.load(p)

    def test_corrupted_pdf_raises_processing_error(self, tmp_path):
        p = tmp_path / "garbage.pdf"
        p.write_bytes(b"%PDF-garbage not real pdf data")
        loader = _loader()

        with pytest.raises(ProcessingError, match="PDF processing failed"):
            loader.load(p)

    def test_binary_csv_raises_processing_error(self, tmp_path):
        """Binary data with a .csv extension fails on UTF-8 decode."""
        p = tmp_path / "garbage.csv"
        p.write_bytes(b"\x80\x81\x82\x83\xff\xfe\xfd")
        loader = _loader()

        with pytest.raises(ProcessingError, match="CSV processing failed"):
            loader.load(p)


# ===========================================================================
# 3. Non-UTF-8 text files
# ===========================================================================

class TestNonUtf8Text:
    """Text files encoded in cp1252 / latin-1."""

    # cp1252 bytes for "cafe naive resume" with accented characters
    _CP1252_TEXT = "caf\xe9 na\xefve r\xe9sum\xe9"
    _CP1252_BYTES = _CP1252_TEXT.encode("cp1252")

    # latin-1 bytes for German text with umlauts
    _LATIN1_TEXT = "Grüße aus Österreich"
    _LATIN1_BYTES = _LATIN1_TEXT.encode("latin-1")

    def test_cp1252_with_default_utf8_raises_processing_error(self, tmp_path):
        """cp1252 bytes are invalid UTF-8; the loader should raise."""
        p = tmp_path / "cp1252.txt"
        p.write_bytes(self._CP1252_BYTES)
        loader = _loader()

        with pytest.raises(ProcessingError, match="TXT processing failed"):
            loader.load(p)  # default encoding='utf-8'

    def test_cp1252_with_correct_encoding_succeeds(self, tmp_path):
        """When the correct encoding is specified, the content round-trips."""
        p = tmp_path / "cp1252.txt"
        p.write_bytes(self._CP1252_BYTES)
        loader = _loader()

        result = loader.load(p, encoding="cp1252")
        assert isinstance(result, ProcessedDocument)
        # The text processor wraps all-caps lines in ## headers, but these
        # words are lowercase so the content should come through directly.
        assert "caf\xe9" in result.content
        assert "r\xe9sum\xe9" in result.content

    def test_latin1_with_correct_encoding_succeeds(self, tmp_path):
        """latin-1 encoded German text loads correctly with encoding='latin-1'."""
        p = tmp_path / "latin1.txt"
        p.write_bytes(self._LATIN1_BYTES)
        loader = _loader()

        result = loader.load(p, encoding="latin-1")
        assert isinstance(result, ProcessedDocument)
        assert "Grüße" in result.content  # Gruesse
        assert "Österreich" in result.content   # Oesterreich


# ===========================================================================
# 4. BOM (Byte Order Mark) text files
# ===========================================================================

class TestBomText:
    """UTF-8 text files with a leading BOM (\xef\xbb\xbf)."""

    _BOM_BYTES = b"\xef\xbb\xbfHello BOM world"

    def test_bom_with_default_utf8_preserves_bom_char(self, tmp_path):
        """With encoding='utf-8' (the default), the BOM character is
        preserved in the output content as U+FEFF."""
        p = tmp_path / "bom.txt"
        p.write_bytes(self._BOM_BYTES)
        loader = _loader()

        result = loader.load(p)
        assert isinstance(result, ProcessedDocument)
        # The BOM appears as the zero-width no-break space at the start.
        assert result.content.startswith("﻿")
        assert "Hello BOM world" in result.content
        assert result.metadata.encoding == "utf-8"

    def test_bom_with_utf8sig_strips_bom(self, tmp_path):
        """With encoding='utf-8-sig', Python's codec strips the BOM."""
        p = tmp_path / "bom.txt"
        p.write_bytes(self._BOM_BYTES)
        loader = _loader()

        result = loader.load(p, encoding="utf-8-sig")
        assert isinstance(result, ProcessedDocument)
        assert not result.content.startswith("﻿")
        assert "Hello BOM world" in result.content
        assert result.metadata.encoding == "utf-8-sig"

    def test_bom_multiline(self, tmp_path):
        """BOM followed by multiple lines -- BOM only at start."""
        p = tmp_path / "bom_multi.txt"
        p.write_bytes(b"\xef\xbb\xbfLine1\nLine2\nLine3")
        loader = _loader()

        result = loader.load(p, encoding="utf-8-sig")
        assert "Line1" in result.content
        assert "Line2" in result.content
        assert "Line3" in result.content
        # BOM stripped by utf-8-sig
        assert "﻿" not in result.content


# ===========================================================================
# 5. Missing file (FileNotFoundError)
# ===========================================================================

class TestMissingFile:
    """Loading a path that does not exist."""

    def test_missing_txt_raises_file_not_found(self, tmp_path):
        p = tmp_path / "does_not_exist.txt"
        loader = _loader()

        with pytest.raises(FileNotFoundError, match="File not found"):
            loader.load(p)

    def test_missing_docx_raises_file_not_found(self, tmp_path):
        p = tmp_path / "does_not_exist.docx"
        loader = _loader()

        with pytest.raises(FileNotFoundError, match="File not found"):
            loader.load(p)

    def test_missing_pdf_raises_file_not_found(self, tmp_path):
        p = tmp_path / "does_not_exist.pdf"
        loader = _loader()

        with pytest.raises(FileNotFoundError, match="File not found"):
            loader.load(p)

    def test_missing_file_string_path(self):
        """String paths are also accepted and yield FileNotFoundError."""
        loader = _loader()

        with pytest.raises(FileNotFoundError, match="File not found"):
            loader.load("/tmp/nonexistent_edge_test_xxxx.txt")


# ===========================================================================
# 6. Unsupported extension (UnsupportedFormatError)
# ===========================================================================

class TestUnsupportedExtension:
    """Loading a file whose extension has no registered processor."""

    def test_unknown_extension_raises_unsupported_format(self, tmp_path):
        p = tmp_path / "data.xyz"
        p.write_text("some data")
        loader = _loader()

        with pytest.raises(UnsupportedFormatError, match="Cannot detect format"):
            loader.load(p)

    def test_no_extension_raises_unsupported_format(self, tmp_path):
        p = tmp_path / "Makefile"
        p.write_text("all: build")
        loader = _loader()

        with pytest.raises(UnsupportedFormatError, match="Cannot detect format"):
            loader.load(p)

    def test_dot_only_extension_raises_unsupported_format(self, tmp_path):
        p = tmp_path / "file.abc123"
        p.write_text("hello")
        loader = _loader()

        with pytest.raises(UnsupportedFormatError, match="Cannot detect format"):
            loader.load(p)


# ===========================================================================
# 7. Lazy-OCR-without-key: text processing must work with openai provider
#    but no API key set, as long as ocr_images=False.
# ===========================================================================

class TestLazyOcrWithoutKey:
    """The redesigned loader lazily initialises the VisionAgent only when
    OCR is actually requested.  Constructing
    ``UnifiedDocumentLoader(ocr_provider='openai')`` with NO API key and
    loading a TEXT document with ``ocr_images=False`` must therefore succeed.
    """

    def test_text_load_without_api_key_succeeds(self, tmp_path, monkeypatch):
        """Text-only path should work even though no OpenAI key is set."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        p = tmp_path / "sample.txt"
        p.write_text("Hello from text-only path")

        loader = UnifiedDocumentLoader(ocr_provider="openai")
        # The OCR object is created but the API key is None -- that is fine
        # as long as we never call batch_process_images.
        assert loader.ocr is not None
        assert loader.ocr.api_key is None

        result = loader.load(p, ocr_images=False)
        assert isinstance(result, ProcessedDocument)
        assert "Hello from text-only path" in result.content

    def test_csv_load_without_api_key_succeeds(self, tmp_path, monkeypatch):
        """CSV (another text format) should also work without OCR creds."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        p = tmp_path / "data.csv"
        p.write_text("a,b,c\n1,2,3\n")

        loader = UnifiedDocumentLoader(ocr_provider="openai")
        result = loader.load(p, ocr_images=False)
        assert isinstance(result, ProcessedDocument)
        assert "a" in result.content
        assert "1" in result.content

    def test_json_load_without_api_key_succeeds(self, tmp_path, monkeypatch):
        """JSON processing should also work without OCR creds."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        p = tmp_path / "data.json"
        p.write_text('{"key": "value"}')

        loader = UnifiedDocumentLoader(ocr_provider="openai")
        result = loader.load(p, ocr_images=False)
        assert isinstance(result, ProcessedDocument)
        assert "key" in result.content
        assert "value" in result.content

    def test_validate_ocr_returns_false_without_key(self, monkeypatch):
        """validate_ocr() should return False when no API key is configured."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        loader = UnifiedDocumentLoader(ocr_provider="openai")
        assert loader.validate_ocr() is False


# ===========================================================================
# 8. Miscellaneous robustness
# ===========================================================================

class TestMiscRobustness:
    """Additional edge-case scenarios."""

    def test_whitespace_only_txt(self, tmp_path):
        """A .txt file containing only whitespace loads with that content."""
        p = tmp_path / "spaces.txt"
        p.write_text("   \n\n   \t\n")
        loader = _loader()

        result = loader.load(p)
        assert isinstance(result, ProcessedDocument)
        # word_count should be 0 since split() on pure whitespace gives []
        assert result.metadata.word_count == 0

    def test_very_long_single_line_txt(self, tmp_path):
        """A single very long line should not crash."""
        p = tmp_path / "long.txt"
        content = "x" * 100_000
        p.write_text(content)
        loader = _loader()

        result = loader.load(p)
        assert isinstance(result, ProcessedDocument)
        assert len(result.content) >= 100_000

    def test_load_returns_metadata_format_for_txt(self, tmp_path):
        """Metadata should correctly reflect the document format."""
        p = tmp_path / "meta.txt"
        p.write_text("hello world")
        loader = _loader()

        result = loader.load(p)
        from doc2mark.core.base import DocumentFormat
        assert result.metadata.format == DocumentFormat.TXT
        assert result.metadata.filename == "meta.txt"

    def test_load_returns_metadata_format_for_csv(self, tmp_path):
        p = tmp_path / "meta.csv"
        p.write_text("col1,col2\nval1,val2\n")
        loader = _loader()

        result = loader.load(p)
        from doc2mark.core.base import DocumentFormat
        assert result.metadata.format == DocumentFormat.CSV
