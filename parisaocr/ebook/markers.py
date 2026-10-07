"""Footnote markers the recognizer did not read.

Note markers are small raised digits. ParisaOCR reads some of them glued to the
word before ("آمد.۲"), boxes some as a line of their own (a 15 px "۶"), and
drops the rest. The dropped ones are found in the page image: a connected
component of the size of a small digit, at the top of a text line, with no ink
under it down to the baseline (dots and marks above letters have the letter
beneath them). A found marker is written into the line's text after the word
it follows, as `MARK` for a marker whose digits are unknown, or as its digits
for a marker line; `structure` numbers the unknown ones in order.

A mark where the line has no number of the page's notes is also read on its own
(`markernum`: cropped to its own height, so its digits are scaled up; the line
reader loses digits of raised two- and three-digit markers). A number one of the
page's notes has then stands for the marker: in place of a number the line reader
got wrong there, or of a raised number read apart; in place of an unread marker,
as one character (`reread_mark`) that `structure` links to the note of its number
or else takes as an unread mark: it never shows, so a mark that is not a marker (a
vowel sign over a letter) adds no digit to the text. Lines with a labeller's or a
person's markers keep theirs.
"""
import re

import numpy as np
from scipy import ndimage

from . import imagecache, markernum
from .source import LEAD_NUMBER
from .textutil import DIGITS, is_digits, to_int

MARK = "\ue000"
REREAD_BASE = 0xE100  # chr(REREAD_BASE + n): a marker read on its own as number n (1 to 999), one character


def reread_mark(digits):
    """The one character that stands for a marker read on its own as DIGITS: `structure` links the note of that
    number, or else takes it as an unread mark (MARK); like MARK, it is never shown."""
    n = to_int(digits)
    return chr(REREAD_BASE + n) if 0 < n < 1000 else MARK


def reread_number(ch):
    """The number a `reread_mark` character stands for, or None."""
    n = ord(ch) - REREAD_BASE if len(ch) == 1 else 0
    return n if 0 < n < 1000 else None
_READ = re.compile(rf"[{DIGITS}{MARK}*][.،؛:»)\]]*$")  # a word that ends in a marker already
_QUOTE_MARK = re.compile(r"(?<=[\u0600-\u06FF])['‘’`]+(?=[.،؛:»)]*$)")  # a raised "۱" read as an apostrophe
_TRAILING = re.compile(rf"([{DIGITS}]+)[.،؛:»)\]]*$")  # the number a word ends in
_GLUED = re.compile(rf"(?<=[^\s{DIGITS}/\-(])([{DIGITS}]{{1,3}})(?=[\s.،؛:!?؟»«)\]\u200c]|$)")  # a marker read in the text
_warned = []


def _rtl_words(line):
    return sorted(line.words, key=lambda w: -w.bbox[2])


def _word_at(words, x):
    inside = [k for k, w in enumerate(words) if w.bbox[0] <= x <= w.bbox[2]]
    right = [k for k, w in enumerate(words) if w.bbox[0] > x]
    return inside[0] if inside else (max(right) if right else len(words) - 1)


def _loose(words, k):
    """The index of a raised number read as a word of its own next to word K (at most 2 digits), or None."""
    for j in (k, k + 1, k - 1):
        t = words[j].text.strip(".،؛:") if 0 < j < len(words) else ""
        if 0 < j < len(words) and is_digits(t) and len(t) <= 2:
            return j
    return None


def _number_at(line, x):
    """The number the line has where the mark at X is (a raised number read apart, or the end of the word), or None."""
    words = _rtl_words(line)
    if not words:
        return None
    k = _word_at(words, x)
    j = _loose(words, k)
    if j is not None:
        return to_int(words[j].text.strip(".،؛:"))
    m = _TRAILING.search(words[k].text)
    return to_int(m.group(1)) if m else None


