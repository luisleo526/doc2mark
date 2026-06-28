"""Shared internal content model used by the extraction pipelines."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class SimpleContent:
    """A single positioned content item assembled by a pipeline before rendering.

    ``type`` is one of ``'text:title'``, ``'text:section'``, ``'text:normal'``,
    ``'text:list'``, ``'text:caption'``, ``'text:image_description'``,
    ``'text:footnote'``, ``'table'``, or ``'image'``. ``content`` is the markdown
    text / markdown-or-HTML table / base64 image data. ``page`` is the page (or
    slide / sheet) number; ``position_y`` orders items within a page; ``mime_type``
    is set for image content (PDF path) and ``None`` elsewhere.
    """
    type: str
    content: str
    page: int
    position_y: float
    mime_type: Optional[str] = None
