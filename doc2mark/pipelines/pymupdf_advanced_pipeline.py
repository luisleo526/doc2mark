import base64
import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Any, Union, Optional, Tuple

import pymupdf

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import TableStyle from office pipeline (shared enum)
try:
    from doc2mark.pipelines.office_advanced_pipeline import TableStyle
except ImportError:
    # Fallback definition if office pipeline not available
    class TableStyle(Enum):
        MINIMAL_HTML = "minimal_html"
        MARKDOWN_GRID = "markdown_grid"
        STYLED_HTML = "styled_html"
        
        @classmethod
        def default(cls):
            return cls.MINIMAL_HTML


@dataclass
class SimpleContent:
    """Simple content item with type and data"""
    type: str  # 'text:title', 'text:section', 'text:normal', 'text:list', 'text:caption', 'table', or 'image'
    content: str  # markdown text, markdown table, or base64 data
    page: int
    position_y: float  # For sorting


class PDFLoader:
    """PDF loader that extracts content in reading order and exports to various formats"""

    def __init__(self, pdf_path: Union[str, Path], ocr=None, table_style: Union[str, TableStyle] = None):
        self.pdf_path = Path(pdf_path)
        self.doc = None
        self.ocr = ocr  # Store the OCR instance
        
        # Set table output style
        if table_style is None:
            self.table_style = TableStyle.default()
        elif isinstance(table_style, str):
            self.table_style = TableStyle(table_style)
        else:
            self.table_style = table_style

        # Log OCR configuration if available
        if self.ocr:
            logger.info(f"📷 OCR configured for PDFLoader: {type(self.ocr).__name__}")
            if hasattr(self.ocr, 'config') and self.ocr.config and self.ocr.config.language:
                logger.info(f"🌍 OCR Language setting: {self.ocr.config.language}")

        self._open_document()

    def _open_document(self):
        """Open PDF document with error handling"""
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {self.pdf_path}")

        try:
            self.doc = pymupdf.open(self.pdf_path)

            # Log PDF configuration
            logger.info("=" * 60)
            logger.info(f"PDF Configuration for: {self.pdf_path.name}")
            logger.info("=" * 60)
            logger.info(f"File path: {self.pdf_path}")
            logger.info(f"File size: {self.pdf_path.stat().st_size / (1024 * 1024):.2f} MB")
            logger.info(f"Total pages: {len(self.doc)}")

            # Count total images in the PDF
            total_images = 0
            images_per_page = []
            for page_num in range(len(self.doc)):
                page = self.doc.load_page(page_num)
                images = page.get_images(full=True)
                num_images = len(images)
                total_images += num_images
                if num_images > 0:
                    images_per_page.append(f"Page {page_num + 1}: {num_images} images")

            logger.info(f"Total images: {total_images}")
            if images_per_page and len(images_per_page) <= 10:
                # Show per-page breakdown if not too many pages with images
                for page_info in images_per_page:
                    logger.info(f"  {page_info}")
            elif images_per_page:
                logger.info(f"  Images found on {len(images_per_page)} pages")

            # Log metadata if available
            metadata = self.doc.metadata
            if metadata:
                logger.info("PDF Metadata:")
                for key, value in metadata.items():
                    if value:
                        logger.info(f"  {key}: {value}")

            # Log PDF version and encryption status
            # Try to get PDF version from various possible attributes
            pdf_version = "Unknown"
            if hasattr(self.doc, 'pdf_version'):
                pdf_version = self.doc.pdf_version
            elif hasattr(self.doc, 'version'):
                pdf_version = self.doc.version
            elif metadata and 'format' in metadata:
                pdf_version = metadata['format']

            logger.info(f"PDF version: {pdf_version}")

            # Check encryption status
            is_encrypted = False
            if hasattr(self.doc, 'is_encrypted'):
                is_encrypted = self.doc.is_encrypted
            elif hasattr(self.doc, 'isEncrypted'):
                is_encrypted = self.doc.isEncrypted
            elif hasattr(self.doc, 'needs_pass'):
                is_encrypted = self.doc.needs_pass

            logger.info(f"Encrypted: {is_encrypted}")
            logger.info("=" * 60)

        except Exception as e:
            logger.error(f"Failed to open PDF: {e}")
            raise

    def convert_to_json(self,
                        extract_images: bool = True,
                        ocr_images: bool = False,
                        show_progress: bool = True) -> Dict[str, Any]:
        """
        Convert PDF to simplified JSON format with content in reading order
        
        Args:
            extract_images: Whether to extract images as base64
            ocr_images: Whether to use OCR to convert images to text descriptions (requires extract_images=True)
            show_progress: Whether to show progress messages
        
        Returns:
            Simplified JSON with content array containing:
            - text:title - Main document title
            - text:section - Section headers (larger fonts)
            - text:normal - Regular paragraph text
            - text:list - Bullet points or numbered lists
            - text:caption - Figure/table captions (smaller text near images/tables)
            - text:image_description - OCR-generated image descriptions (when ocr_images=True)
            - table - Tables with complex structure support:
                * Simple tables: Markdown format with span annotations (*[2x3]* for merged cells)
                * Complex tables: HTML format preserving rowspan/colspan attributes
                * Line breaks in cells preserved using <br> tags
                * Automatic detection and labeling of merged cells
            - image - Base64-encoded images (when ocr_images=False)
        """
        # Initialize document structure
        document = {
            "filename": self.pdf_path.name,
            "pages": len(self.doc),
            "content": []  # Simple array of content items
        }

        # If OCR is requested, collect all images first for batch processing
        ocr_results_map = {}
        if extract_images and ocr_images:
            if show_progress:
                logger.info("Collecting all images for batch OCR processing...")

            all_images_info = self._collect_all_images()

            if all_images_info:
                if show_progress:
                    logger.info(f"Processing {len(all_images_info)} images with batch OCR...")

                # Prepare batch for OCR
                ocr_batch = [{"image_data": info["base64"]} for info in all_images_info]

                try:
                    # Use the configured OCR instance for batch processing
                    if self.ocr:
                        # Prepare image data for batch processing
                        image_data_list = [base64.b64decode(info["base64"]) for info in all_images_info]

                        # Pass language configuration if available
                        kwargs = {}
                        if hasattr(self.ocr, 'config') and self.ocr.config and self.ocr.config.language:
                            kwargs['language'] = self.ocr.config.language
                            logger.info(f"🌍 Passing language configuration to OCR: {self.ocr.config.language}")

                        # Always use batch processing for efficiency
                        logger.info(f"🚀 Using batch OCR processing for {len(image_data_list)} images")
                        ocr_results = self.ocr.batch_process_images(image_data_list, **kwargs)

                        # Extract text from results
                        ocr_texts = []
                        for result in ocr_results:
                            if hasattr(result, 'text'):
                                ocr_texts.append(result.text)
                            else:
                                ocr_texts.append(str(result))

                        # Map results back to image locations
                        for info, ocr_text in zip(all_images_info, ocr_texts):
                            key = (info["page_num"], info["xref"])
                            ocr_results_map[key] = ocr_text

                        if show_progress:
                            logger.info(f"Successfully processed {len(ocr_texts)} images with configured OCR")
                    else:
                        logger.error("No OCR instance available")
                        ocr_images = False  # Disable OCR processing

                except Exception as e:
                    logger.error(f"Batch OCR processing failed: {e}")
                    ocr_images = False  # Fall back to base64 extraction

        # Process each page
        for page_num in range(len(self.doc)):
            if show_progress:
                logger.info(f"Processing page {page_num + 1}/{len(self.doc)}")

            page_content = self._process_page(
                page_num,
                extract_images=extract_images,
                ocr_images=ocr_images,
                ocr_results_map=ocr_results_map  # Pass pre-computed OCR results
            )

            # Add page content to document
            document["content"].extend(page_content)

        return document

    def _collect_all_images(self) -> List[Dict[str, Any]]:
        """Collect all images from all pages for batch processing
        
        Returns:
            List of dictionaries containing image info:
            - page_num: Page number (0-indexed)
            - xref: Image cross-reference
            - base64: Base64-encoded image data
            - position: (x0, y0, x1, y1) tuple
        """
        all_images = []

        for page_num in range(len(self.doc)):
            page = self.doc.load_page(page_num)
            image_list = page.get_images(full=True)

            for img_info in image_list:
                xref = img_info[0]

                try:
                    # Extract image
                    base_image = self.doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    base64_data = base64.b64encode(image_bytes).decode('utf-8')

                    # Get image positions on page
                    img_rects = page.get_image_rects(xref)

                    for img_rect in img_rects:
                        all_images.append({
                            "page_num": page_num,
                            "xref": xref,
                            "base64": base64_data,
                            "position": (img_rect.x0, img_rect.y0, img_rect.x1, img_rect.y1)
                        })

                except Exception as e:
                    logger.warning(f"Failed to extract image {xref} on page {page_num + 1}: {e}")

        return all_images

    def _process_page(self, page_num: int, extract_images: bool = True, ocr_images: bool = False,
                      ocr_results_map: Dict[tuple, str] = None) -> List[Dict[str, Any]]:
        """Process a single page and extract content in reading order"""
        page = self.doc.load_page(page_num)
        content_items = []

        # Extract tables first (to avoid duplicating their text in text blocks)
        table_items, table_bboxes = self._extract_tables_as_markdown(page, page_num)
        content_items.extend(table_items)

        # Extract text blocks (excluding areas covered by tables)
        text_items = self._extract_text_as_markdown(page, page_num, table_bboxes)
        content_items.extend(text_items)

        # Extract images
        if extract_images:
            image_items = self._extract_images_simple(page, page_num, ocr_images=ocr_images,
                                                      ocr_results_map=ocr_results_map)
            content_items.extend(image_items)

        # Sort by vertical position to maintain reading order
        content_items.sort(key=lambda x: x.position_y)

        # Convert to simple format
        simple_content = []
        for item in content_items:
            if item.type.startswith("text:"):
                simple_content.append({
                    "type": item.type,
                    "content": item.content
                })
            elif item.type == "table":
                simple_content.append({
                    "type": "table",
                    "content": item.content  # markdown table
                })
            elif item.type == "image":
                simple_content.append({
                    "type": "image",
                    "content": item.content  # base64 data
                })

        return simple_content

    def _extract_text_as_markdown(self, page, page_num: int, table_bboxes: List[tuple] = None) -> List[SimpleContent]:
        """Extract text blocks and convert to markdown format with text type classification"""
        text_items = []
        table_bboxes = table_bboxes or []

        # Get text dictionary with formatting info
        text_dict = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_LIGATURES)

        # First pass: collect all font sizes to determine averages
        all_font_sizes = []
        for block in text_dict["blocks"]:
            if block["type"] == 0:  # Text block
                for line in block["lines"]:
                    for span in line["spans"]:
                        if span["size"] > 0:
                            all_font_sizes.append(span["size"])

        # Calculate font size statistics
        if all_font_sizes:
            avg_font_size = sum(all_font_sizes) / len(all_font_sizes)
            max_font_size = max(all_font_sizes)
        else:
            avg_font_size = 12
            max_font_size = 12

        # Get image positions for caption detection
        image_bboxes = self._get_image_bboxes(page)

        for block in text_dict["blocks"]:
            if block["type"] == 0:  # Text block
                # Skip if this text block is inside a table bbox
                block_bbox = block["bbox"]
                is_in_table = False
                for table_bbox in table_bboxes:
                    if self._bbox_overlaps(block_bbox, table_bbox):
                        is_in_table = True
                        break

                if not is_in_table:
                    # Analyze block and determine text type
                    markdown_text, text_type = self._convert_block_to_markdown_with_type(
                        block, avg_font_size, max_font_size, page_num, image_bboxes, table_bboxes
                    )

                    if markdown_text.strip():  # Only add non-empty text
                        # Debug logging for missing content
                        if "demonstrates" in markdown_text.lower():
                            logger.info(f"Found 'demonstrates' text: {markdown_text.strip()[:100]}... (type: {text_type})")
                        
                        text_items.append(SimpleContent(
                            type=text_type,
                            content=markdown_text,
                            page=page_num + 1,
                            position_y=block["bbox"][1]
                        ))

        return text_items

    def _get_image_bboxes(self, page) -> List[tuple]:
        """Get all image bounding boxes on the page"""
        image_bboxes = []
        try:
            image_list = page.get_images(full=True)
            for img_info in image_list:
                xref = img_info[0]
                try:
                    img_rects = page.get_image_rects(xref)
                    for img_rect in img_rects:
                        image_bboxes.append((img_rect.x0, img_rect.y0, img_rect.x1, img_rect.y1))
                except:
                    pass
        except:
            pass
        return image_bboxes

    def _is_near_image_or_table(self, bbox: tuple, image_bboxes: List[tuple], table_bboxes: List[tuple],
                                threshold: float = 50) -> bool:
        """Check if text is near an image or table (potential caption)"""
        x0, y0, x1, y1 = bbox
        text_center_x = (x0 + x1) / 2

        # Check proximity to images
        for img_bbox in image_bboxes:
            img_x0, img_y0, img_x1, img_y1 = img_bbox
            img_center_x = (img_x0 + img_x1) / 2

            # Check if text is below or above image and reasonably aligned
            vertical_distance = min(abs(y0 - img_y1), abs(img_y0 - y1))
            horizontal_overlap = min(x1, img_x1) - max(x0, img_x0)
            center_distance = abs(text_center_x - img_center_x)

            if vertical_distance < threshold and (horizontal_overlap > 0 or center_distance < 100):
                return True

        # Check proximity to tables
        for table_bbox in table_bboxes:
            table_x0, table_y0, table_x1, table_y1 = table_bbox
            table_center_x = (table_x0 + table_x1) / 2

            # Check if text is above or below table and reasonably aligned
            vertical_distance = min(abs(y0 - table_y1), abs(table_y0 - y1))
            horizontal_overlap = min(x1, table_x1) - max(x0, table_x0)
            center_distance = abs(text_center_x - table_center_x)

            if vertical_distance < threshold and (horizontal_overlap > 0 or center_distance < 100):
                return True

        return False

    def _convert_block_to_markdown_with_type(self, block: Dict[str, Any], avg_font_size: float, max_font_size: float,
                                             page_num: int, image_bboxes: List[tuple], table_bboxes: List[tuple]) -> \
            Tuple[str, str]:
        """Convert a text block to markdown format and determine its type"""
        lines = []

        # Analyze block characteristics
        block_max_size = 0
        block_min_size = float('inf')
        has_list_pattern = False
        list_line_count = 0
        total_text = ""
        is_bold = False
        is_all_caps = True
        line_count = 0

        for line in block["lines"]:
            line_text = ""
            line_size = 0

            for span in line["spans"]:
                line_text += span["text"]
                line_size = max(line_size, span["size"])
                is_bold = is_bold or (span["flags"] & pymupdf.TEXT_FONT_BOLD)

            if line_text.strip():
                total_text += line_text.strip() + " "
                block_max_size = max(block_max_size, line_size)
                block_min_size = min(block_min_size, line_size)
                line_count += 1

                # Check if not all caps
                if not line_text.isupper() or not any(c.isalpha() for c in line_text):
                    is_all_caps = False

                # Check for list patterns (expanded set of markers)
                if re.match(
                        r'^([\u2022•\-\*\u2013\u2014\u25AA\u25AB\u25CF\u25CB\u25A0\u25A1]|\d+[\.\)]|[a-zA-Z][\.\)])\s+',
                        line_text.strip()):
                    has_list_pattern = True
                    list_line_count += 1

        total_text = total_text.strip()

        # Caption patterns
        caption_patterns = [
            r'^(Figure|Fig\.?|Table|Tbl\.?|Chart|Graph|Image|Plate|Scheme)\s*\d*[\.:)]?',
            r'^(Source|Note|Notes)[\.:)]',
            r'^\d+\.\d+[\.:)]?',  # Numbered captions like "1.1:" or "2.3."
        ]

        is_caption_pattern = any(re.match(pattern, total_text, re.IGNORECASE) for pattern in caption_patterns)

        # Determine text type based on characteristics
        text_type = "text:normal"  # Default

        # Check if it's a caption (various criteria)
        if is_caption_pattern or \
                (self._is_near_image_or_table(block["bbox"], image_bboxes, table_bboxes) and
                 (len(total_text) < 150 or block_max_size < avg_font_size)):
            text_type = "text:caption"
        # Check if it's a title (very large font on first few pages)
        elif page_num <= 1 and block_max_size >= max_font_size * 0.85 and len(total_text) < 200 and line_count <= 3:
            text_type = "text:title"
        # Check if it's a section header (various criteria)
        elif (len(total_text) < 100 and line_count <= 2) and \
                (block_max_size > avg_font_size * 1.2 or
                 (is_bold and block_max_size > avg_font_size * 1.05) or
                 is_all_caps):
            text_type = "text:section"
        # Check if it's a list (majority of lines have list pattern)
        elif has_list_pattern and (list_line_count >= line_count * 0.5 or line_count == 1):
            text_type = "text:list"

        # Generate markdown
        markdown_text = self._convert_block_to_markdown(block)

        # Debug logging for classification
        if text_type != "text:normal":
            logger.debug(
                f"Classified as {text_type}: '{total_text[:50]}...' (size: {block_max_size:.1f}, avg: {avg_font_size:.1f})")

        return markdown_text, text_type

    def _convert_block_to_markdown(self, block: Dict[str, Any]) -> str:
        """Convert a text block to markdown format"""
        lines = []

        # Analyze font sizes to detect headers
        font_sizes = []
        for line in block["lines"]:
            for span in line["spans"]:
                font_sizes.append(span["size"])

        avg_size = sum(font_sizes) / len(font_sizes) if font_sizes else 12

        for line in block["lines"]:
            line_text = ""
            line_size = 0
            is_bold = False
            is_italic = False

            # Combine spans in the line
            for span in line["spans"]:
                line_text += span["text"]
                line_size = span["size"]
                is_bold = is_bold or (span["flags"] & pymupdf.TEXT_FONT_BOLD)
                is_italic = is_italic or (span["flags"] & pymupdf.TEXT_FONT_ITALIC)

            line_text = line_text.strip()
            if not line_text:
                continue

            # First check if this is a list item BEFORE applying any formatting
            list_match = re.match(
                r'^([\u2022•\-\*\u2013\u2014\u25AA\u25AB\u25CF\u25CB\u25A0\u25A1]|\d+[\.\)]|[a-zA-Z][\.\)])\s+',
                line_text)
            
            if list_match:
                # Handle list items without applying text formatting
                marker = list_match.group(1)
                if marker in '•\u2022\u25CF\u25AA\u25A0' or marker == '-' or marker == '*':
                    # Bullet point
                    markdown_line = re.sub(r'^[\u2022•\-\*\u2013\u2014\u25AA\u25AB\u25CF\u25CB\u25A0\u25A1]\s+', '- ',
                                           line_text)
                elif re.match(r'\d+[\.\)]', marker):
                    # Numbered list
                    markdown_line = re.sub(r'^(\d+)[\.\)]\s+', r'\1. ', line_text)
                else:
                    # Letter list (a., b., etc.) - convert to bullet
                    markdown_line = re.sub(r'^[a-zA-Z][\.\)]\s+', '- ', line_text)
            # Detect headers based on size
            elif line_size > avg_size * 1.5:
                # Large text -> H1
                markdown_line = f"# {line_text}"
            elif line_size > avg_size * 1.3:
                # Medium large text -> H2
                markdown_line = f"## {line_text}"
            elif line_size > avg_size * 1.15:
                # Slightly larger text -> H3
                markdown_line = f"### {line_text}"
            else:
                # Regular text
                markdown_line = line_text

                # Apply bold/italic formatting only for non-list items
                if is_bold and is_italic:
                    markdown_line = f"***{markdown_line}***"
                elif is_bold:
                    markdown_line = f"**{markdown_line}**"
                elif is_italic:
                    markdown_line = f"*{markdown_line}*"

            lines.append(markdown_line)

        # Join lines with appropriate spacing
        return "\n".join(lines) + "\n"

    def _bbox_overlaps(self, bbox1: tuple, bbox2: tuple) -> bool:
        """Check if two bounding boxes overlap"""
        x0_1, y0_1, x1_1, y1_1 = bbox1
        x0_2, y0_2, x1_2, y1_2 = bbox2

        # Check if one rectangle is to the left of the other
        if x1_1 < x0_2 or x1_2 < x0_1:
            return False

        # Check if one rectangle is above the other
        if y1_1 < y0_2 or y1_2 < y0_1:
            return False

        return True

    def _extract_tables_as_markdown(self, page, page_num: int) -> Tuple[List[SimpleContent], List[Tuple]]:
        """Extract tables and convert to markdown format"""
        table_items = []
        table_bboxes = []

        try:
            tables = page.find_tables()
            if hasattr(tables, 'tables'):
                for table_idx, table in enumerate(tables.tables):
                    # Store table bbox for excluding from text extraction
                    table_bboxes.append(tuple(table.bbox))

                    # Extract table content with enhanced cell analysis
                    markdown_table = self._convert_table_to_markdown_enhanced(table)

                    if markdown_table.strip():
                        table_items.append(SimpleContent(
                            type="table",  # Table type for better identification
                            content=markdown_table,
                            page=page_num + 1,
                            position_y=table.bbox[1]
                        ))

        except AttributeError as e:
            logger.debug("Table extraction not available in this PyMuPDF version")
        except Exception as e:
            logger.warning(f"Failed to extract tables: {e}")

        return table_items, table_bboxes

    def _convert_table_to_markdown_enhanced(self, table) -> str:
        """Enhanced table conversion with better merged cell detection using cell boundaries"""
        if not table:
            return ""

        try:
            # Try to extract with manual cell-by-cell extraction to avoid overlapping text issues
            extracted_data = self._extract_table_with_dedup(table)
            
            # Fallback to standard extract if manual extraction fails
            if not extracted_data or not any(extracted_data):
                extracted_data = table.extract()
                if not extracted_data or not any(extracted_data):
                    return ""
            
            # Use boundary-based analysis for better merge detection
            table_info = self._analyze_table_with_boundaries(table, extracted_data)
            
            if table_info['is_complex']:
                return self._convert_table_to_html(extracted_data, table_info)
            else:
                return self._convert_table_to_simple_markdown(extracted_data, table_info)

        except Exception as e:
            logger.warning(f"Failed to convert table to markdown: {e}")
            # Fallback to original method
            return self._convert_table_to_markdown(table)

    def _extract_table_with_dedup(self, table) -> List[List]:
        """
        Extract table data cell-by-cell with deduplication of overlapping text spans.
        
        Some PDFs (especially from design software like Adobe Illustrator) have overlapping 
        text layers, which causes garbled text extraction. For example:
        - '3853 8/ 54 9/ 11 /4 015 405' instead of '385 / 491 / 1 405'
        - '11 119933--112 24488' instead of '1 193-1 248'
        
        This method extracts text from each cell's bbox individually and deduplicates 
        overlapping text spans by keeping the longest/most complete version.
        
        Returns:
            Cleaned table data or None if extraction fails (triggers fallback)
        """
        try:
            # Get the standard extraction first to know the table structure
            standard_data = table.extract()
            if not standard_data:
                return []
            
            # Get the page object to extract text
            if not hasattr(table, 'page'):
                # Can't get page, fallback to standard extraction
                return standard_data
            
            page = table.page
            
            # Check if we have rows with cell bbox info
            if not hasattr(table, 'rows') or not table.rows:
                # No row info available, fallback
                return standard_data
            
            # Extract text cell-by-cell with deduplication
            cleaned_data = []
            for row_idx, table_row in enumerate(table.rows):
                row_data = []
                
                # Get the standard row data
                std_row = standard_data[row_idx] if row_idx < len(standard_data) else []
                
                # Get cells for this row
                if hasattr(table_row, 'cells') and table_row.cells:
                    for col_idx, cell_bbox in enumerate(table_row.cells):
                        # Get the standard cell value
                        std_value = std_row[col_idx] if col_idx < len(std_row) else None
                        
                        # If cell_bbox is None, it's part of a merged cell
                        if cell_bbox is None:
                            row_data.append(std_value)
                        elif std_value and isinstance(std_value, str) and std_value.strip():
                            # Extract text from this bbox and deduplicate
                            clean_text = self._extract_text_from_bbox_dedup(page, cell_bbox)
                            row_data.append(clean_text if clean_text else std_value)
                        else:
                            row_data.append(std_value)
                else:
                    # No cell bbox info for this row, use standard data
                    row_data = std_row
                
                cleaned_data.append(row_data)
            
            return cleaned_data
            
        except Exception as e:
            logger.debug(f"Failed to extract table with deduplication: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            # Return None to trigger fallback
            return None
    
    def _extract_text_from_bbox_dedup(self, page, bbox: tuple) -> str:
        """
        Extract text from a bbox and deduplicate overlapping text spans.
        
        When multiple text spans overlap in the same position (common in PDFs with 
        multiple text layers), this method keeps only the longest/most complete version.
        
        Args:
            page: PyMuPDF page object
            bbox: Bounding box tuple (x0, y0, x1, y1)
            
        Returns:
            Deduplicated text string
        """
        try:
            # Get text dict for this bbox region
            text_dict = page.get_text("dict", clip=bbox)
            
            if not text_dict or 'blocks' not in text_dict:
                return ""
            
            # Collect all text spans with their bboxes
            all_spans = []
            for block in text_dict['blocks']:
                if block['type'] == 0:  # Text block
                    for line in block['lines']:
                        for span in line['spans']:
                            text = span['text'].strip()
                            if text:
                                all_spans.append({
                                    'text': text,
                                    'bbox': span['bbox'],
                                    'size': span['size']
                                })
            
            if not all_spans:
                return ""
            
            # Deduplicate overlapping spans - keep the longest/most complete one
            deduplicated = self._deduplicate_spans(all_spans)
            
            # Join the deduplicated text
            return ' '.join(deduplicated)
            
        except Exception as e:
            logger.debug(f"Failed to extract text from bbox: {e}")
            return ""
    
    def _deduplicate_spans(self, spans: List[Dict]) -> List[str]:
        """
        Deduplicate overlapping text spans, keeping the most complete version.
        
        Groups spans by vertical position (same line) and checks for horizontal overlap.
        When spans overlap significantly (≥50% overlap), keeps only the longest text.
        
        This solves the problem of PDFs with multiple text layers where the same 
        content appears multiple times at slightly different positions.
        
        Args:
            spans: List of span dicts with 'text', 'bbox', 'size' keys
            
        Returns:
            List of deduplicated text strings
        """
        if not spans:
            return []
        
        # Group spans by approximate Y position (same line)
        from collections import defaultdict
        lines = defaultdict(list)
        
        for span in spans:
            bbox = span['bbox']
            y_pos = (bbox[1] + bbox[3]) / 2  # Middle Y
            # Round to nearest 5 pixels to group similar Y positions
            y_key = round(y_pos / 5) * 5
            lines[y_key].append(span)
        
        # For each line, deduplicate spans
        result_texts = []
        for y_key in sorted(lines.keys()):
            line_spans = lines[y_key]
            
            # Sort by X position
            line_spans.sort(key=lambda s: s['bbox'][0])
            
            # Check for overlapping spans (same/similar X range)
            deduped_line = []
            i = 0
            while i < len(line_spans):
                current = line_spans[i]
                current_text = current['text']
                current_bbox = current['bbox']
                
                # Look ahead for overlapping spans
                j = i + 1
                overlapping = [current]
                while j < len(line_spans):
                    next_span = line_spans[j]
                    next_bbox = next_span['bbox']
                    
                    # Check if bboxes overlap horizontally
                    if self._bbox_overlaps_horizontally(current_bbox, next_bbox, threshold=0.5):
                        overlapping.append(next_span)
                        j += 1
                    else:
                        break
                
                # If we have overlapping spans, choose the longest text
                if len(overlapping) > 1:
                    # Choose the one with the longest text (most complete)
                    best = max(overlapping, key=lambda s: len(s['text']))
                    deduped_line.append(best['text'])
                    logger.debug(f"Deduplicated {len(overlapping)} overlapping spans, kept: '{best['text']}'")
                else:
                    deduped_line.append(current_text)
                
                i = j if j > i else i + 1
            
            # Join texts from this line
            if deduped_line:
                result_texts.extend(deduped_line)
        
        return result_texts
    
    def _bbox_overlaps_horizontally(self, bbox1: tuple, bbox2: tuple, threshold: float = 0.5) -> bool:
        """Check if two bboxes overlap horizontally by at least threshold ratio."""
        x0_1, y0_1, x1_1, y1_1 = bbox1
        x0_2, y0_2, x1_2, y1_2 = bbox2
        
        # Calculate horizontal overlap
        overlap_start = max(x0_1, x0_2)
        overlap_end = min(x1_1, x1_2)
        
        if overlap_end <= overlap_start:
            return False
        
        overlap_width = overlap_end - overlap_start
        min_width = min(x1_1 - x0_1, x1_2 - x0_2)
        
        if min_width <= 0:
            return False
        
        overlap_ratio = overlap_width / min_width
        return overlap_ratio >= threshold

    def _analyze_table_with_boundaries(self, table, extracted_data: List[List]) -> Dict[str, Any]:
        """Analyze table using cell boundaries if available"""
        if not extracted_data:
            return {'is_complex': False, 'merged_cells': [], 'row_count': 0, 'col_count': 0, 'cell_spans': {}}

        row_count = len(extracted_data)
        col_count = max(len(row) for row in extracted_data) if extracted_data else 0
        
        # Normalize table data
        normalized = []
        for row in extracted_data:
            normalized_row = list(row) + [None] * (col_count - len(row))
            normalized.append(normalized_row)
        
        # Try to get cell boundaries
        boundaries = self._get_cell_boundaries(table)
        
        if boundaries:
            # Use boundary-based detection
            merge_info = self._detect_merges_from_boundaries(boundaries, normalized)
            return merge_info
        else:
            # Fallback to pattern-based detection
            cell_spans = {}
            merged_cells = []
            is_complex = False
            
            # Track cells that are part of a horizontal merge to avoid false rowspan detection
            cells_in_colspan = set()
            
            # First pass: detect colspans
            for row_idx in range(row_count):
                for col_idx in range(col_count):
                    cell = normalized[row_idx][col_idx]
                    
                    # Skip None/empty cells
                    if cell is None or self._is_cell_empty(cell):
                        continue
                    
                    # Calculate colspan for this cell
                    colspan = 1
                    for check_col in range(col_idx + 1, col_count):
                        if check_col < len(normalized[row_idx]) and self._is_cell_empty(normalized[row_idx][check_col]):
                            colspan += 1
                            # Mark these cells as part of a colspan
                            cells_in_colspan.add((row_idx, check_col))
                        else:
                            break
                    
                    if colspan > 1:
                        cell_spans[(row_idx, col_idx)] = (1, colspan)  # rowspan=1 for now
                        is_complex = True
            
            # Second pass: detect rowspans (only for cells not part of a colspan)
            for row_idx in range(row_count):
                for col_idx in range(col_count):
                    cell = normalized[row_idx][col_idx]
                    
                    # Skip if this cell is part of a colspan
                    if (row_idx, col_idx) in cells_in_colspan:
                        continue
                    
                    # Skip None/empty cells
                    if cell is None or self._is_cell_empty(cell):
                        continue
                    
                    # Skip if we already detected a colspan for this cell
                    if (row_idx, col_idx) in cell_spans:
                        continue
                    
                    # Calculate rowspan
                    rowspan = 1
                    for check_row in range(row_idx + 1, row_count):
                        # Check if the cell below is empty AND not part of a colspan
                        if (check_row < len(normalized) and 
                            col_idx < len(normalized[check_row]) and 
                            self._is_cell_empty(normalized[check_row][col_idx]) and
                            (check_row, col_idx) not in cells_in_colspan):
                            rowspan += 1
                        else:
                            break
                    
                    if rowspan > 1:
                        cell_spans[(row_idx, col_idx)] = (rowspan, 1)  # colspan=1
                        is_complex = True
            
            # Build merged cells list
            for (row_idx, col_idx), (rowspan, colspan) in cell_spans.items():
                merged_cells.append({
                    'row': row_idx,
                    'col': col_idx,
                    'rowspan': rowspan,
                    'colspan': colspan,
                    'content': str(normalized[row_idx][col_idx])
                })
            
            return {
                'is_complex': is_complex,
                'merged_cells': merged_cells,
                'row_count': row_count,
                'col_count': col_count,
                'cell_spans': cell_spans
            }

    def _get_cell_boundaries(self, table) -> List[Dict]:
        """Extract cell boundary information from table if available"""
        boundaries = []
        try:
            # Try to access table cells with boundary info (newer PyMuPDF)
            if hasattr(table, 'cells'):
                for cell in table.cells:
                    if len(cell) >= 7:  # Has position info
                        boundaries.append({
                            'bbox': (cell[0], cell[1], cell[2], cell[3]),
                            'text': cell[4],
                            'row': cell[5],
                            'col': cell[6]
                        })
        except:
            pass
        return boundaries

    def _detect_merges_from_boundaries(self, boundaries: List[Dict], normalized_data: List[List]) -> Dict:
        """Detect merged cells using boundary information"""
        cell_spans = {}
        merged_cells = []
        
        # Group cells by position
        cell_map = {}
        for bound in boundaries:
            key = (bound['row'], bound['col'])
            cell_map[key] = bound
        
        # Analyze overlapping boundaries
        for (row, col), cell in cell_map.items():
            bbox = cell['bbox']
            rowspan = 1
            colspan = 1
            
            # Check how many cells this bbox covers
            for (other_row, other_col), other_cell in cell_map.items():
                if (other_row, other_col) == (row, col):
                    continue
                    
                other_bbox = other_cell['bbox']
                
                # Check if bboxes overlap significantly
                if self._bboxes_overlap_significantly(bbox, other_bbox):
                    # This indicates a merged cell
                    if other_row > row:
                        rowspan = max(rowspan, other_row - row + 1)
                    if other_col > col:
                        colspan = max(colspan, other_col - col + 1)
            
            if rowspan > 1 or colspan > 1:
                cell_spans[(row, col)] = (rowspan, colspan)
                merged_cells.append({
                    'row': row,
                    'col': col,
                    'rowspan': rowspan,
                    'colspan': colspan,
                    'content': normalized_data[row][col] if row < len(normalized_data) and col < len(normalized_data[row]) else ""
                })
        
        return {
            'cell_spans': cell_spans,
            'merged_cells': merged_cells
        }

    def _bboxes_overlap_significantly(self, bbox1: tuple, bbox2: tuple, threshold: float = 0.8) -> bool:
        """Check if two bboxes overlap significantly (indicating merged cells)"""
        x0_1, y0_1, x1_1, y1_1 = bbox1
        x0_2, y0_2, x1_2, y1_2 = bbox2
        
        # Calculate intersection
        x0_int = max(x0_1, x0_2)
        y0_int = max(y0_1, y0_2)
        x1_int = min(x1_1, x1_2)
        y1_int = min(y1_1, y1_2)
        
        if x1_int < x0_int or y1_int < y0_int:
            return False
        
        # Calculate overlap area
        intersection_area = (x1_int - x0_int) * (y1_int - y0_int)
        area1 = (x1_1 - x0_1) * (y1_1 - y0_1)
        area2 = (x1_2 - x0_2) * (y1_2 - y0_2)
        
        # Check if overlap is significant relative to smaller cell
        min_area = min(area1, area2)
        if min_area > 0:
            overlap_ratio = intersection_area / min_area
            return overlap_ratio >= threshold
        
        return False

    def _is_cell_empty(self, cell) -> bool:
        """Enhanced check if a cell is truly empty: only '' (empty string) and None are considered empty."""
        if cell is None:
            return True
        
        cell_str = str(cell).strip()
        # Only treat '' as empty (None is already handled above)
        empty_patterns = ['']
        if cell_str in empty_patterns:
            return True
        return False

    def _convert_table_to_simple_markdown(self, table_data: List[List], table_info: Dict) -> str:
        """Convert simple table to markdown format"""
        if not table_data:
            return ""

        markdown_lines = []
        col_count = table_info['col_count']

        # Process each row
        for row_idx, row in enumerate(table_data):
            row_cells = []

            for col_idx in range(col_count):
                # Get cell content
                if col_idx < len(row) and row[col_idx] is not None:
                    cell_text = str(row[col_idx]).strip()
                else:
                    cell_text = ""

                # Handle line breaks
                cell_text = "<br>".join(cell_text.split('\n'))
                # Escape pipe characters
                cell_text = cell_text.replace("|", "\\|")

                row_cells.append(cell_text)

            # Create table row
            row_text = "| " + " | ".join(row_cells) + " |"
            markdown_lines.append(row_text)

            # Add separator after first row
            if row_idx == 0:
                separator = "|" + "|".join([" --- " for _ in range(col_count)]) + "|"
                markdown_lines.append(separator)

        return "\n".join(markdown_lines) + "\n\n"

    def _convert_table_to_html(self, table_data: List[List], table_info: Dict) -> str:
        """Convert complex table to HTML format based on table_style setting"""
        if not table_data:
            return ""
        
        # Route to appropriate converter based on style
        if self.table_style == TableStyle.STYLED_HTML:
            return self._convert_table_to_styled_html(table_data, table_info)
        elif self.table_style == TableStyle.MARKDOWN_GRID:
            return self._convert_table_to_markdown_grid(table_data, table_info)
        else:  # MINIMAL_HTML (default)
            return self._convert_table_to_minimal_html(table_data, table_info)
    
    def _convert_table_to_minimal_html(self, table_data: List[List], table_info: Dict) -> str:
        """Convert complex table to clean, minimal HTML without inline styles"""
        if not table_data:
            return ""

        html_lines = ["<table>"]
        processed_cells = set()

        for row_idx, row in enumerate(table_data):
            html_lines.append("<tr>")

            col_idx = 0
            while col_idx < table_info['col_count']:
                if (row_idx, col_idx) in processed_cells and (row_idx, col_idx) not in table_info['cell_spans']:
                    col_idx += 1
                    continue

                # Get and escape cell content
                cell_text = str(row[col_idx]).strip() if col_idx < len(row) and row[col_idx] is not None else ""
                cell_text = cell_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                cell_text = cell_text.replace('\n', '<br>')

                # Build attributes (only rowspan/colspan if needed)
                attrs = []
                colspan = 1
                rowspan = 1

                if (row_idx, col_idx) in table_info['cell_spans']:
                    rowspan, colspan = table_info['cell_spans'][(row_idx, col_idx)]
                    if rowspan > 1:
                        attrs.append(f'rowspan="{rowspan}"')
                    if colspan > 1:
                        attrs.append(f'colspan="{colspan}"')
                    
                    for r in range(row_idx, min(row_idx + rowspan, table_info['row_count'])):
                        for c in range(col_idx, min(col_idx + colspan, table_info['col_count'])):
                            processed_cells.add((r, c))

                cell_tag = "th" if row_idx == 0 else "td"
                attrs_str = " " + " ".join(attrs) if attrs else ""
                html_lines.append(f"<{cell_tag}{attrs_str}>{cell_text}</{cell_tag}>")

                col_idx += colspan

            html_lines.append("</tr>")

        html_lines.append("</table>")
        return "\n".join(html_lines) + "\n\n"
    
    def _convert_table_to_markdown_grid(self, table_data: List[List], table_info: Dict) -> str:
        """Convert complex table to markdown with merge annotations as comments"""
        if not table_data:
            return ""
        
        lines = []
        processed_cells = set()
        
        # Add merge info as a compact header comment
        if table_info.get('cell_spans'):
            merge_notes = []
            for (r, c), (rowspan, colspan) in table_info['cell_spans'].items():
                if rowspan > 1 or colspan > 1:
                    merge_notes.append(f"R{r+1}C{c+1}:{rowspan}x{colspan}")
            if merge_notes:
                lines.append(f"<!-- Merged: {', '.join(merge_notes)} -->")
        
        # Calculate column widths (no truncation to preserve data integrity)
        col_count = table_info['col_count']
        col_widths = [3] * col_count
        
        for row in table_data:
            for i, cell in enumerate(row[:col_count]):
                cell_text = str(cell).strip() if cell else ""
                col_widths[i] = max(col_widths[i], len(cell_text))
        
        # Build markdown table
        for row_idx, row in enumerate(table_data):
            row_cells = []
            col_idx = 0
            
            while col_idx < col_count:
                if (row_idx, col_idx) in processed_cells and (row_idx, col_idx) not in table_info.get('cell_spans', {}):
                    row_cells.append("↓" if any(
                        r < row_idx and c == col_idx and (r, c) in table_info.get('cell_spans', {})
                        for r in range(row_idx) for c in [col_idx]
                    ) else "→")
                    col_idx += 1
                    continue
                
                cell_text = str(row[col_idx]).strip() if col_idx < len(row) and row[col_idx] is not None else ""
                
                if (row_idx, col_idx) in table_info.get('cell_spans', {}):
                    rowspan, colspan = table_info['cell_spans'][(row_idx, col_idx)]
                    if rowspan > 1 or colspan > 1:
                        cell_text = f"{cell_text} ⊕" if cell_text else "⊕"
                    
                    for r in range(row_idx, min(row_idx + rowspan, table_info['row_count'])):
                        for c in range(col_idx, min(col_idx + colspan, col_count)):
                            processed_cells.add((r, c))
                
                row_cells.append(cell_text.ljust(col_widths[col_idx]))
                col_idx += 1
            
            lines.append("| " + " | ".join(row_cells) + " |")
            
            if row_idx == 0:
                sep_cells = ["-" * w for w in col_widths]
                lines.append("| " + " | ".join(sep_cells) + " |")
        
        return "\n".join(lines) + "\n\n"
    
    def _convert_table_to_styled_html(self, table_data: List[List], table_info: Dict) -> str:
        """Convert complex table to HTML with full inline styles (legacy format)"""
        if not table_data:
            return ""

        html_lines = ["<!-- Complex table converted to HTML for better structure preservation -->"]
        html_lines.append('<table border="1" style="border-collapse: collapse; width: 100%;">')

        processed_cells = set()

        for row_idx, row in enumerate(table_data):
            html_lines.append("  <tr>")

            col_idx = 0
            while col_idx < table_info['col_count']:
                if (row_idx, col_idx) in processed_cells and (row_idx, col_idx) not in table_info['cell_spans']:
                    col_idx += 1
                    continue

                cell_text = str(row[col_idx]).strip() if col_idx < len(row) and row[col_idx] is not None else ""
                # Escape HTML special characters first
                cell_text = cell_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                # Convert newlines to <br> after escaping
                cell_text = cell_text.replace('\n', '<br>')

                cell_attrs = []
                colspan = 1
                rowspan = 1
                
                if (row_idx, col_idx) in table_info['cell_spans']:
                    rowspan, colspan = table_info['cell_spans'][(row_idx, col_idx)]
                    if rowspan > 1:
                        cell_attrs.append(f'rowspan="{rowspan}"')
                    if colspan > 1:
                        cell_attrs.append(f'colspan="{colspan}"')
                    for r in range(row_idx, min(row_idx + rowspan, table_info['row_count'])):
                        for c in range(col_idx, min(col_idx + colspan, table_info['col_count'])):
                            processed_cells.add((r, c))

                cell_tag = "th" if row_idx == 0 else "td"
                
                if cell_tag == "th":
                    style = 'style="background-color: #f0f0f0; font-weight: bold; padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd"'
                else:
                    style = 'style="padding: 8px; text-align: left; vertical-align: top; border: 1px solid #ddd"'

                attrs_str = " " + " ".join(cell_attrs) if cell_attrs else ""
                html_lines.append(f'    <{cell_tag}{attrs_str} {style}>{cell_text}</{cell_tag}>')

                col_idx += colspan

            html_lines.append("  </tr>")

        html_lines.append("</table>")
        return "\n".join(html_lines) + "\n\n"

    def _extract_images_simple(self, page, page_num: int, ocr_images: bool = False,
                               ocr_results_map: Dict[tuple, str] = None) -> List[SimpleContent]:
        """Extract images and convert to base64 or text descriptions using OCR
        
        Args:
            page: PyMuPDF page object
            page_num: Page number (0-indexed)
            ocr_images: If True, use OCR to convert images to text descriptions
            ocr_results_map: Pre-computed OCR results for batch processing
        
        Returns:
            List of SimpleContent items with type 'image' (base64) or 'text:image_description' (OCR text)
        """
        image_items = []

        # Get list of images
        image_list = page.get_images(full=True)

        # If OCR is enabled and we have pre-computed results, use them
        if ocr_images and ocr_results_map is not None:
            for img_info in image_list:
                xref = img_info[0]

                try:
                    # Get image positions on page
                    img_rects = page.get_image_rects(xref)

                    for img_rect in img_rects:
                        # Check if we have OCR result for this image
                        key = (page_num, xref)
                        if key in ocr_results_map:
                            ocr_text = ocr_results_map[key]
                            image_items.append(SimpleContent(
                                type="text:image_description",
                                content=f"<image_ocr_result>{ocr_text}</image_ocr_result>",
                                page=page_num + 1,
                                position_y=img_rect.y0
                            ))
                        else:
                            # Fallback to base64 if OCR result not found
                            logger.warning(f"OCR result not found for image {xref} on page {page_num + 1}")
                            base_image = self.doc.extract_image(xref)
                            image_bytes = base_image["image"]
                            base64_data = base64.b64encode(image_bytes).decode('utf-8')

                            image_items.append(SimpleContent(
                                type="image",
                                content=base64_data,
                                page=page_num + 1,
                                position_y=img_rect.y0
                            ))

                except Exception as e:
                    logger.warning(f"Failed to process image {xref}: {e}")

        # Fallback to original per-page batch processing if no pre-computed results
        elif ocr_images and ocr_results_map is None and image_list:
            ocr_batch = []
            image_positions = []

            for img_info in image_list:
                xref = img_info[0]

                try:
                    # Extract image
                    base_image = self.doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    base64_data = base64.b64encode(image_bytes).decode('utf-8')

                    # Get image positions on page
                    img_rects = page.get_image_rects(xref)

                    for img_rect in img_rects:
                        ocr_batch.append({"image_data": base64_data})
                        image_positions.append((page_num + 1, img_rect.y0))

                except Exception as e:
                    logger.warning(f"Failed to extract image {xref}: {e}")

            # Batch process OCR for this page
            if ocr_batch:
                try:
                    logger.info(f"Processing {len(ocr_batch)} images with OCR on page {page_num + 1}")

                    if self.ocr:
                        # Use the configured OCR instance
                        # Prepare image data for batch processing
                        image_data_list = [base64.b64decode(item["image_data"]) for item in ocr_batch]

                        # Pass language configuration if available
                        kwargs = {}
                        if hasattr(self.ocr, 'config') and self.ocr.config and self.ocr.config.language:
                            kwargs['language'] = self.ocr.config.language
                            logger.info(
                                f"🌍 Passing language configuration to page-level OCR: {self.ocr.config.language}")

                        # Always use batch processing for efficiency
                        logger.info(
                            f"🚀 Using batch OCR processing for {len(image_data_list)} images on page {page_num + 1}")
                        ocr_results = self.ocr.batch_process_images(image_data_list, **kwargs)

                        # Extract text from results
                        ocr_texts = []
                        for result in ocr_results:
                            if hasattr(result, 'text'):
                                ocr_texts.append(result.text)
                            else:
                                ocr_texts.append(str(result))

                        # Create content items with OCR results
                        for i, (ocr_text, (page, y_pos)) in enumerate(zip(ocr_texts, image_positions)):
                            image_items.append(SimpleContent(
                                type="text:image_description",
                                content=f"<image_ocr_result>{ocr_text}</image_ocr_result>",
                                page=page,
                                position_y=y_pos
                            ))
                    else:
                        logger.error("No OCR instance available")
                        # Skip OCR processing if no instance is provided
                        pass

                except Exception as e:
                    logger.error(f"OCR batch processing failed: {e}")
                    # Fall back to base64 extraction
                    ocr_images = False

        # Regular base64 extraction (if OCR is disabled or failed)
        if not ocr_images:
            for img_info in image_list:
                xref = img_info[0]

                try:
                    # Extract image
                    base_image = self.doc.extract_image(xref)
                    image_bytes = base_image["image"]

                    # Get image positions on page
                    img_rects = page.get_image_rects(xref)

                    for img_rect in img_rects:
                        # Convert image to base64
                        base64_data = base64.b64encode(image_bytes).decode('utf-8')

                        image_items.append(SimpleContent(
                            type="image",
                            content=base64_data,
                            page=page_num + 1,
                            position_y=img_rect.y0  # Use y0 for top coordinate of Rect
                        ))

                except Exception as e:
                    logger.warning(f"Failed to extract image {xref}: {e}")

        return image_items

    def export_to_dict(self, extract_images: bool = True, ocr_images: bool = False, show_progress: bool = True) -> Dict[
        str, Any]:
        """
        Export PDF content to a dictionary ready for JSON dumps
        
        Args:
            extract_images: Whether to extract images as base64
            ocr_images: Whether to use OCR to convert images to text descriptions (requires extract_images=True)
            show_progress: Whether to show progress messages
        
        Returns:
            Dictionary with content array containing various content types
        """
        return self.convert_to_json(extract_images=extract_images, ocr_images=ocr_images, show_progress=show_progress)

    def export_to_markdown(self, extract_images: bool = True, ocr_images: bool = False,
                           show_progress: bool = True) -> str:
        """
        Export PDF content to markdown string
        
        Args:
            extract_images: Whether to extract images as base64
            ocr_images: Whether to use OCR to convert images to text descriptions (requires extract_images=True)
            show_progress: Whether to show progress messages
        
        Returns:
            Markdown-formatted string with all content
        """
        # First get the content as dictionary
        json_data = self.convert_to_json(extract_images=extract_images, ocr_images=ocr_images,
                                         show_progress=show_progress)

        # Use the pdf_to_markdown function for consistent formatting
        return pdf_to_markdown(json_data)

    def save_json(self, output_path: Union[str, Path], json_data: Dict[str, Any]):
        """Save the extracted data to JSON file"""
        output_path = Path(output_path)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        logger.info(f"JSON saved to: {output_path}")

    def save_markdown(self, output_path: Union[str, Path], json_data: Dict[str, Any]):
        """Save the content as a markdown file with embedded images"""
        output_path = Path(output_path)

        # Use the pdf_to_markdown function for consistent formatting
        markdown_content = pdf_to_markdown(json_data)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        logger.info(f"Markdown saved to: {output_path}")

    def close(self):
        """Close the document"""
        if self.doc:
            self.doc.close()
            logger.info("Document closed")


# Convenience function for simple usage
def pdf_to_simple_json(
        pdf_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
        output_markdown: bool = False,
        extract_images: bool = True,
        ocr_images: bool = False,
        show_progress: bool = True,
        ocr=None,
        table_style: Union[str, TableStyle] = None
) -> Dict[str, Any]:
    """
    Convert PDF to simplified JSON with content in reading order
    
    Args:
        pdf_path: Path to the PDF file
        output_path: Optional path to save JSON output
        output_markdown: Also save as markdown file
        extract_images: Extract images as base64
        ocr_images: Use OCR to convert images to text descriptions (requires extract_images=True)
        show_progress: Show progress messages
        ocr: OCR instance for image processing
        table_style: Output style for complex tables:
            - 'minimal_html': Clean HTML with only rowspan/colspan (default)
            - 'markdown_grid': Markdown with merge annotations
            - 'styled_html': Full HTML with inline styles (legacy)
    
    Returns:
        Simplified JSON data with content array containing:
        - text:title - Main document title
        - text:section - Section headers  
        - text:normal - Regular paragraph text
        - text:list - Bullet points or numbered lists
        - text:caption - Figure/table captions
        - text:image_description - OCR-generated image descriptions (when ocr_images=True)
        - table - Tables with complex structure support:
            * Simple tables: Markdown format with span annotations (*[2x3]* for merged cells)
            * Complex tables: HTML format preserving rowspan/colspan attributes
            * Line breaks in cells preserved using <br> tags
            * Automatic detection and labeling of merged cells
        - image - Base64-encoded images (when ocr_images=False)
    """
    converter = PDFLoader(pdf_path, ocr=ocr, table_style=table_style)

    try:
        json_data = converter.convert_to_json(
            extract_images=extract_images,
            ocr_images=ocr_images,
            show_progress=show_progress
        )

        if output_path:
            converter.save_json(output_path, json_data)

            if output_markdown:
                markdown_path = Path(output_path).with_suffix('.md')
                converter.save_markdown(markdown_path, json_data)

        return json_data

    finally:
        converter.close()


def pdf_to_markdown(json_data: Dict[str, Any]) -> str:
    """
    Convert PDF JSON data to markdown string with proper formatting.
    
    This function ensures PDFs get the same quality markdown output as Office documents,
    including proper headers, formatted tables, and OCR results in XML code blocks.
    
    Args:
        json_data: The JSON data from pdf_to_simple_json
        
    Returns:
        Formatted markdown string
    """
    markdown_parts = []
    current_page = None
    
    # Debug: Log all content items
    logger.debug(f"Converting {len(json_data.get('content', []))} content items to markdown")
    
    for idx, item in enumerate(json_data.get("content", [])):
        item_type = item.get("type", "")
        content = item.get("content", "")
        
        # Debug: Log each item
        if content and "demonstrates" in content.lower():
            logger.info(f"Item {idx}: type={item_type}, content preview: {content.strip()[:100]}...")
        
        # Skip empty content
        if not content or not content.strip():
            continue
            
        # Add page separator if needed (but not at the beginning)
        if 'page' in item and item['page'] != current_page:
            if current_page is not None and markdown_parts:
                # Only add page break if we have content and it's not the first page
                pass  # Don't add page numbers in markdown for cleaner output
            current_page = item['page']
        
        if item_type == "text:title":
            # Use # for main titles
            markdown_parts.append(f"# {content}")
            markdown_parts.append("")  # Empty line after title
            
        elif item_type == "text:section":
            # Use ## for section headers
            markdown_parts.append(f"## {content}")
            markdown_parts.append("")  # Empty line after section
            
        elif item_type == "text:normal":
            # Regular paragraphs
            markdown_parts.append(content)
            markdown_parts.append("")  # Empty line after paragraph
            
        elif item_type == "text:list":
            # List items (already formatted with bullets/numbers)
            markdown_parts.append(content)
            markdown_parts.append("")  # Empty line after list
            
        elif item_type == "text:caption":
            # Captions in italics
            markdown_parts.append(f"*{content}*")
            markdown_parts.append("")  # Empty line after caption
            
        elif item_type == "text:image_description":
            # Handle OCR results with XML tags in code blocks
            ocr_text = content
            if ocr_text.startswith('<image_ocr_result>') and ocr_text.endswith('</image_ocr_result>'):
                ocr_text = ocr_text[18:-19]  # Remove tags
            
            markdown_parts.append("```")
            markdown_parts.append("<ocr_result>")
            markdown_parts.append(ocr_text)
            markdown_parts.append("</ocr_result>")
            markdown_parts.append("```")
            markdown_parts.append("")  # Empty line after OCR result
            
        elif item_type == "table":
            # Tables are already in markdown or HTML format
            markdown_parts.append(content)
            # Table content already includes trailing newlines
            
        elif item_type == "image":
            # Base64 images
            markdown_parts.append(f'![Image](data:image/png;base64,{content})')
            markdown_parts.append("")  # Empty line after image
    
    # Clean up extra empty lines
    result = "\n".join(markdown_parts)
    # Remove multiple consecutive empty lines
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    
    return result.strip()


# Example usage
if __name__ == "__main__":
    # Process a PDF file
    try:
        # Method 1: Using the convenience function
        # result = pdf_to_simple_json(
        #     pdf_path="../../data/test.pdf",
        #     output_path="output_simple.json",
        #     output_markdown=True,  # Also create markdown file
        #     extract_images=True,
        #     ocr_images=True,
        #     show_progress=True
        # )

        # print(f"\nProcessing completed successfully!")
        # print(f"Check 'output_simple.json' for the results.")
        # print(f"Also created 'output_simple.md' with markdown format.")

        # Method 2: Using the PDFLoader class directly with new export methods
        print("\n--- Using PDFLoader class directly ---")
        loader = PDFLoader("../../../data/test2.pdf")

        # Export to dict (ready for JSON dumps)
        # pdf_dict = loader.export_to_dict(extract_images=True, ocr_images=False, show_progress=False)
        # print(f"\nExported to dict with {len(pdf_dict['content'])} content items")

        # Export to markdown string with OCR
        markdown_str = loader.export_to_markdown(extract_images=True, ocr_images=True, show_progress=False)
        # save to file
        with open("output_simple.md", "w", encoding="utf-8") as f:
            f.write(markdown_str)

        print(f"Exported to markdown string with OCR ({len(markdown_str)} characters)")

        loader.close()

        # # Show sample of the output
        # print("\nSample output structure:")
        # if result["content"]:
        #     for i, item in enumerate(result["content"][:10]):  # Show first 10 items
        #         if item["type"].startswith("text:"):
        #             preview = item["content"].strip()[:80] + "..." if len(item["content"]) > 80 else item[
        #                 "content"].strip()
        #             # Remove newlines for preview
        #             preview = preview.replace('\n', ' ')
        #             print(f"Item {i}: {item['type']} - {preview}")
        #         elif item["type"] == "table":
        #             lines = item["content"].strip().split('\n')
        #             print(f"Item {i}: Table - {len(lines)} rows")
        #             if lines:
        #                 print(f"  First row: {lines[0][:60]}...")
        #         elif item["type"] == "image":
        #             print(f"Item {i}: Image - base64 data ({len(item['content'])} chars)")

    except Exception as e:
        logger.error(f"Error processing PDF: {e}")
        raise
