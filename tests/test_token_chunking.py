"""Tests for token-aware chunking (size_unit='tokens')."""

import builtins
import logging

import pytest

tiktoken = pytest.importorskip("tiktoken")

from doc2mark.core.chunker import (
    ChunkingConfig,
    Chunk,
    _make_length_fn,
    chunk_content,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _token_count(text: str, encoding_name: str = "cl100k_base") -> int:
    """Count tokens using tiktoken."""
    enc = tiktoken.get_encoding(encoding_name)
    return len(enc.encode(text))


def _make_items(texts):
    """Convenience: turn a list of strings into json_content items."""
    return [{"type": "text:normal", "content": t} for t in texts]


# ---------------------------------------------------------------------------
# _make_length_fn
# ---------------------------------------------------------------------------

class TestMakeLengthFn:
    """Unit tests for the length-function factory."""

    def test_chars_mode_returns_len(self):
        cfg = ChunkingConfig(size_unit="chars")
        fn = _make_length_fn(cfg)
        assert fn is len

    def test_tokens_mode_counts_tokens(self):
        cfg = ChunkingConfig(size_unit="tokens", encoding_name="cl100k_base")
        fn = _make_length_fn(cfg)
        text = "hello world"
        assert fn(text) == _token_count(text)

    def test_tokens_mode_different_encoding(self):
        cfg = ChunkingConfig(size_unit="tokens", encoding_name="p50k_base")
        fn = _make_length_fn(cfg)
        text = "hello world"
        expected = len(tiktoken.get_encoding("p50k_base").encode(text))
        assert fn(text) == expected

    def test_default_config_uses_chars(self):
        """Default ChunkingConfig should use character-based sizing."""
        cfg = ChunkingConfig()
        assert cfg.size_unit == "chars"
        fn = _make_length_fn(cfg)
        assert fn is len


# ---------------------------------------------------------------------------
# Token-mode chunking
# ---------------------------------------------------------------------------

class TestTokenModeChunking:
    """Chunks produced in token mode respect max_chunk_size in tokens."""

    def test_single_item_fits_in_token_budget(self):
        text = "A short sentence."
        items = _make_items([text])
        cfg = ChunkingConfig(
            max_chunk_size=100,
            overlap=0,
            size_unit="tokens",
        )
        chunks = chunk_content(items, cfg)
        assert len(chunks) == 1
        assert _token_count(chunks[0].content) <= cfg.max_chunk_size

    def test_splits_when_tokens_exceed_budget(self):
        """Multiple paragraphs that together exceed the token budget get split."""
        para = "The quick brown fox jumps over the lazy dog. " * 20  # ~180 tokens
        items = _make_items([para, para, para])
        cfg = ChunkingConfig(
            max_chunk_size=200,
            overlap=0,
            size_unit="tokens",
        )
        chunks = chunk_content(items, cfg)
        assert len(chunks) >= 2
        for chunk in chunks:
            # Each chunk should respect the budget (tables excepted, but none here)
            assert _token_count(chunk.content) <= cfg.max_chunk_size + 10  # small tolerance for separator tokens

    def test_token_budget_stricter_than_char_budget(self):
        """With the same numeric limit, token mode should produce more chunks
        than char mode because tokens are larger than characters."""
        para = "The quick brown fox jumps over the lazy dog. " * 30
        items = _make_items([para, para])
        limit = 200

        char_chunks = chunk_content(items, ChunkingConfig(
            max_chunk_size=limit, overlap=0, size_unit="chars",
        ))
        token_chunks = chunk_content(items, ChunkingConfig(
            max_chunk_size=limit, overlap=0, size_unit="tokens",
        ))
        # Token-mode should produce at least as many (likely more) chunks
        assert len(token_chunks) >= len(char_chunks)

    def test_token_mode_preserves_section_metadata(self):
        items = [
            {"type": "text:title", "content": "Title"},
            {"type": "text:normal", "content": "Body " * 100},
        ]
        cfg = ChunkingConfig(
            max_chunk_size=50,
            overlap=0,
            size_unit="tokens",
        )
        chunks = chunk_content(items, cfg)
        assert len(chunks) >= 1
        assert chunks[0].section_title == "Title"
        for chunk in chunks:
            assert chunk.section_hierarchy == ["Title"]


# ---------------------------------------------------------------------------
# Char-mode unchanged
# ---------------------------------------------------------------------------

class TestCharModeUnchanged:
    """Verify that char-based chunking still works identically."""

    def test_single_paragraph_char_mode(self):
        items = _make_items(["A single paragraph."])
        chunks = chunk_content(items, ChunkingConfig(size_unit="chars"))
        assert len(chunks) == 1
        assert chunks[0].content == "A single paragraph."

    def test_splits_by_chars(self):
        items = _make_items(["A" * 800, "B" * 800, "C" * 800])
        cfg = ChunkingConfig(max_chunk_size=1000, overlap=0, size_unit="chars")
        chunks = chunk_content(items, cfg)
        assert len(chunks) >= 2

    def test_default_config_char_based(self):
        """chunk_content(items) without explicit config should use chars."""
        items = _make_items(["hello"])
        chunks = chunk_content(items)
        assert len(chunks) == 1
        assert chunks[0].content == "hello"

    def test_overlap_char_mode(self):
        items = _make_items(["A" * 500, "B" * 500])
        cfg = ChunkingConfig(max_chunk_size=600, overlap=100, size_unit="chars")
        chunks = chunk_content(items, cfg)
        if len(chunks) >= 2:
            # Second chunk should contain overlap from first
            assert len(chunks[1].content) > 500


# ---------------------------------------------------------------------------
# Token-mode overlap
# ---------------------------------------------------------------------------

class TestTokenModeOverlap:
    """Overlap in token mode should use token-based tail extraction."""

    def test_overlap_present_in_token_mode(self):
        para_a = "Alpha bravo charlie delta echo foxtrot golf. " * 20
        para_b = "Hotel india juliet kilo lima mike november. " * 20
        items = _make_items([para_a, para_b])
        cfg = ChunkingConfig(
            max_chunk_size=100,
            overlap=20,
            size_unit="tokens",
        )
        chunks = chunk_content(items, cfg)
        if len(chunks) >= 2:
            # The second chunk should be longer than just its own paragraph
            # because overlap text was prepended
            standalone_tokens = _token_count(para_b)
            actual_tokens = _token_count(chunks[1].content)
            assert actual_tokens > standalone_tokens


# ---------------------------------------------------------------------------
# Missing tiktoken fallback
# ---------------------------------------------------------------------------

class TestMissingTiktokenFallback:
    """When tiktoken is not importable, token mode falls back to char counting."""

    def test_fallback_logs_warning(self, monkeypatch, caplog):
        """_make_length_fn should log a warning and return len."""
        real_import = builtins.__import__

        def _block_tiktoken(name, *args, **kwargs):
            if name == "tiktoken":
                raise ImportError("mocked: tiktoken not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_tiktoken)

        cfg = ChunkingConfig(size_unit="tokens")
        with caplog.at_level(logging.WARNING, logger="doc2mark.core.chunker"):
            fn = _make_length_fn(cfg)
        assert fn is len
        assert "tiktoken is not installed" in caplog.text

    def test_fallback_still_chunks(self, monkeypatch):
        """Even without tiktoken, chunking should succeed using characters."""
        real_import = builtins.__import__

        def _block_tiktoken(name, *args, **kwargs):
            if name == "tiktoken":
                raise ImportError("mocked: tiktoken not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_tiktoken)

        items = _make_items(["A" * 800, "B" * 800])
        cfg = ChunkingConfig(max_chunk_size=1000, overlap=0, size_unit="tokens")
        chunks = chunk_content(items, cfg)
        assert len(chunks) >= 1
        # Should have used char-based splitting
        for chunk in chunks:
            assert len(chunk.content) <= 1000 + 10  # small tolerance

    def test_fallback_overlap_uses_chars(self, monkeypatch):
        """Overlap tail extraction falls back to chars when tiktoken missing."""
        real_import = builtins.__import__

        def _block_tiktoken(name, *args, **kwargs):
            if name == "tiktoken":
                raise ImportError("mocked: tiktoken not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_tiktoken)

        items = _make_items(["A" * 500, "B" * 500])
        cfg = ChunkingConfig(max_chunk_size=600, overlap=100, size_unit="tokens")
        chunks = chunk_content(items, cfg)
        # Should succeed without error
        assert len(chunks) >= 1


# ---------------------------------------------------------------------------
# ChunkingConfig defaults
# ---------------------------------------------------------------------------

class TestChunkingConfigDefaults:
    """New fields have backward-compatible defaults."""

    def test_size_unit_default(self):
        cfg = ChunkingConfig()
        assert cfg.size_unit == "chars"

    def test_encoding_name_default(self):
        cfg = ChunkingConfig()
        assert cfg.encoding_name == "cl100k_base"

    def test_existing_fields_unchanged(self):
        cfg = ChunkingConfig()
        assert cfg.max_chunk_size == 1500
        assert cfg.overlap == 200
        assert cfg.split_on_heading_level == 2
        assert cfg.keep_tables_whole is True
        assert cfg.include_page_markers is False
