The result: ProcessedDocument
=============================

Every conversion returns a :class:`~doc2mark.ProcessedDocument`:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Field
     - What it holds
   * - ``content``
     - The document as Markdown (or JSON / plain text with ``output_format``, see below).
   * - ``metadata``
     - A :class:`~doc2mark.DocumentMetadata`: ``filename``, ``format`` (a
       :class:`~doc2mark.DocumentFormat`), ``size_bytes``, and the fields the format provides
       (``page_count``, ``word_count``, ``line_count``, ``row_count``, ...; see
       :class:`~doc2mark.DocumentMetadata` for which format sets which).
       ``metadata.extra`` is a dict of conversion reports, described below.
   * - ``json_content``
     - The same content as a list of typed items in reading order (PDF, Office and image files;
       see :ref:`content-items`).
   * - ``images``
     - Pictures, when ``extract_images=True`` (or OCR) produced any. The shape depends on the
       format: PDF ``{"type": "base64", "data": <base64>}`` for an extracted picture and
       ``{"type": "ocr", "text": ...}`` for an OCR'd one; Word/PowerPoint/Excel ``{"data":
       <bytes>, "page": n}`` (an empty list when nothing was extracted); image files ``{"type":
       "image", "content": <base64 PNG>, ...}`` (:doc:`images`). ``to_dict()`` turns bytes into
       base64.
   * - ``tables``, ``sections``
     - Kept for compatibility; the built-in processors leave them ``None``. Tables are in
       ``content`` and are ``table`` items of ``json_content``.

