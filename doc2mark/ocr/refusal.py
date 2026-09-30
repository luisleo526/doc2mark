"""Recognize OCR answers that are only a refusal or a "no readable text" statement.

A vision model sometimes answers an OCR request with "I'm sorry, but I can't assist
with that request." or "圖片中沒有可辨識的文字。" instead of a transcription. Indexed
as page content, such an answer is worse than nothing: it matches queries it has no
business matching and hides that the page was never read. The LLM providers treat an
answer recognized here as *no content*: it goes to the existing free-form recovery,
and when that is a non-answer too, the result is empty text with
``metadata["ocr_refusal"] = True``.

The deterministic check is high-precision on purpose. It only looks at short answers
(a few lines) and only fires when the WHOLE answer is a refusal or "no readable text"
statement (English, Chinese, Japanese, Korean, German, Spanish, French): a model
speaking about itself or about its input image, optionally with a reason made only of
words about image quality or sensitivity ("because it's too blurry", "It may contain
personal information.") and a stock courtesy tail in the model's own wording ("Please
provide a clearer image.", "If you have any other questions, feel free to ask!").
Anything else in the answer -- a number, a quote, a name, a description of the image, a
transcribed line, a person being addressed or asked for something -- is content, and
the answer is kept. What the patterns cannot decide is
left to the optional judge, :data:`NonContentJudge`.
"""

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

NonContentJudge = Callable[[str], Optional[float]]
"""Optional second opinion on a short OCR answer (``OCRConfig.non_content_judge``).

Called as ``judge(ocr_text)`` with the model's answer as the model wrote it (stripped,
not Markdown-escaped, at most :data:`MAX_JUDGE_CHARS` characters) only when the
deterministic patterns did not fire. It returns the probability, from 0.0 to 1.0,
that the answer is *only* a refusal, an apology, an error message or a statement that
the image has no readable text, with nothing transcribed or described from the image.
A probability of at least :data:`JUDGE_THRESHOLD` makes the answer count as no
content; from :data:`SUSPECT_THRESHOLD` up to it the answer is kept and flagged
``metadata["non_content_suspected"]``. ``None`` means "cannot judge" and keeps the answer,
as does any exception the judge raises (it is logged); such a result is not cached. A judge
whose ``available`` attribute is False is treated as no judge. The judge must not raise for normal input and should be
quick; it is called at most once per answer. Cached OCR results are keyed by the
judge's identity (its qualified name and an optional ``version`` attribute), so give
a judge whose behaviour changes a new ``version``.
"""

#: Longest answer (characters / non-empty lines) the deterministic patterns look at.
MAX_PATTERN_CHARS = 400
MAX_PATTERN_LINES = 3
#: Longest answer the judge is asked about.
MAX_JUDGE_CHARS = 600
#: Judge probability at or above which an answer counts as no content.
JUDGE_THRESHOLD = 0.5
#: Judge probability from which an answer below JUDGE_THRESHOLD is kept but flagged
#: ``metadata["non_content_suspected"]``.
SUSPECT_THRESHOLD = 0.3

