doc2mark
========

doc2mark converts documents into Markdown and structured Python objects. It
supports PDFs, modern Office files, images, text/data files, markup files, and
legacy Office files through LibreOffice.

The default Python API is safe for text-only extraction without OCR credentials.
OCR providers are initialized only when OCR is requested.

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   quickstart
   cli
   formats
   ocr
   ocr_policy
   tables
   contextual_ocr
   caching

.. toctree::
   :maxdepth: 2
   :caption: Reference

   api/index
   development
