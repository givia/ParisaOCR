"""Put the scanned pages in book order using the printed page numbers.

Scanned PDFs are not reliably in order: this one has 50 pages in reverse, two
pages missing and four pages scanned twice (twice badly). The printed number
in the running header is the ground truth, once it is read reliably:

1. Each page's number is chosen among its header candidates (see
   `layout.page_number_candidates`) by agreement with the pages around it: a
   neighbour k PDF pages away should carry a number k higher or k lower
   (lower inside a reversed run). A number no neighbour agrees with is dropped.
2. Pages without a number (chapter openings, maps, blank pages, headers the
   OCR missed) take one from the nearest numbered pages on each side when the
   two agree on a run, forward or reversed; otherwise they continue the run of
   the page before them.
3. Pages that end up with the same number: the number is the page's whose
   number was set by hand, else read (not inferred), else best backed by its
   neighbours. Another page that reads like it, or like another page of the
   group, is a second scan and is dropped (blank pages too). Any other page
   stays in the book, after the page before it in the PDF, under a key between
   that page's number and the next (no printed number of its own): a page whose
   number was misread, front matter whose inferred numbers run into the
   numbered pages, a second run of numbers (two parts numbered apart).
4. Numbers missing from the sequence are reported, and the book gets a note
   at that point.

A page a person kept in the book on the review panel (`keep_by_hand`) is never
dropped as a second scan.
"""
import re
from dataclasses import dataclass, field

WINDOW = 8
_WORD = re.compile(r"[\w\u200c]{3,}")


@dataclass
class Ordered:
    pages: list  # (layout, number) in book order
    reversed_runs: list = field(default_factory=list)  # (first PDF page, last PDF page, first number, last number)
    duplicates: list = field(default_factory=list)  # (dropped PDF page, kept PDF page, number)
    missing: list = field(default_factory=list)  # (first number, last number)
    apart: list = field(default_factory=list)  # (PDF page kept without a number, the number, the PDF page that has it)
    inferred: int = 0


def _alike(a, b):
    """Pages A and B are one page scanned twice: their words are mostly the same (Jaccard 0.5; on the dev books second
    scans read 0.97 to 1.0 alike, different pages that took one number 0.17 at most), or neither has text to tell."""
    ta = {w for l in a.page.lines for w in _WORD.findall(l.text)}
    tb = {w for l in b.page.lines for w in _WORD.findall(l.text)}
    if len(ta) < 8 or len(tb) < 8:
        return len(ta) < 8 and len(tb) < 8
    return len(ta & tb) / len(ta | tb) >= 0.5


def _support(nums, i, c):
    """How much the pages around page i agree with number c. A neighbour's full digit run counts 1,
    its trimmed variants (see `layout.page_number_candidates`) count 0.5, so "332" beats "32"
    when the neighbours read "333" and "336"."""
    total = 0.0
    for j in range(max(0, i - WINDOW), min(len(nums), i + WINDOW + 1)):
        if j != i:
            total += max((1.0 if k == 0 else 0.5 for k, d in enumerate(nums[j]) if abs(d - c) == abs(j - i)), default=0)
    return total


def _off_run(chosen, i):
    """True when the numbered pages before and after page i agree on a run (forward or reversed) that
    page i's number does not fit: a misread digit ("۱۶۴" for "۱۵۴") that a distant page happened to
    support."""
    a = next((k for k in range(i - 1, max(-1, i - WINDOW - 1), -1) if chosen[k] is not None), None)
    b = next((k for k in range(i + 1, min(len(chosen), i + WINDOW + 1)) if chosen[k] is not None), None)
    if a is None or b is None:
        return False
    for step in (1, -1):
        if chosen[b] - chosen[a] == step * (b - a):
            return chosen[i] != chosen[a] + step * (i - a)
    return False


