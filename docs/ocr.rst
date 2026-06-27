OCR
===

OCR providers are optional. The package can process normal text documents
without OpenAI, Vertex AI, or Tesseract credentials.

OpenAI
------

Install OCR extras and set an API key:

.. code-block:: bash

   pip install "doc2mark[ocr]"
   export OPENAI_API_KEY=sk-...

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="openai")
   result = loader.load("scan.pdf", extract_images=True, ocr_images=True)

OpenAI-compatible endpoints can be configured with ``base_url``:

.. code-block:: python

   loader = UnifiedDocumentLoader(
       ocr_provider="openai",
       model="gpt-4o-mini",
       base_url="http://localhost:11434/v1",
       api_key="any-string",
   )

Vertex AI
---------

.. code-block:: bash

   pip install "doc2mark[vertex_ai]"
   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json

.. code-block:: python

   loader = UnifiedDocumentLoader(
       ocr_provider="vertex_ai",
       project="my-gcp-project",
   )

Tesseract
---------

.. code-block:: bash

   pip install "doc2mark[ocr]"

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader
   from doc2mark.ocr.base import OCRConfig

   loader = UnifiedDocumentLoader(
       ocr_provider="tesseract",
       ocr_config=OCRConfig(language="eng"),
   )

Disabling OCR
-------------

.. code-block:: python

   loader = UnifiedDocumentLoader(ocr_provider=None)
   result = loader.load("document.pdf")

.. code-block:: bash

   doc2mark document.pdf --ocr none
