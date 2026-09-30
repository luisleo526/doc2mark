"""E2E tests for the OCR provider, cache and judge issues of the docs audit (PR #27, "Code issues found" 10-13
and 15-18).

One test (the recovery call's tokens) runs the ``doc2mark`` CLI. The others use the public Python API in a
subprocess, because what they cover has no CLI switch: the loader's ``model``, ``top_p``, ``max_workers``,
``default_prompt``, ``project`` ... arguments, the ``OCR`` facade, ``OCRConfig(response_model=...)``, the OCR
cache (``ocr_cache=``), the document cache (``cache_dir=``) and a ``non_content_judge`` of your own.

Only third parties are replaced. The OpenAI model is a local OpenAI-compatible server
(``tests/e2e/fake_openai.py``, reached through ``OPENAI_BASE_URL``): the ``openai`` SDK and LangChain send it
real HTTP requests, and the tests read the request bodies it received. Vertex AI cannot be called without
Google Cloud credentials, so the Vertex tests put a stub ``langchain_google_genai`` package first on the path
(``builders_ocrcache_fixes.stub_gemini_client``): doc2mark and LangChain build the Gemini client and send it
their requests as usual, and the stub records the client's settings and answers like Gemini.
"""

import json
import os
import subprocess
import sys

import pytest

from tests.e2e import builders_ocrcache_fixes as build
from tests.e2e import fake_openai as fake
from tests.e2e.fake_openai import FakeOpenAI

REFUSAL = "I'm sorry, but I can't assist with that request."
TABLE_TASK_PROMPT = "This image is dominated by tabular data."
RECEIPT_TASK_PROMPT = "This is a receipt or invoice."


def run_api(e2e_dir, script, *args, env=None, timeout=300):
    """Run ``script`` with this interpreter (the public ``doc2mark`` Python API) in the test's scratch dir.
    ``env`` overrides the inherited environment (a ``None`` value removes the variable)."""
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    for key, value in (env or {}).items():
        if value is None:
            child_env.pop(key, None)
        else:
            child_env[key] = value
    proc = subprocess.run([sys.executable, "-c", script, *map(str, args)], cwd=e2e_dir, capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout, env=child_env)
    assert proc.returncode == 0, f"exit {proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    return proc


def last_json(proc):
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture
def fake_llm():
    with FakeOpenAI() as server:
        yield server


@pytest.fixture
def receipt(e2e_dir):
    return build.image_file(e2e_dir / "receipt.png", "ACME STORE TOTAL 12.50")


# --------------------------------------------------------------------------------------------------------------
# 10. Loader and facade settings that never reached a request

SAMPLING_SCRIPT = (
    "import json, sys\n"
    "from doc2mark import OCR, UnifiedDocumentLoader\n"
    "how, path, knobs = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])\n"
    "if how == 'loader':\n"
    "    UnifiedDocumentLoader(ocr_provider='openai', **knobs).load(path, ocr_images=True)\n"
    "else:\n"
    "    OCR('openai', **knobs).read_one(open(path, 'rb').read())\n"
)
SAMPLING = {"top_p": 0.1, "frequency_penalty": 0.5, "presence_penalty": 0.25}


@pytest.mark.parametrize("how", ["loader", "facade"])
def test_sampling_settings_reach_the_openai_request(e2e_dir, fake_llm, receipt, how):
    """Issue 10: ``UnifiedDocumentLoader(top_p=..., frequency_penalty=..., presence_penalty=...)`` were stored and
    never sent (the request carried only ``max_completion_tokens, model, response_format, stream``), and the
    facade rejected them with ``TypeError``. Both now send them."""
    fake_llm.script(structured=[fake.page("ACME STORE TOTAL 12.50")])

    run_api(e2e_dir, SAMPLING_SCRIPT, how, receipt, json.dumps(SAMPLING), env=fake_llm.env)

    [body] = fake_llm.requests_of("structured")
    assert {key: body.get(key) for key in SAMPLING} == SAMPLING, sorted(body)


