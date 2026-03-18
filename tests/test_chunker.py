"""Tests for the section-aware chunker."""

import os
import pytest

from doc2mark.core.chunker import Chunk, ChunkingConfig, chunk_content


# ---------------------------------------------------------------------------
# Unit tests — synthetic json_content
# ---------------------------------------------------------------------------

class TestChunkerBasics:
    """Basic chunker behavior."""

    def test_empty_content(self):
        assert chunk_content([]) == []

    def test_none_config_uses_defaults(self):
        items = [{"type": "text:normal", "content": "hello"}]
        chunks = chunk_content(items, None)
        assert len(chunks) == 1
        assert chunks[0].content == "hello"

    def test_single_paragraph(self):
        items = [{"type": "text:normal", "content": "A single paragraph."}]
        chunks = chunk_content(items)
        assert len(chunks) == 1
        assert chunks[0].chunk_index == 0
        assert chunks[0].content == "A single paragraph."

    def test_chunk_index_sequential(self):
        items = [
            {"type": "text:title", "content": "Title"},
            {"type": "text:normal", "content": "Body"},
            {"type": "text:section", "content": "Section"},
            {"type": "text:normal", "content": "More body"},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=10000))
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i


class TestSectionSplitting:
    """Splitting at heading boundaries."""

    def test_split_on_title(self):
        items = [
            {"type": "text:title", "content": "First"},
            {"type": "text:normal", "content": "Body 1"},
            {"type": "text:title", "content": "Second"},
            {"type": "text:normal", "content": "Body 2"},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=10000))
        assert len(chunks) == 2
        assert chunks[0].section_title == "First"
        assert chunks[1].section_title == "Second"

    def test_split_on_section(self):
        items = [
            {"type": "text:section", "content": "Sec A"},
            {"type": "text:normal", "content": "Body A"},
            {"type": "text:section", "content": "Sec B"},
            {"type": "text:normal", "content": "Body B"},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=10000))
        assert len(chunks) == 2

    def test_preamble_without_heading(self):
        items = [
            {"type": "text:normal", "content": "Preamble text"},
            {"type": "text:title", "content": "Title"},
            {"type": "text:normal", "content": "Body"},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=10000))
        assert len(chunks) == 2
        assert chunks[0].section_title is None
        assert "Preamble" in chunks[0].content

    def test_section_hierarchy(self):
        items = [
            {"type": "text:title", "content": "Doc Title"},
            {"type": "text:normal", "content": "Intro"},
            {"type": "text:section", "content": "Chapter 1"},
            {"type": "text:normal", "content": "Chapter body"},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=10000))
        assert len(chunks) == 2
        assert chunks[0].section_hierarchy == ["Doc Title"]
        assert chunks[1].section_hierarchy == ["Doc Title", "Chapter 1"]

    def test_no_split_when_level_below_threshold(self):
        """With split_on_heading_level=1, text:section should NOT split."""
        items = [
            {"type": "text:title", "content": "Title"},
            {"type": "text:normal", "content": "Body"},
            {"type": "text:section", "content": "Sub"},
            {"type": "text:normal", "content": "Sub body"},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=10000, split_on_heading_level=1))
        assert len(chunks) == 1


class TestSizeSplitting:
    """Splitting large sections at item boundaries."""

    def test_splits_large_section(self):
        items = [
            {"type": "text:normal", "content": "A" * 800},
            {"type": "text:normal", "content": "B" * 800},
            {"type": "text:normal", "content": "C" * 800},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=1000, overlap=0))
        assert len(chunks) >= 2

    def test_table_kept_whole(self):
        big_table = "<table>" + "<tr><td>x</td></tr>" * 100 + "</table>"
        items = [
            {"type": "text:normal", "content": "Intro"},
            {"type": "table", "content": big_table},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=100, overlap=0, keep_tables_whole=True))
        # Table should appear complete in one chunk (may exceed max)
        table_chunks = [c for c in chunks if "<table>" in c.content]
        assert len(table_chunks) == 1
        assert big_table in table_chunks[0].content


