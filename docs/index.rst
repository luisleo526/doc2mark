doc2mark
========

doc2mark converts PDF, Word, Excel, PowerPoint, images, HTML, XML, Markdown, text, CSV, JSON
and e-mail files into Markdown and structured Python objects for LLM and retrieval pipelines.
Text, tables and structure are read from the file itself, locally and deterministically; OCR
(OpenAI, Google Gemini on Vertex AI, or local Tesseract) runs only when you turn it on, and then
only on the pages and pictures that need it.

.. code-block:: python

   from doc2mark import load

   result = load("report.pdf")
   print(result.content)                        # Markdown
   chunks = result.get_chunks()                 # section-aware chunks with page spans

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   installation
   quickstart
   rag

.. toctree::
   :maxdepth: 2
   :caption: User guide

   loading
   formats
   output
   pdf
   tables
   images
   ocr
   ocr_policy
   contextual_ocr
   caching
   chunking
   judge
   cli
   troubleshooting

.. toctree::
   :maxdepth: 2
   :caption: Reference

   api/index
   development