``result.markdown`` is ``content``; ``result.text`` is ``content`` with ``#`` heading markers,
``**bold**``, ``*italics*``, ``[links](...)`` and ```code``` marks removed by simple patterns
(tables, list markers and comments stay; a ``#`` followed by a space is removed anywhere, so
``C# is`` becomes ``Cis``). ``result.to_dict()`` returns a JSON-serialisable dict of
all fields (enums as their values), the payload of ``output_format="json"`` and of the CLI's
``--format json``. ``result.get_chunks()`` splits the document for retrieval (:doc:`chunking`).

Output formats
--------------

``load(..., output_format=...)`` takes ``"markdown"`` (default), ``"json"`` or ``"text"``, or an
:class:`~doc2mark.OutputFormat` member.

- ``"markdown"``: ``content`` is Markdown.
- ``"json"``: ``content`` is ``json.dumps(result.to_dict())`` (its ``content`` field is the
  Markdown). For formats that have no ``json_content`` the attribute ``result.json_content`` is
  then set to one ``text:normal`` item holding the Markdown (the JSON string keeps ``null``).
- ``"text"``: ``content`` is ``result.text``.

.. _content-items:

Content items
-------------

Each item of ``json_content`` is a dict with ``type`` and ``content``. PDF items also carry
``page`` (from 1) and ``position_y`` (the vertical position on the page); Word, PowerPoint and
Excel items carry ``page`` (the page, slide or sheet number; Word header and footer items have
none); PDF and Word titles and section headings carry ``level``.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - ``type``
     - Content
   * - ``text:title``
     - A level-1 heading (``#`` in the Markdown): the PDF title (at most one), each Word Title /
       Heading 1, each slide title, each ``Sheet: <name>`` of a workbook. PDF and Word items
       carry ``level``; slide and sheet titles do not.
   * - ``text:section``
     - A heading; ``level`` 2 to 6 matches the ``#`` count in the Markdown.
   * - ``text:normal``
     - A paragraph.
   * - ``text:list``
     - A list, with its Markdown markers.
   * - ``text:caption``
     - A figure or table caption.
   * - ``text:footnote``
     - A footnote, as printed (``1 Source: ...``); the Markdown writes it ``[^1]: ...`` when its
       number is raised.
   * - ``text:image_description``
     - The OCR text of a picture or of a whole-page render, wrapped in ``<image_ocr_result>``
       tags (the Markdown has the plain text).
   * - ``table``
     - A table as HTML (merged cells) or a Markdown pipe table (see :doc:`tables`).
   * - ``image``
     - An extracted picture as base64 (``extract_images=True`` without OCR).
   * - ``text:header`` / ``text:footer``
     - Running headers and footers the Markdown leaves out: the later copies of a repeated line,
       and every copy of a bare page number or of a line repeating a title; also the header and
       footer paragraphs of Word files. They are not in ``content`` and the chunker skips them.

.. code-block:: python

   from collections import Counter
   from doc2mark import load

   result = load("report.pdf")
   print(Counter(item["type"] for item in result.json_content))
   headings = [(i["level"], i["content"]) for i in result.json_content if i["type"] in ("text:title", "text:section")]
   print(headings[:3])

Conversion reports in ``metadata.extra``
----------------------------------------

A key is present only when it has something to say.

.. list-table::
   :header-rows: 1
   :widths: 24 76

   * - Key
     - Meaning
   * - ``ocr_routing``
     - PDF with OCR on. ``{"document_route": "text" | "image", "overrides": [{"page": 3,
       "route": "image", "reason": "illegible_text_layer"}, ...]}``: the route the document took
       and the pages that went another way, with the reason (``searchable_scan``,
       ``illegible_text_layer``, ``no_text_layer``, ``image_dominant_page``,
       ``dense_text_page``). See :doc:`ocr_policy`.
   * - ``ocr_images``
     - PDF with OCR on: what was sent to the provider. ``ocr_requests``, ``page_renders``,
       ``batches``, ``largest_batch``, ``empty`` (answered with no text), ``failed`` (no
       answer), ``skipped`` (counts by reason: ``not_shown`` for pictures the page does not
       show, ``no_content`` for pictures with nothing to read; ``{}`` when none) and, when some
       exist, ``unread_pages`` (pages that show content but whose OCR returned nothing).
   * - ``ocr_issues``
     - Any format, when OCR went wrong somewhere: ``refused`` (answers that were only a refusal
       or "no readable text", emitted as empty text), ``provider_refused`` (of those, the
       provider's own refusal or safety block), ``failed``, ``withheld`` (sample values a
       screenshot left out), ``suspected`` (answers kept although the judge rated them close to
       no content), ``errors`` (up to five messages) and ``locations`` (one ``{"issue",
       "image", "page" | "slide" | "sheet"}`` per image). See :doc:`ocr`.
   * - ``token_usage``
     - LLM OCR token counts of this load: ``input_tokens``, ``output_tokens``,
       ``total_tokens``. A result replayed from ``cache_dir`` has ``token_usage_cached``
       instead (:doc:`chunking`).
   * - ``text_layer_quality``
     - PDF pages whose text layer is garbled: ``[{"page", "legible": false, "garbage_ratio",
       "garbage_glyphs", "judge_legibility", "action": "ocr" | "kept"}]``. Without OCR the text is
       kept and a warning is logged.
   * - ``hidden_text``
     - PDF pages with invisible text over nothing the page shows, which was left out:
       ``[{"page", "chars"}]``.
   * - ``tables_count``
     - PDF: number of tables found.
   * - ``routed_via``, ``route_reason``, ``route_error``
     - DOCX/PPTX with OCR on: ``routed_via`` is ``"pdf"`` when the file took the image route
       (converted with LibreOffice and OCR'd page by page), ``"native"`` when the route was tried
       and the file was read natively after all, with the reason or the error. See :doc:`formats`.
   * - ``converted_from``, ``converted_to``
     - Legacy files converted by LibreOffice, e.g. ``"doc"`` and ``"docx"``.
   * - ``judge``
     - The optional judge was asked something: ``name``, ``model``, ``asked``, ``cached``,
       ``fresh``, ``failed``, ``input_tokens``, ``cost_usd`` (:doc:`judge`).

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="tesseract")
   result = loader.load("scan.pdf", ocr_images=True)
   extra = result.metadata.extra
   print(extra["ocr_routing"])
   print(extra["ocr_images"]["ocr_requests"], "image(s) sent to OCR")
   if extra.get("ocr_issues"):
       print("check before indexing:", extra["ocr_issues"])

Errors
------

:meth:`~doc2mark.UnifiedDocumentLoader.load` raises ``FileNotFoundError`` for a missing file,
:class:`~doc2mark.UnsupportedFormatError` for an extension it does not know and ``ValueError``
for an unknown ``output_format``. Any failure during conversion is raised as
:class:`~doc2mark.ProcessingError` (``Processing failed: ...``) with the original exception as
its ``__cause__``: for example an OCR engine that cannot run (Tesseract without the requested
language data, cause ``OCREngineError``), a failed LibreOffice conversion (a chain of
``ProcessingError`` that ends in :class:`~doc2mark.ConversionError`) or a legacy file without
LibreOffice. So catch
``ProcessingError`` (``UnsupportedFormatError`` is a subclass); ``OCRError`` and
``ConversionError`` do not reach you directly. A picture or page the OCR provider could not read
does not fail the document: it is reported in ``ocr_issues``.