def order(layouts):
    cands = [l.number_candidates if l.kind == "text" else [] for l in layouts]
    chosen = []
    for i, cs in enumerate(cands):
        # The full digit run gets a bonus of one agreeing neighbour over the trimmed variants; a variant
        # wins only when clearly more neighbours back it ("۴۱۴" on page 14, "۵۴۸" on page 48).
        scored = [(_support(cands, i, c) + (1.0 if k == 0 else 0.0), c) for k, c in enumerate(cs)]
        best = max(scored, default=None)
        chosen.append(best[1] if best and _support(cands, i, best[1]) >= 1 else None)
    # second pass with only the chosen numbers, so a wrong candidate on a neighbour cannot vouch for an outlier
    nums = [[c] if c is not None else [] for c in chosen]
    chosen = [c if c is not None and _support(nums, i, c) > 0 else None for i, c in enumerate(chosen)]
    chosen = [None if c is not None and _off_run(chosen, i) else c for i, c in enumerate(chosen)]
    for i, l in enumerate(layouts):  # a number a person set on the review panel is the page's, whatever the others say
        if getattr(l, "number_by_hand", None) is not None:
            chosen[i] = l.number_by_hand

    known = [i for i, c in enumerate(chosen) if c is not None]
    if not known:
        return Ordered([(l, i + 1) for i, l in enumerate(layouts)])
    final, inferred = list(chosen), 0
    wanted = {}  # pages left without a number: the number the run before them would have given
    taken = set(c for c in chosen if c is not None)

    def direction(k, side):
        """+1 or -1: whether the run through known page k goes up or down, judged from its nearest known neighbour."""
        near = [m for m in known if (m < k if side < 0 else m > k)]
        if not near:
            return 1
        m = max(near) if side < 0 else min(near)
        return -1 if (chosen[k] - chosen[m]) * (k - m) < 0 else 1

    for i, c in enumerate(chosen):
        if c is not None:
            continue
        a = max((k for k in known if k < i), default=None)
        b = min((k for k in known if k > i), default=None)
        if a is not None and b is not None and abs(chosen[b] - chosen[a]) == b - a:
            step = 1 if chosen[b] > chosen[a] else -1
            final[i] = chosen[a] + step * (i - a)
        else:
            # The two sides disagree (a gap, or a run boundary): continue the run before the page, or the
            # run after it, whichever gives a number no read page carries.
            options = []
            if a is not None:
                options.append(chosen[a] + direction(a, -1) * (i - a))
            if b is not None:
                options.append(chosen[b] - direction(b, +1) * (b - i))
            if a is not None and options[0] in taken:
                # a read page carries the number the run before it would give: no number of its own; it follows the
                # page before it in the PDF (front matter numbered apart, a page inserted) unless it reads like that
                # page (a second scan of it)
                final[i], wanted[i] = None, options[0]
                inferred += 1
                continue
            final[i] = next((n for n in options if n not in taken), options[0])
        taken.add(final[i])
        inferred += 1

    runs, start = [], None
    for i in range(1, len(final) + 1):
        down = i < len(final) and None not in (final[i], final[i - 1]) and final[i] == final[i - 1] - 1
        if down and start is None:
            start = i - 1
        elif not down and start is not None:
            runs.append((layouts[start].page.index, layouts[i - 1].page.index, final[start], final[i - 1]))
            start = None

    by_num = {}
    for l, n in zip(layouts, final):
        if n is not None:
            by_num.setdefault(n, []).append(l)
    pos = {id(l): i for i, l in enumerate(layouts)}
    out, dups, kept, apart = [], [], [], []
    for i, (l, n) in enumerate(zip(layouts, final)):  # pages with no number of their own
        if n is None:
            twin = next((x for x in by_num.get(wanted.get(i), []) if _alike(l, x)), None)
            if twin is not None and not getattr(l, "keep_by_hand", False):
                dups.append((l.page.index, twin.page.index, wanted[i]))  # a second scan of the page with that number
            else:
                kept.append(l)
                apart.append((l.page.index, None, None))
    for n in sorted(by_num):
        def rank(l):
            i = pos[id(l)]
            return (getattr(l, "number_by_hand", None) is not None, chosen[i] == n, _support(cands, i, n),
                    l.kind == "text", l.quality)
        group = sorted(by_num[n], key=rank, reverse=True)
        out.append((group[0], n))
        seen = [group[0]]
        for d in group[1:]:
            twin = next((k for k in seen if _alike(d, k)), None)
            if twin is None or getattr(d, "keep_by_hand", False):
                kept.append(d)  # another page: it stays, without the number
                apart.append((d.page.index, n, group[0].page.index))
            else:
                dups.append((d.page.index, twin.page.index, n))  # the same page scanned again
            seen.append(d)
    if kept:  # after the page before them in the PDF, between its key and the next one's
        key_of = {id(l): n for l, n in out}
        for d in sorted(kept, key=lambda l: l.page.index):
            i = next(j for j, l in enumerate(layouts) if l is d)
            before = next((key_of[id(layouts[j])] for j in range(i - 1, -1, -1) if id(layouts[j]) in key_of), None)
            higher = sorted(k for k in key_of.values() if before is None or k > before)
            key = (before + higher[0]) / 2 if before is not None and higher else (before + 0.5 if before is not None
                                                                                  else (higher[0] - 0.5 if higher else 0.5))
            key_of[id(d)] = key
            out.append((d, key))
        out.sort(key=lambda x: x[1])
    nums_sorted = sorted(by_num)
    missing = [(a + 1, b - 1) for a, b in zip(nums_sorted, nums_sorted[1:]) if b - a > 1]
    return Ordered(out, runs, dups, missing, inferred=inferred, apart=apart)
