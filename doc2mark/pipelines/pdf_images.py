"""The pictures of a PDF page that the text route OCRs, measured with PyMuPDF.

A *placement* is one place where the page draws a raster image (an image XObject,
directly or through a Form XObject, or an inline image). A *picture* is what the
text route OCRs as one unit and emits once per place it shows:

- an image XObject the page shows whole: one OCR request per image, however often
  the document shows it, and one item per placement;
- tiles: placements that abut edge to edge and together show one picture (a scan cut
  into strips or a grid): the region they cover, rendered, is OCR'd as one picture;
- an inline image (it has no xref), or a picture the page shows only in part (cropped
  by a clip path or by the page edge): the part the page shows, rendered.

What a placement shows is measured with its clip path and the page applied. A placement
the page does not show (off the page, clipped away, or only a sliver of it) is no
picture. A picture is only sent to OCR when its pixels carry content; see
:func:`picture_content`. Everything here is geometry and pixels, never OCR.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pymupdf

logger = logging.getLogger(__name__)

# --- What the page shows --------------------------------------------------------
# What a placement shows is its clipped extent: the image's box cut by the clip paths
# around it and by the visible page (CropBox), as MuPDF's text device reports it. A
# picture the page shows whole always shows, however thin (a line of text kept as an
# image). A picture shown only in part shows when the part is at least
# MIN_PICTURE_POINTS on both sides: a picture placed off the page, clipped away, or
# bleeding onto the page by a sliver shows nothing a reader could read. A picture of
# fewer than MIN_PICTURE_PIXELS pixels on a side holds nothing legible (rules, bullets,
# spacers). These rules apply to whole pictures, after tiles are joined: a figure
# stored as thin bands shows as the figure.
MIN_PICTURE_POINTS = 12.0
MIN_PICTURE_PIXELS = 12
WHOLE_SHARE = 0.99   # a placement showing at least this share of its box shows whole

# --- Tiles ------------------------------------------------------------------------
# Two placements are tiles of one picture when they share an edge: they touch (at most
# TILE_GAP points apart), overlap by at most TILE_OVERLAP of the smaller one, and run
# alongside each other for at least half of the shorter side.
TILE_GAP = 1.0
TILE_OVERLAP = 0.05

# --- Content --------------------------------------------------------------------
# A picture is judged on a grey copy of at most CLASSIFY_SIDE pixels a side.
# - "plain": fewer than MIN_DETAIL_PIXELS edge pixels (a neighbour at least
#   DETAIL_CONTRAST grey levels away), once rows and columns that are mostly edge
#   (LINE_SHARE of them: rules, frame lines) are left out: a flat colour, a gradient,
#   a blank area, a frame. Nothing to read: never OCR'd. The contrast is low on purpose:
#   a faint scan (grey print on white, 40 levels apart) still has edges after the copy
#   is shrunk, and a false "plain" would lose its words.
# - "text": ink (at least INK_CONTRAST levels off the picture's most common grey) broken
#   into strokes the way glyphs are: at least TEXT_TRANSITIONS ink/background changes
#   along the average inked pixel row (a solid icon has 2, a ring 4, a word of text
#   many more, and so have charts with printed values and photos).
# - "shapes": anything else (an icon, a logo mark without letters).
# A *small* picture (under SMALL_PICTURE_SHARE of the page in both directions, or under
# SMALL_PICTURE_POINTS on both sides) is OCR'd only when it reads as text; a larger one
# unless it is plain. When unsure (pixels that cannot be read), a picture is OCR'd.
CLASSIFY_SIDE = 1024
DETAIL_CONTRAST = 16
MIN_DETAIL_PIXELS = 24
LINE_SHARE = 0.8
INK_CONTRAST = 32
TEXT_TRANSITIONS = 6
SMALL_PICTURE_SHARE = 0.10
SMALL_PICTURE_POINTS = 48.0

# --- Rendering regions ------------------------------------------------------------
# A region (tiles, an inline image, a cropped picture) is rendered at the resolution its
# images carry (at least REGION_MIN_DPI, at most REGION_MAX_DPI) and at most
# REGION_MAX_PIXELS in all.
REGION_MIN_DPI = 150
REGION_MAX_DPI = 300
REGION_MAX_PIXELS = 16_000_000
_CLASSIFY_DPI = 72

PLAIN, TEXT, SHAPES = "plain", "text", "shapes"


def page_area(page) -> pymupdf.Rect:
    """The visible page (CropBox) in the frame of image coordinates (unrotated, CropBox origin)."""
    return pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)


def _area(rect: pymupdf.Rect) -> float:
    return 0.0 if rect.is_empty else abs(rect.width * rect.height)


@dataclass(frozen=True)
class Placement:
    """One place where the page draws a raster image (unrotated page coordinates)."""

    xref: int                 # 0 for an inline image
    bbox: pymupdf.Rect        # where the image is drawn
    visible: pymupdf.Rect     # the part of it the page shows (clip paths and the visible page applied)
    width: int = 0            # image pixels
    height: int = 0

    @property
    def tiny(self) -> bool:
        return 0 < min(self.width, self.height) < MIN_PICTURE_PIXELS

    @property
    def partial(self) -> bool:
        """Whether the page shows only part of it (cropped by a clip path or by the page edge)."""
        return _area(self.visible) < WHOLE_SHARE * _area(self.bbox)


def shows(visible: pymupdf.Rect, whole: pymupdf.Rect) -> bool:
    """Whether a picture drawn at ``whole`` of which the page shows ``visible`` shows (see MIN_PICTURE_POINTS)."""
    shown = _area(visible)
    if not shown:
        return False
    return shown >= WHOLE_SHARE * _area(whole) or min(visible.width, visible.height) >= MIN_PICTURE_POINTS


def _clipped_image_boxes(page) -> Optional[List[pymupdf.Rect]]:
    """The extent the page shows of each image it draws, in drawing order: MuPDF's text device, asked to keep
    images and to clip, reports each image's box cut by the clip paths around it and by the page (an image
    clipped away comes back as an empty box). None when this PyMuPDF cannot tell."""
    clip = getattr(pymupdf, "TEXT_CLIP", None)
    if clip is None:
        return None
    try:
        textpage = page.get_textpage(flags=pymupdf.TEXT_PRESERVE_IMAGES | clip)
        blocks = [block for block in textpage.extractBLOCKS() if block[6] == 1]
    except Exception as exc:
        logger.debug(f"Could not measure the clipped images of page {page.number + 1}: {exc}")
        return None
    return [pymupdf.Rect(block[:4]) if block[0] < block[2] and block[1] < block[3] else pymupdf.Rect()
            for block in blocks]


def placements(page) -> List[Placement]:
    """Every raster image placement of ``page``, each once (``get_image_info`` walks what the page draws:
    an image listed twice in the resources, directly and through a Form XObject, is drawn once), with the
    part the page shows (see :func:`_clipped_image_boxes`; the box on the visible page when the clipped
    extents cannot be matched to the placements one to one)."""
    area = page_area(page)
    infos = page.get_image_info(xrefs=True)
    clipped = _clipped_image_boxes(page)
    if clipped is not None and len(clipped) != len(infos):
        logger.debug(f"Page {page.number + 1}: {len(clipped)} clipped image boxes for {len(infos)} images; "
                     f"measuring the images unclipped")
        clipped = None
    seen = set()
    result = []
    for index, info in enumerate(infos):
        bbox = pymupdf.Rect(info["bbox"])
        key = (info.get("xref") or 0, tuple(round(value, 1) for value in bbox))
        if key in seen or bbox.is_empty:
            continue
        seen.add(key)
        visible = bbox & area
        if clipped is not None:
            visible = visible & clipped[index] if not clipped[index].is_empty else pymupdf.Rect()
        result.append(Placement(xref=info.get("xref") or 0, bbox=bbox, visible=visible,
                                width=int(info.get("width") or 0), height=int(info.get("height") or 0)))
    return result


def _abut(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    """Whether ``a`` and ``b`` share an edge (see TILE_GAP and TILE_OVERLAP)."""
    overlap = a & b
    smaller = min(abs(a.width * a.height), abs(b.width * b.height)) or 1.0
    if not overlap.is_empty and abs(overlap.width * overlap.height) > TILE_OVERLAP * smaller:
        return False
    across = min(a.y1, b.y1) - max(a.y0, b.y0)      # side by side: they share a vertical edge
    if (abs(a.x1 - b.x0) <= TILE_GAP or abs(b.x1 - a.x0) <= TILE_GAP) \
            and across >= 0.5 * min(a.height, b.height):
        return True
    along = min(a.x1, b.x1) - max(a.x0, b.x0)       # stacked: they share a horizontal edge
    return (abs(a.y1 - b.y0) <= TILE_GAP or abs(b.y1 - a.y0) <= TILE_GAP) \
        and along >= 0.5 * min(a.width, b.width)


_CELL = 36.0   # points: tile candidates are looked up in a grid of cells this size


def tile_groups(placed: Sequence[Placement]) -> List[List[Placement]]:
    """``placed`` split into groups of tiles (see :func:`_abut`); a placement abutting no other is a group of
    one. Only placements sharing a grid cell are compared, so thousands of bands stay cheap."""
    parent = list(range(len(placed)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    cells: Dict[Tuple[int, int], List[int]] = {}
    for index, placement in enumerate(placed):
        box = placement.bbox
        for cx in range(math.floor((box.x0 - TILE_GAP) / _CELL), math.floor((box.x1 + TILE_GAP) / _CELL) + 1):
            for cy in range(math.floor((box.y0 - TILE_GAP) / _CELL), math.floor((box.y1 + TILE_GAP) / _CELL) + 1):
                cells.setdefault((cx, cy), []).append(index)
    compared = set()
    for members in cells.values():
        for position, i in enumerate(members):
            for j in members[position + 1:]:
                if (i, j) in compared:
                    continue
                compared.add((i, j))
                if root(i) != root(j) and _abut(placed[i].bbox, placed[j].bbox):
                    parent[root(i)] = root(j)
    groups: Dict[int, List[Placement]] = {}
    for index, placement in enumerate(placed):
        groups.setdefault(root(index), []).append(placement)
    return sorted(groups.values(), key=lambda group: min((p.bbox.y0, p.bbox.x0) for p in group))


@dataclass
class Picture:
    """What the text route OCRs as one unit (see the module docstring)."""

    key: Union[int, str]                  # the xref, or "region-<n>" for a rendered region
    rects: List[pymupdf.Rect]             # where it shows: one emitted item each
    xref: int = 0                         # the image XObject (0 for a region)
    region: Optional[pymupdf.Rect] = None  # the region to render (unrotated page coordinates)
    dpi: float = REGION_MIN_DPI           # resolution of a region's images
    placements: List[Placement] = field(default_factory=list)


def _native_dpi(group: Sequence[Placement]) -> float:
    """The resolution (pixels per inch) the group's images carry on the page."""
    dpis = [72.0 * p.width / p.bbox.width for p in group if p.width and p.bbox.width > 0]
    return min(REGION_MAX_DPI, max([REGION_MIN_DPI] + dpis))


