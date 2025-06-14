"""Text and data format processors (TXT, CSV, TSV, JSON, JSONL)."""

import csv
import json
import logging
from pathlib import Path
from typing import Any, List, Union, Tuple

from doc2mark.core.base import (
    BaseProcessor,
    DocumentFormat,
    DocumentMetadata,
    ProcessedDocument,
    ProcessingError
)

logger = logging.getLogger(__name__)


class TextProcessor(BaseProcessor):
    """Processor for text and data formats."""

    def can_process(self, file_path: Union[str, Path]) -> bool:
        """Check if this processor can handle the file."""
        file_path = Path(file_path)
        extension = file_path.suffix.lower().lstrip('.')
        return extension in ['txt', 'csv', 'tsv', 'json', 'jsonl']

    def process(
            self,
            file_path: Union[str, Path],
            **kwargs
    ) -> ProcessedDocument:
        """Process text/data document."""
        file_path = Path(file_path)
        extension = file_path.suffix.lower().lstrip('.')

        # Get file size
        file_size = file_path.stat().st_size

        # Process based on format
        if extension == 'txt':
            content, metadata = self._process_txt(file_path, **kwargs)
            doc_format = DocumentFormat.TXT
        elif extension == 'csv':
            content, metadata = self._process_csv(file_path, **kwargs)
            doc_format = DocumentFormat.CSV
        elif extension == 'tsv':
            content, metadata = self._process_tsv(file_path, **kwargs)
            doc_format = DocumentFormat.TSV
        elif extension == 'json':
            content, metadata = self._process_json(file_path, **kwargs)
            doc_format = DocumentFormat.JSON
        elif extension == 'jsonl':
            content, metadata = self._process_jsonl(file_path, **kwargs)
            doc_format = DocumentFormat.JSONL
        else:
            raise ProcessingError(f"Unsupported text format: {extension}")

        # Build metadata
        doc_metadata = DocumentMetadata(
            filename=file_path.name,
            format=doc_format,
            size_bytes=file_size,
            **metadata
        )

        return ProcessedDocument(
            content=content,
            metadata=doc_metadata
        )

    def _process_txt(self, file_path: Path, **kwargs) -> Tuple[str, dict]:
        """Process plain text file."""
        try:
            # Detect encoding
            encoding = kwargs.get('encoding', 'utf-8')

            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()

            # Count words and lines
            lines = content.split('\n')
            word_count = len(content.split())

            # Format as markdown
            markdown_content = self._format_text_as_markdown(content)

            metadata = {
                'word_count': word_count,
                'line_count': len(lines),
                'encoding': encoding
            }

            return markdown_content, metadata

        except Exception as e:
            logger.error(f"Failed to process TXT: {e}")
            raise ProcessingError(f"TXT processing failed: {str(e)}")

    def _process_csv(self, file_path: Path, **kwargs) -> Tuple[str, dict]:
        """Process CSV file."""
        try:
            encoding = kwargs.get('encoding', 'utf-8')

            with open(file_path, 'r', encoding=encoding, newline='') as f:
                # Detect delimiter
                sample = f.read(1024)
                f.seek(0)
                sniffer = csv.Sniffer()
                delimiter = sniffer.sniff(sample).delimiter

                # Read CSV
                reader = csv.reader(f, delimiter=delimiter)
                rows = list(reader)

            if not rows:
                return "", {'row_count': 0, 'column_count': 0}

            # Convert to markdown table
            markdown_content = self._convert_csv_to_markdown(rows)

            metadata = {
                'row_count': len(rows),
                'column_count': len(rows[0]) if rows else 0,
                'delimiter': delimiter,
                'encoding': encoding
            }

            return markdown_content, metadata

        except Exception as e:
            logger.error(f"Failed to process CSV: {e}")
            raise ProcessingError(f"CSV processing failed: {str(e)}")

    def _process_tsv(self, file_path: Path, **kwargs) -> Tuple[str, dict]:
        """Process TSV file."""
        # TSV is just CSV with tab delimiter
        kwargs['delimiter'] = '\t'
        content, metadata = self._process_csv_with_delimiter(file_path, '\t', **kwargs)
        return content, metadata

    def _process_csv_with_delimiter(self, file_path: Path, delimiter: str, **kwargs) -> Tuple[str, dict]:
        """Process CSV/TSV with specific delimiter."""
        try:
            encoding = kwargs.get('encoding', 'utf-8')

            with open(file_path, 'r', encoding=encoding, newline='') as f:
                reader = csv.reader(f, delimiter=delimiter)
                rows = list(reader)

            if not rows:
                return "", {'row_count': 0, 'column_count': 0}

            # Convert to markdown table
            markdown_content = self._convert_csv_to_markdown(rows)

            metadata = {
                'row_count': len(rows),
                'column_count': len(rows[0]) if rows else 0,
                'delimiter': delimiter,
                'encoding': encoding
            }

            return markdown_content, metadata

        except Exception as e:
            logger.error(f"Failed to process file with delimiter '{delimiter}': {e}")
            raise ProcessingError(f"Processing failed: {str(e)}")

    def _process_json(self, file_path: Path, **kwargs) -> Tuple[str, dict]:
        """Process JSON file."""
        try:
            encoding = kwargs.get('encoding', 'utf-8')

            with open(file_path, 'r', encoding=encoding) as f:
                data = json.load(f)

            # Format JSON as markdown
            markdown_content = self._format_json_as_markdown(data, **kwargs)

            metadata = {
                'encoding': encoding,
                'data_type': type(data).__name__,
                'item_count': len(data) if isinstance(data, (list, dict)) else 1
            }

            return markdown_content, metadata

        except Exception as e:
            logger.error(f"Failed to process JSON: {e}")
            raise ProcessingError(f"JSON processing failed: {str(e)}")

    def _process_jsonl(self, file_path: Path, **kwargs) -> Tuple[str, dict]:
        """Process JSONL (JSON Lines) file."""
        try:
            encoding = kwargs.get('encoding', 'utf-8')

            records = []
            with open(file_path, 'r', encoding=encoding) as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            logger.warning(f"Invalid JSON at line {line_num}: {e}")

            # Format as markdown
            markdown_parts = [f"# JSONL Data ({len(records)} records)\n"]

            for i, record in enumerate(records, 1):
                markdown_parts.append(f"## Record {i}")
                markdown_parts.append(self._format_json_as_markdown(record, indent=False))
                markdown_parts.append("")

            metadata = {
                'encoding': encoding,
                'record_count': len(records)
            }

            return '\n'.join(markdown_parts), metadata

        except Exception as e:
            logger.error(f"Failed to process JSONL: {e}")
            raise ProcessingError(f"JSONL processing failed: {str(e)}")

    def _format_text_as_markdown(self, text: str) -> str:
        """Format plain text as markdown."""
        # Basic formatting - preserve structure
        lines = text.split('\n')
        formatted_lines = []

        for line in lines:
            # Detect potential headers (lines that are all caps or have specific patterns)
            if line.strip() and line.isupper() and len(line) < 100:
                formatted_lines.append(f"## {line}")
            else:
                formatted_lines.append(line)

        return '\n'.join(formatted_lines)

    def _convert_csv_to_markdown(self, rows: List[List[str]]) -> str:
        """Convert CSV rows to markdown table."""
        if not rows:
            return ""

        # Ensure all rows have same number of columns
        max_cols = max(len(row) for row in rows)
        normalized_rows = []
        for row in rows:
            normalized_row = row + [''] * (max_cols - len(row))
            normalized_rows.append(normalized_row)

        # Determine column widths
        col_widths = [0] * max_cols
        for row in normalized_rows:
            for i, cell in enumerate(row):
                col_widths[i] = max(col_widths[i], len(str(cell)))

        # Build markdown table
        lines = []

        # Header (first row)
        if normalized_rows:
            header = "| " + " | ".join(
                str(cell).ljust(col_widths[i])
                for i, cell in enumerate(normalized_rows[0])
            ) + " |"
            lines.append(header)

            # Separator
            separator = "|" + "|".join("-" * (w + 2) for w in col_widths) + "|"
            lines.append(separator)

            # Data rows
            for row in normalized_rows[1:]:
                row_str = "| " + " | ".join(
                    str(cell).ljust(col_widths[i])
                    for i, cell in enumerate(row)
                ) + " |"
                lines.append(row_str)

        return '\n'.join(lines)

    def _format_json_as_markdown(self, data: Any, indent: bool = True, **kwargs) -> str:
        """Format JSON data as markdown."""
        if isinstance(data, dict):
            return self._format_dict_as_markdown(data, indent=indent)
        elif isinstance(data, list):
            return self._format_list_as_markdown(data, indent=indent)
        else:
            # Primitive type
            return f"```json\n{json.dumps(data, indent=2 if indent else None)}\n```"

    def _format_dict_as_markdown(self, data: dict, level: int = 0, indent: bool = True) -> str:
        """Format dictionary as markdown."""
        lines = []

        for key, value in data.items():
            prefix = "  " * level if indent else ""

            if isinstance(value, dict):
                lines.append(f"{prefix}**{key}**:")
                lines.append(self._format_dict_as_markdown(value, level + 1, indent))
            elif isinstance(value, list):
                lines.append(f"{prefix}**{key}**:")
                lines.append(self._format_list_as_markdown(value, level + 1, indent))
            else:
                # Simple value
                lines.append(f"{prefix}**{key}**: {value}")

        return '\n'.join(lines)

    def _format_list_as_markdown(self, data: list, level: int = 0, indent: bool = True) -> str:
        """Format list as markdown."""
        lines = []
        prefix = "  " * level if indent else ""

        for i, item in enumerate(data):
            if isinstance(item, dict):
                lines.append(f"{prefix}- Item {i + 1}:")
                lines.append(self._format_dict_as_markdown(item, level + 1, indent))
            elif isinstance(item, list):
                lines.append(f"{prefix}- Item {i + 1}:")
                lines.append(self._format_list_as_markdown(item, level + 1, indent))
            else:
                lines.append(f"{prefix}- {item}")

        return '\n'.join(lines)
