"""OCR providers for doc2mark."""

from doc2mark.ocr.base import (
    OCRProvider,
    OCRResult,
    OCRConfig,
    BaseOCR,
    OCRFactory
)

# Import and register providers
from doc2mark.ocr.openai import OpenAIOCR, VisionAgent
from doc2mark.ocr.tesseract import TesseractOCR

__all__ = [
    # Enums
    'OCRProvider',

    # Data classes
    'OCRResult',
    'OCRConfig',

    # Base classes
    'BaseOCR',

    # Factory
    'OCRFactory',

    # Providers
    'OpenAIOCR',
    'TesseractOCR',

    # Vision agents
    'VisionAgent',
]
