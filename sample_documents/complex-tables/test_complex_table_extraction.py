"""
Test extraction of complex tables from XLSX, DOCX, PPTX, and PDF files
"""
import logging
from pathlib import Path
from doc2mark.formats.office import OfficeProcessor
from doc2mark.formats.pdf import PDFProcessor
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def extract_tables_from_content(content: str, format_name: str):
    """Extract table information from markdown content"""
    logger.info(f"\n{'='*60}")
    logger.info(f"Analyzing {format_name} output")
    logger.info(f"{'='*60}")
    
    # Count markdown tables
    markdown_tables = content.count('| --- |')
    logger.info(f"Markdown tables found: {markdown_tables}")
    
    # Count HTML tables
    html_tables = content.count('<table')
    logger.info(f"HTML tables found: {html_tables}")
    
    # Look for specific merge patterns
    if 'colspan=' in content:
        # Find all colspan occurrences
        import re
        colspan_matches = re.findall(r'colspan="(\d+)"', content)
        if colspan_matches:
            logger.info(f"Colspan values found: {colspan_matches}")
    
    if 'rowspan=' in content:
        # Find all rowspan occurrences
        import re
        rowspan_matches = re.findall(r'rowspan="(\d+)"', content)
        if rowspan_matches:
            logger.info(f"Rowspan values found: {rowspan_matches}")
    
    # Check for specific merged cells we created
    merge_patterns = [
        ("Company Overview", "Should span 3 columns"),
        ("Division", "Should span 2 rows"),
        ("Technology", "Should span 3 rows"),
        ("First Half", "Should span 2 columns"),
        ("Second Half", "Should span 2 columns"),
        ("Combined Products", "Should span 2 columns"),
        ("All Regions Total", "Should span 2 columns"),
        ("Subtotal (All Divisions)", "Should span 3 columns"),
        ("Grand Total (All Quarters)", "Should span 4 columns"),
        ("Annual Total: $382K", "Should span 3 columns")
    ]
    
    logger.info("\nChecking for specific merge patterns:")
    for pattern, description in merge_patterns:
        if pattern in content:
            logger.info(f"✓ Found: '{pattern}' - {description}")
        else:
            logger.warning(f"✗ Missing: '{pattern}' - {description}")
    
    return content

def test_xlsx():
    """Test XLSX complex table extraction"""
    processor = OfficeProcessor()
    result = processor.process('test_complex_tables/complex_table_test.xlsx')
    
    # Save output
    output_path = Path('test_complex_tables/complex_table_test.xlsx.md')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(result.content)
    
    logger.info(f"XLSX output saved to: {output_path}")
    extract_tables_from_content(result.content, "XLSX")
    
    # Check metadata
    logger.info(f"Tables count in metadata: {result.metadata.extra.get('tables_count', 0)}")

def test_docx():
    """Test DOCX complex table extraction"""
    processor = OfficeProcessor()
    result = processor.process('test_complex_tables/complex_table_test.docx')
    
    # Save output
    output_path = Path('test_complex_tables/complex_table_test.docx.md')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(result.content)
    
    logger.info(f"DOCX output saved to: {output_path}")
    extract_tables_from_content(result.content, "DOCX")
    
    # Check metadata
    logger.info(f"Tables count in metadata: {result.metadata.extra.get('tables_count', 0)}")

def test_pptx():
    """Test PPTX complex table extraction"""
    processor = OfficeProcessor()
    result = processor.process('test_complex_tables/complex_table_test.pptx')
    
    # Save output
    output_path = Path('test_complex_tables/complex_table_test.pptx.md')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(result.content)
    
    logger.info(f"PPTX output saved to: {output_path}")
    extract_tables_from_content(result.content, "PPTX")
    
    # Check metadata
    logger.info(f"Tables count in metadata: {result.metadata.extra.get('tables_count', 0)}")

def test_pdf():
    """Test PDF complex table extraction"""
    # First, we need to convert DOCX to PDF
    # For now, we'll assume the user will do this manually
    pdf_path = Path('test_complex_tables/complex_table_test.pdf')
    
    if not pdf_path.exists():
        logger.warning("PDF file not found. Please convert the DOCX file to PDF first.")
        logger.warning("You can use Word's 'Save As PDF' or an online converter.")
        return
    
    processor = PDFProcessor()
    result = processor.process(str(pdf_path))
    
    # Save output
    output_path = Path('test_complex_tables/complex_table_test.pdf.md')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(result.content)
    
    logger.info(f"PDF output saved to: {output_path}")
    extract_tables_from_content(result.content, "PDF")
    
    # Check metadata
    logger.info(f"Tables count in metadata: {result.metadata.extra.get('tables_count', 0)}")

def main():
    """Run all tests"""
    logger.info("Testing complex table extraction from all formats...")
    
    # Test each format
    test_xlsx()
    test_docx()
    test_pptx()
    test_pdf()
    
    logger.info("\n" + "="*60)
    logger.info("Test complete! Check the output files in test_complex_tables/")
    logger.info("="*60)

if __name__ == "__main__":
    main() 