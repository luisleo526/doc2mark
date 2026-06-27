Convenience Functions
=====================

The ``doc2mark`` package exposes a set of module-level helper functions that
wrap :class:`~doc2mark.UnifiedDocumentLoader` so you can convert documents in a
single call without instantiating the loader yourself.  Use them for scripts,
notebooks, and quick one-off conversions; reach for the loader directly when you
need to reuse a single instance across many files or customise format handlers.

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Function
     - When to use
   * - :func:`load`
     - Load one file and get a :class:`~doc2mark.ProcessedDocument`.
   * - :func:`document_to_markdown`
     - Convert one file to Markdown (optionally save to disk).
   * - :func:`batch_convert_to_markdown`
     - Convert every supported file in a directory to Markdown.
   * - :func:`batch_process_documents`
     - Like ``batch_convert_to_markdown`` but with full control over output
       format and file-saving behaviour.
   * - :func:`batch_process_files`
     - Process an explicit list of files instead of scanning a directory.


Single-document helpers
-----------------------

load
~~~~

.. code-block:: python

   from doc2mark import load

   result = load("report.pdf")
   print(result.content[:200])

   # With image extraction and OCR
   result = load(
       "slides.pptx",
       extract_images=True,
       ocr_images=True,
       ocr_provider="openai",
       api_key="sk-...",
   )

**Signature**

.. code-block:: python

   def load(
       file_path: Union[str, Path],
       output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
       extract_images: bool = False,
       ocr_images: bool = False,
       ocr_provider: Union[str, OCRProvider] = "openai",
       api_key: Optional[str] = None,
       ocr_cache: Optional[OCRCache] = None,
       **kwargs: Any,
   ) -> ProcessedDocument: ...

**Parameters**

``file_path``
   Path to the document to process.

``output_format``
   Desired output format.  Accepts a string (``"markdown"``, ``"html"``,
   ``"text"``) or an :class:`~doc2mark.OutputFormat` member.
   Defaults to ``OutputFormat.MARKDOWN``.

``extract_images``
   When ``True``, images embedded in the document are extracted as base64 data.
   Defaults to ``False``.

``ocr_images``
   When ``True`` **and** ``extract_images`` is also ``True``, extracted images
   are sent to the configured OCR provider for text recognition.
   Defaults to ``False``.

``ocr_provider``
   The OCR backend to use.  Accepts ``"openai"`` or ``"vertex_ai"`` (or an
   :class:`~doc2mark.OCRProvider` member).  Defaults to ``"openai"``.

``api_key``
   API key for the chosen OCR provider.  When ``None`` the provider falls back
   to its own environment-variable lookup.

``ocr_cache``
   An optional :class:`~doc2mark.OCRCache` instance for request-scoped caching
   of OCR results.

``**kwargs``
   Forwarded to :meth:`UnifiedDocumentLoader.load()
   <doc2mark.UnifiedDocumentLoader.load>`.

**Returns**

A :class:`~doc2mark.ProcessedDocument` containing the converted content and
metadata.

.. autofunction:: doc2mark.load
   :noindex:


document_to_markdown
~~~~~~~~~~~~~~~~~~~~

A backward-compatible helper that converts a single file to Markdown and
optionally writes the result to disk.

.. code-block:: python

   from doc2mark import document_to_markdown

   md = document_to_markdown("report.docx", output_path="report.md")

**Signature**

.. code-block:: python

   def document_to_markdown(
       file_path: Union[str, Path],
       output_path: Optional[Union[str, Path]] = None,
       extract_images: bool = False,
       ocr_images: bool = False,
       ocr_provider: Union[str, OCRProvider] = "openai",
       api_key: Optional[str] = None,
       ocr_cache: Optional[OCRCache] = None,
       show_progress: bool = True,
       **kwargs: Any,
   ) -> str: ...

**Parameters**

``file_path``
   Path to the source document.

``output_path``
   If provided, the Markdown string is written to this path (parent directories
   are created automatically).

``extract_images``
   Extract images as base64 data.  Defaults to ``False``.

``ocr_images``
   Run OCR on extracted images (requires ``extract_images=True``).
   Defaults to ``False``.

``ocr_provider``
   OCR backend (``"openai"`` or ``"vertex_ai"``).  Defaults to ``"openai"``.

``api_key``
   API key for the OCR provider.

``ocr_cache``
   Optional :class:`~doc2mark.OCRCache` instance.

``show_progress``
   Print a confirmation message after saving.  Defaults to ``True``.

``**kwargs``
   Forwarded to :meth:`UnifiedDocumentLoader.load()
   <doc2mark.UnifiedDocumentLoader.load>`.

**Returns**

The Markdown content as a plain ``str``.

.. autofunction:: doc2mark.document_to_markdown
   :noindex:


Batch helpers
-------------

batch_convert_to_markdown
~~~~~~~~~~~~~~~~~~~~~~~~~

Scans a directory for supported documents and converts each one to Markdown.
Files are optionally saved to *output_dir* with a ``.md`` extension.

.. code-block:: python

   from doc2mark import batch_convert_to_markdown

   results = batch_convert_to_markdown(
       "incoming/",
       output_dir="converted/",
       recursive=True,
   )
   for path, info in results.items():
       if info.get("success"):
           print(f"{path} -> OK")

**Signature**

.. code-block:: python

   def batch_convert_to_markdown(
       input_dir: Union[str, Path],
       output_dir: Optional[Union[str, Path]] = None,
       extract_images: bool = False,
       ocr_images: bool = False,
       recursive: bool = True,
       ocr_provider: Union[str, OCRProvider] = "openai",
       api_key: Optional[str] = None,
       ocr_cache: Optional[OCRCache] = None,
       show_progress: bool = True,
       **kwargs: Any,
   ) -> Dict[str, Dict[str, Any]]: ...

