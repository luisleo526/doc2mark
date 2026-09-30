"""E2E tests for the optional judge (``--judge``, docs/judge.rst): the TypeSafe/Jev add-on and its fallback.

Without the add-on (the ``doc2mark[typesafe]`` extra is missing, as in CI's image, or ``TYPESAFE_API_KEY`` is
unset) ``--judge typesafe`` must change nothing: one warning, and the same Markdown as ``--judge none``.

With it, each ``requires_typesafe`` test converts the same input with ``--judge none`` and ``--judge typesafe``
and asserts that the judged output is better: a garbled text layer is replaced by the OCR of the page, a brand
line repeated on every slide goes while the slide titles and labels stay, an OCR answer that is only an error
is re-read. These tests call the real TypeSafe API (the only third party mocked is the OCR model, through
``fake_openai``); they are skipped unless the extra is installed and ``TYPESAFE_API_KEY`` is set, and fail
instead of skipping when ``D2M_REQUIRE_TYPESAFE=1``. Verdicts are cached per test under its scratch dir.
"""

import json
import os
import subprocess
import sys

import pytest

from tests.e2e import builders_judge, builders_ocr, builders_route
from tests.e2e import fake_openai as fake
from tests.e2e.fake_openai import FakeOpenAI

INVOICE_BODY = [
    "Invoice total EUR 2340 due on 14 March 2026",
    "Delivery of 1200 units to the Rotterdam depot",
    "Payment reference AX 7731 quoted on all remittances",
]
REAL_WORDS = ("Invoice total", "Rotterdam depot", "remittances")


def words(text):
    """``text`` with every run of whitespace collapsed to one space."""
    return " ".join((text or "").split())


def warnings_in(stderr):
    return [line for line in stderr.splitlines() if " - WARNING - " in line]


def judge_stats(result):
    return ((result.json or {}).get("metadata") or {}).get("extra", {}).get("judge") or {}


def garbled(text, mode):
    """``text`` as the broken text layer of ``builders_route.garble(mode)`` reads it."""
    return "".join(builders_route.GARBLERS[mode](char) for char in text)


@pytest.fixture
def judge_env(e2e_dir):
    """Environment of a judged run: its own verdict cache, so the test does not depend on earlier runs."""
    return {"DOC2MARK_JUDGE_CACHE": str(e2e_dir / "judge-cache")}


@pytest.fixture
def fake_llm():
    with FakeOpenAI() as server:
        yield server


# ---------------------------------------------------------------------------------------------------------------
# Without the add-on: the deterministic rules decide, as with --judge none


def _shifted_invoice(e2e_dir, require_tool):
    require_tool("tesseract")
    pdf = builders_route.garbled_text_pdf(e2e_dir / "shifted.pdf", "INVOICE SUMMARY", INVOICE_BODY, "shifted")
    return pdf, ("--ocr", "tesseract", "--ocr-images")


def _deck(e2e_dir, require_tool):
    return builders_judge.deck_pdf(e2e_dir / "deck.pdf"), ()


@pytest.mark.parametrize("document", [_shifted_invoice, _deck], ids=["garbled-layer-with-ocr", "deck-without-ocr"])
@pytest.mark.parametrize("how", ["flag", "env"])
def test_typesafe_judge_without_a_key_changes_nothing(run_cli, require_tool, e2e_dir, judge_env, document, how):
    """``--judge typesafe`` (or ``DOC2MARK_JUDGE=typesafe``) without ``TYPESAFE_API_KEY`` -- or without the
    extra -- must not fail the run or change its output: one warning says the judge is unavailable."""
    pdf, args = document(e2e_dir, require_tool)
    env = {"TYPESAFE_API_KEY": None, "DOC2MARK_JUDGE": None, **judge_env}

    baseline = run_cli(pdf, *args, "--judge", "none", env=env)
    if how == "flag":
        judged = run_cli(pdf, *args, "--judge", "typesafe", env=env)
    else:
        judged = run_cli(pdf, *args, env={**env, "DOC2MARK_JUDGE": "typesafe"})

    assert baseline.exit_code == 0, baseline.describe()
    assert judged.exit_code == 0, judged.describe()
    assert judged.markdown == baseline.markdown, judged.describe()
    unavailable = [line for line in warnings_in(judged.stderr) if "TypeSafe judge unavailable" in line]
    assert len(unavailable) == 1, judged.describe()
    assert "TYPESAFE_API_KEY is not set" in unavailable[0] or "typesafe-sdk is not installed" in unavailable[0]
    assert not [line for line in warnings_in(baseline.stderr) if "judge" in line.lower()], baseline.describe()


