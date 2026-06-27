"""Section-aware chunking for RAG pipelines."""

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Literal, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A section-aware chunk of document content."""
    content: str
    section_title: Optional[str] = None
    section_hierarchy: List[str] = field(default_factory=list)
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    content_types: List[str] = field(default_factory=list)
    chunk_index: int = 0


@dataclass
class ChunkingConfig:
    """Configuration for the chunking algorithm."""
    max_chunk_size: int = 1500
    overlap: int = 200
    split_on_heading_level: int = 2
    keep_tables_whole: bool = True
    include_page_markers: bool = False
    size_unit: Literal["chars", "tokens"] = "chars"
    encoding_name: str = "cl100k_base"


def _make_length_fn(config: ChunkingConfig) -> Callable[[str], int]:
    """Return a function that measures string length according to *config*.

    When ``size_unit`` is ``"chars"`` the built-in :func:`len` is returned.
    When ``"tokens"``, the function uses *tiktoken* with the requested
    ``encoding_name``.  If *tiktoken* is not installed a warning is logged and
    the function silently falls back to character counting.
    """
    if config.size_unit == "tokens":
        try:
            import tiktoken  # lazy import – optional dependency
        except ImportError:
            logger.warning(
                "tiktoken is not installed; falling back to character-based "
                "chunk sizing.  Install it with:  pip install tiktoken"
            )
            return len

        enc = tiktoken.get_encoding(config.encoding_name)
        return lambda text: len(enc.encode(text))

    return len


def chunk_content(
    json_content: List[Dict[str, Any]],
    config: Optional[ChunkingConfig] = None,
) -> List[Chunk]:
    """Split structured document content into section-aware chunks.

    Args:
        json_content: The ``json_content`` list from a ``ProcessedDocument``.
        config: Chunking parameters.  Uses defaults when ``None``.

    Returns:
        Ordered list of ``Chunk`` objects.
    """
    if not json_content:
        return []

    if config is None:
        config = ChunkingConfig()

    length_fn = _make_length_fn(config)

    # 0. Separate footnote items from body content
    body_items: List[Dict[str, Any]] = []
    footnote_map: Dict[str, str] = {}  # id -> definition text
    for item in json_content:
        if item.get("type") == "text:footnote":
            content_text = item.get("content", "")
            import re as _re
            m = _re.match(r'^\[\^(\w+)\]:\s*(.+)', content_text)
            if m:
                footnote_map[m.group(1)] = content_text
            else:
                footnote_map[content_text[:20]] = content_text
        else:
            body_items.append(item)

    # 1. Group items into sections based on headings
    sections = _group_into_sections(body_items, config)

    # 2. Convert sections into chunks, splitting large ones
    chunks: List[Chunk] = []
    for section in sections:
        section_chunks = _section_to_chunks(section, config, length_fn)
        chunks.extend(section_chunks)

    # 3. Add overlap between consecutive chunks
    if config.overlap > 0 and len(chunks) > 1:
        _apply_overlap(chunks, config, length_fn)

    # 4. Attach footnotes to referencing chunks
    if footnote_map:
        _attach_footnotes(chunks, footnote_map)

    # 5. Assign sequential indices
    for idx, chunk in enumerate(chunks):
        chunk.chunk_index = idx

    return chunks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _heading_level(item_type: str) -> int:
    """Return heading level (1 for title, 2 for section) or 0 for non-heading."""
    if item_type == "text:title":
        return 1
    if item_type == "text:section":
        return 2
    return 0


def _item_to_markdown(item: Dict[str, Any], config: ChunkingConfig) -> str:
    """Render a single content item as markdown text."""
    t = item.get("type", "")
    c = item.get("content", "")

    if t == "text:title":
        return f"# {c}"
    if t == "text:section":
        return f"## {c}"
    if t == "text:list":
        return c
    if t == "text:caption":
        return f"*{c}*"
    if t in ("text:normal", "text:footnote"):
        return c
    if t == "table":
        return c
    if t == "image":
        return f"![Image](data:image/png;base64,{c})"
    if t == "text:image_description":
        return f"```\n<ocr_result>\n{c}\n</ocr_result>\n```"
    # Skip header/footer items (deduped content)
    if t in ("text:header", "text:footer"):
        return ""
    # Fallback: return raw content
    return c


@dataclass
class _Section:
    """Internal grouping of items under a heading."""
    title: Optional[str]
    hierarchy: List[str]
    items: List[Dict[str, Any]]


def _group_into_sections(
    json_content: List[Dict[str, Any]],
    config: ChunkingConfig,
) -> List[_Section]:
    """Walk items and split on headings up to ``split_on_heading_level``."""
    sections: List[_Section] = []
    current_hierarchy: List[str] = []
    current_items: List[Dict[str, Any]] = []
    current_title: Optional[str] = None

    for item in json_content:
        level = _heading_level(item.get("type", ""))
        if level > 0 and level <= config.split_on_heading_level:
            # Flush previous section
            if current_items:
                sections.append(_Section(
                    title=current_title,
                    hierarchy=list(current_hierarchy),
                    items=current_items,
                ))
            # Update hierarchy
            title_text = item.get("content", "")
            if level == 1:
                current_hierarchy = [title_text]
            elif level == 2:
                # Keep level-1 ancestor if present
                current_hierarchy = current_hierarchy[:1] + [title_text]
            current_title = title_text
            current_items = [item]
        else:
            current_items.append(item)

    # Flush last section
    if current_items:
        sections.append(_Section(
            title=current_title,
            hierarchy=list(current_hierarchy),
            items=current_items,
        ))

    return sections


def _section_to_chunks(
    section: _Section,
    config: ChunkingConfig,
    length_fn: Callable[[str], int] = len,
) -> List[Chunk]:
    """Convert a section into one or more chunks, respecting size limits."""
    # Render all items
    rendered: List[str] = []
    for item in section.items:
        text = _item_to_markdown(item, config)
        if text:
            rendered.append(text)

    if not rendered:
        return []

    full_text = "\n\n".join(rendered)

    # Collect metadata
    pages = [item.get("page") for item in section.items if item.get("page") is not None]
    types = list({item.get("type", "") for item in section.items})

    # If it fits, return single chunk
    if length_fn(full_text) <= config.max_chunk_size:
        return [Chunk(
            content=full_text,
            section_title=section.title,
            section_hierarchy=list(section.hierarchy),
            page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None,
            content_types=types,
        )]

    # Need to split — split at item boundaries
    chunks: List[Chunk] = []
    current_parts: List[str] = []
    current_len = 0
    current_pages: List[int] = []
    current_types: set = set()
    current_items_slice: List[Dict[str, Any]] = []

    for i, item in enumerate(section.items):
        text = _item_to_markdown(item, config)
        if not text:
            continue

        item_len = length_fn(text) + (length_fn("\n\n") if current_parts else 0)  # account for separator
        is_table = item.get("type") == "table"

        # If adding this item would exceed limit and we have content already
        if current_parts and (current_len + item_len > config.max_chunk_size):
            # But if it's a table and keep_tables_whole, let it exceed
            if is_table and config.keep_tables_whole:
                pass  # fall through to append
            else:
                # Flush current chunk
                chunks.append(Chunk(
                    content="\n\n".join(current_parts),
                    section_title=section.title,
                    section_hierarchy=list(section.hierarchy),
                    page_start=min(current_pages) if current_pages else None,
                    page_end=max(current_pages) if current_pages else None,
                    content_types=list(current_types),
                ))
                current_parts = []
                current_len = 0
                current_pages = []
                current_types = set()

        current_parts.append(text)
        current_len += item_len
        current_types.add(item.get("type", ""))
        page = item.get("page")
        if page is not None:
            current_pages.append(page)

    # Flush remaining
    if current_parts:
        chunks.append(Chunk(
            content="\n\n".join(current_parts),
            section_title=section.title,
            section_hierarchy=list(section.hierarchy),
            page_start=min(current_pages) if current_pages else None,
            page_end=max(current_pages) if current_pages else None,
            content_types=list(current_types),
        ))

    return chunks


def _attach_footnotes(chunks: List[Chunk], footnote_map: Dict[str, str]) -> None:
    """Append footnote definitions to chunks that reference them."""
    import re as _re
    used: set = set()
    for chunk in chunks:
        refs = _re.findall(r'\[\^(\w+)\](?!:)', chunk.content)
        defs_to_add = []
        for ref_id in refs:
            if ref_id in footnote_map and ref_id not in used:
                defs_to_add.append(footnote_map[ref_id])
                used.add(ref_id)
        if defs_to_add:
            chunk.content = chunk.content + "\n\n" + "\n".join(defs_to_add)

    # Attach unreferenced footnotes to the last chunk
    remaining = [v for k, v in footnote_map.items() if k not in used]
    if remaining and chunks:
        chunks[-1].content = chunks[-1].content + "\n\n" + "\n".join(remaining)


def _tail_by_units(text: str, config: ChunkingConfig) -> str:
    """Return the trailing portion of *text* spanning *config.overlap* units.

    In ``"chars"`` mode this is a simple character slice.  In ``"tokens"`` mode
    the text is encoded with *tiktoken*, the last *overlap* tokens are taken,
    and decoded back to a string.  Falls back to character slicing when
    *tiktoken* is unavailable.
    """
    if config.size_unit == "tokens":
        try:
            import tiktoken

            enc = tiktoken.get_encoding(config.encoding_name)
            tokens = enc.encode(text)
            overlap_tokens = tokens[-config.overlap:]
            return enc.decode(overlap_tokens)
        except ImportError:
            pass  # fall through to char-based slicing
    return text[-config.overlap:]


def _apply_overlap(
    chunks: List[Chunk],
    config: ChunkingConfig,
    length_fn: Callable[[str], int] = len,
) -> None:
    """Prepend trailing text from the previous chunk as overlap."""
    for i in range(1, len(chunks)):
        prev_text = chunks[i - 1].content
        if length_fn(prev_text) <= config.overlap:
            overlap_text = prev_text
        else:
            overlap_text = _tail_by_units(prev_text, config)
            # Try to break at a word boundary
            space_idx = overlap_text.find(" ")
            if space_idx != -1:
                overlap_text = overlap_text[space_idx + 1:]
        chunks[i].content = overlap_text + "\n\n" + chunks[i].content
