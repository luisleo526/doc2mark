"""Tesseract OCR implementation for fallback support."""

import io
import logging
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from typing import List, Optional, Tuple

from doc2mark.core.base import OCRError
from doc2mark.ocr.base import BaseOCR, OCRConfig, OCREngineError, OCRProvider, OCRResult, OCRFactory
from doc2mark.ocr.schema import OCRPage, RawExtraction
from doc2mark.utils.image_utils import (
    detect_image_format,
    PIL_SUPPORTED_FORMATS,
    convert_image_to_supported_format as _convert_image,
)

logger = logging.getLogger(__name__)

# Long language names accepted as aliases (case-insensitive) next to the native
# Tesseract codes (eng, deu, chi_tra, chi_sim, eng+chi_tra, ...).
LANGUAGE_ALIASES = {
    'english': 'eng',
    'chinese': 'chi_sim+chi_tra',
    'chinese_simplified': 'chi_sim',
    'chinese_traditional': 'chi_tra',
    'spanish': 'spa',
    'french': 'fra',
    'german': 'deu',
    'japanese': 'jpn',
    'korean': 'kor',
    'russian': 'rus',
    'arabic': 'ara',
}
# A traineddata name as Tesseract takes it on -l (eng, chi_tra, deu_latf, script/Latin).
_LANGUAGE_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)?$")
# Messages of a Tesseract run that failed to start at all (no usable language data).
_ENGINE_FAILURE_MARKERS = (
    "failed loading language", "could not initialize tesseract", "couldn't load any languages",
    "error opening data file",
)


