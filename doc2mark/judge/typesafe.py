"""TypeSafe (Jev) behind doc2mark's three judge hooks.

``TypeSafeJudge`` answers the hooks the pipelines consult only where their deterministic
rules cannot decide:

- ``legibility_judge(page_text)``: is a PDF page's extracted text layer legible
  (``doc2mark.core.strategy.judge_text_layer``)?
- ``boilerplate_judge(line_text, context)``: is a repeated top/bottom line page chrome
  (``PDFLoader._detect_page_chrome``)?
- ``non_content_judge(ocr_text)``: is an OCR answer only a refusal or a "no readable
  text" statement (``doc2mark.ocr.refusal.non_content_reason``)?

Each is one TypeSafe Noul (``doc2mark.judge.questions``) asked of a pinned model. It is
an optional add-on (``pip install 'doc2mark[typesafe]'``, key in ``TYPESAFE_API_KEY``):
the SDK is imported only when a ``TypeSafeJudge`` is created, and a hook that cannot get
an answer -- extra not installed, no key, network error, timeout, rate limit, invalid
answer -- returns None, so the deterministic rule decides exactly as without a judge.
A hook never raises. Verdicts are cached on disk (``doc2mark.judge.cache``), so a re-run
is deterministic and costs no request.
"""

import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from doc2mark.judge import questions as Q
from doc2mark.judge.cache import DiskVerdictCache, MemoryVerdictCache, Verdict, VerdictCache, verdict_key

logger = logging.getLogger(__name__)

#: Environment variable the SDK reads the API key from.
API_KEY_ENV = "TYPESAFE_API_KEY"
#: Environment variable choosing the hooks ``TypeSafeJudge()`` answers (comma-separated).
HOOKS_ENV = "DOC2MARK_JUDGE_HOOKS"
#: The SDK's logger and the environment variable that sets its level (see cap_sdk_logging).
SDK_LOGGER = "typesafe_sdk"
SDK_LOG_LEVEL_ENV = "TYPESAFE_LOG_LEVEL"

HOOKS = ("legibility", "boilerplate", "non_content")
#: The hooks answered by default: the ones that beat the deterministic rules on the
#: held-out TEST split (see docs/judge.rst and eval/judge_eval.py).
DEFAULT_HOOKS = ("legibility", "boilerplate", "non_content")

#: Seconds per HTTP attempt, and the retry policy: one retry within a RETRY_BUDGET second budget
#: per question, so a question that cannot be answered costs at most about that long.
DEFAULT_TIMEOUT = 2.0
MAX_RETRIES = 1
RETRY_BUDGET = 4.0
#: Questions in flight at once (per judge).
DEFAULT_MAX_WORKERS = 4
#: After this many consecutive failed requests the judge stops asking for BREAKER_COOLDOWN
#: seconds (an outage costs a few timeouts, not one per question).
BREAKER_FAILURES = 3
BREAKER_COOLDOWN = 60.0
# HTTP statuses that reject one request (its state), not the service.
_REQUEST_ERRORS = {400, 404, 413, 422}
_KEY_ERRORS = {401, 403}


def _pipeline_threshold(hook: str) -> float:
    """The fixed probability at which the pipeline acts on a hook's answer."""
    if hook == "legibility":
        from doc2mark.core.strategy import LEGIBILITY_JUDGE_THRESHOLD
        return LEGIBILITY_JUDGE_THRESHOLD
    if hook == "boilerplate":
        from doc2mark.pipelines.pymupdf_advanced_pipeline import _CHROME_JUDGE_THRESHOLD
        return _CHROME_JUDGE_THRESHOLD
    from doc2mark.ocr.refusal import JUDGE_THRESHOLD
    return JUDGE_THRESHOLD


def _pipeline_suspect_threshold(hook: str) -> Optional[float]:
    """The fixed probability from which the pipeline flags a hook's case without acting, if any."""
    if hook == "non_content":
        from doc2mark.ocr.refusal import SUSPECT_THRESHOLD
        return SUSPECT_THRESHOLD
    return None


def _rescale(probability: float, anchors: Sequence[Tuple[float, float]]) -> float:
    """Monotone piecewise-linear map of [0, 1] onto itself through ``anchors``, ``(raw, target)``
    pairs increasing in both: each pipeline threshold ``target`` then falls exactly at its
    calibrated ``raw`` one (a probability below ``raw`` maps strictly below ``target``, ``raw``
    and above to ``target`` or above)."""
    points = [(0.0, 0.0), *anchors, (1.0, 1.0)]
    for (raw0, target0), (raw1, target1) in zip(points, points[1:]):
        if probability == raw1:
            return target1
        if probability < raw1:
            value = target0 + (target1 - target0) * ((probability - raw0) / (raw1 - raw0))
            return min(max(value, target0), math.nextafter(target1, -math.inf))
    return 1.0


