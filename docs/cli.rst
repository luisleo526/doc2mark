Command Line
============

Convert a single file to Markdown:

.. code-block:: bash

   doc2mark report.pdf

Write to a file:

.. code-block:: bash

   doc2mark report.pdf -o report.md

Convert a directory:

.. code-block:: bash

   doc2mark documents/ -o converted/ -r

JSON output uses the same structure as ``ProcessedDocument.to_dict()``:

.. code-block:: bash

   doc2mark report.pdf --format json

Write both Markdown and JSON:

.. code-block:: bash

   doc2mark report.pdf --format both -o report

OCR is disabled by default in the CLI. Enable it explicitly:

.. code-block:: bash

   doc2mark scan.pdf --ocr openai --ocr-images
   doc2mark scan.pdf --ocr tesseract --ocr-images
   doc2mark scan.pdf --ocr vertex_ai --ocr-images

Useful options:

.. code-block:: bash

   doc2mark docs/ -r --pattern "*.pdf" --parallel 4
   doc2mark docs/ --exclude "*.tmp" --max-files 10
   doc2mark report.pdf --include-metadata --max-length 5000
