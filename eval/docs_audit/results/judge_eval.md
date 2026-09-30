# Optional judge (TypeSafe/Jev): published numbers re-measured on main

| | |
|---|---|
| Host | spark2 (`spark-1693`, linux/arm64), Docker image `d2m-e2e:0a3d689002a4` |
| Environment | Python 3.12.14, doc2mark 0.6.1, typesafe-sdk 0.7.2, PyMuPDF 1.28.2, Pillow 12.3.0; model `jev-1.13.0` |
| SHA | `becb74bc46b4aad3090232e5b6f6cee73de86cb6` (main after #26; clean worktree) |
| Claims checked | README.md and docs/judge.rst as committed at `becb74b` ("claimed" columns below) |
| Date | 2026-09-30, 14:12 UTC |
| Command | `D2M_JUDGE_SETS_BASE=cb0018e eval/docs_audit/run_judge_eval.sh ~/code/doc2mark-d2m-docs-audit-becb74b OUT_DIR` (run from `~/code/.executors/d2m-docs-audit-judge/` on spark2, in tmux). Inside a fresh container it runs `python eval/judge_eval.py --calibrate --cache-dir /tmp/judge-cache --json ...` on an empty verdict cache, the same eval with the shipped thresholds (a replay), the whole-document runs on a second empty cache, and a rebuild of the labelled sets |
| Cost | Labelled sets: 344 requests, 187,116 input tokens, **$0.00786**. Documents: 26 requests, 13,520 tokens, $0.00057. **Total $0.0084.** A dress rehearsal with a dummy key (rejected, HTTP 401) cost nothing |
| Output | [`judge_eval.txt`](judge_eval.txt). Per-item probabilities (`judge_eval.json`) stay on spark2 in `~/code/.executors/d2m-docs-audit-judge/out/` |

The PR #22 numbers came from a Mac laptop run whose verdict cache already held 261 of its 351 verdicts from
earlier runs. In this run every verdict was asked fresh.

## Verdict

- **README.md: every number holds exactly.**
- **docs/judge.rst "How well it works" table: 52 of 54 figures hold.** Only non_content TRAIN rule + Jev
  moved: 95.6 / 97.6 / 93.2 % became 94.5 / 97.6 / 90.9 % (accuracy and recall changed, precision did
  not). One TRAIN refusal (`nc-new-es-blurry`) scored 0.94 this time, against 0.95 or above in PR #22,
  and 0.95 is where the judge acts.
- **Paragraph under the table:** the counts hold. Four margins moved by 0.01 to 0.02, all still on the
  right side of their thresholds.
- **Cost and latency:** the per-question averages hold. The page-text average (687) includes the private
  deck; the public sets give 684 in both runs. p50 holds and p95 is a little lower. The whole-document rows
  reproduce exactly in questions, tokens and cost; only the added time is lower (another host). The
  statement "about 300 tokens of fixed overhead" does not hold: the fixed part is about 420 to 470 tokens.
- **Why so little moved:** everything the eval depends on is unchanged since #22 (`strategy.py`,
  `ocr/refusal.py`, `judge/questions.py`, `eval/judge_eval.py` and `tests/data/judge/`). The labelled sets
  rebuilt from main are also **byte-identical** to the committed ones, with PyMuPDF 1.27.2 (their build
  version) and with 1.28.2 (what installs today). A control rebuild from `cb0018e`, the #22 merge, is
  identical too. So the rule-side changes of #23/#25/#26 do not change any page text, route, question or
  context in the sets. What is left is Jev's run-to-run variation (see the last section).

## README.md "Optional: quality judge (TypeSafe/Jev)"

Accuracy, rules alone → rules + judge.

