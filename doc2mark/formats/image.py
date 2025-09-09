"""Image processor for standalone image files."""

import base64
import io
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from PIL import Image

from doc2mark.core.base import (
    BaseProcessor,
    DocumentFormat,
    DocumentMetadata,
    ProcessedDocument,
    ProcessingError
)
from doc2mark.ocr.base import BaseOCR

logger = logging.getLogger(__name__)


class ImageProcessor(BaseProcessor):
    """Processor for image files (PNG, JPG, JPEG, WEBP)."""
    
    def __init__(self, ocr: Optional[BaseOCR] = None):
        """Initialize image processor with optional OCR support.
        
        Args:
            ocr: OCR instance for text extraction from images
        """
        self.ocr = ocr
        logger.info("Initialized ImageProcessor")
    
    def can_process(self, file_path: Union[str, Path]) -> bool:
        """Check if this processor can handle the given file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if the file is a supported image format
        """
        file_path = Path(file_path)
        extension = file_path.suffix.lower().lstrip('.')
        
        # List of supported image extensions
        supported_extensions = {'png', 'jpg', 'jpeg', 'webp'}
        
        return extension in supported_extensions
    
    def process(
        self,
        file_path: Union[str, Path],
        extract_images: bool = True,
        ocr_images: bool = False,
        **kwargs
    ) -> ProcessedDocument:
        """Process an image file.
        
        Args:
            file_path: Path to the image file
            extract_images: Whether to include the image as base64 (default: True)
            ocr_images: Whether to perform OCR on the image (default: False)
            **kwargs: Additional processing options
            
        Returns:
            ProcessedDocument with image content and/or OCR text
            
        Raises:
            ProcessingError: If image processing fails
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"Image file not found: {file_path}")
        
        try:
            # Get file metadata
            file_size = file_path.stat().st_size
            
            # Load image using PIL
            with Image.open(file_path) as img:
                width, height = img.size
                mode = img.mode
                format_name = img.format or file_path.suffix.upper().lstrip('.')
                
                # Convert image to RGB if necessary for better OCR results
                if ocr_images and mode not in ['RGB', 'L']:
                    img = img.convert('RGB')
                
                # Prepare content
                content_parts = []
                images_list = []
                ocr_text = None
                
                # Add image metadata as markdown
                content_parts.append(f"# Image: {file_path.name}\n")
                content_parts.append(f"- **Format**: {format_name}")
                content_parts.append(f"- **Dimensions**: {width} x {height} pixels")
                content_parts.append(f"- **Mode**: {mode}")
                content_parts.append(f"- **Size**: {file_size:,} bytes\n")
                
                # Log OCR request status
                logger.debug(f"OCR requested: {ocr_images}, OCR available: {self.ocr is not None}")
                
                # Perform OCR if requested
                if ocr_images:
                    if self.ocr:
                        logger.info(f"🔍 Performing OCR on image: {file_path.name}")
                        try:
                            # Read image bytes
                            image_bytes = file_path.read_bytes()
                            
                            # Log OCR request
                            logger.debug(f"OCR Provider: {type(self.ocr).__name__}")
                            logger.debug(f"Image size: {len(image_bytes)} bytes")
                            
                            # Use batch processing with single image (OpenAI OCR requires this)
                            ocr_results = self.ocr.batch_process_images([image_bytes])
                            ocr_result = ocr_results[0] if ocr_results else None
                            
                            # Extract text from result
                            if ocr_result and hasattr(ocr_result, 'text'):
                                ocr_text = ocr_result.text
                            elif ocr_result:
                                ocr_text = str(ocr_result)
                            else:
                                ocr_text = None
                            
                            logger.info(f"✓ OCR completed, extracted {len(ocr_text) if ocr_text else 0} characters")
                            
                            if ocr_text and ocr_text.strip():
                                content_parts.append("## OCR Extracted Text\n")
                                content_parts.append(ocr_text.strip())
                                content_parts.append("")
                            else:
                                content_parts.append("## OCR Extraction\n")
                                content_parts.append("*No text detected in image*\n")
                                ocr_text = ""  # Set to empty string to indicate OCR was performed
                        except Exception as e:
                            logger.error(f"❌ OCR failed for {file_path.name}: {e}")
                            content_parts.append("## OCR Extraction\n")
                            content_parts.append(f"*OCR extraction failed: {str(e)}*\n")
                            ocr_text = None  # Keep as None to indicate OCR failed
                    else:
                        logger.warning(f"⚠️ OCR requested but no OCR provider available")
                        content_parts.append("## OCR Extraction\n")
                        content_parts.append("*OCR requested but no OCR provider configured*\n")
                        ocr_text = None
                
                # Extract image as base64 if requested
                # Note: We store the base64 data in images_list for internal use,
                # but don't include it in the markdown content to keep files smaller
                if extract_images:
                    try:
                        # Convert to PNG format for consistency
                        buffer = io.BytesIO()
                        
                        # Handle different image modes
                        if mode == 'RGBA' or mode == 'LA':
                            # Keep transparency
                            img.save(buffer, format='PNG')
                        else:
                            # Convert to RGB for better compatibility
                            if mode != 'RGB':
                                img = img.convert('RGB')
                            img.save(buffer, format='PNG')
                        
                        image_bytes = buffer.getvalue()
                        base64_data = base64.b64encode(image_bytes).decode('utf-8')
                        
                        # Add to images list (for internal use/saving separately)
                        images_list.append({
                            'type': 'image',
                            'content': base64_data,
                            'format': 'png',
                            'width': width,
                            'height': height,
                            'original_format': format_name,
                            'filename': file_path.name
                        })
                        
                        logger.debug(f"✓ Image extracted as base64 ({len(base64_data)} characters)")
                    except Exception as e:
                        logger.warning(f"Failed to extract image as base64: {e}")
            
            # Determine document format
            extension = file_path.suffix.lower().lstrip('.')
            try:
                doc_format = DocumentFormat(extension)
            except ValueError:
                # Handle special cases like jpeg -> jpg
                if extension == 'jpeg':
                    doc_format = DocumentFormat.JPG
                else:
                    doc_format = DocumentFormat.PNG  # Default fallback
            
            # Create metadata
            metadata = DocumentMetadata(
                filename=file_path.name,
                format=doc_format,
                size_bytes=file_size,
                page_count=1,  # Images are single page
                image_count=1,
                extra={
                    'width': width,
                    'height': height,
                    'mode': mode,
                    'format': format_name,
                    'has_ocr': ocr_text is not None  # True if OCR was attempted (even if no text found)
                }
            )
            
            # If OCR extracted text, add word count
            if ocr_text:
                word_count = len(ocr_text.split())
                metadata.word_count = word_count
            
            # Combine content
            content = '\n'.join(content_parts)
            
            # Build JSON content similar to PDF processor
            json_content = [
                {'type': 'text:title', 'content': f'Image: {file_path.name}'},
                {'type': 'text:normal', 'content': f'Format: {format_name}, Dimensions: {width}x{height}, Size: {file_size:,} bytes'}
            ]
            
            # Add OCR result if available (similar to PDF processor)
            if ocr_text is not None and ocr_text != "":
                if ocr_text.strip():
                    json_content.append({
                        'type': 'text:image_description',
                        'content': f'<image_ocr_result>{ocr_text}</image_ocr_result>'
                    })
                else:
                    json_content.append({
                        'type': 'text:image_description',
                        'content': '<image_ocr_result>No text detected in image</image_ocr_result>'
                    })
            
            # Add base64 image if extracted
            if images_list:
                for img in images_list:
                    json_content.append({
                        'type': 'image',
                        'content': img['content']
                    })
            
            return ProcessedDocument(
                content=content,
                metadata=metadata,
                images=images_list if images_list else None,
                json_content=json_content
            )
            
        except Image.UnidentifiedImageError as e:
            raise ProcessingError(f"Not a valid image file: {file_path.name}")
        except Exception as e:
            logger.error(f"Failed to process image {file_path}: {e}")
            raise ProcessingError(f"Image processing failed: {str(e)}")
