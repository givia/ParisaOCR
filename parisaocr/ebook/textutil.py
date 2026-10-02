"""Small text helpers: digits in three scripts, script detection, Persian number formatting."""
import re

# Persian (U+06F0), Arabic-Indic (U+0660) and ASCII digits all occur in OCR output of Persian books.
_DIGIT_MAP = {**{chr(0x06F0 + i): str(i) for i in range(10)}, **{chr(0x0660 + i): str(i) for i in range(10)}}
DIGITS = "0123456789" + "".join(_DIGIT_MAP)
_FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
LATIN = re.compile(r"[A-Za-z]")
ARABIC_SCRIPT = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
SENTENCE_END = tuple(".!?؟:»)]…\"'”")


def ascii_digits(s):
    return "".join(_DIGIT_MAP.get(c, c) for c in s)


def to_int(s):
    """The integer written in S (any digit script), or None if S is not only digits."""
    s = ascii_digits(s.strip())
    return int(s) if s.isdigit() else None


def fa_num(n):
    return "".join(_FA_DIGITS[int(c)] for c in str(n))


def latin_ratio(text):
    """Share of Latin letters among all letters (0 when there are none)."""
    lat = len(LATIN.findall(text))
    ara = len(ARABIC_SCRIPT.findall(text))
    return lat / (lat + ara) if lat + ara else 0.0


def is_digits(s):
    s = s.strip()
    return bool(s) and all(c in DIGITS for c in s)


_PERCENT = re.compile(f"(?<=[{_FA_DIGITS}])/(?=[\\s.،؛:)]|$)")


_TIGHT = re.compile(r"(?<=[\u0600-\u06FF])([:،؛])(?=[\u0600-\u06FF])")


def normalize(text):
    """OCR slips that are safe to undo everywhere: the percent sign read as a slash ("۹۰/ مابقی"), and a
    missing space after a colon or comma between Persian words ("مقدمه:طرح")."""
    return _TIGHT.sub(r"\1 ", _PERCENT.sub("٪", text))
