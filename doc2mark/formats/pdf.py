"""PDF format processor using advanced pipeline."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from doc2mark.core.base import (
    BaseProcessor,
    DocumentFormat,
    DocumentMetadata,
    ProcessedDocument,
    ProcessingError
)
from doc2mark.ocr.base import BaseOCR

logger = logging.getLogger(__name__)


class PDFProcessor(BaseProcessor):
    """Processor for PDF documents using advanced pipeline."""

    def __init__(self, ocr: Optional[BaseOCR] = None, table_style: Optional[str] = None):
        """Initialize PDF processor.
        
        Args:
            ocr: OCR provider for image/scanned PDFs
            table_style: Output style for complex tables:
                - 'minimal_html': Clean HTML with only rowspan/colspan (default)
                - 'markdown_grid': Markdown with merge annotations
                - 'styled_html': Full HTML with inline styles (legacy)
        """
        self.ocr = ocr
        self.table_style = table_style

    def can_process(self, file_path: Union[str, Path]) -> bool:
        """Check if this processor can handle the file."""
        file_path = Path(file_path)
        return file_path.suffix.lower() == '.pdf'

    def process(
            self,
            file_path: Union[str, Path],
            extract_images: bool = True,
            use_ocr: bool = False,
            extract_tables: bool = True,
            show_progress: bool = False,
            **kwargs
    ) -> ProcessedDocument:
        """Process PDF document using advanced pipeline.
        
        Args:
            file_path: Path to PDF file
            extract_images: Whether to extract images as base64
            use_ocr: Whether to perform OCR on images (requires extract_images=True)
            extract_tables: Whether to extract tables (always True for advanced pipeline)
            show_progress: Whether to show processing progress
            **kwargs: Additional parameters
            
        Returns:
            ProcessedDocument with advanced content extraction
        """
        file_path = Path(file_path)

        # Get file size
        file_size = file_path.stat().st_size

        try:
            # Import and use the advanced pipeline
            from doc2mark.pipelines import pdf_to_simple_json, pdf_to_markdown
            
            # Convert parameters
            ocr_images = use_ocr and extract_images  # OCR only if both are True
            
            # Process with advanced pipeline
            logger.info(f"Processing PDF with advanced pipeline: {file_path.name}")
            json_data = pdf_to_simple_json(
                pdf_path=file_path,
                extract_images=extract_images,
                ocr_images=ocr_images,
                show_progress=show_progress,
                ocr=self.ocr,  # Pass the OCR instance
                table_style=kwargs.get('table_style', self.table_style)  # Pass table style
            )
            
            # Convert to markdown using the advanced converter
            markdown_content = pdf_to_markdown(json_data)
            
            # Extract metadata
            metadata = self._extract_metadata_from_json(json_data, file_path, file_size)
            
            # Extract images if any
            images = self._extract_images_from_json(json_data)
            
            # Extract tables count (for metadata)
            tables_count = sum(1 for item in json_data.get('content', []) 
                             if item.get('type') == 'table')
            
            # Add table count to metadata
            if tables_count > 0:
                metadata.extra['tables_count'] = tables_count
            
            return ProcessedDocument(
                content=markdown_content,
                metadata=metadata,
                json_content=json_data.get('content', []),
                images=images
            )
            
        except ImportError:
            logger.warning("Advanced PDF pipeline not available, falling back to basic processing")
            # Fall back to basic processing if advanced pipeline not available
            return self._basic_process(file_path, extract_images, use_ocr, **kwargs)
        except Exception as e:
            logger.error(f"Failed to process PDF with advanced pipeline: {e}")
            raise ProcessingError(f"PDF processing failed: {str(e)}")

    def _extract_metadata_from_json(self, json_data: Dict[str, Any], file_path: Path, file_size: int) -> DocumentMetadata:
        """Extract metadata from JSON data produced by advanced pipeline."""
        # Count words from content
        word_count = 0
        for item in json_data.get('content', []):
            if item.get('type', '').startswith('text:'):
                word_count += len(item.get('content', '').split())
        
        # Build metadata
        metadata = DocumentMetadata(
            filename=file_path.name,
            format=DocumentFormat.PDF,
            size_bytes=file_size,
            page_count=json_data.get('pages', 0),
            word_count=word_count
        )
        
        return metadata

    def _extract_images_from_json(self, json_data: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Extract images from JSON content."""
        images = []
        
        for item in json_data.get('content', []):
            if item.get('type') == 'image':
                # Base64 image
                images.append({
                    'data': item.get('content', ''),
                    'type': 'base64'
                })
            elif item.get('type') == 'text:image_description':
                # OCR result
                ocr_text = item.get('content', '')
                if ocr_text.startswith('<image_ocr_result>') and ocr_text.endswith('</image_ocr_result>'):
                    ocr_text = ocr_text[18:-19]  # Remove tags
                
                images.append({
                    'text': ocr_text,
                    'type': 'ocr'
                })
        
        return images if images else None

    def _basic_process(self, file_path: Path, extract_images: bool, use_ocr: bool, **kwargs) -> ProcessedDocument:
        """Basic PDF processing as fallback."""
        # Import PyMuPDF
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ImportError(
                "PyMuPDF is not installed. "
                "Install it with: pip install PyMuPDF"
            )
        
        # Get file size
        file_size = file_path.stat().st_size
        
        # Open PDF
        try:
            pdf_doc = fitz.open(str(file_path))
        except Exception as e:
            raise ProcessingError(f"Failed to open PDF: {str(e)}")
        
        try:
            # Extract content
            markdown_parts = []
            total_words = 0
            
            for page_num, page in enumerate(pdf_doc):
                # Add page header
                markdown_parts.append(f"### Page {page_num + 1}")
                markdown_parts.append("")
                
                # Extract text
                text = page.get_text().strip()
                
                if text:
                    # Basic text formatting
                    formatted_text = self._format_pdf_text(text)
                    markdown_parts.append(formatted_text)
                    markdown_parts.append("")
                    total_words += len(text.split())
                elif use_ocr and self.ocr:
                    # OCR the page
                    try:
                        pix = page.get_pixmap(dpi=300)
                        img_data = pix.tobytes("png")
                        
                        ocr_result = self.ocr.process_image(img_data)
                        ocr_text = ocr_result.text if hasattr(ocr_result, 'text') else str(ocr_result)
                        
                        if ocr_text:
                            markdown_parts.append("```xml")
                            markdown_parts.append("<ocr_result>")
                            markdown_parts.append(ocr_text)
                            markdown_parts.append("</ocr_result>")
                            markdown_parts.append("```")
                            markdown_parts.append("")
                            total_words += len(ocr_text.split())
                    except Exception as e:
                        logger.warning(f"Failed to OCR page {page_num + 1}: {e}")
            
            # Get metadata
            metadata_dict = pdf_doc.metadata
            
            # Build document metadata
            doc_metadata = DocumentMetadata(
                filename=file_path.name,
                format=DocumentFormat.PDF,
                size_bytes=file_size,
                page_count=len(pdf_doc),
                word_count=total_words,
                title=metadata_dict.get('title'),
                author=metadata_dict.get('author'),
                creation_date=str(metadata_dict.get('creationDate')) if metadata_dict.get('creationDate') else None,
                modification_date=str(metadata_dict.get('modDate')) if metadata_dict.get('modDate') else None,
            )
            
            return ProcessedDocument(
                content='\n'.join(markdown_parts),
                metadata=doc_metadata
            )
            
        finally:
            pdf_doc.close()

    def _format_pdf_text(self, text: str) -> str:
        """Basic text formatting for fallback mode."""
        lines = text.split('\n')
        formatted_lines = []
        
        for line in lines:
            line = line.strip()
            if line:
                # Very basic formatting
                if line.isupper() and len(line) < 100:
                    # Likely a heading
                    formatted_lines.append(f"**{line}**")
                else:
                    formatted_lines.append(line)
        
        # Join lines
        result = []
        current_para = []
        
        for line in formatted_lines:
            if line:
                current_para.append(line)
            else:
                if current_para:
                    result.append(' '.join(current_para))
                    result.append('')
                    current_para = []
        
        if current_para:
            result.append(' '.join(current_para))
        
        return '\n'.join(result)
