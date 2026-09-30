# Labelled sets for the optional judge hooks

Small, hand-labelled evaluation sets for doc2mark's three optional judge hooks. Thresholds are
calibrated on the `train` split and results reported on the `test` split; the `external/` files
were written by someone else and are never used for calibration. Every generated text and context
is what the current doc2mark hands the hook, captured from the real code, not typed by hand.

| file | hook | what one line is |
|---|---|---|
| `legibility.jsonl` | `legibility_judge(page_text)` (`doc2mark/core/strategy.py`) | one page text layer: is it legible? |
| `boilerplate.jsonl` | `boilerplate_judge(line_text, context)` (`PDFLoader._detect_page_chrome`) | one repeated top/bottom line the rule kept and asks about: may its later copies go? |
| `non_content.jsonl` | `non_content_judge(ocr_text)` (`doc2mark/ocr/refusal.py`) | one OCR answer: is it only a refusal / "no readable text"? |
| `non_content_ambiguous.jsonl` | same | answers that are ambiguous by construction (not scored) |
| `external/{legibility,boilerplate,non_content}.jsonl` | the three hooks | the PR #22 reviewer's fresh items (split `external`, see below) |

Regenerate (same PyMuPDF and doc2mark give byte-identical files; the sets were built with
PyMuPDF 1.27.2, Pillow 12.2, fontTools 4.61, Python 3.13):

    python eval/judge_sets.py --out tests/data/judge            # all seven files, atomically
    python eval/judge_sets.py --out tests/data/judge --check    # rebuild and compare byte for byte

Run it from the repository (the script puts its own checkout first on `sys.path` and refuses a
`doc2mark` imported from elsewhere). It also prints the counts below, the split clusters and
cross-split similarities, and every designed boilerplate line the pipeline does not ask about.
The detector fields (`detector`, `judged`, `pattern`, `route`, `pipeline_asks`) and the
boilerplate questions record doc2mark's behaviour at build time: regenerate after changing the
detectors, the routing or the page-chrome rule.

## Common fields

- `id`: unique; `group`: items made from the same base text or the same document; `family`: the
  unit of the split (see "Splits"); `source`: where the text comes from; `kind`: the shape of the
  item (below); `label`: 0 or 1 (meaning per file); `split`: `train`, `test` or `external`.
- Escapes: invisible and garbage code points (controls, format characters, private use,
  non-ASCII spaces, U+FFFD) are written as `\uXXXX` JSON escapes; other text is plain UTF-8.

## legibility.jsonl

Label 1: a reader would rather have this text layer than an OCR of the page render (prose,
tables, numbers, code, hashes, part numbers, any script or language, a few stray bad glyphs).
Label 0: substantially garbled (shifted or substituted letters, glyph-ID offset like
`chr(ord(c) - 29)`, mojibake, private-use glyphs, `(cid:NN)`, U+FFFD runs, a noisy OCR layer,
half of the lines or a slide's big title garbled). Genuinely ambiguous middle cases were left
out rather than guessed (for example a long page whose only garbled line is its title, or
mojibake that touches only a few accented letters).

Fields: `text` is exactly the page text the judge receives (`measure_page(page,
keep_text=True).text`); `spans` is `[[text, font size, font name], ...]` such that
`strategy.text_layer_stats(spans)` reproduces the detector's statistics of that page (the
builder asserts `garbled`, `chars` and `garbage_glyphs` match `measure.signals.text_layer`; for
plain-string items, one span per line at 11.0 pt "Helvetica"). `detector` holds those stats;
`judged` is whether `judge_text_layer` would consult a judge (not garbled and at least
`MIN_JUDGED_CHARS` characters). For PDF pages, `route` is the page route with an OCR provider
active and no judge verdict, and `pipeline_asks` whether the pipeline actually calls the judge
for that page (recorded with a real `PDFLoader` and a recording judge; the builder asserts the
recorded text equals `text`); both are null for plain-string items.

Sources:

- `real_pdf_page`: every page of the committed sample PDFs (`sample_pdf.pdf`, `test-table.pdf`,
  `complex-tables/complex_table_test.pdf`). `test-table.pdf` p1 is labelled 1 as in the spike: its
  38 pt title "Specifications" has no ToUnicode (17 U+FFFD) but the dense table is intact; the
  detector flags it (by design, a garbled title weighs a lot), so the judge never sees it.
