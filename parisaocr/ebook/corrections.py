"""A person's corrections of a converted book, made on the review panel (`review`): OUT/NAME.review.json.

A page the person changed is kept whole, as page labels in the page labeller's format (`labels`): the converter's
decision for every OCR line when the person first changed the page (`decisions`), with the person's changes on top
(their fields listed per line in "h"). Such a page is decided by its labels, as a labeller's page is, and every
reading on it is followed as given: a role, a heading level, a paragraph start, a note's number, a marker's place, a
corrected text (`labels.attach`, `structure`). The rest of the book keeps the converter's own decisions.

Book-level corrections sit beside the pages: the book's data (title, authors ...), its cover, and the issues the
person looked at and left as they are. Every change is logged (a page as it was before), so it can be undone.

A line is known by its OCR row (`source.Line.row`) and, in case the OCR is run again, by its box and text: rows that
moved are found again by their boxes (`labels_for`).
"""
import copy
import datetime
import json
import pathlib

from .textutil import ascii_digits, is_digits

VERSION = 1
LINE_FIELDS = ("r", "l", "n", "p", "m", "a", "x", "t")  # role, level, note number, paragraph start, markers,
#                                                          marker words, corrected text, heading text as printed
PAGE_FIELDS = ("type", "pn", "crop", "rotate", "tables", "figures", "keep")  # figures: the page's picture boxes, as
#                       the person drew them (replacing those found); keep: in the book, though another page took its number
MISSING_FIELDS = ("r", "l", "n", "p", "m", "a", "t", "before", "after")  # a line the OCR missed: its labels, its
#                                                                           text, the row it goes before or after
HISTORY = 300


def path_for(out_dir, name):
    return pathlib.Path(out_dir) / f"{name}.review.json"


def empty():
    return {"version": VERSION, "pages": {}, "book": {}, "ignored": [], "history": []}


def load(path):
    """The corrections file (pages keyed by PDF page number), or an empty one."""
    path = pathlib.Path(path)
    if not path.exists():
        return empty()
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty()
    out = empty()
    out.update({k: d.get(k, out[k]) for k in ("book", "ignored", "history")})
    out["pages"] = {int(k): v for k, v in (d.get("pages") or {}).items() if str(k).isdigit() and isinstance(v, dict)}
    return out