_APOS = "['’]"
# "I can't", "I cannot", "I won't", "I'm unable to", "I am not able to": first person only
# (notices say "we"; a model's refusal is about itself).
_I_NEG = (
    rf"i\s*(?:can(?:no|{_APOS})?t|can\s+not|won{_APOS}?t|will\s+not|could(?:n{_APOS}?t|\s+not)"
    rf"|(?:am|{_APOS}m)\s+(?:unable|not\s+able)\s+to|do(?:n{_APOS}t|\s+not)\s+have\s+the\s+ability\s+to)"
)
_IMAGE_NOUN = r"(?:image|picture|photo(?:graph)?|scan|screenshot)s?"
# The model refers to its input as "the/this (provided) image"; a person writes about
# "your photo" or "the photo you sent" -- those are page text.
_MODEL_DETERMINER = r"(?:(?:the|this|that|these|those|any)\s+)?(?:(?:provided|given)\s+)?"
_FILLER = r"(?:(?!your\b)\w+\s+){0,2}?"
_NOT_THE_USERS = r"(?!\s+(?:you|that\s+you|which\s+you)\b)"
# "this image", "the text in this image", "the contents of this document image"
_IN_THIS_IMAGE = rf"(?:the|this|that)\s+(?:(?:provided|given)\s+)?(?:document\s+)?{_IMAGE_NOUN}\b{_NOT_THE_USERS}"
_IMAGE_OBJECT = (
    rf"(?:{_MODEL_DETERMINER}{_FILLER}{_IMAGE_NOUN}\b{_NOT_THE_USERS}"
    rf"|(?:(?:the|any)\s+)?(?:text|content|contents|writing)\s+(?:in|on|from|of|within)\s+{_IN_THIS_IMAGE})"
)
# What a model refuses to transcribe (a verb people rarely use about themselves): also
# pages, documents or text ("copyrighted book pages", "the text from this image").
_TRANSCRIPTION_OBJECT = (
    rf"(?:{_MODEL_DETERMINER}{_FILLER}(?:{_IMAGE_NOUN}|text|content|contents|document|documents|page|pages)\b"
    rf"{_NOT_THE_USERS}(?:\s+(?:in|on|from|of)\s+{_IN_THIS_IMAGE})?|it|this|that)"
    r"(?:\s+(?:in\s+full|in\s+(?:its|their)\s+entirety|verbatim|word\s+for\s+word))?"
)

# A reason is made only of words about the image's quality or sensitivity: "because the
# resolution is too low", "as it appears to contain copyrighted material". Any other word
# (a name, a number, "a factory floor") makes it content.
_REASON_WORD = (
    rf"(?:it{_APOS}s|its|it|the|this|that|a|an|image|picture|photo|scan|screenshot|text|writing|handwriting"
    r"|characters|words|resolution|quality|lighting|contrast|focus|is|are|was|were|be|too|very|quite|extremely"
    r"|rather|so|low|poor|bad|blurry|blurred|small|tiny|faint|dark|unclear|illegible|unreadable|not|legible"
    r"|readable|clear|visible|enough|pixelated|faded|grainy|out|of|obscured|cropped|appears|seems|looks|to"
    r"|contain|contains|may|might|could|copyrighted|copyright|protected|material|materials|content|sensitive"
    r"|personal|private|confidential|information|data|identifiable|details|and|or|due|lack|clarity|sharpness)\b"
)
_REASON_WORDS = rf"{_REASON_WORD}(?:\s+{_REASON_WORD}){{0,14}}"
# "..., because it's too blurry", "... as it appears to contain copyrighted material"
_REASON_CLAUSE = rf"(?:\s*,?\s*(?:because|as|since|due\s+to)\s+{_REASON_WORDS})"
# A sentence of its own after the refusal: "It appears to be too blurry."
_REASON_SENTENCE = (
    rf"(?:it|this|the\s+(?:{_IMAGE_NOUN}|text|writing|handwriting|resolution|quality|content))\s+{_REASON_WORDS}"
)

