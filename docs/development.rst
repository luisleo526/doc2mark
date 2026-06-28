Development
===========

Install development dependencies:

.. code-block:: bash

   pip install -e ".[all,dev,docs]"

Run tests:

.. code-block:: bash

   python -m pytest

Build docs locally:

.. code-block:: bash

   python -m sphinx -b html -W --keep-going docs docs/_build/html

GitHub Pages
------------

The documentation workflow builds Sphinx HTML and uploads the generated static
site to GitHub Pages. In repository settings, set Pages source to
``GitHub Actions``.
