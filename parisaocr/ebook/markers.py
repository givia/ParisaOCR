"""Footnote markers the recognizer did not read.

Note markers are small raised digits. ParisaOCR reads some of them glued to the
word before ("آمد.۲"), boxes some as a line of their own (a 15 px "۶"), and
drops the rest. The dropped ones are found in the page image: a connected
component of the size of a small digit, at the top of a text line, with no ink
under it down to the baseline (dots and marks above letters have the letter
beneath them). A found marker is written into the line's text after the word
it follows, as `MARK` for a marker whose digits are unknown, or as its digits
for a marker line; `structure` numbers the unknown ones in order.
"""
import re

import numpy as np
from scipy import ndimage

from .layout import ink_mask
from .textutil import DIGITS, is_digits, to_int

MARK = "\ue000"
_READ = re.compile(rf"[{DIGITS}{MARK}*][.،؛:»)\]]*$")  # a word that ends in a marker already
_QUOTE_MARK = re.compile(r"(?<=[\u0600-\u06FF])['‘’`]+(?=[.،؛:»)]*$)")  # a raised "۱" read as an apostrophe


def _rtl_words(line):
    return sorted(line.words, key=lambda w: -w.bbox[2])


def _insert(line, x, mark):
    """Put a marker found at x after the word it follows (the word it sits over, or the nearest to its right).

    ParisaOCR's word boxes are spread over the line rather than measured, so a raised digit the recognizer
    read as a word of its own ("نیامد؟ ۱ در") can sit a box away from where the image shows it; such a
    word next to the mark is the marker, and is joined to the word before it.
    """
    words = _rtl_words(line)
    if not words:
        return
    inside = [k for k, w in enumerate(words) if w.bbox[0] <= x <= w.bbox[2]]
    right = [k for k, w in enumerate(words) if w.bbox[0] > x]
    k = inside[0] if inside else (max(right) if right else len(words) - 1)
    for j in (k, k + 1, k - 1):
        if 0 < j < len(words) and is_digits(words[j].text.strip(".،؛:")) and len(words[j].text.strip(".،؛:")) <= 2:
            words[j - 1].text += words[j].text
            del words[j]
            break
    else:
        w = words[k]
        if _READ.search(w.text):
            return  # the recognizer read this marker already
        m = _QUOTE_MARK.search(w.text)
        w.text = w.text[:m.start()] + mark + w.text[m.end():] if m else w.text + mark
    line.words = words
    line.text = " ".join(w.text for w in words)


def _baseline(mask, line):
    band = mask[line.y0:line.y1, line.x0:line.x1]
    return line.y0 + int(np.argmax(band.sum(axis=1))) if band.size else line.y1


def _superscripts(mask, line, others):
    """x centres of raised digit-sized marks at the top of the line with nothing under them."""
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
        if any(o.x0 <= xc <= o.x1 and o.y0 <= yc <= o.y1 for o in others):
            continue  # ink of the line above
        if mask[y1 + 1:int(base + 0.2 * h), max(0, x0 - 1):x1 + 1].any():
            continue  # a dot or a stroke over a letter
        found.append((x0, x1))
    # digits of one marker ("۱۲") are neighbouring components
    found.sort()
    groups = []
    for x0, x1 in found:
        if groups and x0 - groups[-1][1] < 0.3 * h:
            groups[-1][1] = max(groups[-1][1], x1)
        else:
            groups.append([x0, x1])
    return [(a + b) / 2 for a, b in groups]


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
            if getattr(s, "markers", None):  # the labeller's marker moves with it
                hm = getattr(host[0], "markers", None) or []
                host[0].markers = hm + [k for k in s.markers if k not in hm]
            L.body.remove(s)
            added += 1
    mask = ink_mask(L.page) if mask is None else mask
    lines = L.body + ([L.header] if L.header is not None else [])
    for line in L.body:
        if line.latin:
            continue
        others = [o for o in lines if o is not line]
        for x in _superscripts(mask, line, others):
            before = line.text
            _insert(line, x, MARK)
            added += line.text != before
    return added