def _insert(line, x, mark, reread=None, notes=frozenset()):
    """Put a marker found at x after the word it follows (the word it sits over, or the nearest to its right).

    ParisaOCR's word boxes are spread over the line rather than measured, so a raised digit the recognizer
    read as a word of its own ("نیامد؟ ۱ در") can sit a box away from where the image shows it; such a
    word next to the mark is the marker, and is joined to the word before it. REREAD: the mark's number read on
    its own, one of the page's NOTES (`markernum`): it takes the place of a number there that is not one of them,
    or of the unread marker.
    """
    words = _rtl_words(line)
    if not words:
        return
    k = _word_at(words, x)
    j = _loose(words, k)
    if j is not None:
        t = words[j].text.strip(".،؛:")
        if reread and to_int(t) not in notes:
            words[j].text = words[j].text.replace(t, reread, 1)
        words[j - 1].text += words[j].text
        del words[j]
    else:
        w = words[k]
        if _READ.search(w.text):
            m = _TRAILING.search(w.text)
            if not (reread and m and to_int(m.group(1)) not in notes):
                return  # the recognizer read this marker already
            w.text = w.text[:m.start(1)] + reread + w.text[m.end(1):]
        else:
            m = _QUOTE_MARK.search(w.text)
            put = reread_mark(reread) if reread else mark
            w.text = w.text[:m.start()] + put + w.text[m.end():] if m else w.text + put
    line.words = words
    line.text = " ".join(w.text for w in words)


def _baseline(mask, line):
    band = mask[line.y0:line.y1, line.x0:line.x1]
    return line.y0 + int(np.argmax(band.sum(axis=1))) if band.size else line.y1


def raised_groups(mask, line, others):
    """(x0, x1, y centre) of each raised digit-sized mark at the top of LINE with nothing under it, the digits of one
    marker ("۱۲") together. MASK: the page's ink, or its `imagecache.Ink` (then the marks of the line are kept)."""
    if isinstance(mask, imagecache.Ink):
        ink = mask
        found = imagecache.get("raised", (ink.key, tuple(line.bbox)), lambda: _raised(ink.array, line))
    else:
        found = _raised(mask, line)
    # the ink of the line above or below is not a mark of this line
    found = sorted((x0, x1, yc) for x0, x1, xc, yc in found
                   if not any(o.x0 <= xc <= o.x1 and o.y0 <= yc <= o.y1 for o in others))
    # digits of one marker ("۱۲") are neighbouring components
    groups = []
    for x0, x1, yc in found:
        if groups and x0 - groups[-1][1] < 0.3 * line.h:
            groups[-1][1] = max(groups[-1][1], x1)
            groups[-1][2].append(yc)
        else:
            groups.append([x0, x1, [yc]])
    return [(a, b, sum(ys) / len(ys)) for a, b, ys in groups]


def _superscripts(mask, line, others):
    """x centres of raised digit-sized marks at the top of the line with nothing under them (`raised_groups`)."""
    return [(a + b) / 2 for a, b, _ in raised_groups(mask, line, others)]


def _reread(L, marks):
    """{index in MARKS: digits}: the marks where the line has no number of the page's notes, read on their own
    (`markernum`), where that reading is one of the page's note numbers and not a marker the line reader read
    elsewhere on the page (a tanwin over "اصولاً" read as "۴" must not take note 4 from "محراب۴"); and those note
    numbers."""
    notes = frozenset(to_int(m.group(1)) for l in L.notes if (m := LEAD_NUMBER.match(l.text.strip()))
                      and not m.group(1).startswith("*"))
    if not notes or not markernum.enabled() or getattr(L.page, "labelled", False):
        return {}, notes
    jobs = []
    for i, (line, (x0, x1, yc)) in enumerate(marks):
        if getattr(line, "markers", None) or {"m", "a"} & getattr(line, "forced_fields", set()):
            continue  # a labeller's or a person's markers decide this line
        if _number_at(line, (x0 + x1) / 2) in notes:
            continue  # the line reader has the marker's number
        h = line.h
        box = (max(0, int(x0 - 0.2 * h)), max(0, int(yc - 0.35 * h)),
               min(L.page.width, int(x1 + 0.2 * h)), min(L.page.height, int(yc + 0.35 * h)))
        jobs.append((i, markernum.key(L.page, line, box), box))
    if not jobs:
        return {}, notes
    try:
        got = markernum.read(L.page, [(k, b) for _, k, b in jobs])
    except Exception as e:  # noqa: BLE001  a help, not a step the book needs
        if not _warned:
            _warned.append(e)
            print(f"parisaocr: markers not re-read ({type(e).__name__}: {str(e)[:160]})", flush=True)
        return {}, notes
    read = {to_int(m.group(1)) for l in L.body for m in _GLUED.finditer(l.text)}
    return {i: got[k] for i, k, _ in jobs if got.get(k) and to_int(got[k]) in notes and to_int(got[k]) not in read}, notes


