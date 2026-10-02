"""Splitting a page's footnote area into numbered notes.

Books number their notes per page, per chapter or through the whole book, and
the OCR reads the numbers in many forms: "۱- ...", "۱۰۸ ...", "1. ...",
"۲فریره" (glued), "Jock and Day ۱ : ...", "Alexis Texas ۲" and
"ناهمجنس خواه : Hetero 1" (next to an English run, where the line's direction
moves it), "... A :۳ پایان‌نامه" (inside a mixed line), or not at all: a superscript number in a word
processor's notes often survives only as its dot (". Quakers"). Note text
also holds numbers that are not note numbers ("۴۷۹)." at the start of a line,
page references, years).

So every line yields candidate numbers, and the notes are the longest chain of
increasing numbers in reading order (steps of 1, a missing number or two
allowed), starting where the book's numbering expects (1, or just after the
last note read before this page) unless the chain itself is long enough to
stand on its own. Candidates outside the chain are text. Lines whose number
was not read fill the numbers the chain skips; other lines continue the note
above them, and lines above the first note continue the previous page's last
note.
"""
import re

from .textutil import DIGITS, is_digits, latin_ratio, to_int


def _letters(s):
    return re.sub(r"[\W\d_]", "", s)

_N = rf"(?<![{DIGITS}])([{DIGITS}]{{1,3}})(?![{DIGITS}])"
_FA = "؀-ۿ"
# the number at the start: "۱- ", "۱۰۸ ", "1. ", "(۳) ", "-1 - ", ". ۸۶ ", "۲فریره"
_START = re.compile(rf"^[\s'‘’“”\"(\-–.]*{_N}(?:\s*[-–—.~):©]\s*|\s+|(?=[^\s{DIGITS}]))(.*)$")
# after a leading English run: "Jock and Day ۱ : ..." , "Alexis Texas ۲", "Charles Bukowski ۱"
_AFTER_LATIN = re.compile(rf"^([A-Za-z][^{_FA}{DIGITS}]*?)\s*{_N}\s*[:.]?\s*(.*)$")
# inside a mixed line, before the Persian text: "... A :۳ پایان‌نامه"
_MID = re.compile(rf"^(.*?[A-Za-z][^{_FA}]*?)\s*[:.]?\s*{_N}\s*(?=[{_FA}])(.*)$")
# at the end of the line, after the English term: "ناهمجنس خواه : Hetero 1" (an English-first note's start, which
# the line's direction puts last)
_END_LATIN = re.compile(rf"^(.*?[A-Za-z][A-Za-z'’.\-]*)\s*{_N}\s*$")
_STARS = re.compile(r"^[\s\-–]*(\*{1,3})\s*(.*)$")
_LOST = re.compile(rf"^\s*\.\s*(?=[A-Za-z{_FA}])")  # ". Quakers": a note whose superscript number was not read


