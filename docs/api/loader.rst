UnifiedDocumentLoader
=====================

:class:`~doc2mark.UnifiedDocumentLoader` is the single entry point for turning
documents into Markdown, JSON, or plain text. It auto-detects the input format
from the file extension, dispatches to the right internal processor (Office, PDF,
text, markup, legacy, image, and optional email), and optionally runs an OCR
provider over extracted images. The same instance can convert one file with
:meth:`~doc2mark.UnifiedDocumentLoader.load` or whole directories / explicit file
lists with the batch helpers, which now support opt-in thread-based parallelism
and progress reporting.

All examples assume:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader, OutputFormat

Constructor
-----------

.. code-block:: python

   UnifiedDocumentLoader(
       ocr_provider='openai',
       api_key=None,
       ocr_config=None,
       cache_dir=None,
       ocr_cache=None,
       model='gpt-5.4-mini',
       temperature=0,
       max_tokens=8192,
       max_workers=5,
       prompt_template=PromptTemplate.DEFAULT,
       timeout=30,
       max_retries=3,
       top_p=1.0,
       frequency_penalty=0.0,
       presence_penalty=0.0,
       base_url=None,
       project=None,
       location='global',
       default_prompt=None,
       task=None,
       structured=None,
       detail=None,
       table_style=None,
   )

The constructor builds (and configures) the OCR provider eagerly, so most OCR
tuning happens here. Pass ``ocr_provider=None`` (or the strings ``'none'`` /
``'disabled'``) to disable OCR entirely.

Core parameters
~~~~~~~~~~~~~~~~

``ocr_provider`` : str | :class:`~doc2mark.OCRProvider` | BaseOCR | None
    OCR backend to use. A name or :class:`~doc2mark.OCRProvider` enum
    (``'openai'``, ``'vertex_ai'``, ...), a pre-built OCR instance, or ``None``
    to disable OCR. Defaults to ``'openai'``.
``api_key`` : str | None
    API key for the OCR provider. For OpenAI it falls back to the
    ``OPENAI_API_KEY`` environment variable when omitted.
``ocr_config`` : :class:`~doc2mark.OCRConfig` | None
    Base OCR configuration. When omitted a default config is created so the
    structured-output defaults still apply.
``cache_dir`` : str | None
    Directory for caching *converted documents* on disk (keyed by file path,
    mtime, size, and conversion options). Distinct from ``ocr_cache``.
``ocr_cache`` : OCRCache | None
    Request-scoped cache for individual OCR image calls. See :doc:`/caching`.
``table_style`` : str | None
    How complex tables with merged cells are rendered: ``'minimal_html'``
    (default), ``'markdown_grid'``, or ``'styled_html'``.

OCR / model tuning
~~~~~~~~~~~~~~~~~~~

``model`` : str
    Model name. Defaults to ``'gpt-5.4-mini'`` for OpenAI; for ``vertex_ai`` the
    default ``'gpt-5.4-mini'`` is automatically swapped for a Gemini model.
``temperature`` : float
    Sampling temperature (default ``0``).
``max_tokens`` : int
    Maximum response tokens (default ``4096``).
``max_workers`` : int
    Concurrency used *inside* the OCR provider for batched image OCR
    (default ``5``). This is independent of the ``max_workers`` argument on the
    batch methods, which controls document-level concurrency.
``prompt_template`` : str | PromptTemplate
    Built-in prompt preset (e.g. ``'default'``, ``'table_focused'``,
    ``'document_focused'``, ``'multilingual'``, ``'receipt_focused'``).
``timeout`` / ``max_retries`` : int
    Per-request timeout in seconds and retry budget for OCR calls.
``top_p`` / ``frequency_penalty`` / ``presence_penalty`` : float
    Additional OpenAI sampling controls.
``base_url`` : str | None
    Override base URL for OpenAI-compatible endpoints.
