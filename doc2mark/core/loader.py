"""Main UnifiedDocumentLoader implementation."""

import base64
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from doc2mark.core.base import (
    BaseProcessor,
    DocumentMetadata,
    DocumentFormat,
    OutputFormat,
    ProcessedDocument,
    ProcessingError,
    UnsupportedFormatError
)
from doc2mark.ocr.base import BaseOCR, OCRConfig, OCRFactory, OCRProvider
from doc2mark.ocr.cache import CachedOCR, OCRCache
from doc2mark.ocr.prompts import PromptTemplate

logger = logging.getLogger(__name__)


class UnifiedDocumentLoader:
    """Main document loader with unified API for all formats and enhanced OCR configuration."""

    def __init__(
            self,
            ocr_provider: Optional[Union[str, OCRProvider, BaseOCR]] = 'openai',
            api_key: Optional[str] = None,
            ocr_config: Optional[OCRConfig] = None,
            cache_dir: Optional[str] = None,
            ocr_cache: Optional[OCRCache] = None,
            # Enhanced OCR configuration for OpenAI / Vertex AI
            model: str = "gpt-4.1",
            temperature: float = 0,
            max_tokens: int = 4096,
            max_workers: int = 5,
            prompt_template: Union[str, PromptTemplate] = PromptTemplate.DEFAULT,
            timeout: int = 30,
            max_retries: int = 3,
            # Additional OpenAI parameters
            top_p: float = 1.0,
            frequency_penalty: float = 0.0,
            presence_penalty: float = 0.0,
            base_url: Optional[str] = None,
            # Vertex AI parameters
            project: Optional[str] = None,
            location: str = "global",
            # General OCR parameters
            default_prompt: Optional[str] = None,
            # Table output configuration
            table_style: Optional[str] = None
    ):
        """Initialize the document loader with enhanced OCR configuration.

        Args:
            ocr_provider: OCR provider name, enum, or instance
            api_key: API key for OCR provider (OpenAI defaults to OPENAI_API_KEY env var)
            ocr_config: Basic OCR configuration (from base class)
            cache_dir: Directory for caching processed documents
            ocr_cache: Optional request-scoped OCR cache handler

            # Enhanced OpenAI OCR Configuration:
            model: OpenAI model to use (default: gpt-4.1)
            temperature: Temperature for response generation (0.0-2.0)
            max_tokens: Maximum tokens in response (1-4096)
            max_workers: Maximum concurrent workers for batch processing
            prompt_template: Template name (see PromptTemplate enum for the full list, e.g.
                'default', 'table_focused', 'document_focused', 'multilingual',
                'form_focused', 'receipt_focused', 'handwriting_focused', 'code_focused')
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries for failed requests

            # Additional OpenAI parameters:
            top_p: Nucleus sampling parameter (0.0-1.0)
            frequency_penalty: Reduce word repetition (-2.0 to 2.0)
            presence_penalty: Encourage new topics (-2.0 to 2.0)
            base_url: Optional base URL for OpenAI-compatible API endpoints

            # Vertex AI parameters:
            project: Google Cloud project ID (defaults to GOOGLE_CLOUD_PROJECT env var)
            location: Google Cloud region (default: global)

            # General OCR parameters:
            default_prompt: Custom default prompt to override built-in prompts

            # Table output configuration:
            table_style: Output style for complex tables with merged cells:
                - 'minimal_html': Clean HTML with only rowspan/colspan (default)
                - 'markdown_grid': Markdown with merge annotations
                - 'styled_html': Full HTML with inline styles (legacy)
        """
        logger.info("🚀 Initializing UnifiedDocumentLoader with enhanced OCR configuration")

        self.ocr_cache = None
        self.ocr = self._create_ocr_provider(
            ocr_provider=ocr_provider,
            api_key=api_key,
            ocr_config=ocr_config,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            max_workers=max_workers,
            prompt_template=prompt_template,
            timeout=timeout,
            max_retries=max_retries,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            base_url=base_url,
            project=project,
            location=location,
            default_prompt=default_prompt,
        )
        self._apply_ocr_cache(ocr_cache)

        # Cache directory
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"📁 Cache directory: {self.cache_dir}")

        # Table output style (default: minimal_html for cleaner output)
        self.table_style = table_style if table_style else "minimal_html"
        logger.info(f"📊 Table style: {self.table_style}")

        # Registry of format processors
        self._processors: Dict[DocumentFormat, BaseProcessor] = {}
        self._initialize_processors()

        logger.info("✅ UnifiedDocumentLoader initialized successfully")

    @staticmethod
    def _is_ocr_provider(provider: Union[str, OCRProvider], target: OCRProvider) -> bool:
        if isinstance(provider, OCRProvider):
            return provider == target
        if isinstance(provider, str):
            return provider.lower() == target.value
        return False

    @staticmethod
    def _unwrap_ocr(ocr: Optional[BaseOCR]) -> Optional[BaseOCR]:
        if ocr is None:
            return None
        return ocr.wrapped if isinstance(ocr, CachedOCR) else ocr

    def _create_ocr_provider(
            self,
            ocr_provider: Optional[Union[str, OCRProvider, BaseOCR]],
            api_key: Optional[str] = None,
            ocr_config: Optional[OCRConfig] = None,
            model: str = "gpt-4.1",
            temperature: float = 0,
            max_tokens: int = 4096,
            max_workers: int = 5,
            prompt_template: Union[str, PromptTemplate] = PromptTemplate.DEFAULT,
            timeout: int = 30,
            max_retries: int = 3,
            top_p: float = 1.0,
            frequency_penalty: float = 0.0,
            presence_penalty: float = 0.0,
            base_url: Optional[str] = None,
            project: Optional[str] = None,
            location: str = "global",
            default_prompt: Optional[str] = None,
            model_kwargs: Optional[Dict[str, Any]] = None,
    ) -> Optional[BaseOCR]:
        """Create an OCR provider using the same enhanced path everywhere."""
        if ocr_provider is None or (isinstance(ocr_provider, str) and ocr_provider.lower() in {"none", "disabled"}):
            logger.info("OCR provider disabled")
            return None

        if isinstance(ocr_provider, BaseOCR):
            logger.info(f"✓ Using provided OCR instance: {type(ocr_provider).__name__}")
            return ocr_provider

        logger.info(f"🤖 Initializing OCR provider: {ocr_provider}")
        extra_model_kwargs = dict(model_kwargs or {})

        if self._is_ocr_provider(ocr_provider, OCRProvider.OPENAI):
            logger.info("🔧 Using enhanced OpenAI OCR configuration")
            from doc2mark.ocr.openai import OpenAIOCR

            extra_model_kwargs.setdefault("top_p", top_p)
            extra_model_kwargs.setdefault("frequency_penalty", frequency_penalty)
            extra_model_kwargs.setdefault("presence_penalty", presence_penalty)

            ocr = OpenAIOCR(
                api_key=api_key,
                config=ocr_config,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                max_workers=max_workers,
                prompt_template=prompt_template,
                timeout=timeout,
                max_retries=max_retries,
                default_prompt=default_prompt,
                base_url=base_url,
                **extra_model_kwargs,
            )
            self._log_ocr_configuration(ocr, title="📋 OCR Configuration Summary:")
            return ocr

        if self._is_ocr_provider(ocr_provider, OCRProvider.VERTEX_AI):
            logger.info("Using enhanced Vertex AI OCR configuration")
            from doc2mark.ocr.vertex_ai import VertexAIOCR

            vertex_model = model if model != "gpt-4.1" else "gemini-3.1-flash-lite-preview"
            ocr = VertexAIOCR(
                api_key=api_key,
                config=ocr_config,
                project=project,
                location=location,
                model=vertex_model,
                temperature=temperature,
                max_tokens=max_tokens,
                prompt_template=prompt_template,
                default_prompt=default_prompt,
                **extra_model_kwargs,
            )
            self._log_ocr_configuration(ocr, title="OCR Configuration Summary:")
            return ocr

        logger.info(f"Using standard OCR factory for provider: {ocr_provider}")
        return OCRFactory.create(
            provider=ocr_provider,
            api_key=api_key,
            config=ocr_config
        )

    @staticmethod
    def _log_ocr_configuration(ocr: BaseOCR, title: str):
        if hasattr(ocr, 'get_configuration_summary'):
            config_summary = ocr.get_configuration_summary()
            logger.info(title)
            for key, value in config_summary.items():
                logger.info(f"   {key}: {value}")

    def _apply_ocr_cache(self, ocr_cache: Optional[OCRCache] = None):
        """Apply an explicit cache, preserve embedded cache, or reuse loader cache."""
        if self.ocr is None:
            if ocr_cache is not None:
                self.ocr_cache = ocr_cache
                logger.info(f"OCR cache configured for later use: {type(self.ocr_cache).__name__}")
            return

        if ocr_cache is not None:
            self.ocr_cache = ocr_cache
            if isinstance(self.ocr, CachedOCR):
                self.ocr.cache = ocr_cache
            else:
                self.ocr = CachedOCR(self.ocr, ocr_cache)
            logger.info(f"🧠 OCR cache enabled: {type(self.ocr_cache).__name__}")
            return

        if isinstance(self.ocr, CachedOCR):
            self.ocr_cache = self.ocr.cache
            logger.info(f"🧠 OCR cache enabled: {type(self.ocr_cache).__name__}")
            return

        if self.ocr_cache is not None:
            self.ocr = CachedOCR(self.ocr, self.ocr_cache)
            logger.info(f"🧠 OCR cache enabled: {type(self.ocr_cache).__name__}")

    def _current_ocr_constructor_options(self) -> Dict[str, Any]:
        """Read current provider settings so set_ocr_provider does not downgrade OCR config."""
        current = self._unwrap_ocr(self.ocr)
        if current is None:
            return {
                "api_key": None,
                "ocr_config": None,
                "model": "gpt-4.1",
                "temperature": 0,
                "max_tokens": 4096,
                "max_workers": 5,
                "prompt_template": PromptTemplate.DEFAULT,
                "timeout": 30,
                "max_retries": 3,
                "base_url": None,
                "project": None,
                "location": "global",
                "default_prompt": None,
                "model_kwargs": {},
            }
        return {
            "api_key": getattr(current, "api_key", None),
            "ocr_config": getattr(current, "config", None),
            "model": getattr(current, "model", "gpt-4.1"),
            "temperature": getattr(current, "temperature", 0),
            "max_tokens": getattr(current, "max_tokens", 4096),
            "max_workers": getattr(current, "max_workers", 5),
            "prompt_template": getattr(current, "prompt_template", PromptTemplate.DEFAULT),
            "timeout": getattr(current, "timeout", 30),
            "max_retries": getattr(current, "max_retries", 3),
            "base_url": getattr(current, "base_url", None),
            "project": getattr(current, "project", None),
            "location": getattr(current, "location", "global"),
            "default_prompt": getattr(current, "default_prompt", None),
            "model_kwargs": dict(getattr(current, "model_kwargs", {}) or {}),
        }

    def _initialize_processors(self):
        """Initialize all format processors."""
        # Import processors lazily to avoid circular imports
        try:
            # Import all processors
            from doc2mark.formats.office import OfficeProcessor
            from doc2mark.formats.pdf import PDFProcessor
            from doc2mark.formats.text import TextProcessor
            from doc2mark.formats.markup import MarkupProcessor
            from doc2mark.formats.legacy import LegacyProcessor
            from doc2mark.formats.image import ImageProcessor

            # Initialize processors with OCR support
            office_processor = OfficeProcessor(ocr=self.ocr, table_style=self.table_style)
            pdf_processor = PDFProcessor(ocr=self.ocr, table_style=self.table_style)
            text_processor = TextProcessor()
            markup_processor = MarkupProcessor()
            legacy_processor = LegacyProcessor(ocr=self.ocr)
            image_processor = ImageProcessor(ocr=self.ocr)

            # Register processors for each format
            # Office formats - use our new OfficeProcessor
            self._processors[DocumentFormat.DOCX] = office_processor
            self._processors[DocumentFormat.XLSX] = office_processor
            self._processors[DocumentFormat.PPTX] = office_processor

            # PDF
            self._processors[DocumentFormat.PDF] = pdf_processor

            # Text/Data formats
            for fmt in [DocumentFormat.TXT, DocumentFormat.CSV,
                        DocumentFormat.TSV, DocumentFormat.JSON,
                        DocumentFormat.JSONL]:
                self._processors[fmt] = text_processor

            # Markup formats
            for fmt in [DocumentFormat.HTML, DocumentFormat.XML,
                        DocumentFormat.MARKDOWN]:
                self._processors[fmt] = markup_processor

            # Legacy formats
            for fmt in [DocumentFormat.DOC, DocumentFormat.XLS,
                        DocumentFormat.PPT, DocumentFormat.RTF,
                        DocumentFormat.PPS]:
                self._processors[fmt] = legacy_processor
            
            # Image formats
            for fmt in [DocumentFormat.PNG, DocumentFormat.JPG,
                        DocumentFormat.JPEG, DocumentFormat.WEBP,
                        DocumentFormat.TIFF, DocumentFormat.TIF,
                        DocumentFormat.BMP, DocumentFormat.GIF,
                        DocumentFormat.HEIC, DocumentFormat.HEIF,
                        DocumentFormat.AVIF]:
                self._processors[fmt] = image_processor

            logger.info("Using individual format processors with enhanced image extraction")

            # Try to import UnifiedProcessor for non-Office formats if needed
            try:
                from doc2mark.formats.unified_processor import UnifiedProcessor
                unified_processor = UnifiedProcessor(ocr=self.ocr)
                
                # Only use UnifiedProcessor for formats not handled by our processors
                # This allows backward compatibility while ensuring Office formats use our new code
                logger.info("UnifiedProcessor available for additional format support")
                
            except ImportError:
                logger.info("UnifiedProcessor not available, using individual processors only")

        except ImportError as e:
            logger.error(f"Failed to import required processors: {e}")
            raise ImportError(f"Required format processors not available: {str(e)}") from e

    @staticmethod
    def _normalize_output_format(output_format: Union[str, OutputFormat]) -> OutputFormat:
        """Normalize string output format names to OutputFormat enum values."""
        if isinstance(output_format, OutputFormat):
            return output_format
        if isinstance(output_format, str):
            try:
                return OutputFormat(output_format.lower())
            except ValueError as e:
                valid = ", ".join(fmt.value for fmt in OutputFormat)
                raise ValueError(f"Unsupported output format: {output_format}. Expected one of: {valid}") from e
        raise TypeError(f"Unsupported output format type: {type(output_format).__name__}")

    def load(
            self,
            file_path: Union[str, Path],
            output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
            extract_images: bool = False,
            ocr_images: bool = False,
            show_progress: bool = False,
            # Format-specific parameters
            encoding: str = 'utf-8',
            delimiter: Optional[str] = None
    ) -> ProcessedDocument:
        """Load and process a document.
        
        Args:
            file_path: Path to the document
            output_format: Desired output format (MARKDOWN, JSON, TEXT)
            extract_images: Whether to extract images as base64 (Office/PDF only)
            ocr_images: Whether to perform OCR on extracted images (requires extract_images=True)
            show_progress: Whether to show progress messages during processing
            
            # Format-specific parameters:
            encoding: Text encoding for text/markup files (default: 'utf-8')
            delimiter: Delimiter for CSV files (auto-detect if None)
            
        Returns:
            ProcessedDocument with content and metadata
            
        Raises:
            UnsupportedFormatError: If format is not supported
            ProcessingError: If processing fails
            
        Note:
            - extract_images and ocr_images only work with Office and PDF formats
            - show_progress only works when UnifiedProcessor is available
            - encoding and delimiter only apply to text-based formats
            
            For advanced OCR configuration, use the constructor parameters or
            update_ocr_configuration() method.
        """
        file_path = Path(file_path)
        output_format = self._normalize_output_format(output_format)

        # Validate file exists
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Determine format
        doc_format = self._detect_format(file_path)
        if doc_format not in self._processors:
            raise UnsupportedFormatError(
                f"Unsupported format: {doc_format.value}"
            )

        # Check cache
        if self.cache_dir:
            cache_options = {
                "output_format": output_format.value,
                "extract_images": extract_images,
                "ocr_images": ocr_images,
                "encoding": encoding,
                "delimiter": delimiter,
                "table_style": self.table_style,
                "ocr_provider": type(self._unwrap_ocr(self.ocr)).__name__ if self.ocr else None,
            }
            cached = self._get_cached(file_path, output_format, cache_options)
            if cached:
                logger.info(f"Using cached result for {file_path}")
                return cached
        else:
            cache_options = {}

        # Process document
        processor = self._processors[doc_format]

        try:
            # Check if we're using UnifiedProcessor or fallback processors
            if processor.__class__.__name__ == 'UnifiedProcessor':
                # UnifiedProcessor handles all parameters directly
                result = processor.process(
                    file_path,
                    output_format=output_format,
                    extract_images=extract_images,
                    ocr_images=ocr_images,
                    preserve_layout=True,  # Keep for compatibility
                    show_progress=show_progress,
                    # Format-specific
                    encoding=encoding,
                    delimiter=delimiter
                )
            else:
                # Fallback processors need parameter mapping
                processor_kwargs = {}

                # Map common parameters
                if processor.__class__.__name__ in ['OfficeProcessor', 'LegacyProcessor']:
                    processor_kwargs['extract_images'] = extract_images
                    processor_kwargs['ocr_images'] = ocr_images
                elif processor.__class__.__name__ == 'PDFProcessor':
                    processor_kwargs['extract_images'] = extract_images
                    processor_kwargs['use_ocr'] = ocr_images
                    processor_kwargs['extract_tables'] = True
                elif processor.__class__.__name__ == 'ImageProcessor':
                    processor_kwargs['extract_images'] = extract_images
                    processor_kwargs['ocr_images'] = ocr_images
                elif processor.__class__.__name__ == 'TextProcessor':
                    processor_kwargs['encoding'] = encoding
                    if delimiter:
                        processor_kwargs['delimiter'] = delimiter
                elif processor.__class__.__name__ == 'MarkupProcessor':
                    processor_kwargs['encoding'] = encoding

                # Process with mapped parameters
                result = processor.process(file_path, **processor_kwargs)

                # Apply output format conversion if needed
                if output_format != OutputFormat.MARKDOWN:
                    # Convert content to requested format
                    if output_format == OutputFormat.JSON:
                        # Create JSON structure
                        json_data = result.to_dict()
                        result.content = json.dumps(json_data, indent=2, ensure_ascii=False)
                        if result.json_content is None:
                            result.json_content = [{"type": "text:normal", "content": json_data.get("content", "")}]
                    elif output_format == OutputFormat.TEXT:
                        # Convert to plain text
                        result.content = result.text

            # Cache result
            if self.cache_dir:
                self._cache_result(file_path, output_format, result, cache_options)

            return result

        except Exception as e:
            logger.error(f"Failed to process {file_path}: {e}")
            raise ProcessingError(f"Processing failed: {str(e)}") from e

    def load_directory(
            self,
            directory: Union[str, Path],
            pattern: str = "*",
            recursive: bool = True,
            output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
            **kwargs
    ) -> List[ProcessedDocument]:
        """Load all documents from a directory.
        
        Args:
            directory: Directory path
            pattern: Glob pattern for files
            recursive: Whether to search recursively
            output_format: Desired output format
            **kwargs: Additional processor options
            
        Returns:
            List of processed documents
        """
        directory = Path(directory)
        output_format = self._normalize_output_format(output_format)
        if not directory.is_dir():
            raise ValueError(f"Not a directory: {directory}")

        # Find files
        if recursive:
            files = list(directory.rglob(pattern))
        else:
            files = list(directory.glob(pattern))

        # Process files
        results = []
        for file_path in files:
            if file_path.is_file():
                try:
                    result = self.load(
                        file_path,
                        output_format=output_format,
                        **kwargs
                    )
                    results.append(result)
                except (UnsupportedFormatError, ProcessingError) as e:
                    logger.warning(f"Skipping {file_path}: {e}")
                    continue

        return results

    def batch_process(
            self,
            input_dir: Union[str, Path],
            output_dir: Optional[Union[str, Path]] = None,
            output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
            extract_images: bool = False,
            ocr_images: bool = False,
            recursive: bool = True,
            show_progress: bool = True,
            save_files: bool = True,
            encoding: str = 'utf-8',
            delimiter: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Batch process multiple documents in a directory with full result tracking.
        
        Args:
            input_dir: Directory containing documents
            output_dir: Optional output directory (default: same as input)
            output_format: Output format (MARKDOWN, JSON, TEXT)
            extract_images: Whether to extract images from documents (Office/PDF only)
            ocr_images: Whether to perform OCR on extracted images (requires extract_images=True)
            recursive: Whether to process subdirectories
            show_progress: Whether to show progress messages
            save_files: Whether to save output files
            encoding: Text encoding for text/markup files
            delimiter: CSV delimiter (auto-detect if None)
            
        Returns:
            Dictionary mapping input paths to processing results
            
        Examples:
            # Process with image extraction but no OCR
            loader.batch_process("docs/", extract_images=True, ocr_images=False)
            
            # Process with batch OCR
            loader.batch_process("docs/", extract_images=True, ocr_images=True)
        """
        input_dir = Path(input_dir)
        output_dir = Path(output_dir) if output_dir else input_dir
        output_format = self._normalize_output_format(output_format)

        if not input_dir.exists():
            raise FileNotFoundError(f"Directory not found: {input_dir}")

        logger.info(f"🗂️  Starting batch processing: {input_dir}")
        logger.info(f"📁 Output directory: {output_dir}")
        logger.info(f"📊 Recursive: {recursive}, Save files: {save_files}")
        logger.info(f"🖼️  Image processing: extract_images={extract_images}, ocr_images={ocr_images}")

        # Find all supported files
        pattern = "**/*" if recursive else "*"
        results = {}
        processed_count = 0
        error_count = 0
        start_time = time.time()

        # Collect files by format for better processing
        files_by_format = {}
        all_files = []

        for doc_format in DocumentFormat:
            format_pattern = f"{pattern}.{doc_format.value}"
            files = list(input_dir.glob(format_pattern))
            if files:
                files_by_format[doc_format] = files
                all_files.extend(files)

        # Also check markdown extension variant
        md_files = list(input_dir.glob(f"{pattern}.markdown"))
        if md_files:
            files_by_format[DocumentFormat.MARKDOWN] = files_by_format.get(DocumentFormat.MARKDOWN, []) + md_files
            all_files.extend(md_files)

        total_files = len(all_files)

        if total_files == 0:
            logger.warning("No supported files found")
            return results

        logger.info(f"📄 Found {total_files} files to process")
        if show_progress:
            for fmt, files in files_by_format.items():
                logger.info(f"   {fmt.value.upper()}: {len(files)} files")

        # Process files
        for file_path in all_files:
            if not file_path.is_file():
                continue

            try:
                # Calculate output path
                rel_path = file_path.relative_to(input_dir)
                if save_files:
                    output_path = output_dir / rel_path.parent / file_path.stem
                    # Ensure output directory exists
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                else:
                    output_path = None

                # Show progress
                if show_progress:
                    logger.info(f"📄 Processing {processed_count + 1}/{total_files}: {file_path.name}")

                # Process file
                start_file_time = time.time()
                result = self.load(
                    file_path=file_path,
                    output_format=output_format,
                    extract_images=extract_images,
                    ocr_images=ocr_images,
                    show_progress=show_progress,
                    encoding=encoding,
                    delimiter=delimiter
                )
                file_duration = time.time() - start_file_time

                # Save output if requested
                output_files = []
                if save_files and output_path:
                    output_files = self._save_result(result, output_path, output_format)

                # Store result
                results[str(file_path)] = {
                    'status': 'success',
                    'format': result.metadata.format.value,
                    'content_length': len(result.content) if result.content else 0,
                    'duration': file_duration,
                    'output_files': output_files,
                    'metadata': {
                        'images_extracted': len(result.images) if result.images else 0,
                        'tables_found': len(result.tables) if result.tables else 0,
                        'pages': result.metadata.page_count or 1
                    }
                }

                processed_count += 1

                if show_progress and processed_count % 10 == 0:
                    elapsed = time.time() - start_time
                    rate = processed_count / elapsed
                    eta = (total_files - processed_count) / rate if rate > 0 else 0
                    logger.info(
                        f"📊 Progress: {processed_count}/{total_files} ({processed_count / total_files * 100:.1f}%) - ETA: {eta:.1f}s")

            except Exception as e:
                error_count += 1
                logger.error(f"❌ Failed to process {file_path}: {e}")
                results[str(file_path)] = {
                    'status': 'failed',
                    'error': str(e),
                    'format': file_path.suffix.lower()
                }

        # Final summary
        total_time = time.time() - start_time
        logger.info(f"🏁 Batch processing complete!")
        logger.info(f"📊 Results: {processed_count} succeeded, {error_count} failed")
        logger.info(f"⏱️  Total time: {total_time:.2f}s ({processed_count / total_time:.2f} files/sec)")

        return results

    def batch_process_files(
            self,
            file_paths: List[Union[str, Path]],
            output_dir: Optional[Union[str, Path]] = None,
            output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
            extract_images: bool = False,
            ocr_images: bool = False,
            show_progress: bool = True,
            save_files: bool = True,
            encoding: str = 'utf-8',
            delimiter: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Batch process a specific list of files.
        
        Args:
            file_paths: List of file paths to process
            output_dir: Optional output directory
            output_format: Output format (MARKDOWN, JSON, TEXT)
            extract_images: Whether to extract images from documents (Office/PDF only)
            ocr_images: Whether to perform OCR on extracted images (requires extract_images=True)
            show_progress: Whether to show progress messages
            save_files: Whether to save output files
            encoding: Text encoding for text/markup files
            delimiter: CSV delimiter (auto-detect if None)
            
        Returns:
            Dictionary mapping input paths to processing results
            
        Examples:
            # Process specific files with OCR
            files = ["doc1.pdf", "doc2.docx"]
            loader.batch_process_files(files, extract_images=True, ocr_images=True)
        """
        if not file_paths:
            return {}

        file_paths = [Path(p) for p in file_paths]
        output_format = self._normalize_output_format(output_format)
        total_files = len(file_paths)
        results = {}
        processed_count = 0
        error_count = 0
        start_time = time.time()

        logger.info(f"📄 Starting batch processing of {total_files} files")
        logger.info(f"🖼️  Image processing: extract_images={extract_images}, ocr_images={ocr_images}")

        for i, file_path in enumerate(file_paths):
            try:
                # Calculate output path
                if save_files and output_dir:
                    output_path = Path(output_dir) / file_path.stem
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                else:
                    output_path = None

                # Show progress
                if show_progress:
                    logger.info(f"📄 Processing {i + 1}/{total_files}: {file_path.name}")

                # Process file
                start_file_time = time.time()
                result = self.load(
                    file_path=file_path,
                    output_format=output_format,
                    extract_images=extract_images,
                    ocr_images=ocr_images,
                    show_progress=show_progress,
                    encoding=encoding,
                    delimiter=delimiter
                )
                file_duration = time.time() - start_file_time

                # Save output if requested
                output_files = []
                if save_files and output_path:
                    output_files = self._save_result(result, output_path, output_format)

                # Store result
                results[str(file_path)] = {
                    'status': 'success',
                    'format': result.metadata.format.value,
                    'content_length': len(result.content) if result.content else 0,
                    'duration': file_duration,
                    'output_files': output_files,
                    'metadata': {
                        'images_extracted': len(result.images) if result.images else 0,
                        'tables_found': len(result.tables) if result.tables else 0
                    }
                }

                processed_count += 1

            except Exception as e:
                error_count += 1
                logger.error(f"❌ Failed to process {file_path}: {e}")
                results[str(file_path)] = {
                    'status': 'failed',
                    'error': str(e),
                    'format': file_path.suffix.lower()
                }

        # Final summary
        total_time = time.time() - start_time
        logger.info(f"🏁 Batch processing complete!")
        logger.info(f"📊 Results: {processed_count} succeeded, {error_count} failed")
        logger.info(f"⏱️  Total time: {total_time:.2f}s")

        return results

    def _save_result(
            self,
            result: ProcessedDocument,
            output_path: Path,
            output_format: OutputFormat
    ) -> List[str]:
        """Save processing result to file(s).
        
        Args:
            result: Processing result
            output_path: Base output path (without extension)
            output_format: Output format
            
        Returns:
            List of created file paths
        """
        output_files = []

        if output_format == OutputFormat.MARKDOWN:
            # Save markdown
            md_path = output_path.with_suffix('.md')
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(result.content)
            output_files.append(str(md_path))

        elif output_format == OutputFormat.JSON:
            # Save JSON
            json_path = output_path.with_suffix('.json')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
            output_files.append(str(json_path))

        # Save images if extracted
        if result.images:
            images_dir = output_path.parent / f"{output_path.name}_images"
            images_dir.mkdir(exist_ok=True)

            for i, image_info in enumerate(result.images):
                image_path = images_dir / f"image_{i:03d}.png"
                
                # Handle different image data formats
                image_data = None
                if isinstance(image_info, dict):
                    # Check for different possible keys
                    if 'data' in image_info:
                        image_data = image_info['data']
                    elif 'content' in image_info:
                        # Base64 encoded data
                        import base64
                        image_data = base64.b64decode(image_info['content'])
                elif isinstance(image_info, bytes):
                    image_data = image_info
                elif isinstance(image_info, str):
                    # Assume it's base64 encoded
                    import base64
                    image_data = base64.b64decode(image_info)
                
                if image_data:
                    with open(image_path, 'wb') as f:
                        f.write(image_data)
                    output_files.append(str(image_path))

        return output_files

    def _detect_format(self, file_path: Path, use_mime: bool = False) -> DocumentFormat:
        """Detect document format from file extension or MIME type.
        
        Args:
            file_path: File path
            use_mime: Whether to use MIME type detection
            
        Returns:
            Document format enum
            
        Raises:
            UnsupportedFormatError: If format cannot be detected
        """
        # First try MIME type detection if enabled
        if use_mime:
            try:
                from doc2mark.core.mime_mapper import get_default_mapper
                mapper = get_default_mapper()
                doc_format = mapper.detect_format_from_file(file_path, use_content=False)
                if doc_format:
                    logger.debug(f"Detected format {doc_format} from MIME type for {file_path}")
                    return doc_format
            except Exception as e:
                logger.debug(f"MIME type detection failed: {e}, falling back to extension")
        
        # Fall back to extension-based detection
        extension = file_path.suffix.lower().lstrip('.')

        # Try to match extension to format
        for fmt in DocumentFormat:
            if fmt.value == extension:
                return fmt

        # Special cases
        if extension == 'markdown':
            return DocumentFormat.MARKDOWN
        elif extension == 'htm':
            return DocumentFormat.HTML

        raise UnsupportedFormatError(
            f"Cannot detect format for extension: {extension}"
        )

    def _get_cached(
            self,
            file_path: Path,
            output_format: OutputFormat,
            options: Optional[Dict[str, Any]] = None
    ) -> Optional[ProcessedDocument]:
        """Get cached result if available.
        
        Args:
            file_path: Original file path
            output_format: Output format
            
        Returns:
            Cached document or None
        """
        if not self.cache_dir:
            return None

        cache_file = self._cache_file_path(file_path, output_format, options or {})
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
            return self._document_from_cache_dict(payload["document"])
        except Exception as e:
            logger.warning(f"Failed to read cache for {file_path}: {e}")
            return None

    def _cache_result(
            self,
            file_path: Path,
            output_format: OutputFormat,
            result: ProcessedDocument,
            options: Optional[Dict[str, Any]] = None
    ):
        """Cache processing result.
        
        Args:
            file_path: Original file path
            output_format: Output format
            result: Processing result
        """
        if not self.cache_dir:
            return

        cache_file = self._cache_file_path(file_path, output_format, options or {})
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "doc2mark-document-cache-v1",
            "source": str(file_path.resolve()),
            "output_format": output_format.value,
            "document": self._document_to_cache_dict(result),
        }
        tmp_file = cache_file.with_suffix(cache_file.suffix + ".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            tmp_file.replace(cache_file)
        except Exception as e:
            logger.warning(f"Failed to write cache for {file_path}: {e}")
            try:
                tmp_file.unlink(missing_ok=True)
            except OSError:
                pass

    def _cache_file_path(self, file_path: Path, output_format: OutputFormat, options: Dict[str, Any]) -> Path:
        stat = file_path.stat()
        key_payload = {
            "path": str(file_path.resolve()),
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "output_format": output_format.value,
            "options": self._json_cache_safe(options),
        }
        cache_key = hashlib.sha256(json.dumps(key_payload, sort_keys=True).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{cache_key}.json"

    @classmethod
    def _json_cache_safe(cls, value: Any) -> Any:
        if isinstance(value, bytes):
            return {"__bytes__": base64.b64encode(value).decode("ascii")}
        if isinstance(value, (DocumentFormat, OutputFormat, OCRProvider, PromptTemplate)):
            return value.value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): cls._json_cache_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_cache_safe(item) for item in value]
        return value

    @classmethod
    def _restore_cache_value(cls, value: Any) -> Any:
        if isinstance(value, dict):
            if set(value.keys()) == {"__bytes__"}:
                return base64.b64decode(value["__bytes__"].encode("ascii"))
            return {key: cls._restore_cache_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._restore_cache_value(item) for item in value]
        return value

    @classmethod
    def _document_to_cache_dict(cls, result: ProcessedDocument) -> Dict[str, Any]:
        return {
            "content": result.content,
            "metadata": cls._json_cache_safe(result.metadata.__dict__),
            "images": cls._json_cache_safe(result.images),
            "tables": cls._json_cache_safe(result.tables),
            "sections": cls._json_cache_safe(result.sections),
            "json_content": cls._json_cache_safe(result.json_content),
        }

    @classmethod
    def _document_from_cache_dict(cls, payload: Dict[str, Any]) -> ProcessedDocument:
        metadata_dict = cls._restore_cache_value(payload["metadata"])
        metadata_dict["format"] = DocumentFormat(metadata_dict["format"])
        metadata = DocumentMetadata(**metadata_dict)
        return ProcessedDocument(
            content=payload["content"],
            metadata=metadata,
            images=cls._restore_cache_value(payload.get("images")),
            tables=cls._restore_cache_value(payload.get("tables")),
            sections=cls._restore_cache_value(payload.get("sections")),
            json_content=cls._restore_cache_value(payload.get("json_content")),
        )

    @property
    def supported_formats(self) -> List[str]:
        """Get list of supported formats.
        
        Returns:
            List of format extensions
        """
        return [fmt.value for fmt in DocumentFormat]

    def validate_ocr(self) -> bool:
        """Validate OCR provider configuration.
        
        Returns:
            True if OCR is properly configured
        """
        if self.ocr is None:
            return False
        return self.ocr.validate_api_key()

    def set_ocr_provider(
            self,
            provider: Optional[Union[str, OCRProvider, BaseOCR]],
            api_key: Optional[str] = None,
            config: Optional[OCRConfig] = None,
            ocr_cache: Optional[OCRCache] = None
    ):
        """Change OCR provider.
        
        Args:
            provider: New OCR provider
            api_key: API key for provider
            config: OCR configuration
            ocr_cache: Optional OCR cache handler. Reuses existing handler when omitted.
        """
        preserved_options = self._current_ocr_constructor_options()
        if api_key is not None:
            preserved_options["api_key"] = api_key
        if config is not None:
            preserved_options["ocr_config"] = config

        if provider is None or (isinstance(provider, str) and provider.lower() in {"none", "disabled"}):
            self.ocr = None
        elif isinstance(provider, BaseOCR):
            self.ocr = provider
        else:
            self.ocr = self._create_ocr_provider(
                ocr_provider=provider,
                **preserved_options,
            )

        if not hasattr(self, "ocr_cache"):
            self.ocr_cache = None
        self._apply_ocr_cache(ocr_cache)

        # Reinitialize processors with new OCR
        self._initialize_processors()

    def get_ocr_configuration(self) -> Dict[str, Any]:
        """Get current OCR configuration summary.
        
        Returns:
            Dictionary with OCR configuration details
        """
        if self.ocr is None:
            return {
                "provider": None,
                "enabled": False,
                "api_key_configured": False,
                "config": None,
            }
        if hasattr(self.ocr, 'get_configuration_summary'):
            return self.ocr.get_configuration_summary()
        else:
            return {
                "provider": type(self.ocr).__name__,
                "api_key_configured": bool(self.ocr.api_key),
                "config": self.ocr.config.__dict__ if self.ocr.config else None
            }

    def update_ocr_configuration(self, **kwargs):
        """Update OCR configuration dynamically.
        
        Args:
            **kwargs: Configuration parameters to update
            
        Available for OpenAI OCR:
            - model: str
            - temperature: float
            - max_tokens: int
            - max_workers: int
            - prompt_template: str
            - enable_langchain: bool
            - timeout: int
            - max_retries: int
        """
        logger.info("🔧 Updating OCR configuration...")

        if self.ocr is None:
            logger.warning("OCR provider is disabled; no configuration was updated")
            return

        if hasattr(self.ocr, 'update_model_config'):
            # Extract model configuration parameters
            model_params = {}
            prompt_template = None

            for key, value in kwargs.items():
                if key == 'prompt_template':
                    prompt_template = value
                elif key in ['model', 'temperature', 'max_tokens', 'timeout', 'max_retries']:
                    model_params[key] = value
                elif key in ['max_workers', 'enable_langchain']:
                    # These are instance attributes, set directly
                    setattr(self.ocr, key, value)
                    logger.info(f"✓ Updated {key}: {value}")
                else:
                    # Additional model parameters
                    model_params[key] = value

            # Update model configuration if there are any model parameters
            if model_params:
                self.ocr.update_model_config(**model_params)

            # Update prompt template if specified
            if prompt_template:
                self.ocr.update_prompt_template(prompt_template)

        else:
            logger.warning("⚠️  OCR provider doesn't support dynamic configuration updates")

        # Log updated configuration
        config = self.get_ocr_configuration()
        logger.info("📋 Updated OCR Configuration:")
        for key, value in config.items():
            logger.info(f"   {key}: {value}")

    def get_available_prompt_templates(self) -> Dict[str, str]:
        """Get available prompt templates for OCR.
        
        Returns:
            Dictionary of template names and descriptions
        """
        if self.ocr is None:
            return {}
        if hasattr(self.ocr, 'get_available_prompts'):
            return self.ocr.get_available_prompts()
        else:
            return {"default": "Standard OCR processing"}

    def validate_ocr_setup(self) -> Dict[str, Any]:
        """Validate OCR setup and return status information.
        
        Returns:
            Dictionary with validation results
        """
        logger.info("🔐 Validating OCR setup...")

        if self.ocr is None:
            return {
                "provider": None,
                "enabled": False,
                "api_key_configured": False,
                "api_key_valid": False,
                "configuration": self.get_ocr_configuration(),
                "available_templates": {},
                "errors": [],
            }

        validation_results = {
            "provider": type(self.ocr).__name__,
            "api_key_configured": bool(self.ocr.api_key),
            "api_key_valid": False,
            "configuration": self.get_ocr_configuration(),
            "available_templates": self.get_available_prompt_templates(),
            "errors": []
        }

        try:
            # Validate API key if provider requires it
            if self.ocr.requires_api_key:
                if not self.ocr.api_key:
                    validation_results["errors"].append("API key required but not provided")
                else:
                    validation_results["api_key_valid"] = self.ocr.validate_api_key()
                    if not validation_results["api_key_valid"]:
                        validation_results["errors"].append("API key validation failed")
            else:
                validation_results["api_key_valid"] = True

        except Exception as e:
            validation_results["errors"].append(f"Validation error: {str(e)}")
            logger.error(f"❌ OCR validation failed: {e}")

        # Log validation results
        if validation_results["errors"]:
            logger.warning(f"⚠️  OCR validation issues: {validation_results['errors']}")
        else:
            logger.info("✅ OCR setup validation successful")

        return validation_results
