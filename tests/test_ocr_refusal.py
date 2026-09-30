"""Refusal / "no readable text" handling for the logic the CLI E2E tests cannot reach:
the deterministic patterns over the labelled answers of the Jev spike (report section
3.2), the optional ``non_content_judge`` hook, and Gemini's native block signals
(the E2E image has no Gemini client and no Google credentials)."""

import io
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from doc2mark.core.base import OCRError
from doc2mark.ocr.base import REFUSAL_USAGE_KEY, OCRConfig, OCRResult
from doc2mark.ocr.openai import OpenAIOCR
from doc2mark.ocr.refusal import matches_non_content_pattern, non_content_reason
from doc2mark.ocr.schema import OCRPage, RawExtraction
from doc2mark.ocr.vertex_ai import VertexAIOCR, VertexAIVisionAgent

# (answer, is_non_content): 41 of the 42 hand-labelled answers of the Jev spike B dataset
# (the 42nd, a JSON error body, is JSON_ERROR below).
JEV_ANSWERS = [
    ("I'm sorry, but I can't assist with that request.", True),
    ("I'm sorry, I can't help with identifying or making assumptions about people in images.", True),
    ("I am unable to transcribe this image because the resolution is too low and the text is not legible.", True),
    ("The image appears to be blank or contains no visible text.", True),
    ("No text detected.", True),
    ("There is no readable text in this image. It seems to be a decorative background graphic.", True),
    ("As an AI language model, I cannot read the contents of this document image.", True),
    ("Unable to process the image. Please provide a clearer scan of the page.", True),
    ("I can't transcribe copyrighted book pages in full, but I can summarize the general topic if you'd like.", True),
    ("抱歉，我無法辨識這張圖片中的文字。", True),
    ("很抱歉，這張圖片解析度過低，無法進行文字轉錄。", True),
    ("对不起，我无法处理这张图片的内容。", True),
    ("圖片中沒有可辨識的文字。", True),
    ("申し訳ありませんが、この画像の文字を読み取ることができません。", True),
    ("죄송하지만 이 이미지의 텍스트를 인식할 수 없습니다.", True),
    ("Leider kann ich den Text in diesem Bild nicht erkennen.", True),
    ("[No content]", True),
    ("The page seems to be mostly empty; I could not find any content to transcribe.", True),
    ("This image may contain sensitive personal information, so I won't transcribe it.", True),
    ("## 03 / 關鍵差異\n一般 AI 工具多聚焦個人效率；TeamSync AI 聚焦組織導入後的資料治理、流程銜接、權限控管與跨系統執行。", False),
    ("產品定位\nAI Core 企業AI作業核心", False),
    ("Introduction\nThis is a comprehensive sample DOCX document that demonstrates various document elements "
     "including text formatting, images, and tables.", False),
    ("<table><tr><th>Engine</th><th>MT</th><th>DSG</th></tr><tr><td>1.0 TSI/85 kW</td><td>✓</td><td>–</td></tr></table>", False),
    ("7-ELEVEN 統一超商\n2026/09/12 14:03\n鮮奶茶 1 x 35\n御飯糰 2 x 32\n合計 NT$99\n統一編號 22555003", False),
    ("Bar chart titled 'Quarterly revenue (NT$M)'. Q1 120, Q2 135, Q3 160, Q4 171. Revenue grows every quarter.", False),
    ("A photograph of a factory floor with two workers inspecting a CNC machine; a sign on the wall reads "
     "'SAFETY FIRST'.", False),
    ("數辰創藝科技 logo", False),
    ("Sorry we missed you!\nWe tried to deliver your parcel today. Scan the QR code to reschedule delivery.", False),
    ("We cannot accept returns after 30 days. Items must be unused and in the original packaging.", False),
    ("Dear Mr. Chen,\nI am sorry to inform you that your application for the 2026 grant was not successful. "
     "We received 412 proposals this year.", False),
    ("Error 503\nService Unavailable\nThe server is temporarily unable to service your request. "
     "Please try again later.", False),
    ("No text? No problem.\nOur OCR engine reads handwriting, stamps and faded scans.", False),
    ("致歉聲明\n本公司因系統異常導致9月12日訂單延遲出貨，造成不便，深感抱歉。", False),
    ("Name: ________\nDate: ________\nSignature: ________\n(This page intentionally left blank for notes)", False),
    ("This page intentionally left blank.", False),
    ("$ pip install doc2mark\nSuccessfully installed doc2mark-0.6.1", False),
    ("Thank you!\nQ&A", False),
    ("- 12 -", False),
    ("第3章 システム構成\n本システムは、データ収集層、解析層、表示層の三層で構成される。", False),
    ("Here is the transcription of the image:\n\nMeeting agenda\n1. Budget review\n2. Hiring plan\n3. Q4 roadmap", False),
    ("Invoice No. 2026-0917\nBill to: Acme Ltd.\nAmount due: [illegible]\nDue date: 2026-10-15", False),
]
# A JSON error body could also be a screenshot of an API error: the patterns leave it
# to the judge (conservative), which the Jev spike showed catches it.
JSON_ERROR = '{"error": "image could not be read"}'


