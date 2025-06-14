"""Command line interface for doc2mark."""

import argparse
import sys
from pathlib import Path

from doc2mark import UnifiedDocumentLoader


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="doc2mark - Universal document processor with AI-powered OCR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  doc2mark document.docx                    # Process single file
  doc2mark document.pdf -o output.md       # Process with custom output
  doc2mark /path/to/docs/                   # Process directory
  doc2mark file.pdf --ocr openai           # Use OpenAI OCR
  doc2mark file.pdf --ocr tesseract        # Use Tesseract OCR
  
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

    parser.add_argument(
        "--ocr",
        choices=["openai", "tesseract"],
        default="openai",
        help="OCR provider to use (default: openai)"
    )

    parser.add_argument(
        "--api-key",
        help="API key for OCR provider (defaults to OPENAI_API_KEY env var)"
    )

    parser.add_argument(
        "--format",
        choices=["markdown", "json", "both"],
        default="markdown",
        help="Output format (default: markdown)"
    )

    parser.add_argument(
        "--recursive", "-r",
        action="store_true",
        help="Process directories recursively"
    )

    parser.add_argument(
        "--pattern",
        default="*",
        help="File pattern for directory processing (default: *)"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )

    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress progress output"
    )

    args = parser.parse_args()

    # Validate input
    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    # Set up output path
    output_path = Path(args.output) if args.output else None

    try:
        # Initialize loader
        loader = UnifiedDocumentLoader(
            ocr_provider=args.ocr,
            api_key=args.api_key
        )

        # Show progress unless quiet
        show_progress = not args.quiet

        if input_path.is_file():
            # Process single file
            if args.verbose:
                print(f"Processing file: {input_path}")

            result = loader.load(
                file_path=input_path,
                output_format=args.format
            )

            if output_path:
                # Save to file
                if args.format == "markdown":
                    output_file = output_path.with_suffix('.md')
                    with open(output_file, 'w', encoding='utf-8') as f:
                        f.write(result.content)
                    print(f"Output saved to: {output_file}")
                elif args.format == "json":
                    import json
                    output_file = output_path.with_suffix('.json')
                    with open(output_file, 'w', encoding='utf-8') as f:
                        json.dump(result.json_content, f, ensure_ascii=False, indent=2)
                    print(f"Output saved to: {output_file}")
                elif args.format == "both":
                    # Save both formats
                    md_file = output_path.with_suffix('.md')
                    json_file = output_path.with_suffix('.json')

                    with open(md_file, 'w', encoding='utf-8') as f:
                        f.write(result.content)

                    import json
                    with open(json_file, 'w', encoding='utf-8') as f:
                        json.dump(result.json_content, f, ensure_ascii=False, indent=2)

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
            if args.verbose:
                print(f"Processing directory: {input_path}")
                print(f"Pattern: {args.pattern}")
                print(f"Recursive: {args.recursive}")

            results = loader.load_directory(
                directory=input_path,
                pattern=args.pattern,
                recursive=args.recursive,
                output_format=args.format
            )

            if output_path:
                # Save files to output directory
                output_path.mkdir(parents=True, exist_ok=True)

                for doc in results:
                    # Calculate relative path
                    rel_path = Path(doc.metadata.filename).stem

                    if args.format == "markdown":
                        out_file = output_path / f"{rel_path}.md"
                        with open(out_file, 'w', encoding='utf-8') as f:
                            f.write(doc.content)
                    elif args.format == "json":
                        out_file = output_path / f"{rel_path}.json"
                        import json
                        with open(out_file, 'w', encoding='utf-8') as f:
                            json.dump(doc.json_content, f, ensure_ascii=False, indent=2)
                    elif args.format == "both":
                        md_file = output_path / f"{rel_path}.md"
                        json_file = output_path / f"{rel_path}.json"

                        with open(md_file, 'w', encoding='utf-8') as f:
                            f.write(doc.content)

                        import json
                        with open(json_file, 'w', encoding='utf-8') as f:
                            json.dump(doc.json_content, f, ensure_ascii=False, indent=2)

                print(f"Processed {len(results)} files to: {output_path}")
            else:
                # Print summary
                print(f"Processed {len(results)} files:")
                for doc in results:
                    status = "✅" if doc.content else "❌"
                    size = len(doc.content) if doc.content else 0
                    print(f"  {status} {doc.metadata.filename} ({size} chars)")

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
