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
import types

import pytest

from doc2mark import UnifiedDocumentLoader
from doc2mark.core.strategy import LEGIBILITY_JUDGE_THRESHOLD
from doc2mark.judge import JUDGE_ENV, TypeSafeJudge, judge_hooks, resolve_judge
from doc2mark.judge import questions as Q
from doc2mark.judge.cache import DiskVerdictCache, Verdict, verdict_key
from doc2mark.judge.typesafe import BREAKER_FAILURES, _rescale
from doc2mark.ocr.base import BaseOCR, OCRConfig, OCRResult
from doc2mark.ocr.cache import CachedOCR, MemoryOCRCache, _judge_identity
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


def screen_non_content(answer, judge):
    """doc2mark.ocr.refusal.screen_non_content (imported here, so each test that needs it fails on its own
    against code without it)."""
    from doc2mark.ocr.refusal import screen_non_content as screen
    return screen(answer, judge)


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


def test_a_long_page_is_sampled_at_its_beginning_middle_and_end():
    """m3: only the first 1,500 characters were sent, so a legible opening could hide a garbled body."""
    opening = [f"Legible line {n} of the opening section." for n in range(15)]
    body = [f"Lqyrlfh wrwdo HXU {2340 + n} gxh rq 14 Pdufk 2026" for n in range(80)]
    page = "\n".join(opening + body + ["Closing line of the page."])
    sample = Q.legibility_state(page)["page_text"]
    pieces = sample.split(Q.SAMPLE_GAP)
    assert len(pieces) == 3 and len(sample) <= Q.MAX_PAGE_CHARS + 2 * len(Q.SAMPLE_GAP)
    assert pieces[0].startswith(opening[0]) and "Lqyrlfh" in pieces[1] and pieces[2].endswith("Closing line of the page.")
    assert all(line in page.split("\n") for piece in pieces for line in piece.split("\n"))  # cut at line breaks
    assert Q.legibility_state("A short page.") == {"page_text": "A short page."}


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
    assert key != verdict_key(Q.MODEL, "legibility-v0", state)
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


@pytest.mark.parametrize("anchors", [
    ((0.6, 0.7),), ((0.8, 0.5),), ((0.5, 0.5),), ((0.3, 0.5),), ((0.8, 0.7),), ((0.9, 0.3), (0.95, 0.5)),
])
def test_rescaling_moves_each_pipeline_threshold_onto_its_calibrated_one(anchors):
    values = [n / 100 for n in range(101)]
    mapped = [_rescale(v, anchors) for v in values]
    assert mapped == sorted(mapped) and mapped[0] == 0 and mapped[-1] == pytest.approx(1)
    for raw, target in anchors:
        assert _rescale(raw, anchors) == target
        for value, out in zip(values, mapped):
            assert (value >= raw) == (out >= target), (value, raw, target, out)


def test_a_calibrated_threshold_decides_like_the_pipeline_threshold():
    judge = judge_with(FakeClient(answer=0.65), thresholds={"legibility": 0.6})
    assert judge.legibility_judge("page") >= LEGIBILITY_JUDGE_THRESHOLD  # 0.65 >= 0.6: legible
    judge = judge_with(FakeClient(answer=0.55), thresholds={"legibility": 0.6})
    assert judge.legibility_judge("page") < LEGIBILITY_JUDGE_THRESHOLD
    with pytest.raises(ValueError):
        judge_with(FakeClient(), thresholds={"legibility": 1.5})
    with pytest.raises(ValueError):
        judge_with(FakeClient(), suspect_thresholds={"non_content": 0.0})


SHIPPED = dict(Q.RAW_THRESHOLDS)