# A model's stock courtesy tail, in its own wording: nothing from the image in it. A request
# a person makes ("Please send a clearer photo.", "Please upload a clearer copy.") is not
# one: that answer reads as a support reply and stays content.
_CLEARER = r"(?:clearer|sharper|higher[- ]resolution|higher[- ]quality|better[- ]quality|more\s+legible)"
_CLEARER_IMAGE = rf"(?:a|an)\s+{_CLEARER}\s+{_IMAGE_NOUN}(?:\s+of\s+the\s+(?:page|document|text))?"
_COURTESY = (
    rf"(?:please\s+provide\s+{_CLEARER_IMAGE}"
    rf"|feel\s+free\s+to\s+(?:share|provide)\s+{_CLEARER_IMAGE}"
    rf"|if\s+you\s+(?:can|could)\s+provide\s+{_CLEARER_IMAGE}\s*,?\s*i(?:{_APOS}d|\s+would|{_APOS}ll|\s+will)\s+be\s+(?:happy|glad)"
    r"\s+to\s+(?:help|assist|try\s+again)(?:\s+with\s+(?:that|it|the\s+transcription))?"
    r"|if\s+you\s+have\s+any\s+other\s+(?:questions|requests)(?:\s+or\s+need\s+(?:help|assistance)\s+with\s+something\s+else)?"
    rf"\s*,?\s*(?:feel\s+free\s+to\s+ask|let\s+me\s+know|i(?:{_APOS}d|\s+would)\s+be\s+happy\s+to\s+help)"
    r"|is\s+there\s+anything\s+else\s+i\s+can\s+(?:help\s+(?:you\s+)?with|do\s+for\s+you|assist\s+(?:you\s+)?with)"
    rf"|let\s+me\s+know\s+if\s+there(?:{_APOS}s|\s+is)\s+anything\s+else\s+i\s+can\s+(?:help\s+(?:you\s+)?with|do\s+for\s+you))"
)
# "..., but I can summarize the general topic if you'd like."
_OFFER = (
    r"but\s+i\s+can\s+(?:summarize|describe|provide\s+a\s+(?:summary|description)\s+of)\s+"
    r"(?:it|this|that|the\s+(?:general\s+)?(?:topic|content|contents|main\s+points|image|page|document))"
    rf"(?:\s+for\s+you)?(?:\s+if\s+you(?:{_APOS}d|\s+would)\s+like)?"
)
_SENTENCE_BREAK = r"(?:(?<=[.!?])\s+|\s*\n\s*)"
_TAIL_RES = (
    re.compile(rf"{_SENTENCE_BREAK}{_COURTESY}\s*[.!?]?\s*$", re.IGNORECASE),
    re.compile(rf"{_SENTENCE_BREAK}{_REASON_SENTENCE}\s*[.!]?\s*$", re.IGNORECASE),
    re.compile(rf"\s*[,;]\s*{_OFFER}\s*[.!]?\s*$", re.IGNORECASE),
)

