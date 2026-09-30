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
   substituted for others. No character rule sees these. The judge gets the page text (a
   sample of at most 1,500 characters) of layers the deterministic garbage detector did not
   flag. It is consulted only when OCR is on (``--ocr <provider> --ocr-images``): a page it
   rates illegible is OCR'd from its render instead of emitting the garbage. See
   :func:`doc2mark.core.strategy.judge_text_layer`.

``boilerplate_judge(line_text, context) -> Optional[float]``
   *Is this repeated top/bottom line page chrome?* Running headers and footers are removed
   only on strong evidence; a line that repeats but is attached to the page's content, or the
   first copy of a running header, is kept. The judge is asked about exactly those lines, with
   where and how often they repeat. A line it calls chrome is left out on every page: the
   brand line printed beside every slide title of a deck, logo text, a confidentiality
   marking. Titles, per-page labels (``Lesson 3``), unit notes and disclaimers stay. See
   :class:`doc2mark.pipelines.pymupdf_advanced_pipeline.PDFLoader`.

``non_content_judge(ocr_text) -> Optional[float]``
   *Is this OCR answer only a refusal, an error or a "no readable text" statement?* The
   deterministic check fires only when the whole answer is a first-person refusal; an answer
   such as "Unable to process the image. Please provide a clearer scan." reads the same as a
   support page and is kept. The judge gets short answers (at most 600 characters) the
   patterns keep; one it calls no content is re-read with the free-form recovery and never
   indexed as page text. See :mod:`doc2mark.ocr.refusal`.

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
                         timeout=3.0, max_workers=4)
   loader = UnifiedDocumentLoader(ocr_provider="openai", judge=judge)

When the judge cannot answer
----------------------------

The add-on is optional in every sense: without the ``typesafe`` extra the SDK is never
imported, and without a judge nothing is sent anywhere. When the judge is enabled but cannot
answer, every hook returns ``None`` and the rules decide:

- extra not installed, or no ``TYPESAFE_API_KEY``: one warning (``TypeSafe judge
  unavailable: ...``) and the same output as ``--judge none``;
- the service rejects the key (401/403): the judge switches itself off for the run;
- network errors, timeouts, rate limits (429/529) after the SDK's retries, invalid answers:
  that question is decided by the rule, and the document logs one warning saying how many
  requests got no answer. After three failures in a row the judge pauses for a minute, so an
  outage costs a few timeouts, not one per question.

A conversion never fails because of the judge.

Model, questions and determinism
--------------------------------

Every question is a TypeSafe *Noul* over a small named JSON state -- ``page_text``; the
``line`` with ``where`` and ``font`` in words (numbers become words: the model reads
semantics better than arithmetic); ``ocr_answer`` -- asked of the pinned model version
``jev-1.13.0`` (never the moving ``jev-latest`` alias: the thresholds belong to a version).
The question texts are versioned (``legibility-v1``, ``boilerplate-v1``,
``non-content-v1``, in :mod:`doc2mark.judge.questions`).

Jev is calibrated but not bit-exact (about +-0.04 run to run near 0.5), so every verdict is
cached on disk, keyed by model id, question version and a hash of the state:
``$DOC2MARK_JUDGE_CACHE``, else ``~/.cache/doc2mark/judge`` (``cache_dir=False`` keeps them in
memory). A re-run gives the same output and sends nothing. The OCR and document caches key
on the judge's model, question version and threshold, so a result decided by another judge
setup is not replayed.

Pages of one PDF are asked about concurrently (at most ``max_workers`` requests in flight),
as are the OCR answers of one batch; each HTTP attempt times out after ``timeout`` seconds
and the SDK retries with its default policy.

Privacy
-------

