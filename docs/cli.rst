Command line
============

The ``doc2mark`` command (also ``python -m doc2mark``) converts one file or a folder with the
same loader as the Python API.

.. code-block:: bash

   doc2mark report.pdf                         # Markdown on stdout (see "Output" below)
   doc2mark report.pdf -o report.md            # write report.md
   doc2mark report.pdf --format json -o report # write report.json
   doc2mark report.pdf --format both -o report # write report.md and report.json
   doc2mark scan.pdf --ocr tesseract --ocr-images -o scan.md
   doc2mark documents/ -r -o converted/ --skip-errors

OCR is off unless you pass ``--ocr <provider> --ocr-images``.

Output
------

- **One file, no** ``-o``: Markdown goes to stdout, cut after 1,000 characters with a
  ``... (truncated, use -v for full output)`` line unless ``-v`` is given; ``--format json``
  prints the whole JSON. stdout carries only the document: logs and MuPDF's messages go to
  stderr.
- **One file with** ``-o PATH``: the extension of ``PATH`` is replaced by ``.md`` or ``.json``
  (``-o report`` and ``-o report.txt`` both write ``report.md``; the folder must exist);
  ``--format both`` writes both files. The command prints ``Output saved to: ...`` (not with ``-q``).
- **A folder**: with ``-o DIR`` every converted document is written to ``DIR/<file stem>.md``
  (or ``.json``), flat, whatever sub-folder it came from (two files with the same stem overwrite
  each other); without ``-o`` only a summary is printed.

The JSON is :meth:`ProcessedDocument.to_dict() <doc2mark.ProcessedDocument.to_dict>`: ``content``
(the Markdown), ``metadata`` (with ``extra``), ``images``, ``tables``, ``sections`` and
``json_content``.

Options
-------

OCR
~~~

``--ocr {openai,vertex_ai,tesseract,none}``
   OCR provider (default ``none``). ``gemini`` is not accepted here; use ``vertex_ai``.
``--ocr-images`` / ``--no-ocr-images``
   OCR scanned pages and pictures (default off). Needs ``--ocr`` other than ``none`` (otherwise
   the command stops with a usage error) and implies ``--extract-images``.
``--extract-images`` / ``--no-extract-images``
   Extract pictures (default off).
``--api-key KEY``
   Key for the provider; OpenAI otherwise reads ``OPENAI_API_KEY``.
``--ocr-lang LANG``
   Tesseract language(s) (default ``eng``): codes such as ``deu``, ``chi_tra``, ``eng+chi_tra``
   or long names such as ``chinese_traditional`` (see :ref:`tesseract-languages`). A language
   that is not installed fails the run. Only used with ``--ocr tesseract``.
``--ocr-task {auto,table,document,form,receipt,handwriting,code}``
   Task hint for the LLM providers (default ``auto``).
``--no-structured``
   Free-form Markdown answers instead of structured OCR (LLM providers).
``--ocr-detail {raw,full}``
   ``raw`` skips the interpretation part of structured answers (default ``full``).
``--judge {none,typesafe}``
   The optional judge (:doc:`judge`). Default: ``$DOC2MARK_JUDGE``, else ``none``.

Output
~~~~~~

``-o, --output PATH``
   Output file or folder.
``--format {markdown,json,both}``
   Default ``markdown``.
``--table-style {minimal_html,markdown_grid,styled_html}``
   How tables with merged cells are written (default ``minimal_html``; :doc:`tables`).
``--encoding ENC``
   Encoding of the files written (default ``utf-8``).
``--include-metadata``
   Put a ``---`` block with the file name, size and modification time before the content.
``--max-length N``
   Cut the content after ``N`` characters and add ``... (truncated)``.
``--preserve-structure``
   Accepted but has no effect.

Folders
~~~~~~~

``-r, --recursive``
   Include sub-folders.
``--pattern GLOB``
   Which entries to convert (default ``*``: every entry, sub-folders and unsupported files
   included, so use a pattern such as ``"*.pdf"`` or ``--skip-errors``).
``--exclude GLOB``
   Leave out file names matching ``GLOB`` (repeatable).
``--max-files N``, ``--sort {name,size,date}``
   Keep the first ``N`` entries after sorting (ascending; default ``name``).
``-p, --parallel N``
   Convert ``N`` files at once in separate processes.
``--skip-errors``
   Report a file that fails and go on (the command then exits 0); without it the first failure
   stops the run.
``--retry N``
   Try a failing file ``N`` more times (default ``1``).
``--timeout SECONDS``
   Accepted but not enforced.

Messages
~~~~~~~~

``-v, --verbose``
   Debug logging on stderr, and the whole document on stdout.
``-q, --quiet``
   Errors only.
``--log-file FILE``
   Also log to ``FILE``.
``--progress {bar,dots,none}``, ``--no-color``
   Progress display for folders (on stdout; default ``bar``).

Exit codes
----------

``0`` success (also when ``--skip-errors`` skipped files), ``1`` the input does not exist or a
conversion failed (the message is on stderr), ``2`` invalid options. An OCR engine that cannot
run (for example Tesseract without the requested language) is a failure.

.. code-block:: bash

   doc2mark scan.pdf --ocr tesseract --ocr-images --ocr-lang eng+chi_tra -o scan.md
   doc2mark documents/ -r --pattern "*.pdf" -p 4 -o converted/
   doc2mark documents/ -r --exclude "*.csv" --skip-errors -o converted/
   doc2mark report.pdf --table-style markdown_grid --include-metadata --max-length 5000 -o report.md
   DOC2MARK_JUDGE=typesafe doc2mark report.pdf --ocr tesseract --ocr-images -o report.md