def cap_sdk_logging() -> None:
    """The SDK logs every request and response body -- document text -- at DEBUG. Cap its
    logger at INFO (method, URL, status, timing) unless ``TYPESAFE_LOG_LEVEL`` asks for a
    level or the ``typesafe_sdk`` logger was configured explicitly, so ``doc2mark -v`` does
    not write page text to the log."""
    sdk_logger = logging.getLogger(SDK_LOGGER)
    if sdk_logger.level == logging.NOTSET and not os.environ.get(SDK_LOG_LEVEL_ENV, "").strip():
        sdk_logger.setLevel(logging.INFO)


def resolve_hooks(hooks: Union[None, str, Iterable[str]] = None) -> Tuple[str, ...]:
    """The hooks to answer: ``hooks``, else ``$DOC2MARK_JUDGE_HOOKS``, else DEFAULT_HOOKS."""
    if hooks is None:
        hooks = os.environ.get(HOOKS_ENV, "").strip() or DEFAULT_HOOKS
    if isinstance(hooks, str):
        hooks = [part for part in hooks.replace(" ", "").split(",") if part]
    hooks = tuple(dict.fromkeys(h.strip().lower().replace("-", "_") for h in hooks))
    unknown = [h for h in hooks if h not in HOOKS]
    if unknown:
        raise ValueError(f"Unknown judge hook(s) {unknown}; expected any of {list(HOOKS)}")
    return hooks


@dataclass
class _Stats:
    asked: int = 0         # questions the hooks asked
    cached: int = 0        # ... answered from the verdict cache
    fresh: int = 0         # requests answered by the service
    failed: int = 0        # questions the hooks asked that got no usable answer (once each)
    input_tokens: int = 0
    latencies_ms: List[float] = field(default_factory=list)
    last_error: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        return {f.name: (list(getattr(self, f.name)) if f.name == "latencies_ms" else getattr(self, f.name))
                for f in fields(self)}


def _percentile(values: Sequence[float], share: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(share * (len(ordered) - 1)))))]


class _Hook:
    """One hook of a :class:`TypeSafeJudge`: a callable with the hook's contract.

    ``version`` / ``cache_key`` name the model, question version and thresholds, so the
    OCR and document caches never replay a result decided by another judge setup;
    ``available`` is False when the judge cannot answer at all (the caches then treat the
    run as one without a judge, which is what its output is).
    """

    def __init__(self, judge: "TypeSafeJudge", hook: str):
        self.judge = judge
        self.hook = hook
        self.question = Q.QUESTIONS[hook]
        self.raw_threshold = float(judge.thresholds[hook])
        self.pipeline_threshold = _pipeline_threshold(hook)
        anchors = [(self.raw_threshold, self.pipeline_threshold)]
        suspect, pipeline_suspect = judge.suspect_thresholds.get(hook), _pipeline_suspect_threshold(hook)
        if suspect is not None and pipeline_suspect is not None and suspect < self.raw_threshold:
            anchors.insert(0, (float(suspect), pipeline_suspect))
        self.anchors = tuple(anchors)
        self.version = (f"typesafe:{judge.model}:{self.question.version}:t{self.raw_threshold:g}"
                        + (f":s{anchors[0][0]:g}" if len(anchors) > 1 else ""))
        self.cache_key = self.version
        self._build = Q.STATE_BUILDERS[hook]

    def __repr__(self) -> str:
        return f"<TypeSafe {self.hook} judge {self.version}>"

    @property
    def available(self) -> bool:
        return not self.judge.unavailable

    def __call__(self, *args) -> Optional[float]:
        try:
            if self.judge.unavailable:
                return None
            state = self._build(*args)
            self.judge._count_asked()
            verdict = self.judge._ask(self.question, state, count=True)
            if verdict is None:
                return None
            return _rescale(verdict.probability, self.anchors)
        except Exception as exc:  # a hook never raises: the deterministic rule decides
            logger.debug(f"TypeSafe {self.hook} judge failed: {type(exc).__name__}")
            return None

    def prefetch(self, items: Iterable[Any]) -> None:
        """Ask about every item at once (bounded concurrency), so the calls that follow are
        answered from the cache. ``items`` are the hook's arguments: a text for the
        legibility and non-content hooks, a ``(line_text, context)`` pair for boilerplate."""
        try:
            args = [tuple(item) if self.hook == "boilerplate" else (item,) for item in items]
            self.judge._prefetch(self.question, [self._build(*a) for a in args])
        except Exception as exc:
            logger.debug(f"TypeSafe {self.hook} prefetch failed: {type(exc).__name__}")


