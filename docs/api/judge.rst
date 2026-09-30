Judge
=====

The optional judge (:doc:`/judge`) lives in ``doc2mark.judge``; the TypeSafe client is imported
only when a :class:`~doc2mark.judge.TypeSafeJudge` is created. The loader resolves its ``judge=``
argument with :func:`~doc2mark.judge.resolve_judge` and takes the hooks from it with
:func:`~doc2mark.judge.judge_hooks`.

A hook is a plain callable that returns the probability of "yes" (0 to 1) or ``None`` when it
cannot judge. ``None`` or an exception leaves the rule's decision in place, and so does a value
outside 0 to 1 for the first two hooks (``non_content_judge`` values are not range-checked):

- ``legibility_judge(page_text) -> float | None``: probability that a PDF page's text layer is
  legible (``UnifiedDocumentLoader(legibility_judge=...)``);
- ``boilerplate_judge(line_text, context) -> float | None``: probability that a repeated
  top/bottom line is page chrome (``UnifiedDocumentLoader(boilerplate_judge=...)``);
- ``non_content_judge(ocr_text) -> float | None``: probability that an OCR answer is only a
  refusal, an error or a "no readable text" statement (``OCRConfig(non_content_judge=...)``).

.. autoclass:: doc2mark.judge.TypeSafeJudge
   :members: probability, verdict, usage, close

   Its attributes ``legibility_judge``, ``boilerplate_judge`` and ``non_content_judge`` are the
   hook callables (``None`` for a hook it does not answer); ``unavailable`` says why it cannot
   answer at all (no extra, no key, key rejected), or is ``None``.

.. autofunction:: doc2mark.judge.resolve_judge

.. autofunction:: doc2mark.judge.judge_hooks
