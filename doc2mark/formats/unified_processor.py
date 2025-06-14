"""Unified processor that integrates with existing advanced pipelines."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from doc2mark.core.base import (
    BaseProcessor,
    DocumentFormat,
    DocumentMetadata,
    OutputFormat,
    ProcessedDocument,
    ProcessingError
)

# Import internal pipelines (no external path manipulation needed)

logger = logging.getLogger(__name__)


class UnifiedProcessor(BaseProcessor):
    """Processor that uses the existing advanced pipelines."""

    def __init__(self, ocr=None):
        """Initialize the unified processor.
        
        Args:
            ocr: OCR provider (for compatibility, but pipelines use their own)
        """
        self.ocr = ocr
        self._office_pipeline = None
        self._pdf_pipeline = None
        self._legacy_converter = None

    def can_process(self, file_path: Union[str, Path]) -> bool:
        """Check if this processor can handle the file."""
        file_path = Path(file_path)
        extension = file_path.suffix.lower().lstrip('.')

        # All formats that the unified pipelines can handle
        supported = {
            # Modern Office
            'docx', 'xlsx', 'pptx',
            # Legacy Office  
            'doc', 'xls', 'ppt', 'rtf', 'pps',
            # PDF
            'pdf',
            # Text/Data
            'txt', 'csv', 'tsv', 'json', 'jsonl',
            # Markup
            'html', 'htm', 'xml', 'md', 'markdown'
        }

        return extension in supported

    def process(
            self,
            file_path: Union[str, Path],
            output_format: Union[str, Any] = OutputFormat.MARKDOWN,
            extract_images: bool = True,
            ocr_images: bool = False,
            preserve_layout: bool = True,
            show_progress: bool = False,
            # Format-specific parameters
            encoding: str = 'utf-8',
            delimiter: Optional[str] = None,
            **kwargs
    ) -> ProcessedDocument:
        """Process document using the appropriate pipeline.
        
        Args:
            file_path: Path to the document file
            output_format: Desired output format (e.g., OutputFormat.MARKDOWN)
            extract_images: Whether to extract images from the document
            ocr_images: Whether to perform OCR on extracted images (requires extract_images=True)
            preserve_layout: Whether to preserve document layout during processing
            show_progress: Whether to display progress messages during processing
            encoding: Text file encoding
            delimiter: CSV delimiter
            **kwargs: Any remaining parameters
            
        Returns:
            ProcessedDocument with content, metadata, and extracted data
            
        Raises:
            ProcessingError: If document processing fails
        """
        file_path = Path(file_path)
        extension = file_path.suffix.lower().lstrip('.')

        # Get file size
        file_size = file_path.stat().st_size

        # Determine which pipeline to use
        if extension in ['docx', 'xlsx', 'pptx']:
            return self._process_modern_office(
                file_path, file_size, extract_images, ocr_images,
                output_format=output_format,
                show_progress=show_progress,
                **kwargs
            )
        elif extension in ['doc', 'xls', 'ppt', 'rtf', 'pps']:
            return self._process_legacy_office(
                file_path, file_size, extract_images, ocr_images,
                output_format=output_format,
                show_progress=show_progress,
                **kwargs
            )
        elif extension == 'pdf':
            return self._process_pdf(
                file_path, file_size, extract_images, ocr_images,
                output_format=output_format,
                show_progress=show_progress,
                **kwargs
            )
        elif extension in ['txt', 'csv', 'tsv', 'json', 'jsonl', 'html', 'htm', 'xml', 'md', 'markdown']:
            return self._process_text_based(
                file_path, file_size, extract_images, ocr_images,
                output_format=output_format,
                show_progress=show_progress,
                encoding=encoding,
                delimiter=delimiter if extension in ['csv', 'tsv'] else None,
                **kwargs
            )
        else:
            # For other formats, use the existing processors
            raise ProcessingError(f"Format {extension} not implemented in unified processor")

    def _process_modern_office(self, file_path: Path, file_size: int, extract_images: bool, ocr_images: bool,
                               output_format: Union[str, Any] = OutputFormat.MARKDOWN,
                               show_progress: bool = False,
                               **kwargs) -> ProcessedDocument:
        """Process modern Office formats using office_advanced_pipeline.
        
        Args:
            file_path: Path to the Office document
            file_size: Size of the file in bytes
            extract_images: Whether to extract images from the document
            ocr_images: Whether to perform OCR on extracted images
            **kwargs: Additional options (show_progress, etc.)
        """
        try:
            # Import the internal pipeline
            from doc2mark.pipelines import office_to_json, office_to_markdown

            # Note: office_to_json doesn't support all the OCR parameters
            # It only accepts: file_path, output_path, output_markdown, extract_images, ocr_images, show_progress, ocr
            # So we don't pass the unsupported parameters

            # Use the provided parameters for image extraction and OCR
            json_data = office_to_json(
                file_path=file_path,
                extract_images=extract_images,
                ocr_images=ocr_images,
                show_progress=show_progress,
                ocr=self.ocr
            )

            # Convert to requested format
            content = self._format_content(json_data, output_format)

            # Extract metadata from JSON
            metadata = self._extract_metadata(json_data, file_path, file_size)

            # Extract images if any
            images = self._extract_images_from_json(json_data)

            return ProcessedDocument(
                content=content,
                metadata=metadata,
                json_content=json_data.get('content', []),
                images=images
            )

        except ImportError as e:
            logger.error(f"Failed to import office_advanced_pipeline: {e}")
            raise ProcessingError("Office pipeline not available")
        except Exception as e:
            logger.error(f"Failed to process office file: {e}")
            raise ProcessingError(f"Office processing failed: {str(e)}")

    def _process_legacy_office(self, file_path: Path, file_size: int, extract_images: bool, ocr_images: bool,
                               output_format: Union[str, Any] = OutputFormat.MARKDOWN,
                               show_progress: bool = False,
                               **kwargs) -> ProcessedDocument:
        """Process legacy Office formats by converting first.
        
        Args:
            file_path: Path to the legacy Office document
            file_size: Size of the file in bytes
            extract_images: Whether to extract images from the document
            ocr_images: Whether to perform OCR on extracted images
            **kwargs: Additional options (show_progress, etc.)
        """
        try:
            # Import the internal converters
            from doc2mark.pipelines import LegacyOfficeConverter, office_to_json, office_to_markdown

            # Initialize converter if needed
            if not self._legacy_converter:
                self._legacy_converter = LegacyOfficeConverter()

            # Convert to modern format
            converted_path = self._legacy_converter.convert_file(file_path)

            # Note: office_to_json doesn't support all the OCR parameters
            # It only accepts: file_path, output_path, output_markdown, extract_images, ocr_images, show_progress, ocr
            # So we don't pass the unsupported parameters

            # Process the converted file with provided parameters
            json_data = office_to_json(
                file_path=converted_path,
                extract_images=extract_images,
                ocr_images=ocr_images,
                show_progress=show_progress,
                ocr=self.ocr
            )

            # Convert to requested format
            content = self._format_content(json_data, output_format)

            # Extract metadata
            metadata = self._extract_metadata(json_data, file_path, file_size)

            # Mark as converted
            metadata.extra['converted_from'] = file_path.suffix.lower().lstrip('.')

            # Clean up converted file
            try:
                converted_path.unlink()
            except:
                pass

            # Extract images
            images = self._extract_images_from_json(json_data)

            return ProcessedDocument(
                content=content,
                metadata=metadata,
                json_content=json_data.get('content', []),
                images=images
            )

        except ImportError as e:
            logger.error(f"Failed to import legacy converter: {e}")
            raise ProcessingError("Legacy converter not available")
        except Exception as e:
            logger.error(f"Failed to process legacy file: {e}")
            raise ProcessingError(f"Legacy processing failed: {str(e)}")

    def _process_pdf(self, file_path: Path, file_size: int, extract_images: bool, ocr_images: bool,
                     output_format: Union[str, Any] = OutputFormat.MARKDOWN,
                     show_progress: bool = False,
                     **kwargs) -> ProcessedDocument:
        """Process PDF files using pymupdf_advanced_pipeline.
        
        Args:
            file_path: Path to the PDF document
            file_size: Size of the file in bytes
            extract_images: Whether to extract images from the document
            ocr_images: Whether to perform OCR on extracted images
            **kwargs: Additional options (show_progress, etc.)
        """
        try:
            # Import the internal pipeline
            from doc2mark.pipelines import pdf_to_simple_json

            # Note: pdf_to_simple_json doesn't support all the OCR parameters
            # It only accepts: pdf_path, output_path, output_markdown, extract_images, ocr_images, show_progress, ocr
            # So we don't pass the unsupported parameters

            # Process PDF with provided parameters
            json_data = pdf_to_simple_json(
                pdf_path=file_path,
                extract_images=extract_images,
                ocr_images=ocr_images,
                show_progress=show_progress,
                ocr=self.ocr
            )

            # Convert to requested format
            content = self._format_content(json_data, output_format)

            # Extract metadata
            metadata = self._extract_metadata(json_data, file_path, file_size)

            # Extract images
            images = self._extract_images_from_json(json_data)

            return ProcessedDocument(
                content=content,
                metadata=metadata,
                json_content=json_data.get('content', []),
                images=images
            )

        except ImportError as e:
            logger.error(f"Failed to import pymupdf_advanced_pipeline: {e}")
            raise ProcessingError("PDF pipeline not available")
        except Exception as e:
            logger.error(f"Failed to process PDF: {e}")
            raise ProcessingError(f"PDF processing failed: {str(e)}")

    def _process_text_based(self, file_path: Path, file_size: int, extract_images: bool, ocr_images: bool,
                            output_format: Union[str, Any] = OutputFormat.MARKDOWN,
                            show_progress: bool = False,
                            encoding: str = 'utf-8',
                            delimiter: Optional[str] = None,
                            **kwargs) -> ProcessedDocument:
        """Process text-based formats using the existing processors.
        
        Args:
            file_path: Path to the text-based document
            file_size: Size of the file in bytes
            extract_images: Whether to extract images from the document (not applicable for most text formats)
            ocr_images: Whether to perform OCR on extracted images (not applicable for most text formats)
            **kwargs: Additional options (show_progress, encoding, etc.)
        """
        try:
            extension = file_path.suffix.lower().lstrip('.')

            # Use appropriate processor based on format
            if extension in ['txt', 'csv', 'tsv', 'json', 'jsonl']:
                # Import and use TextProcessor
                from doc2mark.formats.text import TextProcessor
                processor = TextProcessor()

                # TextProcessor accepts encoding and delimiter through kwargs
                processor_kwargs = {
                    'encoding': encoding
                }
                if extension in ['csv', 'tsv'] and delimiter:
                    processor_kwargs['delimiter'] = delimiter

                # Process with TextProcessor
                result = processor.process(
                    file_path,
                    **processor_kwargs
                )

                # Note: TextProcessor doesn't support image extraction/OCR
                # as these are primarily text formats without embedded images
                if extract_images and show_progress:
                    logger.info(f"Image extraction not applicable for {extension} format")

                # Apply output format conversion if needed
                if output_format and output_format != OutputFormat.MARKDOWN:
                    # Convert the content to JSON structure first
                    metadata_dict = result.metadata.__dict__.copy()
                    # Convert DocumentFormat enum to string
                    if 'format' in metadata_dict and hasattr(metadata_dict['format'], 'value'):
                        metadata_dict['format'] = metadata_dict['format'].value

                    json_data = {
                        "content": [{"type": "text:normal", "content": result.content}],
                        "metadata": metadata_dict
                    }
                    result.content = self._format_content(json_data, output_format)

                return result

            elif extension in ['html', 'htm', 'xml', 'md', 'markdown']:
                # Import and use MarkupProcessor
                from doc2mark.formats.markup import MarkupProcessor
                processor = MarkupProcessor()

                # MarkupProcessor accepts encoding through kwargs
                processor_kwargs = {
                    'encoding': encoding
                }

                # Process with MarkupProcessor
                result = processor.process(
                    file_path,
                    **processor_kwargs
                )

                # Note: MarkupProcessor doesn't support image extraction/OCR
                # Though HTML/XML could theoretically contain image references,
                # the current implementation focuses on text content
                if extract_images and show_progress:
                    logger.info(f"Image extraction not implemented for {extension} format")

                # Apply output format conversion if needed
                if output_format and output_format != OutputFormat.MARKDOWN:
                    # Convert the content to JSON structure first
                    metadata_dict = result.metadata.__dict__.copy()
                    # Convert DocumentFormat enum to string
                    if 'format' in metadata_dict and hasattr(metadata_dict['format'], 'value'):
                        metadata_dict['format'] = metadata_dict['format'].value

                    json_data = {
                        "content": [{"type": "text:normal", "content": result.content}],
                        "metadata": metadata_dict
                    }
                    result.content = self._format_content(json_data, output_format)

                return result

            else:
                # This shouldn't happen if can_process is working correctly
                raise ProcessingError(f"Unsupported text format: {extension}")

        except ImportError as e:
            logger.error(f"Failed to import required processor: {e}")
            raise ProcessingError(f"Text processor not available: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to process text-based file: {e}")
            raise ProcessingError(f"Text processing failed: {str(e)}")

    def _extract_metadata(self, json_data: Dict[str, Any], file_path: Path, file_size: int) -> DocumentMetadata:
        """Extract metadata from JSON data."""
        # Determine format
        extension = file_path.suffix.lower().lstrip('.')
        format_map = {
            'docx': DocumentFormat.DOCX,
            'xlsx': DocumentFormat.XLSX,
            'pptx': DocumentFormat.PPTX,
            'doc': DocumentFormat.DOC,
            'xls': DocumentFormat.XLS,
            'ppt': DocumentFormat.PPT,
            'rtf': DocumentFormat.RTF,
            'pps': DocumentFormat.PPS,
            'pdf': DocumentFormat.PDF,
        }

        doc_format = format_map.get(extension, DocumentFormat.TXT)

        # Extract common metadata
        metadata = DocumentMetadata(
            filename=file_path.name,
            format=doc_format,
            size_bytes=file_size
        )

        # Extract format-specific metadata from JSON
        if 'pages' in json_data:
            metadata.page_count = json_data['pages']
        elif 'slides' in json_data:
            metadata.slide_count = json_data['slides']
            metadata.page_count = json_data['slides']  # For compatibility

        # Count words from content
        word_count = 0
        for item in json_data.get('content', []):
            if item.get('type', '').startswith('text:'):
                word_count += len(item.get('content', '').split())
        metadata.word_count = word_count

        return metadata

    def _extract_images_from_json(self, json_data: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Extract images from JSON content."""
        images = []

        for item in json_data.get('content', []):
            if item.get('type') == 'image':
                # Extract base64 data
                images.append({
                    'data': item.get('content', ''),
                    'type': 'base64'
                })

        return images if images else None

    def _json_to_markdown(self, json_data: Dict[str, Any]) -> str:
        """Convert JSON data to markdown string (from UnifiedMarkdownLoader)."""
        # Try to use office_to_markdown if available
        try:
            from doc2mark.pipelines import office_to_markdown
            return office_to_markdown(json_data)
        except:
            # Fallback implementation
            markdown_parts = []

            for item in json_data.get("content", []):
                item_type = item.get("type", "")
                content = item.get("content", "")

                if item_type == "text:title":
                    markdown_parts.append(f"# {content}\n")
                elif item_type == "text:section":
                    markdown_parts.append(f"## {content}\n")
                elif item_type == "text:list":
                    markdown_parts.append(f"{content}\n")
                elif item_type == "text:caption":
                    markdown_parts.append(f"*{content}*\n")
                elif item_type == "text:normal":
                    markdown_parts.append(f"{content}\n")
                elif item_type == "text:image_description":
                    # Handle OCR results with XML tags
                    ocr_text = content
                    if ocr_text.startswith('<image_ocr_result>') and ocr_text.endswith('</image_ocr_result>'):
                        ocr_text = ocr_text[18:-19]  # Remove tags
                    
                    markdown_parts.append("```xml")
                    markdown_parts.append("<ocr_result>")
                    markdown_parts.append(ocr_text)
                    markdown_parts.append("</ocr_result>")
                    markdown_parts.append("```\n")
                elif item_type == "table":
                    markdown_parts.append(content)
                elif item_type == "image":
                    markdown_parts.append(f'![Image](data:image/png;base64,{content})\n')

            return "\n".join(markdown_parts)

    def _format_content(self, json_data: Dict[str, Any], output_format: Union[str, OutputFormat]) -> str:
        """Format content according to the requested output format.
        
        Args:
            json_data: The JSON data from the pipeline
            output_format: The desired output format
            
        Returns:
            Formatted content string
        """
        # Handle string format
        if isinstance(output_format, str):
            output_format = output_format.lower()
            if output_format == 'markdown':
                output_format = OutputFormat.MARKDOWN
            elif output_format == 'json':
                output_format = OutputFormat.JSON
            elif output_format == 'text':
                output_format = OutputFormat.TEXT
            else:
                # Default to markdown for unknown formats
                logger.warning(f"Unknown output format: {output_format}, defaulting to markdown")
                output_format = OutputFormat.MARKDOWN

        # Convert based on format
        if output_format == OutputFormat.MARKDOWN:
            # Use existing conversion methods
            try:
                # Check if this is PDF data (has 'pages' key and no 'slides')
                if 'pages' in json_data and 'slides' not in json_data:
                    from doc2mark.pipelines import pdf_to_markdown
                    return pdf_to_markdown(json_data)
                else:
                    from doc2mark.pipelines import office_to_markdown
                    return office_to_markdown(json_data)
            except:
                return self._json_to_markdown(json_data)

        elif output_format == OutputFormat.JSON:
            # Return the JSON data as a formatted string
            import json
            return json.dumps(json_data, indent=2, ensure_ascii=False)

        elif output_format == OutputFormat.TEXT:
            # Convert to plain text
            text_parts = []
            for item in json_data.get("content", []):
                content = item.get("content", "")
                if content:
                    # Remove any XML-like tags
                    import re
                    content = re.sub(r'<[^>]+>', '', content)
                    text_parts.append(content)

            return "\n\n".join(text_parts)

        else:
            # Default to markdown
            logger.warning(f"Unsupported output format: {output_format}, defaulting to markdown")
            try:
                # Check if this is PDF data (has 'pages' key and no 'slides')
                if 'pages' in json_data and 'slides' not in json_data:
                    from doc2mark.pipelines import pdf_to_markdown
                    return pdf_to_markdown(json_data)
                else:
                    from doc2mark.pipelines import office_to_markdown
                    return office_to_markdown(json_data)
            except:
                return self._json_to_markdown(json_data)
