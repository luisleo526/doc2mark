"""Verdict cache for the optional judge.

A verdict is keyed by ``(model id, question version, state)``, hashed the way
``doc2mark.ocr.cache.build_ocr_cache_key`` hashes an OCR request: a sha256 of the
canonical JSON of the key parts plus a schema version. The same page text, line or OCR
answer asked the same question of the same model gets the same verdict on every run
(the model itself is calibrated but not bit-exact, about +-0.04 near 0.5), and a re-run
costs no request. Failures are never cached: the next run asks again.

``DiskVerdictCache`` keeps one small JSON file per verdict under
``<directory>/<key[:2]>/<key>.json``, written atomically, with an in-memory layer in
front; ``MemoryVerdictCache`` keeps them for the life of the process. Neither raises:
a cache that cannot read or write behaves as a miss.
"""

import hashlib
import json
import logging
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

JUDGE_CACHE_SCHEMA = "judge-verdict-v1"
#: Environment variable naming the verdict cache directory.
CACHE_DIR_ENV = "DOC2MARK_JUDGE_CACHE"


@dataclass(frozen=True)
class Verdict:
    """One answered question: the probability, the model that answered, and what the
    original request cost (``latency_ms`` and ``input_tokens`` of the fresh call)."""

    probability: float
    model: str
    latency_ms: float = 0.0
    input_tokens: int = 0
    created_at: float = 0.0


def verdict_key(model: str, question_version: str, state: Any) -> str:
    """Stable cache key of one question about one state."""
    payload = {"schema": JUDGE_CACHE_SCHEMA, "model": model, "question": question_version, "state": state}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def default_cache_dir() -> Path:
    """``$DOC2MARK_JUDGE_CACHE``, else ``$XDG_CACHE_HOME/doc2mark/judge``, else ``~/.cache/doc2mark/judge``."""
    explicit = os.environ.get(CACHE_DIR_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CACHE_HOME", "").strip()
    return (Path(base).expanduser() if base else Path.home() / ".cache") / "doc2mark" / "judge"


class VerdictCache(ABC):
    """Where verdicts are kept (see the module docstring)."""

    @abstractmethod
    def get(self, key: str) -> Optional[Verdict]:
        """The verdict for ``key``, or None."""

    @abstractmethod
    def set(self, key: str, verdict: Verdict) -> None:
        """Keep ``verdict`` for ``key``."""

    def stats(self) -> Dict[str, Any]:
        return {}


class MemoryVerdictCache(VerdictCache):
    """Verdicts for the life of the process (thread-safe)."""

    def __init__(self):
        self._items: Dict[str, Verdict] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Verdict]:
        with self._lock:
            return self._items.get(key)

    def set(self, key: str, verdict: Verdict) -> None:
        with self._lock:
            self._items[key] = verdict

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"backend": "memory", "entries": len(self._items)}


def _verdict_from_payload(payload: Any) -> Optional[Verdict]:
    if not isinstance(payload, dict) or payload.get("schema") != JUDGE_CACHE_SCHEMA:
        return None
    try:
        verdict = Verdict(
            probability=float(payload["probability"]),
            model=str(payload["model"]),
            latency_ms=float(payload.get("latency_ms", 0.0)),
            input_tokens=int(payload.get("input_tokens", 0)),
            created_at=float(payload.get("created_at", 0.0)),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return verdict if 0.0 <= verdict.probability <= 1.0 else None


class DiskVerdictCache(VerdictCache):
    """One JSON file per verdict under ``directory`` (see the module docstring)."""

    def __init__(self, directory: Union[str, Path, None] = None):
        self.directory = Path(directory).expanduser() if directory else default_cache_dir()
        self._memory = MemoryVerdictCache()
        self._lock = threading.Lock()
        self._counts = {"hits": 0, "misses": 0, "writes": 0, "errors": 0}

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def _count(self, name: str) -> None:
        with self._lock:
            self._counts[name] += 1

    def get(self, key: str) -> Optional[Verdict]:
        verdict = self._memory.get(key)
        if verdict is None:
            try:
                with open(self._path(key), "r", encoding="utf-8") as handle:
                    verdict = _verdict_from_payload(json.load(handle))
            except FileNotFoundError:
                verdict = None
            except (OSError, ValueError) as exc:
                logger.debug(f"judge cache: unreadable entry {key[:12]}: {exc!r}")
                self._count("errors")
                verdict = None
            if verdict is not None:
                self._memory.set(key, verdict)
        self._count("hits" if verdict is not None else "misses")
        return verdict

    def set(self, key: str, verdict: Verdict) -> None:
        self._memory.set(key, verdict)
        path = self._path(key)
        payload = {"schema": JUDGE_CACHE_SCHEMA, **asdict(verdict)}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, sort_keys=True)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.debug(f"judge cache: cannot write {path}: {exc!r}")
            self._count("errors")
            return
        self._count("writes")

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"backend": "disk", "directory": str(self.directory), **self._counts}
