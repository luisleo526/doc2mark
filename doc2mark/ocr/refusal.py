"""Recognize OCR answers that are only a refusal or a "no readable text" statement.

A vision model sometimes answers an OCR request with "I'm sorry, but I can't assist
with that request." or "圖片中沒有可辨識的文字。" instead of a transcription. Indexed
as page content, such an answer is worse than nothing: it matches queries it has no
business matching and hides that the page was never read. The LLM providers treat an
answer recognized here as *no content*: it goes to the existing free-form recovery,
and when that is a non-answer too, the result is empty text with
``metadata["ocr_refusal"] = True``.

The deterministic check is conservative on purpose. It only looks at short answers
(a few lines) and only fires when the answer *starts* with a refusal or "no text"
phrase (English, Chinese, Japanese, Korean, German, Spanish, French), so real content
that merely mentions an apology ("Sorry we missed you!", "I am sorry to inform you
...", "This page intentionally left blank.") is kept. What it cannot decide is left
to the optional judge, :data:`NonContentJudge`.
"""

import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)

NonContentJudge = Callable[[str], Optional[float]]
"""Optional second opinion on a short OCR answer (``OCRConfig.non_content_judge``).

Called as ``judge(ocr_text)`` with the model's answer (stripped, at most
:data:`MAX_JUDGE_CHARS` characters) only when the deterministic patterns did not
fire. It returns the probability, from 0.0 to 1.0, that the answer is *only* a
refusal, an apology, an error message or a statement that the image has no readable
text, with nothing transcribed or described from the image. A probability of at
least :data:`JUDGE_THRESHOLD` makes the answer count as no content. ``None`` means
"cannot judge" and keeps the answer, as does any exception the judge raises (it is
logged). The judge must not raise for normal input and should be quick; it is called
at most once per answer.
"""

#: Longest answer (characters / non-empty lines) the deterministic patterns look at.
MAX_PATTERN_CHARS = 400
MAX_PATTERN_LINES = 3
#: Longest answer the judge is asked about.
MAX_JUDGE_CHARS = 600
#: Judge probability at or above which an answer counts as no content.
JUDGE_THRESHOLD = 0.5

_APOS = "['’]"
# "I can't", "I cannot", "I won't", "I'm unable to", "I am not able to": first person only
# (notices say "we"; a model's refusal is about itself).
_I_NEG = (
    rf"i\s*(?:can(?:no|{_APOS})?t|can\s+not|won{_APOS}?t|will\s+not|could(?:n{_APOS}?t|\s+not)"
    rf"|(?:am|{_APOS}m)\s+(?:unable|not\s+able)\s+to|do(?:n{_APOS}t|\s+not)\s+have\s+the\s+ability\s+to)"
)
_IMAGE_NOUN = r"(?:image|picture|photo(?:graph)?|scan|screenshot)s?"
# The model refers to its input as "the/this (provided) image"; a person writes about
# "your photo", "the attached photo" or "the photo you sent" -- those are page text.
_MODEL_DETERMINER = r"(?:(?:the|this|that|these|those|any)\s+)?(?:(?:provided|given)\s+)?"
_FILLER = r"(?:(?!your\b)\w+\s+){0,2}?"
_NOT_THE_USERS = r"(?!\s+(?:you|that\s+you|which\s+you)\b)"
_IMAGE_OBJECT = (
    rf"(?:{_MODEL_DETERMINER}{_FILLER}{_IMAGE_NOUN}\b{_NOT_THE_USERS}"
    r"|(?:(?:the|any)\s+)?(?:text|content|contents|writing)\s+(?:in|on|from|of)\s+(?:the|this|that)\s+"
    rf"(?:(?:provided|given)\s+)?{_IMAGE_NOUN}\b{_NOT_THE_USERS})"
)
# What a model refuses to transcribe (a verb people rarely use about themselves): also
# pages, documents or text ("copyrighted book pages").
_TRANSCRIPTION_OBJECT = (
    rf"{_MODEL_DETERMINER}{_FILLER}(?:{_IMAGE_NOUN}|text|content|contents|document|documents|page|pages)\b"
    rf"{_NOT_THE_USERS}"
)
_END = r"\s*[.!]?\s*$"
# What a refusal refuses. It must end at its object ("I can't read the scans until Dr.
# Lee signs off." is a note, "I can't help falling in love" a lyric); only the model's
# own words -- "transcribe" and, after an apology, "help with identifying ..." or "a
# transcription of ..." -- may go on.
_BARE_REFUSED = (
    rf"(?:(?:help|assist)(?:\s+(?:you\s+)?with\s+(?:that|this|it|(?:that|this|the)\s+request))?{_END}"
    rf"|comply\s+with\s+(?:that|this|the)\s+request{_END}"
    rf"|transcribe\s+{_TRANSCRIPTION_OBJECT}"
    rf"|(?:read|process|extract|identify|recogni[sz]e|analy[sz]e)\s+{_IMAGE_OBJECT}{_END}"
    rf"|provide\s+(?:a\s+|the\s+|any\s+)?(?:transcription|description)(?:\s+of\s+{_IMAGE_OBJECT})?{_END})"
)
# After an apology also: "I'm sorry, but I can't do that.", "... help with identifying
# people in images", "... provide a transcription of copyrighted material".
_APOLOGY_REFUSED = (
    rf"(?:{_BARE_REFUSED}"
    r"|(?:help|assist)\s+(?:you\s+)?with\s+(?:identifying|recogni[sz]ing|transcribing|reading|analy[sz]ing|processing)\b"
    r"|provide\s+(?:a\s+|the\s+|any\s+)?transcription\b"
    rf"|(?:do|fulfil?l)\s+(?:that|this)(?:\s+request)?{_END})"
)
_TEXT_QUALIFIER = r"(?:readable|visible|legible|discernible|recogni[sz]able|extractable|clear)"
# Absence of text, not quality ("The photo is blurry." is also an app's hint to the user).
_BLANK = (
    r"(?:(?:mostly|completely|entirely|totally|largely|almost\s+entirely)\s+)?"
    r"(?:blank|empty|illegible|unreadable|not\s+(?:legible|readable))"
)
_IN_THE_IMAGE = rf"(?:in|on|within)\s+(?:this|the)\s+(?:(?:provided|given)\s+)?{_IMAGE_NOUN}\b"
_ZH_IMAGE = r"(?:圖片|图片|影像|圖像|图像|照片|畫面|画面)"
# ... the image (中的文字 / 的內容), and nothing after it but the full stop.
_ZH_IMAGE_END = (
    r"(?:這張|这张|此|該|该|本|這個|这个)?[^。，,\n]{0,6}?(?:圖片|图片|影像|圖像|图像|照片|畫面|画面)"
    r"(?:中|裡|里|上|內|内)?(?:的)?(?:文字|內容|内容|字|資訊|信息)?[。.!！]?\s*$"
)
_ZH_READ = r"(?:辨識|辨识|識別|识别|讀取|读取|處理|处理|轉錄|转录|解析|看清|判讀|判读)"

