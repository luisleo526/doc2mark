Optional judge (TypeSafe / Jev)
===============================

doc2mark's rules are deterministic, and a few of their decisions stay open by design: when
the evidence is not strong enough the rule keeps the text (*verbatim first*). Three such
decision points have a **judge hook**: an optional callable asked only about the cases the
rule leaves open. The optional TypeSafe add-on answers all three with `Jev
<https://docs.typesafe.ai>`_, TypeSafe's calibrated yes/no model. Without it, or when it
cannot answer, the rules decide exactly as before.

What each hook decides
----------------------

``legibility_judge(page_text) -> Optional[float]``
   *Is this PDF page's extracted text layer legible?* A text layer can be valid Unicode and
   still be nonsense: a font whose ToUnicode map shifts every letter (``Lqyrlfh wrwdo``), a
   subset font whose glyph IDs were taken for characters (``6DPSOH``), CJK code points
   substituted for others. No character rule sees these. The judge gets the page text of layers the
   deterministic garbage detector did not flag: all of it up to 1,500 characters, else a
   sample of its beginning, middle and end (500 characters each), so a legible opening cannot
   hide a garbled body. It is consulted only when OCR is on (``--ocr <provider> --ocr-images``): a page it
   rates illegible is OCR'd from its render instead of emitting the garbage. See
   :func:`doc2mark.core.strategy.judge_text_layer`.

``boilerplate_judge(line_text, context) -> Optional[float]``
   *Is this repeated top/bottom line page chrome?* Running headers and footers are removed
   only on strong evidence, and verbatim first: a running header keeps its first copy. A line
   that repeats but is attached to the page's content, repeats on few pages, or is a number
   labelled like the pages, keeps every copy. The judge is asked about exactly those lines,
   with where and how often they repeat. A line it calls chrome is thinned to its **first
   copy**, which always stays, as plain text (or, when the text is already in the body of that
   page or an earlier one, as on a deck's cover, that copy is the one left): the brand line
   printed beside every slide title of a deck goes from once per slide to once. The judge can never remove the last copy of a
   line, and it is not asked about a running header's first copy (the rule already removed the
   others: a letterhead stays once). Titles, per-page labels (``Lesson 3``), unit notes and
   disclaimers keep every copy. See
   :class:`doc2mark.pipelines.pymupdf_advanced_pipeline.PDFLoader`.

``non_content_judge(ocr_text) -> Optional[float]``
   *Is this OCR answer only a refusal, an error or a "no readable text" statement?* The
   deterministic check fires only when the whole answer is a first-person refusal; an answer
   such as "Unable to process the image. Please provide a clearer scan." reads the same as a
   support page and is kept. The judge gets short answers (at most 600 characters) the
   patterns keep. From a TypeSafe probability of 0.95 the answer is no content: in the
   structured path the image is re-read by the free-form recovery, whose answer is screened
   the same way (the judge's verdict is cached, so a re-read that returns the same words is
   left out too); in the free-form path (``--no-structured``) the answer is dropped and the
   result flagged ``ocr_refusal``. From 0.90 up to 0.95 the answer is kept and flagged
   ``metadata["non_content_suspected"]`` and counted in the document's
   ``metadata.extra["ocr_issues"]["suspected"]``, with its page (or slide, or sheet) in
   ``locations``. A real answer the judge calls no content is lost,
   so the threshold sits above the overlap measured on the labelled sets. See
   :mod:`doc2mark.ocr.refusal`.

Each returns the probability of the "yes" and ``None`` when it cannot judge; ``None``, an
exception or a value outside ``[0, 1]`` leaves the rule's decision in place.

Enabling the TypeSafe judge
---------------------------

.. code-block:: bash

   pip install 'doc2mark[typesafe]'
   export TYPESAFE_API_KEY=...            # https://console.typesafe.ai
   doc2mark report.pdf --ocr tesseract --ocr-images --judge typesafe

.. code-block:: python

   from doc2mark import UnifiedDocumentLoader

   loader = UnifiedDocumentLoader(ocr_provider="tesseract", judge="typesafe")
   result = loader.load("report.pdf", ocr_images=True)
   result.metadata.extra.get("judge")   # questions asked, cached, failed, tokens, cost

``DOC2MARK_JUDGE=typesafe`` does the same when neither ``judge=`` nor ``--judge`` is given
(``--judge none`` / ``judge="none"`` switch it off). The default is no judge.

``judge=`` also takes an object: any of the attributes ``legibility_judge``,
``boilerplate_judge`` and ``non_content_judge`` it has are wired through to the PDF pipeline
and the OCR providers (the last one via ``OCRConfig.non_content_judge``). A hook passed
explicitly (``legibility_judge=``, ``boilerplate_judge=``, an ``OCRConfig`` that names a
``non_content_judge``) wins over the object's. ``TypeSafeJudge`` is such an object:

