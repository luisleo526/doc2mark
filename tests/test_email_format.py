"""Tests for the EmailProcessor (.eml format)."""

import json
from email.message import EmailMessage
from pathlib import Path

import pytest

from doc2mark.core.base import DocumentFormat, OutputFormat
from doc2mark.formats.email import EmailProcessor


# ---------------------------------------------------------------------------
# Helpers to build .eml fixtures on disk
# ---------------------------------------------------------------------------

def _write_eml(path: Path, msg: EmailMessage) -> Path:
    """Serialize an EmailMessage to *path* and return the path."""
    path.write_bytes(msg.as_bytes())
    return path


def _make_plaintext_email(tmp_path: Path) -> Path:
    """Create a plain-text-only .eml file."""
    msg = EmailMessage()
    msg["From"] = "alice@example.com"
    msg["To"] = "bob@example.com"
    msg["Subject"] = "Plain text test"
    msg["Date"] = "Mon, 01 Jan 2024 12:00:00 +0000"
    msg.set_content("Hello, this is a plain text email body.")
    return _write_eml(tmp_path / "plain.eml", msg)


def _make_html_only_email(tmp_path: Path) -> Path:
    """Create an HTML-only .eml file (no text/plain part)."""
    msg = EmailMessage()
    msg["From"] = "carol@example.com"
    msg["To"] = "dave@example.com"
    msg["Subject"] = "HTML only test"
    msg["Date"] = "Tue, 02 Jan 2024 08:30:00 +0000"
    msg.set_content(
        "<html><body><h1>Greetings</h1><p>This is an <b>HTML</b> email.</p></body></html>",
        subtype="html",
    )
    return _write_eml(tmp_path / "html_only.eml", msg)


def _make_multipart_email(tmp_path: Path) -> Path:
    """Create a multipart/alternative .eml with both text/plain and text/html."""
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["To"] = "recipient@example.com"
    msg["Cc"] = "watcher@example.com"
    msg["Subject"] = "Multipart test"
    msg["Date"] = "Wed, 03 Jan 2024 15:45:00 +0000"
    msg.set_content("Plain text version of the multipart email.")
    msg.add_alternative(
        "<html><body><p>HTML version of the multipart email.</p></body></html>",
        subtype="html",
    )
    return _write_eml(tmp_path / "multipart.eml", msg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEmailProcessorCanProcess:
    """Verify can_process() matches only .eml files."""

    def test_eml_extension_accepted(self, tmp_path):
        p = tmp_path / "msg.eml"
        p.touch()
        assert EmailProcessor().can_process(p) is True

    def test_non_eml_extension_rejected(self, tmp_path):
        p = tmp_path / "msg.txt"
        p.touch()
        assert EmailProcessor().can_process(p) is False


class TestPlainTextEmail:
    """Process a plain-text-only email."""

    def test_markdown_output(self, tmp_path):
        eml_path = _make_plaintext_email(tmp_path)
        result = EmailProcessor().process(eml_path)

        assert "Plain text test" in result.content
        assert "alice@example.com" in result.content
        assert "plain text email body" in result.content
        assert result.metadata.format is DocumentFormat.EML
        assert result.metadata.filename == "plain.eml"
        assert result.metadata.size_bytes > 0

    def test_text_output(self, tmp_path):
        eml_path = _make_plaintext_email(tmp_path)
        result = EmailProcessor().process(eml_path, output_format=OutputFormat.TEXT)

        assert "Subject: Plain text test" in result.content
        assert "From: alice@example.com" in result.content
        assert "plain text email body" in result.content

    def test_json_output(self, tmp_path):
        eml_path = _make_plaintext_email(tmp_path)
        result = EmailProcessor().process(eml_path, output_format=OutputFormat.JSON)

        data = json.loads(result.content)
        assert data["headers"]["Subject"] == "Plain text test"
        assert data["headers"]["From"] == "alice@example.com"
        assert "plain text email body" in data["body"]


class TestHTMLOnlyEmail:
    """Process an email that contains only an HTML body."""

    def test_markdown_output(self, tmp_path):
        eml_path = _make_html_only_email(tmp_path)
        result = EmailProcessor().process(eml_path)

        # The HTML should be converted; the word "Greetings" from <h1> and
        # "HTML" from the <b> tag should survive in some form.
        assert "HTML only test" in result.content
        assert "carol@example.com" in result.content
        assert "Greetings" in result.content
        assert result.metadata.format is DocumentFormat.EML

    def test_text_output(self, tmp_path):
        eml_path = _make_html_only_email(tmp_path)
        result = EmailProcessor().process(eml_path, output_format=OutputFormat.TEXT)

        assert "Subject: HTML only test" in result.content
        assert "Greetings" in result.content


class TestMultipartEmail:
    """Process a multipart email (text/plain preferred over text/html)."""

    def test_prefers_plain_text(self, tmp_path):
        eml_path = _make_multipart_email(tmp_path)
        result = EmailProcessor().process(eml_path)

        # Should use the plain text part, not the HTML part
        assert "Plain text version" in result.content
        assert result.metadata.format is DocumentFormat.EML

    def test_headers_present(self, tmp_path):
        eml_path = _make_multipart_email(tmp_path)
        result = EmailProcessor().process(eml_path)

        assert "sender@example.com" in result.content
        assert "recipient@example.com" in result.content
        assert "watcher@example.com" in result.content
        assert "Multipart test" in result.content

    def test_json_includes_cc(self, tmp_path):
        eml_path = _make_multipart_email(tmp_path)
        result = EmailProcessor().process(eml_path, output_format=OutputFormat.JSON)

        data = json.loads(result.content)
        assert "watcher@example.com" in data["headers"]["Cc"]


class TestEmailProcessorInit:
    """Verify constructor accepts optional ocr and kwargs."""

    def test_default_init(self):
        proc = EmailProcessor()
        assert proc.ocr is None

    def test_with_ocr(self):
        sentinel = object()
        proc = EmailProcessor(ocr=sentinel)
        assert proc.ocr is sentinel

    def test_extra_kwargs_accepted(self):
        # Should not raise
        EmailProcessor(ocr=None, some_future_option=True)


class TestMimeMapperEml:
    """Verify the MIME mapper recognises .eml and message/rfc822."""

    def test_mime_to_format(self):
        from doc2mark.core.mime_mapper import MimeTypeMapper

        mapper = MimeTypeMapper()
        assert mapper.get_format_from_mime("message/rfc822") is DocumentFormat.EML

    def test_format_to_mime(self):
        from doc2mark.core.mime_mapper import MimeTypeMapper

        mapper = MimeTypeMapper()
        assert mapper.get_mime_from_format(DocumentFormat.EML) == "message/rfc822"

    def test_extension_detection(self):
        from doc2mark.core.mime_mapper import MimeTypeMapper

        mapper = MimeTypeMapper()
        fmt = mapper.detect_format_from_file("message.eml")
        assert fmt is DocumentFormat.EML
