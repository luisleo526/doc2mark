"""Command line interface for doc2mark."""

import argparse
import sys
import os
import logging
from pathlib import Path
from datetime import datetime
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import partial

from doc2mark import UnifiedDocumentLoader


def setup_logging(log_file=None, verbose=False):
    """Set up logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers
    )
    return logging.getLogger(__name__)


def filter_files(files, exclude_patterns=None, max_files=None, sort_by="name"):
    """Filter and sort files based on criteria."""
    if exclude_patterns:
        import fnmatch
        filtered = []
        for f in files:
            excluded = False
            for pattern in exclude_patterns:
                if fnmatch.fnmatch(f.name, pattern):
                    excluded = True
                    break
            if not excluded:
                filtered.append(f)
        files = filtered
    
    # Sort files
    if sort_by == "name":
        files.sort(key=lambda x: x.name)
    elif sort_by == "size":
        files.sort(key=lambda x: x.stat().st_size)
    elif sort_by == "date":
        files.sort(key=lambda x: x.stat().st_mtime)
    
    # Limit files
    if max_files:
        files = files[:max_files]
    
    return files


def print_progress(current, total, style="bar", no_color=False):
    """Print progress indicator."""
    if style == "none":
        return
    
    percentage = (current / total) * 100 if total > 0 else 0
    
    if style == "bar":
        bar_length = 40
        filled = int(bar_length * current / total) if total > 0 else 0
        bar = "█" * filled + "░" * (bar_length - filled)
        
        if no_color:
            print(f"\r[{bar}] {percentage:.1f}% ({current}/{total})", end="", flush=True)
        else:
            # Green progress bar
            print(f"\r\033[32m[{bar}]\033[0m {percentage:.1f}% ({current}/{total})", end="", flush=True)
    
    elif style == "dots":
        dots = "." * (current % 4)
        print(f"\rProcessing{dots:<4} {current}/{total}", end="", flush=True)


def process_single_file(file_path, loader_config, processing_config):
    """Process a single file - used for parallel processing."""
    try:
        # Create loader with config
        loader = UnifiedDocumentLoader(
            ocr_provider=loader_config['ocr_provider'],
            api_key=loader_config['api_key']
        )
        
        # Process with retry logic
        retry_count = 0
        result = None
        
        while retry_count <= processing_config['retry']:
            try:
                result = loader.load(
                    file_path=file_path,
                    output_format=processing_config['format'],
                    extract_images=processing_config['extract_images'],
                    ocr_images=processing_config['ocr_images']
                )
                
                # Apply max length if specified
                if processing_config['max_length'] and result.content:
                    if len(result.content) > processing_config['max_length']:
                        result.content = result.content[:processing_config['max_length']] + "\n\n... (truncated)"
                
                # Add metadata if requested
                if processing_config['include_metadata'] and result.content:
                    metadata_str = f"---\nFile: {file_path.name}\nSize: {file_path.stat().st_size} bytes\nModified: {datetime.fromtimestamp(file_path.stat().st_mtime)}\n---\n\n"
                    result.content = metadata_str + result.content
                
                return ('success', file_path, result)
            except Exception as e:
                retry_count += 1
                if retry_count > processing_config['retry']:
                    return ('error', file_path, str(e))
    
    except Exception as e:
        return ('error', file_path, str(e))


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="doc2mark - Universal document processor with AI-powered OCR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  doc2mark document.docx                           # Process single file to stdout
  doc2mark document.pdf -o output.md              # Save to output file
  doc2mark /path/to/docs/ -o /path/to/output/     # Process directory
  
  # OCR options
  doc2mark file.pdf --ocr openai                  # Use OpenAI GPT-4V OCR
  doc2mark file.pdf --ocr tesseract --ocr-lang deu  # German Tesseract OCR
  doc2mark file.pdf --ocr none                    # Disable OCR
  doc2mark file.pdf --no-extract-images           # Skip image extraction
  
  # Advanced processing
  doc2mark docs/ -r --pattern "*.pdf" -p 4        # Parallel processing with 4 workers
  doc2mark docs/ --exclude "*.tmp" --exclude test*  # Exclude patterns
  doc2mark docs/ --max-files 10 --sort size       # Process 10 largest files
  doc2mark file.pdf --retry 3 --timeout 600       # Retry failed files
  
  # Output options
  doc2mark file.pdf --format json                 # JSON output
  doc2mark file.pdf --include-metadata            # Add file metadata
  doc2mark file.pdf --max-length 5000             # Truncate long output
  doc2mark docs/ --skip-errors --log-file process.log  # Continue on errors
  
Supported formats:
  Office: DOCX, XLSX, PPTX, DOC, XLS, PPT, RTF, PPS
  PDF: PDF files with text extraction and OCR
  Data: JSON, JSONL, CSV, TSV
  Markup: HTML, XML, Markdown
  Text: TXT files
        """
    )

    parser.add_argument(
        "input_path",
        help="Input file or directory path"
    )

    parser.add_argument(
        "-o", "--output",
        help="Output file or directory path"
    )

    # OCR and processing options
    ocr_group = parser.add_argument_group('OCR options')
    ocr_group.add_argument(
        "--ocr",
        choices=["openai", "tesseract", "none"],
        default="openai",
        help="OCR provider to use (default: openai, use 'none' to disable OCR)"
    )
    
    ocr_group.add_argument(
        "--api-key",
        help="API key for OCR provider (defaults to OPENAI_API_KEY env var)"
    )
    
    ocr_group.add_argument(
        "--ocr-lang",
        default="eng",
        help="Language for Tesseract OCR (default: eng)"
    )
    
    ocr_group.add_argument(
        "--extract-images",
        action="store_true",
        default=True,
        help="Extract images from documents (default: True)"
    )
    
    ocr_group.add_argument(
        "--no-extract-images",
        dest="extract_images",
        action="store_false",
        help="Disable image extraction"
    )
    
    ocr_group.add_argument(
        "--ocr-images",
        action="store_true",
        default=True,
        help="OCR images in documents (default: True)"
    )
    
    ocr_group.add_argument(
        "--no-ocr-images",
        dest="ocr_images",
        action="store_false",
        help="Disable OCR on images"
    )

    # Output options
    output_group = parser.add_argument_group('Output options')
    output_group.add_argument(
        "--format",
        choices=["markdown", "json", "both"],
        default="markdown",
        help="Output format (default: markdown)"
    )
    
    output_group.add_argument(
        "--encoding",
        default="utf-8",
        help="Output file encoding (default: utf-8)"
    )
    
    output_group.add_argument(
        "--preserve-structure",
        action="store_true",
        help="Preserve original document structure in output"
    )
    
    output_group.add_argument(
        "--include-metadata",
        action="store_true",
        help="Include document metadata in output"
    )
    
    output_group.add_argument(
        "--max-length",
        type=int,
        help="Maximum output length (truncate if longer)"
    )

    # Directory processing options
    dir_group = parser.add_argument_group('Directory processing')
    dir_group.add_argument(
        "--recursive", "-r",
        action="store_true",
        help="Process directories recursively"
    )
    
    dir_group.add_argument(
        "--pattern",
        default="*",
        help="File pattern for directory processing (default: *)"
    )
    
    dir_group.add_argument(
        "--exclude",
        action="append",
        help="Patterns to exclude (can be used multiple times)"
    )
    
    dir_group.add_argument(
        "--max-files",
        type=int,
        help="Maximum number of files to process"
    )
    
    dir_group.add_argument(
        "--sort",
        choices=["name", "size", "date"],
        default="name",
        help="Sort files by (default: name)"
    )

    # Processing options
    proc_group = parser.add_argument_group('Processing options')
    proc_group.add_argument(
        "--parallel", "-p",
        type=int,
        metavar="N",
        help="Process N files in parallel"
    )
    
    proc_group.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout per file in seconds (default: 300)"
    )
    
    proc_group.add_argument(
        "--retry",
        type=int,
        default=1,
        help="Number of retries on failure (default: 1)"
    )
    
    proc_group.add_argument(
        "--skip-errors",
        action="store_true",
        help="Skip files that cause errors instead of stopping"
    )

    # Output control
    output_control = parser.add_argument_group('Output control')
    output_control.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )
    
    output_control.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress output"
    )
    
    output_control.add_argument(
        "--log-file",
        help="Log processing details to file"
    )
    
    output_control.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output"
    )
    
    output_control.add_argument(
        "--progress",
        choices=["bar", "dots", "none"],
        default="bar",
        help="Progress indicator style (default: bar)"
    )

    args = parser.parse_args()

    # Set up logging
    logger = setup_logging(args.log_file, args.verbose)
    
    # Validate input
    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    # Set up output path
    output_path = Path(args.output) if args.output else None
    
    # Handle OCR provider
    ocr_provider = None if args.ocr == "none" else args.ocr

    try:
        # Initialize loader
        loader = UnifiedDocumentLoader(
            ocr_provider=ocr_provider,
            api_key=args.api_key
        )
        
        # Set Tesseract language if using Tesseract
        if args.ocr == "tesseract" and hasattr(loader, 'ocr_processor'):
            loader.ocr_processor.lang = args.ocr_lang

        if input_path.is_file():
            # Process single file
            if not args.quiet:
                logger.info(f"Processing file: {input_path}")
            
            # Process with retry logic
            retry_count = 0
            result = None
            
            while retry_count <= args.retry:
                try:
                    result = loader.load(
                        file_path=input_path,
                        output_format=args.format,
                        extract_images=args.extract_images,
                        ocr_images=args.ocr_images
                    )
                    break
                except Exception as e:
                    retry_count += 1
                    if retry_count <= args.retry:
                        logger.warning(f"Retry {retry_count}/{args.retry} after error: {e}")
                    else:
                        raise
            
            # Apply max length if specified
            if args.max_length and result.content and len(result.content) > args.max_length:
                result.content = result.content[:args.max_length] + "\n\n... (truncated)"
            
            # Add metadata if requested
            if args.include_metadata and result.content:
                metadata_str = f"---\nFile: {input_path.name}\nSize: {input_path.stat().st_size} bytes\nModified: {datetime.fromtimestamp(input_path.stat().st_mtime)}\n---\n\n"
                result.content = metadata_str + result.content

            if output_path:
                # Save to file
                if args.format == "markdown":
                    output_file = output_path.with_suffix('.md')
                    with open(output_file, 'w', encoding=args.encoding) as f:
                        f.write(result.content)
                    if not args.quiet:
                        print(f"Output saved to: {output_file}")
                elif args.format == "json":
                    output_file = output_path.with_suffix('.json')
                    with open(output_file, 'w', encoding=args.encoding) as f:
                        json.dump(result.json_content, f, ensure_ascii=False, indent=2)
                    if not args.quiet:
                        print(f"Output saved to: {output_file}")
                elif args.format == "both":
                    # Save both formats
                    md_file = output_path.with_suffix('.md')
                    json_file = output_path.with_suffix('.json')

                    with open(md_file, 'w', encoding=args.encoding) as f:
                        f.write(result.content)

                    with open(json_file, 'w', encoding=args.encoding) as f:
                        json.dump(result.json_content, f, ensure_ascii=False, indent=2)

                    if not args.quiet:
                        print(f"Output saved to: {md_file} and {json_file}")
            else:
                # Print to stdout
                if args.format == "json":
                    import json
                    print(json.dumps(result.json_content, ensure_ascii=False, indent=2))
                else:
                    # Show preview for markdown
                    content = result.content
                    if len(content) > 1000 and not args.verbose:
                        content = content[:1000] + "\n\n... (truncated, use -v for full output)"
                    print(content)

        elif input_path.is_dir():
            # Process directory
            if not args.quiet:
                logger.info(f"Processing directory: {input_path}")
                logger.info(f"Pattern: {args.pattern}")
                logger.info(f"Recursive: {args.recursive}")
            
            # Get list of files to process
            if args.recursive:
                files = list(input_path.rglob(args.pattern))
            else:
                files = list(input_path.glob(args.pattern))
            
            # Filter and sort files
            files = filter_files(files, args.exclude, args.max_files, args.sort)
            
            if not files:
                logger.warning("No files found matching criteria")
                return
            
            # Process files with progress
            results = []
            failed_files = []
            
            # Prepare configs for parallel processing
            loader_config = {
                'ocr_provider': ocr_provider,
                'api_key': args.api_key
            }
            
            processing_config = {
                'format': args.format,
                'extract_images': args.extract_images,
                'ocr_images': args.ocr_images,
                'max_length': args.max_length,
                'include_metadata': args.include_metadata,
                'retry': args.retry
            }
            
            if args.parallel and args.parallel > 1:
                # Parallel processing
                if not args.quiet:
                    logger.info(f"Processing {len(files)} files in parallel (workers: {args.parallel})")
                
                completed = 0
                with ProcessPoolExecutor(max_workers=args.parallel) as executor:
                    # Submit all tasks
                    future_to_file = {
                        executor.submit(process_single_file, file_path, loader_config, processing_config): file_path
                        for file_path in files
                    }
                    
                    # Process completed tasks
                    for future in as_completed(future_to_file):
                        completed += 1
                        if not args.quiet and args.progress != "none":
                            print_progress(completed, len(files), args.progress, args.no_color)
                        
                        try:
                            status, file_path, result_or_error = future.result(timeout=args.timeout)
                            if status == 'success':
                                results.append(result_or_error)
                            else:
                                if args.skip_errors:
                                    logger.error(f"Failed to process {file_path}: {result_or_error}")
                                    failed_files.append((file_path, result_or_error))
                                else:
                                    raise Exception(f"Failed to process {file_path}: {result_or_error}")
                        except Exception as e:
                            file_path = future_to_file[future]
                            if args.skip_errors:
                                logger.error(f"Failed to process {file_path}: {e}")
                                failed_files.append((file_path, str(e)))
                            else:
                                raise
            else:
                # Sequential processing
                for i, file_path in enumerate(files):
                    if not args.quiet and args.progress != "none":
                        print_progress(i, len(files), args.progress, args.no_color)
                    
                    try:
                        # Process with retry logic
                        retry_count = 0
                        result = None
                        
                        while retry_count <= args.retry:
                            try:
                                result = loader.load(
                                    file_path=file_path,
                                    output_format=args.format,
                                    extract_images=args.extract_images,
                                    ocr_images=args.ocr_images
                                )
                                
                                # Apply max length if specified
                                if args.max_length and result.content and len(result.content) > args.max_length:
                                    result.content = result.content[:args.max_length] + "\n\n... (truncated)"
                                
                                # Add metadata if requested
                                if args.include_metadata and result.content:
                                    metadata_str = f"---\nFile: {file_path.name}\nSize: {file_path.stat().st_size} bytes\nModified: {datetime.fromtimestamp(file_path.stat().st_mtime)}\n---\n\n"
                                    result.content = metadata_str + result.content
                                
                                results.append(result)
                                break
                            except Exception as e:
                                retry_count += 1
                                if retry_count <= args.retry:
                                    logger.warning(f"Retry {retry_count}/{args.retry} for {file_path}: {e}")
                                else:
                                    if args.skip_errors:
                                        logger.error(f"Failed to process {file_path}: {e}")
                                        failed_files.append((file_path, str(e)))
                                    else:
                                        raise
                    
                    except Exception as e:
                        if args.skip_errors:
                            logger.error(f"Failed to process {file_path}: {e}")
                            failed_files.append((file_path, str(e)))
                        else:
                            raise
            
            if not args.quiet and args.progress != "none":
                print_progress(len(files), len(files), args.progress, args.no_color)
                print()  # New line after progress
            
            if output_path:
                # Save files to output directory
                output_path.mkdir(parents=True, exist_ok=True)
                
                for doc in results:
                    # Calculate relative path
                    rel_path = Path(doc.metadata.filename).stem
                    
                    if args.format == "markdown":
                        out_file = output_path / f"{rel_path}.md"
                        with open(out_file, 'w', encoding=args.encoding) as f:
                            f.write(doc.content)
                    elif args.format == "json":
                        out_file = output_path / f"{rel_path}.json"
                        with open(out_file, 'w', encoding=args.encoding) as f:
                            json.dump(doc.json_content, f, ensure_ascii=False, indent=2)
                    elif args.format == "both":
                        md_file = output_path / f"{rel_path}.md"
                        json_file = output_path / f"{rel_path}.json"
                        
                        with open(md_file, 'w', encoding=args.encoding) as f:
                            f.write(doc.content)
                        
                        with open(json_file, 'w', encoding=args.encoding) as f:
                            json.dump(doc.json_content, f, ensure_ascii=False, indent=2)
                
                if not args.quiet:
                    print(f"\nProcessed {len(results)} files to: {output_path}")
                    if failed_files:
                        print(f"Failed: {len(failed_files)} files")
            else:
                # Print summary
                if not args.quiet:
                    print(f"\nProcessed {len(results)} files:")
                    for doc in results:
                        status = "✅" if doc.content else "❌"
                        size = len(doc.content) if doc.content else 0
                        if args.no_color:
                            print(f"  {status} {doc.metadata.filename} ({size} chars)")
                        else:
                            color = "\033[32m" if doc.content else "\033[31m"
                            print(f"  {color}{status}\033[0m {doc.metadata.filename} ({size} chars)")
                    
                    if failed_files:
                        print(f"\nFailed files ({len(failed_files)}):")
                        for file_path, error in failed_files:
                            if args.no_color:
                                print(f"  ❌ {file_path}: {error}")
                            else:
                                print(f"  \033[31m❌\033[0m {file_path}: {error}")

        else:
            print(f"Error: {input_path} is not a file or directory", file=sys.stderr)
            sys.exit(1)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