``project`` / ``location`` : str | None
    Vertex AI Google Cloud project ID (falls back to ``GOOGLE_CLOUD_PROJECT``)
    and region (default ``'global'``).
``default_prompt`` : str | None
    Custom prompt string that overrides the built-in templates.

Structured-OCR knobs
~~~~~~~~~~~~~~~~~~~~~

These override the matching field on the resolved :class:`~doc2mark.OCRConfig`
for LLM providers. Leaving them as ``None`` preserves the supplied config (or
the config default) unchanged.

``task`` : str | :class:`~doc2mark.Task` | None
    Override the OCR task (e.g. ``'receipt'``).
``structured`` : bool | None
    Toggle structured-output mode. The config default is ``True``.
``detail`` : str | None
    Interpretation detail, ``'raw'`` or ``'full'``.

.. note::

   ``max_workers`` here (OCR-internal) and the ``max_workers`` argument on
   :meth:`~doc2mark.UnifiedDocumentLoader.batch_process` /
   :meth:`~doc2mark.UnifiedDocumentLoader.batch_process_files`
   (document-level threads) are two separate dials.

load()
------

.. code-block:: python

   load(
       file_path,
       output_format=OutputFormat.MARKDOWN,
       extract_images=False,
       ocr_images=False,
       show_progress=False,
       encoding='utf-8',
       delimiter=None,
   ) -> ProcessedDocument

Convert a single document and return a :class:`~doc2mark.ProcessedDocument`.

``file_path`` : str | Path
    Path to the document. Raises ``FileNotFoundError`` if it does not exist.
``output_format`` : str | :class:`~doc2mark.OutputFormat`
    ``MARKDOWN`` (default), ``JSON``, or ``TEXT``. Strings are accepted and
    normalized.
``extract_images`` : bool
    Extract embedded images as base64. Only meaningful for Office and PDF
    inputs.
``ocr_images`` : bool
    Run OCR over extracted images. Requires ``extract_images=True`` and a
    configured OCR provider.
``show_progress`` : bool
    Emit per-step progress log messages.
``encoding`` : str
    Text encoding for text/markup inputs (default ``'utf-8'``).
``delimiter`` : str | None
    CSV delimiter; auto-detected when ``None``.

Raises :class:`~doc2mark.UnsupportedFormatError` for unknown formats and
:class:`~doc2mark.ProcessingError` if conversion fails.

.. code-block:: python

   loader = UnifiedDocumentLoader(ocr_provider=None)  # OCR not needed here
   doc = loader.load('report.docx')
   print(doc.content)            # Markdown string
   print(doc.metadata.format)    # DocumentFormat.DOCX

OCR-enabled loading:

.. code-block:: python

   loader = UnifiedDocumentLoader(
       ocr_provider='openai',
       api_key='sk-...',          # or set OPENAI_API_KEY
       prompt_template='table_focused',
   )
   doc = loader.load(
       'scanned.pdf',
       extract_images=True,
       ocr_images=True,           # transcribe image content into the output
   )
   print(doc.content)

batch_process()
---------------

.. code-block:: python

   batch_process(
       input_dir,
       output_dir=None,
       output_format=OutputFormat.MARKDOWN,
       extract_images=False,
       ocr_images=False,
       recursive=True,
       show_progress=True,
       save_files=True,
       encoding='utf-8',
       delimiter=None,
       max_workers=None,
       progress_callback=None,
   ) -> Dict[str, Dict[str, Any]]

Discover every supported file under ``input_dir`` and convert each one. Returns
a dict keyed by the string file path; the keys are returned in deterministic
input order regardless of completion order.

``input_dir`` : str | Path
    Directory to scan. Raises ``FileNotFoundError`` if missing.
``output_dir`` : str | Path | None
    Where outputs are written. Defaults to ``input_dir``.
``recursive`` : bool
    Recurse into subdirectories (default ``True``).
``save_files`` : bool
    Write ``.md`` / ``.json`` (and any extracted images) to ``output_dir``.
