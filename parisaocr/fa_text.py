"""Persian text normalization, shared by ground-truth preparation and evaluation.

canonical(): the orthography the model should learn and emit. It fixes things
that are never intended in Persian text (Arabic yeh/kaf, Arabic-Indic digits,
presentation forms, bidi control characters, tatweel) but keeps meaningful
distinctions (ZWNJ, Persian vs Latin digits, diacritics, Persian punctuation).

relaxed(): canonical() plus folding of distinctions that human transcribers
make inconsistently, for a fair headline metric against third-party ground
truth (digit script, punctuation script, ZWNJ vs space, diacritics).
"""
import re
import unicodedata

BIDI_CONTROLS = "؜‎‏‪‫‬‭‮⁦⁧⁨⁩﻿"
ZWNJ = "‌"
ARABIC_INDIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
DIACRITICS = re.compile("[ً-ٰٟ]")

_CANON = str.maketrans({
    "ي": "ی",        # ARABIC LETTER YEH -> FARSI YEH
    "ى": "ی",        # ALEF MAKSURA -> FARSI YEH
    "ك": "ک",        # ARABIC LETTER KAF -> KEHEH
    "ە": "ه",        # AE (Kurdish) -> HEH
    "\u0640": None,  # TATWEEL inside a word (kashida): typographic stretching, not text
    "\u2026": "...",  # HORIZONTAL ELLIPSIS: indistinguishable from three full stops in print
    # Dashes of every length are one character in our transcriptions (labeling standard: "-");
    # readers disagree about the length (the teacher writes an em dash or a horizontal bar).
    **{d: "-" for d in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"},
    **{a: p for a, p in zip(ARABIC_INDIC_DIGITS, PERSIAN_DIGITS)},
    **{c: None for c in BIDI_CONTROLS},
})

_RELAX = str.maketrans({
    **{p: str(i) for i, p in enumerate(PERSIAN_DIGITS)},
    "،": ",", "؛": ";", "؟": "?", "٪": "%", "٫": ".", "٬": ",",
    ZWNJ: " ",
    "ۀ": "ه",
})

_PRESENTATION_FORMS = re.compile("[ﭐ-﷿ﹰ-﻿]")
# A tatweel not attached to a preceding Arabic-script letter (or its vowel mark)
# is a dash, e.g. the dialogue dash of Persian books ("ـ چای میخوری؟") or a
# range ("۱۳۹۸ـ۱۴۰۰").
_DASH_TATWEEL = re.compile("(?<![\u0620-\u065f\u066e-\u06d3\u06d5\u06fa-\u06ff])\u0640+")
_SPACES = re.compile(r"[^\S\n]+")
_ZWNJ_RUNS = re.compile(f"{ZWNJ}+")


def canonical(text: str) -> str:
    text = _PRESENTATION_FORMS.sub(lambda m: unicodedata.normalize("NFKC", m.group()), text)
    text = _DASH_TATWEEL.sub("-", unicodedata.normalize("NFC", text)).translate(_CANON)
    # HEH + HAMZA ABOVE -> single code point, also when a ZWNJ was typed between them ("خانه‌ٔ")
    text = re.sub(f"ه{ZWNJ}?\u0654", "ۀ", text)
    text = _ZWNJ_RUNS.sub(ZWNJ, text)
    # A ZWNJ next to a space or line edge is invisible and meaningless.
    text = re.sub(f"{ZWNJ}(?=\\s|$)|(?<=\\s){ZWNJ}|^{ZWNJ}", "", text, flags=re.M)
    lines = (_SPACES.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def relaxed(text: str) -> str:
    text = DIACRITICS.sub("", canonical(text).translate(_RELAX))
    lines = (_SPACES.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


if __name__ == "__main__":
    import sys
    fn = relaxed if "--relaxed" in sys.argv else canonical
    sys.stdout.write(fn(sys.stdin.read()) + "\n")