# Refusals the deterministic patterns deliberately leave to the judge, because the same
# words also appear as real page text (an app's upload error, an API error screenshot).
LEFT_TO_THE_JUDGE = {
    "Unable to process the image. Please provide a clearer scan of the page.",
    "很抱歉，這張圖片解析度過低，無法進行文字轉錄。",  # no first person: a system notice reads the same
}


@pytest.mark.parametrize("answer, is_non_content", JEV_ANSWERS)
def test_patterns_on_the_jev_labelled_answers(answer, is_non_content):
    expected = is_non_content and answer not in LEFT_TO_THE_JUDGE
    assert matches_non_content_pattern(answer) is expected


def test_patterns_catch_most_jev_refusals_without_false_alarms():
    refusals = [answer for answer, label in JEV_ANSWERS if label] + [JSON_ERROR]
    content = [answer for answer, label in JEV_ANSWERS if not label]
    assert sum(map(matches_non_content_pattern, refusals)) >= 17  # of 20 (the Jev regex baseline: 17, 1 false alarm)
    assert not any(map(matches_non_content_pattern, content))


@pytest.mark.parametrize("answer", [
    "There are no words to describe our gratitude.",
    "We can't open on Sundays.",
    "抱歉，本店無法提供外送服務。",
    "No content found",
    "I can't help falling in love with you",
    "Leider kann ich morgen nicht kommen.",
    "申し訳ございませんが、ご利用いただけません。",
    "Unable to read the file.\nError code 0x80070570\nContact your administrator",
    "We're sorry, we are unable to process your payment at this time.",
    "無法讀取檔案內容，請稍後再試。",
    "As an AI-first company, we ship faster.",
    "I can't do this alone.",
    "This page is intentionally left blank.",
    "I'm sorry, I can't make it tonight.",
    "Sorry, I can't read your handwriting.",
    "I'm sorry, but I can't comply with the new policy.",
    "There is no content yet.",
    "無法處理的問題，請參考下圖。",
    "Hi team, I can't read the text in the attachment - can you resend it?",
    "Sorry, I cannot read the document you sent.",
    "Unable to process the uploaded image. Please upload a JPG or PNG under 10 MB.",
    "The photo is blurry.\nHold the camera steady and retake it.",
    "As an AI assistant, Aria answers customer questions 24/7.",
    "抱歉，無法讀取您上傳的圖片，請重新上傳。",
    # support chats, emails and letters about an image someone sent
    "Sorry, I can't read your screenshot - could you paste the error log instead?",
    "I can't read the picture, it's too blurry. Can you retake it?",
    "I cannot process your photo ID without a signature. Please resend the form.",
    "I can't find any content about pricing on your website.",
    "I don't see any writing on the back of the card.",
    "Sorry, I can't read the text in this, can you zoom in?",
    "Je ne peux pas lire la photo que vous m'avez envoyée.",
    "Leider kann ich Ihre Bildungsnachweise nicht verarbeiten, da die Unterschrift fehlt.",
    "Leider kann ich das Foto nicht lesen, bitte senden Sie es erneut.",
    "我無法辨識照片中的人是誰，可以再傳一張嗎？",
    # notices and app messages
    "The attached photo is not readable. Please resubmit your application with a new photo.",
    "The uploaded image is empty. Please select a file.",
    "抱歉，系統暫時無法處理照片上傳，工程師正在修復中。",
    "申し訳ありませんが、現在画像のアップロードを処理できません。",
    "죄송합니다, 현재 사진 처리를 할 수 없습니다",
    "No es posible procesar la imagen. Inténtelo de nuevo.",
    "Lo siento, no pude leer la foto de su documento.",
    "Scan is illegible - rescan required",
    "No text found.\nTry a different search term.",
    "There is no visible content in this folder.",
    "There are no clear words to display.",
    # slides and quotes
    "As an AI, I can draft your emails, summarize meetings and answer questions.",
    "I can't do that.",
    "I will not comply.",
    "I'm sorry, Dave. I'm afraid I can't do that.",
    "I cannot describe this picture in words.",
    "The document is empty.",
    # first-person sentences that go on past the object (notes, letters, chats)
    "I can't read the scans until Dr. Lee signs off.",
    "I couldn't process the photos from the wedding yet.",
    "I can't identify any photos from 1962 in the archive.",
    "I cannot provide a description of the driver.",
    "I cannot comply with the request to extend the lease.",
    "I can't help with that, but our billing team can: billing@acme.com",
    "Grandma's recipe card is so faded I can't read the text on it.",
    "很抱歉，我無法處理這批照片，下週一再處理。",
    "As an AI assistant, I cannot give legal advice.",
    "Sorry, I can't read the scans until Dr. Lee signs off.",
    "Sorry, I couldn't process the photos from the wedding yet.",
    "Unfortunately, I cannot provide a description of the driver.",
    "Unfortunately, I can't comply with the request to extend the lease.",
    "Unfortunately, I cannot assist with processing your claim as it was filed after the deadline.",
    "Sorry, I can't help with reading the contract until Monday.",
    "I'm sorry, I can't help with identifying the bird, I'm no expert.",
    "Unfortunately, I can't assist with transcribing the interviews until next week.",
    "I'm sorry, I can't provide a transcription of the hearing until the judge approves it.",
    "I'm sorry, I can't help with reading the text in your photo.",
    "Sorry, I can't read the scans. It seems Dr. Lee has them.",
])
def test_patterns_keep_real_short_text_that_sounds_like_a_refusal(answer):
    assert not matches_non_content_pattern(answer)