| Decision | Set | Claimed | Measured now | |
|---|---|---|---|---|
| Text layer legible? | TEST | 67.4 % → 100 % (46 items) | 67.4 % → 100 % (46) | holds |
| Text layer legible? | EXTERNAL | 68.8 % → 100 % (16 items) | 68.8 % → 100 % (16) | holds |
| Repeated line is page chrome? | TEST | 50.0 % → 95.2 % (42) | 50.0 % → 95.2 % (42) | holds |
| Repeated line is page chrome? | EXTERNAL | 66.7 % → 100 % (3 asked of 20) | 66.7 % → 100 % (3 asked of 20) | holds |
| OCR answer is a refusal or error? | TEST | 76.7 % → 97.8 % (90) | 76.7 % → 97.8 % (90) | holds |
| OCR answer is a refusal or error? | EXTERNAL | 62.5 % → 83.3 % (24) | 62.5 % → 83.3 % (24) | holds |
| "EXTERNAL is 60 items" | | 60 | 16 + 20 + 24 = 60 | holds |

## docs/judge.rst "How well it works" table

Each cell is accuracy / precision / recall, in %.

| Hook, set (items) | Rule only, claimed | Rule only, now | Rule + Jev, claimed | Rule + Jev, now | |
|---|---|---|---|---|---|
| legibility, TRAIN (52) | 59.6 / 88.9 / 28.6 | 59.6 / 88.9 / 28.6 | 98.1 / 96.6 / 100 | 98.1 / 96.6 / 100 | holds |
| legibility, TEST (46) | 67.4 / 100 / 37.5 | 67.4 / 100 / 37.5 | 100 / 100 / 100 | 100 / 100 / 100 | holds |
| legibility, EXTERNAL (16) | 68.8 / 100 / 16.7 | 68.8 / 100 / 16.7 | 100 / 100 / 100 | 100 / 100 / 100 | holds |
| boilerplate, TRAIN (42) | 52.4 / n/a / 0 | 52.4 / n/a / 0 | 95.2 / 100 / 90.0 | 95.2 / 100 / 90.0 | holds |
| boilerplate, TEST (42) | 50.0 / n/a / 0 | 50.0 / n/a / 0 | 95.2 / 100 / 90.5 | 95.2 / 100 / 90.5 | holds |
| boilerplate, EXTERNAL (3 asked of 20) | 66.7 / n/a / 0 | 66.7 / n/a / 0 | 100 / 100 / 100 | 100 / 100 / 100 | holds |
| non_content, TRAIN (91) | 73.6 / 100 / 45.5 | 73.6 / 100 / 45.5 | 95.6 / 97.6 / 93.2 | **94.5 / 97.6 / 90.9** | **changed** (tp 41→40, fn 3→4) |
| non_content, TEST (90) | 76.7 / 100 / 52.3 | 76.7 / 100 / 52.3 | 97.8 / 97.7 / 97.7 | 97.8 / 97.7 / 97.7 | holds |
| non_content, EXTERNAL (24) | 62.5 / n/a / 0 | 62.5 / n/a / 0 | 83.3 / 100 / 55.6 | 83.3 / 100 / 55.6 | holds |

The shipped thresholds give the same table: `--calibrate` picks the shipped ones again (run 2 in
`judge_eval.txt`).

## docs/judge.rst: the paragraphs under the table

