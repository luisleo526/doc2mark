OCR
===

``doc2mark.ocr``: the :class:`~doc2mark.OCR` facade, its configuration and result types, the
provider registry and the three providers. Guide: :doc:`/ocr`.

The facade
----------

.. autoclass:: doc2mark.OCR
   :members: read, read_one

Configuration
-------------

.. autoclass:: doc2mark.OCRConfig

.. list-table::
   :header-rows: 1
   :widths: 22 16 62

   * - Field
     - Default
     - Used by
   * - ``model``
     - ``None``
     - OpenAI (``None``: ``gpt-5.4-mini``). The Vertex AI provider takes its model from its own
       ``model`` argument (the loader's ``model``), not from here.
   * - ``task``
     - ``Task.AUTO``
     - OpenAI, Vertex AI (structured prompt). Not part of the OCR cache key.
   * - ``language``
     - ``None``
     - OpenAI, Vertex AI: language of the answer. Tesseract: recognition language
       (:ref:`tesseract-languages`).
   * - ``temperature``, ``max_tokens``
     - ``None``
     - OpenAI (defaults 0 and 8192).
   * - ``base_url``
     - ``None``
     - OpenAI (else ``OPENAI_BASE_URL``).
   * - ``max_concurrency``
     - ``None``
     - OpenAI, Vertex AI (else ``OCR_MAX_CONCURRENCY``, else LangChain's default); PDF batch
       size.
   * - ``structured``
     - ``True``
     - OpenAI, Vertex AI: ``False`` gives free-form Markdown and ``document=None``.
   * - ``detail``
     - ``"full"``
     - ``"raw"`` asks for no interpretation (Vertex AI also drops it).
   * - ``response_model``
     - ``None``
     - A pydantic model to parse into instead of :class:`~doc2mark.ocr.schema.OCRPage` (Vertex AI; with
       OpenAI a model without an ``interpretation`` field fails).
   * - ``on_parse_error``
     - ``"raw_text"``
     - ``"raw_text"`` keeps the raw answer when parsing fails, ``"raise"`` raises.
   * - ``context_pages``
     - ``0``
     - PDF neighbour-page context: 1 for page renders, 2 also for pictures
       (:doc:`/contextual_ocr`).
   * - ``non_content_judge``
     - ``None``
     - OpenAI, Vertex AI: screens answers the refusal patterns keep (:doc:`/ocr`).
   * - ``enhance_image``, ``detect_layout``
     - ``True``
     - Tesseract only (grey-scale threshold; page segmentation mode). Deprecated for the LLM
       providers.
   * - ``detect_tables``, ``timeout``, ``max_retries``, ``extra``
     - ``True``, ``30``, ``3``, ``None``
     - Nothing; deprecated (a non-default value makes the OpenAI and Vertex AI providers emit a
       ``DeprecationWarning``). The request timeout and retries are provider arguments.

Results and enums
-----------------

.. autoclass:: doc2mark.ocr.OCRResult

   ``text`` (Markdown, ``""`` for a refusal or a failure), ``confidence``, ``language``,
   ``metadata`` (``model``, ``token_usage``, ``failed``, ``ocr_refusal``, ...) and ``document``
   (an :class:`~doc2mark.ocr.schema.OCRPage`, or ``None`` for free-form answers and failed Tesseract
   images).

.. autoclass:: doc2mark.Task
   :members:
   :undoc-members:

.. autoclass:: doc2mark.OCRProvider
   :members:
   :undoc-members:

Providers
---------

``OCRFactory`` maps a provider name (case-insensitive) to a :class:`~doc2mark.ocr.BaseOCR`
subclass; ``"gemini"`` is an alias of ``"vertex_ai"``. Subclass ``BaseOCR`` (implement
``batch_process_images``) and register it to add a provider.

.. code-block:: python

   from doc2mark import OCRConfig, OCRFactory

   print(sorted(OCRFactory.list_providers()))       # ['gemini', 'openai', 'tesseract', 'vertex_ai']
   tesseract = OCRFactory.create("tesseract", config=OCRConfig(language="english"))
   results = tesseract.batch_process_images([open("receipt.png", "rb").read()])
   print(results[0].text)

.. autoclass:: doc2mark.OCRFactory
   :members: create, register_provider, list_providers

.. autoclass:: doc2mark.ocr.BaseOCR
   :members: batch_process_images

.. autoclass:: doc2mark.ocr.openai.OpenAIOCR

.. autoclass:: doc2mark.ocr.vertex_ai.VertexAIOCR

.. autoclass:: doc2mark.ocr.tesseract.TesseractOCR
