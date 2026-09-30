"""A local stand-in for the OpenAI Chat Completions API, for E2E tests of the LLM OCR path.

A real model cannot be driven into a refusal, a malicious table or a router-firewall violation
on demand, so the OCR E2E tests point doc2mark's OpenAI provider at this server through
``OPENAI_BASE_URL``. It replaces ONLY the third-party model: the ``doc2mark`` CLI, LangChain and
the ``openai`` SDK all run for real and talk HTTP to it. Nothing from ``doc2mark`` is imported.

Replies are scripted per request kind. A *structured* request is one that carries a
``response_format`` (doc2mark's structured OCR call); every other request is *free-form*
(doc2mark's free-form recovery call). Each kind has its own queue of replies, consumed in
arrival order; once a queue is down to its last reply, that reply is repeated. ``delay`` holds every
reply back that many seconds (for timeout and concurrency tests); ``max_in_flight`` is the largest
number of requests the server was answering at once.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterable, List, Optional

API_KEY = "sk-e2e-fake-not-a-real-key"


def page(text: str = "", *, tables: Iterable[dict] = (), interpretation: Optional[dict] = None, **raw) -> dict:
    """A structured reply whose content is an OCRPage JSON document (``raw`` extras such as
    ``fields`` or ``headings`` go in ``**raw``)."""
    body = {"raw": {"text": text, "tables": list(tables), **raw}, "interpretation": interpretation}
    return {"message": {"content": json.dumps(body, ensure_ascii=False)}}


def table(html: str = "", *, headers=(), rows=(), markdown: str = "", caption: str = "",
          illustrative: bool = False, row_count: Optional[int] = None) -> dict:
    """One ``raw.tables`` entry."""
    return {
        "caption": caption,
        "headers": list(headers),
        "rows": [list(r) for r in rows],
        "html": html,
        "markdown": markdown,
        "illustrative": illustrative,
        "row_count": row_count,
    }


def interpretation(**fields) -> dict:
    """An ``interpretation`` object; unspecified fields take the schema defaults."""
    return dict(fields)


def text(content: Optional[str], finish_reason: str = "stop") -> dict:
    """A reply whose message content is exactly ``content`` (for a structured request this is
    what a model sends when it ignores the JSON schema)."""
    return {"message": {"content": content}, "finish_reason": finish_reason}


def refusal(message: str) -> dict:
    """A reply that uses the API's native refusal field (``message.refusal``, content null)."""
    return {"message": {"content": None, "refusal": message}}


class FakeOpenAI:
    """Serve ``POST <base_url>/chat/completions`` from scripted replies. Use as a context manager."""

    def __init__(self, delay: float = 0.0):
        self.requests: List[Dict[str, Any]] = []
        self.delay = delay
        self.max_in_flight = 0
        self._in_flight = 0
        self._queues: Dict[str, List[dict]] = {"structured": [], "free_form": []}
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True  # a reply held back for a client that timed out must not block shutdown
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeOpenAI":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    @property
    def env(self) -> Dict[str, str]:
        """Environment for ``run_cli(..., env=...)`` that routes ``--ocr openai`` here."""
        host, port = self._server.server_address[:2]
        return {"OPENAI_BASE_URL": f"http://{host}:{port}/v1", "OPENAI_API_KEY": API_KEY}

    def script(self, *, structured: Iterable[dict] = (), free_form: Iterable[dict] = ()) -> None:
        """Queue replies for structured and free-form requests (see the module docstring)."""
        with self._lock:
            self._queues["structured"] = list(structured)
            self._queues["free_form"] = list(free_form)

    def requests_of(self, kind: str) -> List[Dict[str, Any]]:
        """Request bodies of one kind (``"structured"`` or ``"free_form"``), in arrival order."""
        return [body for body in self.requests if _kind(body) == kind]

    @staticmethod
    def prompt_text(body: Dict[str, Any]) -> str:
        """All text doc2mark sent in one request (system prompt and text parts), newline-joined."""
        chunks = []
        for message in body.get("messages", []):
            content = message.get("content")
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                chunks.extend(part.get("text", "") for part in content if isinstance(part, dict))
        return "\n".join(chunks)

    def _next_reply(self, kind: str) -> dict:
        with self._lock:
            queue = self._queues[kind]
            if not queue:
                raise AssertionError(f"FakeOpenAI: no {kind} reply scripted")
            return queue.pop(0) if len(queue) > 1 else queue[0]

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                with fake._lock:
                    fake.requests.append(body)
                    fake._in_flight += 1
                    fake.max_in_flight = max(fake.max_in_flight, fake._in_flight)
                try:
                    if fake.delay:
                        time.sleep(fake.delay)
                    self._reply(body)
                finally:
                    with fake._lock:
                        fake._in_flight -= 1

            def _reply(self, body):
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    self._send(404, {"error": {"message": f"unexpected path {self.path}"}})
                    return
                try:
                    reply = fake._next_reply(_kind(body))
                except AssertionError as exc:
                    self._send(500, {"error": {"message": str(exc)}})
                    return
                message = {"role": "assistant", "content": None, "refusal": None, **reply["message"]}
                self._send(200, {
                    "id": f"chatcmpl-e2e-{len(fake.requests)}",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body.get("model", "fake-model"),
                    "choices": [{
                        "index": 0,
                        "message": message,
                        "finish_reason": reply.get("finish_reason", "stop"),
                        "logprobs": None,
                    }],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                })

            def _send(self, status: int, payload: dict) -> None:
                data = json.dumps(payload).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):  # the client gave up waiting (a timeout test)
                    pass

        return Handler


def _kind(body: Dict[str, Any]) -> str:
    return "structured" if "response_format" in body else "free_form"
