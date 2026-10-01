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

.. admonition:: Highlight: the optional Jev quality judge

   Deterministic rules settle the clear cases; an optional judge, `TypeSafe's Jev
   <https://docs.typesafe.ai>`_, settles three ambiguous ones the rules cannot: is a PDF page's
   text layer legible (a garbled but valid-Unicode layer is OCR'd instead of indexed as
   gibberish), is a repeated header or brand line page chrome (thinned to one copy, never its
   last), and is an OCR answer only a refusal (re-read or dropped instead of indexed). On the
   held-out TEST set, accuracy of the three decisions went from 67.4 % to 100 %, from 50.0 % to
   95.2 % and from 76.7 % to 97.8 % when the judge was added to the rules. The sets are small:
   read the numbers as a direction, not a guarantee.

   It is **off by default** and doc2mark works fully without it (``pip install
   "doc2mark[typesafe]"``, ``TYPESAFE_API_KEY`` and ``--judge typesafe`` turn it on). **With it
   on, text from your documents is sent to a third party** (TypeSafe). :doc:`judge` has the
   cost and latency, the privacy note and the full evaluation.

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
