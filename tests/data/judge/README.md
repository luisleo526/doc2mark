# Labelled sets for the optional judge hooks

Small, hand-labelled evaluation sets for doc2mark's three optional judge hooks. Thresholds are
calibrated on the `train` split and results reported on the `test` split. Every text and context
is what the current doc2mark hands the hook, captured from the real code, not typed by hand.

| file | hook | what one line is |
|---|---|---|
| `legibility.jsonl` | `legibility_judge(page_text)` (`doc2mark/core/strategy.py`) | one page text layer: is it legible? |
| `boilerplate.jsonl` | `boilerplate_judge(line_text, context)` (`PDFLoader`, `_detect_page_chrome`) | one repeated top/bottom line the rule kept and asked about: is it page chrome? |
| `non_content.jsonl` | `non_content_judge(ocr_text)` (`doc2mark/ocr/refusal.py`) | one OCR answer: is it only a refusal / "no readable text"? |
| `non_content_ambiguous.jsonl` | same | answers that are ambiguous by construction (not scored) |

Regenerate (same PyMuPDF and doc2mark give byte-identical files; the sets were built with
PyMuPDF 1.27.2, Pillow 12.2, fontTools 4.61, Python 3.13):

    python eval/judge_sets.py --out tests/data/judge            # all four files, atomically
    python eval/judge_sets.py --out tests/data/judge --check    # rebuild and compare byte for byte

Run it from the repository (the script puts its own checkout first on `sys.path` and refuses a
`doc2mark` imported from elsewhere). The detector fields (`detector`, `judged`, `pattern`,
`route`, `pipeline_asks`) and the boilerplate questions record doc2mark's behaviour at build
time: regenerate after changing the detectors, the routing or the page-chrome rule.

## Common fields

- `id`: unique; `group`: items made from the same base text or the same document share a group
  (and a split); `source`: where the text comes from; `kind`: the shape of the item (below);
  `label`: 0 or 1 (meaning per file); `split`: `train` or `test`.
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
| train | 30 | 24 | 54 |
| test | 25 | 23 | 48 |

| kind | label | train | test |
|---|---|---|---|
| garb_bad_ocr_layer | 0 | 5 | 3 |
| garb_cid | 0 | 1 | 1 |
| garb_cjk_substituted | 0 | 4 | 3 |
| garb_fffd | 0 | 2 | 2 |
| garb_glyph_offset | 0 | 2 | 3 |
| garb_mixed_half | 0 | 2 | 2 |
| garb_mojibake | 0 | 2 | 2 |
| garb_partial_title | 0 | 1 | 2 |
| garb_pua | 0 | 3 | 0 |
| garb_shifted_latin | 0 | 3 | 3 |
| garb_substituted_latin | 0 | 5 | 3 |
| garb_symbol_font | 0 | 0 | 1 |
| hard_legible_string | 1 | 7 | 7 |
| legible_generated | 1 | 11 | 11 |
| legible_ocr_layer | 1 | 1 | 1 |
| legible_stray_glyphs | 1 | 1 | 1 |
| real_document_text | 1 | 2 | 1 |
| real_pdf_page | 1 | 2 | 2 |

| label | items | detector garbled | judge consulted (`judged`) | pipeline asks the judge |
|---|---|---|---|---|
| 0 | 55 | 17 | 38 | 26 |
| 1 | 47 | 1 | 46 | 28 |

## boilerplate.jsonl

