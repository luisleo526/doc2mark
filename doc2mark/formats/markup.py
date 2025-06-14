"""Markup format processors (HTML, XML, Markdown)."""

import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Union, Tuple
from xml.etree import ElementTree as ET

from doc2mark.core.base import (
    BaseProcessor,
    DocumentFormat,
    DocumentMetadata,
    ProcessedDocument,
    ProcessingError
)

logger = logging.getLogger(__name__)


class SimpleHTMLToMarkdown(HTMLParser):
    """Simple HTML to Markdown converter (from original UnifiedMarkdownLoader)"""

    def __init__(self):
        super().__init__()
        self.markdown = []
        self.current_tag = None
        self.list_type = None
        self.list_item_count = 0
        self.in_pre = False
        self.in_code = False
        self.href = None

    def handle_starttag(self, tag, attrs):
        self.current_tag = tag

        if tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            level = int(tag[1])
            self.markdown.append('\n' + '#' * level + ' ')
        elif tag == 'p':
            self.markdown.append('\n\n')
        elif tag == 'br':
            self.markdown.append('  \n')
        elif tag in ['strong', 'b']:
            self.markdown.append('**')
        elif tag in ['em', 'i']:
            self.markdown.append('*')
        elif tag == 'code':
            self.in_code = True
            self.markdown.append('`')
        elif tag == 'pre':
            self.in_pre = True
            self.markdown.append('\n```\n')
        elif tag == 'blockquote':
            self.markdown.append('\n> ')
        elif tag == 'ul':
            self.list_type = 'ul'
            self.markdown.append('\n')
        elif tag == 'ol':
            self.list_type = 'ol'
            self.list_item_count = 0
            self.markdown.append('\n')
        elif tag == 'li':
            if self.list_type == 'ul':
                self.markdown.append('- ')
            else:
                self.list_item_count += 1
                self.markdown.append(f'{self.list_item_count}. ')
        elif tag == 'a':
            for attr in attrs:
                if attr[0] == 'href':
                    self.href = attr[1]
            self.markdown.append('[')
        elif tag == 'img':
            alt_text = ''
            src = ''
            for attr in attrs:
                if attr[0] == 'alt':
                    alt_text = attr[1]
                elif attr[0] == 'src':
                    src = attr[1]
            self.markdown.append(f'![{alt_text}]({src})')
        elif tag == 'hr':
            self.markdown.append('\n---\n')

    def handle_endtag(self, tag):
        if tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
            self.markdown.append('\n')
        elif tag == 'p':
            self.markdown.append('\n')
        elif tag in ['strong', 'b']:
            self.markdown.append('**')
        elif tag in ['em', 'i']:
            self.markdown.append('*')
        elif tag == 'code':
            self.in_code = False
            self.markdown.append('`')
        elif tag == 'pre':
            self.in_pre = False
            self.markdown.append('\n```\n')
        elif tag == 'li':
            self.markdown.append('\n')
        elif tag in ['ul', 'ol']:
            self.list_type = None
            self.markdown.append('\n')
        elif tag == 'a' and self.href:
            self.markdown.append(f']({self.href})')
            self.href = None

    def handle_data(self, data):
        if self.in_pre:
            self.markdown.append(data)
        else:
            # Clean up whitespace
            data = data.strip()
            if data:
                self.markdown.append(data)

    def get_markdown(self):
        return ''.join(self.markdown).strip()