| Claim | Claimed | Measured now | |
|---|---|---|---|
| Legibility threshold, calibrated on TRAIN | illegible below 0.8 | `--calibrate` picks 0.8 | holds |
| Boilerplate threshold, calibrated on TRAIN | chrome from 0.7 | `--calibrate` picks 0.7 | holds |
| non_content thresholds (fixed policy) | act from 0.95, flag from 0.90 | 0.95 / 0.90 (the shipped `RAW_THRESHOLDS` / `RAW_SUSPECT_THRESHOLDS`) | holds |
| External boilerplate items that are a running header's first copy (not asked) | 17 of the 20 | 17 of 20 (3 asked) | holds |
| Designed lines of the generated documents decided by the rule alone | 50 | 50 (31 "one", 19 "all"); the rule keeps exactly one copy of each (`judge_sets.py` rebuild) | holds |
| TEST, output view: repeated copies the judge took out | 62 | 62 | holds |
| ... copies of a line that needed them | none | 0 | holds |
| ... chrome copies left in | 2 | 2 (`bp-ja-notice-pair-1` p = 0.61, `bp-stamp-pair-1` p = 0.67) | holds |
| Legibility: garbage pages on TRAIN score at most | 0.77 | **0.79** (the same page, `lg-gen-invoice_de-mojibake`) | changed, still below 0.8 (margin now 0.01) |
| Legibility: legible pages on TRAIN score at least | 0.87 | 0.87 (`lg-gen-manual_en-light-ocr-under-image`) | holds |
| Legibility: EXTERNAL lorem-ipsum page | 0.89 | **0.87** (`f-lg-lorem`) | changed, still above 0.8 |
| Boilerplate: content lines on TRAIN score up to (threshold 0.7) | 0.68 | **0.66** (the same line, `bp-zh-unit-footer-1`) | changed, further from 0.7 |
| non_content: refusals and real answers overlap between | 0.87 and 0.96 | **about 0.85 and 0.95** (see the note below) | changed |
| On TEST one real answer ("no results" from a search page) is still called no content | 1 | 1 (`nc-search-no-text`, p = 0.95) | holds |
| On EXTERNAL, refusals kept | 4 of 9 | 4 of 9 (p = 0.85, 0.91, 0.93, 0.94) | holds |
| ... of those, flagged `non_content_suspected` | 3 | 3 | holds |
| The judge cannot undo the detector's false alarm on a legible spec table | still OCR'd | still OCR'd: `lg-real-test-table-p1` is the rule's single TRAIN false positive in both columns | holds |
| Real deck paragraph: the brand line goes from 29 copies to 1; the logo text is asked about; garbled variants are all caught (3 of 6 without the judge) | | not re-measured (private deck) | n/a |

Note on the overlap. The lowest refusal now scores 0.85 (`f-nc-only-photo`, EXTERNAL), or 0.88 on TRAIN
and TEST alone. The highest real answer scores 0.95 (`nc-ja-upload-outage` on TRAIN, `nc-search-no-text`
on TEST). Real answers also sit at 0.84 and 0.86.

The PR #22 evidence JSON (`r1-eval-calibrated.json`) itself gives 0.88 as the lowest refusal and 0.96 as
the highest real answer, with a real answer at 0.87. So the "0.87" in the docs is probably that answer.
Either way the band moved down by about 0.01 to 0.02, and the conclusion still holds: only 0.95 and above
acts.

## docs/judge.rst "Cost and latency"

