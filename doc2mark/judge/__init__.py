"""Optional judges for the decisions doc2mark's deterministic rules cannot make alone.

Three hooks, each consulted only where the rule is unsure, and each optional (None
keeps the rule's decision):

- ``legibility_judge(page_text) -> Optional[float]``: probability that a PDF page's
  extracted text layer is legible (``doc2mark.core.strategy.judge_text_layer``);
- ``boilerplate_judge(line_text, context) -> Optional[float]``: probability that a
  repeated top/bottom line is page chrome (``PDFLoader``);
- ``non_content_judge(ocr_text) -> Optional[float]``: probability that an OCR answer is
  only a refusal or a "no readable text" statement (``doc2mark.ocr.refusal``).

A *judge* is any object with some of these as attributes. ``resolve_judge`` turns the
``UnifiedDocumentLoader(judge=...)`` / ``--judge`` / ``$DOC2MARK_JUDGE`` setting into
one: ``"typesafe"`` is :class:`~doc2mark.judge.typesafe.TypeSafeJudge` (the
``doc2mark[typesafe]`` extra); ``"none"`` or nothing is no judge. See docs/judge.rst.
"""

import logging
import os
from typing import Any, Callable, Dict, Optional

from doc2mark.judge.typesafe import DEFAULT_HOOKS, HOOKS, TypeSafeJudge

logger = logging.getLogger(__name__)

#: Environment variable selecting the judge when none is passed (``typesafe`` or ``none``).
JUDGE_ENV = "DOC2MARK_JUDGE"
#: The hook attributes a judge object may have.
HOOK_ATTRIBUTES = ("legibility_judge", "boilerplate_judge", "non_content_judge")
_OFF = {"", "none", "off", "false", "0", "no"}


def resolve_judge(judge: Any = None) -> Any:
    """The judge object for a ``judge`` setting, or None.

    ``None`` reads ``$DOC2MARK_JUDGE`` (an unknown value there is ignored with a warning);
    ``"none"`` is no judge; ``"typesafe"`` creates a :class:`TypeSafeJudge`; any other
    object is returned as it is. An unknown name raises ``ValueError``.
    """
    from_env = judge is None
    if from_env:
        judge = os.environ.get(JUDGE_ENV, "").strip()
    if isinstance(judge, str):
        name = judge.strip().lower()
        if name in _OFF:
            return None
        if name == "typesafe":
            return TypeSafeJudge()
        if from_env:
            logger.warning(f"{JUDGE_ENV}={judge!r} is not a known judge ('typesafe' or 'none'); no judge is used")
            return None
        raise ValueError(f"Unknown judge {judge!r}: expected 'typesafe', 'none' or a judge object")
    return judge


def judge_hooks(judge: Any) -> Dict[str, Optional[Callable[..., Optional[float]]]]:
    """The hook callables of a judge object, by attribute name (None where it has none)."""
    hooks = {}
    for name in HOOK_ATTRIBUTES:
        hook = getattr(judge, name, None) if judge is not None else None
        hooks[name] = hook if callable(hook) else None
    return hooks


__all__ = ["TypeSafeJudge", "resolve_judge", "judge_hooks", "JUDGE_ENV", "HOOK_ATTRIBUTES", "HOOKS", "DEFAULT_HOOKS"]