def page_pictures(page, placed: Optional[Sequence[Placement]] = None) -> Tuple[List[Picture], Dict[str, int]]:
    """The pictures of ``page`` in reading order (top to bottom), before any pixel check, and how many
    placements were left out: the page does not show them (``not_shown``), or they are too small to hold
    anything legible (``no_content``). Tiles are joined first; the rules then judge whole pictures.
    ``placed`` is the page's :func:`placements` when the caller has them already."""
    everything = placements(page) if placed is None else list(placed)
    skipped = {"not_shown": 0, "no_content": 0}
    pictures: List[Picture] = []
    by_xref: Dict[int, Picture] = {}
    regions = 0
    skipped["not_shown"] = sum(1 for p in everything if p.visible.is_empty)
    for group in tile_groups([p for p in everything if not p.visible.is_empty]):
        region, whole = pymupdf.Rect(), pymupdf.Rect()
        for placement in group:
            region |= placement.visible
            whole |= placement.bbox
        if not shows(region, whole):
            skipped["not_shown"] += len(group)
            continue
        placement = group[0]
        if len(group) == 1 and placement.tiny:
            skipped["no_content"] += 1
            continue
        if len(group) > 1 or not placement.xref or placement.partial:
            # Rendered as the page shows it: tiles joined, an inline image, a cropped picture.
            pictures.append(Picture(key=f"region-{regions}", rects=[region], region=region,
                                    dpi=_native_dpi(group), placements=list(group)))
            regions += 1
            continue
        picture = by_xref.get(placement.xref)
        if picture is None:
            picture = by_xref[placement.xref] = Picture(key=placement.xref, rects=[], xref=placement.xref)
            pictures.append(picture)
        picture.rects.append(placement.visible)
        picture.placements.append(placement)
    return pictures, skipped


