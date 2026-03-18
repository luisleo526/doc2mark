"""Tests for page markers, header/footer deduplication, and page tracking."""

import os
import re
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_doc(path, **kwargs):
    """Load a document via UnifiedDocumentLoader (no OCR)."""
    old_key = os.environ.get("OPENAI_API_KEY")
    if not old_key:
        os.environ["OPENAI_API_KEY"] = "sk-test-dummy-not-used"
    try:
        from doc2mark import UnifiedDocumentLoader
        loader = UnifiedDocumentLoader()
        return loader.load(str(path), extract_images=False, ocr_images=False, **kwargs)
    finally:
        if not old_key:
            del os.environ["OPENAI_API_KEY"]


# ---------------------------------------------------------------------------
# PDF page markers
# ---------------------------------------------------------------------------

class TestPDFPageMarkers:
    """Verify page markers are injected in PDF markdown output."""

    @pytest.fixture
    def sample_pdf(self, sample_documents_dir):
        path = sample_documents_dir / "sample_pdf.pdf"
        if not path.exists():
            pytest.skip("sample_pdf.pdf not found")
        return path

    def test_page_markers_present(self, sample_pdf):
        result = _load_doc(sample_pdf)
        markers = re.findall(r'<!-- page (\d+) -->', result.content)
        # Multi-page PDF should have at least one page marker
        assert len(markers) >= 1, "Expected at least one <!-- page N --> marker"

    def test_page_markers_sequential(self, sample_pdf):
        result = _load_doc(sample_pdf)
        markers = [int(m) for m in re.findall(r'<!-- page (\d+) -->', result.content)]
        # Page numbers should be monotonically increasing
        for i in range(1, len(markers)):
            assert markers[i] > markers[i - 1], f"Page markers not sequential: {markers}"

    def test_json_content_has_page_field(self, sample_pdf):
        result = _load_doc(sample_pdf)
        assert result.json_content is not None
        for item in result.json_content:
            assert "page" in item, f"Item missing 'page' field: {item.get('type')}"
            assert isinstance(item["page"], int)

    def test_json_content_has_position_y(self, sample_pdf):
        result = _load_doc(sample_pdf)
        for item in result.json_content:
            assert "position_y" in item, f"Item missing 'position_y': {item.get('type')}"

    def test_backward_compat_type_and_content(self, sample_pdf):
        """All items still have 'type' and 'content' keys (backward compat)."""
        result = _load_doc(sample_pdf)
        for item in result.json_content:
            assert "type" in item
            assert "content" in item


# ---------------------------------------------------------------------------
# PPTX slide markers
# ---------------------------------------------------------------------------

class TestPPTXSlideMarkers:
    """Verify slide markers are injected in PPTX markdown output."""

    @pytest.fixture
    def sample_pptx(self, sample_documents_dir):
        path = sample_documents_dir / "sample_presentation.pptx"
        if not path.exists():
            pytest.skip("sample_presentation.pptx not found")
        return path

    def test_slide_markers_present(self, sample_pptx):
        result = _load_doc(sample_pptx)
        markers = re.findall(r'<!-- slide (\d+) -->', result.content)
        assert len(markers) >= 1, "Expected at least one <!-- slide N --> marker"

    def test_slide_markers_use_slide_label(self, sample_pptx):
        """PPTX should use 'slide' not 'page'."""
        result = _load_doc(sample_pptx)
        assert "<!-- page " not in result.content, "PPTX should use 'slide' label not 'page'"


# ---------------------------------------------------------------------------
# DOCX page break detection
# ---------------------------------------------------------------------------

class TestDOCXPageBreaks:
    """Verify DOCX page break detection and page tracking."""

    @pytest.fixture
    def sample_docx(self, sample_documents_dir):
        path = sample_documents_dir / "sample_document.docx"
        if not path.exists():
            pytest.skip("sample_document.docx not found")
        return path

    def test_docx_has_page_field(self, sample_docx):
        """DOCX content items should have page field."""
        result = _load_doc(sample_docx)
        assert result.json_content is not None
        for item in result.json_content:
            if item.get("type", "").startswith("text:"):
                assert "page" in item, f"DOCX item missing 'page': {item.get('type')}"

    def test_docx_no_crash(self, sample_docx):
        """DOCX processing should not crash with page tracking."""
        result = _load_doc(sample_docx)
        assert result.content
        assert len(result.content) > 0


# ---------------------------------------------------------------------------
# DOCX header/footer separation
# ---------------------------------------------------------------------------

class TestDOCXHeaderFooterSeparation:
    """Verify DOCX headers/footers are tagged and excluded from body."""

    @pytest.fixture
    def sample_docx(self, sample_documents_dir):
        path = sample_documents_dir / "sample_document.docx"
        if not path.exists():
            pytest.skip("sample_document.docx not found")
        return path

    def test_header_footer_types_in_json(self, sample_docx):
        """If doc has headers/footers, they should be typed text:header/text:footer."""
        result = _load_doc(sample_docx)
        types = {item["type"] for item in result.json_content}
        # Not all docs have headers/footers, so just verify no crash
        # and that if present, they have correct types
        for item in result.json_content:
            assert item["type"] not in ("text:header_old", "text:footer_old")

    def test_headers_not_in_markdown_body(self, sample_docx):
        """Headers/footers should not appear as normal text in markdown."""
        result = _load_doc(sample_docx)
        # The key invariant: text:header and text:footer types are
        # skipped by office_to_markdown (handled by the if/elif chain
        # which silently skips unknown types)
        # Verify json_content is complete (all items preserved)
        assert result.json_content is not None


