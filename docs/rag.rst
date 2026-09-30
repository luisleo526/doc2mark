A minimal RAG pipeline
======================

Retrieval-augmented generation needs three things from a document converter: clean text, pieces
of it small enough to embed, and enough context on each piece to cite it. This page builds that
pipeline with doc2mark in a few lines. The search at the end is a keyword toy so the example runs
anywhere; put your embedding model and vector store in its place.

Convert and chunk
-----------------

.. code-block:: python

   from doc2mark import ChunkingConfig, UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider=None)       # no OCR: nothing leaves the machine
   config = ChunkingConfig(max_chunk_size=1200, overlap=150)

   records = []
   for path in ["report.pdf", "report.docx", "slides.pptx"]:
       result = loader.load(path)
       for chunk in result.get_chunks(config):
           records.append({
               "id": f"{path}#{chunk.chunk_index}",
               "text": chunk.content,
               "source": result.metadata.filename,
               "section": " > ".join(chunk.section_hierarchy),
               "pages": (chunk.page_start, chunk.page_end),
           })

   print(len(records), "chunks")
   print(records[0]["id"], "|", records[0]["section"], "|", records[0]["pages"])

   def search(query, k=3):
       """Keyword overlap: replace with embeddings and a vector store."""
       words = set(query.lower().split())
       return sorted(records, key=lambda r: -len(words & set(r["text"].lower().split())))[:k]

   for hit in search("structured table with merged cells"):
       print(hit["id"], hit["pages"], hit["section"])

:meth:`ProcessedDocument.get_chunks() <doc2mark.ProcessedDocument.get_chunks>` splits the
document at its title and section headings, packs the items of a section into chunks of at most
``max_chunk_size`` characters (tables are never split) and prepends the end of the previous
chunk as overlap. Each :class:`~doc2mark.Chunk` carries its section path and page span, which
is what you store next to the vector. :doc:`chunking` describes the options, token-based sizes
and the edge cases.

What the converter already did for retrieval
--------------------------------------------

- **Tables stay tables.** Merged cells are kept as ``rowspan`` / ``colspan`` in an HTML table
  inside the Markdown, and a line break in a cell is ``<br>``, so a row never turns into a run
  of loose words (:doc:`tables`).
- **Page furniture is thinned, not guessed away.** Bare page numbers are dropped; a running
  header or footer keeps its first copy and its repeats stay out of ``content`` and out of the
  chunks (:doc:`pdf`).
- **Hidden text is not indexed.** Invisible text over nothing the page shows (a known prompt
  injection route) is left out and reported in ``metadata.extra["hidden_text"]``.
- **Reading order follows the layout**: multi-column pages are read column by column.

Scanned documents
-----------------

Turn OCR on for documents that may contain scans or text in pictures. doc2mark OCRs only the
pages and pictures that need it, and records what it did:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="tesseract")
   result = loader.load("scan.pdf", ocr_images=True)
   chunks = result.get_chunks()
   print(len(chunks), chunks[0].content[:80])
   print(result.metadata.extra.get("ocr_issues"))   # None: every image was read

With an LLM provider (``ocr_provider="openai"`` or ``"vertex_ai"``) the loader also adds the
provider's token counts to ``metadata.extra["token_usage"]``, for cost accounting. Check
``metadata.extra["ocr_issues"]`` before indexing: it lists images the provider refused, could not
read, or answered with withheld sample values (:doc:`output`).
