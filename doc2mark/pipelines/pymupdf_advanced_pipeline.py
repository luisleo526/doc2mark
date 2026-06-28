import base64
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Any, Union, Optional, Tuple

import pymupdf

from doc2mark.utils.image_utils import detect_image_format, get_mime_type
from doc2mark.core.table import TableStyle, TableRenderer, TableData

# --- Image-dominant page OCR strategy ---------------------------------------
# Some PDFs (scanned documents, slide decks exported as pictures) carry their
# content as full-page raster images with little or no text layer. OCR'ing each
# embedded image individually fragments the content and wastes calls on
# decorative logos/icons. For such pages we render the whole page once and OCR
# that single image instead. Heuristic thresholds (general, not file-specific):
_PAGE_RENDER_XREF = -1          # sentinel xref marking a whole-page render
_PAGE_RENDER_DPI = 150          # rasterization DPI for page-level OCR
# Document/page strategy thresholds + decision live in core.strategy (shared with
# the Office route). Aliased here for the per-page image-dominance check below.
from doc2mark.core.strategy import (  # noqa: E402
    IMAGE_PAGE_COVERAGE as _IMAGE_PAGE_COVERAGE,
    IMAGE_PAGE_TEXT_LIMIT as _IMAGE_PAGE_TEXT_LIMIT,
    decide_doc_strategy as _decide_doc_strategy,
)
_TINY_IMAGE_FRACTION = 0.10     # images smaller than this (of page w AND h) are decorative

# --- Neighbor-page PDF context for OCR --------------------------------------
# Gemini's INLINE request cap is ~20MB total; stay under it so an inline PDF
# part never 400s. (OpenAI's file cap is 50MB but we gate to the tighter inline
# limit.)
_CONTEXT_PDF_MAX_BYTES = 18 * 1024 * 1024
_WINDOW_CACHE_MAXLEN = 4   # windows overlap; far pages are never reused -> tiny LRU

logger = logging.getLogger(__name__)


from doc2mark.core.types import SimpleContent  # shared content model


@dataclass(frozen=True)
class _HeadingFeatures:
    normalized: str
    length: int
    line_count: int
    size_ratio: float
    max_size_ratio: float
    is_bold: bool
    is_all_caps: bool
    has_list_pattern: bool
    list_line_count: int
    is_explicit_marker: bool
    is_structured_marker: bool
    text_after_marker: str
    has_cjk: bool
    has_checkbox_marker: bool
    has_sentence_punctuation: bool
    has_trailing_continuation: bool
    separator_count: int
    has_form_field_shape: bool
    has_long_clause_shape: bool


