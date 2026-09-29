"""Render spreadsheet cell values the way a spreadsheet application displays them.

openpyxl hands back raw cell values (ints, floats, datetimes, bools) together with each
cell's ``number_format``. :func:`format_cell_value` turns the pair into the displayed text:
``25%`` for 0.25 in ``0%``, ``$1,234.50`` for 1234.5 in ``"$"#,##0.00``, ``2026-03-31`` for a
date in ``yyyy-mm-dd``, ``10`` (not ``10.0``) for an integral number in ``General``.

Retrieval indexes the text a reader sees, so the displayed text is what matters. Formats the
renderer does not understand (fractions, conditional sections) fall back to the ``General``
rendering, which never drops a digit.
"""

import datetime
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Tuple

__all__ = ["format_cell_value"]

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_DATE_TOKEN = re.compile(r"(?i)am/pm|a/p|yyyy|yy|e|b[12]?|mmmmm|mmmm|mmm|mm|m|dddd|ddd|dd|d|hh|h|ss|s|\.0+")
_BRACKET = re.compile(r"\[([^\]]*)\]")
_QUOTED_OR_ESCAPED = re.compile(r'"[^"]*"|\\.')
_DIGIT_PLACEHOLDERS = "0#?"
_EMPTY_SLOT = {"0": "0", "#": "", "?": " "}

Token = Tuple[str, str]  # (kind, text): "lit" literal text, "ph" one of 0#?.,%, "exp" E+/E-, "text" @