# Patterns anchored at the start of the (normalized) answer. Case-insensitive. Each one
# needs the model speaking about itself or about its input image.
_START_PATTERNS = [
    # "I'm sorry, but I can't assist with that request.", "Sorry, I cannot read this image."
    rf"(?:i{_APOS}?m\s+|i\s+am\s+)?(?:very\s+|so\s+|really\s+|truly\s+)?sorry\b[^.!?\n]{{0,60}}?"
    rf"\b(?:{_I_NEG}|i\s*(?:do(?:n{_APOS}t|\s+not)|must\s+not))\s+(?:\w+\s+){{0,3}}?{_APOLOGY_REFUSED}",
    rf"(?:unfortunately|apologies|my\s+apologies|i\s+apologi[sz]e)\b[^.!?\n]{{0,60}}?\b{_I_NEG}\s+"
    rf"(?:\w+\s+){{0,3}}?{_APOLOGY_REFUSED}",
    # "I can't transcribe copyrighted book pages", "I am unable to read the text in this image."
    rf"{_I_NEG}\s+(?:\w+\s+){{0,2}}?{_BARE_REFUSED}",
    rf"(?=[^.\n]*\b{_IMAGE_NOUN}\b)as\s+an\s+ai(?:\s+(?:language\s+)?model|\s+assistant)?\s*,\s*"
    rf"(?:{_I_NEG}|i\s*(?:do(?:n{_APOS}t|\s+not)|am\s+not|have\s+no))",
    # "The image appears to be blank", "The page seems to be mostly empty" (a page or
    # document only with the hedge: "This page is intentionally left blank." is page text)
    rf"(?:the|this)\s+(?:(?:provided|given)\s+)?(?:{_IMAGE_NOUN}\s+(?:is|was|appears\s+to\s+be|seems\s+to\s+be"
    r"|looks(?:\s+to\s+be)?)|(?:page|document)\s+(?:appears\s+to\s+be|seems\s+to\s+be|looks(?:\s+to\s+be)?))\s+"
    rf"{_BLANK}",
    # "There is no readable text in this image", "No text detected.", "I don't see any text in the image"
    rf"(?:there\s+is|there{_APOS}s|there\s+are)\s+no\s+(?:{_TEXT_QUALIFIER}\s+)?"
    rf"(?:text|content|words|characters|writing)\s+{_IN_THE_IMAGE}",
    rf"no\s+(?:{_TEXT_QUALIFIER}\s+)?text\s+(?:was\s+|were\s+|is\s+|could\s+be\s+)?"
    rf"(?:(?:detected|found|present|visible|recogni[sz]ed|identified|extracted|available)(?:\s+{_IN_THE_IMAGE})?"
    rf"\s*[.!]?$|{_IN_THE_IMAGE})",
    rf"i\s+(?:do\s+not|don{_APOS}t|did\s+not|didn{_APOS}t|could\s+not|couldn{_APOS}t|cannot|can{_APOS}t)\s+"
    rf"(?:see|find|detect|identify|locate|make\s+out)\s+any\s+(?:{_TEXT_QUALIFIER}\s+)?"
    rf"(?:text|content|words|characters|writing)(?:\s+{_IN_THE_IMAGE}|\s*[.!]?$)",
    # the whole answer is a placeholder: "[No content]", "No readable text.", "Illegible."
    r"\[\s*(?:no\s+(?:readable\s+)?(?:text|content)(?:\s+(?:found|detected|available))?"
    r"|blank(?:\s+(?:page|image))?|empty(?:\s+(?:page|image))?|illegible|unreadable)\s*\][.!]?$",
    r"(?:no\s+(?:readable\s+)?text(?:\s+(?:found|detected|available))?|illegible|unreadable)[.!]?$",
    # Chinese: first person (我) cannot read the image
    rf"(?=[^。\n]*我)(?:很|非常|十分)?(?:抱歉|對不起|对不起|不好意思)[^。！？!?\n]{{0,30}}?"
    rf"(?:無法|无法|不能|沒辦法|没办法|未能)[^。，,\n]{{0,6}}?{_ZH_READ}{_ZH_IMAGE_END}",
    rf"我(?:無法|无法|不能|沒辦法|没办法)[^。，,\n]{{0,6}}?{_ZH_READ}{_ZH_IMAGE_END}",
    r"(?:這張|这张|此|該|该|本|這個|这个)?(?:圖片|图片|影像|圖像|图像|照片|畫面|画面)"
    r"(?:中|裡|里|上|內|内)?(?:並|并)?(?:沒有|没有|無|无|不含|未包含|未發現|未发现|找不到|未能找到)"
    r"(?:任何)?(?:可(?:辨識|辨识|識別|识别|讀取|读取|讀|读|見|见)的?|清晰的?)?(?:文字|內容|内容|字)[。.!！]?\s*$",
    # Japanese
    r"(?=[^。\n]*(?:画像|写真|イメージ))(?![^\n]*(?:アップロード|お客様|現在))"
    r"(?:申し訳(?:ありません|ございません)|すみません|ごめんなさい)[^。\n]{0,40}?"
    r"(?:読み取|読|認識|処理|文字起こし|転記|抽出|判読)[^。\n]{0,10}?(?:できません|ません|不可|困難)",
    r"(?:この)?画像(?:に|には|の中に)(?:は)?(?:読み取れる|判読できる|認識できる)?(?:文字|テキスト)"
    r"(?:は|が)(?:ありません|含まれていません|見つかりません|見当たりません)",
    # Korean
    r"(?=[^.\n]*(?:이미지|사진|그림))(?![^\n]*(?:현재|업로드|고객님))(?:죄송|미안)(?:하지만|합니다|해요)[^.\n]{0,40}?"
    r"(?:인식|읽|처리|추출|판독|전사)[^.\n]{0,10}?(?:수\s*없|못)",
    r"(?:이\s*)?이미지(?:에는|에|에서)\s*(?:읽을\s*수\s*있는\s*)?(?:텍스트|글자|문자)(?:가|는)\s*"
    r"(?:없습니다|없어요|보이지\s*않습니다)",
    # German ("Ihr Foto" is a person writing to someone)
    r"(?=[^.\n]*\b(?:bild|bildes|bilde|foto|fotos|abbildung|grafik|scan)\b)"
    r"(?![^\n]*\b(?:ihr|ihre|ihren|ihrem|ihrer|ihres|dein|deine|deinen|deinem)\b)"
    r"(?:leider\s+(?:kann|konnte)\s+ich|es\s+tut\s+mir\s+leid[,.\s]+(?:aber\s+)?ich\s+(?:kann|konnte)"
    r"|ich\s+(?:kann|konnte)\s+(?:den|diesen|dieses|das|die)\s+(?:text|bild|inhalt))\b"
    r"[^.\n]{0,80}?\b(?:nicht|keinen|keine)\b[^.\n]{0,40}?"
    r"(?:erkennen|lesen|entziffern|transkribieren|verarbeiten|extrahieren)",
    r"(?:das|dieses)\s+bild\s+enth(?:ä|ae)lt\s+keinen\s+(?:lesbaren\s+|erkennbaren\s+)?text",
    # Spanish (first person; "su foto" is a person writing to someone)
    r"(?=[^.\n]*\b(?:imagen|foto|fotograf(?:í|i)a)\b)(?![^\n]*\b(?:su|sus|tu|tus|usted|ustedes)\b)"
    r"(?:(?:lo\s+siento|lamentablemente|disculpa|perd(?:ó|o)n)[,.\s]+[^.\n]{0,40}?)?no\s+(?:puedo|pude)\s+"
    r"(?:\w+\s+){0,2}?(?:transcribir|leer|procesar|extraer|reconocer|identificar)",
    # French ("vous" is a person writing to someone)
    r"(?=[^.\n]*\b(?:image|photo|capture)\b)(?![^\n]*\b(?:vous|votre|vos|tu|ton|ta|tes|te)\b)"
    r"(?:(?:je\s+suis\s+)?d(?:é|e)sol(?:é|e)e?[,.\s]+[^.\n]{0,40}?)?je\s+ne\s+(?:peux|parviens|suis\s+pas\s+en\s+mesure)\s+"
    r"(?:pas\s+)?(?:de\s+|à\s+)?(?:\w+\s+){0,2}?"
    r"(?:transcrire|lire|traiter|extraire|reconna(?:î|i)tre|identifier)",
]
_START_RE = re.compile("|".join(f"(?:{p})" for p in _START_PATTERNS), re.IGNORECASE)
# A refusal clause anywhere in a short answer: "..., so I won't transcribe it."
_ANYWHERE_RE = re.compile(
    rf"\bi\s*(?:won{_APOS}?t|will\s+not|can(?:no|{_APOS})?t|can\s+not|(?:am|{_APOS}m)\s+(?:unable|not\s+able)\s+to)\s+"
    r"(?:transcribe|provide\s+(?:a\s+)?transcription\s+of|extract\s+(?:the\s+)?text\s+from)"
    rf"\s+(?:it|this|that)(?:\s+{_IMAGE_NOUN})?\s*(?:[.!,]|$)",
    re.IGNORECASE,
)
# An answer that asks someone to do something (resend, retake, upload, try again, "can
# you ...", "please") reads as a support message or an app's error, which is page text as
# often as a refusal: the patterns leave it to the judge.
_ADDRESSES_A_USER = re.compile(
    r"\b(?:can|could|would|will)\s+you\b|\bplease\b|\b(?:re-?take|re-?send|re-?submit|re-?upload|re-?scan|upload"
    r"|zoom|try\s+again)\b|請|请|麻煩|麻烦|再傳|再传|重新上傳|重新上传|可以再|能否|ください|お願い|주세요"
    r"|\bbitte\b|\bveuillez\b|\bpor\s+favor\b|\bint(?:é|e)ntelo\b",
    re.IGNORECASE,
)
# Wrappers a model puts around a bare answer: quotes, emphasis, a code fence (``` is
# folded to ` by the providers).
_WRAPPER_CHARS = " \t\n\"'`*_“”‘’"