def save(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = dict(data, version=VERSION, when=datetime.datetime.now().isoformat(timespec="seconds"),
             pages={str(k): v for k, v in sorted(data["pages"].items())})
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def human(page):
    """Whether a person changed anything on PAGE (else they looked at it and found it right: there is nothing to
    follow, the converter decides it as before)."""
    return bool(page.get("h")) or any(it.get("h") for it in page.get("lines", []))


def _match(items, rows):
    """{row now: item} for a page's stored line items, each found again among ROWS by its row and box, else by the
    row its box overlaps most."""
    out = {}
    for it in items:
        row, b = it.get("i"), it.get("b")
        if row in rows and row not in out and (b is None or _iou(b, rows[row]["bbox"]) >= 0.6):
            out[row] = it
            continue
        best = max(rows, key=lambda r: _iou(b, rows[r]["bbox"]), default=None) if b else None
        if best is not None and best not in out and _iou(b, rows[best]["bbox"]) >= 0.5:
            out[best] = it
    return out


def _current(stored, decided, rows):
    """The page as the converter reads it now (DECIDED), with what the person changed on it (STORED) on top."""
    page = snapshot(decided, rows)
    if not stored:
        return page
    mine = _match([it for it in stored.get("lines", []) if it.get("h")], rows)
    for it in page["lines"]:
        old = mine.get(it["i"])
        if old is None:
            continue
        for k in old["h"]:
            if k in old:
                it[k] = old[k]
            else:
                it.pop(k, None)
        it["h"] = list(old["h"])
        if old.get("was"):
            it["was"] = dict(old["was"])
    ph = list(stored.get("h") or [])
    for k in ph:
        if k in PAGE_FIELDS and k in (stored.get("page") or {}):
            page["page"][k] = stored["page"][k]
        elif k in ("missing", "toc"):
            page[k] = stored.get(k) or []
    page["h"] = ph
    return page


def snapshot(decided, rows):
    """A page's labels from the converter's decisions: DECIDED as `decisions.collect` gives a page, ROWS the page's
    OCR rows ({row: {"bbox", "text"}}) to know each line again."""
    lines = []
    for r in sorted(rows):
        d = dict(decided["lines"].get(r) or {"r": "noise"})
        item = {"i": r, "r": d.get("r", "noise")}
        for k in ("l", "n", "p", "m", "a", "t"):
            if d.get(k) not in (None, 0, False, [], "") or (k == "p" and "p" in d):
                item[k] = d[k]
        item["b"] = [int(v) for v in rows[r]["bbox"]]
        item["o"] = rows[r]["text"]
        item["h"] = []
        lines.append(item)
    page = {"type": decided.get("type", "text")}
    if decided.get("n") is not None and decided["n"] >= 1:
        page["pn"] = str(decided["n"])
    return {"page": page, "lines": lines, "missing": list(decided.get("missing") or []),
            "toc": list(decided.get("toc") or []), "h": []}


def edit_page(data, pdf, decided, rows, edits):
    """Apply EDITS to page PDF (taking the converter's decisions as the start the first time): {"lines": {row:
    {field: value | None}}, "page": {field: value | None}, "missing": [...] | None, "toc": [...] | None}; a None
    value takes the person's change back. -> the page's labels."""
    before = copy.deepcopy(data["pages"].get(pdf))
    page = _current(data["pages"].get(pdf), decided, rows)
    by_row = {it["i"]: it for it in page["lines"]}
    base = {it["i"]: it for it in snapshot(decided, rows)["lines"]}
    for row, fields in (edits.get("lines") or {}).items():
        row = int(row)
        it = by_row.get(row)
        if it is None:
            continue
        for k, v in fields.items():
            if k not in LINE_FIELDS:
                continue
            h = set(it.get("h") or [])
            was = dict(it.get("was") or {})
            if v is None or (k == "x" and v == it.get("o")):  # back to the converter's reading
                if k in base.get(row, {}):
                    it[k] = base[row][k]
                else:
                    it.pop(k, None)
                h.discard(k)
                was.pop(k, None)
            else:
                if k not in h:
                    was[k] = it.get(k)  # what the converter had: a unit's title, a note, changed
                it[k] = v
                h.add(k)
            it["h"] = sorted(h)
            if was:
                it["was"] = was
            else:
                it.pop("was", None)
    ph = set(page.get("h") or [])
    for k, v in (edits.get("page") or {}).items():
        if k not in PAGE_FIELDS:
            continue
        if v is None:
            page["page"].pop(k, None)
            ph.discard(k)
        else:
            page["page"][k] = v
            ph.add(k)
    page["h"] = sorted(ph)
    if edits.get("missing") is not None:
        page["missing"] = [{k: m[k] for k in MISSING_FIELDS if k in m} for m in edits["missing"]
                           if isinstance(m, dict) and str(m.get("t") or "").strip()]
        ph.add("missing")
    if edits.get("toc") is not None:
        page["toc"] = edits["toc"]
        ph.add("toc")
    page["h"] = sorted(ph)
    page["reviewed"] = True
    data["pages"][pdf] = page
    _log(data, {"pdf": pdf, "before": before})
    return page


def revert_page(data, pdf):
    """The page back to the converter's own decisions."""
    if pdf in data["pages"]:
        _log(data, {"pdf": pdf, "before": copy.deepcopy(data["pages"][pdf])})
        del data["pages"][pdf]


def edit_book(data, edits):
    """Book-level corrections: {"meta": {field: value | None}, "cover": pdf page | None}."""
    before = copy.deepcopy(data["book"])
    book = data["book"]
    meta = book.setdefault("meta", {})
    for k, v in (edits.get("meta") or {}).items():
        if v in (None, "", []):
            meta.pop(k, None)
        else:
            meta[k] = v
    if "cover" in edits:
        if edits["cover"] is None:
            book.pop("cover", None)
        else:
            book["cover"] = int(edits["cover"])
    _log(data, {"book": before})


def _log(data, entry):
    entry["when"] = datetime.datetime.now().isoformat(timespec="seconds")
    data["history"].append(entry)
    del data["history"][:-HISTORY]


def undo(data):
    """Take the last change back. -> what it was about ({"pdf": n} or {"book": ...}), or None."""
    if not data["history"]:
        return None
    entry = data["history"].pop()
    if "pdf" in entry:
        if entry["before"] is None:
            data["pages"].pop(entry["pdf"], None)
        else:
            data["pages"][entry["pdf"]] = entry["before"]
    elif "book" in entry:
        data["book"] = entry["book"]
    return entry


def apply_pages(layouts, reviewed):
    """What a person set for whole pages (`labels_for`'s page fields), on the analysed pages: a page made a figure (its
    crop and rotation) or a blank page; the pictures of a text page, drawn by hand (replacing those found: the lines
    inside them are the picture's); its tables as images or as HTML."""
    from .layout import Region
    for L in layouts:
        g = (reviewed or {}).get(L.page.index)
        if not g:
            continue
        page = {k: v for k, v in (g.get("page") or {}).items() if k in (g.get("h") or [])}  # what the person set
        kind = page.get("type")
        if kind in ("figure", "cover"):
            L.kind = "figure"
            if page.get("crop"):
                L.crop = tuple(int(v) for v in page["crop"])
            if page.get("rotate") is not None:
                L.rotate, L.rotate_by_hand = int(page["rotate"]) % 360, True
        elif kind == "blank":
            L.kind = "blank"
        elif L.kind == "figure" and page.get("rotate") is not None:
            L.rotate, L.rotate_by_hand = int(page["rotate"]) % 360, True
        if L.kind == "text" and isinstance(page.get("figures"), list):
            for r in [r for r in L.regions if r.kind == "figure"]:
                L.body += r.lines  # what the found picture held is text again, unless a drawn picture holds it
                L.regions.remove(r)
            for box in page["figures"]:
                x0, y0, x1, y1 = (int(v) for v in box)
                inside = [l for l in L.body if x0 - 5 <= (l.x0 + l.x1) / 2 <= x1 + 5 and y0 - 5 <= l.yc <= y1 + 5]
                L.body = [l for l in L.body if l not in inside]
                L.regions.append(Region("figure", (x0, y0, x1, y1), inside))
            L.body.sort(key=lambda l: (l.y0, -l.x1))
        if page.get("tables") in ("image", "html"):
            L.table_mode = page["tables"]
        if page.get("keep"):
            L.keep_by_hand = True  # `order` keeps it in the book
        pn = str(page.get("pn") or "").strip(" -–—.()[]")
        if "pn" in (g.get("h") or []) and pn and is_digits(pn) and len(pn) <= 4:
            L.number_by_hand = int(ascii_digits(pn))  # the printed number as the person read it


def _iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def labels_for(data, rows_of):
    """{pdf page: page labels as `labels.load_page` gives them ("lines" as {row: label})} for the reviewed pages, with
    every line found again in the current OCR: ROWS_OF(pdf) -> {row: {"bbox", "text"}}. A row whose box moved (the OCR
    was run again) is matched to the row its box overlaps most; a line found nowhere is left out (counted in "lost")."""
    out = {}
    for pdf, page in data["pages"].items():
        if not human(page):
            continue  # looked at and found right: the converter decides it as before
        rows = rows_of(pdf)
        lines, lost, moved = {}, 0, {}
        for it in page.get("lines", []):
            row, b = it.get("i"), it.get("b")
            if row in rows and (b is None or _iou(b, rows[row]["bbox"]) >= 0.6):
                lines[row] = dict(it)
                moved[row] = row
                continue
            best = max(rows, key=lambda r: _iou(b, rows[r]["bbox"]), default=None) if b else None
            if best is not None and _iou(b, rows[best]["bbox"]) >= 0.5 and best not in lines:
                lines[best] = dict(it, i=best)
                moved[row] = best
            else:
                lost += 1
        missing = [dict(m, **{k: moved.get(m[k]) for k in ("before", "after") if m.get(k) is not None})
                   for m in page.get("missing") or []]  # the rows the added lines go by, found again too
        g = dict(page, lines=lines, lost=lost, reviewed=True, missing=missing)  # (labels.attach follows the "h" fields)
        out[pdf] = g
    return out
