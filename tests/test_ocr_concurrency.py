"""Configurable LLM-OCR batch concurrency (max_concurrency).

The vertex_ai / openai providers OCR all page-images via LangChain
``batch_as_completed``. Before 0.5.2 that ran at LangChain's CPU-tied default
(~12), which made large scanned PDFs slow with no way to raise it. These tests
pin the new behaviour: an explicit ``OCRConfig.max_concurrency`` (or the
``OCR_MAX_CONCURRENCY`` env var) is threaded into ``batch_as_completed``'s config.
"""
from unittest.mock import MagicMock

from doc2mark.ocr.base import resolve_max_concurrency


def test_resolve_precedence(monkeypatch):
    monkeypatch.delenv("OCR_MAX_CONCURRENCY", raising=False)
    assert resolve_max_concurrency(None) is None        # default = library default
    assert resolve_max_concurrency(16) == 16            # explicit value
    monkeypatch.setenv("OCR_MAX_CONCURRENCY", "48")
    assert resolve_max_concurrency(None) == 48          # env fallback
    assert resolve_max_concurrency(16) == 16            # explicit wins over env
    monkeypatch.setenv("OCR_MAX_CONCURRENCY", "0")
    assert resolve_max_concurrency(None) is None        # non-positive -> default
    monkeypatch.setenv("OCR_MAX_CONCURRENCY", "not-an-int")
    assert resolve_max_concurrency(None) is None        # invalid -> default


def _agent_with_chain(cls, max_concurrency):
    """Build a vision agent without invoking its LLM __init__, with a mock chain.

    The mock chain returns objects with a ``.content`` attribute, so the agent
    must be on the LEGACY (non-structured) path for that shape to stay valid —
    the structured path expects ``{"raw", "parsed", ...}`` dict payloads instead.
    """
    agent = cls.__new__(cls)
    agent.max_concurrency = max_concurrency
    agent.structured = False
    msg = MagicMock(); msg.content = "ocr text"; msg.usage_metadata = {}
    chain = MagicMock(); chain.batch_as_completed.return_value = [(0, msg)]
    agent._chain = chain
    return agent, chain


def test_vertex_batch_passes_max_concurrency():
    from doc2mark.ocr.vertex_ai import VertexAIVisionAgent
    agent, chain = _agent_with_chain(VertexAIVisionAgent, 32)
    out = agent.batch_invoke([{"image_data": "x"}])
    chain.batch_as_completed.assert_called_once()
    assert chain.batch_as_completed.call_args.kwargs["config"] == {"max_concurrency": 32}
    assert len(out) == 1


def test_vertex_batch_none_means_default():
    from doc2mark.ocr.vertex_ai import VertexAIVisionAgent
    agent, chain = _agent_with_chain(VertexAIVisionAgent, None)
    agent.batch_invoke([{"image_data": "x"}])
    assert chain.batch_as_completed.call_args.kwargs["config"] is None


def test_openai_batch_passes_max_concurrency():
    from doc2mark.ocr.openai import VisionAgent
    agent, chain = _agent_with_chain(VisionAgent, 64)
    agent.batch_invoke([{"image_data": "x"}])
    assert chain.batch_as_completed.call_args.kwargs["config"] == {"max_concurrency": 64}