With the judge enabled, text from your documents leaves your machine and is sent to
TypeSafe (``api.typesafe.ai``): a sample of up to 1,500 characters of each PDF page whose
text layer is judged, the repeated header/footer lines that are judged, and short OCR
answers. Per TypeSafe's documentation, Jev is not trained on customer requests or responses
and a Data Processing Agreement applies; **zero data retention is offered only to enterprise
customers** (see https://docs.typesafe.ai/legal). Do not enable the judge for documents your
agreement with TypeSafe does not cover. doc2mark never logs the API key.

Cost and latency
----------------

Jev is priced per input token (``$0.042`` per million, output free). A question costs about
300 tokens of fixed overhead plus its state; on the labelled sets a page-text question
averaged 691 input tokens (``$0.000029``), a header-line question 552 (``$0.000023``) and an
OCR-answer question 447 (``$0.000019``). Measured latency per request from a laptop: p50
about 210 ms, p95 260-290 ms. Documents converted with the judge record what it did in
``metadata.extra["judge"]`` (``asked``, ``cached``, ``fresh``, ``failed``, ``input_tokens``,
``cost_usd``).

Measured on whole documents (fresh cache, OCR on, pages asked concurrently):

.. list-table::
   :header-rows: 1

   * - document
     - questions
     - input tokens
     - cost
     - added time
   * - 12-page text report
     - 13 (12 pages, 1 header line)
     - 6,554
     - $0.00028
     - +1.0 s
   * - 8-slide deck with a brand line
     - 10 (8 pages, 2 header lines)
     - 4,867
     - $0.00020
     - +0.9 s
   * - 30-page Traditional-Chinese company deck (image route)
     - 2 (header lines)
     - 1,095
     - $0.00005
     - +0.2 s

A second conversion of the same document is answered from the verdict cache: no request, no
added time.

How well it works
-----------------

The labelled sets are in ``tests/data/judge`` (see its ``README.md`` for sources and
labelling); ``eval/judge_eval.py`` decides every item with the rule alone and with the rule
plus the judge, exactly as the pipeline combines them. Thresholds were calibrated on the
TRAIN split; the numbers below are on the held-out TEST split. The positive class is the
hook's action (the page is illegible, the line is chrome, the answer is no content).

TEST split (threshold as calibrated on TRAIN):

.. list-table::
   :header-rows: 1

   * - hook (items; judge asked)
     - rule only: accuracy / precision / recall
     - rule + Jev: accuracy / precision / recall
     - threshold
   * - legibility (46; 39)
     - 63.0 % / 100 % / 29.2 %
     - 100 % / 100 % / 100 %
     - illegible below 0.8
   * - boilerplate (45; 45)
     - 40.0 % / n/a / 0 %
     - 95.6 % / 100 % / 92.6 %
     - chrome from 0.7
   * - non_content (93; 71)
     - 74.2 % / 100 % / 47.8 %
     - 97.8 % / 95.8 % / 100 %
     - no content from 0.9

Pages the route OCRs for another reason (searchable scans) are not scored: neither the
detector nor the judge decides them. A slice built at run time from a real Traditional-Chinese
company deck (read in place, never committed): boilerplate, the brand line and the logo text
beside every slide title, 0 of 2 -> 2 of 2; legibility, garbled variants of its page texts,
3 of 6 -> 6 of 6 (the deck's own pages take the image route, so the judge is never asked about
them).

What the judge still gets wrong on TEST: the logo text of two CJK decks is kept (the safe
direction: the line stays); two content answers are called no content, a German chat line
asking someone to resend a photo and an upload-limits notice (both re-read by the free-form
recovery, then left out when it reads the same). The judge cannot undo a false alarm of the
deterministic garbage detector (a legible spec table with 17 replacement characters is still
OCR'd): it is asked only about layers the detector keeps. The thresholds are calibrated for
``jev-1.13.0``: re-run ``eval/judge_eval.py --calibrate`` before moving the pin.

Current TypeSafe limits for ``jev-1.13``: 100K tokens and 40 requests per second, 32K tokens
of state per question (see https://docs.typesafe.ai/models); English is the model's primary
language, CJK is handled with lower accuracy.