class TestOverlap:
    """Overlap between chunks."""

    def test_overlap_present(self):
        items = [
            {"type": "text:normal", "content": "First paragraph with lots of text here."},
            {"type": "text:normal", "content": "Second paragraph with different text."},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=50, overlap=20))
        if len(chunks) >= 2:
            # Second chunk should contain some text from the end of the first
            assert len(chunks[1].content) > len("Second paragraph with different text.")

    def test_no_overlap_when_zero(self):
        items = [
            {"type": "text:normal", "content": "A" * 500},
            {"type": "text:normal", "content": "B" * 500},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=600, overlap=0))
        if len(chunks) >= 2:
            assert chunks[1].content.startswith("B")


class TestPageMetadata:
    """Page tracking in chunks."""

    def test_page_start_end(self):
        items = [
            {"type": "text:normal", "content": "Page 1 text", "page": 1},
            {"type": "text:normal", "content": "Page 2 text", "page": 2},
            {"type": "text:normal", "content": "Page 3 text", "page": 3},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=10000))
        assert chunks[0].page_start == 1
        assert chunks[0].page_end == 3

    def test_missing_page_field(self):
        items = [
            {"type": "text:normal", "content": "No page field"},
        ]
        chunks = chunk_content(items)
        assert chunks[0].page_start is None
        assert chunks[0].page_end is None


class TestContentTypes:
    """Content type tracking."""

    def test_content_types_tracked(self):
        items = [
            {"type": "text:title", "content": "Title"},
            {"type": "text:normal", "content": "Body"},
            {"type": "table", "content": "| a | b |"},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=10000))
        assert "text:title" in chunks[0].content_types
        assert "text:normal" in chunks[0].content_types
        assert "table" in chunks[0].content_types


class TestHeaderFooterSkipped:
    """Header/footer content should be skipped in chunks."""

    def test_headers_produce_empty_text(self):
        items = [
            {"type": "text:header", "content": "Company Name"},
            {"type": "text:normal", "content": "Real content"},
            {"type": "text:footer", "content": "Page 1"},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=10000))
        assert len(chunks) == 1
        assert "Company Name" not in chunks[0].content
        assert "Real content" in chunks[0].content
        assert "Page 1" not in chunks[0].content


# ---------------------------------------------------------------------------
# Integration tests — real documents
# ---------------------------------------------------------------------------

def _load_doc(path):
    """Load a document without OCR."""
    old_key = os.environ.get("OPENAI_API_KEY")
    if not old_key:
        os.environ["OPENAI_API_KEY"] = "sk-test-dummy-not-used"
    try:
        from doc2mark import UnifiedDocumentLoader
        loader = UnifiedDocumentLoader()
        return loader.load(str(path), extract_images=False, ocr_images=False)
    finally:
        if not old_key:
            del os.environ["OPENAI_API_KEY"]


class TestChunkerIntegration:
    """End-to-end chunking with real documents."""

    @pytest.fixture
    def sample_pdf(self, sample_documents_dir):
        path = sample_documents_dir / "sample_pdf.pdf"
        if not path.exists():
            pytest.skip("sample_pdf.pdf not found")
        return path

    @pytest.fixture
    def sample_docx(self, sample_documents_dir):
        path = sample_documents_dir / "sample_document.docx"
        if not path.exists():
            pytest.skip("sample_document.docx not found")
        return path

    def test_pdf_get_chunks(self, sample_pdf):
        result = _load_doc(sample_pdf)
        chunks = result.get_chunks()
        assert len(chunks) >= 1
        for chunk in chunks:
            assert isinstance(chunk, Chunk)
            assert chunk.content

    def test_docx_get_chunks(self, sample_docx):
        result = _load_doc(sample_docx)
        chunks = result.get_chunks()
        assert len(chunks) >= 1

    def test_custom_config(self, sample_pdf):
        result = _load_doc(sample_pdf)
        cfg = ChunkingConfig(max_chunk_size=500, overlap=50)
        chunks = result.get_chunks(cfg)
        assert len(chunks) >= 1

    def test_get_chunks_without_json_content(self):
        """get_chunks should return a single chunk wrapping raw content."""
        from doc2mark.core.base import ProcessedDocument, DocumentMetadata, DocumentFormat
        doc = ProcessedDocument(
            content="plain text",
            metadata=DocumentMetadata(filename="test.txt", format=DocumentFormat.TXT, size_bytes=10),
        )
        chunks = doc.get_chunks()
        assert len(chunks) == 1
        assert chunks[0].content == "plain text"