- `real_document_text`, `hard_legible_string`: the non-deck legible items of the Jev spike A
  (`sample_document.docx`, `fail-1.docx` and `sample_text.txt` text, and its 14 synthetic
  "hard legible" strings: financial table, hashes, part numbers, code, math, bibliography,
  Japanese, Korean, Simplified Chinese, a form, URLs, short labels, German, letter-spaced title).
- `garb_*` items with `source` `spikeA:...`: the spike A garbage transforms re-applied, span by
  span, to the same committed bases (Caesar shift, alphabet permutation, glyph offset, CJK
  code-point shift, private use, `(cid:NN)`, 40 % U+FFFD, mojibake, OCR-like noise, symbol
  glyphs, every other line permuted). Spike items whose text came from the private deck are
  not here (see "Deck slice").
- `generated:*`: one-page PDFs built by `eval/judge_sets.py` with PyMuPDF: 22 legible pages in
  varied genres and scripts (invoice, contract, letter, cash-flow statement, code, manual,
  slide, form, timetable, German invoice and terms, French terms, Spanish report, Japanese,
  Korean, Simplified and Traditional Chinese, a Chinese slide and form, Russian and Greek
  invoices, a mixed English/Chinese spec), and garbage drawn with an embedded font whose
  ToUnicode map is rewritten after subsetting, so the page renders correctly while its text
  layer is broken: shifted letters, permuted alphabet, glyph-ID offset (digits and punctuation
  fall below U+0020 and extract as U+FFFD, as in real files), CJK and Hangul code-point
  substitution, private use, no ToUnicode (U+FFFD), UTF-8-as-cp1252 mojibake; a `(cid:NN)`
  form re-typeset from a broken extraction; scans (Pillow) with a bad OCR layer, either
  invisible (render mode 3, a searchable scan) or painted under the page image ("text under
  the page image" exports); pages with only a slide's title or every other line garbled; and
  hard legible pages (two stray unmapped glyphs, a row of private-use icon stars, a 3 %-noise
  OCR layer).

Which garbage the deterministic detector sees: `detector.garbled` is true for U+FFFD, private
use, `(cid:NN)`, most mojibake and number-heavy glyph offsets; the valid-Unicode garbage
(shifted, permuted, glyph offset in prose, CJK substitution, OCR noise, partial pages) passes it,
and that is where a judge can help. Note that the pipeline OCRs a searchable scan from its
render before any judge is asked, so the invisible bad OCR layers have `pipeline_asks: false`
(they still test the judge contract, which allows the OCR layer as `page_text`); the
under-image variants are asked.

### legibility (102 items)

| split | label 0 | label 1 | total |
|---|---|---|---|
| train | 29 | 24 | 53 |
| test | 26 | 23 | 49 |

| kind | label | train | test |
|---|---|---|---|
| garb_bad_ocr_layer | 0 | 3 | 5 |
| garb_cid | 0 | 2 | 0 |
| garb_cjk_substituted | 0 | 4 | 3 |
| garb_fffd | 0 | 1 | 3 |
| garb_glyph_offset | 0 | 4 | 1 |
| garb_mixed_half | 0 | 2 | 2 |
| garb_mojibake | 0 | 1 | 3 |
| garb_partial_title | 0 | 3 | 0 |
| garb_pua | 0 | 2 | 1 |
| garb_shifted_latin | 0 | 3 | 3 |
| garb_substituted_latin | 0 | 4 | 4 |
| garb_symbol_font | 0 | 0 | 1 |
| hard_legible_string | 1 | 7 | 7 |
| legible_generated | 1 | 10 | 12 |
| legible_ocr_layer | 1 | 1 | 1 |
| legible_stray_glyphs | 1 | 1 | 1 |
| real_document_text | 1 | 2 | 1 |
| real_pdf_page | 1 | 3 | 1 |

| label | items | detector garbled | judge consulted (`judged`) | pipeline asks the judge |
|---|---|---|---|---|
| 0 | 55 | 17 | 38 | 26 |
| 1 | 47 | 1 | 46 | 28 |

## boilerplate.jsonl

What the pipeline asks (after the PR #22 review, blocker B1): the verbatim-first rule keeps a
repeated top/bottom line when the evidence for page chrome is weak, and asks the judge about it
with a `reason`: `attached_to_content` (no clear gap separates it from the page's text, or other
text sits between it and the page edge), `few_pages` (it repeats on too few pages) or
`numbered_label` (a number counting with the pages, labelled with anything but a page word:
`Lesson · 3`, `3 | Contoso Labs`). At or above 0.5 the judge removes the line's later copies
(2..N) that the rule kept; the first copy always stays, as plain text, so a line is thinned out to
one copy, never lost. The judge is no longer asked about a running header's first copy
(`first_occurrence`: the rule removed its later copies already).

The label is therefore about the OUTPUT, how many copies must remain in the Markdown
(`expected_copies`; `label` 1 = `"one"`, 0 = `"all"`; the judge's action, "chrome", is `"one"`):

- `"one"`: keep only the first copy: company and brand names, logo text, taglines ("by
  <company>"), confidentiality and classification marks, copyright notices, website, contact and
  imprint lines, identical print stamps, a brand or slide word with the page number ("3 | Contoso
  Labs", "Fabrikam · 3", "Slide 3 / 5", "Folie 3 / 5").
- `"all"`: a reader needs it where it appears: per-page labels ("Lesson · 3", "Unit · 3",
  "Exhibit – 4", "Step 3 of 6", "NN / title"), per-page IDs, section, chapter and statement titles,
  unit notes, disclaimers and legal notes, continuation notes ("(continued on next page)"),
  repeated table header cells, dates, IDs, names and accounts that belong to the content.

Fields: `text` and `context` exactly as the pipeline passed them to a recording judge
(`pdf_to_simple_json(path, extract_images=False, ocr_images=False, boilerplate_judge=...)`;
context keys `zone`, `pages`, `repeated_on`, `page_count`, `font_size`, `body_font_size`,
`reason`); `lang` the document's language; `expected_copies` as above; `asked` is true for every
item here (the scored set is exactly the questions the pipeline asks). Every document was
converted twice, with a judge that answers None and with an oracle that answers the intended
label; `asked_in` is `both`, `none` or `oracle` (the two runs ask the same questions now). Labels
come from the generator: every line the builder places is registered with its kind and expected
copies, and an asked line no generator placed fails the build, as does any other reason.

Sources: 102 generated PDFs of 2 to 8 pages (A4 reports, statements, letters and forms,
16:9 slides; English, German, Traditional and Simplified Chinese, Japanese), 4 of them
deck-like (logo text and a small "by <brand>" line in the header row beside a numbered
"NN / title" label, over a big title), plus `tests/e2e/builders_judge.py:deck_pdf` (the E2E
deck: "by Contoso Labs", "CONTOSO"). They are built so that the rule keeps their repeated lines:
brand lines, marks and unit notes in a header row beside a per-page title, lines with no gap to
the body text (header or footer), repeated table header cells over the body, lines on only 2 or 3
pages of a longer document or on both pages of a two-page one, and numbered labels.
`sample_documents/sample_pdf.pdf` was run too; the pipeline asks nothing there.

Designed lines decided by the rule alone (not asked, so not in the scored set), from the
converted output of the None run: 50 lines repeated on 2 or more pages that were
asked about as `first_occurrence` before (exactly the previous set's 54 `first_occurrence`
questions less the 4 that only an oracle "chrome" verdict on a numbered label caused; those labels
are asked once, as `numbered_label`) are now decided by the rule alone, and for every one of
them the rule already keeps exactly one copy, the first (the builder counts the copies left in
`pdf_to_simple_json`'s output outside `text:header`/`text:footer`): 31 of them are
"one" lines (brand, mark, copyright, contact and imprint lines, taglines, print stamps), and
19 are "all" lines (statement titles, unit notes, disclaimers and legal notes,
account, customer and patient lines, running chapter titles) whose later copies the rule
removes as running headers, by design (verbatim first keeps the first copy). Page numbers ("Page
1 of 6", "1", "Seite 1 von 5", "第 1 頁") are removed by the rule; lines whose text changes on
every page without a label pattern ("Exhibit 1", "Slide 1", "第1課 基礎會計", "Invoice No.
INV-7001", "Site log – 8 September 2026", per-page titles beside a brand) never repeat and are
kept; and the decks' "NN / title" labels are kept.

### boilerplate (84 items)

| split | label 0 (all) | label 1 (one) | total |
|---|---|---|---|
| train | 22 | 20 | 42 |
| test | 21 | 21 | 42 |

| kind | label | train | test |
|---|---|---|---|
| account_identifier | 0 | 4 | 0 |
| brand_name | 1 | 3 | 8 |
| brand_tagline | 1 | 5 | 0 |
| confidentiality_mark | 1 | 2 | 4 |
| contact_line | 1 | 1 | 3 |
| continuation_note | 0 | 4 | 0 |
| copyright | 1 | 1 | 1 |
| document_id | 0 | 0 | 2 |
| logo_text | 1 | 5 | 0 |
| page_number_label | 1 | 3 | 4 |
| per_page_id | 0 | 0 | 1 |
| per_page_label | 0 | 4 | 3 |
| print_timestamp | 1 | 0 | 1 |
| section_title | 0 | 3 | 3 |
| table_header_row | 0 | 6 | 8 |
| unit_note | 0 | 1 | 4 |

| reason | all, train | all, test | one, train | one, test |
|---|---|---|---|---|
| attached_to_content | 13 | 12 | 14 | 10 |
| few_pages | 5 | 5 | 3 | 7 |
| numbered_label | 4 | 4 | 3 | 4 |

| language | all, train | all, test | one, train | one, test |
|---|---|---|---|---|
| en | 4 | 11 | 7 | 9 |
| de | 4 | 6 | 4 | 4 |
| zh | 7 | 2 | 4 | 4 |
| ja | 7 | 2 | 5 | 4 |

65 documents with at least one question, in 29 families; `asked_in`: both 84.

## non_content.jsonl and non_content_ambiguous.jsonl

Label 1: the whole answer is only a refusal, apology, error or "no readable text" statement,
with nothing transcribed or described from the image. Label 0: the answer contains any
transcribed or described content, including caveat-then-content answers ("I can't read the
handwriting, but the printed part reads: ..."), short real notes ("Sorry we missed you! ..."),
names, multilingual text, notices with apology words, form values, captions and UI error
screens with specifics.

Fields: `text` is the OCR answer as the model wrote it; `pattern` is whether
`matches_non_content_pattern` fires on it (then the judge is never asked); `judged` is whether
`non_content_reason` would consult a judge (the pattern does not fire and the normalized answer
has at most `MAX_JUDGE_CHARS` characters). Recompute both at evaluation time if the patterns
change.

Sources: the 42 answers of the Jev spike B (`spikeB`; three whose text came from the private
deck were replaced by fictional text of the same shape, and one moved to the ambiguous file);
the PR #19 reviewer's hard negatives and refusal probes (`review-pr19`); examples from
`tests/test_ocr_refusal.py`; and new items (`new`): refusals and "no text" statements the
patterns miss (paraphrases, JSON and error shapes, safety refusals, Chinese, Japanese, Korean,
German, French, Spanish) and content that shares their words.

`non_content_ambiguous.jsonl` holds answers that are ambiguous by construction: generic refusal
lines that name nothing ("Sorry, I can't help.", "I'm sorry, but I can't help you with that."),
can't-read lines a person could equally write in a chat or support reply ("Sorry, I cannot read
the document you sent."), and a few statements that could be an app message or the model. Their
`label` and `split` are null and `why` says what makes them ambiguous; do not score them.

### non_content (181 items)

| split | label 0 | label 1 | total |
|---|---|---|---|
| train | 47 | 44 | 91 |
| test | 46 | 44 | 90 |

| kind | label | train | test |
|---|---|---|---|
| caveat_then_content | 0 | 12 | 11 |
| description | 0 | 2 | 2 |
| error_shape | 1 | 5 | 6 |
| multilingual_content | 0 | 1 | 2 |
| name_or_form | 0 | 3 | 3 |
| named_refusal_shape | 0 | 3 | 0 |
| no_text_en | 1 | 5 | 4 |
| no_text_ml | 1 | 0 | 12 |
| note_chat_letter | 0 | 8 | 5 |
| notice | 0 | 8 | 8 |
| placeholder | 1 | 6 | 5 |
| quote_or_slide | 0 | 4 | 5 |
| refusal_en | 1 | 8 | 8 |
| refusal_ml | 1 | 15 | 6 |
| safety_refusal | 1 | 5 | 3 |
| transcription | 0 | 5 | 5 |
| ui_error | 0 | 1 | 5 |

| label | items | pattern fires | judge consulted (`judged`) |
|---|---|---|---|
| 0 | 93 | 0 | 93 |
| 1 | 88 | 43 | 45 |

`non_content_ambiguous.jsonl`: 42 items (pattern fires on 17).

## External set (external/, never used for calibration)

`external/legibility.jsonl`, `external/boilerplate.jsonl` and `external/non_content.jsonl` hold
the fresh items the PR #22 reviewer wrote to test the judge on data our generators did not make
(`source` `review-pr22`, `split` `external`; 16 legibility, 20 boilerplate and 24 non-content
items). The reviewer's file (`.executors/d2m-review-jev/probes/fresh_items.py`, outside the
repository) is embedded verbatim in `eval/judge_sets.py` (`_review_pr22_items`), so the builder
reproduces these files. Report them separately from TEST; never calibrate on them.

- legibility: the reviewer's text (rot13, line reversal and three code-page mojibakes are applied
  by the embedded code), `spans` one per line at 11.0 pt "Helvetica", `detector` and `judged`
  computed as for the other plain-string items; `route` and `pipeline_asks` null.
- boilerplate: the reviewer's line and context. 17 of the 20 have `reason` `first_occurrence`,
  which the pipeline no longer asks about: they have `asked: false` and `expected_copies: "one"`
  (the rule keeps one copy; not scored for the judge). The 3 others (`attached_to_content`,
  `few_pages`) have `asked: true` and `expected_copies` from the reviewer's label (1 -> "one",
  0 -> "all"). `reviewer_label` keeps the reviewer's original chrome/content label.
- non_content: the reviewer's answer, with `pattern` and `judged` computed as above; `kind` is
  `no_content` (label 1) or `content` (label 0).

| file | items | label 0 | label 1 | notes |
|---|---|---|---|---|
| `external/legibility.jsonl` | 16 | 6 | 10 | detector garbled on 1 (label 0: 1); judge consulted on 15 |
| `external/boilerplate.jsonl` | 20 | 2 | 18 | `asked: true` 3 (f-bp-table-cont attached_to_content all, f-bp-chapter-few few_pages all, f-bp-slide-brand attached_to_content one); `asked: false` 17 (first_occurrence, expected_copies one; reviewer labels: 11 content, 6 chrome) |
| `external/non_content.jsonl` | 24 | 15 | 9 | pattern fires on 0 (label 1: 0, label 0: 0); judge consulted on 24 |

## Splits

`split` is assigned by FAMILY, not by document: every item of one generator template (all its
language variants and brand or name substitutions: e.g. the notes header row with a unit note in
English, German, Chinese and Japanese, the five deck-like documents, the header row with a brand
beside a per-page title in three languages), every item of one base text (a page and all its garbage
transforms), one sample document (all its pages), and one seed answer with its derived variants
(translations of one refusal, the reviewer's hard negatives derived from a spike B answer) is one
family.

Near-duplicates are then clustered within each hook: texts are NFKC-normalised, case-folded and
whitespace-collapsed, and two items of different families are near-duplicates when their char
5-gram Jaccard is at least 0.5 or their `difflib.SequenceMatcher` ratio at least 0.8 (page
texts for legibility, answers for non_content, line texts for boilerplate). For boilerplate the
line text is compared whatever the reason: a pair with similar text is linked in any case, so
text + reason near-duplicates are covered, while concatenating the reason itself would link every
two short lines that merely share a reason. Families linked this way are merged with union-find,
and each merged cluster goes to one split. Clusters are placed largest first (ties by the
SHA-256 of their name), each on the side that keeps the label counts, then the (kind, label)
strata (and for boilerplate the (language, label) and (reason, label) strata) most balanced. A
cluster that holds a near-duplicate of an `external/` item is placed in test, so the external set
stays independent of calibration.

Largest cross-split similarity after the split (recomputed from the files):

| hook | families | clusters (largest) | forced to test | max cross-split Jaccard | max cross-split ratio | pairs above 0.6 | external vs train (Jaccard / ratio) | external vs test (Jaccard / ratio) |
|---|---|---|---|---|---|---|---|---|
| legibility | 41 | 39 (13 items) | 0 | 0.144 | 0.362 | 0 | 0.011 / 0.299 | 0.024 / 0.236 |
| boilerplate | 29 | 27 (17 items) | 0 | 0.245 | 0.667 | 1 | 0.113 / 0.356 | 0.188 / 0.565 |
| non_content | 114 | 112 (13 items) | 1 | 0.405 | 0.745 | 12 | 0.314 / 0.627 | 0.651 / 0.886 |

Cross-split pairs above 0.6 (all below the near-duplicate thresholds, so in different clusters; different sentences that share words or a script's function words):

- boilerplate: `bp-de-footer-no-gap-mark-1` (test) ~ `bp-de-mark-pair-1` (train): Jaccard 0.245, ratio 0.667
- non_content: `nc-new-ko-blurry` (train) ~ `nc-new-ko-no-text` (test): Jaccard 0.194, ratio 0.745
- non_content: `nc-ko-refusal` (train) ~ `nc-new-ko-no-text` (test): Jaccard 0.184, ratio 0.717
- non_content: `nc-zh-tw-refusal` (train) ~ `nc-zh-no-transcription` (test): Jaccard 0.083, ratio 0.706
- non_content: `nc-openai-classic` (train) ~ `nc-new-policy` (test): Jaccard 0.343, ratio 0.694
- non_content: `nc-cannot-provide-transcription` (test) ~ `nc-sensitive-description` (train): Jaccard 0.352, ratio 0.689
- non_content: `nc-placeholder-no-content` (test) ~ `nc-new-no-legible-content` (train): Jaccard 0.211, ratio 0.645
- non_content: `nc-no-text-found` (test) ~ `nc-new-no-text-parens` (train): Jaccard 0.273, ratio 0.636
- non_content: `nc-openai-classic` (train) ~ `nc-meeting-friday` (test): Jaccard 0.247, ratio 0.635
- non_content: `nc-ja-upload-outage` (train) ~ `nc-new-ja-recognize` (test): Jaccard 0.042, ratio 0.621
- non_content: `nc-ko-no-text` (test) ~ `nc-new-ko-blurry` (train): Jaccard 0.062, ratio 0.619
- non_content: `nc-unable-resolution` (test) ~ `nc-new-transcription-unavailable` (train): Jaccard 0.252, ratio 0.617
- non_content: `nc-ja-refusal` (train) ~ `nc-new-ja-recognize` (test): Jaccard 0.041, ratio 0.610

External items with a train or test item above 0.6 (a cluster holding an external item's near-duplicate is placed in test, so calibration never sees it):

- non_content: `f-nc-only-photo` ~ `nc-new-blank-page-image` (train): Jaccard 0.300, ratio 0.627
- non_content: `f-nc-left-blank` ~ `nc-intentionally-blank` (test): Jaccard 0.651, ratio 0.886 (a near-duplicate: forced into test)
- non_content: `f-nc-black` ~ `nc-blank-or-no-text` (test): Jaccard 0.214, ratio 0.653
- non_content: `f-nc-fr-none` ~ `nc-new-fr-no-text` (test): Jaccard 0.178, ratio 0.638

## Deck slice (never committed)

The Traditional-Chinese company deck that motivated these hooks is private and this repository
is public: neither the PDF nor any text from it is committed. `eval/judge_sets.py` exposes
`deck_items(path)`, which reads the deck in place at run time and returns
`{"legibility": [...], "boilerplate": [...], "unlabelled": [...]}`: every page's text layer
(label 1), the spike A transforms of six of its pages (CJK code-point shifts, private use, 20 %
U+FFFD, mojibake; label 0), and the repeated lines the pipeline asks about, labelled by their
shape (a "by <company>" line and a short upper-case logo text are "one"; a numbered
"NN / title" label is "all"; anything else is returned under `unlabelled`). All deck items are
`test` (held out from calibration). `python eval/judge_sets.py --deck <path>` prints their counts.