class TypeSafeJudge:
    """doc2mark's judge hooks answered by TypeSafe's Jev (see the module docstring).

    Args:
        api_key: TypeSafe API key; default ``$TYPESAFE_API_KEY`` (never logged).
        model: Model version to ask (default: the pinned ``questions.MODEL``). The
            thresholds are calibrated for the pinned version.
        hooks: Hooks to answer (``"legibility"``, ``"boilerplate"``, ``"non_content"``);
            default ``$DOC2MARK_JUDGE_HOOKS`` or ``DEFAULT_HOOKS``. A hook left out is None.
        cache_dir: Verdict cache directory (default ``$DOC2MARK_JUDGE_CACHE`` or
            ``~/.cache/doc2mark/judge``); ``False`` keeps verdicts in memory only.
        cache: A ``VerdictCache`` to use instead (overrides ``cache_dir``).
        timeout: Seconds per HTTP attempt; a question gets one retry within ``RETRY_BUDGET``
            seconds, so one that cannot be answered costs at most about that long.
        max_workers: Questions in flight at once.
        thresholds: Override the calibrated per-hook thresholds (``questions.RAW_THRESHOLDS``).
        suspect_thresholds: Override the flag-only thresholds (``questions.RAW_SUSPECT_THRESHOLDS``).
        client: A ready ``typesafe_sdk.TypeSafeClient`` (or a stand-in with its
            ``system_one``); the SDK is not imported then.

    Attributes ``legibility_judge``, ``boilerplate_judge`` and ``non_content_judge`` are
    the hook callables (None for a hook not answered); pass the judge itself to
    ``UnifiedDocumentLoader(judge=...)`` to wire them all.
    """

    name = "typesafe"

    def __init__(self, *, api_key: Optional[str] = None, model: str = Q.MODEL,
                 hooks: Union[None, str, Iterable[str]] = None, cache_dir: Union[None, str, os.PathLike, bool] = None,
                 cache: Optional[VerdictCache] = None, timeout: float = DEFAULT_TIMEOUT,
                 max_workers: int = DEFAULT_MAX_WORKERS, thresholds: Optional[Mapping[str, float]] = None,
                 suspect_thresholds: Optional[Mapping[str, float]] = None, client: Any = None):
        self.model = model
        self.hooks = resolve_hooks(hooks)
        self.timeout = float(timeout)
        self.max_workers = max(1, int(max_workers))
        self.thresholds: Dict[str, float] = {**Q.RAW_THRESHOLDS, **dict(thresholds or {})}
        self.suspect_thresholds: Dict[str, float] = {**Q.RAW_SUSPECT_THRESHOLDS, **dict(suspect_thresholds or {})}
        for hook, value in (*self.thresholds.items(), *self.suspect_thresholds.items()):
            if not 0.0 < float(value) < 1.0:
                raise ValueError(f"threshold for {hook} must be strictly between 0 and 1, got {value!r}")
        if cache is not None:
            self.cache = cache
        elif cache_dir is False:
            self.cache = MemoryVerdictCache()
        else:
            self.cache = DiskVerdictCache(cache_dir if cache_dir not in (None, True) else None)
        self._api_key = api_key
        self._client = client
        self._client_lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(self.max_workers)
        self._lock = threading.Lock()
        self._stats = _Stats()
        self._consecutive_failures = 0
        self._paused_until = 0.0
        # Questions a prefetch could not get answered: the hook call that follows reports the
        # failure (once) instead of asking again, until BREAKER_COOLDOWN has passed.
        self._unanswered: Dict[str, float] = {}
        #: Why the judge cannot answer at all (None when it can): set at construction for a
        #: missing extra or key, and later when the service rejects the key.
        self.unavailable: Optional[str] = None if client is not None else self._check_available()
        if self.unavailable:
            logger.warning(f"TypeSafe judge unavailable: {self.unavailable}; "
                           f"the deterministic rules decide (same output as without a judge)")
        self.legibility_judge = _Hook(self, "legibility") if "legibility" in self.hooks else None
        self.boilerplate_judge = _Hook(self, "boilerplate") if "boilerplate" in self.hooks else None
        self.non_content_judge = _Hook(self, "non_content") if "non_content" in self.hooks else None

    def __repr__(self) -> str:
        return f"TypeSafeJudge(model={self.model!r}, hooks={self.hooks!r})"

    # --- availability and the client ---------------------------------------

    def _check_available(self) -> Optional[str]:
        try:
            import typesafe_sdk  # noqa: F401  (the optional extra)
        except ImportError:
            return "typesafe-sdk is not installed (pip install 'doc2mark[typesafe]')"
        key = self._api_key if self._api_key is not None else os.environ.get(API_KEY_ENV, "")
        if not str(key).strip():
            return f"{API_KEY_ENV} is not set"
        return None

    def _get_client(self) -> Any:
        with self._client_lock:
            if self._client is None and not self.unavailable:
                try:
                    from typesafe_sdk import RetryPolicy, TypeSafeClient
                    cap_sdk_logging()
                    options: Dict[str, Any] = {
                        "model": self.model,
                        "timeout": self.timeout,
                        "retry": RetryPolicy(max_retries=MAX_RETRIES, timeout=RETRY_BUDGET),
                    }
                    if self._api_key is not None:
                        options["api_key"] = self._api_key
                    self._client = TypeSafeClient(**options)
                except Exception as exc:  # a missing or malformed key raises TypeSafeError here
                    self.unavailable = f"cannot create the TypeSafe client ({type(exc).__name__})"
                    with self._lock:
                        self._stats.last_error = self.unavailable
            return self._client

    def close(self) -> None:
        """Release the HTTP client (a new one is created on the next question)."""
        with self._client_lock:
            client, self._client = self._client, None
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    # --- asking -------------------------------------------------------------

    def _count_asked(self) -> None:
        with self._lock:
            self._stats.asked += 1

    def probability(self, hook: str, *args) -> Optional[float]:
        """Jev's raw probability for one hook's question (``args`` as the hook takes them),
        without the hook's rescaling; None when it cannot answer."""
        question = Q.QUESTIONS[hook]
        verdict = self._ask(question, Q.STATE_BUILDERS[hook](*args), count=True)
        return verdict.probability if verdict is not None else None

    def verdict(self, hook: str, *args) -> Optional[Verdict]:
        """The cached or fresh :class:`Verdict` for one hook's question (for evaluation)."""
        return self._ask(Q.QUESTIONS[hook], Q.STATE_BUILDERS[hook](*args), count=True)

    def _ask(self, question: Q.Question, state: Dict[str, Any], count: bool = False) -> Optional[Verdict]:
        """The verdict for one question, from the cache or the service. A question that gets
        no answer counts as one failure when ``count`` (a hook's own question), never for a
        prefetch; the error behind it stays in ``last_error``."""
        key = verdict_key(self.model, question.version, state)
        cached = self.cache.get(key)
        if cached is not None:
            with self._lock:
                self._stats.cached += 1
            return cached
        now = time.monotonic()
        failed_at = self._unanswered.get(key)
        if (self.unavailable or now < self._paused_until
                or (failed_at is not None and now - failed_at < BREAKER_COOLDOWN)):
            return self._no_answer(key, count)
        client = self._get_client()
        if client is None:
            return self._no_answer(key, count)
        with self._slots:
            started = time.perf_counter()
            try:
                response = client.system_one(state, {question.hook: question.payload()}, model=self.model)
            except Exception as exc:
                self._request_failed(question, exc)
                return self._no_answer(key, count, remember=True)
            latency_ms = (time.perf_counter() - started) * 1000.0
        probability = self._noul(response, question.hook)
        if probability is None:
            with self._lock:
                self._stats.last_error = "invalid answer"
            logger.debug(f"TypeSafe {question.hook} judge: the answer carries no probability")
            return self._no_answer(key, count, remember=True)
        usage = getattr(response, "usage", None)
        tokens = getattr(usage, "input_tokens", 0) if usage is not None else 0
        verdict = Verdict(
            probability=probability,
            model=str(getattr(response, "model", None) or self.model),
            latency_ms=round(latency_ms, 1),
            input_tokens=int(tokens) if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) else 0,
            created_at=time.time(),
        )
        self.cache.set(key, verdict)
        with self._lock:
            self._unanswered.pop(key, None)
            self._consecutive_failures = 0
            self._stats.fresh += 1
            self._stats.input_tokens += verdict.input_tokens
            self._stats.latencies_ms.append(verdict.latency_ms)
        return verdict

    def _prefetch(self, question: Q.Question, states: Sequence[Dict[str, Any]]) -> None:
        if self.unavailable:
            return
        pending, seen = [], set()
        for state in states:
            key = verdict_key(self.model, question.version, state)
            if key not in seen and self.cache.get(key) is None:
                seen.add(key)
                pending.append(state)
        if len(pending) < 2:  # one question is asked when the hook needs it
            return
        workers = min(self.max_workers, len(pending))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="doc2mark-judge") as pool:
            list(pool.map(lambda state: self._ask(question, state, count=False), pending))

    @staticmethod
    def _noul(response: Any, name: str) -> Optional[float]:
        """The Noul answer ``name`` of a response, if it is a probability."""
        try:
            answers = getattr(response, "nouls", None)
            if answers is None:
                answers = getattr(response, "answers", None)
            answer = answers[name]
            value = answer.get("noul") if isinstance(answer, Mapping) else getattr(answer, "noul", None)
        except Exception:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value)
        return value if math.isfinite(value) and 0.0 <= value <= 1.0 else None

    def _no_answer(self, key: str, count: bool, remember: bool = False) -> None:
        """No verdict for ``key``: remember it when a request failed (see ``_unanswered``) and
        count it once, for a hook's own question."""
        with self._lock:
            if remember:
                self._unanswered[key] = time.monotonic()
            if count:
                self._stats.failed += 1
        return None

    def _request_failed(self, question: Q.Question, exc: Exception) -> None:
        status = getattr(exc, "status", None)
        status = status if isinstance(status, int) else None
        request_id = getattr(exc, "request_id", None)
        # Name and status only: an exception's message can carry the request body.
        reason = type(exc).__name__ + (f" (HTTP {status})" if status else "")
        logger.debug(f"TypeSafe {question.hook} judge: request failed: {reason}"
                     + (f", request {request_id}" if isinstance(request_id, str) else ""))
        with self._lock:
            self._stats.last_error = reason
            if status in _KEY_ERRORS or type(exc).__name__ in ("TypeSafeAuthenticationError",
                                                               "TypeSafePermissionDeniedError"):
                self.unavailable = f"TypeSafe rejected the API key ({reason})"
            elif status not in _REQUEST_ERRORS:
                self._consecutive_failures += 1
                if self._consecutive_failures >= BREAKER_FAILURES:
                    self._consecutive_failures = 0
                    self._paused_until = time.monotonic() + BREAKER_COOLDOWN

    # --- accounting ---------------------------------------------------------

    def begin_document(self) -> Dict[str, Any]:
        """Start counting one document's questions (see :meth:`end_document`)."""
        with self._lock:
            return {**self._stats.snapshot(), "unavailable": self.unavailable}

    def end_document(self, start: Mapping[str, Any]) -> Dict[str, Any]:
        """What the judge did since ``start`` (a :meth:`begin_document` snapshot): questions
        ``asked``, answered from the ``cached`` verdicts, ``fresh`` requests, ``failed`` ones,
        ``input_tokens``, ``cost_usd``, and ``last_error``."""
        with self._lock:
            now = self._stats.snapshot()
        delta: Dict[str, Any] = {name: now[name] - start.get(name, 0)
                                 for name in ("asked", "cached", "fresh", "failed", "input_tokens")}
        delta["cost_usd"] = round(delta["input_tokens"] * Q.PRICE_PER_INPUT_TOKEN, 8)
        delta["last_error"] = now["last_error"] if delta["failed"] else None
        delta["unavailable"] = self.unavailable
        return delta

    def usage(self) -> Dict[str, Any]:
        """Totals since the judge was created: requests, cache hits, failures, tokens, cost and
        the latency (p50/p95, ms) of the fresh requests."""
        with self._lock:
            stats = self._stats.snapshot()
        latencies = stats.pop("latencies_ms")
        stats["cost_usd"] = round(stats["input_tokens"] * Q.PRICE_PER_INPUT_TOKEN, 8)
        stats["latency_p50_ms"] = _percentile(latencies, 0.5)
        stats["latency_p95_ms"] = _percentile(latencies, 0.95)
        stats["model"] = self.model
        stats["cache"] = self.cache.stats()
        return stats

