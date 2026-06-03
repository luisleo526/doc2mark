"""OCR providers for doc2mark."""

from doc2mark.ocr.base import (
    OCRProvider,
    OCRResult,
    OCRConfig,
    BaseOCR,
    OCRFactory
)
from doc2mark.ocr.cache import OCRCache, MemoryOCRCache, NoOpOCRCache, CachedOCR

# Import and register providers
from doc2mark.ocr.openai import OpenAIOCR, VisionAgent
from doc2mark.ocr.tesseract import TesseractOCR

# Vertex AI provider (optional - requires langchain-google-genai)
try:
    from doc2mark.ocr.vertex_ai import VertexAIOCR, VertexAIVisionAgent
except ImportError:
    VertexAIOCR = None
    VertexAIVisionAgent = None

__all__ = [
    # Enums
    'OCRProvider',

    # Data classes
    'OCRResult',
    'OCRConfig',

    # Base classes
    'BaseOCR',
    'OCRCache',

    # Factory
    'OCRFactory',

    # Cache
    'MemoryOCRCache',
    'NoOpOCRCache',
    'CachedOCR',

    # Providers
    'OpenAIOCR',
    'TesseractOCR',
    'VertexAIOCR',

    # Vision agents
    'VisionAgent',
    'VertexAIVisionAgent',
]