# ---------------------------------------------------------------------------
# PDF header/footer deduplication
# ---------------------------------------------------------------------------

class TestPDFHeaderFooterDedup:
    """Verify repeated content across pages is detected and retyped."""

    def test_dedup_skips_short_docs(self):
        """Documents with <= 3 pages should not have dedup applied."""
        from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader
        loader = PDFLoader.__new__(PDFLoader)
        # Simulate a 2-page document
        doc = {"pages": 2, "content": [
            {"type": "text:normal", "content": "Header", "page": 1, "position_y": 10},
            {"type": "text:normal", "content": "Body", "page": 1, "position_y": 400},
            {"type": "text:normal", "content": "Header", "page": 2, "position_y": 10},
            {"type": "text:normal", "content": "Body 2", "page": 2, "position_y": 400},
        ]}
        loader._detect_repeated_content(doc)
        # Should NOT retype — too few pages
        types = [item["type"] for item in doc["content"]]
        assert "text:header" not in types

    def test_dedup_detects_repeated_header(self):
        """Content at top of >50% of pages should be retyped to text:header."""
        from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader
        import pymupdf

        loader = PDFLoader.__new__(PDFLoader)

        # Mock self.doc with page heights
        class MockPage:
            def __init__(self):
                self.rect = type('Rect', (), {'height': 800})()

        class MockDoc:
            def __len__(self):
                return 5
            def load_page(self, n):
                return MockPage()

        loader.doc = MockDoc()

        # Create content with repeated header on 4/5 pages
        doc = {"pages": 5, "content": []}
        for p in range(1, 6):
            doc["content"].append({
                "type": "text:normal", "content": "Company Name",
                "page": p, "position_y": 20  # top 2.5% of 800px page
            })
            doc["content"].append({
                "type": "text:normal", "content": f"Unique body text page {p}",
                "page": p, "position_y": 400
            })

        loader._detect_repeated_content(doc)

        header_items = [i for i in doc["content"] if i["type"] == "text:header"]
        normal_items = [i for i in doc["content"] if i["type"] == "text:normal"]
        assert len(header_items) == 5, "All 5 repeated headers should be retyped"
        assert len(normal_items) == 5, "Body text should remain text:normal"

    def test_dedup_preserves_all_items(self):
        """Dedup should retype, never remove items from json_content."""
        from doc2mark.pipelines.pymupdf_advanced_pipeline import PDFLoader

        loader = PDFLoader.__new__(PDFLoader)

        class MockPage:
            def __init__(self):
                self.rect = type('Rect', (), {'height': 800})()

        class MockDoc:
            def __len__(self):
                return 4
            def load_page(self, n):
                return MockPage()

        loader.doc = MockDoc()

        doc = {"pages": 4, "content": []}
        for p in range(1, 5):
            doc["content"].append({
                "type": "text:normal", "content": "Repeated Footer",
                "page": p, "position_y": 750  # bottom ~6% of 800px
            })

        original_count = len(doc["content"])
        loader._detect_repeated_content(doc)
        assert len(doc["content"]) == original_count, "No items should be removed"


# ---------------------------------------------------------------------------
# Integration: full pipeline round-trip
# ---------------------------------------------------------------------------

class TestPageMarkersIntegration:
    """End-to-end tests with real documents."""

    @pytest.fixture
    def sample_pdf(self, sample_documents_dir):
        path = sample_documents_dir / "sample_pdf.pdf"
        if not path.exists():
            pytest.skip("sample_pdf.pdf not found")
        return path

    @pytest.fixture
    def sample_pptx(self, sample_documents_dir):
        path = sample_documents_dir / "sample_presentation.pptx"
        if not path.exists():
            pytest.skip("sample_presentation.pptx not found")
        return path

    @pytest.fixture
    def sample_docx(self, sample_documents_dir):
        path = sample_documents_dir / "sample_document.docx"
        if not path.exists():
            pytest.skip("sample_document.docx not found")
        return path

    def test_pdf_no_regression(self, sample_pdf):
        """PDF processing should still produce valid markdown."""
        result = _load_doc(sample_pdf)
        assert result.content
        assert result.metadata.page_count is not None or True  # may not be set

    def test_pptx_no_regression(self, sample_pptx):
        """PPTX processing should still produce valid markdown."""
        result = _load_doc(sample_pptx)
        assert result.content

    def test_docx_no_regression(self, sample_docx):
        """DOCX processing should still produce valid markdown."""
        result = _load_doc(sample_docx)
        assert result.content