@pytest.mark.parametrize("hook, raw, acts", [
    ("legibility", SHIPPED["legibility"] - 0.01, True), ("legibility", SHIPPED["legibility"], False),
    ("legibility", 0.99, False), ("boilerplate", SHIPPED["boilerplate"] - 0.01, False),
    ("boilerplate", SHIPPED["boilerplate"], True), ("non_content", 0.94, False), ("non_content", 0.95, True),
])
def test_the_calibrated_thresholds_decide(hook, raw, acts):
    """The shipped thresholds (questions.RAW_THRESHOLDS) as the pipeline applies them: legibility acts (page
    illegible) below its threshold, the others at or above theirs; non_content acts only from 0.95 (M2)."""
    judge = TypeSafeJudge(client=FakeClient(answer=raw), cache_dir=False)
    args = {"legibility": ("page text",), "non_content": ("an answer",), "boilerplate": ("a line", CONTEXT)}[hook]
    value = getattr(judge, f"{hook}_judge")(*args)
    threshold = PIPELINE_THRESHOLDS[hook]
    assert (value < threshold if hook == "legibility" else value >= threshold) is acts
    assert SHIPPED["non_content"] == 0.95 and Q.RAW_SUSPECT_THRESHOLDS == {"non_content": 0.9}


@pytest.mark.parametrize("raw, reason, suspected", [(0.89, None, False), (0.9, None, True), (0.94, None, True),
                                                    (0.95, "judge", False), (0.99, "judge", False)])
def test_non_content_acts_only_from_095_and_flags_the_band_below(raw, reason, suspected):
    """M2: 0.9 sat inside the overlap of refusals and real answers. From 0.95 an answer is no content; from 0.90
    to 0.95 it is kept and flagged ``non_content_suspected``."""
    judge = TypeSafeJudge(client=FakeClient(answer=raw), cache_dir=False)
    screen = screen_non_content("Unable to process the image.", judge.non_content_judge)
    assert (screen.reason, screen.suspected, screen.unanswered) == (reason, suspected, False)
    text, flags = _StubOCR(config=OCRConfig(non_content_judge=judge.non_content_judge))._free_form_answer(
        "Unable to process the image.")
    assert (text == "") is (reason is not None)
    assert flags.get("non_content_suspected", False) is suspected


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
    expected = "typesafe:jev-1.13.0:non-content-v1:t0.95:s0.9"
    assert first.non_content_judge.version == second.non_content_judge.version == expected
    assert _judge_identity(first.non_content_judge) == _judge_identity(second.non_content_judge) == {
        "name": "doc2mark.judge.typesafe._Hook", "version": expected}
    assert judge_with(FakeClient(), thresholds={"non_content": 0.6}).non_content_judge.version != expected
    assert first.legibility_judge.cache_key.startswith("typesafe:jev-1.13.0:legibility-v2:")
    assert first.boilerplate_judge.cache_key.startswith("typesafe:jev-1.13.0:boilerplate-v2:")


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
    assert judged.content.count(brand) == 1  # B1: the first copy always stays
    assert all(title in judged.content for title, _ in builders_judge.DECK_SLIDES)
    stats = judged.metadata.extra["judge"]
    assert stats["name"] == "typesafe" and stats["model"] == "jev-1.13.0"
    assert stats["asked"] == len(client.calls) >= 1 and stats["fresh"] == stats["asked"] and stats["failed"] == 0

    with caplog.at_level(logging.WARNING):
        failing = UnifiedDocumentLoader(ocr_provider=None, judge=judge_with(FakeClient(error=APIError(503))))
        result = failing.load(pdf)
    assert result.content == plain.content
    assert caplog.text.count("judge question(s) got no answer") == 1
    assert result.metadata.extra["judge"]["failed"] >= 1


def test_document_cache_key_names_the_judges(tmp_path):
    judge = judge_with(FakeClient())
    loader = UnifiedDocumentLoader(ocr_provider=_StubOCR(config=OCRConfig()), judge=judge, cache_dir=str(tmp_path))
    assert loader._judge_identity(loader.boilerplate_judge) == judge.boilerplate_judge.cache_key
    assert loader._judge_identity() == judge.legibility_judge.cache_key


# --- review round 1 ----------------------------------------------------------------------------------------