.. code-block:: python

   from doc2mark.judge import TypeSafeJudge

   judge = TypeSafeJudge(hooks=("legibility", "non_content"),   # or $DOC2MARK_JUDGE_HOOKS
                         cache_dir="/var/cache/doc2mark-judge",
                         timeout=2.0, max_workers=4)
   loader = UnifiedDocumentLoader(ocr_provider="openai", judge=judge)

When the judge cannot answer
----------------------------

The add-on is optional in every sense: without the ``typesafe`` extra the SDK is never
imported, and without a judge nothing is sent anywhere. When the judge is enabled but cannot
answer, every hook returns ``None`` and the rules decide:

- extra not installed, or no ``TYPESAFE_API_KEY``: one warning (``TypeSafe judge
  unavailable: ...``) and the same output as ``--judge none``;
- the service rejects the key (401/403): the judge switches itself off for the run;
- network errors, timeouts, rate limits (429/529), invalid answers: that question is decided
  by the rule, and the document logs one warning saying how many questions got no answer.
  Each HTTP attempt times out after 2 s and a question gets one retry within a 4 s budget,
  so a question that cannot be answered costs at most about 4 s; the questions of a document
  are asked concurrently. After three failures in a row the judge pauses for a minute (its
  questions then return at once), so during an outage a document costs at most a few
  seconds, again after each pause, and once per process with ``--parallel``.

A conversion never fails because of the judge. A document converted while its judge failed
on some question is not written to the document cache (``cache_dir``), and an OCR answer the
judge could not screen is not written to the OCR cache: a later run asks again. A judge that
cannot answer at all (no extra, no key, key rejected) is keyed in both caches as no judge,
which is what its output is.

Model, questions and determinism
--------------------------------

Every question is a TypeSafe *Noul* over a small named JSON state -- ``page_text``; the
``line`` with ``where`` and ``font`` in words (numbers become words: the model reads
semantics better than arithmetic); ``ocr_answer`` -- asked of the pinned model version
``jev-1.13.0`` (never the moving ``jev-latest`` alias: the thresholds belong to a version).
The question texts are versioned (``legibility-v2``, ``boilerplate-v2``,
``non-content-v1``, in :mod:`doc2mark.judge.questions`).

Jev is calibrated but not bit-exact (about +-0.04 run to run near 0.5), so every verdict is
cached on disk, keyed by model id, question version and a hash of the state:
``$DOC2MARK_JUDGE_CACHE``, else ``~/.cache/doc2mark/judge`` (``cache_dir=False`` keeps them in
memory). A re-run gives the same output and sends nothing. The OCR and document caches key
on the judge's model, question version and threshold, so a result decided by another judge
setup is not replayed.

Pages of one PDF are asked about concurrently (at most ``max_workers`` requests in flight),
as are the OCR answers of one batch; each HTTP attempt times out after ``timeout`` seconds
(2 by default), with one retry within a 4 s budget per question.

The SDK logs every request and response body -- your document text -- at DEBUG level on the
``typesafe_sdk`` logger, and the method, URL, status and timing of each request and retry at
INFO. doc2mark drops the SDK's DEBUG records, so ``doc2mark -v`` shows the INFO lines but does
not write page text to the log, and leaves the logger's level alone, so a default run (WARNING)
prints none of them. To see the wire log, ask for it with ``TYPESAFE_LOG_LEVEL=debug`` or set the
``typesafe_sdk`` logger's level yourself.

Privacy
-------