**Parameters**

``input_dir``
   Directory to scan for documents.

``output_dir``
   Destination directory for the generated Markdown files.  When ``None``,
   files are not saved to disk (results are still returned in the dictionary).

``extract_images``
   Extract images as base64 data.  Defaults to ``False``.

``ocr_images``
   Run OCR on extracted images.  Defaults to ``False``.

``recursive``
   Descend into subdirectories.  Defaults to ``True``.

``ocr_provider``
   OCR backend.  Defaults to ``"openai"``.

``api_key``
   API key for the OCR provider.

``ocr_cache``
   Optional :class:`~doc2mark.OCRCache` instance.

``show_progress``
   Print progress messages during processing.  Defaults to ``True``.

``**kwargs``
   Forwarded to :meth:`UnifiedDocumentLoader.batch_process()
   <doc2mark.UnifiedDocumentLoader.batch_process>`.

**Returns**

A ``dict`` mapping each input file path (as a string) to a result dictionary
containing at minimum a ``"success"`` key.

.. autofunction:: doc2mark.batch_convert_to_markdown
   :noindex:


batch_process_documents
~~~~~~~~~~~~~~~~~~~~~~~

A fully configurable batch processor that adds control over the output format
and whether files are written to disk.  Use this instead of
:func:`batch_convert_to_markdown` when you need HTML or plain-text output, or
want to keep results in memory only.

**Signature**

.. code-block:: python

   def batch_process_documents(
       input_dir: Union[str, Path],
       output_dir: Optional[Union[str, Path]] = None,
       output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
       extract_images: bool = False,
       ocr_images: bool = False,
       recursive: bool = True,
       ocr_provider: Union[str, OCRProvider] = "openai",
       api_key: Optional[str] = None,
       ocr_cache: Optional[OCRCache] = None,
       show_progress: bool = True,
       save_files: bool = True,
       **kwargs: Any,
   ) -> Dict[str, Dict[str, Any]]: ...

**Parameters**

``input_dir``
   Directory to scan for documents.

``output_dir``
   Destination directory.  Ignored when ``save_files`` is ``False``.

``output_format``
   Output format (string or :class:`~doc2mark.OutputFormat`).
   Defaults to ``OutputFormat.MARKDOWN``.

``extract_images``
   Extract images as base64 data.  Defaults to ``False``.

``ocr_images``
   Run OCR on extracted images.  Defaults to ``False``.

``recursive``
   Descend into subdirectories.  Defaults to ``True``.

``ocr_provider``
   OCR backend.  Defaults to ``"openai"``.

``api_key``
   API key for the OCR provider.

``ocr_cache``
   Optional :class:`~doc2mark.OCRCache` instance.

``show_progress``
   Print progress messages.  Defaults to ``True``.

``save_files``
   Write output files to *output_dir*.  Defaults to ``True``.

``**kwargs``
   Forwarded to :meth:`UnifiedDocumentLoader.batch_process()
   <doc2mark.UnifiedDocumentLoader.batch_process>`.

**Returns**

A ``dict`` mapping each input file path to a result dictionary with detailed
metadata.

.. autofunction:: doc2mark.batch_process_documents
   :noindex:


batch_process_files
~~~~~~~~~~~~~~~~~~~

Processes an explicit list of file paths rather than scanning a directory.
Useful when you already know exactly which files to convert, or when the files
are spread across multiple directories.

.. code-block:: python

   from doc2mark import batch_process_files

   results = batch_process_files(
       ["reports/q1.pdf", "reports/q2.pdf", "notes/meeting.docx"],
       output_dir="out/",
   )

**Signature**

.. code-block:: python

   def batch_process_files(
       file_paths: List[Union[str, Path]],
       output_dir: Optional[Union[str, Path]] = None,
       output_format: Union[str, OutputFormat] = OutputFormat.MARKDOWN,
       extract_images: bool = False,
       ocr_images: bool = False,
       ocr_provider: Union[str, OCRProvider] = "openai",
       api_key: Optional[str] = None,
       ocr_cache: Optional[OCRCache] = None,
       show_progress: bool = True,
       save_files: bool = True,
       **kwargs: Any,
   ) -> Dict[str, Dict[str, Any]]: ...

**Parameters**

``file_paths``
   A list of paths to the documents to process.

``output_dir``
   Destination directory for output files.  Ignored when ``save_files`` is
   ``False``.

``output_format``
   Output format (string or :class:`~doc2mark.OutputFormat`).
   Defaults to ``OutputFormat.MARKDOWN``.

``extract_images``
   Extract images as base64 data.  Defaults to ``False``.

``ocr_images``
   Run OCR on extracted images.  Defaults to ``False``.

``ocr_provider``
   OCR backend.  Defaults to ``"openai"``.

``api_key``
   API key for the OCR provider.

``ocr_cache``
   Optional :class:`~doc2mark.OCRCache` instance.

``show_progress``
   Print progress messages.  Defaults to ``True``.

``save_files``
   Write output files to *output_dir*.  Defaults to ``True``.

``**kwargs``
   Forwarded to :meth:`UnifiedDocumentLoader.batch_process_files()
   <doc2mark.UnifiedDocumentLoader.batch_process_files>`.

**Returns**

A ``dict`` mapping each input file path to a result dictionary.

.. autofunction:: doc2mark.batch_process_files
   :noindex:
