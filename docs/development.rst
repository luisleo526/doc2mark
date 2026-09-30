Development
===========

.. code-block:: bash

   git clone https://github.com/luisleo526/doc2mark && cd doc2mark
   pip install -e ".[all,dev,docs]"

Unit tests
----------

.. code-block:: bash

   python -m pytest -m "not integration and not requires_api_key and not e2e" -q   # as CI runs them

A plain ``python -m pytest`` also collects the end-to-end suite (below); ``-m "not e2e"`` leaves it
out. Markers (``pyproject.toml``): ``e2e`` (added automatically to everything under
``tests/e2e``), ``requires_api_key`` (calls OpenAI; CI runs them only when the repository has the
secret), ``requires_typesafe`` (calls TypeSafe; skipped without the ``typesafe`` extra or
``TYPESAFE_API_KEY``, failed instead when ``D2M_REQUIRE_TYPESAFE=1``), ``integration``, ``slow``,
``ocr``, ``unit``.

End-to-end tests
----------------

``tests/e2e`` runs the installed ``doc2mark`` command as a subprocess against real Tesseract and
LibreOffice and checks only what it writes: exit code, stdout/stderr and the output files. It
needs Tesseract with the ``eng``, ``chi_tra`` and ``chi_sim`` data and LibreOffice (``soffice``);
the LLM OCR tests point the OpenAI provider at a local stand-in for the API
(``tests/e2e/fake_openai.py``), so no key is needed.

Docker (the reference environment)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   scripts/run_e2e_docker.sh -q -n 8              # the E2E suite, 8 pytest-xdist workers
   scripts/run_e2e_docker.sh -k tesseract -v      # any pytest arguments
   scripts/run_e2e_docker.sh -m "not e2e" -q -n 8 # the unit suite in the same image

The script builds ``tests/e2e/Dockerfile`` (Python 3.12, Tesseract, LibreOffice, Noto CJK fonts)
as ``d2m-e2e:<hash of the Dockerfile>`` when that tag is missing, copies the checkout into a
throwaway container (the checkout is mounted read-only), installs it with ``pip install -e
".[$D2M_E2E_EXTRAS]"`` and runs ``pytest -m e2e`` with your arguments; a later ``-m`` replaces
``-m e2e``. The exit code is pytest's, or 90 when the run could not be set up (image build,
copying the checkout, ``pip install``, a variable named in ``D2M_E2E_PASS_ENV`` that is not
set). CI runs the suite on every pull request with ``-n 4`` and ``OMP_THREAD_LIMIT=1``
(Tesseract otherwise starts several OpenMP threads per call, which slows parallel runs down).

Environment variables:

``D2M_E2E_STRICT=1``
   Set by the script: a test whose tool (``tesseract``, ``soffice``) is missing fails instead of
   being skipped, so a broken environment cannot pass.
``D2M_E2E_PASS_ENV``
   Space-separated names of host variables to forward into the container. Only the names go on
   the ``docker run`` command line, so a secret can come from a file.
``D2M_E2E_EXTRAS``
   The extras installed in the container (default ``ocr,dev``).
``D2M_REQUIRE_TYPESAFE=1``
   The ``requires_typesafe`` tests fail instead of skipping when the extra or the key is missing.

.. code-block:: bash

   set -a; . ~/.config/typesafe/env; set +a            # defines TYPESAFE_API_KEY
   D2M_E2E_EXTRAS="ocr,dev,typesafe" D2M_REQUIRE_TYPESAFE=1 \
       D2M_E2E_PASS_ENV="TYPESAFE_API_KEY D2M_REQUIRE_TYPESAFE" scripts/run_e2e_docker.sh -q -n 8

Without Docker
~~~~~~~~~~~~~~

Install Tesseract (with the languages above) and LibreOffice, then:

.. code-block:: bash

   pip install -e ".[ocr,dev]"
   python -m pytest -m e2e -q

A test whose tool is not on ``PATH`` is skipped with that reason, unless ``D2M_E2E_STRICT=1``.

Writing an E2E test
~~~~~~~~~~~~~~~~~~~

``tests/e2e/conftest.py`` provides the fixtures:

- ``run_cli(input_path, *args, fmt="markdown", env=None, timeout=300, raw=False)`` runs the
  ``doc2mark`` console script on one file and returns ``exit_code``, ``stdout``, ``stderr``,
  ``markdown``, ``json`` (parsed, for ``fmt="json"`` or ``"both"``), ``out_dir`` and
  ``describe()`` for assertion messages. It adds ``-o`` and ``--format`` itself; ``raw=True``
  leaves the whole command line to you.
- ``e2e_dir``: a per-test scratch folder named ``e2e-<test>-<timestamp>`` for input files.
- ``require_tool("tesseract")`` / ``require_tool("soffice")``: call it first in a test that needs
  the program.

``tests/e2e/pdfgen.py`` builds input PDFs at test time (``text_pdf``, ``image_pdf``,
``text_png``); lane-specific builders live in ``tests/e2e/builders_*.py``.

.. code-block:: python

   from tests.e2e import pdfgen

   def test_ocr_reads_scanned_page(run_cli, require_tool, e2e_dir):
       require_tool("tesseract")
       pdf = pdfgen.image_pdf(e2e_dir / "scan.pdf", "HELLO 123")
       result = run_cli(pdf, "--ocr", "tesseract", "--ocr-images")
       assert result.exit_code == 0, result.describe()
       assert "HELLO 123" in result.markdown

Documentation
-------------

.. code-block:: bash

   python -m sphinx -b html -W --keep-going docs docs/_build/html

The API reference is generated from the docstrings (``sphinx.ext.autodoc``), so signatures cannot
drift from the code. ``eval/docs_audit/`` runs every code example of the README and of these pages
in the E2E image and checks its output (``eval/docs_audit/run_on_spark.sh OUT_DIR``; see
``eval/docs_audit/README.md``). The Docs workflow builds the site on pull requests and publishes
it to GitHub Pages from ``main``.
