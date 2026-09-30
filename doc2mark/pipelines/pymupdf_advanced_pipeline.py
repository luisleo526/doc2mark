import base64
import json
import logging
import numbers
import re
import unicodedata
from collections import OrderedDict, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Any, Union, Optional, Tuple

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
# Document and page strategy decisions live in core.strategy (shared with the Office
# route); pdf_routing measures the per-page signals they are made from.
from doc2mark.core.strategy import (  # noqa: E402
    decide_doc_strategy as _decide_doc_strategy,
    decide_page_route as _decide_page_route,
    document_signals as _document_signals,
    judge_text_layer as _judge_text_layer,
    pages_without_text as _pages_without_text,
    VERBATIM_TAIL_REASONS as _VERBATIM_TAIL_REASONS,
    REASON_ILLEGIBLE as _REASON_ILLEGIBLE,
    MIN_UNCAPTURED_RASTER as _MIN_UNCAPTURED_RASTER,
)
from doc2mark.pipelines import pdf_routing  # noqa: E402
_TINY_IMAGE_FRACTION = 0.10     # images smaller than this (of page w AND h) are decorative

# --- Neighbor-page PDF context for OCR --------------------------------------
# Gemini's INLINE request cap is ~20MB total; stay under it so an inline PDF
# part never 400s. (OpenAI's file cap is 50MB but we gate to the tighter inline
# limit.)
_CONTEXT_PDF_MAX_BYTES = 18 * 1024 * 1024
_WINDOW_CACHE_MAXLEN = 4   # windows overlap; far pages are never reused -> tiny LRU

# --- Running headers/footers ("page chrome") ---------------------------------
# Text lines at the top or bottom of the page that repeat from page to page
# (running headers and footers, page numbers) are page furniture, not content.
# Only strong evidence removes a line; see PDFLoader._detect_page_chrome.
_CHROME_BAND = 0.12            # top/bottom fraction of the page height searched for chrome
_CHROME_MIN_PAGES = 3          # chrome repeats on more than a couple of pages...
_CHROME_MIN_SHARE = 0.5        # ...and on more than half of the document's pages
_CHROME_SLOT_TOLERANCE = 4.0   # points: a running header sits at the same height on every page
_CHROME_RUN_DISTANCE = 2       # a per-chapter header repeats on a page at most this far away
_CHROME_HEADING_RATIO = 1.15   # font size / body size from which a line reads as a heading
_CHROME_NUMBER_SIZE_RATIO = 1.5  # page numbers are never bigger than this x body size (KPI figures are)
_CHROME_JUDGE_THRESHOLD = 0.5  # boilerplate_judge probability from which an ambiguous line is chrome
# Page-number shapes (NFKC-normalised, case-folded text); the number in group "n" is masked
# to "#". Numbers anywhere else in a line stay literal: "Invoice No. 1001", "Step 3",
# "INV-2024-0001" or "Chapter 3" never look like page numbers. In "N/M" and "N of M" the total
# M stays literal too, and a page number is never larger than it: 15/03 ... 20/03 are dates, but
# 1/15 ... 6/15 on six pages read as page numbers.
_PAGE_NUMBER_PATTERNS = [
    (re.compile(r"第\s*(?P<n>\d{1,4})\s*[頁页]"), "第#頁"),                                  # 第 N 頁
    (re.compile(r"(?<!\w)(?P<k>page|pg|pp|p|seite|página|pagina)\.?\s*(?P<n>\d{1,4})(?![\w.,:/-])"),
     r"\g<k> #"),                                                                             # Page N, p. N
    (re.compile(r"(?<![\w.,:/-])(?P<n>\d{1,4})(?P<t>\s*/\s*\d{1,4}|\s+of\s+\d{1,4})(?![\w.,:/-])"),
     r"#\g<t>"),                                                                              # N/M, N of M
    (re.compile(r"(?<![\w-])[-–—]\s*(?P<n>\d{1,4})\s*[-–—](?![\w-])"), "-#-"),                # - N -
    (re.compile(r"^[\[(]?(?P<n>\d{1,4})[\])]?$"), "#"),                                        # N alone
    (re.compile(r"^(?P<n>\d{1,4})(?P<t>\s*[|·•–—]|\s+-\s)"), r"#\g<t>"),                      # N | ACME
    (re.compile(r"(?P<t>[|·•–—]\s*|\s-\s+)(?P<n>\d{1,4})$"), r"\g<t>#"),                      # ACME | N
]
_ROMAN_PAGE_NUMBER = re.compile(r"(?:(?:page|p\.?)\s*)?[-–—]?\s*(?P<r>[ivxlcdm]+)\s*[-–—]?")
_ROMAN_NUMERAL = re.compile(r"m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})")
# Words a bare page number may carry around its number ("Page 3 of 12", "Seite 3 von 12", "第 3 頁，共 12 頁").
_PAGE_WORDS = {"page", "pg", "pp", "p", "seite", "página", "pagina", "第", "頁", "页"}
_PAGE_NUMBER_WORDS = _PAGE_WORDS | {"of", "von", "de", "sur", "di", "van", "共"}

# boilerplate_judge(line_text, context) -> probability that a repeated line is page
# chrome rather than content, or None when it cannot tell (see PDFLoader).
BoilerplateJudge = Callable[[str, Dict[str, Any]], Optional[float]]

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


@dataclass(slots=True)
class _PageLine:
    """One text line of a page for running header/footer detection.

    ``rect`` is the line bbox as ``get_text()`` reports it (unrotated page space);
    ``x0``..``y1`` are the same box as displayed, i.e. with ``/Rotate`` applied, so
    "top of the page" means what a reader sees. ``zone`` is "header"/"footer" for
    lines in the top/bottom band and None elsewhere.
    """
    page: int
    text: str
    rect: Tuple[float, float, float, float]
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    zone: Optional[str] = None
    edge: float = 0.0               # distance of the line's centre from its zone's page edge
    norm: str = ""
    template: Optional[str] = None
    numbers: Tuple[int, ...] = ()
    totals: Tuple[Tuple[int, int], ...] = ()  # (N, M) of each masked "N/M" or "N of M"


