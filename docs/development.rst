Development
===========

Install development dependencies:

.. code-block:: bash

   pip install -e ".[all,dev,docs]"

Run tests:

.. code-block:: bash

   python -m pytest

This also collects the end-to-end tests described below; add ``-m "not e2e"`` to
leave them out.

Build docs locally:

.. code-block:: bash

   python -m sphinx -b html -W --keep-going docs docs/_build/html

End-to-end tests
----------------

``tests/e2e`` drives the real ``doc2mark`` CLI as a subprocess and asserts only on
what it writes: exit code, stdout/stderr and the ``.md`` / ``.json`` output files.
Every test in that directory gets the ``e2e`` marker automatically, so ``-m e2e``
selects the suite and ``-m "not e2e"`` is the plain unit suite.

The suite needs system tools that ``pip`` does not provide: Tesseract (with the
``eng``, ``chi_tra`` and ``chi_sim`` language data) and LibreOffice (``soffice``).

Docker (reference environment)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   scripts/run_e2e_docker.sh -v                  # whole e2e suite
   scripts/run_e2e_docker.sh -k tesseract        # any pytest arguments
   scripts/run_e2e_docker.sh -m "not e2e" -q     # unit suite in the same image

The script builds ``tests/e2e/Dockerfile`` as ``d2m-e2e:<hash of the Dockerfile>``
when that tag is missing, so editing the Dockerfile rebuilds the image. It copies
the checkout into a throwaway container (the checkout itself is mounted read-only),
installs it with ``pip install -e ".[ocr,dev]"`` (pip cache in the
``d2m-e2e-pip-cache`` volume), sets ``D2M_E2E_STRICT=1`` and runs
``pytest -m e2e`` with your arguments appended; a later ``-m`` replaces
``-m e2e``. The exit code is pytest's, or 90 when the runner itself could not set
the run up (image build, copying the checkout, ``pip install``, or a name in
``D2M_E2E_PASS_ENV`` that is not set).

To forward host environment variables into the container, list their names in
``D2M_E2E_PASS_ENV`` (space-separated). Only the names go on the ``docker run``
command line, so a secret can come from a file:

.. code-block:: bash

   set -a; . ~/.secrets/openai.env; set +a
   D2M_E2E_PASS_ENV="OPENAI_API_KEY" scripts/run_e2e_docker.sh -v

Locally
~~~~~~~

Install Tesseract and LibreOffice with your package manager, then:

.. code-block:: bash

   pip install -e ".[ocr,dev]"
   python -m pytest -m e2e -v

A test that needs a tool that is not on ``PATH`` is skipped with that reason.
With ``D2M_E2E_STRICT=1`` (set by the Docker runner) it fails instead, so a broken
environment cannot pass by skipping.

Writing E2E tests
~~~~~~~~~~~~~~~~~

``tests/e2e/conftest.py`` provides:

* ``run_cli(input_path, *args, fmt="markdown", env=None, timeout=300, raw=False)``:
  runs the installed ``doc2mark`` console script on one file and returns a result
  with ``exit_code``, ``stdout``, ``stderr``, ``markdown``, ``json`` (parsed, when
  ``fmt`` is ``json`` or ``both``), ``out_dir`` and ``describe()`` for assertion
  messages. It supplies ``-o`` and ``--format`` itself, and each call writes to its
  own fresh directory. ``raw=True`` leaves the whole command line to you (directory
  input, stdout output); ``markdown`` and ``json`` are then ``None``.
* ``e2e_dir``: a per-test scratch directory named ``e2e-<test>-<timestamp>`` for
  input files.
* ``require_tool("tesseract")`` / ``require_tool("soffice")``: call it first in a
  test that needs that binary.

``tests/e2e/pdfgen.py`` builds input PDFs at test time with PyMuPDF and Pillow:
``text_pdf`` (Latin, or CJK with ``cjk=True``) and ``image_pdf`` (page images with
no text layer, so only OCR can read them). Put builders that belong to one lane in
your own module so parallel work does not conflict.

.. code-block:: python

   from tests.e2e import pdfgen

   def test_ocr_reads_scanned_page(run_cli, require_tool, e2e_dir):
       require_tool("tesseract")
       pdf = pdfgen.image_pdf(e2e_dir / "scan.pdf", "HELLO 123")
       result = run_cli(pdf, "--ocr", "tesseract", "--ocr-images")
       assert result.exit_code == 0, result.describe()
       assert "HELLO 123" in result.markdown

GitHub Pages
------------

The documentation workflow builds Sphinx HTML and uploads the generated static
site to GitHub Pages. In repository settings, set Pages source to
``GitHub Actions``.