``max_workers`` : int | None
    **Opt-in parallelism.** When set to a value greater than ``1`` *and* more
    than one file is found, documents are converted concurrently with a thread
    pool (capped at the file count). ``None`` (default) keeps the original
    sequential behavior.
``progress_callback`` : Callable[[int, int, str], None] | None
    Invoked as ``callback(done, total, path)`` after each file finishes —
    whether it succeeded or failed. ``path`` is the same string key used in the
    returned dict.

``extract_images``, ``ocr_images``, ``output_format``, ``show_progress``,
``encoding``, and ``delimiter`` behave as in
:meth:`~doc2mark.UnifiedDocumentLoader.load`.

Result dict shape
~~~~~~~~~~~~~~~~~~

Each value is a per-file result dict. On success:

.. code-block:: python

   {
       'status': 'success',
       'format': 'docx',              # DocumentFormat value
       'content_length': 1234,        # len(content), characters
       'duration': 0.42,              # seconds spent on this file
       'output_files': ['out/report.md'],   # written paths (empty if save_files=False)
       'metadata': {
           'images_extracted': 3,
           'tables_found': 1,
           'pages': 2,                # batch_process only
       },
   }

On failure the entry is compact:

.. code-block:: python

   {
       'status': 'failed',
       'error': 'Processing failed: ...',
       'format': '.pdf',             # file suffix
   }

.. note::

   ``batch_process`` includes ``'pages'`` in ``metadata``;
   :meth:`~doc2mark.UnifiedDocumentLoader.batch_process_files` omits it
   (it reports only ``images_extracted`` and ``tables_found``).

Example with parallelism and a progress bar:

.. code-block:: python

   loader = UnifiedDocumentLoader(ocr_provider=None)

   def on_progress(done, total, path):
       print(f'[{done}/{total}] {path}')

   results = loader.batch_process(
       'docs/',
       output_dir='out/',
       max_workers=4,               # convert up to 4 files concurrently
       progress_callback=on_progress,
   )

   ok = sum(1 for r in results.values() if r['status'] == 'success')
   print(f'{ok}/{len(results)} converted')

batch_process_files()
---------------------

.. code-block:: python

   batch_process_files(
       file_paths,
       output_dir=None,
       output_format=OutputFormat.MARKDOWN,
       extract_images=False,
       ocr_images=False,
       show_progress=True,
       save_files=True,
       encoding='utf-8',
       delimiter=None,
       max_workers=None,
       progress_callback=None,
   ) -> Dict[str, Dict[str, Any]]

Like :meth:`~doc2mark.UnifiedDocumentLoader.batch_process`, but converts an
explicit list of files instead of scanning a directory (so there is no
``recursive`` argument). It returns the same per-file result dict described
above, except each success entry's ``metadata`` contains only
``images_extracted`` and ``tables_found`` (no ``pages``). An empty
``file_paths`` returns an empty dict.

``file_paths`` : list[str | Path]
    Files to convert. Order is preserved in the returned dict.
``output_dir`` : str | Path | None
    Output directory. When ``None`` (or ``save_files=False``) nothing is written
    to disk.
``max_workers`` / ``progress_callback``
    Same opt-in parallelism and ``(done, total, path)`` callback as
    :meth:`~doc2mark.UnifiedDocumentLoader.batch_process`.

.. code-block:: python

   loader = UnifiedDocumentLoader(ocr_provider='openai')
   results = loader.batch_process_files(
       ['a.pdf', 'b.docx', 'c.pptx'],
       output_dir='out/',
       extract_images=True,
       ocr_images=True,
       max_workers=3,
       progress_callback=lambda d, t, p: print(f'{d}/{t}'),
   )
   for path, info in results.items():
       print(path, info['status'])

API
---

.. autoclass:: doc2mark.core.loader.UnifiedDocumentLoader
   :members:
   :show-inheritance:
