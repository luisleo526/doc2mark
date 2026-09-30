"""Command line interface for doc2mark."""

from collections import Counter, deque
from contextlib import suppress
from datetime import datetime
from pathlib import Path
import argparse
import inspect
import json
import logging
import multiprocessing
import multiprocessing.connection
import os
import signal
import sys
import time

from doc2mark import UnifiedDocumentLoader
from doc2mark.ocr.base import OCRConfig, Task
from doc2mark.pipelines import pymupdf_compat

logger = logging.getLogger(__name__)


def keep_stdout_for_documents():
    """stdout is where the CLI writes a document when no output file is given: PyMuPDF's own prints (its
    pymupdf_layout recommendation, MuPDF's errors about a damaged file) must not land there. The errors go
    to the log on stderr as warnings instead."""
    pymupdf_compat.quiet_layout_recommendation()
    pymupdf_compat.messages_to_log()


def setup_logging(log_file=None, verbose=False, quiet=False):
    """Set up logging configuration."""
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.ERROR
    else:
        level = logging.WARNING
    
    handlers = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers
    )
    return logging.getLogger(__name__)


def document_json_payload(result):
    """Return the canonical JSON payload for CLI output."""
    return result.to_dict()


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
    
    # Sort files, ascending (the path breaks ties, so the order never depends on how the file system lists them)
    if sort_by == "name":
        files.sort(key=lambda x: (x.name, str(x)))
    elif sort_by == "size":
        files.sort(key=lambda x: (x.stat().st_size, str(x)))
    elif sort_by == "date":
        files.sort(key=lambda x: (x.stat().st_mtime, str(x)))
    
    # Limit files
    if max_files:
        files = files[:max_files]
    
    return files


def collect_files(directory, pattern="*", recursive=False):
    """The files of ``directory`` that match ``pattern``. Sub-folders are never converted themselves:
    ``recursive`` only decides whether the files inside them are matched too."""
    matches = directory.rglob(pattern) if recursive else directory.glob(pattern)
    return [path for path in matches if path.is_file()]


def plan_output_names(files, input_root, output_root, suffixes):
    """The output name of every file of a folder run: ``{file: path relative to the output folder, no extension}``.

    The input tree is mirrored (``2024/report.md`` -> ``2024/report``), so files of different folders never
    share a name. Files of one folder that still would (``report.txt`` and ``report.md`` both give
    ``report.md``), or whose output would be an input file itself (``note.md`` converted into its own folder),
    keep their whole file name instead: ``report.txt`` is written as ``report.txt.md``. Every other file keeps
    its stem. The plan depends only on the set of files, never on the order they are converted in, and each
    clash is logged as a warning. ``suffixes`` are the extensions written (``(".md",)``, ``(".json",)`` or both).
    """
    def key(path):  # one name on a case-insensitive file system
        return path.as_posix().casefold()

    def overwrites_a_source(base):
        return any(key((output_root / base.with_name(base.name + suffix)).resolve()) in sources
                   for suffix in suffixes)

    relative = {path: path.relative_to(input_root) for path in files}
    natural = {path: rel.with_name(rel.stem) for path, rel in relative.items()}
    sources = {key(path.resolve()) for path in files}
    sharing = Counter(key(base) for base in natural.values())
    clashing = {path for path, base in natural.items() if sharing[key(base)] > 1 or overwrites_a_source(base)}

    names = {path: natural[path] for path in files if path not in clashing}
    taken = {key(base) for base in names.values()}
    for path in sorted(clashing, key=lambda p: relative[p].as_posix()):
        candidate, number = relative[path], 1
        while key(candidate) in taken or overwrites_a_source(candidate):
            number += 1
            candidate = relative[path].with_name(f"{relative[path].name}-{number}")
        taken.add(key(candidate))
        names[path] = candidate

    for base_key in sorted({key(natural[path]) for path in clashing}):
        group = sorted((p for p in clashing if key(natural[p]) == base_key), key=lambda p: relative[p].as_posix())
        inputs = ", ".join(relative[p].as_posix() for p in group)
        shown = natural[group[0]].as_posix() + suffixes[0]
        outputs = ", ".join(names[p].as_posix() + suffixes[0] for p in group)
        if len(group) > 1:
            logger.warning(f"{inputs} would all be written as {shown}; writing {outputs} instead")
        else:
            logger.warning(f"{inputs} would overwrite an input file ({shown}); writing {outputs} instead")
    return names


