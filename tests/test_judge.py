"""Unit tests for the optional judge add-on (doc2mark.judge): no network, a stand-in TypeSafe client.

The E2E suite (tests/e2e/test_judge.py) drives the CLI with and without the real service; these tests pin
the parts it cannot reach: the fallback on every kind of failure, the verdict cache, the question state,
bounded concurrency, and the loader wiring.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time

import pytest

from doc2mark import UnifiedDocumentLoader
from doc2mark.core.strategy import LEGIBILITY_JUDGE_THRESHOLD
from doc2mark.judge import JUDGE_ENV, TypeSafeJudge, judge_hooks, resolve_judge
from doc2mark.judge import questions as Q
from doc2mark.judge.cache import DiskVerdictCache, Verdict, verdict_key
from doc2mark.judge.typesafe import BREAKER_FAILURES, _rescale
from doc2mark.ocr.base import BaseOCR, OCRConfig
from doc2mark.ocr.cache import _judge_identity
from doc2mark.ocr.refusal import JUDGE_THRESHOLD, prefetch_non_content
from tests.e2e import builders_judge


class _Usage:
    def __init__(self, tokens):
        self.input_tokens = tokens


class _Answer:
    def __init__(self, value):
        self.noul = value


class FakeResponse:
    def __init__(self, name, value, model="jev-1.13.0", tokens=321):
        self.nouls = {name: _Answer(value)}
        self.model = model
        self.usage = _Usage(tokens)


class FakeClient:
    """Stands in for typesafe_sdk.TypeSafeClient: answers every question with ``answer`` (a value or a
    function of the state), or raises ``error``."""

    def __init__(self, answer=0.9, error=None, delay=0.0):
        self.answer, self.error, self.delay = answer, error, delay
        self.calls = []
        self._lock = threading.Lock()
        self.in_flight = self.max_in_flight = 0

    def system_one(self, state, questions, model=None):
        with self._lock:
            self.calls.append((state, questions, model))
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.error is not None:
                raise self.error
            (name,) = questions
            value = self.answer(state) if callable(self.answer) else self.answer
            return FakeResponse(name, value)
        finally:
            with self._lock:
                self.in_flight -= 1


class APIError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status} with a body that must not be logged: page text")
        self.status = status


# The pipeline's own thresholds: with them a hook returns Jev's probability unchanged.
PIPELINE_THRESHOLDS = {"legibility": LEGIBILITY_JUDGE_THRESHOLD, "boilerplate": 0.5, "non_content": JUDGE_THRESHOLD}


def judge_with(client, **options):
    options.setdefault("cache_dir", False)
    options.setdefault("thresholds", PIPELINE_THRESHOLDS)
    return TypeSafeJudge(client=client, **options)


CONTEXT = {"zone": "header", "pages": [2, 3, 4, 5], "repeated_on": [2, 3, 4, 5], "page_count": 5,
           "font_size": 8.6, "body_font_size": 20.0, "reason": "attached_to_content"}


# --- optional: nothing is imported or asked unless enabled ------------------------------------------------


def test_the_sdk_is_not_imported_unless_a_judge_is_enabled(tmp_path):
    script = (
        "import sys\n"
        "from doc2mark import UnifiedDocumentLoader\n"
        "UnifiedDocumentLoader(ocr_provider=None)\n"
        "import doc2mark.judge\n"
        "print('typesafe_sdk' in sys.modules)\n"
    )
    env = {key: value for key, value in os.environ.items() if key != JUDGE_ENV}
    proc = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "False"


def test_resolve_judge(monkeypatch, caplog):
    monkeypatch.delenv(JUDGE_ENV, raising=False)
    assert resolve_judge(None) is None
    assert resolve_judge("none") is None
    marker = object()
    assert resolve_judge(marker) is marker
    with pytest.raises(ValueError):
        resolve_judge("nonsense")
    monkeypatch.setenv(JUDGE_ENV, "nonsense")
    with caplog.at_level(logging.WARNING):
        assert resolve_judge(None) is None
    assert JUDGE_ENV in caplog.text
    monkeypatch.setenv(JUDGE_ENV, "typesafe")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert isinstance(resolve_judge(None), TypeSafeJudge)
    assert resolve_judge("none") is None  # an explicit "none" wins over the environment


def test_missing_extra_leaves_every_decision_to_the_rules(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)  # import typesafe_sdk -> ImportError
    with caplog.at_level(logging.WARNING):
        judge = TypeSafeJudge(cache_dir=False)
    assert "typesafe-sdk is not installed" in judge.unavailable
    assert judge.legibility_judge("Invoice total EUR 2340") is None
    assert judge.boilerplate_judge("by Contoso Labs", CONTEXT) is None
    assert judge.non_content_judge("I'm sorry.") is None
    assert caplog.text.count("TypeSafe judge unavailable") == 1


def test_missing_key_leaves_every_decision_to_the_rules(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    judge = TypeSafeJudge(cache_dir=False)
    assert judge.unavailable in ("TYPESAFE_API_KEY is not set",
                                 "typesafe-sdk is not installed (pip install 'doc2mark[typesafe]')")
    assert judge.legibility_judge("Invoice total EUR 2340") is None
    assert judge.usage()["asked"] == 0


# --- questions, state, answers ---------------------------------------------------------------------------


def test_each_hook_asks_one_versioned_noul_of_the_pinned_model():
    client = FakeClient(answer=0.83)
    judge = judge_with(client)

    assert judge.legibility_judge("INVOICE\nInvoice total EUR 2340") == pytest.approx(0.83)
    assert judge.boilerplate_judge("by Contoso Labs", dict(CONTEXT, reason="numbered_label")) == pytest.approx(0.83)
    assert judge.non_content_judge("  Unable to process the image.  ") == pytest.approx(0.83)

    states = [state for state, _, _ in client.calls]
    assert states[0] == {"page_text": "INVOICE\nInvoice total EUR 2340"}
    assert states[1] == {"line": "by Contoso Labs", "where": "at the top of the page, in the same place on 4 of the 5 pages",
                         "font": "smaller than the body text",
                         "note": "The number in this line goes up by one from each page to the next, like a page number."}
    assert states[2] == {"ocr_answer": "Unable to process the image."}
    for (_, questions, model), hook in zip(client.calls, ("legibility", "boilerplate", "non_content")):
        assert model == Q.MODEL == "jev-1.13.0"
        assert questions == {hook: Q.QUESTIONS[hook].payload()}
        assert questions[hook]["type"] == "noul" and set(questions[hook]["criteria"]) == {"true", "false"}


def test_page_text_is_sampled():
    page = "\n".join(f"line {n} of a long page with plenty of words in it" for n in range(200))
    state = Q.legibility_state(page)
    assert len(state["page_text"]) <= Q.MAX_PAGE_CHARS and page.startswith(state["page_text"])
    assert state["page_text"].endswith("in it")  # cut at a line break


@pytest.mark.parametrize("answer", [None, 1.5, -0.1, float("nan"), True, "0.9"])
def test_an_invalid_answer_is_no_answer(answer):
    judge = judge_with(FakeClient(answer=answer))
    assert judge.legibility_judge("Invoice total EUR 2340") is None
    assert judge.usage()["failed"] == 1


def test_a_hook_never_raises_and_does_not_log_the_request_body(caplog):
    judge = judge_with(FakeClient(error=APIError(500)))
    with caplog.at_level(logging.DEBUG, logger="doc2mark.judge"):
        assert judge.legibility_judge("Invoice total EUR 2340") is None
    assert judge.usage()["failed"] == 1 and judge.usage()["last_error"] == "APIError (HTTP 500)"
    assert "page text" not in caplog.text


def test_a_rejected_key_turns_the_judge_off():
    client = FakeClient(error=APIError(401))
    judge = judge_with(client)
    assert judge.non_content_judge("Unable to process the image.") is None
    assert "rejected the API key" in judge.unavailable
    assert judge.non_content_judge("Another answer.") is None
    assert len(client.calls) == 1


def test_repeated_failures_pause_the_judge():
    client = FakeClient(error=TimeoutError("read timeout"))
    judge = judge_with(client)
    for n in range(BREAKER_FAILURES + 2):
        assert judge.legibility_judge(f"page {n} of the report") is None
    assert len(client.calls) == BREAKER_FAILURES


def test_a_rejected_request_does_not_pause_the_judge():
    client = FakeClient(error=APIError(422))
    judge = judge_with(client)
    for n in range(BREAKER_FAILURES + 1):
        judge.legibility_judge(f"page {n} of the report")
    assert len(client.calls) == BREAKER_FAILURES + 1


# --- cache ------------------------------------------------------------------------------------------------


def test_verdicts_are_cached_on_disk_and_replayed(tmp_path):
    client = FakeClient(answer=0.12)
    judge = judge_with(client, cache_dir=tmp_path)
    assert judge.legibility_judge("Lqyrlfh wrwdo HXU 2340") == pytest.approx(0.12)
    assert judge.legibility_judge("Lqyrlfh wrwdo HXU 2340") == pytest.approx(0.12)
    assert len(client.calls) == 1

    # a new process (new judge) replays the verdict without asking; a failing service does not matter
    replay = judge_with(FakeClient(error=ConnectionError("offline")), cache_dir=tmp_path)
    assert replay.legibility_judge("Lqyrlfh wrwdo HXU 2340") == pytest.approx(0.12)
    assert replay.usage()["cached"] == 1 and replay.usage()["fresh"] == 0

    # another question version, model or state is another key
    state = Q.legibility_state("Lqyrlfh wrwdo HXU 2340")
    key = verdict_key(Q.MODEL, Q.LEGIBILITY.version, state)
    assert key != verdict_key("jev-1.14.0", Q.LEGIBILITY.version, state)
    assert key != verdict_key(Q.MODEL, "legibility-v2", state)
    stored = json.loads((tmp_path / key[:2] / f"{key}.json").read_text())
    assert stored["probability"] == 0.12 and stored["model"] == "jev-1.13.0" and stored["input_tokens"] == 321


def test_failures_are_not_cached(tmp_path):
    judge_with(FakeClient(error=ConnectionError("offline")), cache_dir=tmp_path).legibility_judge("some page text")
    client = FakeClient(answer=0.95)
    assert judge_with(client, cache_dir=tmp_path).legibility_judge("some page text") == pytest.approx(0.95)
    assert len(client.calls) == 1


def test_a_broken_cache_file_is_a_miss(tmp_path):
    cache = DiskVerdictCache(tmp_path)
    key = verdict_key(Q.MODEL, "v", {"x": 1})
    (tmp_path / key[:2]).mkdir()
    (tmp_path / key[:2] / f"{key}.json").write_text("{not json")
    assert cache.get(key) is None
    cache.set(key, Verdict(probability=0.4, model="jev-1.13.0"))
    assert DiskVerdictCache(tmp_path).get(key).probability == 0.4


# --- thresholds -----------------------------------------------------------------------------------------


def test_rescaling_moves_the_pipeline_threshold_onto_the_calibrated_one():
    for raw, target in ((0.6, 0.7), (0.8, 0.5), (0.5, 0.5), (0.3, 0.5)):
        values = [n / 100 for n in range(101)]
        mapped = [_rescale(v, raw, target) for v in values]
        assert mapped == sorted(mapped) and mapped[0] == 0 and mapped[-1] == pytest.approx(1)
        assert _rescale(raw, raw, target) == pytest.approx(target)
        for value, out in zip(values, mapped):
            assert (value >= raw) == (out >= target), (value, raw, target, out)


def test_a_calibrated_threshold_decides_like_the_pipeline_threshold():
    judge = judge_with(FakeClient(answer=0.65), thresholds={"legibility": 0.6})
    assert judge.legibility_judge("page") >= LEGIBILITY_JUDGE_THRESHOLD  # 0.65 >= 0.6: legible
    judge = judge_with(FakeClient(answer=0.55), thresholds={"legibility": 0.6})
    assert judge.legibility_judge("page") < LEGIBILITY_JUDGE_THRESHOLD
    with pytest.raises(ValueError):
        judge_with(FakeClient(), thresholds={"legibility": 1.5})


@pytest.mark.parametrize("hook, raw, acts", [
    ("legibility", 0.79, True), ("legibility", 0.8, False), ("legibility", 0.95, False),
    ("non_content", 0.89, False), ("non_content", 0.9, True), ("boilerplate", 0.69, False), ("boilerplate", 0.7, True),
])
def test_the_calibrated_thresholds_decide(hook, raw, acts):
    """The shipped thresholds (questions.RAW_THRESHOLDS, calibrated on the TRAIN split) as the pipeline applies
    them: legibility acts (page illegible) below its threshold, the others at or above theirs."""
    judge = TypeSafeJudge(client=FakeClient(answer=raw), cache_dir=False)
    args = {"legibility": ("page text",), "non_content": ("an answer",), "boilerplate": ("a line", CONTEXT)}[hook]
    value = getattr(judge, f"{hook}_judge")(*args)
    threshold = PIPELINE_THRESHOLDS[hook]
    assert (value < threshold if hook == "legibility" else value >= threshold) is acts
    assert Q.RAW_THRESHOLDS == {"legibility": 0.8, "boilerplate": 0.7, "non_content": 0.9}


def test_hooks_can_be_chosen(monkeypatch):
    judge = judge_with(FakeClient(), hooks="legibility,non-content")
    assert judge.legibility_judge is not None and judge.non_content_judge is not None
    assert judge.boilerplate_judge is None
    monkeypatch.setenv("DOC2MARK_JUDGE_HOOKS", "boilerplate")
    assert judge_with(FakeClient()).hooks == ("boilerplate",)
    with pytest.raises(ValueError):
        judge_with(FakeClient(), hooks=["headings"])


def test_hook_identity_is_stable_for_the_caches():
    first, second = (TypeSafeJudge(client=FakeClient(), cache_dir=False) for _ in range(2))
    expected = f"typesafe:jev-1.13.0:non-content-v1:t{Q.RAW_THRESHOLDS['non_content']:g}"
    assert first.non_content_judge.version == second.non_content_judge.version == expected
    assert _judge_identity(first.non_content_judge) == _judge_identity(second.non_content_judge) == {
        "name": "doc2mark.judge.typesafe._Hook", "version": expected}
    assert judge_with(FakeClient(), thresholds={"non_content": 0.6}).non_content_judge.version != expected
    assert first.legibility_judge.cache_key.startswith("typesafe:jev-1.13.0:legibility-v1:")


# --- concurrency --------------------------------------------------------------------------------------------


def test_prefetch_asks_each_question_once_with_bounded_concurrency():
    client = FakeClient(answer=0.97, delay=0.05)
    judge = judge_with(client, max_workers=3)
    pages = [f"page {n}: Delivery of {n} units to the Rotterdam depot" for n in range(12)]
    judge.legibility_judge.prefetch(pages + pages[:4])

    assert len(client.calls) == 12 and client.max_in_flight <= 3
    assert all(judge.legibility_judge(page) == pytest.approx(0.97) for page in pages)
    assert len(client.calls) == 12
    usage = judge.usage()
    assert usage["fresh"] == 12 and usage["cached"] == 12 and usage["latency_p50_ms"] >= 40


def test_prefetch_of_ocr_answers_skips_what_the_patterns_decide():
    seen = []

    class Judge:
        def __call__(self, answer):
            return None

        def prefetch(self, answers):
            seen.extend(answers)

    prefetch_non_content(["No text detected.", "  Unable to process the image.  ", "x" * 700, ""], Judge())
    assert seen == ["Unable to process the image."]


# --- loader wiring ------------------------------------------------------------------------------------------


class _StubOCR(BaseOCR):
    def batch_process_images(self, images, **kwargs):
        return []


class _HookObject:
    def __init__(self):
        self.legibility_judge = lambda text: 0.9
        self.boilerplate_judge = lambda text, context: 0.9
        self.non_content_judge = lambda answer: 0.1


def test_judge_object_wires_all_hooks():
    judge = _HookObject()
    config = OCRConfig()
    loader = UnifiedDocumentLoader(ocr_provider=_StubOCR(config=config), judge=judge)
    assert loader.legibility_judge is judge.legibility_judge
    assert loader.boilerplate_judge is judge.boilerplate_judge
    assert loader.ocr.config.non_content_judge is judge.non_content_judge
    assert config.non_content_judge is None  # the caller's config object is not mutated
    assert judge_hooks(judge)["boilerplate_judge"] is judge.boilerplate_judge


def test_explicit_hooks_win_over_the_judge_object():
    mine = lambda answer: 0.2  # noqa: E731
    explicit = lambda text: 0.3  # noqa: E731
    loader = UnifiedDocumentLoader(ocr_provider=_StubOCR(config=OCRConfig(non_content_judge=mine)),
                                   judge=_HookObject(), legibility_judge=explicit)
    assert loader.legibility_judge is explicit
    assert loader.ocr.config.non_content_judge is mine


def test_a_new_ocr_provider_gets_the_judge_too():
    judge = _HookObject()
    loader = UnifiedDocumentLoader(ocr_provider=None, judge=judge)
    loader.set_ocr_provider(_StubOCR(config=OCRConfig()))
    assert loader.ocr.config.non_content_judge is judge.non_content_judge


def test_judge_from_the_environment(monkeypatch):
    monkeypatch.setenv(JUDGE_ENV, "typesafe")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    loader = UnifiedDocumentLoader(ocr_provider=None)
    assert isinstance(loader.judge, TypeSafeJudge)
    assert loader.boilerplate_judge is loader.judge.boilerplate_judge
    assert UnifiedDocumentLoader(ocr_provider=None, judge="none").judge is None


def test_loader_uses_the_judge_on_page_chrome_and_reports_it(tmp_path, caplog):
    pdf = builders_judge.deck_pdf(tmp_path / "deck.pdf")
    brand = builders_judge.BRAND_LINE

    plain = UnifiedDocumentLoader(ocr_provider=None).load(pdf)
    assert plain.content.count(brand) == len(builders_judge.DECK_SLIDES)
    assert "judge" not in (plain.metadata.extra or {})

    client = FakeClient(answer=lambda state: 0.95 if state["line"] == brand else 0.05)
    judged = UnifiedDocumentLoader(ocr_provider=None, judge=judge_with(client)).load(pdf)
    assert judged.content.count(brand) == 0
    assert all(title in judged.content for title, _ in builders_judge.DECK_SLIDES)
    stats = judged.metadata.extra["judge"]
    assert stats["name"] == "typesafe" and stats["model"] == "jev-1.13.0"
    assert stats["asked"] == len(client.calls) >= 1 and stats["fresh"] == stats["asked"] and stats["failed"] == 0

    with caplog.at_level(logging.WARNING):
        failing = UnifiedDocumentLoader(ocr_provider=None, judge=judge_with(FakeClient(error=APIError(503))))
        result = failing.load(pdf)
    assert result.content == plain.content
    assert caplog.text.count("judge request(s) got no answer") == 1
    assert result.metadata.extra["judge"]["failed"] >= 1


def test_document_cache_key_names_the_judges(tmp_path):
    judge = judge_with(FakeClient())
    loader = UnifiedDocumentLoader(ocr_provider=_StubOCR(config=OCRConfig()), judge=judge, cache_dir=str(tmp_path))
    assert loader._judge_identity(loader.boilerplate_judge) == judge.boilerplate_judge.cache_key
    assert loader._judge_identity() == judge.legibility_judge.cache_key