# What a refusal refuses: the request, the image, its text or its page.
_BARE_REFUSED = (
    rf"(?:(?:help|assist)(?:\s+(?:you\s+)?with\s+(?:that|this|it|(?:that|this|the)\s+request|{_IMAGE_OBJECT}))?"
    r"|comply\s+with\s+(?:that|this|it|(?:that|this|the)\s+request)"
    r"|provide\s+(?:any\s+)?(?:assistance|help)\s+with\s+(?:that|this|it|(?:that|this|the)\s+request)"
    rf"|transcribe\s+{_TRANSCRIPTION_OBJECT}"
    rf"|(?:read|process|extract|identify|recogni[sz]e|analy[sz]e)\s+{_IMAGE_OBJECT}"
    rf"|(?:provide|share|give\s+you)\s+(?:the\s+|any\s+)?(?:text|content|contents|writing)\s+(?:from|in|of)\s+{_IN_THIS_IMAGE}"
    rf"|provide\s+(?:a\s+|the\s+|any\s+)?(?:transcription|description)(?:\s+of\s+{_IMAGE_OBJECT})?)"
)
# What a model declines to help with: people, or the image and its text -- never "your
# ..." ("... help with identifying or making assumptions about people in images").
_MODEL_TOPIC = (
    r"(?:(?!\byour\b)[^.!?\n]){0,60}?"
    rf"\b(?:people|persons?|individuals|faces?|texts?|contents?|{_IMAGE_NOUN})\b"
    rf"(?:\s+(?:in|on|from|of|within)\s+{_MODEL_DETERMINER}{_IMAGE_NOUN}\b)?"
)
# After an apology also: "I'm sorry, but I can't do that.", "... help with identifying
# people in images", "... provide a transcription of copyrighted material".
_APOLOGY_REFUSED = (
    rf"(?:{_BARE_REFUSED}"
    r"|(?:help|assist)\s+(?:you\s+)?with\s+(?:identifying|recogni[sz]ing|transcribing|reading|analy[sz]ing|processing)\b"
    rf"{_MODEL_TOPIC}"
    r"|provide\s+(?:a\s+|the\s+|any\s+)?transcription\s+of\s+"
    rf"(?:{_TRANSCRIPTION_OBJECT}|(?:the\s+|this\s+|any\s+)?copyrighted\s+\w+(?:\s+\w+)?)"
    r"|(?:do|fulfil?l)\s+(?:that|this)(?:\s+request)?"
    r"|share\s+(?:that|this|it)(?:\s+with\s+you)?)"
)
# The apology itself, and nothing between it and the refusal ("I'm sorry Dave, I'm
# afraid I can't do that." is a quote).
_APOLOGY = (
    r"(?:(?:i['’]?m\s+|i\s+am\s+)?(?:very\s+|so\s+|really\s+|truly\s+|terribly\s+)?sorry"
    r"(?:\s+for\s+(?:the|any)\s+inconvenience)?"
    r"|unfortunately|(?:my\s+)?apologies|i\s+apologi[sz]e(?:\s+for\s+(?:the|any)\s+inconvenience)?)"
    r"\s*[,.!]?\s*(?:but\s+)?"
)
_TEXT_QUALIFIER = r"(?:readable|visible|legible|discernible|recogni[sz]able|extractable|clear)"
_TEXT_NOUN = r"(?:text|content|words|characters|writing)"
# Absence of text, not quality ("The photo is blurry." is also an app's hint to the user).
_BLANK = (
    r"(?:(?:mostly|completely|entirely|totally|largely|almost\s+entirely)\s+)?"
    r"(?:blank|empty|illegible|unreadable|not\s+(?:legible|readable))"
)
_IN_THE_IMAGE = rf"(?:in|on|within)\s+(?:this|the)\s+(?:(?:provided|given)\s+)?{_IMAGE_NOUN}\b"
_TOO_BAD = r"(?:blurry|blurred|small|faint|dark|low[- ]resolution|pixelated|unclear|grainy)"
_IN_IMAGE = rf"(?:in|on|within)\s+(?:(?:this|the)\s+)?(?:(?:provided|given)\s+)?{_IMAGE_NOUN}\b"
_NO_TEXT = (
    # "The image appears to be blank (or contains no visible text)", "The page seems to be
    # mostly empty" (a page or document only with the hedge: "This page is intentionally
    # left blank." is page text)
    rf"(?:(?:the|this)\s+(?:(?:provided|given)\s+)?(?:{_IMAGE_NOUN}\s+(?:is|was|appears\s+to\s+be|seems\s+to\s+be"
    r"|looks(?:\s+to\s+be)?)|(?:page|document)\s+(?:appears\s+to\s+be|seems\s+to\s+be|looks(?:\s+to\s+be)?))\s+"
    rf"{_BLANK}(?:\s+(?:or|and)\s+(?:contains|has)\s+no\s+(?:{_TEXT_QUALIFIER}\s+)?(?:text|content|writing))?"
    # "There is no readable text in this image", "There's no text visible in this image"
    rf"|(?:there\s+is|there{_APOS}s|there\s+are)\s+no\s+(?:{_TEXT_QUALIFIER}\s+)?{_TEXT_NOUN}"
    rf"(?:\s+(?:visible|present|detected))?\s+{_IN_THE_IMAGE}"
    # "No text detected.", "No text detected in image" (doc2mark's own prompt asks for it)
    rf"|no\s+(?:{_TEXT_QUALIFIER}\s+)?text\s+(?:was\s+|were\s+|is\s+|could\s+be\s+)?"
    rf"(?:(?:detected|found|present|visible|recogni[sz]ed|identified|extracted|available)(?:\s+{_IN_IMAGE})?|{_IN_IMAGE})"
    # "I don't see any text in the image", "I could not find any content to transcribe"
    rf"|i\s+(?:do\s+not|don{_APOS}t|did\s+not|didn{_APOS}t|could\s+not|couldn{_APOS}t|cannot|can{_APOS}t)\s+"
    rf"(?:see|find|detect|identify|locate|make\s+out)\s+any\s+(?:{_TEXT_QUALIFIER}\s+)?{_TEXT_NOUN}"
    rf"(?:\s+to\s+(?:transcribe|extract))?(?:\s+{_IN_THE_IMAGE})?"
    # "The image does not contain any text.", "This image contains no text."
    rf"|(?:the|this)\s+(?:(?:provided|given)\s+)?{_IMAGE_NOUN}\s+(?:(?:does\s+not|doesn{_APOS}t|did\s+not|didn{_APOS}t)"
    r"\s+(?:appear\s+to\s+|seem\s+to\s+)?(?:contain|have|include|show)\s+any|(?:contains|has|shows|appears\s+to\s+contain"
    rf"|seems\s+to\s+contain)\s+no)\s+(?:{_TEXT_QUALIFIER}\s+)?{_TEXT_NOUN}"
    # "The image is too blurry to read.", "The text in the image is too small and blurry to read."
    rf"|(?:(?:the|this)\s+(?:(?:provided|given)\s+)?{_IMAGE_NOUN}|the\s+(?:text|writing|handwriting)\s+{_IN_THE_IMAGE})"
    rf"\s+is\s+too\s+{_TOO_BAD}(?:\s+(?:and|or)\s+{_TOO_BAD})?\s+to\s+(?:read|transcribe|make\s+out)"
    # the whole answer is a placeholder: "[No content]", "No readable text.", "Illegible."
    r"|\[\s*(?:no\s+(?:readable\s+)?(?:text|content)(?:\s+(?:found|detected|available))?"
    r"|blank(?:\s+(?:page|image))?|empty(?:\s+(?:page|image))?|illegible|unreadable)\s*\]"
    r"|no\s+(?:readable\s+)?text(?:\s+(?:found|detected|available))?|illegible|unreadable)"
)
_END = r"\s*[.!]?"
_ZH_IMAGE = r"(?:圖片|图片|影像|圖像|图像|照片|畫面|画面)"
# ... the image (中的文字 / 的內容), and nothing after it but the full stop.
_ZH_IMAGE_END = (
    rf"(?:這張|这张|此|該|该|本|這個|这个|這些|这些)?{_ZH_IMAGE}"
    r"(?:中|裡|里|上|內|内)?(?:的)?(?:文字|內容|内容|字|資訊|信息)?[。.!！]?"
)
# How a model says it cannot read: nothing else between "cannot" and the verb.
_ZH_CANNOT = r"(?:無法|无法|不能|沒辦法|没办法|未能)(?:進行|进行|清楚|清晰|正確|正确|完整|準確|准确){0,2}"
# Words a refusal in German, Spanish, French, Korean or Japanese uses for what it cannot
# read: a name or any other word makes the sentence content ("das Bild von Herrn Müller").
_DE_WORD = (
    r"(?:den|diesen|dieses|diese|diesem|dieser|das|die|dem|der|des|ein|eine|einen|im|in|auf|aus|"
    r"text|texte|textes|bild|bildes|bilde|inhalt|inhalte|inhalts|foto|fotos|abbildung|grafik|scan|"
    r"dokument|dokuments|seite|schrift|handschrift|handschriftlichen|handschriftliche|gedruckten|"
    r"lesbaren|enthaltenen|geschriebenen|wörter|worte|zeichen|buchstaben|leider|hier|darauf|darin)"
)
_ES_WORD = r"(?-i:[a-záéíóúüñ]+)"  # lower case: a capitalized word is a name
_FR_WORD = r"(?-i:[a-zàâçéèêëîïôûùüÿœæ'’-]+)"
_KO_OBJECT = (
    r"(?:(?:이|해당|제공된|첨부된)\s*)?(?:이미지|사진|그림)(?:의|에서|에|속의|안의)?\s*"
    r"(?:텍스트|글자|문자|내용|손글씨)?(?:를|을|은|는|가|이)?\s*"
)
_JA_OBJECT = (
    r"(?:この|その|提供された|添付の|添付された)?(?:画像|写真|イメージ)(?:の|に|内の|中の)?"
    r"(?:文字|テキスト|内容|手書き部分|手書きの文字|手書き)?(?:は|を|が)?"
)
_ZH_READ = r"(?:辨識|辨识|識別|识别|讀取|读取|處理|处理|轉錄|转录|解析|看清|判讀|判读)"
# Gaps inside a non-English sentence: no digits or quotes (a number or a quote is content).
_GAP = r"[^.\n0-9\"“”„«»「」]"

