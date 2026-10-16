"""Saving the pictures of a converted document next to its Markdown (batch processing).

The processors hand pictures over in different shapes: Word, PowerPoint and Excel files as bytes
(``{"data": <bytes>}``), image files as base64 text under ``"content"``, and PDF files as base64 text under
``"data"`` (``{"data": <str>, "type": "base64"}``; ``{"text": ..., "type": "ocr"}`` for a picture read by OCR).
``batch_process`` and ``batch_process_files`` save them with ``UnifiedDocumentLoader._save_result``. The PDF shape
used to be written as text into a binary file: that raised ``TypeError`` after the Markdown file was written, so
every PDF with a picture was reported as ``failed``.
"""

import base64
import shutil
from pathlib import Path

import pytest

from doc2mark.core.base import DocumentFormat, DocumentMetadata, OutputFormat, ProcessedDocument
from doc2mark.core.loader import UnifiedDocumentLoader


@pytest.fixture
def loader():
    """Loader with OCR disabled so the tests never touch a network or an API."""
    return UnifiedDocumentLoader(ocr_provider=None)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _document(images):
    return ProcessedDocument(
        content="# Title\n\nBody\n",
        metadata=DocumentMetadata(filename="report.pdf", format=DocumentFormat.PDF, size_bytes=1),
        images=images,
    )


def _saved_pictures(images_dir: Path):
    """The bytes of every picture file in ``images_dir``, in a fixed order."""
    return sorted(path.read_bytes() for path in images_dir.glob("image_*.png"))


def test_pdf_pictures_given_as_base64_text_are_saved_as_their_bytes(loader, tmp_path):
    """Breaks if ``_save_result`` writes the base64 text of a PDF picture (a ``str``) into a binary file."""
    document = _document(
        [
            {"data": _b64(b"first picture"), "type": "base64"},
            {"data": _b64(b"second picture"), "type": "base64"},
        ]
    )

    files = loader._save_result(document, tmp_path / "report", OutputFormat.MARKDOWN)

    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "# Title\n\nBody\n"
    assert _saved_pictures(tmp_path / "report_images") == [b"first picture", b"second picture"]
    assert len(files) == 3
    assert all(Path(path).is_file() for path in files)


def test_every_picture_shape_is_saved_and_an_ocr_answer_is_not_a_picture(loader, tmp_path):
    """Breaks if a shape is not decoded the way its processor writes it, or if an OCR answer becomes a file."""
    document = _document(
        [
            {"data": _b64(b"pdf picture"), "type": "base64"},
            {"text": "text read by OCR", "type": "ocr"},
            {"data": b"office picture", "page": 2},
            {"data": bytearray(b"bytearray picture")},
            {"type": "image", "content": _b64(b"image file"), "format": "png"},
            b"bare bytes",
            _b64(b"bare text"),
        ]
    )

    loader._save_result(document, tmp_path / "mixed", OutputFormat.MARKDOWN)

    images_dir = tmp_path / "mixed_images"
    assert _saved_pictures(images_dir) == sorted(
        [b"pdf picture", b"office picture", b"bytearray picture", b"image file", b"bare bytes", b"bare text"]
    )
    assert len(list(images_dir.iterdir())) == 6


@pytest.mark.parametrize("entry", ["batch_process", "batch_process_files"])
def test_a_pdf_with_pictures_is_reported_as_converted(entry, loader, sample_documents_dir, tmp_path):
    """Breaks if saving the pictures of a PDF raises: the file is then reported ``failed`` although its
    Markdown was written (a known issue of docs/loading.rst before this fix)."""
    pdf = sample_documents_dir / "sample_pdf.pdf"
    assert pdf.is_file(), "tests rely on sample_documents/sample_pdf.pdf"
    pictures = [
        base64.b64decode(item["data"])
        for item in loader.load(pdf, extract_images=True).images or []
        if item.get("type") == "base64" and item.get("data")
    ]
    assert pictures, "sample_pdf.pdf is expected to hold at least one picture"

    out = tmp_path / "out"
    if entry == "batch_process":
        source = tmp_path / "in"
        source.mkdir()
        shutil.copy(pdf, source / pdf.name)
        results = loader.batch_process(source, output_dir=out, extract_images=True, show_progress=False)
    else:
        results = loader.batch_process_files([pdf], output_dir=out, extract_images=True, show_progress=False)

    (info,) = results.values()
    assert info["status"] == "success", info.get("error")
    assert (out / "sample_pdf.md").is_file()
    assert _saved_pictures(out / "sample_pdf_images") == sorted(pictures)
    assert len(info["output_files"]) == 1 + len(pictures)
    assert all(Path(path).is_file() for path in info["output_files"])
