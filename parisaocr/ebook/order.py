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
3. Pages that end up with the same number are duplicate scans; the one read
   with the most confidence stays.
4. Numbers missing from the sequence are reported, and the book gets a note
   at that point.
"""
from dataclasses import dataclass, field

WINDOW = 8


@dataclass
class Ordered:
    pages: list  # (layout, number) in book order
    reversed_runs: list = field(default_factory=list)  # (first PDF page, last PDF page, first number, last number)
    duplicates: list = field(default_factory=list)  # (dropped PDF page, kept PDF page, number)
    missing: list = field(default_factory=list)  # (first number, last number)
    inferred: int = 0


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

    known = [i for i, c in enumerate(chosen) if c is not None]
    if not known:
        return Ordered([(l, i + 1) for i, l in enumerate(layouts)])
    final, inferred = list(chosen), 0
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
            final[i] = next((n for n in options if n not in taken), options[0])
        taken.add(final[i])
        inferred += 1

    runs, start = [], None
    for i in range(1, len(final) + 1):
        down = i < len(final) and final[i] == final[i - 1] - 1
        if down and start is None:
            start = i - 1
        elif not down and start is not None:
            runs.append((layouts[start].page.index, layouts[i - 1].page.index, final[start], final[i - 1]))
            start = None

    by_num = {}
    for l, n in zip(layouts, final):
        by_num.setdefault(n, []).append(l)
    out, dups = [], []
    for n in sorted(by_num):
        group = sorted(by_num[n], key=lambda l: (l.kind == "text", l.quality), reverse=True)
        out.append((group[0], n))
        dups += [(d.page.index, group[0].page.index, n) for d in group[1:]]
    nums_sorted = sorted(by_num)
    missing = [(a + 1, b - 1) for a, b in zip(nums_sorted, nums_sorted[1:]) if b - a > 1]
    return Ordered(out, runs, dups, missing, inferred)