# The whole answer (after the courtesy tail and a reason sentence are set aside) is one of
# these. Case-insensitive; each needs the model speaking about itself or its input image.
_WHOLE_PATTERNS = [
    # "I'm sorry, but I can't assist with that request.", "Sorry, I cannot read this image."
    rf"{_APOLOGY}(?:{_I_NEG}|i\s*(?:do(?:n{_APOS}t|\s+not)|must\s+not))\s+(?:\w+\s+){{0,3}}?{_APOLOGY_REFUSED}"
    rf"{_REASON_CLAUSE}?{_END}",
    # "I can't transcribe copyrighted book pages", "I am unable to read the text in this image."
    rf"{_I_NEG}\s+(?:\w+\s+){{0,2}}?{_BARE_REFUSED}{_REASON_CLAUSE}?{_END}",
    # "As an AI language model, I cannot read the contents of this document image."
    rf"as\s+an\s+ai(?:\s+(?:language\s+)?model|\s+assistant)?\s*,\s*(?:{_I_NEG}|i\s*do(?:n{_APOS}t|\s+not))\s+"
    rf"(?:\w+\s+){{0,2}}?{_BARE_REFUSED}{_REASON_CLAUSE}?{_END}",
    # "This image may contain sensitive personal information, so I won't transcribe it."
    rf"(?:the|this)\s+(?:{_IMAGE_NOUN}|content|text|page|document)\s+{_REASON_WORDS}\s*[,;]\s*so\s+{_I_NEG}\s+"
    rf"(?:transcribe|provide\s+(?:a\s+)?transcription\s+of|extract\s+(?:the\s+)?text\s+from)\s+(?:it|this|that)"
    rf"(?:\s+{_IMAGE_NOUN})?{_END}",
    # "The image appears to be blank.", "The page seems to be mostly empty; I could not
    # find any content to transcribe."
    rf"{_NO_TEXT}(?:\s*[.;,]\s*(?:and\s+)?{_NO_TEXT})?{_REASON_CLAUSE}?{_END}",
    # Chinese: first person (我) cannot read the image
    rf"(?:很|非常|十分)?(?:抱歉|對不起|对不起|不好意思)[，,、\s]*(?:但是?[，,\s]*)?我(?:目前|暫時|暂时|現在|现在)?"
    rf"{_ZH_CANNOT}{_ZH_READ}{_ZH_IMAGE_END}",
    rf"我(?:目前|暫時|暂时|現在|现在)?{_ZH_CANNOT}{_ZH_READ}{_ZH_IMAGE_END}",
    r"(?:這張|这张|此|該|该|本|這個|这个)?(?:圖片|图片|影像|圖像|图像|照片|畫面|画面)"
    r"(?:中|裡|里|上|內|内)?(?:並|并)?(?:沒有|没有|無|无|不含|未包含|未發現|未发现|找不到|未能找到)"
    r"(?:任何)?(?:可(?:辨識|辨识|識別|识别|讀取|读取|讀|读|見|见)的?|清晰的?)?(?:文字|內容|内容|字)[。.!！]?",
    # Japanese
    r"(?![^\n]*(?:アップロード|お客様|現在))"
    rf"(?:申し訳(?:ありません|ございません)(?:が)?|すみません(?:が)?|ごめんなさい)[、,\s]*{_JA_OBJECT}"
    r"(?:読み取|読|認識|処理|文字起こし|転記|抽出|判読)[^。\n0-9]{0,10}?(?:できません|ません|不可|困難)(?:でした)?[。.!！]?",
    r"(?:この)?画像(?:に|には|の中に)(?:は)?(?:読み取れる|判読できる|認識できる)?(?:文字|テキスト)"
    r"(?:は|が)(?:ありません|含まれていません|見つかりません|見当たりません)(?:でした)?[。.!！]?",
    # Korean
    rf"(?![^\n]*(?:현재|업로드|고객님))(?:죄송|미안)(?:하지만|합니다|해요)[,.\s]*{_KO_OBJECT}"
    rf"(?:인식|읽|처리|추출|판독|전사){_GAP}{{0,10}}?(?:수\s*없|못){_GAP}{{0,8}}?[.!]?",
    r"(?:이\s*)?이미지(?:에는|에|에서)\s*(?:읽을\s*수\s*있는\s*)?(?:텍스트|글자|문자)(?:가|는)\s*"
    r"(?:없습니다|없어요|보이지\s*않습니다)[.!]?",
    # German ("Ihr Foto" / "du" is a person writing to someone)
    r"(?=[^.\n]*\b(?:bild|bildes|bilde|foto|fotos|abbildung|grafik|scan)\b)"
    r"(?![^\n]*\b(?:ihr|ihre|ihren|ihrem|ihrer|ihres|dein|deine|deinen|deinem|du|dich|dir)\b)"
    r"(?:leider\s+(?:kann|konnte)\s+ich|es\s+tut\s+mir\s+leid[,.\s]+(?:aber\s+)?ich\s+(?:kann|konnte)"
    r"|ich\s+(?:kann|konnte)\s+(?:den|diesen|dieses|das|die)\s+(?:text|bild|inhalt))\b"
    rf"(?:\s+{_DE_WORD}\b){{0,8}}\s+(?:nicht|keinen|keine)\b(?:\s+{_DE_WORD}\b){{0,4}}\s+"
    r"(?:erkennen|lesen|entziffern|transkribieren|verarbeiten|extrahieren)[.!]?",
    r"(?:das|dieses)\s+bild\s+enth(?:ä|ae)lt\s+keinen\s+(?:lesbaren\s+|erkennbaren\s+)?text[.!]?",
    # Spanish (first person; "su foto" / "tú" is a person writing to someone)
    r"(?=[^.\n]*\b(?:imagen|foto|fotograf(?:í|i)a)\b)(?![^\n]*\b(?:su|sus|tu|tus|usted|ustedes|te|me)\b)"
    r"(?:(?:lo\s+siento|lamentablemente|disculpa|perd(?:ó|o)n)[,.\s]+(?:pero\s+)?)?no\s+(?:puedo|pude)\s+"
    rf"(?:{_ES_WORD}\s+){{0,2}}?(?:transcribir|leer|procesar|extraer|reconocer|identificar)(?:\s+{_ES_WORD}){{0,8}}[.!]?",
    # French ("vous" is a person writing to someone)
    r"(?=[^.\n]*\b(?:image|photo|capture)\b)(?![^\n]*\b(?:vous|votre|vos|tu|ton|ta|tes|te)\b)"
    r"(?:(?:je\s+suis\s+)?d(?:é|e)sol(?:é|e)e?[,.\s]+(?:mais\s+)?)?je\s+ne\s+(?:peux|parviens|suis\s+pas\s+en\s+mesure)\s+"
    rf"(?:pas\s+)?(?:de\s+|à\s+)?(?:{_FR_WORD}\s+){{0,2}}?"
    rf"(?:transcrire|lire|traiter|extraire|reconna(?:î|i)tre|identifier)(?:\s+{_FR_WORD}){{0,8}}[.!]?",
]
_WHOLE_RE = re.compile("|".join(f"(?:{p})" for p in _WHOLE_PATTERNS), re.IGNORECASE)
# An answer that asks someone to do something (resend, retake, upload, try again, "can
# you ...", "please") reads as a support message or an app's error, which is page text as
# often as a refusal: the patterns leave it to the judge. Checked after the model's stock
# courtesy tail is set aside.
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