# ---------------------------------------------------------------------------------------------------------------
# With the add-on: the judged output is better than the rules' output on the same input


@pytest.mark.requires_typesafe
@pytest.mark.parametrize("mode", ["shifted", "glyph_offset"])
def test_typesafe_judge_replaces_a_garbled_text_layer_by_the_ocr_of_the_page(run_cli, require_tool, e2e_dir,
                                                                             judge_env, mode):
    """A text layer whose letters are shifted (wrong ToUnicode map) or offset by 29 (glyph IDs taken for
    Unicode) is valid Unicode, so no character rule flags it: without a judge the nonsense is the page's text.
    Jev judges the layer illegible, and with OCR on the page is read from its render instead."""
    require_tool("tesseract")
    pdf = builders_route.garbled_text_pdf(e2e_dir / f"{mode}.pdf", "INVOICE SUMMARY", INVOICE_BODY, mode)
    nonsense = garbled("Delivery of 1200 units", mode)

    without = run_cli(pdf, "--ocr", "tesseract", "--ocr-images", "--judge", "none", fmt="both")
    judged = run_cli(pdf, "--ocr", "tesseract", "--ocr-images", "--judge", "typesafe", fmt="both", env=judge_env)

    assert without.exit_code == 0, without.describe()
    assert nonsense in words(without.markdown), without.describe()
    assert not any(real in words(without.markdown) for real in REAL_WORDS), without.describe()

    assert judged.exit_code == 0, judged.describe()
    assert all(real in words(judged.markdown) for real in REAL_WORDS), judged.describe()
    assert nonsense not in words(judged.markdown), judged.describe()
    stats = judge_stats(judged)
    assert stats.get("model") == "jev-1.13.0" and stats.get("asked", 0) >= 1 and stats.get("failed") == 0, judged.json
    assert not [line for line in warnings_in(judged.stderr) if "judge" in line.lower()], judged.describe()


@pytest.mark.requires_typesafe
def test_typesafe_judge_thins_the_brand_line_to_one_copy(run_cli, e2e_dir, judge_env):
    """The deck's brand line ("by Contoso Labs") sits under the logo text, in the header row of every slide:
    the rule cannot take it off the page edge, so without a judge it is emitted once per slide. Jev calls it
    page chrome: its repeats go, its first copy stays (the judge never removes the last copy of a line), and
    every slide title, numbered slide label and body line stays."""
    pdf = builders_judge.deck_pdf(e2e_dir / "deck.pdf")
    slides = builders_judge.DECK_SLIDES

    without = run_cli(pdf, "--judge", "none")
    judged = run_cli(pdf, "--judge", "typesafe", fmt="both", env=judge_env)

    assert without.exit_code == 0 and judged.exit_code == 0, judged.describe()
    assert without.markdown.count(builders_judge.BRAND_LINE) == len(slides), without.describe()
    assert judged.markdown.count(builders_judge.BRAND_LINE) == 1, judged.describe()
    for number, (title, body) in enumerate(slides, 1):
        for result in (without, judged):
            text = words(result.markdown)
            assert f"{number:02d} / {title}" in text, result.describe()
            assert all(line in text for line in (title, *body)), result.describe()
    stats = judge_stats(judged)
    assert stats.get("asked", 0) >= 1 and stats.get("failed") == 0, judged.json