def test_default_requests_carry_no_sampling_settings(e2e_dir, fake_llm, receipt):
    """The loader's defaults (the API's own defaults) are not sent: reasoning models such as gpt-5 reject the
    parameters, and a default request stays as it was."""
    fake_llm.script(structured=[fake.page("ACME STORE TOTAL 12.50")])

    run_api(e2e_dir, SAMPLING_SCRIPT, "loader", receipt, "{}", env=fake_llm.env)

    [body] = fake_llm.requests_of("structured")
    assert not set(SAMPLING) & set(body), sorted(body)


MAX_WORKERS_SCRIPT = (
    "import sys\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "UnifiedDocumentLoader(ocr_provider='openai', max_workers=int(sys.argv[2])).load(sys.argv[1], ocr_images=True)\n"
)


def test_max_workers_caps_concurrent_requests(e2e_dir):
    """Issue 10: the loader's ``max_workers`` was stored and never used; a six-page scan went out six requests at
    once whatever it said. It caps the concurrent OCR requests (when ``OCRConfig.max_concurrency`` is not set)."""
    pages = [f"LEDGER PAGE {n}" for n in range(1, 7)]
    scan = build.scan_pdf(e2e_dir / "scan.pdf", pages)
    with FakeOpenAI(delay=1.0) as server:
        server.script(structured=[fake.page("LEDGER")])
        run_api(e2e_dir, MAX_WORKERS_SCRIPT, scan, 2, env={**server.env, "OCR_MAX_CONCURRENCY": None})

    assert len(server.requests_of("structured")) == len(pages), len(server.requests)
    assert server.max_in_flight == 2, server.max_in_flight