def format_cell_value(value, number_format="General") -> str:
    """Displayed text of a spreadsheet cell ``value`` under ``number_format``.

    ``None`` is the empty string, booleans are ``TRUE``/``FALSE`` and strings are returned
    unchanged. Dates and times follow the format's date tokens (ISO 8601 when it has none);
    numbers follow its digit placeholders, percent, thousands separators, scaling commas,
    scientific notation, sections (positive;negative;zero) and literal text.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return value
    fmt = number_format or "General"
    try:
        if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
            return _format_temporal(value, fmt)
        if isinstance(value, datetime.timedelta):
            return _format_elapsed(value, fmt)
        if isinstance(value, (int, float, Decimal)):
            return _format_number(value, fmt)
    except (ArithmeticError, ValueError, OverflowError, IndexError, KeyError):
        pass
    if isinstance(value, (int, float, Decimal)):
        return _general(value)
    return str(value)


# --------------------------------------------------------------------------- shared helpers


def _unquoted(section: str) -> str:
    return _QUOTED_OR_ESCAPED.sub("", section)


def _split_sections(fmt: str) -> List[str]:
    """Split a format on ``;`` that is not quoted, escaped or inside brackets."""
    sections, start, i = [], 0, 0
    while i < len(fmt):
        ch = fmt[i]
        if ch == '"':
            end = fmt.find('"', i + 1)
            i = len(fmt) if end < 0 else end + 1
        elif ch == "\\":
            i += 2
        elif ch == "[":
            end = fmt.find("]", i)
            i = len(fmt) if end < 0 else end + 1
        elif ch == ";":
            sections.append(fmt[start:i])
            start = i = i + 1
        else:
            i += 1
    sections.append(fmt[start:])
    return sections


def _strip_brackets(section: str) -> Tuple[str, bool]:
    """Drop colours and locale tags, keep currency symbols as literals and elapsed-time
    tokens as plain tokens; report whether the section has a condition like ``[>100]``."""
    has_condition = False

    def replace(match):
        nonlocal has_condition
        inner = match.group(1)
        if inner.startswith("$"):
            symbol = inner[1:].split("-", 1)[0]
            return f'"{symbol}"' if symbol else ""
        if inner[:1] in "<>=":
            has_condition = True
            return ""
        if re.fullmatch(r"(?i)h+|m+|s+", inner):
            return inner
        return ""  # colour ([Red], [Color10]) or another tag without text

    return _BRACKET.sub(replace, section), has_condition


def _pick_section(sections: List[str], value) -> Tuple[str, bool]:
    """(section, show_minus): positive;negative;zero sections, as spreadsheets apply them.
    An explicit negative section formats the absolute value and shows no automatic minus."""
    if value < 0 and len(sections) >= 2 and sections[1].strip():
        return sections[1], False
    if value == 0 and len(sections) >= 3 and sections[2].strip():
        return sections[2], False
    return sections[0], True


# --------------------------------------------------------------------------- numbers


def _general(value) -> str:
    """``General``: integers keep every digit; floats get at most 15 significant digits
    (so 0.1 + 0.2 shows ``0.3``) and an ``E+NN``/``E-NN`` exponent when one is needed."""
    if isinstance(value, int):
        return str(value)
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return str(number)
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    text = "%.15g" % number
    if "e" in text:
        mantissa, exponent = text.split("e")
        sign = "-" if exponent.startswith("-") else "+"
        text = f"{mantissa}E{sign}{int(exponent.lstrip('+-')):02d}"
    return text


def _tokens(section: str) -> List[Token]:
    tokens, i = [], 0
    while i < len(section):
        ch = section[i]
        if ch == '"':
            end = section.find('"', i + 1)
            end = len(section) if end < 0 else end
            tokens.append(("lit", section[i + 1:end]))
            i = end + 1
        elif ch == "\\" and i + 1 < len(section):
            tokens.append(("lit", section[i + 1]))
            i += 2
        elif ch in "_*" and i + 1 < len(section):
            i += 2  # alignment padding / fill character: no text of its own
        elif ch in "0#?.,%":
            tokens.append(("ph", ch))
            i += 1
        elif ch in "Ee" and i + 1 < len(section) and section[i + 1] in "+-":
            tokens.append(("exp", section[i:i + 2]))
            i += 2
        elif ch == "@":
            tokens.append(("text", ch))
            i += 1
        else:
            tokens.append(("lit", ch))
            i += 1
    return tokens


def _is_digit(token: Token) -> bool:
    return token[0] == "ph" and token[1] in _DIGIT_PLACEHOLDERS


def _plain(tokens: List[Token], value) -> str:
    """Render literal/percent/text tokens outside the digit body."""
    out = []
    for kind, text in tokens:
        if kind == "lit":
            out.append(text)
        elif kind == "text":
            out.append(_general(value))
        elif kind == "ph" and text == "%":
            out.append("%")
    return "".join(out)


def _round_half_up(number: Decimal, places: int) -> Decimal:
    return number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _group(digits: str) -> str:
    head = len(digits) % 3 or 3
    parts = [digits[:head]] + [digits[i:i + 3] for i in range(head, len(digits), 3)]
    return ",".join(part for part in parts if part)


def _format_number(value, fmt: str) -> str:
    section, show_minus = _pick_section(_split_sections(fmt), value)
    section, has_condition = _strip_brackets(section)
    unquoted = _unquoted(section)
    if has_condition or "/" in unquoted:
        return _general(value)
    if section.strip().lower() in ("general", ""):
        return _general(value if show_minus else abs(value))
    if re.search(r"(?i)[ymdhs]", unquoted) and not re.search(r"[0#?]", unquoted):
        return _general(value)  # a date/time format on a plain number
    tokens = _tokens(section)
    digits = [i for i, token in enumerate(tokens) if _is_digit(token)]
    if not digits:  # "@", or a literal-only section such as "Yes"
        return _plain(tokens, value) or _general(value)

    body = tokens[digits[0]:digits[-1] + 1]
    prefix, suffix = tokens[:digits[0]], tokens[digits[-1] + 1:]
    while suffix and suffix[0] in (("ph", "."), ("ph", ",")):  # "0." or a scaling comma "0,"
        body.append(suffix.pop(0))
    if any(kind == "exp" for kind, _ in suffix):
        return _general(value)

    percent = sum(1 for token in tokens if token == ("ph", "%"))
    number = Decimal(repr(float(value))) if isinstance(value, float) else Decimal(value)
    number = abs(number) * (Decimal(100) ** percent)
    exp_at = next((i for i, (kind, _) in enumerate(body) if kind == "exp"), None)
    if exp_at is None:
        text = _fixed(number, body)
    else:
        text = _scientific(number, body[:exp_at], body[exp_at][1], body[exp_at + 1:])
    minus = "-" if show_minus and value < 0 and re.search(r"[1-9]", text) else ""
    return (minus + _plain(prefix, value) + text + _plain(suffix, value)).strip()


def _fixed(number: Decimal, body: List[Token]) -> str:
    point = next((i for i, token in enumerate(body) if token == ("ph", ".")), None)
    int_part = body if point is None else body[:point]
    dec_part = [] if point is None else body[point + 1:]

    int_slots = [i for i, token in enumerate(int_part) if _is_digit(token)]
    last = int_slots[-1] if int_slots else -1
    # A comma right after the last digit placeholder of the integer part ("#,##0,") or of the
    # whole body ("0.0,,") divides by 1000 each; a comma between digit placeholders groups.
    scale = sum(1 for token in int_part[last + 1:] if token == ("ph", ","))
    scale += sum(1 for token in dec_part if token == ("ph", ","))
    grouping = any(token == ("ph", ",") for token in int_part[:last + 1])
    number = number / (Decimal(1000) ** scale)
    dec_part = [token for token in dec_part if token != ("ph", ",")]

    dec_slots = [text for kind, text in dec_part if kind == "ph" and text in _DIGIT_PLACEHOLDERS]
    int_str, _, frac_str = format(_round_half_up(number, len(dec_slots)), "f").partition(".")
    frac_str = frac_str.ljust(len(dec_slots), "0")[:len(dec_slots)]

    # Integer part: fill placeholders right to left; the leftmost one takes any overflow.
    int_tokens = [token for token in int_part if token != ("ph", ",")]
    slots = [i for i, token in enumerate(int_tokens) if _is_digit(token)]
    out = [text if kind == "lit" else "" for kind, text in int_tokens]
    remaining = int_str.lstrip("0")
    for n, position in enumerate(reversed(slots)):
        placeholder = int_tokens[position][1]
        if n == len(slots) - 1:
            out[position] = remaining or _EMPTY_SLOT[placeholder]
            remaining = ""
        elif remaining:
            out[position], remaining = remaining[-1], remaining[:-1]
        else:
            out[position] = _EMPTY_SLOT[placeholder]
    if grouping:
        int_text = _group("".join(out[i] for i in slots).strip())
    else:
        int_text = "".join(out)

    # Decimal part: '0' keeps trailing zeros, '#' drops them, '?' turns them into spaces.
    dec = list(frac_str)
    for i in range(len(dec_slots) - 1, -1, -1):
        if dec[i] != "0" or dec_slots[i] == "0":
            break
        dec[i] = _EMPTY_SLOT[dec_slots[i]]
    if point is None:
        return int_text
    dec_literals = "".join(text for kind, text in dec_part if kind == "lit")
    return f"{int_text}.{''.join(dec).rstrip()}{dec_literals}"


def _scientific(number: Decimal, mantissa_body: List[Token], marker: str, exp_body: List[Token]) -> str:
    point = next((i for i, token in enumerate(mantissa_body) if token == ("ph", ".")), None)
    int_width = max(1, sum(1 for token in (mantissa_body if point is None else mantissa_body[:point]) if _is_digit(token)))
    dec_count = 0 if point is None else sum(1 for token in mantissa_body[point + 1:] if _is_digit(token))
    exp_width = max(1, sum(1 for token in exp_body if _is_digit(token)))
    exponent = 0
    if number != 0:
        exponent = number.adjusted()
        exponent -= exponent % int_width  # engineering formats (##0.0E+0) step by the integer width
    mantissa = _round_half_up(number.scaleb(-exponent), dec_count)
    if number != 0 and mantissa >= Decimal(10) ** int_width:
        exponent += int_width
        mantissa = _round_half_up(number.scaleb(-exponent), dec_count)
    sign = "-" if exponent < 0 else ("+" if marker[1] == "+" else "")
    return f"{format(mantissa, 'f')}{marker[0].upper()}{sign}{abs(exponent):0{exp_width}d}"


# --------------------------------------------------------------------------- dates and times


def _iso(value) -> str:
    if isinstance(value, datetime.datetime):
        if value.time() == datetime.time(0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="seconds")
    return value.isoformat()


def _format_temporal(value, fmt: str) -> str:
    section, _ = _strip_brackets(_split_sections(fmt)[0])
    if section.strip().lower() == "general" or not re.search(r"(?i)[ymdhs]", _unquoted(section)):
        return _iso(value)
    if isinstance(value, datetime.datetime):
        moment = value
    elif isinstance(value, datetime.date):
        moment = datetime.datetime(value.year, value.month, value.day)
    else:
        moment = datetime.datetime(1899, 12, 31, value.hour, value.minute, value.second, value.microsecond)
    return _render_date(moment, section)


def _date_tokens(section: str) -> List[Token]:
    tokens, i = [], 0
    while i < len(section):
        ch = section[i]
        if ch == '"':
            end = section.find('"', i + 1)
            end = len(section) if end < 0 else end
            tokens.append(("lit", section[i + 1:end]))
            i = end + 1
        elif ch == "\\" and i + 1 < len(section):
            tokens.append(("lit", section[i + 1]))
            i += 2
        elif ch in "_*" and i + 1 < len(section):
            i += 2
        else:
            match = _DATE_TOKEN.match(section, i)
            if match:
                tokens.append(("tok", match.group(0)))
                i = match.end()
            else:
                tokens.append(("lit", ch))
                i += 1
    return tokens


def _render_date(moment: datetime.datetime, section: str) -> str:
    tokens = _date_tokens(section)
    names = [text.lower() for kind, text in tokens if kind == "tok"]
    twelve_hour = "am/pm" in names or "a/p" in names
    out, seen = [], []
    for index, (kind, text) in enumerate(tokens):
        if kind == "lit":
            out.append(text)
            continue
        lower = text.lower()
        if lower in ("m", "mm"):
            before = seen[-1] if seen else ""
            after = next((t.lower() for k, t in tokens[index + 1:] if k == "tok"), "")
            if before.startswith("h") or after.startswith("s"):  # minutes, not months
                out.append(f"{moment.minute:02d}" if lower == "mm" else str(moment.minute))
                seen.append(lower)
                continue
        seen.append(lower)
        if lower in ("yyyy", "e"):
            out.append(f"{moment.year:04d}")
        elif lower == "yy":
            out.append(f"{moment.year % 100:02d}")
        elif lower.startswith("b"):
            out.append(str(moment.year + 543))  # Buddhist era
        elif lower == "mmmmm":
            out.append(_MONTHS[moment.month - 1][0])
        elif lower == "mmmm":
            out.append(_MONTHS[moment.month - 1])
        elif lower == "mmm":
            out.append(_MONTHS[moment.month - 1][:3])
        elif lower == "mm":
            out.append(f"{moment.month:02d}")
        elif lower == "m":
            out.append(str(moment.month))
        elif lower == "dddd":
            out.append(_DAYS[moment.weekday()])
        elif lower == "ddd":
            out.append(_DAYS[moment.weekday()][:3])
        elif lower == "dd":
            out.append(f"{moment.day:02d}")
        elif lower == "d":
            out.append(str(moment.day))
        elif lower in ("hh", "h"):
            hour = (moment.hour % 12 or 12) if twelve_hour else moment.hour
            out.append(f"{hour:02d}" if lower == "hh" else str(hour))
        elif lower in ("ss", "s"):
            out.append(f"{moment.second:02d}" if lower == "ss" else str(moment.second))
        elif lower.startswith("."):
            out.append("." + f"{moment.microsecond:06d}"[:len(lower) - 1])
        elif lower == "am/pm":
            marker = "AM" if moment.hour < 12 else "PM"
            out.append(marker if text[0].isupper() else marker.lower())
        else:  # a/p
            marker = "A" if moment.hour < 12 else "P"
            out.append(marker if text[0].isupper() else marker.lower())
    return "".join(out)


def _format_elapsed(value: datetime.timedelta, fmt: str) -> str:
    total = int(round(value.total_seconds()))
    sign, total = ("-" if total < 0 else ""), abs(total)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    section = _split_sections(fmt)[0].lower()
    if "[m" in section:
        return f"{sign}{hours * 60 + minutes}:{seconds:02d}"
    if "[s" in section:
        return f"{sign}{total}"
    text = f"{sign}{hours}:{minutes:02d}"
    return f"{text}:{seconds:02d}" if "s" in _unquoted(section) or "[h" not in section else text