def _raised(mask, line):
    """(x0, x1, x centre, y centre) of the raised digit-sized marks at the top of LINE with nothing under them,
    the lines around it not yet considered."""
    h = line.h
    H, W = mask.shape
    X0, X1 = max(0, line.x0 - h), min(W, line.x1 + 4)
    Y0, Y1 = max(0, int(line.y0 - 0.5 * h)), min(H, line.y1 + 2)
    if line.w <= 0 or line.h <= 0:
        return []
    base = _baseline(mask, line)  # the Persian baseline is the densest row
    strip = mask[Y0:Y1, X0:X1]
    lab, _ = ndimage.label(strip, structure=np.ones((3, 3)))
    found = []
    for k, sl in enumerate(ndimage.find_objects(lab), 1):
        if sl is None:
            continue
        ys, xs = sl
        y0, y1, x0, x1 = ys.start + Y0, ys.stop + Y0, xs.start + X0, xs.stop + X0
        ch, cw = y1 - y0, x1 - x0
        if not (0.2 * h <= ch <= 0.6 * h and cw <= 0.5 * h and (lab[sl] == k).sum() >= 10):
            continue
        yc, xc = (y0 + y1) / 2, (x0 + x1) / 2
        if y1 > base - 0.15 * h or not line.y0 - 0.3 * h <= yc <= line.y0 + 0.5 * h:
            continue
        if mask[y1 + 1:int(base + 0.2 * h), max(0, x0 - 1):x1 + 1].any():
            continue  # a dot or a stroke over a letter
        found.append((x0, x1, xc, yc))
    return found


def add_markers(layout, mask=None):
    """Write the markers on a page with footnotes into its body lines. Returns how many were added."""
    L = layout
    if L.kind != "text" or not L.notes or not L.body:
        return 0
    added = 0
    # marker lines: a raised number boxed on its own, joined to the text line it sits on: a low one of up to 2
    # digits, or the very marker the page labeller saw there (up to 3 digits). Not 3-digit numbers on the rules'
    # word alone: glued to line ends they make a text page look like a contents page (10 junk chapters in one book)
    def marker_line(l):
        t = l.text.strip()
        if not is_digits(t) or len(t) > 3:
            return False
        return getattr(l, "markers", None) == [to_int(t)] or (len(t) <= 2 and l.h < 0.6 * L.lh)
    small = [l for l in L.body if marker_line(l)]
    for s in small:
        host = [l for l in L.body if l is not s and l.y0 - 0.5 * l.h <= s.yc <= l.y0 + 0.5 * l.h and l.x0 - l.h <= s.x0 <= l.x1]
        if host:
            _insert(host[0], (s.x0 + s.x1) / 2, s.text.strip())
            first = (getattr(host[0], "parts", None) or [host[0]])[0]  # an OCR line of the page: the review panel
            first.marker_rows = list(getattr(first, "marker_rows", ())) + [s.row]  # shows the number with it
            if getattr(s, "markers", None):  # the labeller's marker moves with it
                hm = getattr(host[0], "markers", None) or []
                host[0].markers = hm + [k for k in s.markers if k not in hm]
            L.body.remove(s)
            added += 1
    mask = imagecache.Ink(L.page.image) if mask is None else mask
    lines = L.body + ([L.header] if L.header is not None else [])
    marks = []
    for line in L.body:
        if line.latin:
            continue
        others = [o for o in lines if o is not line]
        marks += [(line, g) for g in raised_groups(mask, line, others)]
    reads, notes = _reread(L, marks) if marks else ({}, frozenset())
    for i, (line, (x0, x1, _)) in enumerate(marks):
        before = line.text
        _insert(line, (x0 + x1) / 2, MARK, reads.get(i), notes)
        added += line.text != before
    return added
