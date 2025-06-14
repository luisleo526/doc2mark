"""Base OCR interface for doc2mark."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class OCRProvider(Enum):
    """Available OCR providers."""
    OPENAI = "openai"
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
