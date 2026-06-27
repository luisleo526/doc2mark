"""Email format processor (.eml files)."""

import email
import email.policy
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional, Union

from doc2mark.core.base import (
    BaseProcessor,
    DocumentFormat,
    DocumentMetadata,
    OutputFormat,
    ProcessedDocument,
    ProcessingError,
)

logger = logging.getLogger(__name__)

# Header names to extract (in display order)
_HEADER_NAMES = ("From", "To", "Cc", "Subject", "Date")


def _html_to_markdown(html: str) -> str:
    """Convert HTML body to markdown using the project's existing converter."""
    try:
        from doc2mark.formats.markup import SimpleHTMLToMarkdown

        parser = SimpleHTMLToMarkdown()
        parser.feed(html)
        return parser.get_markdown()
    except Exception:  # pragma: no cover – fallback to tag strip
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return text.strip()


class EmailProcessor(BaseProcessor):
    """Processor for .eml (RFC 822) email files."""

    def __init__(self, ocr=None, **kwargs):
        self.ocr = ocr

    # ------------------------------------------------------------------
    # BaseProcessor interface
    # ------------------------------------------------------------------

    def can_process(self, file_path: Union[str, Path]) -> bool:
        """Check if this processor can handle the file."""
        return Path(file_path).suffix.lower() == ".eml"

    def process(
        self,
        file_path: Union[str, Path],
        output_format: OutputFormat = OutputFormat.MARKDOWN,
        **kwargs,
    ) -> ProcessedDocument:
        """Process an .eml file and return a ProcessedDocument.

        Parameters
        ----------
        file_path : str | Path
            Path to the ``.eml`` file.
        output_format : OutputFormat
            Desired output representation (MARKDOWN, TEXT, or JSON).
        """
        file_path = Path(file_path)

        try:
            with open(file_path, "rb") as fp:
                msg = email.message_from_binary_file(fp, policy=email.policy.default)
        except Exception as exc:
            raise ProcessingError(f"Failed to parse .eml file: {exc}") from exc

        # --- Extract headers ---------------------------------------------------
        headers: Dict[str, Optional[str]] = {}
        for name in _HEADER_NAMES:
            value = msg.get(name)
            headers[name] = str(value) if value is not None else None

        # --- Extract body ------------------------------------------------------
        body = self._extract_body(msg)

        # --- Build output per requested format ---------------------------------
        content = self._render(headers, body, output_format)

        # --- Metadata ----------------------------------------------------------
        file_size = file_path.stat().st_size
        metadata = DocumentMetadata(
            filename=file_path.name,
            format=DocumentFormat.EML,
            size_bytes=file_size,
        )

        return ProcessedDocument(content=content, metadata=metadata)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_body(msg: email.message.EmailMessage) -> str:
        """Return the best plain-text representation of the email body.

        Preference order:
        1. text/plain part
        2. text/html part converted to markdown
        """
        # Walk parts for multipart messages; for simple messages the loop
        # still works (a non-multipart message yields itself).
        plain_parts = []
        html_parts = []

        for part in msg.walk():
            content_type = part.get_content_type()
            # Skip multipart containers themselves
            if part.get_content_maintype() == "multipart":
                continue
            if content_type == "text/plain":
                payload = part.get_content()
                if isinstance(payload, str):
                    plain_parts.append(payload)
            elif content_type == "text/html":
                payload = part.get_content()
                if isinstance(payload, str):
                    html_parts.append(payload)

        if plain_parts:
            return "\n".join(plain_parts)

        if html_parts:
            return _html_to_markdown("\n".join(html_parts))

        return ""

    @staticmethod
    def _render(
        headers: Dict[str, Optional[str]],
        body: str,
        output_format: OutputFormat,
    ) -> str:
        """Render headers + body into the requested output format."""

        if output_format == OutputFormat.JSON:
            payload: Dict[str, Any] = {
                "headers": {k: v for k, v in headers.items() if v is not None},
                "body": body,
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        if output_format == OutputFormat.TEXT:
            lines = []
            for name in _HEADER_NAMES:
                value = headers.get(name)
                if value is not None:
                    lines.append(f"{name}: {value}")
            lines.append("")
            lines.append(body)
            return "\n".join(lines)

        # Default: MARKDOWN
        lines = []
        subject = headers.get("Subject") or "(no subject)"
        lines.append(f"# {subject}")
        lines.append("")
        for name in _HEADER_NAMES:
            if name == "Subject":
                continue
            value = headers.get(name)
            if value is not None:
                lines.append(f"**{name}:** {value}  ")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(body)
        return "\n".join(lines)