def single_rects(page, placed: Optional[Sequence[Placement]] = None) -> List[pymupdf.Rect]:
    """Visible rectangles of the pictures the text route reads one by one whatever their pixels: image
    XObjects the page shows whole that are not small (see :func:`is_small`). Tiles, inline images, cropped
    pictures and small pictures (icons, thumbnails: read only when they look like text) are not counted, so
    a page without a text layer that shows them is left to its render. Geometry only: cheap for routing."""
    rects = []
    for picture in page_pictures(page, placed)[0]:
        if picture.xref:
            rects.extend(rect for rect in picture.rects if not is_small(rect, page))
    return rects


def is_small(rect: pymupdf.Rect, page) -> bool:
    """Small enough to be an icon, a bullet or a logo mark (see SMALL_PICTURE_SHARE), compared with the
    page in the same (unrotated) frame."""
    area = page_area(page)
    return ((abs(rect.width) < SMALL_PICTURE_SHARE * area.width and abs(rect.height) < SMALL_PICTURE_SHARE * area.height)
            or max(abs(rect.width), abs(rect.height)) < SMALL_PICTURE_POINTS)


# --- Pixels ---------------------------------------------------------------------


def _grey_samples(pix: pymupdf.Pixmap) -> np.ndarray:
    """A pixmap as a grey array (rows x columns), alpha flattened onto white, sampled down to at most
    CLASSIFY_SIDE pixels a side (every n-th pixel: thin strokes keep their contrast). ``pix`` is never
    changed: a pixmap made from an image XObject is MuPDF's cached copy of that image, and shrinking it
    in place would shrink the image everywhere else it is read (extraction, renders)."""
    step = max(1, math.ceil(max(pix.width, pix.height) / CLASSIFY_SIDE))
    buffer = getattr(pix, "samples_mv", None) or pix.samples
    rows = np.frombuffer(buffer, dtype=np.uint8).reshape(pix.height, pix.stride)[::step, :pix.width * pix.n]
    samples = rows.reshape(rows.shape[0], pix.width, pix.n)[:, ::step, :].astype(np.float32)
    if pix.colorspace is None:   # a stencil mask (/ImageMask): alpha only, painted where opaque
        return (255.0 - samples[:, :, -1]).astype(np.int16)
    alpha = pix.alpha
    colour = samples[:, :, :pix.n - 1] if alpha else samples
    if colour.shape[2] >= 3:
        grey = colour[:, :, 0] * 0.299 + colour[:, :, 1] * 0.587 + colour[:, :, 2] * 0.114
    else:
        grey = colour[:, :, 0]
    if alpha:
        share = samples[:, :, -1] / 255.0
        grey = grey * share + 255.0 * (1.0 - share)
    return grey.astype(np.int16)