class PDFLoader:
    """PDF loader that extracts content in reading order and exports to various formats"""

    def __init__(self, pdf_path: Union[str, Path], ocr=None, table_style: Union[str, TableStyle] = None,
                 legibility_judge=None, boilerplate_judge: Optional[BoilerplateJudge] = None):
        """Open ``pdf_path``.

        Args:
            pdf_path: PDF file to load.
            ocr: Optional OCR provider for images and image-dominant pages.
            table_style: Output style for complex tables (see ``TableStyle``).
            legibility_judge: Optional ``judge(page_text) -> Optional[float]`` hook of the
                text-layer quality gate; see :func:`doc2mark.core.strategy.judge_text_layer`
                for its contract.
            boilerplate_judge: Optional ``boilerplate_judge(line_text, context) -> Optional[float]``.
                Running headers/footers are removed only on strong evidence, and only a
                bare page number or a line repeating a title or heading loses every copy
                (see ``_detect_page_chrome``). The lines the rule keeps although they repeat
                in the top/bottom band are *ambiguous*. When a judge is given, it is asked
                about them and about nothing else, once per distinct line (per group of
                numbered lines): ``line_text`` is the line as extracted (a group's first
                line); ``context`` is a dict with ``zone`` ("header"/"footer"), ``pages``
                (1-based pages where the rule kept the line), ``repeated_on`` (1-based pages
                where it repeats, including copies already removed), ``page_count``,
                ``font_size``, ``body_font_size`` and ``reason``, why the rule kept it:

                * "numbered_label": a number labelled with anything but a page word that
                  follows the page order on most pages ("3 | ACME Corp", "Lesson · 3"),
                  which may be a page number or per-page content; asked once per group.
                  At or above 0.5 the group is handled like a running header with a page
                  number: its copies at the page edge are chrome except the first, which
                  stays and is then asked about as a "first_occurrence"; copies the rule
                  cannot take off the page edge stay, as plain text.
                * "first_occurrence": the first copy of a running header or footer, kept
                  so its text is not lost (every other copy is already removed).
                * "few_pages": it repeats on too few pages.
                * "attached_to_content": no clear gap separates it from the page's text,
                  or other text sits between it and the page edge.

                The judge returns the probability (0..1) that the line is page chrome rather
                than content: at or above 0.5 every kept copy of the line is treated as
                chrome (typed text:header / text:footer, left out of the Markdown), except
                as said for "numbered_label"; below 0.5, None, an invalid value or an
                exception keeps it. Without a judge, ambiguous lines are kept.
        """
        self.pdf_path = Path(pdf_path)
        self.doc = None
        self.ocr = ocr  # Store the OCR instance
        self.boilerplate_judge = boilerplate_judge
        self._first_text_page_num = None
        self._legibility_judge = legibility_judge
        self._page_measures: Dict[int, "pdf_routing.PageMeasure"] = {}
        self._page_routes: Dict[int, Tuple[str, str]] = {}
        self._rendered_pages: set = set()  # pages whose content is the OCR of their render
        self._judged_pages: set = set()    # pages the legibility judge was asked about
        self._chrome_regions: Optional[Dict[int, List[Tuple[Tuple[float, float, float, float], str, bool]]]] = None
        # OCR options of the conversion under way: the page text depends on them (see _text_page).
        self._text_options: Tuple[bool, Optional[Dict[tuple, str]]] = (False, None)

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
        self._copies = pdf_routing.PageCopies(self.doc)  # editable page copies for the routing checks
        self._record_rotated_crop_boxes()

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
            ocr_images: Whether to use OCR to convert images to text descriptions
                (implies image extraction; needs an OCR instance)
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
            plus, when applicable, ``ocr_routing``, ``text_layer_quality`` and
            ``hidden_text`` (see _record_routing).
        """
        # Initialize document structure
        document = {
            "filename": self.pdf_path.name,
            "pages": len(self.doc),
            "content": []  # Simple array of content items
        }

        self._rendered_pages = set()

        # OCR needs the images: asking for OCR implies extracting them for it.
        if ocr_images and self.ocr is None:
            logger.warning(f"{self.pdf_path.name}: ocr_images=True but no OCR provider is configured; "
                           f"images are not OCR'd")
            ocr_images = False
        if ocr_images and not extract_images:
            logger.info("ocr_images=True implies image extraction for OCR")
            extract_images = True

        # If OCR is requested, collect all images first for batch processing
        ocr_results_map = {}
        # The page text depends on this run's OCR (see _text_source): the document-wide passes
        # that read every page through _text_page run afresh for it.
        self._text_options = (ocr_images, ocr_results_map)
        self._chrome_regions = None
        self._first_text_page_num = None
        if extract_images and ocr_images:
            if show_progress:
                logger.info("Collecting all images for batch OCR processing...")

            self._window_pdf_cache = OrderedDict()
            all_images_info = self._collect_all_images()

            if all_images_info:
                if show_progress:
                    logger.info(f"Processing {len(all_images_info)} images with batch OCR...")

                # Whole-page renders ask the model to ALSO synthesize structured
                # page_markdown (a readable document instead of a flat OCR dump);
                # embedded figures never do. The flag applies to a whole batch, so the
                # two kinds go in separate batches when a document mixes them.
                renders = [info for info in all_images_info if info.get("is_page_render")]
                figures = [info for info in all_images_info if not info.get("is_page_render")]
                for batch, synthesis in ((renders, True), (figures, False)):
                    if batch:
                        self._ocr_batch(batch, ocr_results_map, synthesis_markdown=synthesis,
                                        show_progress=show_progress)

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

        self._record_routing(document, ocr_active=bool(ocr_images))

        return document

    def _ocr_batch(self, batch: List[Dict[str, Any]], ocr_results_map: Dict[tuple, str], *,
                   synthesis_markdown: bool, show_progress: bool) -> None:
        """OCR one batch of collected images into ``ocr_results_map`` (keyed by page and xref).

        A failed batch leaves its images without results: they become lightweight
        placeholders (see _extract_images_simple) while the deterministic text/table
        layer is still emitted. Never fall back to base64 extraction: dumping
        megabytes of base64 image data into a text/RAG output is useless and harmful.
        """
        try:
            # Prepare image data for batch processing
            image_data_list = [base64.b64decode(info["base64"]) for info in batch]
            # Per-image neighbor-page PDF context (aligned positionally
            # with image_data_list). All None when the feature is off.
            context_pdfs = [info.get("context_pdf_b64") for info in batch]

            # Pass language configuration if available
            kwargs = {}
            if hasattr(self.ocr, 'config') and self.ocr.config and self.ocr.config.language:
                kwargs['language'] = self.ocr.config.language
                logger.info(f"🌍 Passing language configuration to OCR: {self.ocr.config.language}")

            # Only inject context when at least one image carries it, so
            # the off-default path stays byte-identical (cache keys + call).
            if any(context_pdfs):
                kwargs['context_pdfs'] = context_pdfs
            if synthesis_markdown:
                kwargs['synthesis_markdown'] = True

            # Always use batch processing for efficiency
            logger.info(f"🚀 Using batch OCR processing for {len(image_data_list)} images")
            ocr_results = self.ocr.batch_process_images(image_data_list, **kwargs)

            # Map results back to image locations
            for info, result in zip(batch, ocr_results):
                ocr_results_map[(info["page_num"], info["xref"])] = (
                    result.text if hasattr(result, 'text') else str(result))

            if show_progress:
                logger.info(f"Successfully processed {len(image_data_list)} images with configured OCR")
        except Exception as e:
            logger.error(f"Batch OCR processing failed: {e}; emitting image placeholders")

    def _detect_repeated_content(self, document: Dict[str, Any]) -> None:
        """Retype repeated page furniture that only exists as whole items.

        Running header/footer *text lines* are recognised line by line before
        conversion (``_detect_page_chrome``) and arrive here already typed
        ``text:header`` / ``text:footer``. This pass handles tables: a table repeated
        unchanged at the same place in the top or bottom band of most pages (a
        letterhead, a logo box) keeps its first copy, and the later copies are retyped
        ``text:header`` / ``text:footer``, which ``pdf_to_markdown`` leaves out. OCR
        text of images and page renders (``text:image_description``, including the
        ``[image: OCR unavailable]`` placeholder) is page content or a failure marker
        and is never retyped. Items are retyped, never removed.

        Modifies document["content"] in place.
        """
        total_pages = document.get("pages", 0)
        content = document.get("content", [])
        if total_pages < _CHROME_MIN_PAGES or not content:
            return

        heights: Dict[int, float] = {}
        tables: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        for item in content:
            page, pos_y = item.get("page"), item.get("position_y")
            if item.get("type") != "table" or page is None or pos_y is None:
                continue
            if page not in heights:
                heights[page] = self._page_height(page - 1)
            if pos_y < _CHROME_BAND * heights[page]:
                zone = "header"
            elif pos_y > (1 - _CHROME_BAND) * heights[page]:
                zone = "footer"
            else:
                continue
            tables[(zone, " ".join(item.get("content", "").split()))].append(item)

        for (zone, _), items in tables.items():
            first = min(items, key=lambda item: (item["page"], item["position_y"]))
            copies = [item for item in items
                      if abs(item["position_y"] - first["position_y"]) <= _CHROME_SLOT_TOLERANCE]
            pages = {item["page"] for item in copies}
            if len(pages) < _CHROME_MIN_PAGES or len(pages) <= _CHROME_MIN_SHARE * total_pages:
                continue
            for item in copies:
                if item is not first:
                    item["type"] = f"text:{zone}"

    def _record_rotated_crop_boxes(self) -> None:
        """Record, before any table detection runs, how to read the tables of rotated pages.

        PyMuPDF's find_tables() derotates a /Rotate page by rewriting it, reports table boxes
        as the page is displayed, and deletes the page's own /CropBox. On a cropped page (a
        CropBox that is not its MediaBox) it derotates with the cropped size but lays the
        page out uncropped, so its boxes come back shifted. Per rotated page:

        * ``_rotated_crop_boxes``: its own raw /CropBox entry. ``_uncrop_for_tables`` takes it
          off before the page's tables are found, so find_tables() and everything else that
          reads the tables sees the whole MediaBox in one frame; ``_restore_cropbox`` puts it
          back right after.
        * ``_crop_offsets``: where the visible area (the CropBox within the MediaBox) starts in
          the MediaBox, which brings those uncropped boxes into the frame of get_text().
        * ``_rotated_cropped_pages``: pages whose boxes cannot be brought back (a CropBox
          inherited from /Pages, which stays while find_tables() runs, or page boxes that
          cannot be read). They get no table suppression: the table's text may repeat, but
          no text is dropped.
        """
        self._rotated_crop_boxes: Dict[int, str] = {}
        self._crop_offsets: Dict[int, Tuple[float, float]] = {}
        self._rotated_cropped_pages: set = set()
        self._table_frames: Dict[int, Any] = {}
        for number in range(len(self.doc)):
            try:
                page = self.doc.load_page(number)
                if not page.rotation:
                    continue
                # Recorded before anything else is read: a page that fails below still gets its
                # CropBox back after find_tables(), only no table suppression.
                kind, own = self.doc.xref_get_key(page.xref, "CropBox")
                if kind in ("array", "xref"):
                    self._rotated_crop_boxes[number] = own
                media = self._box_numbers(self._inherited_page_entry(page.xref, "MediaBox"))
                if media is None:
                    raise ValueError("no MediaBox")
                kind, parent = self.doc.xref_get_key(page.xref, "Parent")
                inherited = self._inherited_page_entry(int(parent.split()[0]), "CropBox") if kind == "xref" else None
                if inherited is not None and self._box_numbers(inherited) != media:
                    self._rotated_cropped_pages.add(number)
                    continue
                if number not in self._rotated_crop_boxes:
                    continue
                crop = self._box_numbers(own)
                visible = (max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3]))
                if visible[0] >= visible[2] or visible[1] >= visible[3]:
                    raise ValueError(f"CropBox {own} outside the MediaBox")
                self._crop_offsets[number] = (visible[0] - media[0], media[3] - visible[3])
            except Exception as e:
                logger.debug(f"Could not read the page boxes of page {number + 1}, its tables are not suppressed: {e}")
                self._rotated_cropped_pages.add(number)

    def _inherited_page_entry(self, xref: int, key: str) -> Optional[str]:
        """A page's /CropBox or /MediaBox entry, its own or inherited through /Parent."""
        for _ in range(64):  # the page tree is shallow; this only guards against cycles
            kind, value = self.doc.xref_get_key(xref, key)
            if kind in ("array", "xref"):
                return value
            kind, parent = self.doc.xref_get_key(xref, "Parent")
            if kind != "xref":
                return None
            xref = int(parent.split()[0])
        return None

    def _box_numbers(self, source: Optional[str]) -> Optional[Tuple[float, float, float, float]]:
        """The rectangle of a PDF box entry (``[0 0 595 842]``, an indirect ``12 0 R``, or an
        array holding indirect numbers) with its corners in order, as MuPDF reads it; None
        without an entry. Raises ValueError when the entry is not four numbers."""
        if not source:
            return None
        if not source.lstrip().startswith("["):
            source = self.doc.xref_object(int(source.split()[0]), compressed=True)
        tokens = source.strip().strip("[]").split()
        values = []
        while tokens:
            if len(tokens) >= 3 and tokens[2] == "R":
                values.append(float(self.doc.xref_object(int(tokens[0]), compressed=True)))
                tokens = tokens[3:]
            else:
                values.append(float(tokens.pop(0)))
        if len(values) != 4:
            raise ValueError(f"not a rectangle: {source!r}")
        x0, y0, x1, y1 = values
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def _page_height(self, page_index: int) -> float:
        """Displayed height of a page (``/Rotate`` applied); 800 when it cannot be read."""
        try:
            return self.doc.load_page(page_index).rect.height or 800.0
        except Exception:
            return 800.0

    def _page_chrome_regions(self, page_index: int) -> List[Tuple[Tuple[float, float, float, float], str, bool]]:
        """Running header/footer lines of a page as ``[(line bbox, "header" | "footer", kept), ...]``.

        ``kept`` marks a line that stays as content, emitted as plain text: the first copy of a
        running header, a numbered label, a page number the rule leaves. The whole document is
        analysed once, on first use (``_detect_page_chrome``).
        """
        if getattr(self, "_chrome_regions", None) is None:
            try:
                self._chrome_regions = self._detect_page_chrome()
            except Exception as e:
                logger.warning(f"Running header/footer detection failed, keeping every line: {e}")
                self._chrome_regions = {}
        return self._chrome_regions.get(page_index, [])

    def _detect_page_chrome(self) -> Dict[int, List[Tuple[Tuple[float, float, float, float], str, bool]]]:
        """Find running headers, footers and page numbers, line by line, in the whole document.

        A line is page chrome only on strong evidence. It sits in the top or bottom
        ``_CHROME_BAND`` of the displayed page, at a height ("slot") where lines with that
        evidence appear on at least ``_CHROME_MIN_PAGES`` pages and on more than half of all
        pages, and there it

        * repeats: the same normalised text (Unicode width and whitespace folded, case
          ignored, with at least one letter) on ``_CHROME_MIN_PAGES`` pages, or on a page at
          most ``_CHROME_RUN_DISTANCE`` away (per-chapter running headers). Heading-sized
          text must repeat on more than half of all pages, so the titles of consecutive
          slides are not taken for running headers; or
        * is a page number: the same text once page-number-shaped tokens are masked
          (``_page_number_template``), with one number per page that follows the page order
          on ``_CHROME_MIN_PAGES`` pages (``_page_number_lines``), and nothing around the
          number but page words (``3``, ``- 3 -``, ``Page 3 of 12``, ``ACME | Page 3``,
          ``第 3 頁``). A bare page number printed elsewhere on a page before or after the
          numbered ones (a cover or contents page with its own header or footer) continues that
          numbering when it has the same printed form and sits about as close to its page edge.

        It must also be at the page edge: every row between it and the edge is chrome too,
        and a clear gap separates the chrome from the page's content, which is not a table
        continuing under a repeated header row (``_peel_chrome_rows``).

        A number labelled with anything but a page word that follows the page order
        (``3 | ACME Corp``, ``Lesson · 3``, ``Ticket | 3``) may be a page number or per-page
        content, so the rule keeps it on every page, as plain text (a page number beside it in
        the same row still goes); the optional ``boilerplate_judge`` may say it is chrome. A
        bare number that follows the page order (``1124``, ``2019``) cannot be told from a page
        number and is treated as one.

        Verbatim first: a chrome line loses *every* copy only when it is a bare page number
        or when its text is a title or heading of the document (heading-sized content on or
        before the page of its first copy: a title that is also the running header; in the
        middle of a page it is looked for only on that page and the ``_CHROME_RUN_DISTANCE``
        pages before it).
        Otherwise its first copy stays as content, so a statement title, a unit note or a
        disclaimer repeated as page furniture is not lost; only the later copies are chrome.

        Lines that repeat but miss the evidence bar are ambiguous and kept, like those first
        copies, unless the optional ``boilerplate_judge`` says they are chrome.

        Returns ``{page index: [(line bbox in get_text() space, "header" | "footer", kept), ...]}``
        where ``kept`` marks a line that stays as content, emitted as plain text.
        """
        page_count = len(self.doc) if self.doc is not None else 0
        if page_count < 2:
            return {}

        pages_rows: List[Tuple[List[List[_PageLine]], List[List[_PageLine]]]] = []
        band_lines: List[_PageLine] = []
        body_lines: List[_PageLine] = []
        sizes: Dict[float, int] = defaultdict(int)
        for page_index in range(page_count):
            with self._text_page(self.doc.load_page(page_index)) as page:
                lines = self._page_lines(page, page_index)
            for line in lines:
                sizes[round(line.size, 1)] += len(line.text)
                if line.zone:
                    line.norm = self._normalized(line.text)
                    line.template, line.numbers, line.totals = self._page_number_template(line.norm)
                    band_lines.append(line)
                else:
                    body_lines.append(line)
            pages_rows.append(self._edge_rows(self._rows(lines)))
        if not band_lines:
            return {}
        body_size = self._weighted_median(list(sizes.items()))
        heading_size = body_size * _CHROME_HEADING_RATIO
        judge = getattr(self, "boilerplate_judge", None)

        # Evidence for each band line, within its slot. ``recurring``: its text is in the
        # slot on another page too, the widest notion of "repeated" (the judge's band).
        # ``labelled``: numbered like the pages, but with a label that is not a page word.
        slots = self._chrome_slots(band_lines)
        recurring, repeated, numbered, labelled = set(), set(), set(), set()
        slot_of: Dict[int, int] = {}
        for slot_id, slot in enumerate(slots):
            pages_by_norm = defaultdict(set)
            for line in slot:
                pages_by_norm[line.norm].add(line.page)
                slot_of[id(line)] = slot_id
            for line in slot:
                pages = pages_by_norm[line.norm]
                if not re.search(r"[^\W\d_]", line.norm) or len(pages) < 2:
                    continue
                recurring.add(id(line))
                if line.size >= heading_size:
                    strong = len(pages) >= _CHROME_MIN_PAGES and len(pages) > _CHROME_MIN_SHARE * page_count
                else:
                    strong = (len(pages) >= _CHROME_MIN_PAGES
                              or any(0 < abs(page - line.page) <= _CHROME_RUN_DISTANCE for page in pages))
                if strong:
                    repeated.add(id(line))
            for line in self._page_number_lines(slot, body_size):
                (numbered if self._is_page_number_template(line.template) else labelled).add(id(line))

        def page_context(lines: List[_PageLine], reason: str, repeated_on) -> Dict[str, Any]:
            first = min(lines, key=lambda line: (line.page, line.y0))
            return {
                "zone": first.zone,
                "pages": sorted({line.page + 1 for line in lines}),
                "repeated_on": sorted(repeated_on),
                "page_count": page_count,
                "font_size": round(first.size, 2),
                "body_font_size": round(body_size, 2),
                "reason": reason,
            }

        # Numbered labels on most pages go to the judge; one it calls chrome is handled like a
        # running header with a page number from here on, and not asked about again (but for
        # its first copy, see below).
        judged: set = set()
        if judge is not None:
            groups: Dict[Tuple[int, str], List[_PageLine]] = defaultdict(list)
            for line in band_lines:
                if id(line) in labelled:
                    groups[(slot_of[id(line)], line.template)].append(line)
            for lines in groups.values():
                pages = {line.page + 1 for line in lines}
                if len(pages) < _CHROME_MIN_PAGES or len(pages) <= _CHROME_MIN_SHARE * page_count:
                    continue
                first = min(lines, key=lambda line: (line.page, line.y0))
                if self._judged_boilerplate(judge, first.text, page_context(lines, "numbered_label", pages)):
                    ids = {id(line) for line in lines}
                    numbered |= ids
                    labelled -= ids
                    judged |= ids

        capable = set()
        for slot_id, slot in enumerate(slots):
            evident_pages = {line.page for line in slot if id(line) in repeated or id(line) in numbered}
            if len(evident_pages) >= _CHROME_MIN_PAGES and len(evident_pages) > _CHROME_MIN_SHARE * page_count:
                capable.add(slot_id)
        candidates = {line_id for line_id in repeated | numbered if slot_of[line_id] in capable}

        # A bare page number printed elsewhere on a page before or after the numbered ones (a
        # cover or contents page with its own header or footer) continues the page numbering: same
        # offset and printed form ("(3)" is not "3"), in either band, and at most about a line
        # further from its page edge than the numbering's own lines. It is still removed only at
        # the page edge.
        series: Dict[tuple, List[_PageLine]] = defaultdict(list)
        for line in band_lines:
            if id(line) in candidates and id(line) in numbered and self._is_bare_page_number(line.template):
                for position, value in enumerate(line.numbers):
                    series[(line.template, position, value - line.page - 1)].append(line)
        for line in band_lines:
            if (id(line) in candidates or line.template is None or not self._is_bare_page_number(line.template)
                    or line.size > body_size * _CHROME_NUMBER_SIZE_RATIO or any(n > m for n, m in line.totals)):
                continue
            for position, value in enumerate(line.numbers):
                members = series.get((line.template, position, value - line.page - 1), [])
                pages = {member.page for member in members}
                if (len(pages) >= _CHROME_MIN_PAGES and not min(pages) <= line.page <= max(pages)
                        and self._number_shape(line.norm) in {self._number_shape(member.norm) for member in members}
                        and line.edge <= max(member.edge + 1.5 * member.size for member in members)):
                    numbered.add(id(line))
                    candidates.add(id(line))
                    break

        page_numbers = {id(line) for line in band_lines if id(line) in candidates and id(line) in numbered
                        and self._is_bare_page_number(line.template)}
        chrome: Dict[int, _PageLine] = {}
        for top_rows, bottom_rows in pages_rows:
            for zone, edge_first in (("header", top_rows), ("footer", bottom_rows)):
                for row in self._peel_chrome_rows(edge_first, zone, candidates, page_numbers, labelled):
                    chrome.update((id(line), line) for line in row if id(line) not in labelled)

        def repeat_key(line: _PageLine) -> Tuple[str, str]:
            return line.zone, line.template if id(line) in numbered else line.norm

        # Verbatim first: unless the line is a bare page number or repeats a title or heading
        # (heading-sized content on or before the page of its first copy), its first copy
        # stays (see the docstring). A contents entry or other body-sized text with the same
        # words is not the title. Titles near the top or bottom of a page are in the lines read
        # above; the middle of a page is read only for the page of a first copy and the
        # _CHROME_RUN_DISTANCE pages before it.
        firsts: Dict[Tuple[str, str], _PageLine] = {}
        for line in chrome.values():
            if id(line) in numbered and self._is_bare_page_number(line.template):
                continue
            key = repeat_key(line)
            if key not in firsts or (line.page, line.y0) < (firsts[key].page, firsts[key].y0):
                firsts[key] = line
        near = {page for line in firsts.values()
                for page in range(max(0, line.page - _CHROME_RUN_DISTANCE), line.page + 1)}
        headings: Dict[str, int] = {}
        for line in band_lines + body_lines:
            if line.size >= heading_size and id(line) not in chrome:
                norm = line.norm or self._normalized(line.text)
                headings[norm] = min(headings.get(norm, page_count), line.page)
        for page_index in sorted(near):
            for size, text in self._middle_lines(page_index):
                if size >= heading_size:
                    norm = self._normalized(text)
                    headings[norm] = min(headings.get(norm, page_count), page_index)
        first_copies = [line for line in firsts.values() if headings.get(line.norm, page_count) > line.page]
        for line in first_copies:
            del chrome[id(line)]
        kept = {id(line) for line in first_copies}

        if judge is not None:
            copies: Dict[Tuple[str, str], set] = defaultdict(set)
            ambiguous: Dict[Tuple[str, str], List[_PageLine]] = defaultdict(list)
            for line in band_lines:
                if id(line) in recurring or id(line) in numbered:
                    copies[repeat_key(line)].add(line.page + 1)
                    if id(line) not in chrome:
                        ambiguous[repeat_key(line)].append(line)
            for key, lines in ambiguous.items():
                first = min(lines, key=lambda line: (line.page, line.y0))
                if id(first) in kept:
                    reason = "first_occurrence"
                elif all(id(line) in judged for line in lines):
                    continue  # asked about as a numbered label already
                elif id(first) in candidates:
                    reason = "attached_to_content"
                else:
                    reason = "few_pages"
                if self._judged_boilerplate(judge, first.text, page_context(lines, reason, copies[key])):
                    chrome.update((id(line), line) for line in lines)
                    kept.difference_update(id(line) for line in lines)

        # A kept line with a number counting with the pages (a page number the rule leaves, a
        # numbered label) and a kept first copy of a running header are emitted as plain text, so
        # they never turn into a heading, list item or footnote; a heading-sized first copy
        # without such a number stays in its block and is classified like any other content (a
        # title repeated on every page).
        regions: Dict[int, List[Tuple[Tuple[float, float, float, float], str, bool]]] = defaultdict(list)
        for line in chrome.values():
            regions[line.page].append((line.rect, line.zone, False))
        plain = [line for line in band_lines
                 if id(line) not in chrome and (id(line) in numbered or id(line) in labelled)]
        plain += [line for line in first_copies
                  if id(line) in kept and id(line) not in numbered and line.size < heading_size]
        for line in plain:
            regions[line.page].append((line.rect, line.zone, True))
        return dict(regions)

    def _page_lines(self, page, page_index: int) -> List[_PageLine]:
        """The page's non-empty text lines near its top and bottom edges, with displayed
        coordinates and band zone.

        Only the outer 2.5 bands of an unrotated page are extracted: the band and the rows
        right after it are all the chrome decision needs (``_middle_lines`` reads the rest when
        needed). Rotated pages are extracted whole.
        """
        self._restore_cropbox(page, page_index)
        rect = page.rect
        height = rect.height or 1.0
        matrix = page.rotation_matrix if page.rotation else None
        if matrix is None:
            reach = 2.5 * _CHROME_BAND * height
            clips = [pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + reach),
                     pymupdf.Rect(rect.x0, rect.y1 - reach, rect.x1, rect.y1)]
        else:
            clips = [None]
        lines = []
        blocks = [block for clip in clips
                  for block in page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_LIGATURES, clip=clip)["blocks"]]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                text = "".join(span.get("text", "") for span in spans).strip()
                if not text:
                    continue
                shown = pymupdf.Rect(self._visual_box(line))
                if matrix is not None:
                    shown = shown * matrix
                centre = (shown.y0 + shown.y1) / 2
                if centre < _CHROME_BAND * height:
                    zone = "header"
                elif centre > (1 - _CHROME_BAND) * height:
                    zone = "footer"
                else:
                    zone = None
                lines.append(_PageLine(page=page_index, text=text, rect=tuple(line["bbox"]),
                                       x0=shown.x0, y0=shown.y0, x1=shown.x1, y1=shown.y1,
                                       size=max((span.get("size", 0.0) for span in spans), default=0.0),
                                       zone=zone, edge=centre if zone != "footer" else height - centre))
        return lines

    def _middle_lines(self, page_index: int) -> List[Tuple[float, str]]:
        """``(font size, text)`` of the lines ``_page_lines`` leaves out: the middle of an
        unrotated page, with a margin reaching into the edge areas so that no line is cut."""
        with self._text_page(self.doc.load_page(page_index)) as page:
            if page.rotation:
                return []
            rect = page.rect
            reach = 2.5 * _CHROME_BAND * (rect.height or 1.0) - 50
            clip = pymupdf.Rect(rect.x0, rect.y0 + reach, rect.x1, rect.y1 - reach)
            if clip.is_empty:
                return []
            lines = []
            for block in page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_LIGATURES, clip=clip)["blocks"]:
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text = "".join(span.get("text", "") for span in spans).strip()
                    if text:
                        lines.append((max((span.get("size", 0.0) for span in spans), default=0.0), text))
            return lines

    @staticmethod
    def _number_shape(norm: str) -> str:
        """A normalised line with its numbers masked: the printed form of a page number."""
        return re.sub(r"\d+", "#", norm)

    @staticmethod
    def _normalized(text: str) -> str:
        """Text as running headers are compared: Unicode width folded, whitespace collapsed, case ignored."""
        return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()

    @staticmethod
    def _rows(lines: List[_PageLine]) -> List[List[_PageLine]]:
        """Lines grouped into rows (vertically overlapping lines), top to bottom as displayed."""
        rows: List[List[_PageLine]] = []
        top = bottom = 0.0
        for line in sorted(lines, key=lambda line: (line.y0, line.x0)):
            if rows:
                overlap = min(bottom, line.y1) - max(top, line.y0)
                if overlap >= 0.5 * max(min(bottom - top, line.y1 - line.y0), 1e-6):
                    rows[-1].append(line)
                    bottom = max(bottom, line.y1)
                    continue
            rows.append([line])
            top, bottom = line.y0, line.y1
        return rows

    @staticmethod
    def _edge_rows(rows: List[List[_PageLine]]) -> Tuple[List[List[_PageLine]], List[List[_PageLine]]]:
        """The rows the chrome decision can use, from each page edge inward: the rows in that
        band and the three rows after them (``_row_detached`` looks that far)."""
        top = next((i for i, row in enumerate(rows) if any(line.zone != "header" for line in row)), len(rows))
        bottom = next((i for i, row in enumerate(rows[::-1]) if any(line.zone != "footer" for line in row)),
                      len(rows))
        return rows[:top + 3], rows[::-1][:bottom + 3]

    @staticmethod
    def _chrome_slots(lines: List[_PageLine]) -> List[List[_PageLine]]:
        """Band lines grouped by zone and distance from that page edge (within tolerance)."""
        slots: List[List[_PageLine]] = []
        anchor = 0.0
        for line in sorted(lines, key=lambda line: (line.zone, line.edge)):
            if slots and slots[-1][0].zone == line.zone and line.edge - anchor <= _CHROME_SLOT_TOLERANCE:
                slots[-1].append(line)
            else:
                slots.append([line])
                anchor = line.edge
        return slots

    @staticmethod
    def _page_number_template(norm: str) -> Tuple[Optional[str], Tuple[int, ...], Tuple[Tuple[int, int], ...]]:
        """Mask the page-number-shaped tokens of a normalised line.

        Returns ``(template, numbers, totals)``: the line with each page-number-shaped token
        (``_PAGE_NUMBER_PATTERNS``, or a whole-line roman numeral such as ``iv``,
        ``page iv``, ``- iv -``) replaced by ``#``, the tokens' values, and ``(N, M)`` for each
        masked ``N/M`` or ``N of M``; or ``(None, (), ())`` when the line has none.
        """
        roman = _ROMAN_PAGE_NUMBER.fullmatch(norm)
        if roman and _ROMAN_NUMERAL.fullmatch(roman.group("r")):
            digits = [{"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}[c] for c in roman.group("r")]
            value = sum(-d if i + 1 < len(digits) and digits[i + 1] > d else d for i, d in enumerate(digits))
            return norm[:roman.start("r")] + "#" + norm[roman.end("r"):], (value,), ()

        numbers: List[int] = []
        totals: List[Tuple[int, int]] = []

        def mask(match, template):
            value = int(match.group("n"))
            numbers.append(value)
            total = re.search(r"\d+", match.groupdict().get("t") or "")
            if total:
                totals.append((value, int(total.group())))
            return match.expand(template)

        text = norm
        for pattern, template in _PAGE_NUMBER_PATTERNS:
            text = pattern.sub(lambda match: mask(match, template), text)
        return (text, tuple(numbers), tuple(totals)) if numbers else (None, (), ())

    @staticmethod
    def _page_number_lines(slot: List[_PageLine], body_size: float) -> List[_PageLine]:
        """The slot's lines whose masked numbers follow the page order.

        Lines with the same template are grouped. A line qualifies when, for one masked
        token, at least ``_CHROME_MIN_PAGES`` pages share its offset between that token's
        value and the page number (chapter-restarting numbering keeps one offset per
        chapter). Pages showing several different numbers of the shape (a row of chart axis
        years), heading-sized numbers (KPI figures) and groups where a number is larger than
        the total printed after it (the dates 15/03 ... 20/03, or 01/03 ... 06/03) never count.
        Whether a qualifying line is a page number depends on its words
        (``_is_page_number_template``).
        """
        groups: Dict[Tuple[str, int], List[_PageLine]] = defaultdict(list)
        for line in slot:
            if line.template is not None and line.size <= body_size * _CHROME_NUMBER_SIZE_RATIO:
                groups[(line.template, len(line.numbers))].append(line)
        found: List[_PageLine] = []
        for (template, count), lines in groups.items():
            if any(number > total for line in lines for number, total in line.totals):
                continue
            values_by_page = defaultdict(set)
            for line in lines:
                values_by_page[line.page].add(line.numbers)
            lines = [line for line in lines if len(values_by_page[line.page]) == 1]
            following = set()
            for position in range(count):
                pages_by_offset = defaultdict(set)
                for line in lines:
                    pages_by_offset[line.numbers[position] - (line.page + 1)].add(line.page)
                following.update(id(line) for line in lines
                                 if len(pages_by_offset[line.numbers[position] - (line.page + 1)]) >= _CHROME_MIN_PAGES)
            found.extend(line for line in lines if id(line) in following)
        return found

    def _peel_chrome_rows(self, rows: List[List[_PageLine]], zone: str, candidates: set,
                          page_numbers: set, riders: set = frozenset()) -> List[List[_PageLine]]:
        """The rows at one page edge that hold chrome; ``rows`` runs from that edge inward.

        Rows are taken while every line in them is a chrome candidate of this zone, or a
        ``riders`` line (a numbered label, which stays content) next to at least one candidate.
        The innermost taken row must then stand apart from the rows after it
        (``_row_detached``; a row of bare page numbers, ``page_numbers``, needs no gap);
        otherwise it is given back, and so on.
        """
        peeled: List[List[_PageLine]] = []
        for row in rows:
            if (not all(line.zone == zone and (id(line) in candidates or id(line) in riders) for line in row)
                    or not any(id(line) in candidates for line in row)):
                break
            peeled.append(row)
        while peeled and len(peeled) < len(rows) and not self._row_detached(
                peeled[-1], rows[len(peeled):], zone, all(id(line) in page_numbers for line in peeled[-1])):
            peeled.pop()
        return peeled

    def _row_detached(self, chrome_row: List[_PageLine], next_rows: List[List[_PageLine]], zone: str,
                      page_numbers: bool = False) -> bool:
        """Does ``chrome_row`` stand apart from ``next_rows``, the rows after it toward the page centre?

        It needs a gap of at least 0.7 of its height (3pt minimum; heights measured from the
        baselines) before the next row, except a row of bare page numbers (``page_numbers``):
        Word and LibreOffice set the page number right under a full page's last line. And it
        must not be a table's header row: one with three or more cells (lines, or parts of a
        line set apart by runs of spaces) lined up with three cells of one of the next three rows.
        """
        top = min(line.y0 for line in chrome_row)
        bottom = max(line.y1 for line in chrome_row)
        if zone == "header":
            gap = min(line.y0 for line in next_rows[0]) - bottom
        else:
            gap = top - max(line.y1 for line in next_rows[0])
        if not page_numbers and gap < max(3.0, 0.7 * (bottom - top)):
            return False
        cells = self._row_cells(chrome_row)
        if len(cells) < 3:
            return True
        for row in next_rows[:3]:
            unmatched = list(cells)
            aligned = 0
            for x0, x1 in self._row_cells(row):
                for cell in unmatched:
                    if min(x1, cell[1]) > max(x0, cell[0]):
                        unmatched.remove(cell)
                        aligned += 1
                        break
            if aligned >= 3:
                return False
        return True

    @staticmethod
    def _row_cells(row: List[_PageLine]) -> List[Tuple[float, float]]:
        """Horizontal extents of a row's cells: its lines, split where a line has a run of two or
        more spaces. Positions inside a line are estimated from typical glyph widths (a
        space is narrower than a digit, a CJK character wider than a letter)."""
        def advance(char: str) -> float:
            if char.isspace():
                return 0.28
            if unicodedata.east_asian_width(char) in ("W", "F"):
                return 1.0
            return 0.67 if char.isupper() else 0.56 if char.isdigit() else 0.5

        cells = []
        for line in row:
            offsets = [0.0]
            for char in line.text:
                offsets.append(offsets[-1] + advance(char))
            scale = (line.x1 - line.x0) / (offsets[-1] or 1.0)
            for part in re.finditer(r"\S+(?: \S+)*", line.text):
                cells.append((line.x0 + scale * offsets[part.start()], line.x0 + scale * offsets[part.end()]))
        return cells

    @staticmethod
    def _weighted_median(pairs: List[Tuple[float, int]]) -> float:
        """Median of values weighted by counts (with character counts: the body font size)."""
        pairs = sorted((value, weight) for value, weight in pairs if weight > 0)
        total = sum(weight for _, weight in pairs)
        seen = 0
        for value, weight in pairs:
            seen += weight
            if 2 * seen >= total:
                return value
        return 0.0

    @staticmethod
    def _is_bare_page_number(template: str) -> bool:
        """A page-number template with nothing but page-number words around its numbers."""
        return all(word in _PAGE_NUMBER_WORDS for word in re.findall(r"[^\W\d_]+", template))

    @staticmethod
    def _is_page_number_template(template: str) -> bool:
        """Whether numbers following the page order in lines of this template are page numbers
        by their words: bare (``#``, ``- # -``, ``# of 12``) or with a page word
        (``acme | page #``, ``第#頁``). Other labels (``# | acme corp``, ``lesson · #``) may be
        per-page content."""
        words = re.findall(r"[^\W\d_]+", template)
        return all(word in _PAGE_NUMBER_WORDS for word in words) or any(word in _PAGE_WORDS for word in words)

    @staticmethod
    def _judged_boilerplate(judge: BoilerplateJudge, text: str, context: Dict[str, Any]) -> bool:
        """Ask ``judge`` about an ambiguous repeated line; anything but a probability >= 0.5 keeps it."""
        try:
            probability = judge(text, context)
        except Exception as e:
            logger.warning(f"boilerplate_judge failed on {text[:40]!r}: {e}; keeping the line")
            return False
        if probability is None:
            return False
        if (isinstance(probability, bool) or not isinstance(probability, numbers.Real)
                or not 0.0 <= float(probability) <= 1.0):
            logger.warning(f"boilerplate_judge returned {probability!r} for {text[:40]!r} "
                           f"(expected None or 0..1); keeping the line")
            return False
        return float(probability) >= _CHROME_JUDGE_THRESHOLD

    def _uncrop_for_tables(self, page, page_num: Optional[int] = None) -> None:
        """Take a rotated page's own /CropBox off before its tables are found, so that
        find_tables() reports them in one uncropped frame (see ``_record_rotated_crop_boxes``);
        ``_restore_cropbox`` puts it back once the tables are extracted. ``page_num`` is the
        page's index in ``self.doc`` (default ``page.number``): the page object may be another
        handle on the file (a cleaned copy), whose own document is the one written to.

        While the CropBox is off, ``pdf_routing.PageCopies`` does not copy the page (the copy
        would not show what the page shows), so the tables of such a page are read from the
        page itself even when it has hidden text; the text path still leaves that text out."""
        number = page.number if page_num is None else page_num
        if number not in getattr(self, "_rotated_crop_boxes", {}) or number in self._rotated_cropped_pages:
            return
        try:
            doc = page.parent
            doc.xref_set_key(page.xref, "CropBox", "null")
            self._table_frames[number] = (doc, page.xref, page.derotation_matrix)
        except Exception as e:
            logger.debug(f"Could not uncrop page {number + 1} for table detection, its tables are not suppressed: {e}")
            self._rotated_cropped_pages.add(number)

    def _restore_cropbox(self, page, page_num: Optional[int] = None) -> None:
        """Put back the /CropBox that ``_uncrop_for_tables`` or PyMuPDF's find_tables() took off
        a rotated page, so its text is read in its own frame (see ``_record_rotated_crop_boxes``).
        Written as the original entry, without PyMuPDF's validation, on the page's own document
        (another handle on the file than ``self.doc`` when text is read from a cleaned copy), and
        on the page ``_uncrop_for_tables`` uncropped if the text now comes from another object.
        ``page_num`` is the page's index in ``self.doc`` (default ``page.number``). If writing
        fails, the page gets no table suppression."""
        number = page.number if page_num is None else page_num
        source = getattr(self, "_rotated_crop_boxes", {}).get(number)
        if source is None:
            return
        targets = [(page.parent, page.xref)]
        frame = getattr(self, "_table_frames", {}).get(number)
        if frame is not None and (frame[0] is not targets[0][0] or frame[1] != targets[0][1]):
            targets.append(frame[:2])
        for doc, xref in targets:
            try:
                if doc.xref_get_key(xref, "CropBox")[0] == "null":
                    doc.xref_set_key(xref, "CropBox", source)
            except Exception as e:
                logger.debug(f"Could not restore the CropBox of page {number + 1}: {e}")
                self._rotated_cropped_pages.add(number)

    @contextmanager
    def _text_page(self, page, ocr_images: Optional[bool] = None,
                   ocr_results_map: Optional[Dict[tuple, str]] = None):
        """The page object whose text is read for ``page`` (a page of ``self.doc``): the page as
        ``_text_source`` gives it, without its hidden text. Text and tables (``_process_page``),
        running headers and footers (``_detect_page_chrome``, ``_middle_lines``) and the
        first-text-page check all read a page through here, so they see the same text.
        ``ocr_images`` and ``ocr_results_map`` default to the conversion under way (see
        ``convert_to_json``). The page copies the text source makes are discarded when the
        block ends, so blocks never nest: ``_process_page`` runs the document-wide passes
        before opening its page's block."""
        copies = getattr(self, "_copies", None)
        if copies is None:  # no routing state (a loader built without __init__): the page itself
            yield page
            return
        if ocr_images is None:
            ocr_images, ocr_results_map = getattr(self, "_text_options", (False, None))
        try:
            yield self._text_source(page, page.number, ocr_images, ocr_results_map)
        finally:
            copies.discard()

    def _get_first_text_page_num(self) -> int:
        """Return the first page index containing non-empty text, falling back to 0.

        Running header/footer lines do not count: a cover whose text layer holds only
        page chrome is not where the document title is.
        """
        cached = getattr(self, "_first_text_page_num", None)
        if cached is not None:
            return cached

        doc = getattr(self, "doc", None)
        if doc is None:
            return 0

        try:
            for page_num in range(len(doc)):
                with self._text_page(doc.load_page(page_num)) as page:
                    text_dict = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_LIGATURES)
                for block in text_dict.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        bbox = line.get("bbox")
                        if bbox and self._chrome_line(bbox, self._page_chrome_regions(page_num)):
                            continue
                        for span in line.get("spans", []):
                            if span.get("text", "").strip():
                                self._first_text_page_num = page_num
                                return page_num
        except Exception as e:
            logger.debug(f"Failed to detect first text page, defaulting to page 0: {e}")
            self._first_text_page_num = 0
            return 0

        self._first_text_page_num = 0
        return 0

    def _page_measure(self, page_num: int) -> "pdf_routing.PageMeasure":
        """Routing signals of one page (measured once, see pdf_routing.measure_page)."""
        measure = self._page_measures.get(page_num)
        if measure is None:
            page = self.doc.load_page(page_num)
            measure = pdf_routing.measure_page(
                page,
                ocr_rects=self._ocr_image_rects,
                keep_text=self._legibility_judge is not None and self.ocr is not None,
                copies=self._copies,
            )
            self._page_measures[page_num] = measure
        return measure

    def _judged_signals(self, page_num: int):
        """The page's signals with the optional legibility judge's verdict (asked once,
        only for text layers the deterministic detector did not flag)."""
        measure = self._page_measure(page_num)
        if self._legibility_judge is None or page_num in self._judged_pages:
            return measure.signals
        self._judged_pages.add(page_num)
        verdict = _judge_text_layer(self._legibility_judge, measure.signals.text_layer, measure.text or "")
        if verdict is not None:
            measure = replace(measure, signals=replace(measure.signals, judge_legibility=verdict))
            self._page_measures[page_num] = measure
        return measure.signals

    def _ocr_image_rects(self, page) -> List[Any]:
        """Placements the text route OCRs one by one (non-decorative image XObjects)."""
        rects = []
        for img_info in page.get_images(full=True):
            try:
                rects.extend(r for r in page.get_image_rects(img_info[0]) if not self._is_decorative_image(r, page))
            except Exception as e:
                logger.debug(f"Failed to get image rects for xref {img_info[0]}: {e}")
        return rects

    def _page_signals(self, page_num: int):
        return self._page_measure(page_num).signals

    def _document_image_strategy(self) -> str:
        """High-level DOCUMENT route (core.strategy.decide_doc_strategy) from the
        pages' signals: mean visible image coverage and mean legible text density
        (script-aware, see core.strategy.text_weight). Text-layer quality is not a
        document signal here: every page is checked and garbled pages are OCR'd one
        by one (see _page_route), so they never take clean pages with them.

        - "image": pages are mostly pictures (mean coverage high) AND carry little
          legible text — the real content is in the page images. Pages are rendered
          and OCR'd as whole images (with neighbor-page context when enabled); the
          OCR is authoritative.
        - "text": there is a usable text layer or little image coverage. The
          deterministic rule-based layer (complex tables + text, preserved verbatim
          for BM42 RAG) is authoritative, and embedded figures are OCR'd individually.

        This is every page's default; a page whose own signals clearly disagree
        overrides it (see _page_route). Decided once per document (cached).
        """
        if self._doc_strategy is not None:
            return self._doc_strategy
        n = len(self.doc) if self.doc is not None else 0
        if n == 0:
            self._doc_strategy = "text"
            return self._doc_strategy
        mean_cov, mean_text, illegible = _document_signals([self._page_signals(i) for i in range(n)])
        self._doc_strategy = _decide_doc_strategy(mean_cov, mean_text)
        logger.info(f"📑 Document OCR strategy: {self._doc_strategy} "
                    f"(mean coverage {mean_cov:.2f}, mean legible text {mean_text:.0f}/page, "
                    f"garbled text pages {illegible:.0%})")
        return self._doc_strategy

    def _page_route(self, page_num: int) -> Tuple[str, str]:
        """``(route, reason)`` of one page when an OCR provider is active: the
        document route, unless the page's own signals clearly disagree (a
        searchable scan, a garbled text layer, content only as vector outlines or
        inline images, a scanned page in a text report, a dense text page in an
        image deck). See core.strategy.decide_page_route."""
        route = self._page_routes.get(page_num)
        if route is None:
            document_route = self._document_image_strategy()
            route = _decide_page_route(self._page_signals(page_num), document_route)
            if route[0] == "text" and self._legibility_judge is not None:
                # Only a page that would keep its text layer needs the judge's opinion.
                route = _decide_page_route(self._judged_signals(page_num), document_route)
            self._page_routes[page_num] = route
        return route

    def _text_source(self, page, page_num: int, ocr_images: bool, ocr_results_map: Optional[Dict[tuple, str]]):
        """The page as the text and table extractors should read it (discard
        ``self._copies`` once they are done).

        Invisible (render mode 3) text over nothing the page shows is hidden text, and
        an invisible copy of the painted text it lies on is a duplicate: neither reaches
        them (H-F15). Invisible text over what the page shows (a scanner's OCR layer, a
        transparent copy of text baked into artwork or drawn as outlines) is that
        content's text: kept, except over a picture whose OCR in this run returned text,
        so each picture gives one source, not two (R-F1).
        """
        measure = self._page_measure(page_num)
        if not measure.has_invisible_text:
            return page
        ocrd = self._ocrd_picture_rects(page, page_num, ocr_images, ocr_results_map)
        return pdf_routing.text_source(page, measure, self._copies, ocrd_rects=ocrd)

    def _ocrd_picture_rects(self, page, page_num: int, ocr_images: bool,
                            ocr_results_map: Optional[Dict[tuple, str]]) -> List[Any]:
        """Placements on this page whose OCR in this run returned text (the batch results;
        the legacy per-page OCR runs after text extraction, so its results are unknown
        here and the invisible text over its pictures is kept)."""
        if not ocr_images or self.ocr is None or not ocr_results_map:
            return []
        rects = []
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if not (ocr_results_map.get((page_num, xref)) or "").strip():
                continue
            try:
                rects.extend(r for r in page.get_image_rects(xref) if not self._is_decorative_image(r, page))
            except Exception as e:
                logger.debug(f"Failed to get image rects for xref {xref}: {e}")
        return rects

    def _record_routing(self, document: Dict[str, Any], ocr_active: bool) -> None:
        """Record the routing facts in ``document`` and warn about text that cannot be trusted or extracted.

        - ``ocr_routing`` (OCR active only): the document route and the pages that
          overrode it, with the reason (see core.strategy.decide_page_route).
        - ``text_layer_quality``: pages whose text layer is garbled (the detector, or
          the legibility judge when OCR is active), and whether it was replaced by OCR
          of the render (``"ocr"``) or kept as extracted (``"kept"``, with a warning).
        - ``hidden_text``: pages whose invisible (render mode 3) text over nothing the
          page shows was left out.
        - Warnings when the output is empty, when pages showing content produced no
          text (OCR returned nothing), or, without OCR, for pages that have no usable
          text layer but show content (scans, vector outlines).
        """
        n = len(self.doc)
        if n == 0:
            return
        name = self.pdf_path.name
        if ocr_active:
            doc_route = self._document_image_strategy()
            overrides = []
            for i in range(n):
                route, reason = self._page_route(i)
                if route != doc_route:
                    overrides.append({"page": i + 1, "route": route, "reason": reason})
            document["ocr_routing"] = {"document_route": doc_route, "overrides": overrides}
        # The legibility judge was asked (by _page_route) only when OCR is active: its
        # verdict can only choose OCR for a page, so without OCR it is never consulted.
        signals = [self._page_signals(i) for i in range(n)]

        quality, kept = [], []
        for i, page in enumerate(signals):
            if not page.text_layer_illegible:
                continue
            ocrd = i in self._rendered_pages
            layer = page.text_layer
            quality.append({
                "page": i + 1,
                "legible": False,
                "garbage_ratio": round(layer.garbage_ratio, 3),
                "garbage_glyphs": layer.garbage_glyphs,
                "judge_legibility": page.judge_legibility,
                "action": "ocr" if ocrd else "kept",
            })
            if not ocrd:
                kept.append(i)
        if quality:
            document["text_layer_quality"] = quality
        if kept:
            logger.warning(f"{name} {pdf_routing.describe_pages(kept)}: the text layer looks garbled (undecodable "
                           f"glyphs or unreadable text) and was kept as extracted; OCR of the page render (an OCR "
                           f"provider with ocr_images=True; CLI: --ocr <provider> --ocr-images) reads it instead")

        hidden = [i for i, page in enumerate(signals) if page.hidden_chars]
        if hidden:
            document["hidden_text"] = [{"page": i + 1, "chars": signals[i].hidden_chars} for i in hidden]
            logger.warning(f"{name} {pdf_routing.describe_pages(hidden)}: invisible (render mode 3) text over "
                           f"nothing the page shows was left out of the output")

        emitted = {item.get("page") for item in document.get("content", []) if str(item.get("content", "")).strip()}
        if not emitted:
            hint = ("" if ocr_active else "; its pages carry no usable text layer, enable OCR (an OCR provider "
                    "with ocr_images=True; CLI: --ocr <provider> --ocr-images) to read scanned or drawn pages")
            logger.warning(f"{name}: no text could be extracted from its {n} page(s){hint}")
        elif ocr_active:
            silent = [i for i, page in enumerate(signals) if i + 1 not in emitted
                      and (page.image_coverage >= _MIN_UNCAPTURED_RASTER or page.uncaptured_content
                           or page.invisible.chars)]
            if silent:
                logger.warning(f"{name} {pdf_routing.describe_pages(silent)}: no text was extracted although the "
                               f"page shows content (the OCR returned nothing)")
        else:
            missing = _pages_without_text(signals)
            if missing:
                logger.warning(f"{name} {pdf_routing.describe_pages(missing)}: little or no text layer; content "
                               f"shown only as scanned images or drawn outlines was not extracted; enable OCR (an "
                               f"OCR provider with ocr_images=True; CLI: --ocr <provider> --ocr-images) to read it")

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

        Pages routed "image" (see _page_route) contribute ONE whole-page render
        (xref ``_PAGE_RENDER_XREF``); other pages contribute their embedded images,
        skipping decorative thumbnails. Each entry has page_num, xref, base64,
        mime_type, position, and (for renders) is_page_render=True.
        """
        all_images = []

        for page_num in range(len(self.doc)):
            page = self.doc.load_page(page_num)

            # Whole-page OCR for pages routed "image" (by the document route or
            # their own signals); the other pages OCR only their embedded figures.
            if self.ocr is not None and self._page_route(page_num)[0] == "image":
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
        """Process a single page, routed by its OCR route (_page_route, applied in
        _collect_all_images):

        - IMAGE-authoritative (a whole-page render was OCR'd): emit ONLY the OCR
          transcription. A sparse text layer on such a page is chrome
          (logo / footer / page number), the invisible layer of a searchable scan,
          or a garbled layer; the whole-page OCR already captures what it shows,
          so emitting it too would duplicate content or add garbage. When the
          render OCR'd to nothing, the page falls back to its own text layer
          instead of disappearing.
        - TEXT-authoritative: emit the deterministic text/table layer (preserved
          verbatim for the BM42 RAG flow) plus per-image OCR for embedded figures.

        Invisible (render mode 3) text is content only over what the page shows (a
        scanner's OCR layer, text drawn as outlines) and only where no OCR of that
        picture replaced it; see _text_source.
        """
        page = self.doc.load_page(page_num)

        # --- IMAGE-authoritative page: the whole-page OCR IS the content. ---
        if (ocr_images and ocr_results_map is not None
                and (page_num, _PAGE_RENDER_XREF) in ocr_results_map):
            render_text = (ocr_results_map.get((page_num, _PAGE_RENDER_XREF)) or "").strip()
            if render_text:
                self._rendered_pages.add(page_num)
                items = [{
                    "type": "text:image_description",
                    "content": f"<image_ocr_result>{render_text}</image_ocr_result>",
                    "page": page_num + 1,
                    "position_y": 0.0,
                }]
                # A page overridden to render OCR keeps whatever real painted text the
                # OCR did not reproduce (verbatim first).
                reason = self._page_routes.get(page_num, (None, None))[1]
                if reason in _VERBATIM_TAIL_REASONS:
                    missing = pdf_routing.missing_painted_lines(page, self._page_measure(page_num), render_text,
                                                                garbled=reason == _REASON_ILLEGIBLE)
                    if missing:
                        items.append({
                            "type": "text:normal",
                            "content": "\n".join(missing),
                            "page": page_num + 1,
                            "position_y": float(page.rect.height),
                        })
                return items
            # Blank render, refusal or OCR failure: keep the page's own text layer
            # (verbatim first) rather than drop the page.
            fallback = self._process_page(page_num, extract_images=False, ocr_images=False)
            if fallback:
                logger.warning(f"{self.pdf_path.name} page {page_num + 1}: OCR of the page render returned "
                               f"no text; keeping the page's own text layer")
            return fallback

        # --- TEXT-authoritative page: rule-based text/tables + per-image OCR. ---
        # The document-wide passes read every page through _text_page: run them (once) before
        # this page's block opens, so blocks never nest.
        self._page_chrome_regions(page_num)
        self._get_first_text_page_num()
        content_items = []
        with self._text_page(page, ocr_images, ocr_results_map) as text_page:
            # A rotated page's tables are found and read uncropped, in one frame; its CropBox
            # is back before anything else reads the page (see _record_rotated_crop_boxes).
            self._uncrop_for_tables(text_page, page_num)
            try:
                table_items, table_bboxes = self._extract_tables_as_markdown(text_page, page_num)
            finally:
                self._restore_cropbox(text_page, page_num)
            content_items.extend(table_items)
            text_items = self._extract_text_as_markdown(text_page, page_num, table_bboxes)
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
        """Extract text blocks and convert to markdown format with text type classification.

        Blocks are filtered line by line first (``_filter_text_blocks``): text inside a
        table is left to the table item, running headers/footers become text:header /
        text:footer items, and overprinted duplicates are emitted once.
        """
        text_items = []
        self._restore_cropbox(page, page_num)
        # find_tables() reports display (rotated) coordinates, get_text() unrotated ones.
        table_bboxes = self._table_regions_in_text_space(page, table_bboxes or [], page_num)

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

        blocks, chrome_items = self._filter_text_blocks(page, text_dict["blocks"], table_bboxes, page_num)
        text_items.extend(chrome_items)
        for block in blocks:
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

    def _table_regions_in_text_space(self, page, table_bboxes: List[tuple], page_num: Optional[int] = None) -> List[tuple]:
        """Table bboxes mapped into the coordinate space of ``get_text()``.

        ``find_tables()`` reports bboxes as the page is displayed (``/Rotate`` applied)
        while ``get_text()`` reports unrotated page coordinates. On a rotated page the two
        must be brought together before comparing them, or the table's text is emitted a
        second time and unrelated text where the rotated box lands is dropped. A cropped
        page's tables were found uncropped (``_uncrop_for_tables``): their boxes are derotated
        as for the whole MediaBox, then moved by where the visible area starts. Where that is
        not possible (see ``_record_rotated_crop_boxes``), or when the tables were found on
        another page object than the one uncropped (text read from a copy), none are returned:
        the table's text may then appear twice, but no text is dropped. ``page_num`` is the
        page's index in ``self.doc`` (default ``page.number``).
        """
        number = page.number if page_num is None else page_num
        frame = getattr(self, "_table_frames", {}).pop(number, None)
        if not table_bboxes or not page.rotation:
            return list(table_bboxes)
        uncropped = frame is not None and frame[0] is page.parent and frame[1] == page.xref
        if number in getattr(self, "_rotated_cropped_pages", ()) or (
                not uncropped and (frame is not None or number in getattr(self, "_rotated_crop_boxes", {}))):
            logger.debug(f"Page {number + 1} is rotated and cropped: table text is not suppressed")
            return []
        matrix = frame[2] if uncropped else page.derotation_matrix
        dx, dy = getattr(self, "_crop_offsets", {}).get(number, (0.0, 0.0))
        return [tuple(pymupdf.Rect(bbox) * matrix + (-dx, -dy, -dx, -dy)) for bbox in table_bboxes]

    def _filter_text_blocks(self, page, blocks: List[Dict[str, Any]], table_bboxes: List[tuple],
                            page_num: Optional[int] = None) -> Tuple[List[Dict[str, Any]], List[SimpleContent]]:
        """Split a page's text blocks into content blocks and running header/footer items.

        * A span whose centre lies inside a table bbox is dropped: the table item carries
          it. The other lines of the same PyMuPDF block (a caption or note printed right
          against the table, a title next to a logo box) stay.
        * A running header/footer line (``_detect_page_chrome``) becomes its own item with
          its raw text, so it is never classified as a heading, list item or footnote:
          text:header / text:footer, or text:normal for a line kept as content (a running
          header's first copy, a numbered label, a page number the rule leaves).
        * Overprinted copies of a line are dropped (``_drop_overprinted_lines``).

        Returns ``(blocks, header/footer items)``: the text blocks in their original order,
        each untouched block as it was and the others with their surviving lines and a
        recomputed bbox. ``page_num`` is the page's index in ``self.doc`` (default ``page.number``).
        """
        page_num = page.number if page_num is None else page_num
        chrome_regions = self._page_chrome_regions(page_num)
        entries: List[List[Any]] = []
        chrome_items: List[SimpleContent] = []
        for index, block in enumerate(blocks):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                chrome = self._chrome_line(line["bbox"], chrome_regions)
                line = self._line_outside_tables(line, table_bboxes)
                if line is None:
                    continue
                if chrome:
                    zone, kept = chrome
                    text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                    chrome_items.append(SimpleContent(type="text:normal" if kept else f"text:{zone}", content=text,
                                                      page=page_num + 1, position_y=line["bbox"][1]))
                else:
                    entries.append([index, line])

        lines_by_block: Dict[int, List[Dict[str, Any]]] = OrderedDict()
        for index, line in self._drop_overprinted_lines(page, entries):
            lines_by_block.setdefault(index, []).append(line)
        kept_blocks = []
        for index, lines in lines_by_block.items():
            block = blocks[index]
            if len(lines) == len(block["lines"]) and all(a is b for a, b in zip(lines, block["lines"])):
                kept_blocks.append(block)
            else:
                kept_blocks.append(dict(block, lines=lines, bbox=self._union_bbox(line["bbox"] for line in lines)))
        return kept_blocks, chrome_items

    def _line_outside_tables(self, line: Dict[str, Any], table_bboxes: List[tuple]) -> Optional[Dict[str, Any]]:
        """``line`` without its spans inside a table bbox; None when no text is left."""
        spans = line.get("spans", [])
        up = self._text_up(line)
        kept = [span for span in spans if not self._point_inside(self._span_anchor(span, up), table_bboxes)]
        if len(kept) == len(spans):
            return line
        if not any(span.get("text", "").strip() for span in kept):
            return None
        return dict(line, spans=kept, bbox=self._union_bbox(span["bbox"] for span in kept))

    @staticmethod
    def _point_inside(point: Tuple[float, float], regions: List[tuple]) -> bool:
        x, y = point
        return any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in regions)

    @staticmethod
    def _text_up(line: Dict[str, Any]) -> Optional[Tuple[int, int]]:
        """The direction the glyph tops point to for an axis-aligned line: ``(0, -1)`` for
        ordinary text, ``(-1, 0)``, ``(0, 1)`` or ``(1, 0)`` for text turned 90/180/270
        degrees; None for slanted text."""
        dx, dy = line.get("dir", (1.0, 0.0))
        if abs(dy) < 1e-3 and abs(abs(dx) - 1) < 1e-3:
            return (0, -1) if dx > 0 else (0, 1)
        if abs(dx) < 1e-3 and abs(abs(dy) - 1) < 1e-3:
            return (-1, 0) if dy < 0 else (1, 0)
        return None

    @staticmethod
    def _span_anchor(span: Dict[str, Any], up: Optional[Tuple[int, int]]) -> Tuple[float, float]:
        """Where a span's glyphs are: on its middle, 0.3 x size above the baseline (mid
        x-height), or the bbox centre for slanted text. Span boxes can be several times taller
        than the text (fonts declaring Word/Cambria-like ascent and descent in PyMuPDF 1.28)."""
        x0, y0, x1, y1 = span["bbox"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if up is None or "origin" not in span or not span.get("size"):
            return cx, cy
        if up[1]:
            return cx, span["origin"][1] + up[1] * 0.3 * span["size"]
        return span["origin"][0] + up[0] * 0.3 * span["size"], cy

    @staticmethod
    def _visual_box(line: Dict[str, Any]) -> Tuple[float, float, float, float]:
        """A line's box from its glyphs' baselines and sizes: across the text it runs from
        0.25 x size below the baseline to 0.8 x size above it (within the reported box), along
        the text it is the reported box. Reported boxes can be several times taller than the
        text, which would make neighbouring lines overlap. Slanted lines keep their bbox."""
        box = list(line["bbox"])
        up = PDFLoader._text_up(line)
        spans = [span for span in line.get("spans", [])
                 if span.get("text", "").strip() and "origin" in span and span.get("size")]
        if not spans or up is None:
            return tuple(box)
        axis = 1 if up[1] else 0  # the axis across the text: y for horizontal lines, x for vertical ones
        ends = [span["origin"][axis] + up[axis] * k * span["size"] for span in spans for k in (0.8, -0.25)]
        low, high = max(box[axis], min(min(ends), box[axis + 2])), min(box[axis + 2], max(max(ends), box[axis]))
        box[axis], box[axis + 2] = low, high
        return tuple(box)

    @staticmethod
    def _union_bbox(bboxes) -> Tuple[float, float, float, float]:
        boxes = list(bboxes)
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    @staticmethod
    def _chrome_line(bbox: tuple, regions: List[Tuple[tuple, str, bool]]) -> Optional[Tuple[str, bool]]:
        """``(zone, kept)`` of the running header/footer line that is this line (IoU >= 0.6), if any."""
        for rect, zone, kept in regions:
            width = min(bbox[2], rect[2]) - max(bbox[0], rect[0])
            height = min(bbox[3], rect[3]) - max(bbox[1], rect[1])
            if width <= 0 or height <= 0:
                continue
            overlap = width * height
            union = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                     + (rect[2] - rect[0]) * (rect[3] - rect[1]) - overlap)
            if union > 0 and overlap / union >= 0.6:
                return zone, kept
        return None

    def _drop_overprinted_lines(self, page, entries: List[List[Any]]) -> List[List[Any]]:
        """Emit text drawn 2-3 times at (nearly) the same place once.

        Fake bold, drop shadows and highlight overlays repeat glyphs on top of each
        other, and PyMuPDF returns every copy as a line of its own. ``entries`` is
        ``[[block index, line], ...]`` in extraction order. Two lines are compared only
        when they overlap both ways (vertically by 70% of the shorter one, measured from the
        baselines: ``_visual_box``) and have the same font size; then

        * same text, horizontal overlap of 70% of the narrower line: the later copy goes;
        * one text inside the other and every glyph of it drawn on the same glyph of the
          other (``_glyphs_coincide``): the shorter line goes (a highlighted phrase);
        * a line that starts inside another one and whose first characters are drawn on
          that line's last characters (one fake-bold word in a sentence): those
          characters are cut from it.

        Repeats that are not drawn on top of each other ("No No", "10 10", one word on two
        lines, clipped spreadsheet cells that merely overlap) are real text and stay.
        """
        if len(entries) < 2:
            return entries
        boxes = [self._visual_box(line) for _, line in entries]
        order = sorted(range(len(entries)), key=lambda k: boxes[k][1])
        dropped = set()
        for position, i in enumerate(order):
            for j in order[position + 1:]:
                if i in dropped or boxes[j][1] >= boxes[i][3]:
                    break
                a, b = boxes[i], boxes[j]
                if j in dropped or min(a[2], b[2]) <= max(a[0], b[0]) or not self._overlaps_vertically(a, b):
                    continue
                first, second = sorted((i, j))
                verdict = self._overprint_verdict(page, entries[first][1], entries[second][1])
                if verdict is None:
                    continue
                target = first if verdict[1] == "first" else second
                if verdict[0] == "drop":
                    dropped.add(target)
                else:
                    entries[target][1] = self._trim_line_start(entries[target][1], verdict[2])
                    boxes[target] = self._visual_box(entries[target][1])
        return [entry for k, entry in enumerate(entries) if k not in dropped]

    @staticmethod
    def _overlaps_vertically(a: tuple, b: tuple) -> bool:
        overlap = min(a[3], b[3]) - max(a[1], b[1])
        return overlap > 0 and overlap >= 0.7 * min(a[3] - a[1], b[3] - b[1])

    def _overprint_verdict(self, page, first: Dict[str, Any], second: Dict[str, Any]) -> Optional[tuple]:
        """How two overlapping lines relate; ``first`` was extracted first.

        Returns None (not an overprint), ``("drop", "first" | "second")`` or
        ``("trim", "first" | "second", characters)``; see ``_drop_overprinted_lines``.
        """
        lines = []
        for name, line in (("first", first), ("second", second)):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            size = max((span.get("size", 0.0) for span in line.get("spans", [])), default=0.0)
            if not text or size <= 0:
                return None
            lines.append((name, text, line["bbox"], line, size))
        (_, text_a, box_a, _, size_a), (_, text_b, box_b, _, size_b) = lines
        if abs(size_a - size_b) > 0.1 * max(size_a, size_b):
            return None
        if " ".join(text_a.split()) == " ".join(text_b.split()):
            overlap = min(box_a[2], box_b[2]) - max(box_a[0], box_b[0])
            if overlap >= 0.7 * min(box_a[2] - box_a[0], box_b[2] - box_b[0]):
                return "drop", "second"
            return None

        # One text drawn a second time over its own occurrence inside the other line.
        short, long = sorted(lines, key=lambda item: len(item[1]))
        if (len(short[1]) >= 2 and short[1] in long[1]
                and short[2][0] >= long[2][0] - 2 and short[2][2] <= long[2][2] + 2):
            if self._glyphs_coincide(page, short[3], len(short[1])):
                return "drop", short[0]
            return None

        # A left-to-right line starting inside another and repeating that line's last characters.
        left, right = sorted(lines, key=lambda item: item[2][0])
        if (all(self._text_up(item[3]) == (0, -1) for item in lines)
                and left[2][0] < right[2][0] < left[2][2] < right[2][2]):
            for count in range(min(len(left[1]), len(right[1]) - 1), 1, -1):
                if left[1].endswith(right[1][:count]):
                    if self._glyphs_coincide(page, right[3], count):
                        return "trim", right[0], count
                    break
        return None

    @staticmethod
    def _glyphs_coincide(page, line: Dict[str, Any], count: int) -> bool:
        """Is each of the first ``count`` characters of ``line`` (after leading blanks) drawn on
        the same character of another line, its origin (baseline point) within 2pt? Read from
        the glyph positions of the line's own area (``rawdict``); when the copies cannot be
        told apart, the answer is no."""
        wanted = [c for c in "".join(span.get("text", "") for span in line.get("spans", [])).lstrip()[:count]
                  if not c.isspace()]
        clip = pymupdf.Rect(line["bbox"]) + (-1, -1, 1, 1)
        raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_LIGATURES, clip=clip)
        raw_lines = [[char for span in raw_line.get("spans", []) for char in span.get("chars", [])
                      if not char["c"].isspace()]
                     for block in raw.get("blocks", []) if block.get("type") == 0
                     for raw_line in block.get("lines", [])]
        own = next((chars for chars in raw_lines if [char["c"] for char in chars[:len(wanted)]] == wanted), None)
        if not wanted or own is None:
            return False
        others = [char for chars in raw_lines if chars is not own for char in chars]
        return all(any(other["c"] == glyph["c"] and abs(other["origin"][0] - glyph["origin"][0]) <= 2.0
                       and abs(other["origin"][1] - glyph["origin"][1]) <= 2.0
                       for other in others)
                   for glyph in own[:len(wanted)])

    @staticmethod
    def _trim_line_start(line: Dict[str, Any], count: int) -> Dict[str, Any]:
        """``line`` without its first ``count`` characters after any leading blanks."""
        text = "".join(span.get("text", "") for span in line["spans"])
        remaining = len(text) - len(text.lstrip()) + count
        spans = []
        for span in line["spans"]:
            span_text = span.get("text", "")
            if remaining >= len(span_text):
                remaining -= len(span_text)
                continue
            if remaining:
                x0, y0, x1, y1 = span["bbox"]
                cut = x0 + (x1 - x0) * remaining / len(span_text)
                span = dict(span, text=span_text[remaining:], bbox=(cut, y0, x1, y1))
                if "origin" in span:
                    span["origin"] = (cut, span["origin"][1])
                remaining = 0
            spans.append(span)
        return dict(line, spans=spans, bbox=PDFLoader._union_bbox(span["bbox"] for span in spans))

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

    def _extract_tables_as_markdown(self, page, page_num: int) -> Tuple[List[SimpleContent], List[Tuple]]:
        """Extract the page's tables as Markdown/HTML (see :mod:`doc2mark.pipelines.pdf_tables`).

        Returns the rendered tables and their bounding boxes (a header row drawn above
        the ruled cells included); the text path skips text inside those boxes. Grids
        that are not tables (logo shapes, page frames) are not returned, so their text
        stays with the text path. Borderless tables with clear numeric columns are found
        too. A table that continues from the previous page gets that page's header row
        instead of promoting its first data row.
        """
        from doc2mark.pipelines import pdf_tables

        try:
            finder = page.find_tables()
            found = list(getattr(finder, "tables", None) or [])
        except Exception as e:
            logger.warning(f"Failed to find tables on page {page_num + 1}: {e}")
            finder, found = None, []

        try:
            tables, outside = pdf_tables.extract_page_tables(page, found, getattr(finder, "textpage", None))
            self._table_carry = pdf_tables.continue_table(
                tables, outside, page_num, page.rect.height, getattr(self, "_table_carry", None),
                lambda: pdf_tables.next_page_top_lines(page))
            renderer = TableRenderer(self.table_style)
            table_items, table_bboxes = [], []
            for table in tables:
                markdown_table = renderer.render(table.table_data())
                if markdown_table.strip():
                    table_items.append(SimpleContent(
                        type="table",
                        content=markdown_table,
                        page=page_num + 1,
                        position_y=table.bbox[1]
                    ))
                    table_bboxes.append(tuple(table.bbox))
            return table_items, table_bboxes
        except Exception as e:
            logger.warning(f"Failed to extract tables on page {page_num + 1}, using PyMuPDF's plain cell text: {e}")
            self._table_carry = None
            return self._extract_tables_plain(found, page_num)

    def _extract_tables_plain(self, tables, page_num: int) -> Tuple[List[SimpleContent], List[Tuple]]:
        """Last resort: PyMuPDF's own cell text, no merged cells."""
        renderer = TableRenderer(self.table_style)
        table_items, table_bboxes = [], []
        for table in tables:
            try:
                data = table.extract()
                if not any((cell or "").strip() for row in data or [] for cell in row):
                    continue  # no text found: leave the region to the text path
                markdown_table = renderer.render(TableData.from_2d_array(data))
            except Exception as e:
                logger.debug(f"Plain table extraction failed: {e}")
                continue
            if markdown_table.strip():
                table_items.append(SimpleContent(type="table", content=markdown_table, page=page_num + 1,
                                                 position_y=table.bbox[1]))
                table_bboxes.append(tuple(table.bbox))
        return table_items, table_bboxes

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
            ocr_images: Whether to use OCR to convert images to text descriptions (implies image extraction)
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
            ocr_images: Whether to use OCR to convert images to text descriptions (implies image extraction)
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
        if getattr(self, "_copies", None) is not None:
            self._copies.close()
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
        table_style: Union[str, TableStyle] = None,
        legibility_judge=None,
        boilerplate_judge: Optional[BoilerplateJudge] = None,
) -> Dict[str, Any]:
    """
    Convert PDF to simplified JSON with content in reading order

    Args:
        pdf_path: Path to the PDF file
        output_path: Optional path to save JSON output
        output_markdown: Also save as markdown file
        extract_images: Extract images as base64
        ocr_images: Use OCR to convert images to text descriptions (implies image extraction)
        show_progress: Show progress messages
        ocr: OCR instance for image processing
        table_style: Output style for complex tables:
            - 'minimal_html': Clean HTML with only rowspan/colspan (default)
            - 'markdown_grid': Markdown with merge annotations
            - 'styled_html': Full HTML with inline styles (legacy)
        legibility_judge: Optional ``judge(page_text) -> Optional[float]`` for the
            text-layer quality gate (see doc2mark.core.strategy.judge_text_layer)
        boilerplate_judge: Optional judge for repeated header/footer lines the
            deterministic rule keeps; see ``PDFLoader``.
    
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
    converter = PDFLoader(pdf_path, ocr=ocr, table_style=table_style, legibility_judge=legibility_judge,
                          boilerplate_judge=boilerplate_judge)

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