@pytest.mark.requires_typesafe
def test_typesafe_judge_keeps_the_letterhead_of_a_letter(run_cli, e2e_dir, judge_env):
    """Review B1: the rule keeps the first copy of a letterhead repeated on every page and drops the others; a
    chrome verdict on that first copy deleted the sender's name, address and phone. The judge is not asked
    about a first copy: the letter comes out exactly as without it, with the letterhead once."""
    pdf = builders_judge.letter_pdf(e2e_dir / "letter.pdf")

    without = run_cli(pdf, "--judge", "none")
    judged = run_cli(pdf, "--judge", "typesafe", fmt="both", env=judge_env)

    assert without.exit_code == 0 and judged.exit_code == 0, judged.describe()
    for line in builders_judge.LETTERHEAD:
        assert without.markdown.count(line) == 1 and judged.markdown.count(line) == 1, judged.describe()
    assert judged.markdown == without.markdown, judged.describe()
    assert judge_stats(judged).get("asked", 0) == 0, judged.json


@pytest.mark.requires_typesafe
@pytest.mark.parametrize("answer", [
    "Unable to process the image. Please provide a clearer scan of the page.",
    "Error: the image could not be processed (unsupported content).",
])
def test_typesafe_judge_rereads_an_image_whose_answer_is_only_an_error(run_cli, e2e_dir, judge_env, fake_llm, answer):
    """An OCR answer that is only an error notice has no first-person refusal the patterns can be sure of (a
    support page says the same), so without a judge it is indexed as the page's text. Jev rates it no content
    (at least 0.95): the image is re-read with the free-form recovery, whose transcription becomes the page."""
    scan = builders_ocr.scan_pdf(e2e_dir / "scan.pdf")
    transcription = "RENEWAL NOTICE 5518\n\nPolicy 4471 renews on 1 November 2026."

    fake_llm.script(structured=[fake.page(answer)], free_form=[fake.text(transcription)])
    without = run_cli(scan, "--ocr", "openai", "--ocr-images", "--judge", "none", env=fake_llm.env, fmt="both")
    fake_llm.script(structured=[fake.page(answer)], free_form=[fake.text(transcription)])
    judged = run_cli(scan, "--ocr", "openai", "--ocr-images", "--judge", "typesafe",
                     env={**fake_llm.env, **judge_env}, fmt="both")

    assert without.exit_code == 0, without.describe()
    assert builders_ocr.normalize(answer) in builders_ocr.normalize(without.markdown), without.describe()
    assert "RENEWAL NOTICE 5518" not in without.markdown, without.describe()

    assert judged.exit_code == 0, judged.describe()
    assert "RENEWAL NOTICE 5518" in judged.markdown and "Policy 4471" in judged.markdown, judged.describe()
    assert builders_ocr.normalize(answer) not in builders_ocr.normalize(judged.markdown), judged.describe()
    assert judge_stats(judged).get("failed") == 0, judged.json


@pytest.mark.requires_typesafe
@pytest.mark.parametrize("answer", [
    "Sorry we missed you! We'll try again tomorrow between 9 and 12. Parcel 4471-2290.",
    "We are sorry, our store is closed on 3 October for inventory.",
])
def test_typesafe_judge_keeps_a_short_note_that_apologises(run_cli, e2e_dir, judge_env, fake_llm, answer):
    """The other side of the refusal judge: a real short note with apology words is page content and stays
    exactly as without a judge, and is not re-read."""
    scan = builders_ocr.scan_pdf(e2e_dir / "scan.pdf")
    fake_llm.script(structured=[fake.page(answer)], free_form=[fake.text("unused")])

    judged = run_cli(scan, "--ocr", "openai", "--ocr-images", "--judge", "typesafe",
                     env={**fake_llm.env, **judge_env}, fmt="both")

    assert judged.exit_code == 0, judged.describe()
    assert builders_ocr.normalize(answer) in builders_ocr.normalize(judged.markdown), judged.describe()
    assert fake_llm.requests_of("free_form") == [], "a kept answer must not be re-read"
    assert judge_stats(judged).get("asked") == 1 and judge_stats(judged).get("failed") == 0, judged.json


# ---------------------------------------------------------------------------------------------------------------
# Wrap-up follow-ups: what a judged run prints, and where a suspected OCR answer is reported


