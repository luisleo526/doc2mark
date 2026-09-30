UnifiedDocumentLoader
=====================

The loader: one instance holds the OCR provider, caches, table style and judges, and converts
files with :meth:`~doc2mark.UnifiedDocumentLoader.load` or in batches. Guide: :doc:`/loading`.

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
       max_workers=None,
       prompt_template='default',
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
       legibility_judge=None,
       boilerplate_judge=None,
       judge=None,
   )

``ocr_provider``
   ``"openai"``, ``"vertex_ai"``, ``"gemini"``, ``"tesseract"``, an
   :class:`~doc2mark.OCRProvider`, a provider instance (:class:`~doc2mark.ocr.BaseOCR`), or
   ``None`` / ``"none"`` / ``"disabled"`` for no OCR. The provider object is created here; it
   only calls out when a document is loaded with ``ocr_images=True``.
``api_key``
   OpenAI key; default ``OPENAI_API_KEY``.
``ocr_config``
   An :class:`~doc2mark.OCRConfig`. ``task``, ``structured`` and ``detail``, when not ``None``,
   override its fields; for ``"openai"`` and ``"vertex_ai"`` the loader's ``model``,
   ``temperature`` and ``max_tokens`` (and ``base_url`` for OpenAI) take precedence over the
   config's.
``cache_dir``, ``ocr_cache``
   The document cache folder and the OCR answer cache (:doc:`/caching`).
``model``, ``temperature``, ``max_tokens``
   For ``"openai"``, ``"vertex_ai"`` and ``"gemini"``. With Vertex AI the default
   ``gpt-5.4-mini`` means ``ocr_config.model``, else ``gemini-3.1-flash-lite-preview``.
``timeout``, ``max_retries``
   Per-request timeout (seconds) and retries of the OpenAI or Vertex AI client.
``max_workers``
   At most this many OCR requests at once, when ``ocr_config.max_concurrency`` is not set
   (``None``: ``OCR_MAX_CONCURRENCY``, else LangChain's default).
``top_p``, ``frequency_penalty``, ``presence_penalty``
   Sampling settings of the OpenAI request and the Vertex AI client, sent only when they differ
   from these defaults (the API's own).
``base_url``
   OpenAI-compatible endpoint (default ``OPENAI_BASE_URL``).
``project``, ``location``
   Vertex AI project (default ``GOOGLE_CLOUD_PROJECT``) and location.
``prompt_template``, ``default_prompt``
   Prompt of free-form OCR answers (``structured=False`` and the retry of an empty structured
   answer): ``default``, ``table_focused``, ``document_focused``, ``multilingual``,
   ``form_focused``, ``receipt_focused``, ``handwriting_focused``, ``code_focused``, or your own
   prompt text (``default_prompt``).
``table_style``
   ``"minimal_html"`` (default), ``"markdown_grid"`` or ``"styled_html"`` (:doc:`/tables`).
``legibility_judge``, ``boilerplate_judge``, ``judge``
   The judge hooks, or a judge object / ``"typesafe"`` / ``"none"``; default
   ``$DOC2MARK_JUDGE`` (:doc:`/judge`).

.. autoclass:: doc2mark.UnifiedDocumentLoader
   :members: load, batch_process, batch_process_files, load_directory, supported_formats,
             set_ocr_provider, get_ocr_configuration, validate_ocr_setup

   ``load()`` notes: ``ocr_images=True`` implies ``extract_images=True`` when an OCR provider is
   configured, and both flags also apply to image files; ``delimiter`` is currently ignored (the
   CSV delimiter is detected). The batch result dictionaries are described in :doc:`/loading`.
