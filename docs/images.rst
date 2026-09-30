Images
======

Image files
-----------

``.png``, ``.jpg``, ``.jpeg``, ``.webp``, ``.tif``, ``.tiff``, ``.bmp`` and ``.gif`` are read with
Pillow; ``.heic`` and ``.heif`` need ``doc2mark[heif]`` (pillow-heif), and ``.avif`` needs a Pillow
build with AVIF support. Without OCR the Markdown describes the picture; with OCR it also holds
the text the provider read:

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="tesseract")
   result = loader.load("receipt.png", ocr_images=True)
   print(result.content)

.. code-block:: text

   # Image: receipt.png

   - **Format**: PNG
   - **Dimensions**: 1654 x 2339 pixels
   - **Mode**: RGB
   - **Size**: 41,873 bytes

   ## OCR Extracted Text

   ACME STORE
   Total 9.50

When the provider finds no text, the section is ``## OCR Extraction`` with *No text detected in
image*; when OCR fails for this image (the request raised, for example without an API key, or the
provider flagged the answer failed), the section shows ``[image: OCR unavailable]``, the failure
is counted in ``metadata.extra["ocr_issues"]`` and the file still converts, but is not stored in
``cache_dir`` (an OCR engine that cannot run at all, such as Tesseract without its language data,
fails the load).
``extract_images=True`` (implied by ``ocr_images=True`` with a provider) also returns the image
re-encoded as PNG in ``result.images`` (``{"type": "image", "content": <base64>, "format":
"png", "width", "height", "original_format", "filename"}``). ``metadata`` has ``page_count`` 1,
``image_count`` 1, ``word_count`` of the OCR text, and ``extra`` with ``width``, ``height``,
``mode``, ``format`` and ``has_ocr``.

Pictures inside documents
-------------------------

``extract_images=True`` without OCR returns the pictures of PDF, Word, Excel and PowerPoint files
as they are: each is embedded in the Markdown as a ``data:`` URI image (``![Image](data:...)``),
is an ``image`` item of ``json_content`` and is listed in ``result.images`` (PDF: ``{"type":
"base64", "data": ...}``; Office: ``{"data": <bytes>, "page": n}``). Leave it off when you index
text. With ``ocr_images=True`` and a provider the pictures are OCR'd instead and their text is
placed where the picture is (``text:image_description`` items):

- **PDF.** A picture is OCR'd by what it shows, once per distinct image, and its text is emitted
  at each place the page shows it. Plain backgrounds, colour blocks, frames and icons without
  text are not sent to OCR; small charts, stamps and lettered logos are. A picture cropped by a
  clip path is read as the page shows it. A picture whose OCR failed leaves ``[image: OCR
  unavailable]`` where it shows; a logo repeated at the same place on most pages is kept once.
  Scanned pages and pages whose content is only in pictures are OCR'd as a whole-page render
  instead (:doc:`ocr_policy`).
- **Word, PowerPoint, Excel.** Pictures are OCR'd in one batch per document (in Word, the
  pictures of headers, footers and text boxes too); a picture in which OCR finds no text adds
  nothing, one whose OCR failed leaves ``[image: OCR unavailable]``, as in PDFs. A picture in a
  Word or Excel table cell is marked ``[Image]`` (``[Image: OCR text]`` with OCR) in its cell.
  Word and PowerPoint files made mostly of pictures
  can be converted to PDF and OCR'd page by page (the Office image route, :doc:`formats`).

``metadata.extra["ocr_images"]`` (PDF) reports how many requests were sent and which pictures
were skipped, and ``metadata.extra["ocr_issues"]`` lists pictures that were refused or could not
be read (:doc:`output`).

Large images and cost
---------------------

Set ``OCR_MAX_IMAGE_DIM`` (pixels) to downscale images whose longest side is larger before they
are sent to an LLM provider; images within the bound are left as they are. It is off by default.
