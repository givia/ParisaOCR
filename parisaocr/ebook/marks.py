"""Hand marks for a book's structure (`parisaocr epub --review`, see `review`): the pages that open parts,
chapters and sections, with their titles, and the pages that are figures. Saved as OUT/NAME.marks.json, keyed
by PDF page number, with the converter's own decisions beside them (`auto`) so the two can be compared later.
A reviewed marks file is the whole truth about openings: it replaces what the rules and models decided."""
import datetime
import difflib
import json
import pathlib

from .source import Line

KINDS = ("part", "chapter", "section", "figure")


def path_for(out_dir, name):
    return pathlib.Path(out_dir) / f"{name}.marks.json"


def load(path):
    """The marks file as a dict with "pages": {pdf page: {"kind", "title"}}, or None when there is none."""
    path = pathlib.Path(path)
    if not path.exists():
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    pages = {}
    for k, v in (d.get("pages") or {}).items():
        try:
            pdf = int(k)
        except ValueError:
            continue
        if isinstance(v, dict) and v.get("kind") in KINDS:
            pages[pdf] = {"kind": v["kind"], "title": " ".join((v.get("title") or "").split())}
    d["pages"] = pages
    d["reviewed"] = True  # a marks file, however it was written, is the reader's word
    return d


def save(path, pages, auto=None):
    """Write PAGES ({pdf page: {"kind", "title"}}) and, when given, AUTO (the same for the converter's decisions)."""
    clean = {int(k): {"kind": v["kind"], "title": " ".join((v.get("title") or "").split())}
             for k, v in pages.items() if isinstance(v, dict) and v.get("kind") in KINDS}
    d = {"version": 1, "when": datetime.datetime.now().isoformat(timespec="seconds"), "reviewed": True,
         "pages": {str(k): v for k, v in sorted(clean.items())}}
    if auto is not None:
        d["auto"] = {str(k): v for k, v in sorted(((int(k), v) for k, v in auto.items()))}
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _key(s):
    return "".join(c for c in s if c.isalnum())


def _similar(a, b):
    return difflib.SequenceMatcher(None, _key(a), _key(b)).ratio()


def _title_lines(L, title, auto):
    """The page's first lines that print TITLE (to leave them out of the body); without a good match, the
    lines the rules took as the title block, if any."""
    fallback = list((auto or {}).get("title_lines") or [])
    if not title:
        return fallback
    lines = sorted(L.body, key=lambda l: l.y0)
    found, best = [], 0.0
    for l in lines[:8]:
        cand = found + [l]
        s = _similar(" ".join(x.text for x in cand), title)
        if s > best:
            found, best = cand, s
        elif found:
            break
    return found if best >= 0.5 else fallback


def apply(marks, pages, starts):
    """The openings STARTS ({book page: start dict}, the rules' result) replaced by the reviewed MARKS.
    PAGES: [(layout, book page)] in reading order. A marked page the rules also opened, with the same kind
    and title, keeps the rules' analysis of its lines; otherwise the mark decides, and the page's lines that
    print the title are left out of the body."""
    by_pdf = {L.page.index: (L, n) for L, n in pages}
    out = {}
    for pdf, m in marks["pages"].items():
        if pdf not in by_pdf or m["kind"] == "figure":
            continue
        L, n = by_pdf[pdf]
        title, auto = m["title"], starts.get(n)
        if m["kind"] == "part":
            # the part's title page; what a page prints under the title (a section's title, text) stays its text
            if auto is not None and auto["kind"] == "part" and (not title or _similar(title, _title_of(auto)) >= 0.95):
                out[n] = auto
                continue
            found = _title_lines(L, title or _title_of(auto), auto)
            out[n] = dict(kind="part", title=title or (auto or {}).get("title", ""), after_h2=[], bylines=[],
                          rest=[l for l in L.body if l not in found] if found else [])
            continue
        if auto is not None and auto["kind"] == m["kind"] and (not title or _similar(title, _title_of(auto)) >= 0.95):
            out[n] = auto
            continue
        found = _title_lines(L, title, auto)
        rest = [l for l in L.body if l not in found]
        if m["kind"] == "chapter":
            out[n] = dict(kind="chapter", title=title or _title_of(auto) or " ".join(l.text for l in found),
                          label="", title_lines=[], after_h2=[], bylines=[], rest=rest)
        else:
            box = found[0].bbox if found else (L.left, 0, L.right, L.lh or 1)
            head = [Line(title, box, 100.0, [])] if title else found
            out[n] = dict(kind="section", after_h2=head, bylines=[], rest=rest)
    return out


def _title_of(start):
    if not start:
        return ""
    return start.get("title") or " ".join(l.text for l in start.get("after_h2") or [])


def snapshot(starts, pages):
    """{pdf page: {"kind", "title"}} of the rules' openings, for the review page and the marks file."""
    by_n = {n: L for L, n in pages}
    return {by_n[n].page.index: {"kind": st["kind"], "title": _title_of(st)} for n, st in starts.items() if n in by_n}


def apply_figures(marks, layouts):
    """Pages marked as figures are figures (the whole page as the picture), whatever the layout rules saw."""
    for L in layouts:
        m = marks["pages"].get(L.page.index)
        if m and m["kind"] == "figure" and L.kind != "figure":
            L.kind, L.crop, L.rotate = "figure", None, 0
