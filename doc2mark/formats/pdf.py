"""PDF format processor."""

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
    """Processor for PDF documents."""

    def __init__(self, ocr: Optional[BaseOCR] = None):
        """Initialize PDF processor.
        
        Args:
            ocr: OCR provider for image/scanned PDFs
        """
        self.ocr = ocr
        self._pymupdf = None
        self._pdfplumber = None

    @property
    def pymupdf(self):
        """Lazy load PyMuPDF."""
        if self._pymupdf is None:
            try:
                import fitz  # PyMuPDF
                self._pymupdf = fitz
            except ImportError:
                raise ImportError(
                    "PyMuPDF is not installed. "
                    "Install it with: pip install PyMuPDF"
                )
        return self._pymupdf

    @property
    def pdfplumber(self):
        """Lazy load pdfplumber for table extraction."""
        if self._pdfplumber is None:
            try:
                import pdfplumber
                self._pdfplumber = pdfplumber
            except ImportError:
                logger.warning(
                    "pdfplumber is not installed. Table extraction will be limited. "
                    "Install it with: pip install pdfplumber"
                )
        return self._pdfplumber

    def can_process(self, file_path: Union[str, Path]) -> bool:
        """Check if this processor can handle the file."""
        file_path = Path(file_path)
        return file_path.suffix.lower() == '.pdf'

    def process(
            self,
            file_path: Union[str, Path],
            **kwargs
    ) -> ProcessedDocument:
        """Process PDF document."""
        file_path = Path(file_path)

        # Get file size
        file_size = file_path.stat().st_size

        # Open PDF
        try:
            pdf_doc = self.pymupdf.open(str(file_path))
        except Exception as e:
            raise ProcessingError(f"Failed to open PDF: {str(e)}")

        try:
            # Extract content
            markdown_parts = []
            images_extracted = []
            total_words = 0

            # Collect all pages needing OCR for batch processing
            ocr_batch = []
            page_info = []

            # First pass: collect text and pages needing OCR
            for page_num, page in enumerate(pdf_doc):
                page_content = []

                # Try to extract text
                text = page.get_text().strip()

                if text:
                    # Page has extractable text
                    page_content.append(f"### Page {page_num + 1}")
                    page_content.append("")

                    # Clean and format text
                    formatted_text = self._format_pdf_text(text)
                    page_content.append(formatted_text)

                    # Count words
                    total_words += len(text.split())

                    # Extract tables if pdfplumber is available
                    if self.pdfplumber and kwargs.get('extract_tables', True):
                        tables = self._extract_tables_from_page(file_path, page_num)
                        for table in tables:
                            page_content.append("")
                            page_content.append(table)

                else:
                    # Page might be scanned/image-based - collect for batch OCR
                    if self.ocr and kwargs.get('use_ocr', True):
                        # Convert page to image for batch processing
                        try:
                            pix = page.get_pixmap(dpi=300)
                            img_data = pix.tobytes("png")
                            ocr_batch.append(img_data)
                            page_info.append({
                                'page_num': page_num,
                                'page_content': page_content
                            })
                        except Exception as e:
                            logger.warning(f"Failed to convert page {page_num + 1} to image: {e}")

                # Store page content for text-based pages
                if text:
                    page_info.append({
                        'page_num': page_num,
                        'page_content': page_content,
                        'has_text': True
                    })

                # Extract images if requested - collect for batch OCR
                if kwargs.get('extract_images', False):
                    images = self._extract_images_from_page_batch(page, page_num, ocr_batch, page_info)
                    images_extracted.extend(images)

            # Batch process OCR if needed
            ocr_results = []
            if ocr_batch and self.ocr:
                logger.info(f"🚀 Processing {len(ocr_batch)} pages/images with batch OCR")

                # Prepare OCR kwargs with language configuration if available
                ocr_kwargs = {}
                if hasattr(self.ocr, 'config') and self.ocr.config and self.ocr.config.language:
                    ocr_kwargs['language'] = self.ocr.config.language
                    logger.info(f"🌍 Passing language configuration to PDF batch OCR: {self.ocr.config.language}")

                try:
                    ocr_results = self.ocr.batch_process_images(ocr_batch, **ocr_kwargs)
                    logger.info(f"✅ Batch OCR completed successfully")
                except Exception as e:
                    logger.error(f"❌ Batch OCR failed: {e}")
                    ocr_results = []

            # Second pass: build final content with OCR results
            ocr_index = 0
            for info in page_info:
                page_num = info['page_num']
                page_content = info['page_content']

                if info.get('has_text'):
                    # Text-based page, add as-is
                    if page_content:
                        markdown_parts.extend(page_content)
                        markdown_parts.append("")
                else:
                    # OCR-based page
                    if ocr_index < len(ocr_results):
                        ocr_result = ocr_results[ocr_index]
                        ocr_text = ocr_result.text if hasattr(ocr_result, 'text') else str(ocr_result)

                        if ocr_text:
                            page_content.append(f"### Page {page_num + 1} (OCR)")
                            page_content.append("")
                            page_content.append(ocr_text)
                            total_words += len(ocr_text.split())

                            markdown_parts.extend(page_content)
                            markdown_parts.append("")

                        ocr_index += 1

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

            # Create processed document
            return ProcessedDocument(
                content='\n'.join(markdown_parts),
                metadata=doc_metadata,
                images=images_extracted if images_extracted else None
            )

        finally:
            pdf_doc.close()

    def _format_pdf_text(self, text: str) -> str:
        """Format PDF text for better readability."""
        lines = text.split('\n')
        formatted_lines = []

        for line in lines:
            line = line.strip()
            if line:
                # Simple heuristics for formatting
                # Could be enhanced with more sophisticated logic
                if line.isupper() and len(line) < 100:
                    # Likely a heading
                    formatted_lines.append(f"**{line}**")
                else:
                    formatted_lines.append(line)

        # Join lines, preserving paragraph breaks
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

    def _ocr_pdf_page(self, page, **kwargs) -> str:
        """OCR a PDF page."""
        try:
            # Render page to image
            pix = page.get_pixmap(dpi=300)
            img_data = pix.tobytes("png")

            # Prepare OCR kwargs with language configuration if available
            ocr_kwargs = {}
            if hasattr(self.ocr, 'config') and self.ocr.config and self.ocr.config.language:
                ocr_kwargs['language'] = self.ocr.config.language
                logger.info(f"🌍 Passing language configuration to PDF OCR: {self.ocr.config.language}")

            # Add any additional kwargs passed from process method
            ocr_kwargs.update(kwargs)

            # OCR the image
            ocr_result = self.ocr.process_image(img_data, **ocr_kwargs)
            return ocr_result.text

        except Exception as e:
            logger.error(f"Failed to OCR PDF page: {e}")
            return ""

    def _extract_tables_from_page(self, file_path: Path, page_num: int) -> List[str]:
        """Extract tables from a PDF page using pdfplumber."""
        tables = []

        try:
            with self.pdfplumber.open(str(file_path)) as pdf:
                page = pdf.pages[page_num]
                page_tables = page.extract_tables()

                for table in page_tables:
                    if table and len(table) > 1:
                        # Convert to markdown table
                        table_md = self._convert_table_to_markdown(table)
                        tables.append(table_md)

        except Exception as e:
            logger.warning(f"Failed to extract tables from page {page_num}: {e}")

        return tables

    def _convert_table_to_markdown(self, table: List[List[Any]]) -> str:
        """Convert extracted table to markdown format."""
        if not table or not table[0]:
            return ""

        # Clean table data
        cleaned_table = []
        for row in table:
            cleaned_row = [str(cell) if cell is not None else "" for cell in row]
            cleaned_table.append(cleaned_row)

        # Determine column widths
        col_widths = [0] * len(cleaned_table[0])
        for row in cleaned_table:
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(cell))

        # Build markdown table
        lines = []

        # Header
        header = "| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(cleaned_table[0])) + " |"
        lines.append(header)

        # Separator
        separator = "|" + "|".join("-" * (w + 2) for w in col_widths) + "|"
        lines.append(separator)

        # Data rows
        for row in cleaned_table[1:]:
            row_str = "| " + " | ".join(
                cell.ljust(col_widths[i]) if i < len(row) else "".ljust(col_widths[i])
                for i in range(len(col_widths))
            ) + " |"
            lines.append(row_str)

        return '\n'.join(lines)

    def _extract_images_from_page_batch(self, page, page_num: int, ocr_batch: List[bytes], page_info: List[Dict]) -> \
    List[Dict[str, Any]]:
        """Extract images from a PDF page for batch OCR processing."""
        images = []

        try:
            image_list = page.get_images()

            for img_index, img in enumerate(image_list):
                try:
                    # Extract image
                    xref = img[0]
                    pix = self.pymupdf.Pixmap(page.parent, xref)

                    if pix.n - pix.alpha < 4:  # GRAY or RGB
                        img_data = pix.tobytes("png")
                    else:  # CMYK
                        pix = self.pymupdf.Pixmap(self.pymupdf.csRGB, pix)
                        img_data = pix.tobytes("png")

                    # Add to batch for later OCR processing
                    if self.ocr:
                        ocr_batch.append(img_data)
                        images.append({
                            'page': page_num + 1,
                            'index': img_index,
                            'text': '',  # Will be filled in batch processing
                            'size': len(img_data),
                            'ocr_batch_index': len(ocr_batch) - 1  # Track position in batch
                        })
                    else:
                        images.append({
                            'page': page_num + 1,
                            'index': img_index,
                            'text': '',
                            'size': len(img_data)
                        })

                except Exception as e:
                    logger.warning(f"Failed to extract image {img_index} from page {page_num}: {e}")

        except Exception as e:
            logger.warning(f"Failed to extract images from page {page_num}: {e}")

        return images

    def _extract_images_from_page(self, page, page_num: int) -> List[Dict[str, Any]]:
        """Extract images from a PDF page with individual OCR (fallback method)."""
        images = []

        try:
            image_list = page.get_images()

            # Collect images for batch processing if multiple images
            if len(image_list) > 1 and self.ocr:
                image_data_list = []
                image_info_list = []

                for img_index, img in enumerate(image_list):
                    try:
                        # Extract image
                        xref = img[0]
                        pix = self.pymupdf.Pixmap(page.parent, xref)

                        if pix.n - pix.alpha < 4:  # GRAY or RGB
                            img_data = pix.tobytes("png")
                        else:  # CMYK
                            pix = self.pymupdf.Pixmap(self.pymupdf.csRGB, pix)
                            img_data = pix.tobytes("png")

                        image_data_list.append(img_data)
                        image_info_list.append({
                            'page': page_num + 1,
                            'index': img_index,
                            'size': len(img_data)
                        })

                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_index} from page {page_num}: {e}")

                # Batch OCR if we have images
                if image_data_list:
                    try:
                        # Prepare OCR kwargs with language configuration if available
                        ocr_kwargs = {}
                        if hasattr(self.ocr, 'config') and self.ocr.config and self.ocr.config.language:
                            ocr_kwargs['language'] = self.ocr.config.language

                        logger.info(f"🚀 Processing {len(image_data_list)} images with batch OCR on page {page_num + 1}")
                        ocr_results = self.ocr.batch_process_images(image_data_list, **ocr_kwargs)

                        # Combine results
                        for info, ocr_result in zip(image_info_list, ocr_results):
                            text = ocr_result.text if hasattr(ocr_result, 'text') else str(ocr_result)
                            info['text'] = text
                            images.append(info)

                    except Exception as e:
                        logger.warning(f"Failed to batch OCR images on page {page_num}: {e}")
                        # Add images without OCR text
                        for info in image_info_list:
                            info['text'] = ''
                            images.append(info)
            else:
                # Single image or no OCR - process individually
                for img_index, img in enumerate(image_list):
                    try:
                        # Extract image
                        xref = img[0]
                        pix = self.pymupdf.Pixmap(page.parent, xref)

                        if pix.n - pix.alpha < 4:  # GRAY or RGB
                            img_data = pix.tobytes("png")
                        else:  # CMYK
                            pix = self.pymupdf.Pixmap(self.pymupdf.csRGB, pix)
                            img_data = pix.tobytes("png")

                        # OCR if available
                        text = ""
                        if self.ocr:
                            try:
                                # Prepare OCR kwargs with language configuration if available
                                ocr_kwargs = {}
                                if hasattr(self.ocr, 'config') and self.ocr.config and self.ocr.config.language:
                                    ocr_kwargs['language'] = self.ocr.config.language

                                ocr_result = self.ocr.process_image(img_data, **ocr_kwargs)
                                text = ocr_result.text
                            except Exception as e:
                                logger.warning(f"Failed to OCR image: {e}")

                        images.append({
                            'page': page_num + 1,
                            'index': img_index,
                            'text': text,
                            'size': len(img_data)
                        })

                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_index} from page {page_num}: {e}")

        except Exception as e:
            logger.warning(f"Failed to extract images from page {page_num}: {e}")

        return images
