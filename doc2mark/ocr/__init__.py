"""OCR providers for doc2mark."""

from typing import List, Optional, Union

from doc2mark.ocr.base import (
    OCRProvider,
    OCRResult,
    OCRConfig,
    BaseOCR,
    OCRFactory,
    Task,
)
from doc2mark.ocr.schema import (
    OCRPage,
    RawExtraction,
    Interpretation,
    Table,
    KeyValue,
)
from doc2mark.ocr.cache import (
    OCRCache,
    MemoryOCRCache,
    NoOpOCRCache,
    RedisOCRCache,
    CachedOCR,
    create_ocr_cache,
)

# Import and register providers
from doc2mark.ocr.openai import OpenAIOCR, VisionAgent
from doc2mark.ocr.tesseract import TesseractOCR

# Vertex AI provider (optional - requires langchain-google-genai)
try:
    from doc2mark.ocr.vertex_ai import VertexAIOCR, VertexAIVisionAgent
except ImportError:
    VertexAIOCR = None
    VertexAIVisionAgent = None


def _coerce_task(task: Union[str, Task]) -> Task:
    """Coerce an ergonomic string (e.g. ``"receipt"``) into a :class:`Task`.

    Accepts a :class:`Task` unchanged. Raises a clear ``ValueError`` listing the
    valid names for an unknown task.
    """
    if isinstance(task, Task):
        return task
    try:
        return Task(task)
    except ValueError:
        available = [t.value for t in Task]
        raise ValueError(f"Unknown OCR task: {task!r}. Available: {available}")


def _validate_detail(detail: str) -> str:
    """Validate that ``detail`` is one of the supported levels."""
    if detail not in ("raw", "full"):
        raise ValueError(
            f"Unknown OCR detail: {detail!r}. Available: ['raw', 'full']"
        )
    return detail


class OCR:
    """The single user-facing entry point for structured OCR.

    Wraps an OCR provider behind an ergonomic facade::

        from doc2mark.ocr import OCR
        ocr = OCR("openai")                 # creds from env
        results = ocr.read(images)          # List[bytes] -> List[OCRResult]
        results[0].text                     # rendered markdown (back-compat)
        results[0].document.raw.text        # structured verbatim transcription
        results[0].document.interpretation  # structured analysis (may be None)

    Ergonomic kwargs are coerced: ``OCR("openai", task="receipt")`` maps the
    string to :class:`Task.RECEIPT`, and an unknown ``task``/``detail`` raises a
    clear ``ValueError``.
    """

    def __init__(
            self,
            provider: Union[str, OCRProvider] = "openai",
            *,
            api_key: Optional[str] = None,
            **config_kwargs,
    ):
        # Coerce/validate ergonomic kwargs before building the config so a bad
        # value raises immediately with a clear message.
        if "task" in config_kwargs:
            config_kwargs["task"] = _coerce_task(config_kwargs["task"])
        if "detail" in config_kwargs:
            config_kwargs["detail"] = _validate_detail(config_kwargs["detail"])

        self.config = OCRConfig(**config_kwargs)
        self._provider = OCRFactory.create(provider, api_key=api_key, config=self.config)

    def read(
            self,
            images: List[bytes],
            *,
            task: Optional[Union[str, Task]] = None,
            tasks: Optional[List[Union[str, Task]]] = None,
            language: Optional[str] = None,
            structured: Optional[bool] = None,
            detail: Optional[str] = None,
    ) -> List[OCRResult]:
        """Run OCR over a batch of images, returning one :class:`OCRResult` each.

        All keyword arguments are per-call overrides of the config; ``None``
        preserves the configured behavior. ``tasks`` (per-image) wins over the
        single ``task``.
        """
        if task is not None:
            task = _coerce_task(task)
        if tasks is not None:
            tasks = [_coerce_task(t) for t in tasks]
        if detail is not None:
            detail = _validate_detail(detail)
        return self._provider.batch_process_images(
            images,
            task=task,
            tasks=tasks,
            language=language,
            structured=structured,
            detail=detail,
        )

    def read_one(self, image: bytes, **kw) -> OCRResult:
        """Convenience wrapper around :meth:`read` for a single image."""
        return self.read([image], **kw)[0]

__all__ = [
    # Facade
    'OCR',

    # Enums
    'OCRProvider',
    'Task',

    # Data classes
    'OCRResult',
    'OCRConfig',

    # Structured schema models
    'OCRPage',
    'RawExtraction',
    'Interpretation',
    'Table',
    'KeyValue',

    # Base classes
    'BaseOCR',
    'OCRCache',

    # Factory
    'OCRFactory',

    # Cache
    'MemoryOCRCache',
    'NoOpOCRCache',
    'RedisOCRCache',
    'CachedOCR',
    'create_ocr_cache',

    # Providers
    'OpenAIOCR',
    'TesseractOCR',
    'VertexAIOCR',

    # Vision agents
    'VisionAgent',
    'VertexAIVisionAgent',
]