@pytest.mark.parametrize("answer", [
    "Sorry, I can't help with that.",
    "I can't help with that.",
    "I can't comply with that request.",
    "I'm unable to read the text in this image.",
    "I am unable to transcribe this image because the resolution is too low.",
    "I'm sorry, but I can't do that.",
    "I'm sorry, I can't help with identifying people in images.",
    "Sorry, I can't provide a transcription of copyrighted material.",
    # with a model's stock follow-up
    "I'm sorry, but I can't assist with that. If you have any other questions, feel free to ask!",
    "I'm sorry, but I can't assist with that.\nIf you have any other questions, feel free to ask!",
    "I'm sorry, I can't help with that. Is there anything else I can do for you?",
    "I'm sorry, but I can't read the text in this image. It appears to be too blurry.",
    "Sorry, I cannot process this image. It seems to contain sensitive personal information.",
    "I'm sorry, I can't provide a description of this image. It may contain sensitive content.",
    "I can't read the text in this image. It looks to be too dark.",
    "Illegible",
    "[Blank]",
    "No readable text",
    "No text available",
])
def test_canonical_refusals_and_bare_placeholders_count_as_no_content(answer):
    """Ambiguous by nature -- a slide could say it -- but these are what models answer
    instead of a transcription, so they are not indexed."""
    assert matches_non_content_pattern(answer)


def test_patterns_ignore_long_answers_that_start_with_an_apology():
    answer = "I'm sorry, but I can't read the header. " + "Line item 12 costs 30 dollars. " * 20
    assert not matches_non_content_pattern(answer)


@pytest.mark.parametrize("answer", [
    "I'm sorry, but I can't assist with that" + "\n" * 350 + "x",
    "I can't read the text in this image" + " \n" * 170 + "x",
])
def test_patterns_do_not_backtrack_over_runs_of_blank_lines(answer):
    started = time.perf_counter()
    assert not matches_non_content_pattern(answer)
    assert time.perf_counter() - started < 0.05