def _without_tails(answer: str) -> str:
    """``answer`` without the model's stock courtesy tail, a reason sentence made only of
    image-quality words, and an offer to summarize instead (each at most twice)."""
    for _ in range(4):
        for tail in _TAIL_RES:
            match = tail.search(answer)
            if match and match.start() > 0:
                answer = answer[: match.start()].rstrip()
                break
        else:
            break
    return answer


def matches_non_content_pattern(text: str) -> bool:
    """Whether ``text`` is a short answer that is, as a whole, a refusal or a "no
    readable text" statement, by the deterministic multilingual patterns alone."""
    answer = _normalize(text)
    if not answer or len(answer) > MAX_PATTERN_CHARS:
        return False
    lines = [" ".join(line.split()) for line in answer.split("\n") if line.strip()]
    if len(lines) > MAX_PATTERN_LINES:
        return False
    # One space between words, one line break between lines: the patterns' optional
    # whitespace cannot backtrack over long runs of blanks.
    answer = _without_tails("\n".join(lines))
    if not answer or _ADDRESSES_A_USER.search(answer):
        return False
    return _WHOLE_RE.fullmatch(answer) is not None


@dataclass(frozen=True)
class NonContentScreen:
    """What screening one answer found: ``reason`` it is no content (``"pattern"`` or
    ``"judge"``) or None to keep it; ``suspected``: kept, but the judge rated it between
    ``SUSPECT_THRESHOLD`` and ``JUDGE_THRESHOLD``; ``unanswered``: the judge was asked and gave
    no usable answer (the patterns decided; a cache must not keep the result)."""

    reason: Optional[str] = None
    suspected: bool = False
    unanswered: bool = False