class TesseractOCR(BaseOCR):
    """Tesseract-based OCR implementation."""

    def __init__(self, api_key: Optional[str] = None, config: Optional[OCRConfig] = None):
        """Initialize Tesseract OCR provider.
        
        Args:
            api_key: Not used for Tesseract
            config: OCR configuration
        """
        super().__init__(api_key, config)
        self._pytesseract = None
        self._pil = None
        # (requested language, tessdata env, binary) the engine check passed for, and
        # the language code it resolved to; re-checked when any of them changes.
        self._engine_checked_for: Optional[Tuple[str, Optional[str], str]] = None
        self._engine_language: Optional[str] = None

        logger.info("📝 Initializing Tesseract OCR (offline mode)")
        if config and config.language:
            logger.info(f"🌐 Language configured: {config.language}")
        else:
            logger.info("🌐 Using default language: English")

        # Log configuration settings
        if config:
            logger.debug(f"⚙️  Configuration:")
            logger.debug(f"   - Language: {config.language or 'english'}")
            logger.debug(f"   - Enhance image: {config.enhance_image}")
            logger.debug(f"   - Detect layout: {config.detect_layout}")
            logger.debug(f"   - Detect tables: {config.detect_tables}")

    @property
    def pytesseract(self):
        """Lazy load pytesseract."""
        if self._pytesseract is None:
            logger.debug("📦 Loading pytesseract...")
            try:
                import pytesseract
                self._pytesseract = pytesseract
                logger.debug("✓ pytesseract loaded successfully")
            except ImportError:
                logger.error("❌ pytesseract is not installed")
                raise ImportError(
                    "pytesseract is not installed. "
                    "Install it with: pip install pytesseract"
                )
        return self._pytesseract

    @property
    def pil(self):
        """Lazy load PIL."""
        if self._pil is None:
            logger.debug("📦 Loading PIL/Pillow...")
            try:
                from PIL import Image
                self._pil = Image
                logger.debug("✓ PIL/Pillow loaded successfully")
            except ImportError:
                logger.error("❌ Pillow is not installed")
                raise ImportError(
                    "Pillow is not installed. "
                    "Install it with: pip install Pillow"
                )
        return self._pil

    def validate_api_key(self) -> bool:
        """Tesseract doesn't require an API key."""
        logger.debug("✓ Tesseract validation: No API key required")
        return True

    def _process_single_image(self, image_data: bytes, language_code: Optional[str] = None, **kwargs) -> OCRResult:
        """Internal method to process a single image using Tesseract.
        
        Args:
            image_data: Image data as bytes
            language_code: Tesseract ``-l`` value (from :meth:`_ensure_engine`); resolved
                from the config when omitted
            **kwargs: Additional options
            
        Returns:
            OCRResult with extracted text

        Raises:
            OCREngineError: Tesseract itself could not start (no usable language data)
            OCRError: this image could not be read
        """
        image_size = len(image_data)
        if language_code is None:
            language_code = self._ensure_engine()
        logger.debug(f"🖼️  Processing image with Tesseract ({image_size} bytes)")

        try:
            # Normalize unsupported formats (EMF, WMF, etc.) to PNG before PIL opens them
            fmt = detect_image_format(image_data)
            if fmt not in PIL_SUPPORTED_FORMATS and fmt != 'unknown':
                logger.info(f"🔄 Normalizing '{fmt}' image to PNG for Tesseract")
                image_data, _ = _convert_image(image_data, supported_formats=PIL_SUPPORTED_FORMATS)

            logger.debug("🔄 Converting bytes to PIL Image...")
            image = self.pil.open(io.BytesIO(image_data))
            original_mode = image.mode
            original_size = image.size
            logger.debug(f"✓ Image loaded: {original_size[0]}x{original_size[1]}, mode: {original_mode}")

            # Preprocess image if enhancement is enabled
            if self.config.enhance_image:
                logger.debug("🎨 Preprocessing image for better OCR...")
                image_data = self.preprocess_image(image_data)
                image = self.pil.open(io.BytesIO(image_data))
                logger.debug(f"✓ Image preprocessed: {image.size[0]}x{image.size[1]}, mode: {image.mode}")

            # Configure Tesseract
            config_str = self._build_tesseract_config(**kwargs)
            logger.debug(f"⚙️  Tesseract config: {config_str}")
            logger.debug(f"🌐 Language: {language_code}")

            # Perform OCR
            logger.debug("🧠 Starting Tesseract OCR...")
            text = self.pytesseract.image_to_string(
                image,
                lang=language_code,
                config=config_str
            )

            # Clean up extracted text
            text = text.strip()
            logger.debug(f"📝 Extracted text length: {len(text)} chars")

            if text:
                # Log first 100 chars as preview
                preview = text[:100].replace('\n', ' ')
                logger.debug(f"📄 Text preview: {preview}...")
            else:
                logger.debug("⚠️  No text extracted from image")

            # Get confidence scores if requested
            confidence = None
            if kwargs.get("with_confidence", False):
                logger.debug("📊 Calculating confidence scores...")
                try:
                    data = self.pytesseract.image_to_data(
                        image,
                        lang=language_code,
                        output_type=self.pytesseract.Output.DICT
                    )
                    confidences = [int(c) for c in data['conf'] if int(c) > 0]
                    if confidences:
                        confidence = sum(confidences) / len(confidences) / 100.0  # Convert to 0-1 range
                        logger.debug(f"📊 Average confidence: {confidence:.2f}")
                    else:
                        confidence = 0.0
                        logger.debug("⚠️  No confidence data available")
                except Exception as e:
                    logger.debug(f"⚠️  Failed to calculate confidence: {e}")
                    confidence = None

            logger.debug("✅ Tesseract OCR completed successfully")

            # Build structured document (interpretation-free for non-LLM provider).
            # raw.text keeps the verbatim transcription; ``text`` is its Markdown
            # rendering, escaped like every OCR answer (an image can show markup).
            document = OCRPage(
                raw=RawExtraction(
                    text=text,
                    detected_language=self.config.language,
                ),
                interpretation=None,
            )

            return OCRResult(
                text=document.to_markdown(),
                confidence=confidence,
                language=self.config.language,
                metadata={
                    "engine": "tesseract",
                    "config": config_str,
                    "language_code": language_code,
                    "image_size_bytes": image_size,
                    "original_image_size": original_size,
                    "original_image_mode": original_mode,
                    "enhanced": self.config.enhance_image
                },
                document=document,
            )

        except Exception as e:
            message = str(e)
            if isinstance(e, self.pytesseract.TesseractNotFoundError) or any(
                    marker in message.lower() for marker in _ENGINE_FAILURE_MARKERS):
                raise OCREngineError(self._engine_hint(f"Tesseract could not start: {message}")) from e
            logger.error(f"❌ Tesseract OCR failed on an image ({image_size} bytes, language {language_code}): {e}")
            raise OCRError(f"Failed to process image with Tesseract: {message}") from e

    def batch_process_images(
            self,
            images: List[bytes],
            max_workers: int = 4,  # Tesseract is CPU-bound, so fewer workers
            **kwargs
    ) -> List[OCRResult]:
        """
        Process multiple images concurrently for better performance.
        
        Args:
            images: List of image data
            max_workers: Maximum number of concurrent workers (CPU-bound, so fewer workers)
            **kwargs: Additional options
            
        Returns:
            List of OCR results in the same order as input
        """
        total_images = len(images)
        logger.info(f"🚀 Starting batch Tesseract processing of {total_images} images with {max_workers} workers")

        if total_images == 0:
            return []

        # The engine must be usable before any image is read: a missing binary or
        # missing language data fails the batch loudly (OCREngineError) instead of
        # turning every image into an empty result.
        language_code = self._ensure_engine()

        def process_single_image(index: int, image_data: bytes):
            """One image; a failure on this image becomes a failed (empty) result."""
            try:
                logger.debug(f"🔄 Processing image {index + 1}/{total_images} with Tesseract")
                return index, self._process_single_image(image_data, language_code=language_code, **kwargs)
            except OCREngineError:
                raise
            except Exception as e:
                logger.error(f"❌ Failed to process image {index + 1}: {e}")
                return index, OCRResult(
                    text="",
                    metadata={
                        "error": str(e),
                        "image_index": index,
                        "failed": True,
                        "engine": "tesseract"
                    }
                )

        results = [None] * total_images

        # For small batches, use sequential processing
        if total_images <= 2:
            logger.debug("Using sequential processing for small batch")
            for index, image_data in enumerate(images):
                results[index] = process_single_image(index, image_data)[1]
            return results

        # Process images concurrently
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_single_image, index, image_data)
                       for index, image_data in enumerate(images)]

            # Collect results as they complete; an engine failure stops the batch.
            completed_count = 0
            for future in as_completed(futures):
                index, result = future.result()
                results[index] = result
                completed_count += 1

                # Log progress every 10% or every 3 images (Tesseract is slower)
                if completed_count % max(1, total_images // 10) == 0 or completed_count % 3 == 0:
                    progress = completed_count / total_images * 100
                    logger.info(f"📊 Batch progress: {completed_count}/{total_images} ({progress:.1f}%)")

        # Count successful results
        successful = sum(1 for r in results if r and not r.metadata.get('failed'))
        logger.info(f"✅ Batch Tesseract OCR complete: {successful}/{total_images} successful")

        return results

    def preprocess_image(self, image_data: bytes) -> bytes:
        """Preprocess image for better OCR results.
        
        Args:
            image_data: Raw image data
            
        Returns:
            Preprocessed image data
        """
        if not self.config.enhance_image:
            logger.debug("🎨 Image enhancement disabled, skipping preprocessing")
            return image_data

        logger.debug("🎨 Starting image preprocessing...")
        try:
            # Convert to PIL Image
            image = self.pil.open(io.BytesIO(image_data))
            original_mode = image.mode
            logger.debug(f"   Original mode: {original_mode}")

            # Convert to grayscale for better OCR
            if image.mode != 'L':
                logger.debug("   Converting to grayscale...")
                image = image.convert('L')

            # Enhance contrast (simple thresholding)
            threshold = 150
            logger.debug(f"   Applying threshold: {threshold}")
            image = image.point(lambda p: p > threshold and 255)

            # Convert back to bytes
            logger.debug("   Converting back to bytes...")
            output = io.BytesIO()
            image.save(output, format='PNG')
            processed_data = output.getvalue()

            logger.debug(f"✓ Image preprocessing complete: {len(image_data)} -> {len(processed_data)} bytes")
            return processed_data

        except Exception as e:
            logger.warning(f"⚠️  Image preprocessing failed: {e}")
            logger.warning("   Using original image data")
            return image_data

    def _get_language_code(self) -> str:
        """Tesseract ``-l`` value for ``config.language``.

        Native Tesseract codes are taken as they are, alone or combined with ``+``
        (``eng``, ``deu``, ``chi_tra``, ``eng+chi_tra``), and the long names in
        :data:`LANGUAGE_ALIASES` still map to codes (``chinese_traditional`` ->
        ``chi_tra``). No language means English. Whether the codes are installed is
        checked by :meth:`_ensure_engine`.

        Raises:
            ValueError: a component is empty or not a Tesseract language name.
        """
        requested = (self.config.language or "").strip()
        if not requested:
            return 'eng'
        codes: List[str] = []
        for component in requested.split('+'):
            component = component.strip()
            alias = LANGUAGE_ALIASES.get(component.lower())
            if alias:
                expanded = alias.split('+')
            elif component and _LANGUAGE_CODE_RE.match(component):
                expanded = [component]
            else:
                raise ValueError(
                    f"Invalid Tesseract language {requested!r}: use Tesseract codes such as "
                    f"'eng', 'chi_tra' or 'eng+chi_tra', or one of: {', '.join(sorted(LANGUAGE_ALIASES))}"
                )
            codes.extend(code for code in expanded if code not in codes)
        logger.debug(f"🌐 Language mapping: '{requested}' -> '{'+'.join(codes)}'")
        return '+'.join(codes)

    def _installed_languages(self) -> Tuple[Optional[str], List[str]]:
        """``tesseract --list-langs``: the tessdata directory and the installed codes."""
        command = self.pytesseract.pytesseract.tesseract_cmd
        try:
            listing = subprocess.run([command, "--list-langs"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise OCREngineError(self._engine_hint(
                f"Tesseract is not installed or not runnable ({command!r}): {exc}")) from exc
        lines = [line.strip() for line in (listing.stdout or "").splitlines() if line.strip()]
        directory = None
        if lines and lines[0].lower().startswith("list of available languages"):
            match = re.search(r'"([^"]*)"', lines[0])
            directory = match.group(1) if match else None
            lines = lines[1:]
        if listing.returncode != 0 and not lines:
            raise OCREngineError(self._engine_hint(
                f"'tesseract --list-langs' failed: {(listing.stderr or '').strip() or listing.returncode}"))
        return directory, lines

    @staticmethod
    def _engine_hint(message: str) -> str:
        prefix = os.environ.get("TESSDATA_PREFIX")
        where = f"TESSDATA_PREFIX={prefix}" if prefix else "TESSDATA_PREFIX is not set"
        return f"{message} ({where}; install the tessdata language packs or point TESSDATA_PREFIX at them)"

    def _ensure_engine(self) -> str:
        """Check once (per language and environment) that Tesseract can run with the
        requested language data, and return the ``-l`` value to use.

        Raises:
            OCREngineError: Tesseract is missing, the language value is invalid, or
                language data is not installed (e.g. an unknown code or a broken
                ``TESSDATA_PREFIX``). Every image would fail, so the batch fails.
        """
        try:
            requested = self._get_language_code()
            command = str(self.pytesseract.pytesseract.tesseract_cmd)
        except (ValueError, ImportError) as exc:
            raise OCREngineError(str(exc)) from exc
        key = (requested, os.environ.get("TESSDATA_PREFIX"), command)
        if self._engine_checked_for == key and self._engine_language:
            return self._engine_language
        directory, installed = self._installed_languages()
        by_name = {code.lower(): code for code in installed}
        resolved, missing = [], []
        for code in requested.split('+'):
            if code in installed:
                resolved.append(code)
            elif code.lower() in by_name:
                resolved.append(by_name[code.lower()])
            else:
                missing.append(code)
        if missing:
            raise OCREngineError(self._engine_hint(
                f"Tesseract language data not found for {', '.join(repr(m) for m in missing)} "
                f"(tessdata directory: {directory or 'unknown'}; installed: {', '.join(installed) or 'none'}; "
                f"long names accepted: {', '.join(sorted(LANGUAGE_ALIASES))})"))
        self._engine_checked_for, self._engine_language = key, '+'.join(resolved)
        return self._engine_language

    def _build_tesseract_config(self, **kwargs) -> str:
        """Build Tesseract configuration string.
        
        Args:
            **kwargs: Additional options
            
        Returns:
            Configuration string
        """
        logger.debug("⚙️  Building Tesseract configuration...")
        config_parts = []

        # Page segmentation mode
        if self.config.detect_layout:
            config_parts.append('--psm 3')  # Fully automatic page segmentation
            logger.debug("   PSM 3: Fully automatic page segmentation")
        else:
            config_parts.append('--psm 6')  # Uniform block of text
            logger.debug("   PSM 6: Uniform block of text")

        # OCR Engine Mode
        config_parts.append('--oem 3')  # Default, based on what is available
        logger.debug("   OEM 3: Default OCR engine mode")

        # Additional custom config
        if 'tesseract_config' in kwargs:
            custom_config = kwargs['tesseract_config']
            config_parts.append(custom_config)
            logger.debug(f"   Custom config: {custom_config}")

        final_config = ' '.join(config_parts)
        logger.debug(f"✓ Final configuration: {final_config}")
        return final_config

    @property
    def requires_api_key(self) -> bool:
        """Tesseract doesn't require an API key."""
        return False


# Register the provider
logger.debug("🔌 Registering Tesseract OCR provider")
OCRFactory.register_provider(OCRProvider.TESSERACT, TesseractOCR)
