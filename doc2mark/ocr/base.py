"""Base OCR interface for doc2mark."""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Union


def resolve_max_concurrency(config_value: Optional[int] = None) -> Optional[int]:
    """Resolve the LLM-OCR batch concurrency cap.

    Precedence: explicit ``config_value`` > ``OCR_MAX_CONCURRENCY`` env var > ``None``.
    ``None`` means "use the LangChain default" (a CPU-tied thread pool, typically ~12),
    preserving the pre-0.5.2 behaviour. A positive int caps how many image OCR calls run
    concurrently in ``batch_as_completed`` — raise it to keep large scanned documents
    within an SLA (e.g. 32 ≈ a few-thousand-page doc in minutes).
    """
    if config_value is not None:
        return config_value
    env = os.getenv("OCR_MAX_CONCURRENCY")
    if env:
        try:
            value = int(env)
            return value if value > 0 else None
        except ValueError:
            return None
    return None


class OCRProvider(Enum):
    """Available OCR providers."""
    OPENAI = "openai"
    VERTEX_AI = "vertex_ai"
    TESSERACT = "tesseract"


@dataclass
class OCRResult:
    """Result from OCR processing."""
    text: str
    confidence: Optional[float] = None
    language: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class OCRConfig:
    """Configuration for OCR processing."""
    language: Optional[str] = None
    enhance_image: bool = True
    detect_tables: bool = True
    detect_layout: bool = True
    max_retries: int = 3
    timeout: int = 30
    extra: Optional[Dict[str, Any]] = None
    # Max concurrent image OCR calls for LLM providers (vertex_ai/openai) in
    # batch_as_completed. None = LangChain default (~CPU-tied). Falls back to the
    # OCR_MAX_CONCURRENCY env var when None. Raise for large scanned docs.
    max_concurrency: Optional[int] = None


class BaseOCR(ABC):
    """Abstract base class for OCR providers."""

    def __init__(self, api_key: Optional[str] = None, config: Optional[OCRConfig] = None):
        """Initialize OCR provider.
        
        Args:
            api_key: API key for the provider (if required)
            config: OCR configuration options
        """
        self.api_key = api_key
        self.config = config or OCRConfig()

    @abstractmethod
    def batch_process_images(self, images: List[bytes], **kwargs) -> List[OCRResult]:
        """Process multiple images in batch using LangChain.
        
        This is the primary method for OCR processing. All implementations
        must use LangChain for efficient batch processing.
        
        Args:
            images: List of image data as bytes
            **kwargs: Additional provider-specific options
            
        Returns:
            List of OCRResult objects in the same order as input
        """
        pass

    def validate_api_key(self) -> bool:
        """Validate that the API key is set if required.
        
        Returns:
            True if valid, False otherwise
        """
        # Base implementation - providers can override
        return True

    def preprocess_image(self, image_data: bytes) -> bytes:
        """Preprocess image before OCR (optional).
        
        Args:
            image_data: Raw image data
            
        Returns:
            Preprocessed image data
        """
        # Base implementation - no preprocessing
        return image_data

    @property
    def provider_name(self) -> str:
        """Get the provider name."""
        return self.__class__.__name__.replace('OCR', '')

    @property
    def requires_api_key(self) -> bool:
        """Check if this provider requires an API key."""
        # Override in subclasses
        return True


class OCRFactory:
    """Factory for creating OCR providers."""

    _providers: Dict[OCRProvider, type] = {}

    @classmethod
    def register_provider(cls, provider: OCRProvider, provider_class: type):
        """Register an OCR provider.
        
        Args:
            provider: Provider enum value
            provider_class: Provider class type
        """
        cls._providers[provider] = provider_class

    @classmethod
    def create(
            cls,
            provider: Union[OCRProvider, str],
            api_key: Optional[str] = None,
            config: Optional[OCRConfig] = None
    ) -> BaseOCR:
        """Create an OCR provider instance.
        
        Args:
            provider: Provider type or string name
            api_key: API key for the provider
            config: OCR configuration
            
        Returns:
            OCR provider instance
            
        Raises:
            ValueError: If provider is not registered
        """
        if isinstance(provider, str):
            try:
                provider = OCRProvider(provider.lower())
            except ValueError:
                raise ValueError(f"Unknown OCR provider: {provider}")

        if provider not in cls._providers:
            raise ValueError(f"OCR provider {provider.value} is not registered")

        provider_class = cls._providers[provider]
        return provider_class(api_key=api_key, config=config)

    @classmethod
    def list_providers(cls) -> List[str]:
        """List available OCR providers.
        
        Returns:
            List of provider names
        """
        return [p.value for p in cls._providers.keys()]