def candidates(text):
    """[(number, text without it, weight)] a line could start a note with, strongest first."""
    out = []
    m = _START.match(text)
    if m:
        k = to_int(m.group(1))
        out.append((k, m.group(2).strip(), 1.0))
        if k >= 10 and k % 10 == 0 and re.match(r"\s*[-–]", text[m.end(1):]):
            out.append((k // 10, m.group(2).strip(), 0.8))  # "۶۰ -همان.": a dash read as a zero
    for rx in (_AFTER_LATIN, _MID):
        m = rx.match(text)
        if m:
            out.append((to_int(m.group(2)), f"{m.group(1).strip()} {m.group(3).strip()}".strip(), 0.8))
    m = _END_LATIN.match(text)
    if m and not out:
        out.append((to_int(m.group(2)), m.group(1).strip(" :"), 0.8))
    return [c for c in out if c[0] is not None and c[0] > 0]


def _best_chain(cands, top):
    """cands: [(line index, number, text, weight)]. The best chain of increasing numbers in line order:
    [(line index, number, text)]."""
    best = {}  # i -> (score, chain)
    for i, (li, k, text, w) in enumerate(cands):
        start_bonus = 0.5 if k == 1 or (top and top < k <= top + 3) else 0.0
        options = [(w + start_bonus, [(li, k, text)])]
        for j in range(i):
            lj, kj, _, _ = cands[j]
            if lj < li and 1 <= k - kj <= 3 and j in best:
                s, chain = best[j]
                options.append((s + w - 0.6 * (k - kj - 1), chain + [(li, k, text)]))
        best[i] = max(options, key=lambda o: o[0])
    if not best:
        return []
    score, chain = max(best.values(), key=lambda o: (o[0], len(o[1])))
    first = chain[0][1]
    plausible = first == 1 or (top and top < first <= top + 3) or len(chain) >= 2
    return chain if plausible else []


def split_notes(lines, top, area_width):
    """lines: the footnote area's lines in reading order (objects with .text, .w). top: the last note number read
    before this page (0 if none). Returns (lead, notes): lead = texts continuing the previous page's last note;
    notes = [(number, text, stars)] in order (stars: numbered by asterisks, "*", "**")."""
    texts = [l.text.strip() for l in lines]
    stars = [_STARS.match(t) for t in texts]
    if any(stars):
        notes, lead = [], []
        for t, s in zip(texts, stars):
            if s:
                notes.append([len(s.group(1)), s.group(2).strip(), True])
            elif notes:
                notes[-1][1] += " " + t
            else:
                lead.append(t)
        return lead, [tuple(n) for n in notes]

    cands = [(i, k, rest, w) for i, t in enumerate(texts) for k, rest, w in candidates(t)]
    # a note number the detector boxed apart from its text ("۴۴" on a line of its own, the note on the next)
    for i, t in enumerate(texts[:-1]):
        k = to_int(t.strip(" .-–)("))
        if k and is_digits(t.strip(" .-–)(")) and not candidates(texts[i + 1]):
            cands.append((i + 1, k, _LOST.sub("", texts[i + 1]).strip(), 0.9))
    cands.sort()
    lost = {i for i, t in enumerate(texts) if _LOST.match(t)}
    if all(getattr(l, "p_start", None) is not None for l in lines):
        # learned note starts (roles.annotate): a line the model is sure starts a note, that begins with the
        # punctuation a lost number leaves behind, counts as a lost number; a numbered candidate the model
        # doubts weighs less
        numbered = {i for i, _, _, _ in cands}
        lost |= {i for i, (l, t) in enumerate(zip(lines, texts))
                 if l.p_start >= 0.5 and i not in numbered and t and not t[0].isalnum() and len(_letters(t)) >= 3}
        cands = [(i, k, rest, w * (0.5 if lines[i].p_start < 0.15 else 1.0)) for i, k, rest, w in cands]
    chain = _best_chain(cands, top)
    short_latin = {i for i, (t, l) in enumerate(zip(texts, lines)) if latin_ratio(t) > 0.5 and l.w < 0.6 * area_width}

    starts = {li: (k, rest) for li, k, rest in chain}
    # numbers the chain skips, filled by lines whose number was not read, in order
    expected_first = 1 if not chain else (top + 1 if top and top < chain[0][1] <= top + 3 else
                                         1 if chain[0][1] <= 3 else chain[0][1])
    bounds = [(-1, expected_first - 1)] + [(li, k) for li, k, _ in chain]
    for (la, ka), (lb, kb) in zip(bounds, bounds[1:] + [(len(texts), None)]):
        missing = (kb - ka - 1) if kb is not None else 0
        fillers = [i for i in range(la + 1, lb) if i in lost or (kb is not None and i in short_latin)]
        if kb is None:
            fillers = [i for i in range(la + 1, lb) if i in lost]  # after the last note only a lost number starts one
            missing = len(fillers)
        for n, i in enumerate(fillers[-missing:] if missing > 0 else []):
            starts[i] = (ka + 1 + n, _LOST.sub("", texts[i]).strip())
    if not chain and not starts:
        # no number anywhere: lines starting with a lost number are notes numbered on from top or 1
        k0 = (top + 1) if top else 1
        for n, i in enumerate(sorted(lost)):
            starts[i] = (k0 + n, _LOST.sub("", texts[i]).strip())

    lead, notes = [], []
    for i, t in enumerate(texts):
        if i in starts:
            notes.append([starts[i][0], starts[i][1], False])
        elif notes:
            notes[-1][1] += " " + t
        else:
            lead.append(t)
    return lead, [tuple(n) for n in notes]
