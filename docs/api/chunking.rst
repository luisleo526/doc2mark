Chunking
========

See :doc:`/chunking` for how a document is split (sections, packing, overlap, footnotes) and
:doc:`/rag` for a pipeline.

.. autofunction:: doc2mark.chunk_content

.. autoclass:: doc2mark.ChunkingConfig
   :members:

   Fields (defaults): ``max_chunk_size`` (1500), ``overlap`` (200), ``split_on_heading_level``
   (2), ``keep_tables_whole`` (True), ``include_page_markers`` (False, no effect),
   ``size_unit`` (``"chars"`` or ``"tokens"``), ``encoding_name`` (``"cl100k_base"``, used with
   ``"tokens"``).

.. autoclass:: doc2mark.Chunk
   :members:

   Fields: ``content`` (Markdown), ``section_title``, ``section_hierarchy`` (the title and
   section headings above it), ``page_start``, ``page_end``, ``content_types`` (item types in
   the chunk), ``chunk_index`` (from 0).
