"""Main UnifiedDocumentLoader implementation."""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from doc2mark.core.base import (
    BaseProcessor,
    DocumentFormat,
    OutputFormat,
    ProcessedDocument,
    ProcessingError,
    UnsupportedFormatError
)
from doc2mark.ocr.base import BaseOCR, OCRConfig, OCRFactory, OCRProvider
from doc2mark.ocr.prompts import PromptTemplate

logger = logging.getLogger(__name__)


class UnifiedDocumentLoader:
    """Main document loader with unified API for all formats and enhanced OCR configuration."""

    def __init__(
            self,
            ocr_provider: Union[str, OCRProvider, BaseOCR] = 'openai',
            api_key: Optional[str] = None,
            ocr_config: Optional[OCRConfig] = None,
            cache_dir: Optional[str] = None,
            # Enhanced OCR configuration for OpenAI
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
            
            # Enhanced OpenAI OCR Configuration:
            model: OpenAI model to use (default: gpt-4.1)
            temperature: Temperature for response generation (0.0-2.0)
            max_tokens: Maximum tokens in response (1-4096)
            max_workers: Maximum concurrent workers for batch processing
            prompt_template: Template name ('default', 'table_focused', 'document_focused', 'multilingual')
            timeout: Request timeout in seconds
            max_retries: Maximum number of retries for failed requests
            
            # Additional OpenAI parameters:
            top_p: Nucleus sampling parameter (0.0-1.0)
            frequency_penalty: Reduce word repetition (-2.0 to 2.0)
            presence_penalty: Encourage new topics (-2.0 to 2.0)
            base_url: Optional base URL for OpenAI-compatible API endpoints
            
            # General OCR parameters:
            default_prompt: Custom default prompt to override built-in prompts
            
            # Table output configuration:
            table_style: Output style for complex tables with merged cells:
                - 'minimal_html': Clean HTML with only rowspan/colspan (default)
                - 'markdown_grid': Markdown with merge annotations
                - 'styled_html': Full HTML with inline styles (legacy)
        """
        logger.info("🚀 Initializing UnifiedDocumentLoader with enhanced OCR configuration")

        # Initialize OCR with enhanced configuration
        if isinstance(ocr_provider, BaseOCR):
            self.ocr = ocr_provider
            logger.info(f"✓ Using provided OCR instance: {type(ocr_provider).__name__}")
        else:
            logger.info(f"🤖 Initializing OCR provider: {ocr_provider}")

            # For OpenAI provider, use enhanced configuration
            if (isinstance(ocr_provider, str) and ocr_provider.lower() == 'openai') or \
                    (isinstance(ocr_provider, OCRProvider) and ocr_provider == OCRProvider.OPENAI):

                logger.info("🔧 Using enhanced OpenAI OCR configuration")

                # Import OpenAI OCR specifically to use enhanced constructor
                from doc2mark.ocr.openai import OpenAIOCR

                self.ocr = OpenAIOCR(
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
                    # Additional OpenAI parameters
                    top_p=top_p,
                    frequency_penalty=frequency_penalty,
                    presence_penalty=presence_penalty
                )

                # Log the configuration being used
                config_summary = self.ocr.get_configuration_summary()
                logger.info("📋 OCR Configuration Summary:")
                for key, value in config_summary.items():
                    logger.info(f"   {key}: {value}")

            else:
                # Use standard factory for other providers
                logger.info(f"📦 Using standard OCR factory for provider: {ocr_provider}")
                self.ocr = OCRFactory.create(
                    provider=ocr_provider,
                    api_key=api_key,
                    config=ocr_config
                )

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
                        DocumentFormat.JPEG, DocumentFormat.WEBP]:
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
            raise ImportError(f"Required format processors not available: {str(e)}")

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
            cached = self._get_cached(file_path, output_format)
            if cached:
                logger.info(f"Using cached result for {file_path}")
                return cached

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
                    # Note: These processors don't support all parameters
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
                        json_data = {
                            "filename": result.metadata.filename,
                            "format": result.metadata.format.value,
                            "content": [{"type": "text:normal", "content": result.content}],
                            "metadata": {
                                "size_bytes": result.metadata.size_bytes,
                                "page_count": result.metadata.page_count,
                                "word_count": result.metadata.word_count
                            }
                        }
                        result.content = json.dumps(json_data, indent=2, ensure_ascii=False)
                        result.json_content = json_data.get('content', [])
                    elif output_format == OutputFormat.TEXT:
                        # Convert to plain text
                        result.content = result.text

            # Cache result
            if self.cache_dir:
                self._cache_result(file_path, output_format, result)

            return result

        except Exception as e:
            logger.error(f"Failed to process {file_path}: {e}")
            raise ProcessingError(f"Processing failed: {str(e)}")

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
            import json
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(result.json_content, f, ensure_ascii=False, indent=2)
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
            output_format: OutputFormat
    ) -> Optional[ProcessedDocument]:
        """Get cached result if available.
        
        Args:
            file_path: Original file path
            output_format: Output format
            
        Returns:
            Cached document or None
        """
        # Implementation depends on caching strategy
        # This is a placeholder
        return None

    def _cache_result(
            self,
            file_path: Path,
            output_format: OutputFormat,
            result: ProcessedDocument
    ):
        """Cache processing result.
        
        Args:
            file_path: Original file path
            output_format: Output format
            result: Processing result
        """
        # Implementation depends on caching strategy
        # This is a placeholder
        pass

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
        return self.ocr.validate_api_key()

    def set_ocr_provider(
            self,
            provider: Union[str, OCRProvider, BaseOCR],
            api_key: Optional[str] = None,
            config: Optional[OCRConfig] = None
    ):
        """Change OCR provider.
        
        Args:
            provider: New OCR provider
            api_key: API key for provider
            config: OCR configuration
        """
        if isinstance(provider, BaseOCR):
            self.ocr = provider
        else:
            self.ocr = OCRFactory.create(
                provider=provider,
                api_key=api_key,
                config=config
            )

        # Reinitialize processors with new OCR
        self._initialize_processors()

    def get_ocr_configuration(self) -> Dict[str, Any]:
        """Get current OCR configuration summary.
        
        Returns:
            Dictionary with OCR configuration details
        """
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