def _masked_pixmap(doc, xref: int) -> Optional[pymupdf.Pixmap]:
    """The image XObject ``xref`` decoded (grey or RGB), with its soft mask as alpha; None when it has a
    soft mask that cannot be applied (a transparent logo's colour channels alone may be flat)."""
    pix = pymupdf.Pixmap(doc, xref)
    if pix.colorspace is not None and pix.colorspace.n not in (1, 3):   # CMYK, DeviceN, ...
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    smask = doc.xref_get_key(xref, "SMask")
    if smask[0] == "xref" and not pix.alpha:
        mask = pymupdf.Pixmap(doc, int(smask[1].split()[0]))
        if (mask.width, mask.height) != (pix.width, pix.height) or mask.n != 1:
            return None
        pix = pymupdf.Pixmap(pix, mask)
    return pix


def image_grey(doc, xref: int) -> Optional[np.ndarray]:
    """The image XObject ``xref`` as a grey array, its soft mask applied; None when it cannot be decoded
    or its soft mask cannot be applied."""
    try:
        pix = _masked_pixmap(doc, xref)
        return None if pix is None else _grey_samples(pix)
    except Exception as exc:
        logger.debug(f"Could not decode image {xref} for its content check: {exc}")
        return None


def flattened_png(doc, xref: int) -> Optional[bytes]:
    """For an image XObject with a soft mask (transparency), a PNG of it composited onto white, as a white
    page shows it: the image's own colour channels may be flat (black letters whose shape is only in the
    mask). None for an image without a soft mask, or when it cannot be composited."""
    if doc.xref_get_key(xref, "SMask")[0] != "xref":
        return None
    try:
        pix = _masked_pixmap(doc, xref)
        if pix is None or not pix.alpha:
            return None
        samples = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width * pix.n]
        samples = samples.reshape(pix.height, pix.width, pix.n).astype(np.float32)
        share = samples[:, :, -1:] / 255.0
        colour = samples[:, :, :-1] * share + 255.0 * (1.0 - share)
        space = pymupdf.csRGB if colour.shape[2] == 3 else pymupdf.csGRAY
        flat = pymupdf.Pixmap(space, pix.width, pix.height, np.round(colour).astype(np.uint8).tobytes(), 0)
        return flat.tobytes("png")
    except Exception as exc:
        logger.debug(f"Could not composite image {xref} onto white: {exc}")
        return None


