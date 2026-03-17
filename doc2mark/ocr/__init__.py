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

    # Factory
    'OCRFactory',

    # Providers
    'OpenAIOCR',
    'TesseractOCR',
    'VertexAIOCR',

    # Vision agents
    'VisionAgent',
    'VertexAIVisionAgent',
]