def test_the_judge_never_removes_the_last_copy_of_a_letterhead(tmp_path):
    """B1: a chrome verdict on the first copy of a running header (the rule had already removed the other
    copies) deleted the sender's letterhead. The first copy always stays; such a line is not even asked about."""
    pdf = builders_judge.letter_pdf(tmp_path / "letter.pdf")
    plain = UnifiedDocumentLoader(ocr_provider=None).load(pdf)
    asked = []

    def always_chrome(text, context):
        asked.append((text, context["reason"]))
        return 1.0

    judged = UnifiedDocumentLoader(ocr_provider=None, boilerplate_judge=always_chrome).load(pdf)
    for line in builders_judge.LETTERHEAD:
        assert plain.content.count(line) == 1 and judged.content.count(line) == 1, (line, judged.content)
    assert judged.content == plain.content
    assert all(reason != "first_occurrence" for _, reason in asked), asked


def test_a_chrome_verdict_keeps_the_first_copy_as_plain_text(tmp_path):
    """B1: the deck's brand line (attached to each slide's header row) is thinned to its first copy, which
    stays as a plain line; every slide title and numbered label stays."""
    pdf = builders_judge.deck_pdf(tmp_path / "deck.pdf")
    judged = UnifiedDocumentLoader(ocr_provider=None, boilerplate_judge=lambda text, context: 1.0).load(pdf)
    brand = builders_judge.BRAND_LINE
    assert judged.content.count(brand) == 1
    assert brand in judged.content.splitlines()  # a plain line, not a heading
    for number, (title, _) in enumerate(builders_judge.DECK_SLIDES, 1):
        assert title in judged.content and f"{number:02d} / {title}" in judged.content


def test_a_line_already_on_the_cover_keeps_that_copy_only(tmp_path):
    """B1, the real deck's shape: its cover shows the brand line in the page body, then every slide repeats it
    in the header row. The cover's copy is the one that stays: the brand line goes from once per page to once."""
    pdf = builders_judge.deck_pdf(tmp_path / "deck.pdf", cover=True)
    brand = builders_judge.BRAND_LINE
    plain = UnifiedDocumentLoader(ocr_provider=None).load(pdf)
    assert plain.content.count(brand) == len(builders_judge.DECK_SLIDES) + 1
    judged = UnifiedDocumentLoader(ocr_provider=None, boilerplate_judge=lambda text, context: 1.0).load(pdf)
    assert judged.content.count(brand) == 1
    assert judged.content.index(brand) < judged.content.index("01 / ")


def _deck_verdicts(state):
    return 0.95 if state["line"] in (builders_judge.BRAND_LINE, builders_judge.LOGO_TEXT) else 0.05


def test_a_run_without_a_key_is_not_replayed_for_a_run_with_one(tmp_path, monkeypatch):
    """M1: the document cache keyed the judge's identity, not whether it could answer: a run without a key
    was replayed for a later run with the key, and the judge never ran. A judge that cannot answer is keyed
    as no judge, which is what its output is."""
    pdf, cache_dir = builders_judge.deck_pdf(tmp_path / "deck.pdf"), str(tmp_path / "doc-cache")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    first = UnifiedDocumentLoader(ocr_provider=None, judge=TypeSafeJudge(cache_dir=False), cache_dir=cache_dir).load(pdf)
    assert first.content.count(builders_judge.BRAND_LINE) == len(builders_judge.DECK_SLIDES)

    judge = TypeSafeJudge(client=FakeClient(answer=_deck_verdicts), cache_dir=False)
    second = UnifiedDocumentLoader(ocr_provider=None, judge=judge, cache_dir=cache_dir).load(pdf)
    assert second.content.count(builders_judge.BRAND_LINE) == 1
    assert second.metadata.extra["judge"]["fresh"] >= 1


def test_a_run_whose_judge_failed_is_not_cached(tmp_path):
    """M1: a run in which the judge failed on some question was cached and replayed for later runs."""
    pdf, cache_dir = builders_judge.deck_pdf(tmp_path / "deck.pdf"), str(tmp_path / "doc-cache")
    failing = TypeSafeJudge(client=FakeClient(error=APIError(503)), cache_dir=False)
    first = UnifiedDocumentLoader(ocr_provider=None, judge=failing, cache_dir=cache_dir).load(pdf)
    assert first.metadata.extra["judge"]["failed"] >= 1

    working = TypeSafeJudge(client=FakeClient(answer=_deck_verdicts), cache_dir=False)
    second = UnifiedDocumentLoader(ocr_provider=None, judge=working, cache_dir=cache_dir).load(pdf)
    assert second.content.count(builders_judge.BRAND_LINE) == 1
    assert second.metadata.extra["judge"]["fresh"] >= 1