def write_outputs(doc, base, output_format, encoding):
    """Write a converted document as ``<base>.md`` and/or ``<base>.json``. ``base`` has no extension added by
    ``with_suffix``: a dot in a file name (``v1.2``) is part of the name."""
    base.parent.mkdir(parents=True, exist_ok=True)
    if output_format in ("markdown", "both"):
        with open(base.parent / f"{base.name}.md", 'w', encoding=encoding) as f:
            f.write(doc.content)
    if output_format in ("json", "both"):
        with open(base.parent / f"{base.name}.json", 'w', encoding=encoding) as f:
            json.dump(document_json_payload(doc), f, ensure_ascii=False, indent=2)


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
    # A worker process does not run main(); a spawned one (macOS, Python 3.14) has no logging set up either.
    if not logging.getLogger().handlers:
        setup_logging(processing_config.get('log_file'), processing_config.get('verbose', False),
                      processing_config.get('quiet', False))
    keep_stdout_for_documents()
    try:
        # Create loader with config
        loader = UnifiedDocumentLoader(
            ocr_provider=loader_config['ocr_provider'],
            api_key=loader_config['api_key'],
            ocr_config=loader_config.get('ocr_config'),
            table_style=loader_config.get('table_style'),
            judge=loader_config.get('judge'),
        )
        
        # Process with retry logic
        retry_count = 0
        result = None
        
        while retry_count <= processing_config['retry']:
            try:
                result = loader.load(
                    file_path=file_path,
                    output_format=processing_config['load_format'],
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


def _worker_main(connection, loader_config, processing_config):
    """Entry point of a conversion worker: convert every file it is sent until the parent hangs up."""
    if hasattr(os, "setsid"):
        with suppress(OSError):
            os.setsid()  # a process group of its own: stopping the worker stops what it started (LibreOffice, ...)
    while True:
        try:
            file_path = connection.recv()
        except (EOFError, OSError):
            return
        outcome = process_single_file(file_path, loader_config, processing_config)
        try:
            connection.send(outcome)
        except (EOFError, OSError):  # the parent went away
            return
        except Exception as e:  # a result that cannot be passed on
            with suppress(OSError):
                connection.send(('error', file_path, f"the result could not be passed on: {e}"))


class ConversionWorker:
    """A child process that converts the files it is sent, one at a time, with ``process_single_file``."""

    def __init__(self, loader_config, processing_config):
        context = multiprocessing.get_context()
        self.connection, child_connection = context.Pipe()
        self.process = context.Process(target=_worker_main, args=(child_connection, loader_config, processing_config),
                                       daemon=True)
        self.process.start()
        child_connection.close()
        self.file_path = None
        self.deadline = None

    def start(self, file_path, timeout):
        """Send a file to convert; ``timeout`` seconds from now it counts as too slow (0 or None: never)."""
        self.file_path = file_path
        self.deadline = time.monotonic() + timeout if timeout else None
        self.connection.send(file_path)

    def stop(self):
        """Kill the worker, and what it started if it leads its own process group."""
        if hasattr(os, "killpg") and self.process.is_alive():
            with suppress(OSError):
                os.killpg(self.process.pid, signal.SIGKILL)
        self.process.kill()
        self.process.join(10)
        self.connection.close()


def convert_files(files, loader_config, processing_config, workers, timeout):
    """Convert ``files`` in up to ``workers`` worker processes and yield ``(status, file_path, result or error)``
    for each file as it finishes.

    A file that takes longer than ``timeout`` seconds (0 or None: no limit) is stopped: its worker is killed and
    replaced, and its outcome is an error. A worker that dies (out of memory, a crash in a native library) is an
    error for its file too. Closing the generator stops every worker.
    """
    pending = deque(files)
    idle, busy = [], []
    try:
        while pending or busy:
            while pending and (idle or len(busy) < workers):
                worker = idle.pop() if idle else ConversionWorker(loader_config, processing_config)
                worker.start(pending.popleft(), timeout)
                busy.append(worker)

            deadlines = [worker.deadline for worker in busy if worker.deadline is not None]
            ready = multiprocessing.connection.wait(
                [worker.connection for worker in busy],
                timeout=max(0.0, min(deadlines) - time.monotonic()) if deadlines else None)
            now = time.monotonic()
            for worker in list(busy):
                if worker.connection in ready:
                    try:
                        outcome, reusable = worker.connection.recv(), True
                    except (EOFError, OSError):
                        worker.stop()
                        reason = f"the worker process died (exit code {worker.process.exitcode})"
                        outcome, reusable = ('error', worker.file_path, reason), False
                elif worker.deadline is not None and now >= worker.deadline:
                    worker.stop()
                    outcome, reusable = ('error', worker.file_path, f"timed out after {timeout} s"), False
                else:
                    continue
                busy.remove(worker)
                if reusable:
                    idle.append(worker)
                yield outcome
    finally:
        for worker in busy + idle:
            worker.stop()


def main():
    """Main CLI entry point."""
    openai_model = inspect.signature(UnifiedDocumentLoader.__init__).parameters["model"].default
    parser = argparse.ArgumentParser(
        description="doc2mark - Universal document processor with AI-powered OCR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  # Basic usage
  doc2mark document.docx                           # Process single file to stdout
  doc2mark document.pdf -o output.md              # Save to output file
  doc2mark /path/to/docs/ -o /path/to/output/     # Process directory, same tree of .md files in the output
  
  # OCR options
  doc2mark file.pdf --ocr openai --ocr-images     # Use OpenAI OCR (default model: {openai_model})
  doc2mark file.pdf --ocr vertex_ai --ocr-images  # Use Vertex AI / Gemini OCR
  doc2mark file.pdf --ocr tesseract --ocr-lang deu  # German Tesseract OCR
  doc2mark file.pdf --ocr none                    # Disable OCR
  doc2mark file.pdf --no-extract-images           # Skip image extraction
  doc2mark file.pdf --ocr tesseract --ocr-images --judge typesafe  # Optional TypeSafe judge
  
  # Advanced processing
  doc2mark docs/ -r --pattern "*.pdf" -p 4        # Parallel processing with 4 workers
  doc2mark docs/ --exclude "*.tmp" --exclude test*  # Exclude patterns
  doc2mark docs/ --max-files 10 --sort size       # Process the 10 smallest files (sorts are ascending)
  doc2mark docs/ --retry 3 --timeout 600          # Retry failed files; stop a file after 600 s
  
  # Output options
  doc2mark file.pdf --format json                 # JSON output
  doc2mark file.pdf --include-metadata            # Add file metadata
  doc2mark file.pdf --max-length 5000             # Truncate long output
  doc2mark docs/ --skip-errors --log-file process.log  # Continue on errors
  
Supported formats:
  Office: DOCX, XLSX, PPTX, DOC, XLS, PPT, RTF, PPS
  PDF: PDF files with text extraction and OCR
  Images: PNG, JPG, JPEG, WEBP, TIFF, TIF, BMP, GIF, HEIC, HEIF, AVIF (text with --ocr)
  Email: EML
  Data: JSON, JSONL, CSV, TSV
  Markup: HTML (also .htm), XML, Markdown (MD, also .markdown)
  Text: TXT files
        """
    )

    parser.add_argument(
        "input_path",
        help="Input file or directory path"
    )

    parser.add_argument(
        "-o", "--output",
        help="Output file or directory path (a folder run writes the input folder tree under it)"
    )

    # OCR and processing options
    ocr_group = parser.add_argument_group('OCR options')
    ocr_group.add_argument(
        "--ocr",
        choices=["openai", "vertex_ai", "tesseract", "none"],
        default="none",
        help="OCR provider to use (default: none)"
    )
    
    ocr_group.add_argument(
        "--api-key",
        help="API key for OCR provider (defaults to OPENAI_API_KEY env var)"
    )
    
    ocr_group.add_argument(
        "--ocr-lang",
        default="eng",
        help="Language for Tesseract OCR: Tesseract codes such as eng, deu, chi_tra or eng+chi_tra, "
             "or a long name such as chinese_traditional (default: eng). A language that is not "
             "installed fails the run."
    )

    ocr_group.add_argument(
        "--ocr-task",
        choices=[t.value for t in Task],
        default="auto",
        help="OCR task intent for LLM providers (default: auto)"
    )

    ocr_group.add_argument(
        "--no-structured",
        dest="structured",
        action="store_false",
        default=True,
        help="Disable structured OCR output (use free-form markdown)"
    )

    ocr_group.add_argument(
        "--ocr-detail",
        choices=["raw", "full"],
        default="full",
        help="OCR detail level: raw (transcription only) or full (default: full)"
    )
    
    ocr_group.add_argument(
        "--extract-images",
        action="store_true",
        default=False,
        help="Extract images from documents (default: False)"
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
        default=False,
        help="OCR images in documents (default: False)"
    )
    
    ocr_group.add_argument(
        "--no-ocr-images",
        dest="ocr_images",
        action="store_false",
        help="Disable OCR on images"
    )

    judge_group = parser.add_argument_group('Judge options')
    judge_group.add_argument(
        "--judge",
        choices=["none", "typesafe"],
        default=None,
        help="Optional judge for the decisions the rules cannot make alone: whether a PDF text layer "
             "is legible, whether a repeated header/footer line is page chrome, whether an OCR answer "
             "is only a refusal. 'typesafe' needs the doc2mark[typesafe] extra and TYPESAFE_API_KEY; "
             "without them the rules decide, as with 'none'. (default: $DOC2MARK_JUDGE, else none)"
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
        "--table-style",
        choices=["minimal_html", "markdown_grid", "styled_html"],
        default=None,
        help="Output style for tables with merged cells (default: minimal_html)"
    )

    output_group.add_argument(
        "--preserve-structure",
        action="store_true",
        help="Deprecated, no effect: a folder run always writes the input folder tree under -o"
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
        help="Glob for the files of a folder run (default: *, every file; sub-folders are never converted "
             "themselves, -r decides whether their files are matched too)"
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
        help="Sort files by, ascending: --max-files keeps the first ones, so size keeps the smallest and date "
             "the oldest (default: name)"
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
        help="Timeout per file in seconds for folder runs, its retries included: a file that takes longer is "
             "stopped and counts as failed; 0 means no limit (default: 300)"
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
    logger = setup_logging(args.log_file, args.verbose, args.quiet)
    keep_stdout_for_documents()

    if args.timeout < 0:
        parser.error("--timeout must be 0 (no limit) or a number of seconds")
    if args.preserve_structure:
        logger.warning("--preserve-structure is deprecated and has no effect: a folder run always writes "
                       "the input folder tree under -o")

    # Validate input
    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    # Set up output path
    output_path = Path(args.output) if args.output else None
    
    # Handle OCR provider
    ocr_provider = None if args.ocr == "none" else args.ocr
    if args.ocr_images and ocr_provider is None:
        parser.error("--ocr-images requires --ocr openai, --ocr vertex_ai, or --ocr tesseract")
    if args.ocr_images:
        args.extract_images = True

    ocr_config = OCRConfig(
        language=args.ocr_lang if args.ocr == "tesseract" else None,
        task=Task(args.ocr_task),
        structured=args.structured,
        detail=args.ocr_detail,
    )
    load_format = "markdown" if args.format in {"json", "both"} else args.format

    try:
        # Initialize loader
        loader = UnifiedDocumentLoader(
            ocr_provider=ocr_provider,
            api_key=args.api_key,
            ocr_config=ocr_config,
            table_style=args.table_style,
            judge=args.judge,
        )

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
                        output_format=load_format,
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
                        json.dump(document_json_payload(result), f, ensure_ascii=False, indent=2)
                    if not args.quiet:
                        print(f"Output saved to: {output_file}")
                elif args.format == "both":
                    # Save both formats
                    md_file = output_path.with_suffix('.md')
                    json_file = output_path.with_suffix('.json')

                    with open(md_file, 'w', encoding=args.encoding) as f:
                        f.write(result.content)

                    with open(json_file, 'w', encoding=args.encoding) as f:
                        json.dump(document_json_payload(result), f, ensure_ascii=False, indent=2)

                    if not args.quiet:
                        print(f"Output saved to: {md_file} and {json_file}")
            else:
                # Print to stdout
                if args.format == "json":
                    print(json.dumps(document_json_payload(result), ensure_ascii=False, indent=2))
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
            
            # Get list of files to process, filter and sort them
            files = collect_files(input_path, args.pattern, args.recursive)
            files = filter_files(files, args.exclude, args.max_files, args.sort)
            
            if not files:
                logger.warning("No files found matching criteria")
                return

            # Where every file goes: the input tree mirrored under the output folder
            output_names = {}
            if output_path:
                suffixes = {"markdown": (".md",), "json": (".json",), "both": (".md", ".json")}[args.format]
                output_names = plan_output_names(files, input_path, output_path, suffixes)
                output_path.mkdir(parents=True, exist_ok=True)

            # Prepare configs for the conversion workers
            loader_config = {
                'ocr_provider': ocr_provider,
                'api_key': args.api_key,
                'ocr_config': ocr_config,
                'table_style': args.table_style,
                'judge': args.judge,
            }
            
            processing_config = {
                'format': args.format,
                'load_format': load_format,
                'extract_images': args.extract_images,
                'ocr_images': args.ocr_images,
                'max_length': args.max_length,
                'include_metadata': args.include_metadata,
                'retry': args.retry,
                'verbose': args.verbose,
                'quiet': args.quiet,
                'log_file': args.log_file,
            }

            # Every file is converted in a worker process that is stopped when it takes longer than --timeout, and
            # written as soon as it is done: a failure that ends the run keeps what was converted before it
            workers = args.parallel if args.parallel and args.parallel > 1 else 1
            if not args.quiet:
                logger.info(f"Processing {len(files)} files ({workers} at a time, at most {args.timeout or 'any number of'} s each)")

            converted = []  # (path relative to the input folder, content length)
            failed_files = []
            show_progress = not args.quiet and args.progress != "none"
            if show_progress:
                print_progress(0, len(files), args.progress, args.no_color)
            for done, (status, file_path, result_or_error) in enumerate(
                    convert_files(files, loader_config, processing_config, workers, args.timeout), 1):
                if status == 'success':
                    if output_path:
                        write_outputs(result_or_error, output_path / output_names[file_path], args.format,
                                      args.encoding)
                    converted.append((file_path.relative_to(input_path),
                                      len(result_or_error.content) if result_or_error.content else 0))
                elif args.skip_errors:
                    logger.error(f"Failed to process {file_path}: {result_or_error}")
                    failed_files.append((file_path, result_or_error))
                else:
                    raise RuntimeError(f"Failed to process {file_path}: {result_or_error}")
                if show_progress:
                    print_progress(done, len(files), args.progress, args.no_color)

            if show_progress:
                print()  # New line after progress
            
            if output_path:
                if not args.quiet:
                    print(f"\nProcessed {len(converted)} files to: {output_path}")
                    if failed_files:
                        print(f"Failed: {len(failed_files)} files")
            else:
                # Print summary
                if not args.quiet:
                    print(f"\nProcessed {len(converted)} files:")
                    for name, size in converted:
                        status = "✅" if size else "❌"
                        if args.no_color:
                            print(f"  {status} {name} ({size} chars)")
                        else:
                            color = "\033[32m" if size else "\033[31m"
                            print(f"  {color}{status}\033[0m {name} ({size} chars)")
                    
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
