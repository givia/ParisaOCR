"""What the converter made of every OCR line of a book, for the review panel (`review`).

Each line gets a decision in the page labeller's vocabulary (`labels`): its role (header, pagenum, heading, byline,
epigraph, body, quote, verse, note, endnote, reference, caption, table, figure, contents, noise, other), a heading's
level, whether it starts a paragraph, the number of the note it starts (0 where it continues one). `structure` records
most of them on the lines as it builds the book (`structure._decide`); the layout's running header and page number,
the contents pages and the lines nothing took (stamps, specks, lines below the reading) are filled in here. A page's
decisions in this form are what the panel shows and what a person's corrections start from (`corrections`).
"""

ROLES = ("body", "heading", "byline", "epigraph", "quote", "verse", "note", "endnote", "reference", "caption", "table",
         "figure", "contents", "header", "pagenum", "noise", "other")


def _rows(l):
    """The OCR rows a line stands for: its own, those of the pieces it was joined from, and number boxes joined to it."""
    rows = [r for r in getattr(l, "marker_rows", ()) if r >= 0]  # a raised number boxed apart, joined to it
    for x in getattr(l, "parts", None) or [l]:
        if x.row >= 0:
            rows.append(x.row)
        rows += [r for r in getattr(x, "joined", ()) if r >= 0]
        rows += [r for r in getattr(x, "marker_rows", ()) if r >= 0] if x is not l else []
    return rows


def collect(layouts, book, ordered):
    """{pdf page: {"n": printed page or None, "kind": text|figure|blank, "type": the page's kind in the labeller's
    words, "lines": {row: decision}}} for every page of the book."""
    n_of = {L.page.index: n for L, n in ordered.pages}
    toc_pages = set(book.report.get("toc_pages", []))
    opens = {ch.page: ch.kind for ch in book.chapters}
    out = {}
    for L in layouts:
        p = L.page
        n = n_of.get(p.index)
        lines = {}

        def put(l, d):
            for r in _rows(l):
                lines.setdefault(r, dict(d))

        missing = []
        for l in p.lines:  # what structure recorded, with what a labeller said beyond it (kept when a person reviews)
            d = getattr(l, "decided", None)
            if d is None:
                continue
            d = dict(d)
            for k, attr in (("m", "label_m"), ("a", "label_a"), ("t", "label_t")):
                if getattr(l, attr, None):
                    d[k] = getattr(l, attr)
            if l.row == -1:  # a line the OCR missed: a heading as the labeller gave it, a line a person added
                m = {k: v for k, v in d.items() if k in ("r", "l", "n", "p", "m", "a")}
                m.setdefault("r", "heading")
                if m["r"] == "heading":
                    m["l"] = m.get("l") or 1
                m["t"] = getattr(l, "missing_text", None) or l.text
                if getattr(l, "missing_after", None) is not None:
                    m["after"] = l.missing_after
                else:
                    m["before"] = getattr(l, "missing_before", None)
                missing.append(m)
                continue
            put(l, d)
        if L.header is not None:
            put(L.header, {"r": "header"})
        if L.footer is not None:
            put(L.footer, {"r": "pagenum"})
        if n in toc_pages:
            for l in p.lines:
                put(l, {"r": "contents"})
        elif getattr(p, "label_type", "") == "ad" or (book.cover is not None and book.cover[0] is L):
            for l in p.lines:
                put(l, {"r": "other" if L.kind != "figure" else "figure"})
        for l in p.lines:  # what nothing took: a stamp, a speck, a line the reading left out
            put(l, {"r": "noise"})
        if getattr(p, "labelled", False) and getattr(p, "label_type", ""):
            kind = p.label_type
        elif n in toc_pages:
            kind = "contents"
        elif L.kind in ("figure", "blank"):
            kind = L.kind
        elif n in opens:
            kind = {"part": "part", "front": "other"}.get(opens[n], "opening")
        else:
            kind = "text"
        printed = n if n is not None and float(n).is_integer() else None  # a page kept by hand has no number
        out[p.index] = {"n": printed, "kind": L.kind, "type": kind, "lines": lines, "missing": missing,
                        "toc": list(getattr(p, "label_toc", []) or [])}
    return out