class _FreeFormOCR(BaseOCR):
    """A provider whose every answer is ``answer``, screened the way the LLM providers screen free-form answers."""

    def __init__(self, answer, **kwargs):
        super().__init__(**kwargs)
        self.answer, self.calls = answer, 0

    def batch_process_images(self, images, **kwargs):
        self.calls += len(images)
        return [OCRResult(text=text, metadata=flags)
                for text, flags in (self._free_form_answer(self.answer) for _ in images)]


def test_ocr_answers_the_judge_could_not_screen_are_not_cached():
    """M1 (OCR cache): an answer screened while the judge failed was cached under the judge's key and never
    screened again."""
    judge = TypeSafeJudge(client=FakeClient(error=APIError(503)), cache_dir=False)
    provider = _FreeFormOCR("Unable to process the image.", config=OCRConfig(non_content_judge=judge.non_content_judge))
    cached = CachedOCR(provider, MemoryOCRCache())
    first = cached.batch_process_images([b"image-1"])
    assert first[0].text and first[0].metadata.get("non_content_unjudged") is True
    cached.batch_process_images([b"image-1"])
    assert provider.calls == 2


def test_ocr_results_of_a_judge_that_cannot_answer_are_keyed_as_without_one(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    unavailable = TypeSafeJudge(cache_dir=False)
    assert unavailable.non_content_judge.available is False
    assert _judge_identity(unavailable.non_content_judge) is None
    screen = screen_non_content("Unable to process the image.", unavailable.non_content_judge)
    assert (screen.reason, screen.suspected, screen.unanswered) == (None, False, False)
    provider = _FreeFormOCR("A short caption.", config=OCRConfig(non_content_judge=unavailable.non_content_judge))
    cached = CachedOCR(provider, MemoryOCRCache())
    cached.batch_process_images([b"image-1"])
    cached.batch_process_images([b"image-1"])
    assert provider.calls == 1  # cached, as without a judge


def test_a_judge_that_stops_answering_during_a_batch_leaves_the_batch_uncached():
    judge = TypeSafeJudge(client=FakeClient(error=APIError(401)), cache_dir=False)
    provider = _FreeFormOCR("Unable to process the image.", config=OCRConfig(non_content_judge=judge.non_content_judge))
    cached = CachedOCR(provider, MemoryOCRCache())
    cached.batch_process_images([b"image-1", b"image-2"])
    assert judge.unavailable
    cached.batch_process_images([b"image-1", b"image-2"])
    assert provider.calls == 4


def test_a_failed_question_counts_once_and_keeps_its_cause():
    """m2: a question whose prefetch failed was asked again by its hook and counted twice, and the cause was
    replaced by "paused after repeated failures"."""
    client = FakeClient(error=TimeoutError("read timeout"))
    judge = judge_with(client)
    pages = [f"page {n}: Delivery of {n} units to the Rotterdam depot" for n in range(3)]
    judge.legibility_judge.prefetch(pages)
    assert all(judge.legibility_judge(page) is None for page in pages)
    usage = judge.usage()
    assert (usage["asked"], usage["failed"], len(client.calls)) == (3, 3, 3)
    assert usage["last_error"] == "TimeoutError"
    assert judge.legibility_judge("one more page, while paused") is None
    assert judge.usage()["failed"] == 4 and judge.usage()["last_error"] == "TimeoutError"


@pytest.fixture
def fake_sdk(monkeypatch):
    """A stand-in ``typesafe_sdk`` module that records how its client is built (the list it returns); the
    real SDK logger's level and filters are restored afterwards."""
    created = []
    sdk_logger = logging.getLogger("typesafe_sdk")
    previous, filters = sdk_logger.level, list(sdk_logger.filters)
    _fake_sdk(monkeypatch, created)
    yield created
    sdk_logger.setLevel(previous)
    sdk_logger.filters[:] = filters


def _fake_sdk(monkeypatch, created):
    module = types.ModuleType("typesafe_sdk")

    class RetryPolicy:
        def __init__(self, **options):
            self.__dict__.update(options)

    class TypeSafeClient:
        def __init__(self, **options):
            created.append(options)

        def system_one(self, state, questions, model=None):
            (name,) = questions
            return FakeResponse(name, 0.97)

    module.RetryPolicy, module.TypeSafeClient = RetryPolicy, TypeSafeClient
    monkeypatch.setitem(sys.modules, "typesafe_sdk", module)
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-not-a-real-key")


def test_a_question_that_cannot_be_answered_costs_at_most_about_four_seconds(fake_sdk):
    """m1: the SDK's default retries let a hanging endpoint cost 10-21 s per document."""
    judge = TypeSafeJudge(cache_dir=False)
    assert judge.legibility_judge("A legible page of text.") is not None
    (options,) = fake_sdk
    assert options["timeout"] <= 2.0 and options["retry"].max_retries <= 1 and options["retry"].timeout <= 4.0


@pytest.fixture
def printed(monkeypatch):
    """What the CLI's log handler prints: a handler on the root logger, whose level each test sets as
    ``doc2mark`` (WARNING) or ``doc2mark -v`` (DEBUG) does. Yields ``(root logger, SDK records printed)``."""
    records = []

    class Printed(logging.Handler):
        def emit(self, record):
            if record.name == "typesafe_sdk":
                records.append((record.levelno, record.getMessage()))

    root, handler = logging.getLogger(), Printed()
    previous = root.level
    root.addHandler(handler)
    monkeypatch.delenv("TYPESAFE_LOG_LEVEL", raising=False)
    logging.getLogger("typesafe_sdk").setLevel(logging.NOTSET)
    yield root, records
    root.removeHandler(handler)
    root.setLevel(previous)


def _sdk_logs_one_request():
    """What the SDK logs for one request: the wire (headers and body: page text) at DEBUG, the outcome at INFO."""
    sdk_logger = logging.getLogger("typesafe_sdk")
    sdk_logger.debug("POST https://api.typesafe.ai/v1/system -> headers={} body=b'Invoice total EUR 2340'")
    sdk_logger.info("POST https://api.typesafe.ai/v1/system <- 200 in 180ms (request r-1)")


def test_a_default_run_prints_nothing_from_the_sdk(fake_sdk, printed):
    """Follow-up of m4: capping the SDK logger at INFO set its level explicitly, so its INFO record of every
    request and retry passed the WARNING-level CLI handler: a default ``--judge typesafe`` run printed one line
    per question."""
    root, records = printed
    root.setLevel(logging.WARNING)
    TypeSafeJudge(cache_dir=False).legibility_judge("A legible page of text.")
    _sdk_logs_one_request()
    assert records == []


def test_verbose_runs_print_the_request_outcome_but_never_a_body(fake_sdk, printed):
    """m4: ``doc2mark -v`` sets the root logger to DEBUG; the SDK's wire log holds document text."""
    root, records = printed
    root.setLevel(logging.DEBUG)
    TypeSafeJudge(cache_dir=False).legibility_judge("A legible page of text.")
    _sdk_logs_one_request()
    assert records == [(logging.INFO, "POST https://api.typesafe.ai/v1/system <- 200 in 180ms (request r-1)")]


def test_the_wire_log_shows_when_the_sdk_logger_is_set_to_debug(fake_sdk, printed, monkeypatch):
    """Whoever sets the SDK's own level (``TYPESAFE_LOG_LEVEL=debug``, which the SDK applies at import, or
    ``logging.getLogger("typesafe_sdk").setLevel``) gets what they asked for."""
    root, records = printed
    root.setLevel(logging.WARNING)
    monkeypatch.setenv("TYPESAFE_LOG_LEVEL", "debug")
    logging.getLogger("typesafe_sdk").setLevel(logging.DEBUG)
    TypeSafeJudge(cache_dir=False).legibility_judge("A legible page of text.")
    _sdk_logs_one_request()
    assert [level for level, _ in records] == [logging.DEBUG, logging.INFO]
