import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Any, Union, Optional, Tuple

import pymupdf

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class SimpleContent:
    """Simple content item with type and data"""
    type: str  # 'text:title', 'text:section', 'text:normal', 'text:list', 'text:caption', 'table', or 'image'
    content: str  # markdown text, markdown table, or base64 data
    page: int
    position_y: float  # For sorting


class PDFLoader:
    """PDF loader that extracts content in reading order and exports to various formats"""

    def __init__(self, pdf_path: Union[str, Path], ocr=None):
        self.pdf_path = Path(pdf_path)
        self.doc = None
        self.ocr = ocr  # Store the OCR instance

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

            # Detect headers based on size
            if line_size > avg_size * 1.5:
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

                # Apply bold/italic formatting
                if is_bold and is_italic:
                    markdown_line = f"***{markdown_line}***"
                elif is_bold:
                    markdown_line = f"**{markdown_line}**"
                elif is_italic:
                    markdown_line = f"*{markdown_line}*"

            # Detect list items (more comprehensive patterns)
            list_match = re.match(
                r'^([\u2022•\-\*\u2013\u2014\u25AA\u25AB\u25CF\u25CB\u25A0\u25A1]|\d+[\.\)]|[a-zA-Z][\.\)])\s+',
                line_text)
            if list_match:
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

            for table_idx, table in enumerate(tables.tables):
                # Store table bbox for excluding from text extraction
                table_bboxes.append(tuple(table.bbox))

                # Extract table content
                markdown_table = self._convert_table_to_markdown(table)

                if markdown_table.strip():
                    table_items.append(SimpleContent(
                        type="table",  # Table type for better identification
                        content=markdown_table,
                        page=page_num + 1,
                        position_y=table.bbox[1]
                    ))
                    logger.debug(
                        f"Extracted table {table_idx} on page {page_num + 1}: {table.row_count}x{table.col_count}")

        except AttributeError:
            logger.debug("Table extraction not available in this PyMuPDF version")
        except Exception as e:
            logger.warning(f"Failed to extract tables: {e}")

        return table_items, table_bboxes

    def _convert_table_to_markdown(self, table) -> str:
        """Convert a table to markdown format with support for complex structures
        
        This enhanced version handles:
        - Merged cells (rowspan/colspan)
        - Complex table structures
        - Cell span detection and labeling
        - Optional HTML table output for very complex tables
        
        Line breaks within table cells are preserved using HTML <br> tags.
        """
        if not table:
            return ""

        try:
            # Get detailed table information
            extracted_data = table.extract()

            if not extracted_data or not any(extracted_data):
                return ""

            # Analyze table structure for complexity
            table_info = self._analyze_table_structure(extracted_data)

            # If table is too complex, use HTML format
            if table_info['is_complex']:
                return self._convert_table_to_html(extracted_data, table_info)
            else:
                return self._convert_table_to_simple_markdown(extracted_data, table_info)

        except Exception as e:
            logger.warning(f"Failed to convert table to markdown: {e}")
            return ""

    def _analyze_table_structure(self, table_data: List[List]) -> Dict[str, Any]:
        """Analyze table structure to detect merged cells and complexity
        
        Returns:
            Dictionary with table analysis:
            - is_complex: Whether table has merged cells
            - merged_cells: List of merged cell info
            - row_count: Number of rows
            - col_count: Number of columns
            - cell_spans: Dict mapping (row, col) to (rowspan, colspan)
        """
        if not table_data:
            return {'is_complex': False, 'merged_cells': [], 'row_count': 0, 'col_count': 0, 'cell_spans': {}}

        row_count = len(table_data)
        col_count = max(len(row) for row in table_data) if table_data else 0

        # Initialize analysis structures
        cell_spans = {}
        merged_cells = []
        is_complex = False

        # Create a normalized table (all rows same length)
        normalized = []
        for row in table_data:
            normalized_row = list(row) + [None] * (col_count - len(row))
            normalized.append(normalized_row)

        # Enhanced merged cell detection
        # Track cells we've already identified as part of a merge
        identified_merges = set()

        for row_idx in range(row_count):
            for col_idx in range(col_count):
                # Skip if already identified as part of a merge
                if (row_idx, col_idx) in identified_merges:
                    continue

                cell = normalized[row_idx][col_idx]

                if cell is None or str(cell).strip() == "":
                    # Empty cell - check if part of a merge
                    span_info = self._detect_cell_span(normalized, row_idx, col_idx)
                    if span_info:
                        merged_cells.append(span_info)
                        is_complex = True

                        # Mark cells as identified
                        if span_info['type'] == 'rowspan_continuation':
                            for r in range(span_info['source_row'], row_idx + 1):
                                identified_merges.add((r, col_idx))
                        elif span_info['type'] == 'colspan_continuation':
                            for c in range(span_info['source_col'], col_idx + 1):
                                identified_merges.add((row_idx, c))
                else:
                    # Non-empty cell - check if it spans multiple cells
                    rowspan, colspan = self._calculate_cell_span(normalized, row_idx, col_idx, str(cell))

                    if rowspan > 1 or colspan > 1:
                        cell_spans[(row_idx, col_idx)] = (rowspan, colspan)

                        # Mark all spanned cells as identified
                        for r in range(row_idx, min(row_idx + rowspan, row_count)):
                            for c in range(col_idx, min(col_idx + colspan, col_count)):
                                identified_merges.add((r, c))

                        merged_cells.append({
                            'row': row_idx,
                            'col': col_idx,
                            'rowspan': rowspan,
                            'colspan': colspan,
                            'content': str(cell)
                        })
                        is_complex = True

        return {
            'is_complex': is_complex,
            'merged_cells': merged_cells,
            'row_count': row_count,
            'col_count': col_count,
            'cell_spans': cell_spans
        }

    def _detect_cell_span(self, table: List[List], row: int, col: int) -> Optional[Dict]:
        """Detect if an empty cell is part of a span from another cell"""
        # Check if empty cell is part of a row span from above
        if row > 0:
            above_cell = table[row - 1][col]
            if above_cell and str(above_cell).strip():
                # Check if cells below also empty (indicating rowspan)
                span_rows = 1
                for check_row in range(row, len(table)):
                    if not table[check_row][col] or str(table[check_row][col]).strip() == "":
                        span_rows += 1
                    else:
                        break

                if span_rows > 1:
                    return {
                        'type': 'rowspan_continuation',
                        'source_row': row - 1,
                        'source_col': col,
                        'span_rows': span_rows
                    }

        # Check if empty cell is part of a col span from left
        if col > 0:
            left_cell = table[row][col - 1]
            if left_cell and str(left_cell).strip():
                # Check if cells to right also empty (indicating colspan)
                span_cols = 1
                for check_col in range(col, len(table[row])):
                    if not table[row][check_col] or str(table[row][check_col]).strip() == "":
                        span_cols += 1
                    else:
                        break

                if span_cols > 1:
                    return {
                        'type': 'colspan_continuation',
                        'source_row': row,
                        'source_col': col - 1,
                        'span_cols': span_cols
                    }

        return None

    def _calculate_cell_span(self, table: List[List], row: int, col: int, cell_content: str) -> Tuple[int, int]:
        """Calculate how many rows and columns a cell spans"""
        rowspan = 1
        colspan = 1

        # Check colspan: count consecutive empty cells to the right
        for check_col in range(col + 1, len(table[row])):
            if not table[row][check_col] or str(table[row][check_col]).strip() == "":
                # Additional check: ensure it's not a different empty cell
                if row > 0 and (not table[row - 1][check_col] or str(table[row - 1][check_col]).strip() == ""):
                    colspan += 1
                else:
                    break
            else:
                break

        # Check rowspan: count consecutive empty cells below
        for check_row in range(row + 1, len(table)):
            if col < len(table[check_row]):
                if not table[check_row][col] or str(table[check_row][col]).strip() == "":
                    # Additional check: ensure it's not a different empty cell
                    if col > 0 and (not table[check_row][col - 1] or str(table[check_row][col - 1]).strip() == ""):
                        rowspan += 1
                    else:
                        break
                else:
                    break
            else:
                break

        return rowspan, colspan

    def _convert_table_to_simple_markdown(self, table_data: List[List], table_info: Dict) -> str:
        """Convert simple table to markdown format with span annotations"""
        if not table_data:
            return ""

        markdown_lines = []
        processed_cells = set()  # Track cells that are part of spans

        # Add table complexity note if needed
        if table_info['merged_cells']:
            markdown_lines.append("<!-- Table contains merged cells (marked with *) -->")

        # Process each row
        for row_idx, row in enumerate(table_data):
            row_cells = []

            for col_idx in range(table_info['col_count']):
                # Skip if this cell is part of a span
                if (row_idx, col_idx) in processed_cells:
                    continue

                # Get cell content
                if col_idx < len(row) and row[col_idx] is not None:
                    cell_text = str(row[col_idx]).strip()
                else:
                    cell_text = ""

                # Handle line breaks
                cell_text = "<br>".join(cell_text.split('\n'))
                # Escape pipe characters
                cell_text = cell_text.replace("|", "\\|")

                # Check if this cell has spans
                if (row_idx, col_idx) in table_info['cell_spans']:
                    rowspan, colspan = table_info['cell_spans'][(row_idx, col_idx)]

                    # Mark spanned cells as processed
                    for r in range(row_idx, min(row_idx + rowspan, table_info['row_count'])):
                        for c in range(col_idx, min(col_idx + colspan, table_info['col_count'])):
                            if r != row_idx or c != col_idx:
                                processed_cells.add((r, c))

                    # Add span indicator
                    if rowspan > 1 or colspan > 1:
                        span_note = f"*[{rowspan}x{colspan}]*"
                        cell_text = f"{cell_text} {span_note}" if cell_text else span_note

                # For cells that span multiple columns, repeat the content
                if (row_idx, col_idx) in table_info['cell_spans']:
                    _, colspan = table_info['cell_spans'][(row_idx, col_idx)]
                    for _ in range(colspan):
                        row_cells.append(cell_text)
                else:
                    row_cells.append(cell_text)

            # Ensure row has correct number of columns
            while len(row_cells) < table_info['col_count']:
                row_cells.append("")

            # Create table row
            row_text = "| " + " | ".join(row_cells[:table_info['col_count']]) + " |"
            markdown_lines.append(row_text)

            # Add separator after first row
            if row_idx == 0:
                separator = "|" + "|".join([" --- " for _ in range(table_info['col_count'])]) + "|"
                markdown_lines.append(separator)

        return "\n".join(markdown_lines) + "\n\n"

    def _convert_table_to_html(self, table_data: List[List], table_info: Dict) -> str:
        """Convert complex table to HTML format for better structure preservation"""
        if not table_data:
            return ""

        html_lines = ["<!-- Complex table converted to HTML for better structure preservation -->"]
        html_lines.append("<table>")

        processed_cells = set()

        # Process each row
        for row_idx, row in enumerate(table_data):
            html_lines.append("  <tr>")

            for col_idx in range(table_info['col_count']):
                # Skip if this cell is part of a span
                if (row_idx, col_idx) in processed_cells:
                    continue

                # Get cell content
                if col_idx < len(row) and row[col_idx] is not None:
                    cell_text = str(row[col_idx]).strip()
                else:
                    cell_text = ""

                # Convert newlines to <br>
                cell_text = cell_text.replace('\n', '<br>')

                # Determine cell attributes
                cell_attrs = []

                # Check if this cell has spans
                if (row_idx, col_idx) in table_info['cell_spans']:
                    rowspan, colspan = table_info['cell_spans'][(row_idx, col_idx)]

                    if rowspan > 1:
                        cell_attrs.append(f'rowspan="{rowspan}"')
                    if colspan > 1:
                        cell_attrs.append(f'colspan="{colspan}"')

                    # Mark spanned cells as processed
                    for r in range(row_idx, min(row_idx + rowspan, table_info['row_count'])):
                        for c in range(col_idx, min(col_idx + colspan, table_info['col_count'])):
                            processed_cells.add((r, c))

                # Determine if header cell (first row typically)
                cell_tag = "th" if row_idx == 0 else "td"

                # Build cell HTML
                attrs_str = " " + " ".join(cell_attrs) if cell_attrs else ""
                html_lines.append(f'    <{cell_tag}{attrs_str}>{cell_text}</{cell_tag}>')

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

        # Convert to markdown string
        markdown_parts = []

        for item in json_data["content"]:
            if item["type"].startswith("text:"):
                markdown_parts.append(item["content"])
            elif item["type"] == "table":
                markdown_parts.append(item["content"])  # Table is already in markdown format
            elif item["type"] == "image":
                # Include image as markdown with base64 data URL
                markdown_parts.append(f'![Image](data:image/png;base64,{item["content"]})\n')

        return "\n".join(markdown_parts)

    def save_json(self, output_path: Union[str, Path], json_data: Dict[str, Any]):
        """Save the extracted data to JSON file"""
        output_path = Path(output_path)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        logger.info(f"JSON saved to: {output_path}")

    def save_markdown(self, output_path: Union[str, Path], json_data: Dict[str, Any]):
        """Save the content as a markdown file with embedded images"""
        output_path = Path(output_path)

        with open(output_path, 'w', encoding='utf-8') as f:
            for item in json_data["content"]:
                if item["type"].startswith("text:"):
                    # All text types including text:image_description
                    f.write(item["content"])
                    f.write("\n")
                elif item["type"] == "table":
                    f.write(item["content"])  # Table is already in markdown format
                    f.write("\n")
                elif item["type"] == "image":
                    # Write image as markdown with base64 data URL
                    f.write(f'![Image](data:image/png;base64,{item["content"]})\n\n')

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
        ocr=None
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
    converter = PDFLoader(pdf_path, ocr=ocr)

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
