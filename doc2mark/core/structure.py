"""The tables and sections of a processed document, read from its structured content items."""

from typing import Any, Dict, List, Tuple


def tables_and_sections(json_content: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """``(tables, sections)`` of a document's ``json_content``, in document order.

    A table is one ``table`` item: ``{"page", "format", "content"}``, ``content`` being the table as the
    Markdown output has it (``format`` ``"html"`` or ``"markdown"``). A section is one heading item
    (``text:title``, ``text:section``): ``{"level", "title", "page"}``, the level being the one the Markdown
    heading has (a title is 1, a section without a level 2) and the title the heading as the Markdown shows it,
    on one line, with its number (``1.2 Scope``) when the document numbers its headings.
    """
    tables: List[Dict[str, Any]] = []
    sections: List[Dict[str, Any]] = []
    for item in json_content:
        kind = item.get("type")
        content = item.get("content") or ""
        if kind == "table":
            tables.append({
                "page": item.get("page"),
                "format": "html" if content.lstrip().startswith("<") else "markdown",
                "content": content,
            })
        elif kind in ("text:title", "text:section"):
            title = " ".join(part.strip() for part in content.split("\n") if part.strip())
            if title:
                if item.get("marker"):
                    title = f"{item['marker']} {title}"
                sections.append({
                    "level": int(item.get("level") or (1 if kind == "text:title" else 2)),
                    "title": title,
                    "page": item.get("page"),
                })
    return tables, sections
