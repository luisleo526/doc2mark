"""Sphinx configuration for doc2mark."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

project = "doc2mark"
author = "doc2mark Team"
copyright = "2026, doc2mark Team"

from doc2mark import __version__  # noqa: E402

version = __version__
release = __version__

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
]

autosummary_generate = True
autodoc_typehints = "description"
napoleon_google_docstring = True
napoleon_numpy_docstring = True

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_title = f"doc2mark {release}"
html_show_sourcelink = True
