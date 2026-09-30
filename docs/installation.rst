Installation
============

doc2mark needs Python 3.10 or later (CI tests 3.10 to 3.13).

.. code-block:: bash

   pip install doc2mark

The base install converts PDF, Word, Excel, PowerPoint, images, HTML, XML, Markdown, text, CSV,
JSON and e-mail files without any model or API key. Its dependencies are PyMuPDF 1.27.1 or
later, numpy, python-docx, openpyxl, python-pptx, BeautifulSoup and lxml, Markdown, chardet,
Pillow, pydantic 2 and defusedxml. pandas is not a dependency: if your own code uses it, install
it yourself.

Optional extras
---------------

.. list-table::
   :header-rows: 1
   :widths: 18 42 40

   * - Extra
     - Installs
     - Needed for
   * - ``ocr``
     - openai, langchain, langchain-openai, pytesseract
     - The OpenAI provider (and OpenAI-compatible endpoints) and the Tesseract provider. Tesseract
       also needs the ``tesseract`` program, see below.
   * - ``vertex_ai``
     - langchain-google-genai, langchain
     - The Google Gemini / Vertex AI provider.
   * - ``typesafe``
     - typesafe-sdk
     - The optional quality judge (:doc:`judge`).
   * - ``tokenizers``
     - tiktoken
     - Chunk sizes counted in tokens (:doc:`chunking`).
   * - ``redis``
     - redis
     - The Redis OCR cache (:doc:`caching`).
   * - ``heif``
     - pillow-heif
     - Reading ``.heic`` and ``.heif`` image files.
   * - ``mime``
     - python-magic
     - Content sniffing in the MIME helpers of ``doc2mark.core.mime_mapper``. The loader itself
       picks the format from the file extension and does not use it.
   * - ``all``
     - all of the above
     -

.. code-block:: bash

   pip install "doc2mark[ocr]"          # OpenAI and Tesseract OCR
   pip install "doc2mark[ocr,tokenizers]"
   pip install "doc2mark[all]"

System programs
---------------

Two features call programs that pip does not install.

**Tesseract** (the ``tesseract`` provider). Install the program and the language data you need;
English is the default. On Debian or Ubuntu:

.. code-block:: bash

   sudo apt-get install tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-tra tesseract-ocr-chi-sim

On macOS, ``brew install tesseract tesseract-lang``. ``tesseract --list-langs`` shows what is
installed. A language that is not installed, a missing ``tesseract`` program or a wrong
``TESSDATA_PREFIX`` fails the conversion with an error (the CLI exits non-zero) as soon as the
document has something to OCR; doc2mark does not write placeholder text instead. Language codes
are described in :ref:`tesseract-languages`.

**LibreOffice** (``soffice`` on the ``PATH``). It converts the legacy formats ``.doc``,
``.xls``, ``.ppt``, ``.pps`` and ``.rtf`` before they are read, and with OCR on it renders
Word and PowerPoint files made mostly of pictures to PDF (the Office image route, see
:doc:`formats`). Without it, legacy files fail with a :class:`~doc2mark.ProcessingError` that
says how to install it, and DOCX/PPTX files are read natively. On Debian or Ubuntu:

.. code-block:: bash

   sudo apt-get install libreoffice-writer libreoffice-calc libreoffice-impress

On macOS, ``brew install --cask libreoffice``.

Checking the install
--------------------

.. code-block:: bash

   doc2mark --help
   python -c "import doc2mark; print(doc2mark.__version__)"

The reference environment of the test suite (Python 3.12, Tesseract with English and Chinese
data, LibreOffice and CJK fonts) is ``tests/e2e/Dockerfile``; see :doc:`development`.
