"""Fixtures for the CLI-driven E2E suite (see docs/development.rst).

Tests here observe only what the ``doc2mark`` console script does: its exit code,
stdout/stderr and the files it writes. Nothing from ``doc2mark`` is imported.
"""

import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

E2E_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items):
    """Mark everything under tests/e2e as ``e2e``, so ``-m e2e`` and ``-m "not e2e"`` always split the suites."""
    for item in items:
        if E2E_DIR in item.path.resolve().parents:
            item.add_marker(pytest.mark.e2e)


@dataclass(frozen=True)
class CliResult:
    """One CLI run. ``markdown`` / ``json`` are None when the CLI did not write that file."""

    argv: tuple
    exit_code: int
    stdout: str
    stderr: str
    out_dir: Path
    markdown: Optional[str] = None
    json: Optional[dict] = None

    def describe(self) -> str:
        """Everything the run produced as one string, for assertion messages."""
        parts = [
            f"argv: {' '.join(self.argv)}",
            f"exit code: {self.exit_code}",
            f"--- stdout ---\n{self.stdout}",
            f"--- stderr ---\n{self.stderr}",
        ]
        if self.markdown is not None:
            parts.append(f"--- markdown ---\n{self.markdown}")
        return "\n".join(parts)


def _cli() -> str:
    """The ``doc2mark`` console script, the way users run it (the one next to this interpreter wins)."""
    search_path = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
    script = shutil.which("doc2mark", path=search_path)
    if script is None:
        pytest.fail("`doc2mark` console script not found: run `pip install -e .` (docs/development.rst)", pytrace=False)
    return script


@pytest.fixture
def e2e_dir(request, tmp_path) -> Path:
    """Per-test scratch dir ``e2e-<test name>-<timestamp>``: build inputs here; ``run_cli`` writes outputs under it."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)
    path = tmp_path / f"e2e-{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    path.mkdir()
    return path


@pytest.fixture
def run_cli(e2e_dir):
    """Run ``doc2mark <input_path> <args>`` in a subprocess and return a :class:`CliResult`.

    The fixture owns ``-o`` and ``--format``, so do not pass them: ``fmt`` is
    ``markdown`` (default), ``json`` or ``both``, and the output is read back from
    ``result.md`` / ``result.json``. Every call writes to its own fresh
    ``<e2e_dir>/out-<n>/``, so a run that writes nothing cannot be mistaken for an
    earlier run. ``env`` overrides the inherited environment (a ``None`` value
    removes the variable).
    """
    runs = itertools.count(1)

    def run(input_path, *args, fmt="markdown", env=None, timeout=300) -> CliResult:
        out_dir = e2e_dir / f"out-{next(runs)}"
        out_dir.mkdir()
        argv = [_cli(), str(input_path), "-o", str(out_dir / "result"), "--format", fmt, *map(str, args)]
        child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        for key, value in (env or {}).items():
            if value is None:
                child_env.pop(key, None)
            else:
                child_env[key] = value
        try:
            proc = subprocess.run(
                argv,
                cwd=e2e_dir,
                env=child_env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"doc2mark did not finish within {timeout}s: {' '.join(argv)}", pytrace=False)
        markdown_file, json_file = out_dir / "result.md", out_dir / "result.json"
        return CliResult(
            argv=tuple(argv),
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            out_dir=out_dir,
            markdown=markdown_file.read_text(encoding="utf-8") if markdown_file.exists() else None,
            json=json.loads(json_file.read_text(encoding="utf-8")) if json_file.exists() else None,
        )

    return run


@pytest.fixture
def require_tool():
    """Call ``require_tool("tesseract")`` (or ``"soffice"``) first in a test that needs that binary.

    A missing tool skips the test, unless ``D2M_E2E_STRICT=1`` (set by
    ``scripts/run_e2e_docker.sh``), where it fails the test instead.
    """

    def require(name: str) -> str:
        path = shutil.which(name)
        if path:
            return path
        if os.environ.get("D2M_E2E_STRICT") == "1":
            pytest.fail(f"required tool {name!r} is not on PATH (D2M_E2E_STRICT=1)", pytrace=False)
        pytest.skip(f"{name!r} is not on PATH; install it or use scripts/run_e2e_docker.sh")

    return require