DEFAULT_PROMPT_SCRIPT = (
    "import sys\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "loader = UnifiedDocumentLoader(ocr_provider='openai', default_prompt=sys.argv[2],\n"
    "                               structured=sys.argv[3] == 'structured')\n"
    "print(loader.load(sys.argv[1], ocr_images=True).content)\n"
)
CUSTOM_PROMPT = "Transcribe the ledger page exactly, column by column."


@pytest.mark.parametrize("mode", ["free-form", "structured"])
def test_default_prompt_is_the_free_form_prompt(e2e_dir, fake_llm, receipt, mode):
    """Issue 10: ``default_prompt`` was stored and never used in any prompt. Like ``prompt_template`` it is the
    prompt of free-form requests: ``structured=False``, and the free-form retry of an empty structured answer
    (structured requests keep their task prompt)."""
    fake_llm.script(structured=[fake.page("")], free_form=[fake.text("LEDGER 4471")])

    proc = run_api(e2e_dir, DEFAULT_PROMPT_SCRIPT, receipt, CUSTOM_PROMPT, mode, env=fake_llm.env)

    assert "LEDGER 4471" in proc.stdout, proc.stdout
    [free_form] = fake_llm.requests_of("free_form")
    assert CUSTOM_PROMPT in FakeOpenAI.prompt_text(free_form), FakeOpenAI.prompt_text(free_form)[:300]
    for body in fake_llm.requests_of("structured"):
        assert CUSTOM_PROMPT not in FakeOpenAI.prompt_text(body)


FACADE_TIMEOUT_SCRIPT = (
    "import json, sys\n"
    "from doc2mark import OCR\n"
    "result = OCR('openai', timeout=1, max_retries=1).read_one(open(sys.argv[1], 'rb').read())\n"
    "print(json.dumps({'failed': bool((result.metadata or {}).get('failed')), 'text': result.text}))\n"
)


def test_facade_timeout_and_retries_reach_the_openai_client(e2e_dir, receipt):
    """Issue 10/11: ``OCR("openai", timeout=1, max_retries=1)`` went into the inert ``OCRConfig.timeout`` /
    ``max_retries`` fields (a hidden DeprecationWarning), and the request waited the provider's default 30 s
    with 3 retries. They are the provider's request timeout and retries now: a reply held back 4 s times out,
    is tried once more, and the image is reported failed."""
    with FakeOpenAI(delay=4.0) as server:
        server.script(structured=[fake.page("ACME STORE TOTAL 12.50")])
        proc = run_api(e2e_dir, FACADE_TIMEOUT_SCRIPT, receipt, env=server.env)

    output = last_json(proc)
    assert output["failed"] is True and "ACME" not in output["text"], output
    assert len(server.requests) == 2, len(server.requests)


# --------------------------------------------------------------------------------------------------------------
# 11. Vertex AI / Gemini settings

VERTEX_SCRIPT = (
    "import json, sys\n"
    "from doc2mark import OCR, UnifiedDocumentLoader\n"
    "how, path, settings = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])\n"
    "if how.startswith('OCR:'):\n"
    "    print(json.dumps(OCR(how[4:], **settings).read_one(open(path, 'rb').read()).text))\n"
    "else:\n"
    "    print(json.dumps(UnifiedDocumentLoader(ocr_provider=how, **settings).load(path, ocr_images=True).content))\n"
)
VERTEX_CASES = {
    "facade vertex_ai": ("OCR:vertex_ai",
                         {"project": "my-gcp-project", "location": "europe-west4", "model": "gemini-2.0-flash",
                          "timeout": 7, "max_retries": 2},
                         {"project": "my-gcp-project", "location": "europe-west4", "model": "gemini-2.0-flash",
                          "timeout": 7, "max_retries": 2}),
    "facade gemini": ("OCR:gemini", {"model": "gemini-2.0-flash", "temperature": 0.3, "max_tokens": 2048},
                      {"model": "gemini-2.0-flash", "temperature": 0.3, "max_output_tokens": 2048}),
    "loader gemini": ("gemini",
                      {"model": "gemini-2.0-flash", "location": "europe-west4", "project": "my-gcp-project",
                       "timeout": 7, "max_retries": 2, "top_p": 0.2, "frequency_penalty": 0.5,
                       "presence_penalty": 0.25},
                      {"model": "gemini-2.0-flash", "location": "europe-west4", "project": "my-gcp-project",
                       "timeout": 7, "max_retries": 2, "top_p": 0.2, "frequency_penalty": 0.5,
                       "presence_penalty": 0.25}),
    "loader vertex_ai": ("vertex_ai", {"timeout": 7, "max_retries": 2, "top_p": 0.2},
                         {"model": "gemini-3.1-flash-lite-preview", "location": "global", "timeout": 7,
                          "max_retries": 2, "top_p": 0.2}),
}


@pytest.mark.parametrize("case", list(VERTEX_CASES))
def test_vertex_settings_reach_the_gemini_client(e2e_dir, receipt, case):
    """Issue 11 (and the Vertex half of 10): ``OCR("vertex_ai", project=...)`` raised ``TypeError``;
    ``OCR("vertex_ai"/"gemini", model=...)`` kept the default model; ``UnifiedDocumentLoader(ocr_provider="gemini",
    model=..., location=...)`` ignored both (only ``"vertex_ai"`` passed them); and the loader passed neither
    ``timeout`` / ``max_retries`` nor the sampling settings to Vertex AI. Every one reaches the Gemini client."""
    how, settings, expected = VERTEX_CASES[case]
    stub = build.stub_gemini_client(e2e_dir / "stub")
    log = e2e_dir / "gemini.jsonl"
    env = {"PYTHONPATH": os.pathsep.join(filter(None, [str(stub), os.environ.get("PYTHONPATH")])),
           "D2M_STUB_GEMINI_LOG": str(log), "D2M_STUB_GEMINI_ANSWER": "GEMINI READ ACME 12.50",
           "GOOGLE_CLOUD_PROJECT": None}

    proc = run_api(e2e_dir, VERTEX_SCRIPT, how, receipt, json.dumps(settings), env=env)

    assert "GEMINI READ ACME 12.50" in last_json(proc), proc.stdout
    recorded = build.gemini_log(log)
    assert len(recorded["requests"]) == 1, recorded
    [client] = recorded["clients"]
    assert client.get("vertexai") is True, client
    assert {key: client.get(key) for key in expected} == expected, client


# --------------------------------------------------------------------------------------------------------------
# 12. The OCR cache key

OCR_CACHE_SCRIPT = (
    "import json, sys\n"
    "from doc2mark import MemoryOCRCache, OCRConfig, UnifiedDocumentLoader\n"
    "cache = MemoryOCRCache()\n"
    "contents = []\n"
    "for settings in json.loads(sys.argv[2]):\n"
    "    config = OCRConfig(**settings.pop('ocr_config', {}))\n"
    "    loader = UnifiedDocumentLoader(ocr_provider='openai', ocr_cache=cache, ocr_config=config, **settings)\n"
    "    contents.append(loader.load(sys.argv[1], ocr_images=True).content)\n"
    "print(json.dumps(contents))\n"
)


@pytest.mark.parametrize("first, second", [
    ({"task": "receipt"}, {"task": "table"}),
    ({"ocr_config": {"task": "receipt"}}, {"ocr_config": {"task": "table"}}),
], ids=["loader task", "OCRConfig task"])
def test_ocr_cache_does_not_replay_an_answer_made_for_another_task(e2e_dir, fake_llm, receipt, first, second):
    """Issue 12: the OCR cache key left out ``OCRConfig.task``, so two loaders that differ only in their task and
    share a cache replayed the receipt answer for the table request. The second request is sent, with the table
    task's prompt."""
    fake_llm.script(structured=[fake.page("RECEIPT ANSWER 12.50"), fake.page("TABLE ANSWER 12.50")])

    proc = run_api(e2e_dir, OCR_CACHE_SCRIPT, receipt, json.dumps([first, second]), env=fake_llm.env)

    contents = last_json(proc)
    requests = fake_llm.requests_of("structured")
    assert len(requests) == 2, contents
    assert RECEIPT_TASK_PROMPT in FakeOpenAI.prompt_text(requests[0])
    assert TABLE_TASK_PROMPT in FakeOpenAI.prompt_text(requests[1])
    assert "RECEIPT ANSWER" in contents[0] and "TABLE ANSWER" in contents[1], contents


def test_ocr_cache_is_shared_across_concurrency_settings(e2e_dir, fake_llm, receipt):
    """``max_concurrency`` only says how many requests run at once, not what an image says: it is no longer part
    of the key, so a loader with another concurrency cap is answered from the cache."""
    fake_llm.script(structured=[fake.page("RECEIPT ANSWER 12.50"), fake.page("SECOND ANSWER")])
    settings = [{"ocr_config": {"max_concurrency": 4}}, {"ocr_config": {"max_concurrency": 8}}]

    proc = run_api(e2e_dir, OCR_CACHE_SCRIPT, receipt, json.dumps(settings), env=fake_llm.env)

    contents = last_json(proc)
    assert len(fake_llm.requests_of("structured")) == 1, contents
    assert "RECEIPT ANSWER" in contents[1], contents


# --------------------------------------------------------------------------------------------------------------
# 13. The document cache (cache_dir) key

DOC_CACHE_SCRIPT = (
    "import json, sys\n"
    "from doc2mark import OCRConfig, UnifiedDocumentLoader\n"
    "settings = json.loads(sys.argv[3])\n"
    "config = OCRConfig(**settings.pop('ocr_config', {}))\n"
    "loader = UnifiedDocumentLoader(ocr_provider='openai', cache_dir=sys.argv[2], ocr_config=config, **settings)\n"
    "print(json.dumps(loader.load(sys.argv[1], ocr_images=True).content))\n"
)
FREE_FORM = {"structured": False}
DOC_CACHE_CASES = {
    "model": ({"model": "gpt-5.4-mini"}, {"model": "gpt-5.4-nano"}),
    "task": ({"task": "receipt"}, {"task": "table"}),
    "language": ({"ocr_config": {"language": "English"}}, {"ocr_config": {"language": "German"}}),
    "detail": ({"detail": "full"}, {"detail": "raw"}),
    "structured": ({"structured": True}, FREE_FORM),
    "prompt_template": (FREE_FORM, {**FREE_FORM, "prompt_template": "table_focused"}),
    "default_prompt": (FREE_FORM, {**FREE_FORM, "default_prompt": CUSTOM_PROMPT}),
}


def _answers(fake_llm, runs):
    """Script one answer per run, FIRST then SECOND, on the queue of the request kind each run sends."""
    structured, free_form = [], []
    for number, settings in enumerate(runs):
        queue = free_form if settings.get("structured") is False else structured
        queue.append(("FIRST", "SECOND")[number])
    fake_llm.script(structured=[fake.page(f"{word} ANSWER 12.50") for word in structured] or [fake.page("unused")],
                    free_form=[fake.text(f"{word} ANSWER 12.50") for word in free_form] or [fake.text("unused")])


@pytest.mark.parametrize("setting", list(DOC_CACHE_CASES))
def test_cache_dir_does_not_replay_ocr_text_after_an_ocr_setting_changed(e2e_dir, fake_llm, receipt, setting):
    """Issue 13: the ``cache_dir`` key named the OCR provider's class only, so after a change of model, task,
    language, detail, structured mode or prompt the next run returned the old OCR text from disk. Each is part
    of the key: the second run converts the file again."""
    first, second = DOC_CACHE_CASES[setting]
    _answers(fake_llm, [first, second])
    cache_dir = e2e_dir / "document-cache"

    one = last_json(run_api(e2e_dir, DOC_CACHE_SCRIPT, receipt, cache_dir, json.dumps(first), env=fake_llm.env))
    two = last_json(run_api(e2e_dir, DOC_CACHE_SCRIPT, receipt, cache_dir, json.dumps(second), env=fake_llm.env))

    assert "FIRST ANSWER" in one, one
    assert len(fake_llm.requests) == 2, two
    assert "SECOND ANSWER" in two and "FIRST ANSWER" not in two, two


def test_cache_dir_replays_a_document_converted_with_the_same_settings(e2e_dir, fake_llm, receipt):
    """The document cache still works: the same settings in a new process read the stored document."""
    _answers(fake_llm, [{}, {}])
    cache_dir = e2e_dir / "document-cache"
    settings = json.dumps({"task": "receipt", "model": "gpt-5.4-mini"})

    one = last_json(run_api(e2e_dir, DOC_CACHE_SCRIPT, receipt, cache_dir, settings, env=fake_llm.env))
    two = last_json(run_api(e2e_dir, DOC_CACHE_SCRIPT, receipt, cache_dir, settings, env=fake_llm.env))

    assert len(fake_llm.requests) == 1 and one == two and "FIRST ANSWER" in two, two


# --------------------------------------------------------------------------------------------------------------
# 15. non_content_judge values outside [0, 1]

JUDGE_SCRIPT = (
    "import json, sys\n"
    "from doc2mark import MemoryOCRCache, UnifiedDocumentLoader\n"
    "verdict = float(sys.argv[2])\n"
    "class Judge:\n"
    "    def non_content_judge(self, answer):\n"
    "        return verdict\n"
    "loader = UnifiedDocumentLoader(ocr_provider='openai', judge=Judge(), ocr_cache=MemoryOCRCache())\n"
    "runs = [loader.load(sys.argv[1], ocr_images=True) for _ in range(2)]\n"
    "print(json.dumps({'contents': [run.content for run in runs],\n"
    "                  'issues': [run.metadata.extra.get('ocr_issues') for run in runs]}))\n"
)


@pytest.mark.parametrize("verdict", ["1.5", "7", "-1", "nan"])
def test_a_non_content_verdict_outside_0_to_1_is_no_verdict(e2e_dir, fake_llm, receipt, verdict):
    """Issue 15: ``non_content_judge`` values were not range-checked: 1.5 or 7 dropped a real answer as "no
    content", and -1 or NaN counted as a verdict (so the answer was cached as screened). Like the other two hooks,
    a value outside [0, 1] (or NaN) is no verdict: the answer is kept, and, as with ``None``, it is not cached,
    so the next run asks again."""
    answer = "Totals by region: see chart 4471"
    fake_llm.script(structured=[fake.page(answer)], free_form=[fake.text("unused")])

    output = last_json(run_api(e2e_dir, JUDGE_SCRIPT, receipt, verdict, env={**fake_llm.env, "DOC2MARK_JUDGE": None}))

    assert all(answer in content for content in output["contents"]), output
    assert not any((issues or {}).get("refused") for issues in output["issues"]), output
    assert len(fake_llm.requests_of("structured")) == 2 and fake_llm.requests_of("free_form") == [], output


# --------------------------------------------------------------------------------------------------------------
# 16. Tokens of the free-form recovery call

@pytest.mark.parametrize("recovery", ["answered", "refused"])
def test_recovery_call_tokens_are_counted(run_cli, fake_llm, e2e_dir, recovery):
    """Issue 16: an empty structured answer is asked again in free form, and that second call's tokens were not
    added to ``token_usage``. The fake server bills 100 input + 20 output tokens per request: two requests."""
    scan = build.scan_pdf(e2e_dir / "scan.pdf", ["BOARD MINUTES 2026"])
    reply = fake.text("Board minutes 2026") if recovery == "answered" else fake.refusal(REFUSAL)
    fake_llm.script(structured=[fake.page("")], free_form=[reply])

    result = run_cli(scan, "--ocr", "openai", "--ocr-images", fmt="both", env=fake_llm.env)

    assert result.exit_code == 0, result.describe()
    assert len(fake_llm.requests) == 2, result.describe()
    usage = result.json["metadata"]["extra"].get("token_usage")
    assert usage == {"input_tokens": 200, "output_tokens": 40, "total_tokens": 240}, result.json["metadata"]


# --------------------------------------------------------------------------------------------------------------
# 17. OCRConfig.response_model with OpenAI

RESPONSE_MODEL_SCRIPT = (
    "import json, sys\n"
    "from pydantic import BaseModel\n"
    "from doc2mark import OCR, OCRConfig, UnifiedDocumentLoader\n"
    "class Invoice(BaseModel):\n"
    "    merchant: str\n"
    "    total: str\n"
    "if sys.argv[2] == 'facade':\n"
    "    result = OCR('openai', response_model=Invoice).read_one(open(sys.argv[1], 'rb').read())\n"
    "    doc = result.document\n"
    "    print(json.dumps({'type': type(doc).__name__, 'fields': doc.model_dump(), 'text': result.text}))\n"
    "else:\n"
    "    loader = UnifiedDocumentLoader(ocr_provider='openai', ocr_config=OCRConfig(response_model=Invoice))\n"
    "    print(json.dumps({'text': loader.load(sys.argv[1], ocr_images=True).content}))\n"
)


@pytest.mark.parametrize("how", ["facade", "loader"])
def test_openai_parses_into_a_custom_response_model(e2e_dir, fake_llm, receipt, how):
    """Issue 17: with ``OCRConfig(response_model=Invoice)`` the OpenAI provider failed every image with ``OCRError:
    'Invoice' object has no attribute 'interpretation'``. As documented (and as Vertex AI did), the answer is
    parsed into your model: ``result.document`` is the ``Invoice`` and ``result.text`` its fields as JSON."""
    fake_llm.script(structured=[fake.text(json.dumps({"merchant": "ACME STORE", "total": "12.50"}))])

    output = last_json(run_api(e2e_dir, RESPONSE_MODEL_SCRIPT, receipt, how, env=fake_llm.env))

    [body] = fake_llm.requests_of("structured")
    assert body["response_format"]["json_schema"]["name"] == "Invoice", body["response_format"]
    assert "ACME STORE" in output["text"] and "12.50" in output["text"], output
    if how == "facade":
        assert output["type"] == "Invoice" and output["fields"] == {"merchant": "ACME STORE", "total": "12.50"}, output


# --------------------------------------------------------------------------------------------------------------
# 18. The DeprecationWarning of inert OCRConfig fields

DEPRECATED = {
    "facade openai": "from doc2mark import OCR\nOCR('openai', detect_tables=False)\n",
    "facade vertex_ai": "from doc2mark import OCR\nOCR('vertex_ai', extra={'seed': 1})\n",
    "loader": ("from doc2mark import OCRConfig, UnifiedDocumentLoader\n"
               "UnifiedDocumentLoader(ocr_provider='openai', ocr_config=OCRConfig(enhance_image=False))\n"),
}


@pytest.mark.parametrize("case", list(DEPRECATED))
def test_deprecation_warning_points_at_the_callers_line(e2e_dir, case):
    """Issue 18: the DeprecationWarning for inert ``OCRConfig`` fields named a doc2mark line (``stacklevel=2``),
    so Python's default filters, which show DeprecationWarnings only for ``__main__``, hid it. It names the line
    of your code that created the provider, and a script shows it without ``-W``."""
    proc = run_api(e2e_dir, DEPRECATED[case], env={"PYTHONWARNINGS": None, "OPENAI_API_KEY": None})

    lines = [line for line in proc.stderr.splitlines() if "DeprecationWarning" in line]
    assert len(lines) == 1 and lines[0].startswith("<string>:2: DeprecationWarning:"), proc.stderr