class TestJudgeContract:
    def test_judge_decides_what_the_patterns_leave(self):
        seen = []
        assert non_content_reason(JSON_ERROR, lambda text: seen.append(text) or 0.97) == "judge"
        assert seen == [JSON_ERROR]

    def test_judge_is_not_consulted_when_a_pattern_fires(self):
        seen = []
        assert non_content_reason("No text detected.", lambda text: seen.append(text) or 0.0) == "pattern"
        assert seen == []

    @pytest.mark.parametrize("judge", [
        pytest.param(lambda text: None, id="cannot-judge"),
        pytest.param(lambda text: 0.49, id="below-threshold"),
        pytest.param(lambda text: 1 / 0, id="raises"),
        pytest.param(lambda text: "yes", id="not-a-probability"),
    ])
    def test_answers_are_kept_unless_the_judge_says_non_content(self, judge):
        assert non_content_reason(JSON_ERROR, judge) is None

    def test_threshold_is_inclusive(self):
        assert non_content_reason(JSON_ERROR, lambda text: 0.5) == "judge"

    def test_long_answers_are_not_sent_to_the_judge(self):
        seen = []
        assert non_content_reason("word " * 200, lambda text: seen.append(text) or 1.0) is None
        assert seen == []


class TestProviderJudgeHook:
    """OCRConfig.non_content_judge on an LLM provider: a judged refusal goes to the
    free-form recovery; if that is a refusal too, the result is empty and flagged."""

    def _provider(self, monkeypatch, judge, recovered_text):
        ocr = OpenAIOCR(api_key="test-key", config=OCRConfig(non_content_judge=judge))
        monkeypatch.setattr(ocr, "_ensure_vision_agent", lambda *a, **k: None)
        monkeypatch.setattr(ocr, "_batch_process_with_vision_agent",
                            lambda imgs, *a, **k: [OCRResult(text=recovered_text) for _ in imgs])
        return ocr

    @staticmethod
    def _answer(text):
        return OCRResult(text=text, document=OCRPage(raw=RawExtraction(text=text)), metadata={})

    @staticmethod
    def _judge(text):
        """Recognizes the JSON error body only, like a real judge would."""
        return 0.97 if text == JSON_ERROR else 0.02

    def test_judged_refusal_is_recovered(self, monkeypatch):
        ocr = self._provider(monkeypatch, self._judge, "Invoice No. 2026-0917")
        results = [self._answer(JSON_ERROR)]

        ocr._screen_structured_answers(results)
        out = ocr._recover_empty_structured(results, [b"img"])

        assert out[0].text == "Invoice No. 2026-0917"
        assert out[0].metadata["structured_fallback"] == "free_form"
        assert not out[0].metadata.get("ocr_refusal")

    def test_judged_refusal_with_refused_recovery_is_empty_and_flagged(self, monkeypatch):
        ocr = self._provider(monkeypatch, self._judge, "I'm sorry, but I can't assist with that request.")
        results = [self._answer(JSON_ERROR)]

        ocr._screen_structured_answers(results)
        out = ocr._recover_empty_structured(results, [b"img"])

        assert out[0].text == ""
        assert out[0].metadata["ocr_refusal"] is True

    def test_without_a_judge_the_answer_is_kept(self, monkeypatch):
        ocr = self._provider(monkeypatch, None, "unused")
        results = [self._answer(JSON_ERROR)]

        ocr._screen_structured_answers(results)

        assert results[0].text == JSON_ERROR


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class _ScriptedGeminiAgent:
    """Stands in for the LangChain Gemini chain: structured and free-form replies are
    scripted, everything after the chain is the real provider code."""

    def __init__(self, structured, free_form):
        self.structured, self.free_form, self.calls = list(structured), list(free_form), []

    def batch_invoke(self, input_dicts, structured=None):
        self.calls.append("structured" if structured else "free_form")
        replies = self.structured if structured else self.free_form
        return [replies.pop(0) for _ in input_dicts]

    _extract_usage = staticmethod(VertexAIVisionAgent._extract_usage)