class PDFLoader:
    """PDF loader that extracts content in reading order and exports to various formats"""

    def __init__(self, pdf_path: Union[str, Path], ocr=None, table_style: Union[str, TableStyle] = None):
        self.pdf_path = Path(pdf_path)
        self.doc = None
        self.ocr = ocr  # Store the OCR instance
        self._first_text_page_num = None

        # Neighbor-page PDF context (off by default). Resolve the context tier
        # once from the OCR instance's config (NOT self.config, which does not
        # exist). 0=off, 1=page-renders only, 2=renders + embedded images.
        self._window_pdf_cache: "OrderedDict[int, Optional[str]]" = OrderedDict()
        cfg = getattr(self.ocr, "config", None)
        self._context_tier = int(getattr(cfg, "context_pages", 0) or 0) if (self.ocr and cfg) else 0
        self._doc_strategy: Optional[str] = None  # lazy: "image" | "text" (document-level route)

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

    def _extract_image_bytes(self, xref: int) -> Optional[Tuple[bytes, str, str]]:
        """Extract image bytes with Pixmap fallback for problematic formats (e.g. JBIG2).

        Args:
            xref: Image cross-reference number

        Returns:
            Tuple of (image_bytes, extension, mime_type) or None if extraction fails
        """
        # Primary path: extract_image (fast, preserves original format)
        try:
            base_image = self.doc.extract_image(xref)
            if base_image and base_image.get("image"):
                image_bytes = base_image["image"]
                ext = base_image.get("ext", "png")
                fmt = detect_image_format(image_bytes)
                mime = get_mime_type(fmt)
                return image_bytes, ext, mime
        except Exception as e:
            logger.debug(f"extract_image failed for xref {xref}: {e}")

        # Fallback: render via Pixmap (handles JBIG2, JPEG2000, etc.)
        try:
            pix = pymupdf.Pixmap(self.doc, xref)
            if pix.alpha:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            img_bytes = pix.tobytes("png")
            logger.info(f"Used Pixmap fallback for xref {xref} ({len(img_bytes)} bytes)")
            return img_bytes, "png", "image/png"
        except Exception as e:
            logger.warning(f"Pixmap fallback also failed for xref {xref}: {e}")

        return None

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

            self._window_pdf_cache = OrderedDict()
            all_images_info = self._collect_all_images()

            if all_images_info:
                if show_progress:
                    logger.info(f"Processing {len(all_images_info)} images with batch OCR...")

                try:
                    # Use the configured OCR instance for batch processing
                    if self.ocr:
                        # Prepare image data for batch processing
                        image_data_list = [base64.b64decode(info["base64"]) for info in all_images_info]
                        # Per-image neighbor-page PDF context (aligned positionally
                        # with image_data_list). All None when the feature is off.
                        context_pdfs = [info.get("context_pdf_b64") for info in all_images_info]

                        # Pass language configuration if available
                        kwargs = {}
                        if hasattr(self.ocr, 'config') and self.ocr.config and self.ocr.config.language:
                            kwargs['language'] = self.ocr.config.language
                            logger.info(f"🌍 Passing language configuration to OCR: {self.ocr.config.language}")

                        # Only inject context when at least one image carries it, so
                        # the off-default path stays byte-identical (cache keys + call).
                        if any(context_pdfs):
                            kwargs['context_pdfs'] = context_pdfs

                        # Image strategy (whole-page renders): ask the model to ALSO
                        # synthesize structured page_markdown, so the rendered output is
                        # a readable document instead of a flat OCR dump. Text-strategy
                        # batches (embedded figures) carry no page render -> never set.
                        if any(info.get("is_page_render") for info in all_images_info):
                            kwargs['synthesis_markdown'] = True

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
                    # Do NOT fall back to base64 extraction here: dumping megabytes of
                    # base64 image data into a text/RAG output is useless and harmful.
                    # Keep ocr_images on with the empty/partial map so missing images
                    # become lightweight placeholders (see _extract_images_simple),
                    # while the deterministic text/table layer is still emitted.
                    logger.error(f"Batch OCR processing failed: {e}; emitting image placeholders")

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

        # Post-process: detect and tag repeated headers/footers
        self._detect_repeated_content(document)

        return document

    def _detect_repeated_content(self, document: Dict[str, Any]) -> None:
        """Detect repeated headers/footers across pages and retype them.

        Items appearing on >50% of pages at the top or bottom of pages
        are retyped to 'text:header' or 'text:footer'. Only applies to
        documents with more than 3 pages to avoid false positives.

        Modifies document["content"] in place.
        """
        total_pages = document.get("pages", 0)
        if total_pages <= 3:
            return

        content = document.get("content", [])
        if not content:
            return

        # Get page heights for zone calculation
        page_heights = {}
        for page_num in range(len(self.doc)):
            try:
                page_heights[page_num + 1] = self.doc.load_page(page_num).rect.height
            except Exception:
                page_heights[page_num + 1] = 800  # fallback

        # Build frequency map: (normalized_text, zone) -> set of pages
        from collections import defaultdict
        freq_map = defaultdict(set)

        for item in content:
            if not item.get("type", "").startswith("text:"):
                continue
            page = item.get("page")
            pos_y = item.get("position_y")
            if page is None or pos_y is None:
                continue

            page_height = page_heights.get(page, 800)
            if page_height <= 0:
                continue

            y_pct = pos_y / page_height
            if y_pct < 0.12:
                zone = "header"
            elif y_pct > 0.88:
                zone = "footer"
            else:
                continue

            # Normalize: strip whitespace, remove standalone page numbers
            text = item.get("content", "").strip()
            normalized = re.sub(r'^\d+$', '', text).strip()
            normalized = re.sub(r'^[Pp]age\s+\d+$', '', normalized).strip()
            if not normalized:
                # Pure page number — mark directly
                zone_key = ("__page_number__", zone)
            else:
                zone_key = (normalized, zone)

            freq_map[zone_key].add(page)

        # Identify repeated content (appears on >50% of pages)
        threshold = total_pages * 0.5
        repeated = set()
        for key, pages in freq_map.items():
            if len(pages) >= threshold:
                repeated.add(key)

        if not repeated:
            return

        # Retype matching items
        for item in content:
            if not item.get("type", "").startswith("text:"):
                continue
            if item["type"] in ("text:header", "text:footer"):
                continue  # already tagged

            page = item.get("page")
            pos_y = item.get("position_y")
            if page is None or pos_y is None:
                continue

            page_height = page_heights.get(page, 800)
            if page_height <= 0:
                continue

            y_pct = pos_y / page_height
            if y_pct < 0.12:
                zone = "header"
            elif y_pct > 0.88:
                zone = "footer"
            else:
                continue

            text = item.get("content", "").strip()
            normalized = re.sub(r'^\d+$', '', text).strip()
            normalized = re.sub(r'^[Pp]age\s+\d+$', '', normalized).strip()
            if not normalized:
                zone_key = ("__page_number__", zone)
            else:
                zone_key = (normalized, zone)

            if zone_key in repeated:
                item["type"] = f"text:{zone}"

    def _get_first_text_page_num(self) -> int:
        """Return the first page index containing non-empty text, falling back to 0."""
        cached = getattr(self, "_first_text_page_num", None)
        if cached is not None:
            return cached

        doc = getattr(self, "doc", None)
        if doc is None:
            return 0

        try:
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                text_dict = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_LIGATURES)
                for block in text_dict.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            if span.get("text", "").strip():
                                self._first_text_page_num = page_num
                                return page_num
        except Exception as e:
            logger.debug(f"Failed to detect first text page, defaulting to page 0: {e}")
            return 0

        self._first_text_page_num = 0
        return 0

    def _page_image_coverage(self, page) -> float:
        """Fraction of the page area covered by raster images (capped at 1.0)."""
        page_area = abs(page.rect.width * page.rect.height) or 1.0
        covered = 0.0
        for img_info in page.get_images(full=True):
            for rect in page.get_image_rects(img_info[0]):
                covered += abs(rect.width * rect.height)
        return min(covered / page_area, 1.0)

    def _document_image_strategy(self) -> str:
        """High-level DOCUMENT route from two deterministic signals: mean per-page
        image coverage AND mean per-page selectable-text density.

        - "image": pages are mostly pictures (mean coverage high) AND carry little
          selectable text (mean chars/page low) — the real content is baked into the
          page images. Every page is rendered and OCR'd as a whole image (with
          neighbor-page context when enabled); the OCR is authoritative.
        - "text": there is a usable selectable-text layer (mean chars/page not low)
          OR little image coverage. The deterministic rule-based layer (complex
          tables + text, preserved verbatim for BM42 RAG) is authoritative, and
          embedded figures are OCR'd individually.

        Text density is the decisive signal: image coverage alone misclassifies a
        text document that happens to carry large figures. Decided once per document
        (cached); a uniform strategy avoids mixing OCR-only and rule-based pages.
        """
        if self._doc_strategy is not None:
            return self._doc_strategy
        n = len(self.doc) if self.doc is not None else 0
        if n == 0:
            self._doc_strategy = "text"
            return self._doc_strategy
        mean_cov = sum(self._page_image_coverage(self.doc.load_page(i)) for i in range(n)) / n
        mean_text = sum(len(self.doc.load_page(i).get_text().strip()) for i in range(n)) / n

        self._doc_strategy = _decide_doc_strategy(mean_cov, mean_text)
        logger.info(f"📑 Document OCR strategy: {self._doc_strategy} "
                    f"(mean coverage {mean_cov:.2f}, mean text {mean_text:.0f} chars/page)")
        return self._doc_strategy

    def _render_page_png(self, page) -> bytes:
        """Rasterize a whole page to PNG bytes for page-level OCR."""
        return page.get_pixmap(dpi=_PAGE_RENDER_DPI).tobytes("png")

    def _is_decorative_image(self, rect, page) -> bool:
        """True for tiny images (logos/icons/bullets) not worth an OCR call."""
        return (abs(rect.width) < _TINY_IMAGE_FRACTION * page.rect.width
                and abs(rect.height) < _TINY_IMAGE_FRACTION * page.rect.height)

    def _build_window_pdf(self, k: int) -> Optional[str]:
        """Base64 (RAW, no data-uri prefix) of a PDF with only pages {k-1,k,k+1},
        clamped to doc bounds. Built once per page index, LRU-bounded. Returns None
        on failure/oversize -> caller treats None as 'no context' (graceful)."""
        if k in self._window_pdf_cache:
            self._window_pdf_cache.move_to_end(k)
            return self._window_pdf_cache[k]
        result: Optional[str] = None
        try:
            a = max(0, k - 1)
            b = min(len(self.doc) - 1, k + 1)          # k=0->{0,1}; last->{n-2,n-1}; single->{0}
            out = pymupdf.open()
            try:
                out.insert_pdf(self.doc, from_page=a, to_page=b)   # inclusive/inclusive
                data = out.tobytes(deflate=True, garbage=3)        # compress + drop orphans
            finally:
                out.close()
            if len(data) <= _CONTEXT_PDF_MAX_BYTES:
                result = base64.b64encode(data).decode("utf-8")
            else:
                logger.warning(f"Context PDF for page {k+1} is {len(data)} bytes "
                               f"(> {_CONTEXT_PDF_MAX_BYTES}); skipping context for this page.")
        except Exception as e:
            logger.warning(f"Failed to build context PDF for page {k+1}: {e}; OCR without context.")
        self._window_pdf_cache[k] = result
        if len(self._window_pdf_cache) > _WINDOW_CACHE_MAXLEN:
            self._window_pdf_cache.popitem(last=False)             # evict oldest (LRU)
        return result

    def _collect_all_images(self) -> List[Dict[str, Any]]:
        """Collect images for batch OCR.

        Image-dominant pages contribute ONE whole-page render (xref
        ``_PAGE_RENDER_XREF``); other pages contribute their embedded images,
        skipping decorative thumbnails. Each entry has page_num, xref, base64,
        mime_type, position, and (for renders) is_page_render=True.
        """
        all_images = []

        # Document-level route: when the doc is mostly pictures, OCR EVERY page
        # as a whole image; otherwise OCR only the embedded figures per page.
        doc_image_strategy = self.ocr is not None and self._document_image_strategy() == "image"

        for page_num in range(len(self.doc)):
            page = self.doc.load_page(page_num)

            # Whole-page OCR for the image-strategy document (every page rendered).
            if doc_image_strategy:
                try:
                    png = self._render_page_png(page)
                    ctx = self._build_window_pdf(page_num) if self._context_tier >= 1 else None
                    all_images.append({
                        "page_num": page_num,
                        "xref": _PAGE_RENDER_XREF,
                        "base64": base64.b64encode(png).decode('utf-8'),
                        "mime_type": "image/png",
                        "is_page_render": True,
                        "position": (0.0, 0.0, page.rect.width, page.rect.height),
                        "context_pdf_b64": ctx,
                    })
                    continue
                except Exception as e:
                    logger.warning(f"Page render failed on page {page_num + 1}: {e}; "
                                   f"falling back to per-image OCR")

            for img_info in page.get_images(full=True):
                xref = img_info[0]

                try:
                    img_rects = page.get_image_rects(xref)
                    # Skip decorative thumbnails before paying for extraction/OCR.
                    img_rects = [r for r in img_rects if not self._is_decorative_image(r, page)]
                    if not img_rects:
                        continue

                    result = self._extract_image_bytes(xref)
                    if result is None:
                        continue
                    image_bytes, _, mime = result
                    base64_data = base64.b64encode(image_bytes).decode('utf-8')

                    ctx = self._build_window_pdf(page_num) if self._context_tier >= 2 else None
                    for img_rect in img_rects:
                        all_images.append({
                            "page_num": page_num,
                            "xref": xref,
                            "base64": base64_data,
                            "mime_type": mime,
                            "position": (img_rect.x0, img_rect.y0, img_rect.x1, img_rect.y1),
                            "context_pdf_b64": ctx,
                        })

                except Exception as e:
                    logger.warning(f"Failed to extract image {xref} on page {page_num + 1}: {e}")

        return all_images

    def _process_page(self, page_num: int, extract_images: bool = True, ocr_images: bool = False,
                      ocr_results_map: Dict[tuple, str] = None) -> List[Dict[str, Any]]:
        """Process a single page, routed by the high-level OCR strategy.

        The document-level route (_document_image_strategy, applied in
        _collect_all_images) selects the strategy:

        - IMAGE-authoritative (a whole-page render exists): emit ONLY the OCR
          transcription. The sparse text layer on such a page is chrome
          (logo / footer / page number) that the whole-page OCR already
          captures, so emitting the text layer too would just duplicate it and
          add junk header/footer mini-tables.
        - TEXT-authoritative: emit the deterministic text/table layer (preserved
          verbatim for the BM42 RAG flow) plus per-image OCR for embedded figures.
        """
        page = self.doc.load_page(page_num)

        # --- IMAGE-authoritative strategy: the whole-page OCR IS the content. ---
        if (ocr_images and ocr_results_map is not None
                and (page_num, _PAGE_RENDER_XREF) in ocr_results_map):
            render_text = (ocr_results_map.get((page_num, _PAGE_RENDER_XREF)) or "").strip()
            if not render_text:
                return []
            return [{
                "type": "text:image_description",
                "content": f"<image_ocr_result>{render_text}</image_ocr_result>",
                "page": page_num + 1,
                "position_y": 0.0,
            }]

        # --- TEXT-authoritative strategy: rule-based text/tables + per-image OCR. ---
        content_items = []
        table_items, table_bboxes = self._extract_tables_as_markdown(page, page_num)
        content_items.extend(table_items)
        text_items = self._extract_text_as_markdown(page, page_num, table_bboxes)
        content_items.extend(text_items)
        if extract_images:
            content_items.extend(self._extract_images_simple(
                page, page_num, ocr_images=ocr_images, ocr_results_map=ocr_results_map))

        content_items.sort(key=lambda x: x.position_y)

        simple_content = []
        for item in content_items:
            if item.type.startswith("text:"):
                simple_content.append({
                    "type": item.type,
                    "content": item.content,
                    "page": item.page,
                    "position_y": item.position_y
                })
            elif item.type == "table":
                simple_content.append({
                    "type": "table",
                    "content": item.content,  # markdown table
                    "page": item.page,
                    "position_y": item.position_y
                })
            elif item.type == "image":
                entry = {
                    "type": "image",
                    "content": item.content,  # base64 data
                    "page": item.page,
                    "position_y": item.position_y
                }
                if item.mime_type:
                    entry["mime_type"] = item.mime_type
                simple_content.append(entry)

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
                except Exception as e:
                    logger.debug(f"Failed to get image rects: {e}")
        except Exception as e:
            logger.debug(f"Failed to get image bboxes: {e}")
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

                if self._has_list_marker(line_text.strip()):
                    has_list_pattern = True
                    list_line_count += 1

        total_text = total_text.strip()

        # Caption patterns
        caption_patterns = [
            r'^(Figure|Fig\.?|Table|Tbl\.?|Chart|Graph|Image|Plate|Scheme)\s*\d*[\.:)]?',
            r'^(Source|Note|Notes)[\.:)]',
            r'^\d+\.\d+[\.:)]',  # Numbered captions like "1.1:" or "2.3."
        ]

        is_caption_pattern = any(re.match(pattern, total_text, re.IGNORECASE) for pattern in caption_patterns)
        normalized_total_text = self._normalized_heading_text(total_text)
        structured_total_match = self._structured_heading_match(normalized_total_text)
        is_bare_structured_heading = (
            structured_total_match is not None
            and structured_total_match.end() == len(normalized_total_text)
        )
        heading_features = self._build_heading_features(
            total_text,
            line_count=line_count,
            block_max_size=block_max_size,
            avg_font_size=avg_font_size,
            max_font_size=max_font_size,
            is_bold=is_bold,
            is_all_caps=is_all_caps,
            has_list_pattern=has_list_pattern,
            list_line_count=list_line_count,
        )
        is_heading_candidate = self._is_probable_heading_features(
            heading_features,
            require_layout_signal=True,
        )

        # Determine text type based on characteristics
        text_type = "text:normal"  # Default

        # Check if it's a footnote (small text at bottom of page with numeric marker)
        # page_num is 0-indexed here
        try:
            page_height = self.doc.load_page(page_num).rect.height if page_num >= 0 else 0
            if page_height > 0:
                block_y_pct = block["bbox"][1] / page_height
                if (block_y_pct > 0.85
                        and block_max_size < avg_font_size * 0.9
                        and re.match(r'^[\d\*\u2020\u2021\u00a7]+[\.\)\s]', total_text.strip())):
                    text_type = "text:footnote"
        except (IndexError, AttributeError):
            pass

        # Only run further classification if not already classified as footnote
        if text_type == "text:normal":
            # Check if it's a caption (various criteria)
            if (is_caption_pattern and not is_bare_structured_heading) or \
                    (self._is_near_image_or_table(block["bbox"], image_bboxes, table_bboxes) and
                     (len(total_text) < 150 or block_max_size < avg_font_size)):
                text_type = "text:caption"
            # Check if it's a title (very large font on first few pages)
            elif (page_num == self._get_first_text_page_num()
                  and is_heading_candidate
                  and self._has_title_layout_signal(heading_features)
                  and heading_features.length < 120
                  and line_count <= 2):
                text_type = "text:title"
            # Check if it's a section header (various criteria)
            elif is_heading_candidate and heading_features.length < 100 and line_count <= 2:
                text_type = "text:section"
            # Check if it's a list (majority of lines have list pattern)
            elif has_list_pattern and (list_line_count >= line_count * 0.5 or line_count == 1):
                text_type = "text:list"

        # Generate markdown
        markdown_text = self._convert_block_to_markdown(
            block,
            preserve_structured_headings=text_type in ("text:title", "text:section"),
            allow_heading_formatting=text_type in ("text:title", "text:section"),
        )

        # Debug logging for classification
        if text_type != "text:normal":
            logger.debug(
                f"Classified as {text_type}: '{total_text[:50]}...' (size: {block_max_size:.1f}, avg: {avg_font_size:.1f})")

        return markdown_text, text_type

    def _normalized_heading_text(self, text: str) -> str:
        return re.sub(r'\s+', ' ', (text or '')).strip()

    def _explicit_heading_match(self, normalized: str):
        explicit_heading_patterns = [
            r'^第\s*[一二三四五六七八九十百千\d]+\s*[條章节章節篇]',
            r'^(附錄|附件|附表)\s*[A-Za-z\d一二三四五六七八九十百千]*',
            r'^(Appendix|Chapter|Section)\b',
        ]
        for pattern in explicit_heading_patterns:
            match = re.match(pattern, normalized, re.IGNORECASE)
            if match:
                return match
        return None

    def _structured_heading_match(self, normalized: str):
        structured_heading_patterns = [
            r'^\d+(?:\.\d+)+',
            r'^\d+(?:-\d+)+',
            r'^\d+[\.)、．]',
            r'^[\(（]\d+[\)）]',
            r'^[一二三四五六七八九十百千]+[、．\.]',
            r'^[壹貳參肆伍陸柒捌玖拾]+[、．\.]',
            r'^[\(（][一二三四五六七八九十百千]+[\)）]',
        ]
        for pattern in structured_heading_patterns:
            match = re.match(pattern, normalized, re.IGNORECASE)
            if match and self._has_structured_marker_boundary(normalized, match):
                return match
        return None

    def _has_structured_marker_boundary(self, normalized: str, match) -> bool:
        """Avoid treating decimal/version prefixes as outline markers."""
        if match.end() >= len(normalized):
            return True
        next_char = normalized[match.end()]
        if next_char.isspace():
            return True
        if re.match(r'[\u4e00-\u9fff]', next_char):
            return True
        return not next_char.isascii() or not next_char.isalnum()

    def _has_list_marker(self, text: str) -> bool:
        normalized = self._normalized_heading_text(text)
        if not normalized:
            return False
        if re.match(r'^[\u2022•\-\*\u2013\u2014\u25AA\u25AB\u25CF\u25CB\u25A0\u25A1]\s+', normalized):
            return True
        if re.match(r'^(?:\d+|[a-zA-Z])[\.\)]\s+', normalized):
            return True
        return self._structured_heading_match(normalized) is not None

    def _is_explicit_heading_text(self, text: str) -> bool:
        normalized = self._normalized_heading_text(text)
        return self._explicit_heading_match(normalized) is not None

    def _is_structured_heading_text(self, text: str) -> bool:
        normalized = self._normalized_heading_text(text)
        match = self._structured_heading_match(normalized)
        if not match:
            return False
        return match.end() == len(normalized) or bool(normalized[match.end():].strip())

    def _build_heading_features(
        self,
        text: str,
        *,
        line_count: int = 1,
        block_max_size: float = 0.0,
        avg_font_size: float = 0.0,
        max_font_size: float = 0.0,
        is_bold: bool = False,
        is_all_caps: bool = False,
        has_list_pattern: bool = False,
        list_line_count: int = 0,
    ) -> _HeadingFeatures:
        normalized = self._normalized_heading_text(text)
        explicit_match = self._explicit_heading_match(normalized)
        structured_match = self._structured_heading_match(normalized)
        marker_match = explicit_match or structured_match
        text_after_marker = normalized[marker_match.end():].strip() if marker_match else normalized
        separator_count = sum(normalized.count(separator) for separator in (',', '，', '、', ':', '：'))
        size_ratio = block_max_size / avg_font_size if avg_font_size > 0 else 1.0
        max_size_ratio = block_max_size / max_font_size if max_font_size > 0 else 1.0
        has_structured_marker = structured_match is not None and bool(text_after_marker)
        has_long_clause_shape = (
            has_structured_marker
            and len(normalized) > 32
            and any(separator in text_after_marker for separator in (',', '，', '、', ':', '：'))
        )

        return _HeadingFeatures(
            normalized=normalized,
            length=len(normalized),
            line_count=line_count,
            size_ratio=size_ratio,
            max_size_ratio=max_size_ratio,
            is_bold=is_bold,
            is_all_caps=is_all_caps,
            has_list_pattern=has_list_pattern,
            list_line_count=list_line_count,
            is_explicit_marker=explicit_match is not None,
            is_structured_marker=has_structured_marker,
            text_after_marker=text_after_marker,
            has_cjk=bool(re.search(r'[\u4e00-\u9fff]', normalized)),
            has_checkbox_marker=bool(re.match(r'^[□■☑☐]', normalized)),
            has_sentence_punctuation=bool(re.search(r'[。！？!?；;]', normalized) or normalized.endswith('.')),
            has_trailing_continuation=normalized.endswith(('，', ',', '、', '；', ';')),
            separator_count=separator_count,
            has_form_field_shape=bool(re.search(r'_{3,}|\.{4,}|…{2,}', normalized)),
            has_long_clause_shape=has_long_clause_shape,
        )

    def _has_heading_layout_signal(self, features: _HeadingFeatures) -> bool:
        return (
            features.size_ratio >= 1.2
            or (features.is_bold and features.size_ratio >= 1.05)
            or features.is_all_caps
        )

    def _has_title_layout_signal(self, features: _HeadingFeatures) -> bool:
        return (
            features.max_size_ratio >= 0.85
            and (features.size_ratio >= 1.15 or features.is_bold or features.is_all_caps)
        )

    def _has_hard_body_shape(self, features: _HeadingFeatures) -> bool:
        return (
            features.has_checkbox_marker
            or features.has_form_field_shape
            or features.has_sentence_punctuation
            or features.has_trailing_continuation
            or features.length > 120
        )

    def _has_soft_body_shape(self, features: _HeadingFeatures) -> bool:
        if any(separator in features.text_after_marker for separator in (',', '，')) and features.length > 24:
            return True
        if any(separator in features.text_after_marker for separator in (':', '：')) and features.length > 30:
            return True
        if features.separator_count >= 3:
            return True
        if features.separator_count >= 2 and not features.is_structured_marker and features.length > 36:
            return True
        return features.has_long_clause_shape

    def _is_probable_heading_features(
        self,
        features: _HeadingFeatures,
        *,
        require_layout_signal: bool = False,
    ) -> bool:
        if not features.normalized:
            return False
        if features.line_count > 2:
            return False
        if self._has_hard_body_shape(features):
            return False

        has_layout_signal = self._has_heading_layout_signal(features)
        has_soft_body_shape = self._has_soft_body_shape(features)

        if features.is_explicit_marker:
            return features.length <= 80 and not (has_soft_body_shape and features.length > 60)

        if require_layout_signal and not has_layout_signal:
            return False

        if features.is_structured_marker:
            if features.has_long_clause_shape:
                return False
            if has_soft_body_shape and not has_layout_signal:
                return False
            return features.length <= 80

        if has_soft_body_shape:
            return False

        length_limit = 24 if features.has_cjk else 80
        return features.length <= length_limit

    def _is_probable_heading_text(self, text: str) -> bool:
        """Return True for short structural heading shapes without body blockers."""
        features = self._build_heading_features(text)
        return self._is_probable_heading_features(features, require_layout_signal=False)

    def _convert_block_to_markdown(
        self,
        block: Dict[str, Any],
        preserve_structured_headings: bool = False,
        allow_heading_formatting: bool = False,
    ) -> str:
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
            
            if (preserve_structured_headings
                    and self._is_structured_heading_text(line_text)
                    and self._is_probable_heading_text(line_text)):
                markdown_line = line_text
            elif list_match:
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
            elif allow_heading_formatting and line_size > avg_size * 1.5 and self._is_probable_heading_text(line_text):
                # Large text -> H1
                markdown_line = f"# {line_text}"
            elif allow_heading_formatting and line_size > avg_size * 1.3 and self._is_probable_heading_text(line_text):
                # Medium large text -> H2
                markdown_line = f"## {line_text}"
            elif allow_heading_formatting and line_size > avg_size * 1.15 and self._is_probable_heading_text(line_text):
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
            table_data = self._analyze_table_with_boundaries(table, extracted_data)

            renderer = TableRenderer(self.table_style)
            return renderer.render(table_data)

        except Exception as e:
            logger.warning(f"Failed to convert table to markdown: {e}")
            # Fallback: extract and render as simple markdown
            try:
                data = table.extract()
                if data:
                    table_data = TableData.from_2d_array(data)
                    renderer = TableRenderer(self.table_style)
                    return renderer.render(table_data)
            except Exception:
                pass
            return ""

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

    def _analyze_table_with_boundaries(self, table, extracted_data: List[List]) -> TableData:
        """Analyze table using cell boundaries if available. Returns TableData."""
        if not extracted_data:
            return TableData.empty()

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
            return TableData.from_raw(normalized, merge_info)
        else:
            # Fallback to pattern-based detection with conservative heuristics
            # to reduce false positives on legitimately sparse tables
            cell_spans = {}
            merged_cells = []
            is_complex = False

            # Pre-compute column emptiness ratio to avoid treating sparse columns as merges
            col_empty_count = [0] * col_count
            for r in range(row_count):
                for c in range(col_count):
                    if self._is_cell_empty(normalized[r][c]):
                        col_empty_count[c] += 1
            col_mostly_empty = [count > row_count * 0.5 for count in col_empty_count]

            # Track cells that are part of a horizontal merge to avoid false rowspan detection
            cells_in_colspan = set()

            # First pass: detect colspans (only when trailing empty cells are NOT in a mostly-empty column)
            for row_idx in range(row_count):
                for col_idx in range(col_count):
                    cell = normalized[row_idx][col_idx]
                    if cell is None or self._is_cell_empty(cell):
                        continue

                    colspan = 1
                    for check_col in range(col_idx + 1, col_count):
                        if (check_col < len(normalized[row_idx]) and
                                self._is_cell_empty(normalized[row_idx][check_col]) and
                                not col_mostly_empty[check_col]):
                            colspan += 1
                            cells_in_colspan.add((row_idx, check_col))
                        else:
                            break

                    if colspan > 1:
                        cell_spans[(row_idx, col_idx)] = (1, colspan)
                        is_complex = True

            # Second pass: detect rowspans (only for cells not part of a colspan)
            for row_idx in range(row_count):
                for col_idx in range(col_count):
                    cell = normalized[row_idx][col_idx]
                    if (row_idx, col_idx) in cells_in_colspan:
                        continue
                    if cell is None or self._is_cell_empty(cell):
                        continue
                    if (row_idx, col_idx) in cell_spans:
                        continue
                    # Skip if this column is mostly empty (sparse data, not merges)
                    if col_mostly_empty[col_idx]:
                        continue

                    rowspan = 1
                    for check_row in range(row_idx + 1, row_count):
                        if (check_row < len(normalized) and
                                col_idx < len(normalized[check_row]) and
                                self._is_cell_empty(normalized[check_row][col_idx]) and
                                (check_row, col_idx) not in cells_in_colspan):
                            rowspan += 1
                        else:
                            break

                    if rowspan > 1:
                        cell_spans[(row_idx, col_idx)] = (rowspan, 1)
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

            return TableData.from_raw(normalized, {
                'is_complex': is_complex,
                'cell_spans': cell_spans,
            })

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
        except (AttributeError, IndexError, TypeError) as e:
            logger.debug(f"Failed to get cell boundaries: {e}")
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
        
        row_count = len(normalized_data)
        col_count = max(len(r) for r in normalized_data) if normalized_data else 0
        return {
            'is_complex': len(merged_cells) > 0,
            'cell_spans': cell_spans,
            'merged_cells': merged_cells,
            'row_count': row_count,
            'col_count': col_count
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
                        # Decorative thumbnails were skipped during collection.
                        if self._is_decorative_image(img_rect, page):
                            continue
                        # Check if we have OCR result for this image
                        key = (page_num, xref)
                        if key in ocr_results_map:
                            ocr_text = (ocr_results_map[key] or "").strip()
                            if not ocr_text:
                                continue  # skip images that OCR'd to nothing
                            image_items.append(SimpleContent(
                                type="text:image_description",
                                content=f"<image_ocr_result>{ocr_text}</image_ocr_result>",
                                page=page_num + 1,
                                position_y=img_rect.y0
                            ))
                        else:
                            # OCR was requested but this image has no result (batch
                            # failure or partial result). Emit a lightweight placeholder
                            # — never dump raw base64 into a text/RAG output.
                            logger.warning(f"OCR result not found for image {xref} on page {page_num + 1}")
                            image_items.append(SimpleContent(
                                type="text:image_description",
                                content="<image_ocr_result>[image: OCR unavailable]</image_ocr_result>",
                                page=page_num + 1,
                                position_y=img_rect.y0,
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
                    result = self._extract_image_bytes(xref)
                    if result is None:
                        continue
                    image_bytes, _, _mime = result
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
                    result = self._extract_image_bytes(xref)
                    if result is None:
                        continue
                    image_bytes, _, mime = result

                    # Get image positions on page
                    img_rects = page.get_image_rects(xref)

                    for img_rect in img_rects:
                        base64_data = base64.b64encode(image_bytes).decode('utf-8')

                        image_items.append(SimpleContent(
                            type="image",
                            content=base64_data,
                            page=page_num + 1,
                            position_y=img_rect.y0,
                            mime_type=mime
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
    
    for item in json_data.get("content", []):
        item_type = item.get("type", "")
        content = item.get("content", "")

        # Skip empty content
        if not content or not content.strip():
            continue

        # Skip repeated headers/footers (tagged by _detect_repeated_content)
        if item_type in ("text:header", "text:footer"):
            continue

        # Add page separator if needed (but not at the beginning)
        if 'page' in item and item['page'] != current_page:
            if current_page is not None and markdown_parts:
                # Only add page break if we have content and it's not the first page
                markdown_parts.append("")
                markdown_parts.append(f"<!-- page {item['page']} -->")
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
            # OCR'd-image text — strip the internal provenance wrapper and emit
            # clean text (no code-fence / <ocr_result> noise) for a readable,
            # RAG-clean export.
            ocr_text = content
            if ocr_text.startswith('<image_ocr_result>') and ocr_text.endswith('</image_ocr_result>'):
                ocr_text = ocr_text[18:-19]
            markdown_parts.append(ocr_text.strip())
            markdown_parts.append("")  # Empty line after OCR result
            
        elif item_type == "table":
            # Tables are already in markdown or HTML format
            markdown_parts.append(content)
            # Table content already includes trailing newlines
            
        elif item_type == "text:footnote":
            # Format as markdown footnote definition if it matches N. pattern
            footnote_text = content.strip()
            m = re.match(r'^(\d+)[\.\)\s]+(.+)', footnote_text)
            if m:
                markdown_parts.append(f"[^{m.group(1)}]: {m.group(2)}")
            else:
                markdown_parts.append(footnote_text)
            markdown_parts.append("")

        elif item_type == "image":
            mime = item.get("mime_type") or 'image/png'
            markdown_parts.append(f'![Image](data:{mime};base64,{content})')
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