With the judge enabled, text from your documents leaves your machine and is sent to
TypeSafe (``api.typesafe.ai``): up to 1,500 characters of each PDF page whose text layer is
judged (all of a short page, else its beginning, middle and end), the repeated header/footer
lines that are judged, and short OCR answers. Per TypeSafe's documentation, Jev is not
trained on customer requests or responses and a Data Processing Agreement applies; **zero data retention is offered only to enterprise
customers** (see https://docs.typesafe.ai/legal). Do not enable the judge for documents your
agreement with TypeSafe does not cover. doc2mark never logs the API key.

Cost and latency
----------------

Jev is priced per input token (``$0.042`` per million, output free). A question costs about
300 tokens of fixed overhead plus its state; on the labelled sets a page-text question
averaged 687 input tokens (``$0.000029``), a header-line question 573 (``$0.000024``) and an
OCR-answer question 446 (``$0.000019``). Measured latency per request from a laptop: p50
about 210 ms, p95 270-300 ms (one run's header-line questions reached 530 ms). Documents
converted with the judge record what it did in ``metadata.extra["judge"]`` (``asked``,
``cached``, ``fresh``, ``failed``, ``input_tokens``, ``cost_usd``).

Measured on whole documents (fresh verdict cache, OCR on, pages asked concurrently):

.. list-table::
   :header-rows: 1

   * - document
     - questions
     - input tokens
     - cost
     - added time
   * - 12-page text report
     - 13 (12 pages, 1 header line)
     - 6,790
     - $0.00029
     - +1.3 s
   * - 8-slide deck with a brand line
     - 10 (8 pages, 2 header lines)
     - 5,051
     - $0.00021
     - +1.2 s
   * - 3-page letter with a letterhead
     - 3 (pages; the letterhead is not asked about)
     - 1,679
     - $0.00007
     - +0.6 s
   * - 30-page Traditional-Chinese company deck (image route)
     - 2 (header lines)
     - 1,135
     - $0.00005
     - +0.6 s

A second conversion of the same document is answered from the verdict cache: no request, no
added time.

How well it works
-----------------

The labelled sets are in ``tests/data/judge`` (see its ``README.md`` for sources and
labelling); ``eval/judge_eval.py`` decides every item with the rule alone and with the rule
plus the judge, exactly as the pipeline combines them. Three sets: TRAIN (the only set the
legibility and boilerplate thresholds were calibrated on), TEST (held out: split from TRAIN by
family, every variant of one template, base text or document on one side, near-duplicates kept
together) and EXTERNAL (60 items written by the reviewer of this add-on, never used for
calibration). The non-content thresholds (act from 0.95, flag from 0.90) are a fixed policy,
set above the overlap of refusals and real answers. The positive class is the hook's action:
the page is illegible (OCR'd), the repeated line is thinned to one copy, the answer is no
content.

.. list-table::
   :header-rows: 1

   * - hook, set (items)
     - rule only: accuracy / precision / recall
     - rule + Jev: accuracy / precision / recall
   * - legibility, TRAIN (52)
     - 59.6 % / 88.9 % / 28.6 %
     - 98.1 % / 96.6 % / 100 %
   * - legibility, TEST (46)
     - 67.4 % / 100 % / 37.5 %
     - 100 % / 100 % / 100 %
   * - legibility, EXTERNAL (16)
     - 68.8 % / 100 % / 16.7 %
     - 100 % / 100 % / 100 %
   * - boilerplate, TRAIN (42)
     - 52.4 % / n/a / 0 %
     - 95.2 % / 100 % / 90.0 %
   * - boilerplate, TEST (42)
     - 50.0 % / n/a / 0 %
     - 95.2 % / 100 % / 90.5 %
   * - boilerplate, EXTERNAL (3 asked of 20)
     - 66.7 % / n/a / 0 %
     - 100 % / 100 % / 100 %
   * - non_content, TRAIN (91)
     - 73.6 % / 100 % / 45.5 %
     - 95.6 % / 97.6 % / 93.2 %
   * - non_content, TEST (90)
     - 76.7 % / 100 % / 52.3 %
     - 97.8 % / 97.7 % / 97.7 %
   * - non_content, EXTERNAL (24)
     - 62.5 % / n/a / 0 %
     - 83.3 % / 100 % / 55.6 %

Thresholds: legibility illegible below 0.8, boilerplate chrome from 0.7 (both calibrated on
TRAIN), non_content no content from 0.95 and flagged from 0.90. Boilerplate is scored on the
questions the pipeline asks (a running header's first copy is decided by the rule: 17 of the
20 external boilerplate items, and 50 designed lines of the generated documents, are such
lines, and the rule already keeps one copy of each). In output terms, on TEST the judge took
62 repeated copies out, none of a line that needed them, and left 2 chrome copies in.

On a real Traditional-Chinese company deck read in place (never committed), the brand line
beside every slide title goes from 29 copies to 1 (the cover's), and the logo text beside it
is asked about too; garbled variants of its page texts are all caught (3 of 6 without the
judge).

Margins: legibility's garbage pages score at most 0.77 and its legible pages at least 0.87 on
TRAIN (0.89 on EXTERNAL, a lorem-ipsum page), around the 0.8 threshold; a false alarm costs
one OCR pass of a legible page. Boilerplate's content lines score up to 0.68 on TRAIN against
0.7; a false chrome verdict thins a content line to one copy. Refusals and real answers
overlap between 0.87 and 0.96, which is why only 0.95 and above acts: on TEST one real answer
("no results" from a search page) is still called no content, and on EXTERNAL 4 of 9 refusals
are kept (3 of them flagged ``non_content_suspected``). The judge cannot undo a false alarm of
the deterministic garbage detector (a legible spec table with 17 replacement characters is
still OCR'd): it is asked only about layers the detector keeps. The thresholds belong to
``jev-1.13.0``: re-run ``eval/judge_eval.py --calibrate`` before moving the pin.

Current TypeSafe limits for ``jev-1.13``: 100K tokens and 40 requests per second, 32K tokens
of state per question (see https://docs.typesafe.ai/models); English is the model's primary
language, CJK is handled with lower accuracy.
