doc2mark
========

doc2mark converts PDF, Word, Excel, PowerPoint, images, HTML, XML, Markdown, text, CSV, JSON
and e-mail files into Markdown and structured Python objects for LLM and retrieval pipelines.
Text, tables and structure are read from the file itself, locally and deterministically; OCR
(OpenAI, Google Gemini on Vertex AI, or local Tesseract) runs only when you turn it on, and then
only on the pages and pictures that need it. The optional quality judge below, which sends text to
TypeSafe, runs only when you turn that on.

.. code-block:: python

   from doc2mark import load

   result = load("report.pdf")
   print(result.content)                        # Markdown
   chunks = result.get_chunks()                 # section-aware chunks with page spans

.. admonition:: Highlight: the optional Jev quality judge

   Deterministic rules decide the clear cases and keep the text when the evidence is weak. An
   optional judge, `TypeSafe's Jev <https://docs.typesafe.ai>`_, can be asked about three cases
   the rules leave open: is a PDF page's text layer legible (with OCR on, a garbled but
   valid-Unicode layer is OCR'd instead of indexed as gibberish), is a repeated header or brand
   line page chrome (thinned to one copy, never its last), and is an OCR answer from an LLM
   provider only a refusal (re-read or dropped instead of indexed).

   Accuracy on the labelled sets, rules alone to rules plus judge (TEST is held out from
   calibration; EXTERNAL, 60 items, was written by a reviewer and never used for it):

   .. list-table::
      :header-rows: 1

      * - Decision
        - TEST
        - EXTERNAL
      * - Text layer legible?
        - 67.4 % to 100 % (46 items)
        - 68.8 % to 100 % (16 items)
      * - Repeated line is page chrome?
        - 50.0 % to 95.2 % (42)
        - 66.7 % to 100 % (3 asked of 20)
      * - OCR answer is a refusal or error?
        - 76.7 % to 97.8 % (90)
        - 62.5 % to 83.3 % (24)

   The sets are small: read the numbers as a direction, not a guarantee. The judge is not always
   right (on EXTERNAL it kept 4 of 9 refusals).

   It is **off by default** and doc2mark works fully without it. **With it on, text from your
   documents is sent to a third party** (TypeSafe). To turn it on: ``pip install
   "doc2mark[typesafe]"``, ``TYPESAFE_API_KEY`` and ``--judge typesafe``. :doc:`judge` has the
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