def picture_content(grey: np.ndarray) -> str:
    """``PLAIN``, ``TEXT`` or ``SHAPES`` for a grey picture (see the module constants)."""
    if grey.size == 0:
        return PLAIN
    edges = np.zeros(grey.shape, dtype=bool)
    edges[:, 1:] |= np.abs(np.diff(grey, axis=1)) >= DETAIL_CONTRAST
    edges[1:, :] |= np.abs(np.diff(grey, axis=0)) >= DETAIL_CONTRAST
    edges[edges.mean(axis=1) >= LINE_SHARE, :] = False     # rules and frame lines
    edges[:, edges.mean(axis=0) >= LINE_SHARE] = False
    if int(edges.sum()) < MIN_DETAIL_PIXELS:
        return PLAIN
    background = int(np.bincount(np.clip(grey, 0, 255).ravel().astype(np.int64), minlength=256).argmax())
    ink = np.abs(grey - background) >= INK_CONTRAST
    rows = ink.any(axis=1)
    if not rows.any():
        return SHAPES
    transitions = np.abs(np.diff(ink[rows].astype(np.int8), axis=1)).sum(axis=1)
    return TEXT if float(transitions.mean()) >= TEXT_TRANSITIONS else SHAPES


def _covering_text(page, region: pymupdf.Rect) -> bool:
    """Whether painted text lies in ``region`` (it would show in a render of the region)."""
    try:
        for block in page.get_text("dict", clip=region, flags=0).get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip() and span.get("alpha", 255):
                        return True
    except Exception:
        return True
    return False


def render_region(page, region: pymupdf.Rect, dpi: float, copies=None) -> pymupdf.Pixmap:
    """The pixels of ``region`` (unrotated page coordinates) as the page shows them, without its text when a
    page copy can be made from ``copies`` (a ``pdf_routing.PageCopies``): the text layer already emits that
    text. Discard ``copies`` afterwards."""
    clip = region * page.rotation_matrix
    pixels = max(1.0, clip.width * clip.height) * (dpi / 72) ** 2
    if pixels > REGION_MAX_PIXELS:
        dpi = 72 * math.sqrt(REGION_MAX_PIXELS / max(1.0, clip.width * clip.height))
    source = page
    if copies is not None and _covering_text(page, region):
        try:
            source = copies.copy(page.number)
            source.add_redact_annot(region, fill=False)
            source.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                    graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                    text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        except Exception as exc:  # render with the text rather than not at all
            logger.debug(f"Could not remove the text over a picture on page {page.number + 1}: {exc}")
            source = page
    return source.get_pixmap(clip=clip, dpi=round(dpi), alpha=False)


def classify(page, picture: Picture, grey_of_xref, copies=None) -> str:
    """The content of ``picture`` (``PLAIN``, ``TEXT`` or ``SHAPES``); ``grey_of_xref(xref)`` returns an
    image's grey array (callers cache it: one image may show on every page). ``TEXT`` when unsure."""
    try:
        if picture.xref:
            grey = grey_of_xref(picture.xref)
        else:
            grey = _grey_samples(render_region(page, picture.region, _CLASSIFY_DPI, copies))
    except Exception as exc:
        logger.debug(f"Content check failed for a picture on page {page.number + 1}: {exc}")
        grey = None
    finally:
        if copies is not None and not picture.xref:
            copies.discard()
    return TEXT if grey is None else picture_content(grey)


def ocr_worthy(page, picture: Picture, content: str) -> bool:
    """Whether a picture of this content is sent to OCR: never a plain one; a small one only when it reads
    as text; any other one."""
    if content == PLAIN:
        return False
    if content == TEXT:
        return True
    return not all(is_small(rect, page) for rect in picture.rects)
