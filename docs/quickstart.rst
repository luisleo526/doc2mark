Quickstart
==========

Install the package:

.. code-block:: bash

   pip install doc2mark

Load a document:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader()
   result = loader.load("document.pdf")

   print(result.content)
   print(result.metadata.filename)

Use the convenience function for one-off conversions:

.. code-block:: python

   from doc2mark import load

   result = load("report.docx")
   markdown = result.content

Structured Output
-----------------

Every load returns a :class:`doc2mark.ProcessedDocument`.

.. code-block:: python

   payload = result.to_dict()
   print(payload["metadata"]["format"])

Batch Processing
----------------

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader()
   results = loader.batch_process(
       input_dir="documents",
       output_dir="converted",
       recursive=True,
       save_files=True,
   )

Document Cache
--------------

Set ``cache_dir`` to persist processed documents between calls:

.. code-block:: python

   loader = UnifiedDocumentLoader(cache_dir=".doc2mark-cache")
   result = loader.load("report.pdf")

Chunking for RAG
----------------

Split structured output into section-aware chunks for retrieval-augmented
generation pipelines:

.. code-block:: python

   from doc2mark import load, chunk_content, ChunkingConfig

   result = load("report.pdf", output_format="json")

   config = ChunkingConfig(
       max_chunk_size=1500,       # max characters per chunk
       overlap=200,               # character overlap between consecutive chunks
       split_on_heading_level=2,  # split on h1 and h2 headings
       keep_tables_whole=True,    # avoid splitting tables across chunks
       include_page_markers=False,
   )

   chunks = chunk_content(result.json_content, config)

   for chunk in chunks:
       print(chunk.chunk_index, chunk.section_title, len(chunk.content))

Each :class:`~doc2mark.Chunk` carries ``section_title``,
``section_hierarchy``, ``page_start``, ``page_end``, ``content_types``, and
``chunk_index`` metadata.