| Claim | Claimed | Measured now | |
|---|---|---|---|
| Page-text (legibility) question, average input tokens | 687 ($0.000029) | 684 ($0.0000287) | 687 included the 3 pages of the private deck; without them, PR #22's own evidence also gives 684 (token counts are deterministic) |
| Header-line (boilerplate) question | 573 ($0.000024) | 573 ($0.0000241) | holds |
| OCR-answer (non_content) question | 446 ($0.000019) | 446 ($0.0000187) | holds |
| "About 300 tokens of fixed overhead plus its state" | ~300 | **about 420 (non_content) to 470 (legibility)**: a 9- to 14-character OCR answer costs 429 to 430 tokens, a 57-character page 481; the smallest header-line question costs 562, its state included | does not hold (same in #22) |
| p50 latency per request ("from a laptop") | about 210 ms | 211.9 / 207.4 / 210.0 ms (legibility / boilerplate / non_content), 209.9 overall, from spark2 | holds |
| p95 latency per request | 270 to 300 ms; one run's header-line questions reached 530 ms | 263.9 / 246.9 / 269.3 ms, 266.9 overall; no outlier | slightly lower (other host, other run) |

Whole documents (fresh verdict cache, OCR on through a stand-in that returns nothing, pages asked
concurrently), measured as in PR #22:

- 12-page text report: `tests/e2e/pdfgen.text_pdf` with the "Maintenance report page N" pages of the PR #22
  script.
- 8-slide deck and 3-page letter: `builders_judge.deck_pdf` and `letter_pdf`.
- Each document goes through `judge_eval.document_run` with a new judge, again from the cache, then without
  a judge.
- Added time = the wall time with the judge minus the wall time without it.

| Document | Claimed: questions / input tokens / cost / added time | Measured now | |
|---|---|---|---|
| 12-page text report | 13 (12 pages, 1 header line) / 6,790 / $0.00029 / +1.3 s | 13 (12 legibility, 1 boilerplate) / 6,790 / $0.00028518 / **+1.16 s** (1.20 s with the judge, 0.04 s without) | questions, tokens and cost hold; added time lower |
| 8-slide deck with a brand line | 10 (8 pages, 2 header lines) / 5,051 / $0.00021 / +1.2 s | 10 (8 legibility, 2 boilerplate) / 5,051 / $0.00021214 / **+0.99 s** (1.02 s, 0.03 s) | questions, tokens and cost hold; added time lower |
| 3-page letter with a letterhead | 3 (pages; the letterhead is not asked about) / 1,679 / $0.00007 / +0.6 s | 3 (3 legibility, 0 boilerplate) / 1,679 / $0.00007052 / **+0.39 s** (0.41 s, 0.02 s) | questions, tokens and cost hold; added time lower |
| 30-page Traditional-Chinese company deck (image route) | 2 (header lines) / 1,135 / $0.00005 / +0.6 s | not re-measured (private deck) | n/a |
| A second conversion is answered from the verdict cache: no request, no added time | | 0 requests (13 / 10 / 3 cached); 0.04 / 0.05 / 0.02 s, against 0.04 / 0.03 / 0.02 s without a judge | holds |

The added times are single measurements on spark2, while PR #22 measured on a Mac laptop. They include
creating each judge's HTTP client.

The first line of section 3 in `judge_eval.txt` is PyMuPDF 1.28's `pymupdf_layout` advert. It appears
because the runner uses the library directly; only the CLI switches it off.

## Not re-measured, and why

- **Everything that needs the private Traditional-Chinese deck:**
  - the 30-page deck row of the whole-document table;
  - the deck paragraph (29 copies to 1; 3 of 6 garbled variants caught without the judge);
  - the deck's 3 pages inside the published 687-token average.

  The deck exists only on the Mac main checkout and is absent from spark2. This repository is public, so
  the deck must not be copied there and nothing from it may be committed.
- **"Measured latency per request from a laptop":** measured from spark2 instead. It shows the same order
  of magnitude, but the network path differs.
- **Service facts:** the price ($0.042 per million input tokens, output free) and the TypeSafe limits
  paragraph (100K tokens and 40 requests per second, 32K tokens of state) can't be measured by the eval.
  The eval's cost figures are tokens times the price constant in `doc2mark/judge/questions.py`.

## Run-to-run variation of Jev (for context)

These compare this run's verdicts with the PR #22 evidence verdicts, over the same item ids.

| Hook | Items | Mean \|Δp\| | Median | Max | Largest moves | Items that crossed their threshold |
|---|---|---|---|---|---|---|
| legibility | 95 | 0.008 | 0.000 | 0.05 | `lg-gen-form_en-title-garbled` 0.29 → 0.24, `lg-gen-code_py-permuted` 0.14 → 0.19 | none |
| boilerplate | 87 | 0.013 | 0.010 | 0.08 | `bp-de-lektion-1` 0.64 → 0.56, `bp-ja-unit-footer-1` 0.66 → 0.59 | none |
| non_content | 162 | 0.009 | 0.000 | 0.14 | `nc-named-ja` 0.81 → 0.67, `nc-retake-hint` 0.36 → 0.27 | `nc-new-es-blurry` (TRAIN) 0.95 → 0.94 |

docs/judge.rst says Jev varies "about +-0.04 run to run near 0.5". Most items move far less than that. Two
moved more: 0.08 (`bp-de-lektion-1`, 0.64 → 0.56) and 0.14 (`nc-named-ja`, 0.81 → 0.67). Neither crossed
its threshold.
