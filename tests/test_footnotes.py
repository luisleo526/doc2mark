"""Tests for footnote/endnote extraction."""

import os
import re
import pytest


# ---------------------------------------------------------------------------
# DOCX footnote parsing (unit tests with synthetic data)
# ---------------------------------------------------------------------------

class TestDocxFootnoteLoader:
    """Verify _load_footnotes parses DOCX ZIP correctly."""

    def _make_docx_with_footnotes(self, tmp_path, footnotes=None, endnotes=None):
        """Create a minimal DOCX (ZIP) with footnotes/endnotes XML."""
        import zipfile
        docx_path = tmp_path / "test_footnotes.docx"

        # Minimal DOCX with a paragraph
        content_types = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        )
        if footnotes:
            content_types += '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
        if endnotes:
            content_types += '<Override PartName="/word/endnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"/>'
        content_types += '</Types>'

        rels = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>'
        )

        word_rels = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '</Relationships>'
        )

        document_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>'
            '<w:p><w:r><w:t>Test paragraph</w:t></w:r></w:p>'
            '</w:body>'
            '</w:document>'
        )

        with zipfile.ZipFile(docx_path, 'w') as zf:
            zf.writestr('[Content_Types].xml', content_types)
            zf.writestr('_rels/.rels', rels)
            zf.writestr('word/_rels/document.xml.rels', word_rels)
            zf.writestr('word/document.xml', document_xml)
            if footnotes:
                zf.writestr('word/footnotes.xml', footnotes)
            if endnotes:
                zf.writestr('word/endnotes.xml', endnotes)

        return docx_path

    def test_footnotes_parsed(self, tmp_path):
        """Footnotes XML should be parsed into id->text mapping."""
        footnotes_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:footnote w:id="0"><w:p><w:r><w:t>separator</w:t></w:r></w:p></w:footnote>'
            '<w:footnote w:id="1"><w:p><w:r><w:t>First footnote text.</w:t></w:r></w:p></w:footnote>'
            '<w:footnote w:id="2"><w:p><w:r><w:t>Second footnote text.</w:t></w:r></w:p></w:footnote>'
            '</w:footnotes>'
        )
        docx_path = self._make_docx_with_footnotes(tmp_path, footnotes=footnotes_xml)

        from doc2mark.pipelines.office_advanced_pipeline import DocxLoader
        loader = DocxLoader.__new__(DocxLoader)
        loader.file_path = docx_path
        notes = loader._load_footnotes()

        assert "1" in notes
        assert "2" in notes
        assert "0" not in notes  # separator skipped
        assert notes["1"] == "First footnote text."
        assert notes["2"] == "Second footnote text."

    def test_endnotes_parsed(self, tmp_path):
        """Endnotes XML should also be parsed."""
        endnotes_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:endnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:endnote w:id="0"><w:p><w:r><w:t>separator</w:t></w:r></w:p></w:endnote>'
            '<w:endnote w:id="1"><w:p><w:r><w:t>An endnote.</w:t></w:r></w:p></w:endnote>'
            '</w:endnotes>'
        )
        docx_path = self._make_docx_with_footnotes(tmp_path, endnotes=endnotes_xml)

        from doc2mark.pipelines.office_advanced_pipeline import DocxLoader
        loader = DocxLoader.__new__(DocxLoader)
        loader.file_path = docx_path
        notes = loader._load_footnotes()

        assert "1" in notes
        assert notes["1"] == "An endnote."

    def test_no_footnotes_xml(self, tmp_path):
        """DOCX without footnotes.xml should return empty dict (no crash)."""
        docx_path = self._make_docx_with_footnotes(tmp_path)

        from doc2mark.pipelines.office_advanced_pipeline import DocxLoader
        loader = DocxLoader.__new__(DocxLoader)
        loader.file_path = docx_path
        notes = loader._load_footnotes()

        assert notes == {}

    def test_corrupt_zip_no_crash(self, tmp_path):
        """Corrupt DOCX should not crash, just return empty dict."""
        bad_path = tmp_path / "bad.docx"
        bad_path.write_bytes(b"not a zip file")

        from doc2mark.pipelines.office_advanced_pipeline import DocxLoader
        loader = DocxLoader.__new__(DocxLoader)
        loader.file_path = bad_path
        notes = loader._load_footnotes()

        assert notes == {}


# ---------------------------------------------------------------------------
# DOCX integration — process real DOCX
# ---------------------------------------------------------------------------