class MarkupProcessor(BaseProcessor):
    """Processor for markup formats."""

    def __init__(self):
        """Initialize markup processor."""
        self._bs4 = None
        self._markdownify = None
        self._markdown = None

    @property
    def beautifulsoup(self):
        """Lazy load BeautifulSoup."""
        if self._bs4 is None:
            try:
                from bs4 import BeautifulSoup
                self._bs4 = BeautifulSoup
            except ImportError:
                raise ImportError(
                    "beautifulsoup4 is not installed. "
                    "Install it with: pip install beautifulsoup4"
                )
        return self._bs4

    @property
    def markdownify(self):
        """Lazy load markdownify."""
        if self._markdownify is None:
            try:
                import markdownify
                self._markdownify = markdownify
            except ImportError:
                logger.warning(
                    "markdownify is not installed. HTML conversion will be limited. "
                    "Install it with: pip install markdownify"
                )
        return self._markdownify

    @property
    def markdown(self):
        """Lazy load markdown parser."""
        if self._markdown is None:
            try:
                import markdown
                self._markdown = markdown
            except ImportError:
                logger.warning(
                    "markdown is not installed. Markdown parsing will be limited. "
                    "Install it with: pip install markdown"
                )
        return self._markdown

    def can_process(self, file_path: Union[str, Path]) -> bool:
        """Check if this processor can handle the file."""
        file_path = Path(file_path)
        extension = file_path.suffix.lower().lstrip('.')
        return extension in ['html', 'htm', 'xml', 'md', 'markdown']

    def process(
            self,
            file_path: Union[str, Path],
            **kwargs
    ) -> ProcessedDocument:
        """Process markup document."""
        file_path = Path(file_path)
        extension = file_path.suffix.lower().lstrip('.')

        # Get file size
        file_size = file_path.stat().st_size

        # Process based on format
        if extension in ['html', 'htm']:
            content, metadata = self._process_html(file_path, **kwargs)
            doc_format = DocumentFormat.HTML
        elif extension == 'xml':
            content, metadata = self._process_xml(file_path, **kwargs)
            doc_format = DocumentFormat.XML
        elif extension in ['md', 'markdown']:
            content, metadata = self._process_markdown(file_path, **kwargs)
            doc_format = DocumentFormat.MARKDOWN
        else:
            raise ProcessingError(f"Unsupported markup format: {extension}")

        # Build metadata
        doc_metadata = DocumentMetadata(
            filename=file_path.name,
            format=doc_format,
            size_bytes=file_size,
            **metadata
        )

        return ProcessedDocument(
            content=content,
            metadata=doc_metadata
        )

    def _process_html(self, file_path: Path, **kwargs) -> Tuple[str, dict]:
        """Process HTML file."""
        try:
            encoding = kwargs.get('encoding', 'utf-8')

            with open(file_path, 'r', encoding=encoding) as f:
                html_content = f.read()

            # Parse HTML
            soup = self.beautifulsoup(html_content, 'html.parser')

            # Extract metadata
            title = soup.find('title')
            title_text = title.get_text().strip() if title else None

            # Count elements
            word_count = len(soup.get_text().split())
            link_count = len(soup.find_all('a'))
            image_count = len(soup.find_all('img'))

            # Convert to markdown
            if self.markdownify:
                # Use markdownify for better conversion
                try:
                    markdown_content = self.markdownify.markdownify(
                        str(soup),
                        heading_style="ATX",
                        bullets="-",
                        code_language="python"
                    )
                except Exception as e:
                    logger.warning(f"markdownify failed, using fallback: {e}")
                    # Use our simple converter as fallback
                    parser = SimpleHTMLToMarkdown()
                    parser.feed(html_content)
                    markdown_content = parser.get_markdown()
            else:
                # Use our simple converter
                parser = SimpleHTMLToMarkdown()
                parser.feed(html_content)
                markdown_content = parser.get_markdown()

            metadata = {
                'title': title_text,
                'word_count': word_count,
                'link_count': link_count,
                'image_count': image_count,
                'encoding': encoding
            }

            return markdown_content, metadata

        except Exception as e:
            logger.error(f"Failed to process HTML: {e}")
            raise ProcessingError(f"HTML processing failed: {str(e)}")

    def _process_xml(self, file_path: Path, **kwargs) -> Tuple[str, dict]:
        """Process XML file."""
        try:
            encoding = kwargs.get('encoding', 'utf-8')

            # Parse XML
            tree = ET.parse(str(file_path))
            root = tree.getroot()

            # Convert to markdown
            markdown_content = self._xml_to_markdown(root)

            # Count elements
            element_count = len(root.findall('.//*'))

            metadata = {
                'root_tag': root.tag,
                'element_count': element_count,
                'encoding': encoding
            }

            return markdown_content, metadata

        except Exception as e:
            logger.error(f"Failed to process XML: {e}")
            raise ProcessingError(f"XML processing failed: {str(e)}")

    def _process_markdown(self, file_path: Path, **kwargs) -> Tuple[str, dict]:
        """Process Markdown file."""
        try:
            encoding = kwargs.get('encoding', 'utf-8')

            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()

            # Extract metadata from frontmatter if present
            frontmatter = {}
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    # Parse YAML frontmatter
                    try:
                        import yaml
                        frontmatter = yaml.safe_load(parts[1])
                        content = parts[2].strip()
                    except ImportError:
                        logger.warning("PyYAML not installed, skipping frontmatter parsing")
                    except Exception as e:
                        logger.warning(f"Failed to parse frontmatter: {e}")

            # Count elements
            lines = content.split('\n')
            word_count = len(content.split())

            # Count headers
            header_count = sum(1 for line in lines if line.strip().startswith('#'))

            # Count links and images
            link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'
            image_pattern = r'!\[([^\]]*)\]\(([^)]+)\)'

            links = re.findall(link_pattern, content)
            images = re.findall(image_pattern, content)

            metadata = {
                'word_count': word_count,
                'line_count': len(lines),
                'header_count': header_count,
                'link_count': len(links),
                'image_count': len(images),
                'encoding': encoding,
                'frontmatter': frontmatter if frontmatter else None
            }

            return content, metadata

        except Exception as e:
            logger.error(f"Failed to process Markdown: {e}")
            raise ProcessingError(f"Markdown processing failed: {str(e)}")

    def _basic_html_to_markdown(self, soup) -> str:
        """Basic HTML to Markdown conversion."""
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()

        # Get text content
        text = soup.get_text()

        # Process line by line
        lines = []
        for line in text.split('\n'):
            line = line.strip()
            if line:
                lines.append(line)

        # Try to identify headers by their tags
        markdown_parts = []

        # Process headers
        for i in range(1, 7):
            for header in soup.find_all(f'h{i}'):
                text = header.get_text().strip()
                if text:
                    markdown_parts.append(f"{'#' * i} {text}")

        # Process paragraphs
        for p in soup.find_all('p'):
            text = p.get_text().strip()
            if text:
                markdown_parts.append(text)
                markdown_parts.append("")

        # Process lists
        for ul in soup.find_all('ul'):
            for li in ul.find_all('li'):
                text = li.get_text().strip()
                if text:
                    markdown_parts.append(f"- {text}")
            markdown_parts.append("")

        for ol in soup.find_all('ol'):
            for i, li in enumerate(ol.find_all('li'), 1):
                text = li.get_text().strip()
                if text:
                    markdown_parts.append(f"{i}. {text}")
            markdown_parts.append("")

        # If no structured content found, return the plain text
        if not markdown_parts:
            return '\n\n'.join(lines)

        return '\n'.join(markdown_parts)

    def _xml_to_markdown(self, element, level=0) -> str:
        """Convert XML element to markdown."""
        lines = []

        # Element name as header
        if level == 0:
            lines.append(f"# {element.tag}")
        else:
            lines.append(f"{'#' * min(level + 1, 6)} {element.tag}")

        # Attributes
        if element.attrib:
            lines.append("")
            lines.append("**Attributes:**")
            for key, value in element.attrib.items():
                lines.append(f"- {key}: {value}")

        # Text content
        if element.text and element.text.strip():
            lines.append("")
            lines.append(element.text.strip())

        # Process child elements
        for child in element:
            lines.append("")
            lines.append(self._xml_to_markdown(child, level + 1))

            # Include tail text
            if child.tail and child.tail.strip():
                lines.append(child.tail.strip())

        return '\n'.join(lines)
