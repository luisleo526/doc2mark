"""Shared image format detection and conversion utilities.

Provides format detection via magic bytes and conversion to common formats (PNG)
for use across OCR providers and document processing pipelines.
"""

import io
import logging
from typing import Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Format string constants
FORMAT_PNG = 'png'
FORMAT_JPEG = 'jpeg'
FORMAT_GIF = 'gif'
FORMAT_WEBP = 'webp'
FORMAT_TIFF = 'tiff'
FORMAT_BMP = 'bmp'
FORMAT_ICO = 'ico'
FORMAT_EMF = 'emf'
FORMAT_WMF = 'wmf'
FORMAT_HEIC = 'heic'
FORMAT_HEIF = 'heif'
FORMAT_AVIF = 'avif'
FORMAT_UNKNOWN = 'unknown'

# Formats that PIL/Pillow can open natively (cross-platform)
PIL_SUPPORTED_FORMATS = {
    FORMAT_PNG, FORMAT_JPEG, FORMAT_GIF, FORMAT_WEBP,
    FORMAT_TIFF, FORMAT_BMP, FORMAT_ICO,
}

# Formats that require special handling (platform-dependent or need extra libs)
VECTOR_FORMATS = {FORMAT_EMF, FORMAT_WMF}

FORMAT_TO_MIME = {
    FORMAT_PNG: 'image/png',
    FORMAT_JPEG: 'image/jpeg',
    FORMAT_GIF: 'image/gif',
    FORMAT_WEBP: 'image/webp',
    FORMAT_TIFF: 'image/tiff',
    FORMAT_BMP: 'image/bmp',
    FORMAT_ICO: 'image/x-icon',
    FORMAT_EMF: 'image/emf',
    FORMAT_WMF: 'image/wmf',
    FORMAT_HEIC: 'image/heic',
    FORMAT_HEIF: 'image/heif',
    FORMAT_AVIF: 'image/avif',
}


def detect_image_format(image_data: bytes) -> str:
    """Detect image format from binary data using magic bytes.

    Args:
        image_data: Raw image bytes

    Returns:
        Format string: 'png', 'jpeg', 'gif', 'webp', 'tiff', 'bmp',
                       'ico', 'emf', 'wmf', 'heic', 'heif', 'avif', or 'unknown'
    """
    if len(image_data) < 12:
        return FORMAT_UNKNOWN

    if image_data[:8] == b'\x89PNG\r\n\x1a\n':
        return FORMAT_PNG
    if image_data[:2] == b'\xff\xd8':
        return FORMAT_JPEG
    if image_data[:6] in (b'GIF87a', b'GIF89a'):
        return FORMAT_GIF
    if image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
        return FORMAT_WEBP
    if image_data[:4] in (b'II*\x00', b'MM\x00*'):
        return FORMAT_TIFF
    if image_data[:2] == b'BM':
        return FORMAT_BMP
    if image_data[:4] == b'\x00\x00\x01\x00':
        return FORMAT_ICO

    # HEIC / HEIF / AVIF: ISO Base Media File Format — ftyp box at offset 4
    # Box size (4 bytes) + 'ftyp' (4 bytes) + major brand (4 bytes)
    if len(image_data) >= 12 and image_data[4:8] == b'ftyp':
        brand = image_data[8:12]
        if brand in (b'heic', b'heix', b'heim', b'hevx'):
            return FORMAT_HEIC
        if brand in (b'heif', b'mif1', b'msf1'):
            return FORMAT_HEIF
        if brand in (b'avif', b'avis'):
            return FORMAT_AVIF

    # EMF: starts with 0x01000000 and has ' EMF' signature at offset 40
    if len(image_data) > 44 and image_data[:4] == b'\x01\x00\x00\x00':
        if image_data[40:44] == b' EMF':
            return FORMAT_EMF

    # WMF: Aldus placeable metafile header
    if image_data[:4] == b'\xd7\xcd\xc6\x9a':
        return FORMAT_WMF
    # WMF: standard header (type 1 or 2, header size 9, version 0x0100 or 0x0300)
    if len(image_data) >= 6:
        wmf_type = int.from_bytes(image_data[:2], 'little')
        wmf_header_size = int.from_bytes(image_data[2:4], 'little')
        wmf_version = int.from_bytes(image_data[4:6], 'little')
        if wmf_type in (1, 2) and wmf_header_size == 9 and wmf_version in (0x0100, 0x0300):
            return FORMAT_WMF

    return FORMAT_UNKNOWN


