"""Inputs for tests/e2e/test_ocrcache_fixes.py: an image file, a scanned PDF, and a stand-in for the Gemini client.

Vertex AI cannot be called without Google Cloud credentials, so the Vertex tests put a stub
``langchain_google_genai`` package first on ``PYTHONPATH`` (:func:`stub_gemini_client`). It replaces only that
third-party client module: doc2mark and LangChain run unmodified, build the client as they always do, and send
it their requests. The stub writes one JSON line per client it is given the settings of (``{"client": {...}}``)
and per request it answers (``{"request": [<text of each message>]}``) to ``$D2M_STUB_GEMINI_LOG``, and answers
every structured request with a page whose text is ``$D2M_STUB_GEMINI_ANSWER``.
"""

import json
import textwrap
from pathlib import Path
from typing import List

from tests.e2e import builders_ocr, pdfgen


def image_file(path: Path, text: str) -> Path:
    """A PNG picture of ``text`` (no text layer: only OCR can read it)."""
    path.write_bytes(pdfgen.text_png(text))
    return path


def scan_pdf(path: Path, pages: List[str]) -> Path:
    """An image-only PDF, one full-page picture per page: one OCR request per page, all in one batch."""
    return builders_ocr.scan_pdf(path, pages)


_STUB = '''
"""Stand-in for the langchain_google_genai package in the doc2mark E2E tests (see builders_ocrcache_fixes)."""
import json
import os

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

_SCALARS = (str, int, float, bool, type(None))


def _log(entry):
    with open(os.environ["D2M_STUB_GEMINI_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(entry, ensure_ascii=False) + "\\n")


def _texts(prompt):
    texts = []
    for message in prompt.to_messages():
        content = message.content
        if isinstance(content, str):
            texts.append(content)
        else:
            texts.append("\\n".join(part.get("text", "") for part in content
                                    if isinstance(part, dict) and part.get("type") == "text"))
    return texts


class ChatGoogleGenerativeAI(RunnableLambda):
    """Records the settings doc2mark builds the client with; answers each request like Gemini."""

    def __init__(self, **settings):
        _log({"client": {key: value for key, value in settings.items() if isinstance(value, _SCALARS)}})
        self.settings = settings
        super().__init__(self._free_form)

    def _answer(self, prompt, content):
        _log({"request": _texts(prompt)})
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        return AIMessage(content=content, usage_metadata=usage)

    def _free_form(self, prompt):
        return self._answer(prompt, os.environ.get("D2M_STUB_GEMINI_ANSWER", "STUB GEMINI ANSWER"))

    def with_structured_output(self, schema, method=None, include_raw=False):
        def structured(prompt):
            text = os.environ.get("D2M_STUB_GEMINI_ANSWER", "STUB GEMINI ANSWER")
            message = self._answer(prompt, json.dumps({"raw": {"text": text}, "interpretation": None}))
            return {"raw": message, "parsed": schema.model_validate_json(message.content), "parsing_error": None}
        return RunnableLambda(structured)
'''


def stub_gemini_client(root: Path) -> Path:
    """Write the stub ``langchain_google_genai`` package under ``root`` and return ``root`` (prepend it to
    ``PYTHONPATH``)."""
    package = root / "langchain_google_genai"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(textwrap.dedent(_STUB).lstrip(), encoding="utf-8")
    return root


def gemini_log(path: Path) -> dict:
    """What the stub recorded: ``{"clients": [settings, ...], "requests": [[message texts], ...]}``."""
    clients, requests = [], []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if "client" in entry:
                clients.append(entry["client"])
            else:
                requests.append(entry["request"])
    return {"clients": clients, "requests": requests}