@pytest.mark.parametrize("finish_reason", ["SAFETY", "RECITATION", "FinishReason.PROHIBITED_CONTENT", 4])
def test_gemini_blocked_answer_is_no_content(finish_reason):
    blocked = SimpleNamespace(content="partial recited page", usage_metadata={},
                              response_metadata={"finish_reason": finish_reason})
    parsed = OCRPage(raw=RawExtraction(text="partial recited page"))
    agent = _ScriptedGeminiAgent(
        structured=[{"parsed": parsed, "raw": blocked, "parsing_error": None}],
        free_form=[("", {})],
    )
    ocr = VertexAIOCR(api_key="test-key", config=OCRConfig())
    ocr._vision_agent = agent

    [result] = ocr.batch_process_images([_png()])

    assert result.text == "" and "partial" not in result.document.raw.text
    assert result.metadata["ocr_refusal"] is True
    assert agent.calls == ["structured", "free_form"], "the free-form recovery must be tried first"


def test_gemini_free_form_block_returns_no_text():
    blocked = SimpleNamespace(content="partial", usage_metadata={"total_tokens": 3},
                              response_metadata={"finish_reason": "SAFETY"})
    agent = VertexAIVisionAgent.__new__(VertexAIVisionAgent)
    agent.structured = False
    agent.max_concurrency = None
    agent._chain = MagicMock()
    agent._chain.batch_as_completed.return_value = [(0, blocked)]

    assert agent.batch_invoke([{"image_data": "", "prompt": "p"}], structured=False) == [
        ("", {"total_tokens": 3, REFUSAL_USAGE_KEY: "finish_reason=SAFETY"})
    ]


def test_gemini_stop_is_content():
    ok = SimpleNamespace(content="Board minutes 2026", usage_metadata={}, response_metadata={"finish_reason": "STOP"})
    agent = _ScriptedGeminiAgent(
        structured=[{"parsed": OCRPage(raw=RawExtraction(text="Board minutes 2026")), "raw": ok, "parsing_error": None}],
        free_form=[],
    )
    ocr = VertexAIOCR(api_key="test-key", config=OCRConfig())
    ocr._vision_agent = agent

    [result] = ocr.batch_process_images([_png()])

    assert result.text == "Board minutes 2026" and not result.metadata.get("ocr_refusal")
    assert agent.calls == ["structured"]


class _Agent:
    """A vision agent double for one mode; records the calls it serves."""

    def __init__(self, structured, calls):
        self.structured, self.calls = structured, calls

    def batch_invoke(self, input_dicts):
        self.calls.append("structured" if self.structured else "free_form")
        if self.structured:
            page = OCRPage(raw=RawExtraction(text="Board minutes 2026"))
            return [{"parsed": page, "raw": None, "parsing_error": None, "usage": {}} for _ in input_dicts]
        return [("Board minutes 2026", {}) for _ in input_dicts]


def test_each_openai_call_uses_an_agent_of_its_own_mode(monkeypatch):
    """The free-form recovery swaps the shared agent; a structured call that starts while
    the free-form agent is installed must still run on a structured agent."""
    calls = []
    ocr = OpenAIOCR(api_key="test-key", config=OCRConfig())
    monkeypatch.setattr("doc2mark.ocr.openai.VisionAgent", lambda **kw: _Agent(kw["structured"], calls))
    ocr._vision_agent = _Agent(False, calls)  # left installed by another thread's recovery

    [result] = ocr._batch_process_with_vision_agent([_png()], structured=True)

    assert calls == ["structured"]
    assert result.document is not None and result.text == "Board minutes 2026"


def test_firewall_redo_parse_failure_surfaces_with_on_parse_error_raise(monkeypatch):
    ocr = OpenAIOCR(api_key="test-key", config=OCRConfig(on_parse_error="raise"))
    withheld = OCRResult(text="t", document=OCRPage(raw=RawExtraction(text="t")),
                         metadata={"router_violations": ["illustrative content on document_type='table'"]})

    def redo(indices):
        raise OCRError("Structured OCR parse failed: boom")

    with pytest.raises(OCRError):
        ocr._enforce_router_firewall([withheld], redo)


def test_firewall_redo_failure_keeps_the_flagged_result_by_default():
    ocr = OpenAIOCR(api_key="test-key", config=OCRConfig())
    withheld = OCRResult(text="t", document=OCRPage(raw=RawExtraction(text="t")),
                         metadata={"router_violations": ["illustrative content on document_type='table'"]})

    def redo(indices):
        raise OCRError("Structured OCR parse failed: boom")

    [kept] = ocr._enforce_router_firewall([withheld], redo)

    assert kept.text == "t" and kept.metadata["router_fallback"] == "unresolved"