def _usable(judge: Optional[NonContentJudge]) -> Optional[NonContentJudge]:
    """``judge``, unless it says it cannot answer at all (``available`` is False): then
    screening is exactly what it is without a judge."""
    try:
        unavailable = judge is not None and getattr(judge, "available", True) is False
    except Exception:
        unavailable = False
    return None if judge is None or unavailable else judge


def screen_non_content(text: str, judge: Optional[NonContentJudge] = None) -> NonContentScreen:
    """Screen one OCR answer: the deterministic patterns, then the optional judge (see
    :class:`NonContentScreen`). Empty text is not judged here (it is simply empty). Pass the
    answer as the model wrote it, not its Markdown-escaped rendering."""
    answer = _normalize(text)
    if not answer:
        return NonContentScreen()
    if matches_non_content_pattern(answer):
        return NonContentScreen(reason="pattern")
    judge = _usable(judge)
    if judge is None or len(answer) > MAX_JUDGE_CHARS:
        return NonContentScreen()
    try:
        probability = judge(answer)
    except Exception as exc:  # the hook must never break OCR
        logger.warning("non_content_judge failed (%s); keeping the OCR answer", exc)
        return NonContentScreen(unanswered=True)
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        if probability is not None:
            logger.warning("non_content_judge returned %r, not a probability; keeping the OCR answer", probability)
        return NonContentScreen(unanswered=True)
    if probability >= JUDGE_THRESHOLD:
        return NonContentScreen(reason="judge")
    return NonContentScreen(suspected=probability >= SUSPECT_THRESHOLD)


