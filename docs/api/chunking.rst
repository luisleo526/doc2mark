Chunking
========

The chunking module splits structured document content into overlapping,
section-aware pieces suitable for retrieval-augmented generation (RAG)
pipelines.  It operates on the ``json_content`` representation produced by
:func:`doc2mark.load` (with ``output_format="json"``), **not** on raw
Markdown strings, so that section boundaries, page numbers, and content
types are preserved as metadata on every chunk.

Two sizing modes are supported: character-based (the default) and
token-based.  Token mode relies on `tiktoken <https://github.com/openai/tiktoken>`_
and falls back to character counting gracefully when the library is not
installed.  Install the optional dependency with:

.. code-block:: bash

   pip install doc2mark[tokenizers]


.. _chunking-config:

ChunkingConfig
--------------

Configuration dataclass that controls how content is split.

.. code-block:: python

   from doc2mark import ChunkingConfig

   cfg = ChunkingConfig(
       max_chunk_size=1500,
       overlap=200,
       split_on_heading_level=2,
       keep_tables_whole=True,
       include_page_markers=False,
       size_unit="chars",
       encoding_name="cl100k_base",
   )

Fields
~~~~~~

``max_chunk_size`` : ``int``, default ``1500``
    Maximum size of a single chunk, measured in the units selected by
    ``size_unit``.

``overlap`` : ``int``, default ``200``
    Number of trailing units from the previous chunk prepended to the next
    chunk to provide context continuity.

``split_on_heading_level`` : ``int``, default ``2``
    Heading depth at which the document is divided into sections before
    chunking.  Level 1 corresponds to ``text:title`` items and level 2 to
    ``text:section`` items in the JSON content.

``keep_tables_whole`` : ``bool``, default ``True``
    When ``True``, a table that would push a chunk over ``max_chunk_size``
    is kept intact in a single chunk rather than being split across two.

``include_page_markers`` : ``bool``, default ``False``
    Reserved for future use.

``size_unit`` : ``Literal["chars", "tokens"]``, default ``"chars"``
    Unit of measurement for ``max_chunk_size`` and ``overlap``.

    * ``"chars"`` -- uses Python's built-in ``len()`` (fast, no extra
      dependencies).
    * ``"tokens"`` -- uses *tiktoken* with the encoding given by
      ``encoding_name``.  If *tiktoken* is not installed, a warning is
      logged and the function silently falls back to character counting.

``encoding_name`` : ``str``, default ``"cl100k_base"``
    The *tiktoken* encoding to use when ``size_unit="tokens"``.  Common
    values include ``"cl100k_base"`` (GPT-4 / text-embedding-ada-002) and
    ``"o200k_base"`` (GPT-4o).

.. autoclass:: doc2mark.ChunkingConfig
   :members:
   :show-inheritance:


.. _chunk-dataclass:

Chunk
-----

Immutable result object returned by :func:`~doc2mark.chunk_content`.  Each
chunk carries the rendered Markdown text together with the section context
and page span that produced it.

Fields
~~~~~~

``content`` : ``str``
    The Markdown text of this chunk.

``section_title`` : ``Optional[str]``, default ``None``
    Title of the section this chunk belongs to, or ``None`` for content
    that precedes the first heading.

``section_hierarchy`` : ``List[str]``
    Ordered list of ancestor headings, e.g.
    ``["Document Title", "Chapter 2"]``.

``page_start`` : ``Optional[int]``, default ``None``
    First page number spanned by items in this chunk (if the source format
    provides page information).

``page_end`` : ``Optional[int]``, default ``None``
    Last page number spanned by items in this chunk.

``content_types`` : ``List[str]``
    Set of item type strings present in this chunk (e.g.
    ``["text:normal", "table"]``).

``chunk_index`` : ``int``, default ``0``
    Zero-based position of this chunk in the sequence returned by
    :func:`~doc2mark.chunk_content`.

.. autoclass:: doc2mark.Chunk
   :members:
   :show-inheritance:


.. _chunk-content-fn:

chunk_content
-------------

The main entry point for chunking.  It accepts the structured
``json_content`` list (a ``List[Dict]`` of content blocks) from a
:class:`~doc2mark.ProcessedDocument` and returns an ordered list of
:class:`~doc2mark.Chunk` objects.

Signature
~~~~~~~~~

.. code-block:: python

   def chunk_content(
       json_content: List[Dict[str, Any]],
       config: Optional[ChunkingConfig] = None,
   ) -> List[Chunk]: ...

Parameters
~~~~~~~~~~

``json_content`` : ``List[Dict[str, Any]]``
    The structured content blocks obtained from
    ``ProcessedDocument.json_content``.  Each dictionary has at least
    ``"type"`` and ``"content"`` keys.  Pass the value returned by
    ``load(..., output_format="json").json_content``.

``config`` : ``Optional[ChunkingConfig]``, default ``None``
    Chunking parameters.  When ``None``, a :class:`~doc2mark.ChunkingConfig`
    with default values is used.

Returns
~~~~~~~

``List[Chunk]``
    An ordered list of :class:`~doc2mark.Chunk` objects with sequential
    ``chunk_index`` values starting from 0.

Notes
~~~~~

* The function groups items into sections by scanning for heading items
  (``text:title`` at level 1 and ``text:section`` at level 2) up to the
  depth set by ``split_on_heading_level``.
* Within each section, items are accumulated until the next item would
  exceed ``max_chunk_size``; the accumulated items are then flushed as a
  chunk.
* When ``overlap`` is greater than zero, the trailing portion of each
  chunk is prepended to the next chunk (breaking at a word boundary when
  possible).  In token mode the overlap is computed by encoding, slicing,
  and decoding via *tiktoken*.
* Footnotes (``text:footnote`` items) are separated during processing and
  appended to the chunks that reference them.  Unreferenced footnotes are
  attached to the last chunk.
* An empty ``json_content`` list returns an empty list.
* You can also call :meth:`ProcessedDocument.get_chunks(config)
  <doc2mark.ProcessedDocument.get_chunks>` as a convenience shortcut that
  delegates to this function.

Example -- character mode (default)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from doc2mark import load, chunk_content, ChunkingConfig

   result = load("report.pdf", output_format="json")

   chunks = chunk_content(
       result.json_content,
       ChunkingConfig(max_chunk_size=1000, overlap=150),
   )

   for chunk in chunks:
       print(f"[{chunk.chunk_index}] {chunk.section_title!r}  "
             f"(pages {chunk.page_start}-{chunk.page_end})")
       print(chunk.content[:120], "...")
       print()

Example -- token mode
~~~~~~~~~~~~~~~~~~~~~

Token mode measures sizes in *tiktoken* tokens instead of characters.
Install the optional dependency first (``pip install doc2mark[tokenizers]``).

.. code-block:: python

   from doc2mark import load, chunk_content, ChunkingConfig

   result = load("report.pdf", output_format="json")

   cfg = ChunkingConfig(
       max_chunk_size=512,
       overlap=64,
       size_unit="tokens",
       encoding_name="cl100k_base",
   )
   chunks = chunk_content(result.json_content, cfg)

   print(f"{len(chunks)} chunks produced in token mode")

.. autofunction:: doc2mark.chunk_content