@pytest.mark.requires_typesafe
def test_typesafe_judge_prints_no_request_lines_by_default(run_cli, e2e_dir, judge_env):
    """A default ``--judge typesafe`` run printed one INFO line per TypeSafe request and retry: the SDK's logger
    was set to INFO explicitly, which let its records past the CLI's WARNING level. They show with ``-v`` (the
    request's method, URL, status and time), and a request or response body (page text) never does."""
    pdf = builders_judge.deck_pdf(e2e_dir / "deck.pdf")

    quiet = run_cli(pdf, "--judge", "typesafe", fmt="both", env=judge_env)
    verbose = run_cli(pdf, "--judge", "typesafe", "-v",
                      env={"DOC2MARK_JUDGE_CACHE": str(e2e_dir / "judge-cache-verbose")})

    assert quiet.exit_code == 0 and verbose.exit_code == 0, verbose.describe()
    assert judge_stats(quiet).get("fresh", 0) >= 1, quiet.json
    assert [line for line in quiet.stderr.splitlines() if " - INFO - " in line or " - DEBUG - " in line] == [], \
        quiet.describe()
    requests = [line for line in verbose.stderr.splitlines() if " - INFO - " in line and "typesafe" in line.lower()
                and " <- " in line]
    assert requests, verbose.describe()
    assert not [line for line in verbose.stderr.splitlines() if "body=" in line or "headers=" in line], \
        verbose.describe()
    assert builders_judge.BRAND_LINE not in "\n".join(requests), verbose.describe()


SUSPECTING_JUDGE = (
    "import json, sys\n"
    "from doc2mark import UnifiedDocumentLoader\n"
    "class Judge:\n"
    "    def non_content_judge(self, answer):\n"
    "        return json.loads(sys.argv[2])\n"
    "loader = UnifiedDocumentLoader(ocr_provider='openai', judge=Judge())\n"
    "result = loader.load(sys.argv[1], ocr_images=True)\n"
    "print(json.dumps({'content': result.content, 'ocr_issues': result.metadata.extra.get('ocr_issues')}))\n"
)


def run_api(e2e_dir, script, *args, env=None, timeout=300):
    """Run ``script`` with this interpreter (the public ``doc2mark`` Python API), in the test's scratch dir."""
    proc = subprocess.run([sys.executable, "-c", script, *map(str, args)], cwd=e2e_dir, capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout, env={**os.environ, **(env or {})})
    assert proc.returncode == 0, f"exit {proc.returncode}\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    return proc


@pytest.mark.parametrize("verdict, suspected", [(0.4, True), (0.1, False)])
def test_an_answer_the_judge_suspects_is_kept_and_reported_with_its_page(e2e_dir, fake_llm, verdict, suspected):
    """An OCR answer the non-content judge rates close to "no content" (0.90-0.95 raw for Jev, between the
    pipeline's 0.3 and 0.5) is kept as the page's text, and was flagged only in the per-image OCR metadata,
    which never reaches the caller. It is counted in ``metadata.extra["ocr_issues"]["suspected"]``, with its
    page, next to refusals and failures. The judge is a plain Python object here (the hook has no CLI switch)."""
    scan = builders_ocr.scan_pdf(e2e_dir / "scan.pdf")
    answer = "Sorry we missed you! We'll try again tomorrow between 9 and 12. Parcel 4471-2290."
    fake_llm.script(structured=[fake.page(answer)], free_form=[fake.text("unused")])

    proc = run_api(e2e_dir, SUSPECTING_JUDGE, scan, json.dumps(verdict), env=fake_llm.env)

    output = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "Parcel 4471-2290" in output["content"], output
    issues = output["ocr_issues"] or {}
    if suspected:
        assert issues.get("suspected") == 1 and issues.get("refused") == 0, output
        assert issues.get("locations") == [{"issue": "suspected", "image": 1, "page": 1}], output
    else:
        assert not issues.get("suspected"), output
    assert fake_llm.requests_of("free_form") == [], "a kept answer must not be re-read"