Label 1 (page chrome): removing every copy loses nothing a reader needs beyond the publisher's
identity, branding, marking or page numbering: company and brand names, taglines ("by
<company>"), logo text, confidentiality and classification marks, draft marks, copyright
notices, website and contact lines, company imprints, page numbers with a brand or label
("3 | Contoso Labs", "Slide 2 / 5"), identical print timestamps. Label 0 (content): anything
specific to the page or needed to read it: per-page labels ("Lesson · 1", "Exhibit – 4",
"Step 1 of 6", "NN / title"), per-page IDs ("Invoice · 1001"), section and statement titles,
unit notes, disclaimers and legal notes (verbatim-first keeps one copy), repeated table header
cells, account, customer and patient identifiers.

Fields: `text` and `context` exactly as the pipeline passed them to a recording judge
(`pdf_to_simple_json(path, extract_images=False, ocr_images=False, boilerplate_judge=...)`;
context keys `zone`, `pages`, `repeated_on`, `page_count`, `font_size`, `body_font_size`,
`reason`). Every document was converted twice: with a judge that answers None (what the rule
asks by itself) and with an oracle that answers the intended label (a numbered label judged
chrome makes the pipeline ask about its first copy as `first_occurrence`). `asked_in` is `both`,
`none` or `oracle`. Labels come from the generator: every line the builder places is
registered with its kind and label, and an asked line no generator placed fails the build.

Sources: 59 generated PDFs of 2 to 8 pages (A4 reports and letters, 16:9 slides, English,
German, Traditional and Simplified Chinese, Japanese), four of them deck-like (logo
text and a small "by <brand>" line in the header row beside a numbered "NN / title" label, over a
big title), plus `tests/e2e/builders_judge.py:deck_pdf` (the E2E deck: "by Contoso Labs",
"CONTOSO"). `sample_documents/sample_pdf.pdf` was run too; the pipeline asks nothing there.

Designed lines the pipeline never asked about (decided by the rule alone, so out of scope):
bare and worded page numbers ("Page 1 of 6", "1", "Seite 1 von 5", "第 1 頁"), which the rule
removes; lines whose text changes on every page without a page-number shape ("Exhibit 1",
"Slide 1", "第1課 基礎會計", "Invoice No. INV-7001", "Site log – 8 September 2026", a per-page
section title beside the brand), which never repeat; and the decks' "NN / title" labels.

### boilerplate (88 items)

| split | label 0 | label 1 | total |
|---|---|---|---|
| train | 16 | 27 | 43 |
| test | 18 | 27 | 45 |

| kind | label | train | test |
|---|---|---|---|
| account_identifier | 0 | 2 | 1 |
| brand_name | 1 | 8 | 7 |
| brand_tagline | 1 | 3 | 4 |
| company_imprint | 1 | 1 | 0 |
| confidentiality_mark | 1 | 3 | 3 |
| contact_line | 1 | 2 | 3 |
| copyright | 1 | 2 | 2 |
| disclaimer | 0 | 2 | 2 |
| legal_note | 0 | 0 | 1 |
| logo_text | 1 | 2 | 3 |
| marking | 1 | 1 | 1 |
| page_number_label | 1 | 4 | 4 |
| per_page_id | 0 | 0 | 1 |
| per_page_label | 0 | 1 | 2 |
| print_timestamp | 1 | 1 | 0 |
| section_title | 0 | 2 | 3 |
| statement_title | 0 | 2 | 2 |
| table_header_row | 0 | 4 | 4 |
| unit_note | 0 | 3 | 2 |

| reason | label 0 | label 1 |
|---|---|---|
| first_occurrence | 19 | 35 |
| attached_to_content | 8 | 12 |
| few_pages | 3 | 3 |
| numbered_label | 4 | 4 |

56 documents with at least one question; `asked_in`: both 84, oracle 4.

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
| train | 46 | 42 | 88 |
| test | 47 | 46 | 93 |

| kind | label | train | test |
|---|---|---|---|
| caveat_then_content | 0 | 11 | 12 |
| description | 0 | 2 | 2 |
| error_shape | 1 | 5 | 6 |
| multilingual_content | 0 | 1 | 2 |
| name_or_form | 0 | 3 | 3 |
| named_refusal_shape | 0 | 2 | 1 |
| no_text_en | 1 | 4 | 5 |
| no_text_ml | 1 | 6 | 6 |
| note_chat_letter | 0 | 6 | 7 |
| notice | 0 | 8 | 8 |
| placeholder | 1 | 5 | 6 |
| quote_or_slide | 0 | 5 | 4 |
| refusal_en | 1 | 8 | 8 |
| refusal_ml | 1 | 10 | 11 |
| safety_refusal | 1 | 4 | 4 |
| transcription | 0 | 5 | 5 |
| ui_error | 0 | 3 | 3 |

| label | items | pattern fires | judge consulted (`judged`) |
|---|---|---|---|
| 0 | 93 | 0 | 93 |
| 1 | 88 | 43 | 45 |

`non_content_ambiguous.jsonl`: 42 items (pattern fires on 17).

## Splits

`split` is assigned per `group`, so near-duplicates (a page and its garbled variants, the lines
of one document, the variants of one answer) never straddle train and test. Groups are taken in
the order of the SHA-256 of their name and each goes to the side that keeps its (kind, label)
strata most balanced (ties broken by the hash), which gives about 50/50 per stratum.

## Deck slice (never committed)

The Traditional-Chinese company deck that motivated these hooks is private and this repository
is public: neither the PDF nor any text from it is committed. `eval/judge_sets.py` exposes
`deck_items(path)`, which reads the deck in place at run time and returns
`{"legibility": [...], "boilerplate": [...], "unlabelled": [...]}`: every page's text layer
(label 1), the spike A transforms of six of its pages (CJK code-point shifts, private use, 20 %
U+FFFD, mojibake; label 0), and the repeated lines the pipeline asks about, labelled by their
shape (a "by <company>" line and a short upper-case logo text are chrome; a numbered
"NN / title" label is content; anything else is returned under `unlabelled`). All deck items are
`test` (held out from calibration). `python eval/judge_sets.py --deck <path>` prints their counts.
