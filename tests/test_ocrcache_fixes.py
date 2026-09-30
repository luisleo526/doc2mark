"""Unit tests for the OCR provider, cache and judge fixes of the docs audit (PR #27, issues 10-13 and 15-18) that
the E2E tests (tests/e2e/test_ocrcache_fixes.py) cannot reach: a Redis round trip of a custom response model,
the prompt-text part of the OCR cache key, which settings the keys hold, the Vertex AI recovery's token count
and ``update_ocr_configuration(max_workers=...)``."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from doc2mark import UnifiedDocumentLoader
from doc2mark.ocr import cache as cache_module
from doc2mark.ocr.base import TASK_PROMPTS, OCRConfig, OCRResult, Task
from doc2mark.ocr.cache import CachedOCR, RedisOCRCache, build_ocr_cache_key
from doc2mark.ocr.openai import OpenAIOCR
from doc2mark.ocr.prompts import PROMPTS, PromptTemplate
from doc2mark.ocr.refusal import screen_non_content
from doc2mark.ocr.schema import OCRPage, RawExtraction
from doc2mark.ocr.vertex_ai import VertexAIOCR
from tests.test_ocr_cache import install_fake_redis


class Invoice(BaseModel):
    merchant: str
    total: str


class _InvoiceOCR(OpenAIOCR):
    """OpenAI provider whose requests are answered with an Invoice, counting the calls."""

    def __init__(self, **kwargs):
        super().__init__(api_key="k", config=OCRConfig(response_model=Invoice), **kwargs)
        self.calls = 0

    def batch_process_images(self, images, **kwargs):
        self.calls += 1
        invoice = Invoice(merchant="ACME <STORE>", total="12.50")
        return [OCRResult(text=self._custom_document_text(invoice), document=invoice) for _ in images]


def test_a_custom_response_model_survives_a_redis_round_trip(monkeypatch):
    install_fake_redis(monkeypatch)
    provider = _InvoiceOCR()
    cache = RedisOCRCache("redis://localhost/0", key_prefix="test")
    ocr = CachedOCR(provider, cache)

    first = ocr.batch_process_images([b"receipt"])
    second = ocr.batch_process_images([b"receipt"])
    third = ocr.batch_process_images([b"receipt"])

    assert provider.calls == 1
    assert isinstance(second[0].document, Invoice) and second[0].document == first[0].document
    assert third[0].document == first[0].document and second[0].text == first[0].text
    # A hit refreshes the entry (the stored fields are written back as they are).
    assert cache.stats()["refreshes"] == 2 and cache.stats()["errors"] == 0


def test_a_custom_document_that_no_longer_fits_its_model_is_a_miss(monkeypatch):
    install_fake_redis(monkeypatch)
    cache = RedisOCRCache("redis://localhost/0", key_prefix="test")
    provider = _InvoiceOCR()
    key = build_ocr_cache_key(provider, b"receipt", kwargs={})
    cache.set(key, OCRResult(text="{}", document=SimpleNamespace(model_dump=lambda mode=None: {"merchant": 1})))

    [result] = CachedOCR(provider, cache).batch_process_images([b"receipt"])

    assert provider.calls == 1 and isinstance(result.document, Invoice)


def test_custom_document_text_is_escaped_json():
    text = OpenAIOCR._custom_document_text(Invoice(merchant="ACME <STORE>", total="12.50"))

    assert text == '{"merchant": "ACME &lt;STORE>", "total": "12.50"}'


def test_the_ocr_cache_key_follows_doc2mark_prompt_text(monkeypatch):
    provider = OpenAIOCR(api_key="k")
    before = build_ocr_cache_key(provider, b"img")
    monkeypatch.setitem(TASK_PROMPTS, Task.RECEIPT, TASK_PROMPTS[Task.RECEIPT] + " Reworded.")
    monkeypatch.setattr(cache_module, "_PROMPTS_SHA256", None)

    assert build_ocr_cache_key(provider, b"img") != before


@pytest.mark.parametrize("change", [
    {"task": Task.TABLE}, {"on_parse_error": "raise"}, {"model": "gpt-5.4-nano"}, {"response_model": Invoice},
])
def test_every_answer_changing_config_field_is_keyed(change):
    base = build_ocr_cache_key(OpenAIOCR(api_key="k", config=OCRConfig()), b"img")

    assert build_ocr_cache_key(OpenAIOCR(api_key="k", config=OCRConfig(**change)), b"img") != base


def test_a_response_model_whose_fields_change_is_another_key():
    class Receipt(BaseModel):
        total: str

    first = build_ocr_cache_key(OpenAIOCR(api_key="k", config=OCRConfig(response_model=Receipt)), b"img")

    class Receipt(BaseModel):  # noqa: F811 - same name, other fields
        total: str
        tax: str

    assert build_ocr_cache_key(OpenAIOCR(api_key="k", config=OCRConfig(response_model=Receipt)), b"img") != first


def test_the_same_request_is_one_key_however_it_was_configured():
    """The model settings are keyed as the provider sends them, not once per place they were set."""
    by_argument = build_ocr_cache_key(OpenAIOCR(api_key="k", model="gpt-5.4-nano"), b"img")
    by_config = build_ocr_cache_key(OpenAIOCR(api_key="k", config=OCRConfig(model="gpt-5.4-nano")), b"img")
    # The loader passes its own temperature (0), which wins over the config's.
    warm = UnifiedDocumentLoader(ocr_provider="openai", api_key="k", ocr_config=OCRConfig(temperature=0.4)).ocr
    hot = UnifiedDocumentLoader(ocr_provider="openai", api_key="k", ocr_config=OCRConfig(temperature=0.9)).ocr

    assert by_argument == by_config
    assert build_ocr_cache_key(warm, b"img") == build_ocr_cache_key(hot, b"img")


def test_client_objects_among_the_extra_settings_leave_the_key_alone():
    class RateLimiter:
        pass

    plain = build_ocr_cache_key(OpenAIOCR(api_key="k"), b"img")

    assert build_ocr_cache_key(OpenAIOCR(api_key="k", rate_limiter=RateLimiter()), b"img") == plain


def test_the_document_cache_keys_the_context_tier(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("Quarterly notes", encoding="utf-8")
    cache_dir = tmp_path / "cache"
    for tier in (0, 1):
        loader = UnifiedDocumentLoader(ocr_provider="openai", api_key="k", cache_dir=str(cache_dir),
                                       ocr_config=OCRConfig(context_pages=tier))
        loader.load(source)

    assert len(list(cache_dir.glob("*.json"))) == 2


def test_a_task_given_as_text_or_as_task_is_one_key():
    as_task = build_ocr_cache_key(OpenAIOCR(api_key="k", config=OCRConfig(task=Task.RECEIPT)), b"img")

    assert build_ocr_cache_key(OpenAIOCR(api_key="k", config=OCRConfig(task="receipt")), b"img") == as_task


def test_the_settings_identity_leaves_out_what_changes_no_answer():
    from doc2mark.ocr.cache import ocr_settings_identity

    a = OpenAIOCR(api_key="key-a", timeout=5, max_retries=1, max_workers=2, config=OCRConfig(max_concurrency=4))
    b = OpenAIOCR(api_key="key-b", timeout=60, max_retries=5, max_workers=8, config=OCRConfig(max_concurrency=16))

    assert ocr_settings_identity(a) == ocr_settings_identity(b)


def test_the_loader_passes_sampling_settings_only_when_set():
    default = UnifiedDocumentLoader(ocr_provider="openai", api_key="k").ocr
    set_ = UnifiedDocumentLoader(ocr_provider="openai", api_key="k", top_p=0.3, presence_penalty=0.1).ocr

    assert default.model_kwargs == {}
    assert set_.model_kwargs == {"top_p": 0.3, "presence_penalty": 0.1}


def test_vertex_config_model_settings_apply_unless_given():
    from_config = VertexAIOCR(config=OCRConfig(model="gemini-2.0-flash", temperature=0.4, max_tokens=1024))
    explicit = VertexAIOCR(config=OCRConfig(model="gemini-2.0-flash"), model="gemini-2.5-pro")
    loader = UnifiedDocumentLoader(ocr_provider="vertex_ai", ocr_config=OCRConfig(model="gemini-2.0-flash")).ocr

    assert (from_config.model, from_config.temperature, from_config.max_tokens) == ("gemini-2.0-flash", 0.4, 1024)
    assert explicit.model == "gemini-2.5-pro"
    assert loader.model == "gemini-2.0-flash"
    assert VertexAIOCR().model == "gemini-3.1-flash-lite-preview"


def test_recovery_tokens_are_added_when_the_page_keeps_its_headings():
    provider = VertexAIOCR()
    page = OCRPage(raw=RawExtraction(headings=["Q3 results"]))
    empty = OCRResult(text="", document=page, metadata={"token_usage": {"input_tokens": 10, "total_tokens": 12}})
    recovered = OCRResult(text="", metadata={"token_usage": {"input_tokens": 7, "total_tokens": 9}})

    [result] = provider._apply_recovered([empty], [0], [recovered])

    assert result.document.raw.headings == ["Q3 results"]
    assert result.metadata["token_usage"] == {"input_tokens": 17, "total_tokens": 21}


def test_vertex_recovery_tokens_are_added():
    provider = VertexAIOCR()
    empty = OCRResult(text="", document=OCRPage(), metadata={"token_usage": {"input_tokens": 10, "total_tokens": 12}})
    recovered = OCRResult(text="Board minutes", metadata={"token_usage": {"input_tokens": 7, "total_tokens": 9}})

    [result] = provider._apply_recovered([empty], [0], [recovered])

    assert result.metadata["token_usage"] == {"input_tokens": 17, "total_tokens": 21}
    assert result.document.raw.text == "Board minutes"


def test_a_timeout_update_is_the_request_timeout_and_survives_a_provider_switch():
    loader = UnifiedDocumentLoader(ocr_provider="openai", api_key="k")

    loader.update_ocr_configuration(timeout=60, max_retries=5)

    assert (loader.ocr.timeout, loader.ocr.max_retries) == (60, 5) and "timeout" not in loader.ocr.model_kwargs
    loader.set_ocr_provider("vertex_ai")
    assert (loader.ocr.timeout, loader.ocr.max_retries) == (60, 5)


def test_a_changed_prompt_template_shapes_the_free_form_prompt():
    ocr = OpenAIOCR(api_key="k")
    ocr.prompt_template = PromptTemplate.TABLE_FOCUSED
    own = OpenAIOCR(api_key="k", default_prompt="Read the ledger.")

    assert ocr._build_prompt().startswith(PROMPTS[PromptTemplate.TABLE_FOCUSED])
    assert own._build_prompt().startswith("Read the ledger.")
    own.update_prompt_template("table_focused")
    assert own._build_prompt().startswith(PROMPTS[PromptTemplate.TABLE_FOCUSED])


def test_update_ocr_configuration_max_workers_rebuilds_the_client():
    loader = UnifiedDocumentLoader(ocr_provider="openai", api_key="k")
    provider = loader.ocr
    provider._vision_agent = object()

    loader.update_ocr_configuration(max_workers=3)

    assert provider.max_workers == 3 and provider._vision_agent is None
    assert provider._max_concurrency() == 3


@pytest.mark.parametrize("verdict", [1.5, -0.1, float("nan"), float("inf"), 10 ** 400, True, "0.9"])
def test_a_non_content_judge_value_that_is_not_a_probability_is_no_verdict(verdict):
    screen = screen_non_content("Totals by region: see chart", lambda answer: verdict)

    assert screen.reason is None and screen.unanswered and not screen.suspected


def test_an_answer_in_a_custom_model_is_not_screened():
    provider = OpenAIOCR(api_key="k", config=OCRConfig(non_content_judge=lambda answer: 0.99))
    invoice = Invoice(merchant="ACME", total="12.50")
    results = [OCRResult(text=OpenAIOCR._custom_document_text(invoice), document=invoice)]

    provider._screen_structured_answers(results)

    assert results[0].document is invoice


def test_structured_answers_keep_their_page():
    page = OCRPage(raw=RawExtraction(text=""))

    assert OpenAIOCR._is_empty_structured(OCRResult(text="", document=page))
    assert not OpenAIOCR._is_empty_structured(OCRResult(text="", document=Invoice(merchant="", total="")))