def get_mime_type(format_str: str) -> str:
    """Get MIME type string for an image format.

    Args:
        format_str: Format string from detect_image_format()

    Returns:
        MIME type string, defaults to 'image/png' for unknown formats
    """
    return FORMAT_TO_MIME.get(format_str, 'image/png')


def normalize_image_to_png(image_data: bytes) -> bytes:
    """Convert any PIL-supported image to PNG bytes.

    Handles mode conversion (CMYK, palette, etc.) to ensure
    broad compatibility.

    Args:
        image_data: Raw image bytes in any PIL-supported format

    Returns:
        PNG image bytes

    Raises:
        ValueError: If the image cannot be converted
    """
    try:
        from PIL import Image
    except ImportError:
        raise ImportError(
            "Pillow is required for image conversion. "
            "Install with: pip install Pillow"
        )

    try:
        img = Image.open(io.BytesIO(image_data))

        if img.mode in ('CMYK', 'P', 'LA', 'PA'):
            img = img.convert('RGBA')
        elif img.mode not in ('RGB', 'RGBA', 'L'):
            img = img.convert('RGB')

        output = io.BytesIO()
        img.save(output, format='PNG')
        output.seek(0)
        return output.read()
    except Exception as e:
        raise ValueError(f"Failed to convert image to PNG: {e}")


def _try_convert_vector_image(image_data: bytes, format_str: str) -> Optional[bytes]:
    """Attempt to convert EMF/WMF to PNG using available libraries.

    Tries wand (ImageMagick) first, then falls back to other options.

    Args:
        image_data: Raw image bytes
        format_str: 'emf' or 'wmf'

    Returns:
        PNG bytes if conversion succeeded, None otherwise
    """
    # Try wand (ImageMagick binding)
    try:
        from wand.image import Image as WandImage
        with WandImage(blob=image_data, format=format_str) as img:
            img.format = 'png'
            return img.make_blob()
    except ImportError:
        logger.debug("wand (ImageMagick) not available for %s conversion", format_str)
    except Exception as e:
        logger.debug("wand conversion failed for %s: %s", format_str, e)

    # Try PIL as last resort (Windows-only for EMF/WMF, but worth trying)
    try:
        return normalize_image_to_png(image_data)
    except Exception as e:
        logger.debug("PIL conversion failed for %s: %s", format_str, e)

    return None


def convert_image_to_supported_format(
    image_data: bytes,
    supported_formats: Optional[Set[str]] = None,
) -> Tuple[bytes, str]:
    """Convert image to a supported format, returning (bytes, mime_type).

    If the image is already in a supported format, returns it as-is.
    Otherwise, attempts conversion to PNG.

    Args:
        image_data: Raw image bytes
        supported_formats: Set of accepted format strings.
            Defaults to {'png', 'jpeg', 'jpg', 'gif', 'webp'} (OpenAI Vision API).

    Returns:
        Tuple of (converted_image_bytes, mime_type)
    """
    if supported_formats is None:
        supported_formats = {'png', 'jpeg', 'jpg', 'gif', 'webp'}

    current_format = detect_image_format(image_data)

    if current_format in supported_formats:
        mime_type = get_mime_type(current_format)
        logger.debug("Image format '%s' is already supported", current_format)
        return image_data, mime_type

    logger.info("Converting image from '%s' to PNG", current_format)

    # Vector formats need special handling
    if current_format in VECTOR_FORMATS:
        converted = _try_convert_vector_image(image_data, current_format)
        if converted is not None:
            logger.debug(
                "Vector image converted: %d bytes -> %d bytes",
                len(image_data), len(converted),
            )
            return converted, 'image/png'
        logger.warning(
            "Cannot convert %s image. Install wand (ImageMagick) for support. "
            "Returning original bytes — downstream processing may fail.",
            current_format.upper(),
        )
        return image_data, get_mime_type(current_format)

    # Raster formats — use PIL
    try:
        converted = normalize_image_to_png(image_data)
        logger.debug(
            "Image converted: %d bytes -> %d bytes",
            len(image_data), len(converted),
        )
        return converted, 'image/png'
    except (ImportError, ValueError) as e:
        logger.warning("Image conversion failed: %s. Returning original bytes.", e)
        return image_data, get_mime_type(current_format)
