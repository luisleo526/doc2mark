"""Tesseract OCR implementation for fallback support."""

import io
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    # Try to import LangChain components for Tesseract (if a LangChain wrapper exists)
    from langchain_community.document_loaders import PyTesseractLoader

    LANGCHAIN_TESSERACT_AVAILABLE = True
except ImportError:
    LANGCHAIN_TESSERACT_AVAILABLE = False
from typing import List, Optional

from doc2mark.core.base import OCRError
from doc2mark.ocr.base import BaseOCR, OCRConfig, OCRProvider, OCRResult, OCRFactory

logger = logging.getLogger(__name__)


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

    def _process_single_image(self, image_data: bytes, **kwargs) -> OCRResult:
        """Internal method to process a single image using Tesseract.
        
        Args:
            image_data: Image data as bytes
            **kwargs: Additional options
            
        Returns:
            OCRResult with extracted text
        """
        image_size = len(image_data)
        logger.debug(f"🖼️  Processing image with Tesseract ({image_size} bytes)")

        try:
            logger.debug("🔄 Converting bytes to PIL Image...")
            # Convert bytes to PIL Image
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
            language_code = self._get_language_code()
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

            return OCRResult(
                text=text,
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
                }
            )

        except Exception as e:
            logger.error(f"❌ Tesseract OCR failed: {e}")
            logger.error(f"   Image size: {image_size} bytes")
            logger.error(f"   Language: {self._get_language_code()}")
            raise OCRError(f"Failed to process image with Tesseract: {str(e)}")

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

        # For small batches, use sequential processing
        if total_images <= 2:
            logger.debug("Using sequential processing for small batch")
            return [self._process_single_image(img, **kwargs) for img in images]

        # Prepare image data with indices for proper ordering
        indexed_images = [(i, image_data) for i, image_data in enumerate(images)]
        results = [None] * total_images

        def process_single_image(indexed_data):
            """Process a single image with its index."""
            index, image_data = indexed_data
            try:
                logger.debug(f"🔄 Processing image {index + 1}/{total_images} with Tesseract")
                result = self._process_single_image(image_data, **kwargs)
                return index, result
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

        # Process images concurrently
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                futures = [executor.submit(process_single_image, indexed_data)
                           for indexed_data in indexed_images]

                # Collect results as they complete
                completed_count = 0
                for future in as_completed(futures):
                    try:
                        index, result = future.result()
                        results[index] = result
                        completed_count += 1

                        # Log progress every 10% or every 3 images (Tesseract is slower)
                        if completed_count % max(1, total_images // 10) == 0 or completed_count % 3 == 0:
                            progress = completed_count / total_images * 100
                            logger.info(f"📊 Batch progress: {completed_count}/{total_images} ({progress:.1f}%)")

                    except Exception as e:
                        logger.error(f"❌ Future execution failed: {e}")

        except Exception as e:
            logger.error(f"❌ Batch processing failed: {e}")
            # Fallback to sequential processing
            logger.info("🔄 Falling back to sequential processing")
            return self.process_images(images, **kwargs)

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
        """Convert language to Tesseract language code.
        
        Returns:
            Tesseract language code
        """
        if not self.config.language:
            logger.debug("🌐 No language specified, using English")
            return 'eng'

        # Map common language names to Tesseract codes
        language_map = {
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

        lang_lower = self.config.language.lower()
        tesseract_code = language_map.get(lang_lower, 'eng')

        if tesseract_code != 'eng' and lang_lower not in language_map:
            logger.warning(f"⚠️  Language '{self.config.language}' not in mapping, using English")
            tesseract_code = 'eng'
        else:
            logger.debug(f"🌐 Language mapping: '{self.config.language}' -> '{tesseract_code}'")

        return tesseract_code

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
