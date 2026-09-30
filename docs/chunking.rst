Chunking and token usage
========================

Chunks
------

:func:`~doc2mark.chunk_content` turns a document's ``json_content`` into
:class:`~doc2mark.Chunk` objects; :meth:`ProcessedDocument.get_chunks()
<doc2mark.ProcessedDocument.get_chunks>` calls it for you.

.. code-block:: python

   from doc2mark import ChunkingConfig, chunk_content, load

   result = load("report.pdf")
   chunks = chunk_content(result.json_content, ChunkingConfig(max_chunk_size=800, overlap=100))
   for chunk in chunks:
       print(chunk.chunk_index, chunk.section_title, chunk.page_start, chunk.page_end,
             len(chunk.content), sorted(chunk.content_types))

``json_content`` is filled for every output format by the PDF, Word, Excel, PowerPoint and image
processors. The other formats (text, CSV, JSON, HTML, XML, Markdown, e-mail) leave it empty
with the default Markdown output; ``get_chunks()`` then returns the whole ``content`` as one chunk,
and ``output_format="json"`` gives them a single ``text:normal`` item.

How a document is split
~~~~~~~~~~~~~~~~~~~~~~~~

1. **Sections.** A ``text:title`` item (level 1) or ``text:section`` item (level 2) starts a new
   section when its level is at most ``split_on_heading_level`` (default 2: titles and sections;
   1: titles only). Every ``text:section`` item counts as level 2, whatever its own ``level``;
   in chunk text a title is written ``# ...`` and every section heading ``## ...``.
2. **Packing.** The items of a section are rendered to Markdown and packed into a chunk until the
   next item would pass ``max_chunk_size``. Chunks are cut only between items: a single paragraph
   longer than ``max_chunk_size`` is one chunk of its own, and a table is never cut. With
   ``keep_tables_whole=True`` (the default) a table that does not fit is still added to the
   current chunk; with ``False`` it starts a new one.
3. **Overlap.** Every chunk after the first starts with the last ``overlap`` units of the chunk
   before it (from its first space on), across section boundaries too. Overlap is added after
   packing, so a chunk can be up to ``overlap`` longer than ``max_chunk_size``.
4. **Footnotes.** ``text:footnote`` items (``[^1]: ...``) are taken out of the flow and appended
   to the chunk that first references them as ``[^1]``; unreferenced ones go to the last chunk.
   PDF body text marks footnotes as superscripts (``^1^``), so PDF footnotes end up in the last
   chunk.

Items of type ``text:header`` and ``text:footer`` (the repeated copies of a PDF's running headers
and footers) are left out; a Word header or footer is written once and chunked like other text. An ``image`` item, which is there only with ``extract_images=True`` and
no OCR, is written as a ``data:`` URI Markdown image: leave image extraction off (or drop those
items) when you index text. ``include_page_markers`` is accepted but has no effect.

``page_start`` / ``page_end`` come from the items' ``page`` numbers (pages for PDF, slides for
PowerPoint, sheets for Excel); ``content_types`` is the set of item types in the chunk (it can
name ``text:header`` / ``text:footer`` although their text is left out). ``section_hierarchy`` is
the current title and section; in a document without a title the first section stays in it as
if it were one.

Sizes in tokens
~~~~~~~~~~~~~~~

.. code-block:: python

   from doc2mark import ChunkingConfig, load

   config = ChunkingConfig(max_chunk_size=512, overlap=64, size_unit="tokens", encoding_name="cl100k_base")
   chunks = load("report.pdf").get_chunks(config)
   print(len(chunks), "chunks of at most about 512 tokens")

``size_unit="tokens"`` counts with tiktoken (``pip install "doc2mark[tokenizers]"``). tiktoken
downloads an encoding's data the first time it is used, so the first call needs network access
(or a pre-filled ``TIKTOKEN_CACHE_DIR``). Without tiktoken installed a warning is logged and sizes
are counted in characters.

OCR token usage
---------------

When an LLM provider OCRs something during :meth:`~doc2mark.UnifiedDocumentLoader.load`, the
provider's token counts for that document are added up in ``metadata.extra["token_usage"]``:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="openai")
   result = loader.load("scan.pdf", ocr_images=True)
   print(result.metadata.extra["token_usage"])   # {'input_tokens': ..., 'output_tokens': ..., 'total_tokens': ...}

Only fresh calls count: an OCR cache hit and a result replayed from ``cache_dir`` cost nothing
(a replayed document carries the original count as ``token_usage_cached`` instead). Tesseract
reports no tokens, and a document that needed no OCR has no ``token_usage`` key. Each
:class:`~doc2mark.ocr.OCRResult` from the :class:`~doc2mark.OCR` facade carries its own
``metadata["token_usage"]``.
