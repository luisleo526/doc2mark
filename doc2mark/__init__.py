"""doc2mark - AI-powered universal document processor.

A Python package that unifies document processing across multiple formats
with advanced AI-powered OCR capabilities.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

__version__ = "0.5.2"
__author__ = "Hao Liang Wen"
__email__ = "luisleo52655@gmail.com"

logging.getLogger(__name__).addHandler(logging.NullHandler())

from doc2mark.core.base import (
    DocumentFormat,
    OutputFormat,
    ProcessedDocument,
    DocumentMetadata,
    ProcessingError,
    UnsupportedFormatError,
    OCRError,
    ConversionError
)
# Main imports
from doc2mark.core.loader import UnifiedDocumentLoader
from doc2mark.ocr.base import OCRProvider, OCRConfig, OCRFactory
from doc2mark.ocr.cache import (
    OCRCache,
    MemoryOCRCache,
    NoOpOCRCache,
    RedisOCRCache,
    create_ocr_cache,
)
from doc2mark.core.table import TableStyle
from doc2mark.core.chunker import Chunk, ChunkingConfig, chunk_content

__all__ = [
    # Main class
    'UnifiedDocumentLoader',

    # Enums
    'DocumentFormat',
    'OutputFormat',
    'OCRProvider',
    'TableStyle',

    # Data classes
    'ProcessedDocument',
    'DocumentMetadata',
    'OCRConfig',
    'OCRCache',

    # Exceptions
    'ProcessingError',
    'UnsupportedFormatError',
    'OCRError',
    'ConversionError',

    # Factory
    'OCRFactory',

    # OCR cache
    'MemoryOCRCache',
    'NoOpOCRCache',
    'RedisOCRCache',
    'create_ocr_cache',

    # Chunking
    'Chunk',
    'ChunkingConfig',
    'chunk_content',

    # Convenience functions
    'load',
    'document_to_markdown',
    'batch_convert_to_markdown',
    'batch_process_documents',
]


# Convenience functions
def load(
        file_path: Union[str, Path],
        output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
        extract_images: bool = False,
        ocr_images: bool = False,
        ocr_provider: Union[str, OCRProvider] = 'openai',
        api_key: Optional[str] = None,
        ocr_cache: Optional[OCRCache] = None,
        **kwargs: Any
) -> ProcessedDocument:
    """
    Quick load function for single documents.
    
    Args:
        file_path: Path to the document
        output_format: Output format (default: markdown)
        extract_images: Whether to extract images from documents
            - True: Extract images as base64 data
            - False: Skip image extraction entirely
        ocr_images: Whether to perform OCR on extracted images (requires extract_images=True)
            - True: Use batch OCR processing to convert images to text descriptions
            - False: Keep images as base64 data in output
        ocr_provider: OCR provider to use
        api_key: API key for OCR provider
        ocr_cache: Optional request-scoped OCR cache handler
        **kwargs: Additional options
        
    Returns:
        ProcessedDocument
        
    Examples:
        # Basic text extraction only
        load("document.pdf")
        
        # Extract images as base64 (no OCR)
        load("document.pdf", extract_images=True, ocr_images=False)
        
        # Extract images and perform OCR
        load("document.pdf", extract_images=True, ocr_images=True)
    """
    loader = UnifiedDocumentLoader(
        ocr_provider=ocr_provider,
        api_key=api_key,
        ocr_cache=ocr_cache
    )
    return loader.load(
        file_path,
        output_format=output_format,
        extract_images=extract_images,
        ocr_images=ocr_images,
        **kwargs
    )


def document_to_markdown(
        file_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
        extract_images: bool = False,
        ocr_images: bool = False,
        ocr_provider: Union[str, OCRProvider] = 'openai',
        api_key: Optional[str] = None,
        ocr_cache: Optional[OCRCache] = None,
        show_progress: bool = True,
        **kwargs: Any
) -> str:
    """
    Convert any supported document to Markdown (backward compatibility function).
    
    Args:
        file_path: Path to the document
        output_path: Optional path to save the markdown file
        extract_images: Whether to extract images from documents
            - True: Extract images as base64 data
            - False: Skip image extraction entirely
        ocr_images: Whether to perform OCR on extracted images (requires extract_images=True)
            - True: Use batch OCR processing to convert images to text descriptions
            - False: Keep images as base64 data in output
        ocr_provider: OCR provider to use
        api_key: API key for OCR provider
        ocr_cache: Optional request-scoped OCR cache handler
        show_progress: Whether to show progress messages
        **kwargs: Additional options
        
    Returns:
        Markdown string
    """
    loader = UnifiedDocumentLoader(
        ocr_provider=ocr_provider,
        api_key=api_key,
        ocr_cache=ocr_cache
    )

    # Process document
    result = loader.load(
        file_path=file_path,
        output_format=OutputFormat.MARKDOWN,
        extract_images=extract_images,
        ocr_images=ocr_images,
        **kwargs
    )

    # Save if output path provided
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(result.content)
        if show_progress:
            print(f"Markdown saved to: {output_path}")

    return result.content


def batch_convert_to_markdown(
        input_dir: Union[str, Path],
        output_dir: Optional[Union[str, Path]] = None,
        extract_images: bool = False,
        ocr_images: bool = False,
        recursive: bool = True,
        ocr_provider: Union[str, OCRProvider] = 'openai',
        api_key: Optional[str] = None,
        ocr_cache: Optional[OCRCache] = None,
        show_progress: bool = True,
        **kwargs: Any
) -> Dict[str, Dict[str, Any]]:
    """
    Batch convert documents to Markdown (backward compatibility function).
    
    Args:
        input_dir: Directory containing documents
        output_dir: Optional output directory
        extract_images: Whether to extract images from documents
            - True: Extract images as base64 data
            - False: Skip image extraction entirely
        ocr_images: Whether to perform OCR on extracted images (requires extract_images=True)
            - True: Use batch OCR processing to convert images to text descriptions
            - False: Keep images as base64 data in output
        recursive: Whether to process subdirectories
        ocr_provider: OCR provider to use
        api_key: API key for OCR provider
        ocr_cache: Optional request-scoped OCR cache handler
        show_progress: Whether to show progress messages
        **kwargs: Additional options
        
    Returns:
        Dictionary mapping input paths to results
    """
    loader = UnifiedDocumentLoader(
        ocr_provider=ocr_provider,
        api_key=api_key,
        ocr_cache=ocr_cache
    )

    return loader.batch_process(
        input_dir=input_dir,
        output_dir=output_dir,
        output_format=OutputFormat.MARKDOWN,
        extract_images=extract_images,
        ocr_images=ocr_images,
        recursive=recursive,
        show_progress=show_progress,
        save_files=True,
        **kwargs
    )


def batch_process_documents(
        input_dir: Union[str, Path],
        output_dir: Optional[Union[str, Path]] = None,
        output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
        extract_images: bool = False,
        ocr_images: bool = False,
        recursive: bool = True,
        ocr_provider: Union[str, OCRProvider] = 'openai',
        api_key: Optional[str] = None,
        ocr_cache: Optional[OCRCache] = None,
        show_progress: bool = True,
        save_files: bool = True,
        **kwargs: Any
) -> Dict[str, Dict[str, Any]]:
    """
    Advanced batch processing with full configuration options.
    
    Args:
        input_dir: Directory containing documents
        output_dir: Optional output directory
        output_format: Output format
        extract_images: Whether to extract images from documents
            - True: Extract images as base64 data
            - False: Skip image extraction entirely
        ocr_images: Whether to perform OCR on extracted images (requires extract_images=True)
            - True: Use batch OCR processing to convert images to text descriptions
            - False: Keep images as base64 data in output
        recursive: Whether to process subdirectories
        ocr_provider: OCR provider to use
        api_key: API key for OCR provider
        ocr_cache: Optional request-scoped OCR cache handler
        show_progress: Whether to show progress messages
        save_files: Whether to save output files
        **kwargs: Additional options
        
    Returns:
        Dictionary mapping input paths to results with detailed metadata
    """
    loader = UnifiedDocumentLoader(
        ocr_provider=ocr_provider,
        api_key=api_key,
        ocr_cache=ocr_cache
    )

    return loader.batch_process(
        input_dir=input_dir,
        output_dir=output_dir,
        output_format=output_format,
        extract_images=extract_images,
        ocr_images=ocr_images,
        recursive=recursive,
        show_progress=show_progress,
        save_files=save_files,
        **kwargs
    )


def batch_process_files(
        file_paths: List[Union[str, Path]],
        output_dir: Optional[Union[str, Path]] = None,
        output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
        extract_images: bool = False,
        ocr_images: bool = False,
        ocr_provider: Union[str, OCRProvider] = 'openai',
        api_key: Optional[str] = None,
        ocr_cache: Optional[OCRCache] = None,
        show_progress: bool = True,
        save_files: bool = True,
        **kwargs: Any
) -> Dict[str, Dict[str, Any]]:
    """
    Batch process a specific list of files.
    
    Args:
        file_paths: List of file paths to process
        output_dir: Optional output directory
        output_format: Output format
        extract_images: Whether to extract images from documents
            - True: Extract images as base64 data
            - False: Skip image extraction entirely
        ocr_images: Whether to perform OCR on extracted images (requires extract_images=True)
            - True: Use batch OCR processing to convert images to text descriptions
            - False: Keep images as base64 data in output
        ocr_provider: OCR provider to use
        api_key: API key for OCR provider
        ocr_cache: Optional request-scoped OCR cache handler
        show_progress: Whether to show progress messages
        save_files: Whether to save output files
        **kwargs: Additional options
        
    Returns:
        Dictionary mapping input paths to results
        
    Examples:
        # Basic text extraction only
        batch_process_files(["doc1.pdf", "doc2.docx"])
        
        # Extract images as base64 (no OCR)
        batch_process_files(files, extract_images=True, ocr_images=False)
        
        # Extract images and perform batch OCR
        batch_process_files(files, extract_images=True, ocr_images=True)
    """
    loader = UnifiedDocumentLoader(
        ocr_provider=ocr_provider,
        api_key=api_key,
        ocr_cache=ocr_cache
    )

    return loader.batch_process_files(
        file_paths=file_paths,
        output_dir=output_dir,
        output_format=output_format,
        extract_images=extract_images,
        ocr_images=ocr_images,
        show_progress=show_progress,
        save_files=save_files,
        **kwargs
    )