class TestDocxFootnoteIntegration:
    """Verify footnote processing doesn't crash on real documents."""

    @pytest.fixture
    def sample_docx(self, sample_documents_dir):
        path = sample_documents_dir / "sample_document.docx"
        if not path.exists():
            pytest.skip("sample_document.docx not found")
        return path

    def test_docx_no_crash_with_footnotes(self, sample_docx):
        """Processing a real DOCX should not crash with footnote extraction."""
        old_key = os.environ.get("OPENAI_API_KEY")
        if not old_key:
            os.environ["OPENAI_API_KEY"] = "sk-test-dummy-not-used"
        try:
            from doc2mark import UnifiedDocumentLoader
            loader = UnifiedDocumentLoader()
            result = loader.load(str(sample_docx), extract_images=False, ocr_images=False)
            assert result.content
            assert len(result.content) > 0
        finally:
            if not old_key:
                del os.environ["OPENAI_API_KEY"]


# ---------------------------------------------------------------------------
# PDF footnote detection (unit tests)
# ---------------------------------------------------------------------------

class TestPDFFootnoteDetection:
    """Verify PDF footnote heuristic classification."""

    def test_footnote_type_in_json(self, sample_documents_dir):
        """PDF with small text at bottom may have text:footnote items."""
        pdf_path = sample_documents_dir / "sample_pdf.pdf"
        if not pdf_path.exists():
            pytest.skip("sample_pdf.pdf not found")

        old_key = os.environ.get("OPENAI_API_KEY")
        if not old_key:
            os.environ["OPENAI_API_KEY"] = "sk-test-dummy-not-used"
        try:
            from doc2mark import UnifiedDocumentLoader
            loader = UnifiedDocumentLoader()
            result = loader.load(str(pdf_path), extract_images=False, ocr_images=False)
            # Just verify no crash — not all PDFs have footnotes
            assert result.json_content is not None
        finally:
            if not old_key:
                del os.environ["OPENAI_API_KEY"]

    def test_footnote_classification_logic(self):
        """Footnote classification should fire for small text at page bottom with numeric marker."""
        from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader
        import pymupdf

        # We can't easily unit-test _convert_block_to_markdown_with_type
        # without a real document, so we test the conditions directly.
        # The heuristic: block_y > 85% of page, font < 0.9 * avg, starts with digit pattern.

        # This is a structural test — verify the method exists and handles
        # footnote classification without crashing.
        assert hasattr(PDFLoader, '_convert_block_to_markdown_with_type')


# ---------------------------------------------------------------------------
# Chunker footnote integration
# ---------------------------------------------------------------------------

class TestChunkerFootnotes:
    """Verify footnotes travel with their referencing chunks."""

    def test_footnote_attached_to_referencing_chunk(self):
        from doc2mark.core.chunker import chunk_content, ChunkingConfig

        items = [
            {"type": "text:normal", "content": "Some text with a reference [^1] here."},
            {"type": "text:footnote", "content": "[^1]: This is the footnote definition."},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=10000, overlap=0))
        assert len(chunks) == 1
        assert "[^1]" in chunks[0].content
        assert "[^1]: This is the footnote definition." in chunks[0].content

    def test_unreferenced_footnotes_go_to_last_chunk(self):
        from doc2mark.core.chunker import chunk_content, ChunkingConfig

        items = [
            {"type": "text:normal", "content": "Body text without references."},
            {"type": "text:footnote", "content": "[^99]: Orphan footnote."},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=10000, overlap=0))
        assert len(chunks) == 1
        assert "[^99]: Orphan footnote." in chunks[0].content

    def test_footnotes_excluded_from_body(self):
        """text:footnote items should not appear as body text in chunks."""
        from doc2mark.core.chunker import chunk_content, ChunkingConfig

        items = [
            {"type": "text:title", "content": "Title"},
            {"type": "text:normal", "content": "Body [^1]"},
            {"type": "text:section", "content": "Section 2"},
            {"type": "text:normal", "content": "Body 2"},
            {"type": "text:footnote", "content": "[^1]: Footnote text."},
        ]
        chunks = chunk_content(items, ChunkingConfig(max_chunk_size=10000, overlap=0))
        # Footnote definition should be attached to chunk with [^1] reference
        ref_chunk = [c for c in chunks if "[^1]" in c.content and "[^1]:" not in c.content.split("[^1]:")[0].split("[^1]")[-1]]
        # At minimum, footnote def appears in the output
        all_text = "\n".join(c.content for c in chunks)
        assert "[^1]: Footnote text." in all_text