def non_content_reason(text: str, judge: Optional[NonContentJudge] = None) -> Optional[str]:
    """Why ``text`` is not page content -- ``"pattern"`` (deterministic match) or
    ``"judge"`` (the optional :data:`NonContentJudge` said so) -- or ``None`` to keep
    it (see :func:`screen_non_content`)."""
    return screen_non_content(text, judge).reason


def prefetch_non_content(texts, judge: Optional[NonContentJudge]) -> None:
    """Hand a judge that can ``prefetch(answers)`` every answer of ``texts`` that
    :func:`non_content_reason` would ask it about, so it can ask them all at once."""
    prefetch = getattr(_usable(judge), "prefetch", None)
    if not callable(prefetch):
        return
    answers = []
    for text in texts:
        answer = _normalize(text or "")
        if answer and len(answer) <= MAX_JUDGE_CHARS and not matches_non_content_pattern(answer):
            answers.append(answer)
    if answers:
        try:
            prefetch(answers)
        except Exception as exc:  # the hook must never break OCR
            logger.debug("non_content_judge prefetch failed (%r)", exc)


__all__ = [
    "NonContentJudge",
    "NonContentScreen",
    "JUDGE_THRESHOLD",
    "SUSPECT_THRESHOLD",
    "MAX_JUDGE_CHARS",
    "screen_non_content",
    "matches_non_content_pattern",
    "non_content_reason",
    "prefetch_non_content",
]