def _normalize(text: str) -> str:
    return (text or "").strip().strip(_WRAPPER_CHARS).strip()


def matches_non_content_pattern(text: str) -> bool:
    """Whether ``text`` is a short answer that starts with (or is) a refusal or a
    "no readable text" statement, by the deterministic multilingual patterns alone."""
    answer = _normalize(text)
    if not answer or len(answer) > MAX_PATTERN_CHARS:
        return False
    if sum(1 for line in answer.splitlines() if line.strip()) > MAX_PATTERN_LINES:
        return False
    if _ADDRESSES_A_USER.search(answer):
        return False
    return bool(_START_RE.match(answer) or _ANYWHERE_RE.search(answer))


def non_content_reason(text: str, judge: Optional[NonContentJudge] = None) -> Optional[str]:
    """Why ``text`` is not page content -- ``"pattern"`` (deterministic match) or
    ``"judge"`` (the optional :data:`NonContentJudge` said so) -- or ``None`` to keep
    it. Empty text is not judged here (it is simply empty)."""
    answer = _normalize(text)
    if not answer:
        return None
    if matches_non_content_pattern(answer):
        return "pattern"
    if judge is None or len(answer) > MAX_JUDGE_CHARS:
        return None
    try:
        probability = judge(answer)
    except Exception as exc:  # the hook must never break OCR
        logger.warning("non_content_judge failed (%s); keeping the OCR answer", exc)
        return None
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        if probability is not None:
            logger.warning("non_content_judge returned %r, not a probability; keeping the OCR answer", probability)
        return None
    return "judge" if probability >= JUDGE_THRESHOLD else None


__all__ = [
    "NonContentJudge",
    "JUDGE_THRESHOLD",
    "MAX_JUDGE_CHARS",
    "matches_non_content_pattern",
    "non_content_reason",
]
