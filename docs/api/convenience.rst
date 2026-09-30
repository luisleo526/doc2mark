Convenience functions
=====================

Module-level shortcuts that create a :class:`~doc2mark.UnifiedDocumentLoader` for one call. The
loader gets ``ocr_provider`` (default ``"openai"``), ``api_key`` and ``ocr_cache``. Every other
keyword argument goes where it belongs: the options of
:meth:`~doc2mark.UnifiedDocumentLoader.load` (or of the batch method) to that call, all other
loader settings (``table_style``, ``cache_dir``, ``model``, ``judge``, ...) to the loader; a name
that is neither raises ``TypeError`` (:doc:`/loading`).

.. list-table::
   :header-rows: 1
   :widths: 32 68

   * - Function
     - Does
   * - :func:`doc2mark.load`
     - Convert one file, return a :class:`~doc2mark.ProcessedDocument`.
   * - :func:`doc2mark.document_to_markdown`
     - Convert one file, return the Markdown string; with ``output_path`` also write it (parent
       folders are created) and print ``Markdown saved to: ...`` unless ``show_progress=False``.
   * - :func:`doc2mark.batch_convert_to_markdown`
     - :meth:`~doc2mark.UnifiedDocumentLoader.batch_process` with Markdown output and
       ``save_files=True``: without ``output_dir`` the ``.md`` files are written next to the
       inputs.
   * - :func:`doc2mark.batch_process_documents`
     - :meth:`~doc2mark.UnifiedDocumentLoader.batch_process` with every option (output format,
       ``save_files``).
   * - :func:`doc2mark.batch_process_files`
     - :meth:`~doc2mark.UnifiedDocumentLoader.batch_process_files` for an explicit list of paths.
       Importable from ``doc2mark`` but not listed in ``doc2mark.__all__``.

The batch functions return the per-file result dictionaries described in :doc:`/loading`
(``{"status": "success" | "failed", ...}``).
``ocr_images=True`` does not need ``extract_images=True``: with an OCR provider it turns
extraction on itself. Pictures of Word, PowerPoint and Excel files are returned as bytes, not
base64 (:doc:`/output`), and ``ocr_cache`` lives as long as the cache object, not one request
(:doc:`/caching`); the docstrings below predate both.

.. code-block:: python

   from doc2mark import batch_process_files

   results = batch_process_files(
       ["reports/q1.pdf", "reports/q2.pdf", "notes/meeting.docx"],
       output_dir="out",
       ocr_provider=None,
   )
   print({path: info["status"] for path, info in results.items()})

.. autofunction:: doc2mark.load

.. autofunction:: doc2mark.document_to_markdown

.. autofunction:: doc2mark.batch_convert_to_markdown

.. autofunction:: doc2mark.batch_process_documents

.. autofunction:: doc2mark.batch_process_files
