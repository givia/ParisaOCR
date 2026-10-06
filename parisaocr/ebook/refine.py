"""Second look at a page once its layout is known: stacked fractions, footnote markers, verse lines."""
import re

from .layout import hemistich_gap, ink_mask
from .markers import add_markers
from .source import Line, Word
from .textutil import DIGITS, is_digits


def _fraction(num, den, bbox, conf):
    text = f"{num}/{den}"
    return Line(text, bbox, conf, [Word(text, bbox, conf)])


def join_fractions(L):
    """Stacked fractions (⅔ set as a small ۲ over a small ۳) come out of the OCR as tiny digit lines, and
    split the text line around them. The digits are paired into one "۲/۳" line placed where they stood;
    `structure` joins the pieces of the row back together. Returns the number of fractions found."""
    small = [l for l in L.body if is_digits(l.text) and len(l.text) <= 3 and l.h < 0.7 * L.lh]
    found = 0
    for a in sorted(small, key=lambda l: l.y0):
        if a not in L.body:
            continue
        for b in small:
            if b is a or b not in L.body:
                continue
            overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
            if b.y0 > a.yc and b.y0 - a.y1 < 0.5 * L.lh and overlap > 0.4 * min(a.w, b.w):
                box = (min(a.x0, b.x0), a.y0, max(a.x1, b.x1), b.y1)
                frac = _fraction(a.text, b.text, box, min(a.conf, b.conf))
                frac.parts = [a, b]  # its OCR lines (the review panel shows them with the fraction's role)
                L.body = [l for l in L.body if l is not a and l is not b] + [frac]
                found += 1
                break
    # a denominator whose numerator the recognizer joined to the words beside it ("۱‌آن را در بر")
    for d in [l for l in small if l in L.body]:
        for host in L.body:
            if host is d or not host.words or not (host.y0 < d.y0 < host.y1 + 0.3 * L.lh):
                continue
            words = sorted(host.words, key=lambda w: -w.bbox[2])
            m = re.match(rf"([{DIGITS}]+)\u200c?(.*)$", words[0].text)
            if abs(host.x1 - d.x1) < host.h and m and m.group(2):
                words[0].text = m.group(2)
                host.words = words
                host.text = " ".join(w.text for w in words)
                host.bbox = (host.x0, host.y0, min(host.x1, d.x0), host.y1)  # the numerator is no longer part of it
                L.body = [l for l in L.body if l is not d] + [
                    _fraction(m.group(1), d.text, (d.x0, host.y0, max(d.x1, host.x1), d.y1), min(d.conf, host.conf))]
                found += 1
                break
    return found


def refine(layout):
    """Returns the number of footnote markers found in the image."""
    L = layout
    if L.kind != "text" or not L.body:
        return 0
    join_fractions(L)
    mask = ink_mask(L.page)
    added = add_markers(L, mask)
    for line in L.body:
        if not line.latin and len(line.text.split()) >= 4:  # two hemistichs of two words or more
            line.split = hemistich_gap(mask, line, L.width)
    return added
