"""Book structure from a page labeller (Gemini): the labels decide, the layout rules do not.

A labeller looks at each page image with the page's numbered OCR lines (LABELS/p-NNN.json) and says what the
page is (cover, title, imprint, contents, part, opening, text, endnotes, bibliography, index, ad, ...), its
printed page number, and for every line its role (header, pagenum, heading, byline, body, quote, epigraph,
verse, note, endnote, reference, caption, table, figure, contents, noise, other), a heading's level, the number
of a note or endnote where one starts, the note markers a line carries, and a heading's text as printed when
the OCR misread it; headings the OCR missed are listed apart, a contents page's entries too. A line's index is
its row in the page's OCR jsonl (`source.Line.row`). Two book-level answers may sit beside the page labels:
book_outline.json (every heading's final level, and for units their kind, title and author, decided for the
book as a whole) and book_meta.json (title, authors, translators, publisher ...).

On a labelled page the labels decide: running headers, page numbers, stamps and noise are left out of the
text; the footnote area is the lines labelled as notes, numbered as labelled, rows of Latin notes read left
to right; endnotes are collected for the unit they close and linked from its markers; a line's markers are
written into its text where the OCR lost them; the table of contents is the pages of that type and their
entries; a page opens a unit when the outline (or, without one, the page's own labels) puts a level-1 heading
at its head; other headings are section headings. The layout rules keep deciding what labels do not say
(page kind, regions, margins), and everything on pages without labels.
"""
import json
import pathlib
import re
from difflib import SequenceMatcher

from .notes import candidates
from .textutil import DIGITS, ascii_digits, fa_num, is_digits, latin_ratio

DROP = ("header", "pagenum", "noise")
_STARS = re.compile(r"^[\s\-–]*(\*{1,3})\s*(.*)$")
_LEAD_NUMBER = re.compile(rf"^[\s'‘’“”\"(\[\-–.]*[{DIGITS}]{{1,3}}\s*[-–—.~):,،]?\s*")
_LABEL = re.compile(r"^(فصل|بخش|قسمت|گفتار|دفتر|پیوست|ضمیمه|یادداشت|کتاب|پرده|درس)(\s|$)")
_GLUED = re.compile(rf"(?<=[^\s{DIGITS}/\-(])([{DIGITS}]{{1,3}})(?=[\s.،؛:!?؟»«)\]\u200c]|$)")
_DIGIT_RUN = re.compile(rf"(?<=[^\s{DIGITS}])(\s?)([{DIGITS}]{{1,3}})(?![{DIGITS}])")  # digits after a word, maybe spaced
_GLUED4 = re.compile(rf"(?<=[^\s{DIGITS}/\-(])([{DIGITS}]{{1,4}})(?=[\s.،؛:!?؟»«)\]°'‘’`\u200c]|$)")  # glued, up to 4 digits
_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z\-]*")
_JUNK_AFTER = re.compile(r"[°'‘’`]+")  # what the OCR makes of the rest of a raised number


def _near(run, fk):
    """RUN is FK read with one digit dropped, one changed, or one added after it."""
    if len(run) == len(fk) - 1:
        return any(fk[:i] + fk[i + 1:] == run for i in range(len(fk)))
    if len(run) == len(fk) and len(fk) >= 2:
        return sum(a != b for a, b in zip(run, fk)) == 1
    if len(run) == len(fk) + 1:
        return run.startswith(fk)
    return False


def _read(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_page(labels_dir, index):
    """The labels of page INDEX (with "lines" as {row: label}), or None when the page has no valid labels."""
    g = _read(pathlib.Path(labels_dir) / f"p-{index:03d}.json")
    if not g or not g.get("valid", True):
        return None
    g["lines"] = {it["i"]: it for it in g.get("lines", []) if isinstance(it, dict) and isinstance(it.get("i"), int)}
    return g


def book_meta(labels_dir):
    """The book's bibliographic data from book_meta.json, or {}; fields answered "none" or empty are left out."""
    g = _read(pathlib.Path(labels_dir) / "book_meta.json") or {}
    out = {}
    for k, v in (g.get("answer") or {}).items():
        if isinstance(v, list):
            v = [x for x in v if isinstance(x, str) and x.strip() and x.strip().lower() != "none"]
        elif not isinstance(v, str) or v.strip().lower() in ("", "none"):
            v = None
        if v:
            out[k] = v
    return out


def _outline(labels_dir):
    """{(page, row, k): final heading} from book_outline.json; row -1 with k for a heading the OCR missed."""
    g = _read(pathlib.Path(labels_dir) / "book_outline.json") or {}
    answer = {h.get("id"): h for h in (g.get("answer") or {}).get("headings", []) if isinstance(h, dict)}
    return {(h["page"], h["row"], h["k"]): answer[h["id"]] for h in g.get("headings", []) if h["id"] in answer}


def attach(pages, labels_dir):
    """Put the labels of LABELS_DIR on the pages and their lines; a heading the OCR missed becomes a line of its own.
    -> the number of labelled pages."""
    from .source import Line
    outline = _outline(labels_dir)
    count = 0
    for p in pages:
        g = load_page(labels_dir, p.index)
        if g is None:
            continue
        p.labelled = True
        count += 1
        page = g.get("page") or {}
        p.label_type, p.label_pn = page.get("type", "other"), page.get("pn", "")
        p.label_toc = g.get("toc") or []
        p.label_marker_words = bool(g.get("markers_with_words"))  # the labeller named the word each marker follows
        restored = []
        for l in p.lines:
            it = g["lines"].get(l.row)
            def foreign(jt):  # a margin mark, or the number of another note than the line's own
                return jt.get("r") in ("header", "pagenum", "noise") or (
                    jt.get("r") in ("note", "endnote") and jt.get("n") and it and it.get("n") and jt["n"] != it["n"])
            if getattr(l, "unjoined", None) and any(foreign(g["lines"].get(jr) or {}) for jr in getattr(l, "joined", ())):
                l.text, l.words, l.bbox, box = l.unjoined  # the box joined to it does not belong to it: both as read
                l.joined = ()
                restored.append(box)
            for jr in getattr(l, "joined", ()):  # a note number source.join_number_boxes put into this line: its label
                jt = g["lines"].get(jr)           # (a note starting, with its number) carries over
                if jt and jt.get("r") in ("note", "endnote") and jt.get("n"):
                    if it is None or it.get("r") not in ("note", "endnote"):
                        it = dict(jt)
                    elif not it.get("n"):
                        it = dict(it, n=jt["n"])
            if it is not None:
                _set(l, it, outline.get((p.index, l.row, -1)))
        for box in restored:  # with its own label
            p.lines.append(box)
            if g["lines"].get(box.row) is not None:
                _set(box, g["lines"][box.row], outline.get((p.index, box.row, -1)))
        by_row = {l.row: l for l in p.lines}
        for k, m in enumerate(g.get("missing") or []):
            if m.get("r") not in ("heading", "byline") or not (m.get("t") or "").strip():
                continue
            below = by_row.get(m.get("before"))
            if below is not None:
                h = max(8, below.h)
                box = (below.x0, max(0, below.y0 - 1.5 * h), below.x1, max(1, below.y0 - 0.3 * h))
            else:
                box = (0.25 * p.width, 0.04 * p.height, 0.75 * p.width, 0.07 * p.height)
            line = Line(m["t"].strip(), tuple(int(v) for v in box), 100.0, [], row=-1)
            _set(line, {"r": m["r"], "l": m.get("l") or 1}, outline.get((p.index, -1, k)))
            p.lines.append(line)
    return count


def _set(l, it, final=None):
    r = it.get("r") or "other"
    num = it.get("n")
    l.label_role = l.role = r
    l.level = int(it.get("l") or 0) if r == "heading" else 0
    if final is not None and r == "heading":
        l.level = int(final.get("level") or 0)
        if l.level == 0:  # not a heading of the book (a running header taken for one, a title's later lines)
            l.label_role = l.role = "header"
        l.unit_kind, l.unit_title, l.unit_author = (final.get("kind") or "").replace("none", ""), final.get("title") or "", final.get("author") or ""
    l.note_num = int(num) if r in ("note", "endnote") and isinstance(num, int) and not isinstance(num, bool) else None
    l.para = bool(it.get("p"))
    l.markers = [int(k) for k in it.get("m") or [] if isinstance(k, int)]
    words = it.get("a")
    l.marker_words = [w for w in words if isinstance(w, str)] if isinstance(words, list) else []
    if r in ("heading", "byline") and (it.get("t") or "").strip():
        l.ocr_text, l.text = l.text, it["t"].strip()  # the labeller read the line as printed
    l.p_head = 1.0 if l.label_role == "heading" else 0.0
    l.p_note = 1.0 if r == "note" else 0.0
    l.p_start = (0.5 if num is None else 1.0 if num and num > 0 else 0.0) if r == "note" else 0.0


def _role(l):
    return getattr(l, "label_role", None)


def _inside(a, b, tol=4):
    return a[0] >= b[0] - tol and a[1] >= b[1] - tol and a[2] <= b[2] + tol and a[3] <= b[3] + tol


def relayout(L, metrics):
    """Header, page number, body, footnotes and endnotes of a labelled text page from its labels."""
    p = L.page
    if not getattr(p, "labelled", False):
        return
    if L.kind != "text":
        if any(_role(l) == "heading" for l in p.lines):  # a title page the layout took for a picture
            L.kind = "text"
            L.body = [l for l in p.lines if _role(l) not in DROP]
        else:
            return
    if L.header is not None:
        parts = [l for l in p.lines if _inside(l.bbox, L.header.bbox)]
        if parts and any(_role(l) not in (None,) + DROP for l in parts):
            L.body += [l for l in parts if _role(l) not in DROP]
            L.header = None
            L.number_candidates = []
    for r in L.regions:  # lines the layout put in a picture or table that the labeller reads as text
        back = [l for l in r.lines if _role(l) not in (None, "figure", "table", "caption")]
        r.lines = [l for l in r.lines if l not in back]
        L.body += back
    seen = {id(l) for l in L.body + L.notes}
    extra = [l for l in p.lines if l.row == -1 and id(l) not in seen]  # headings the OCR missed
    keep, notes, endnotes = [], [], []
    for l in L.body + L.notes + extra:
        r = _role(l)
        if r in DROP:
            continue
        (notes if r == "note" else endnotes if r == "endnote" else keep).append(l)
    pn = (getattr(p, "label_pn", "") or "").strip(" -–—.()[]")
    if is_digits(pn) and len(pn) <= 4:
        L.number_candidates = [int(ascii_digits(pn))]
    key = lambda l: (l.y0, -l.x1)
    L.body, L.notes = sorted(keep, key=key), sorted(notes, key=key)
    L.endnote_lines = sorted(endnotes, key=key)
    if L.body:
        metrics(L)
    L.label_notes = split(L.notes)
    L.labelled = True


def _rows(lines):
    rows = []
    for l in sorted(lines, key=lambda l: l.yc):
        row = next((r for r in rows if abs(r[0].yc - l.yc) < 0.5 * max(r[0].h, l.h)), None)
        if row:
            row.append(l)
        else:
            rows.append([l])
    return rows


def split(lines):
    """(lead, notes) as `notes.split_notes` gives them, from the labeller's note numbers: a line with a number
    starts that note, other lines continue the note before them (or, before the first, the previous page's)."""
    ordered = []
    for row in _rows(lines):
        ltr = latin_ratio(" ".join(l.text for l in row)) > 0.5
        ordered += sorted(row, key=(lambda l: l.x0) if ltr else (lambda l: -l.x1))
    lead, found = [], []
    for l in ordered:
        text = l.text.strip()
        k = getattr(l, "note_num", None)
        star = _STARS.match(text)
        if k and k > 0:
            rest = next((r for num, r, _ in candidates(text) if num == k), None)
            if rest is None:
                rest = _LEAD_NUMBER.sub("", text, count=1) if re.match(rf"^[\s'‘’“”\"(\[\-–.]*[{DIGITS}]", text) else text
            found.append([k, rest.strip(), False])
        elif star and not k:
            found.append([len(star.group(1)), star.group(2).strip(), True])
        elif found:
            found[-1][1] = (found[-1][1] + " " + text).strip()
        else:
            lead.append(text)
    return lead, [tuple(n) for n in found]


def marked_text(l):
    """The line's text with the note markers the labeller saw on it, as digits glued to the word they follow: a
    marker the OCR read is left as it is; one it read in part ("۱۷" for "۱۷۶", a space before it) is completed, and
    split from a next word glued to it (after a Latin word glued to it, the marker goes after that word); one it
    misread ("۳۳" for ۳۲, "۱۷" for ۱۰۷) is replaced, not doubled; a marker the image search found (MARK) takes the
    next number; the rest go at the end of the line. Image marks left over go when the page's labels account for
    all its notes (l.drop_marks, set by structure.text_page)."""
    from .markers import MARK  # not at the top: markers imports layout, which imports this module
    text, want = l.text, getattr(l, "markers", None)
    if not want or (is_digits(text.strip()) and [int(ascii_digits(text.strip()))] == want):
        return text  # no markers, or a marker the OCR boxed apart as a line of its own
    words = getattr(l, "marker_words", None) or []
    if len(words) == len(want):
        placed = _place_after_words(text, want, words)
        if placed is not None:
            return placed
    pos, tail = 0, []
    for k in want:
        fk = fa_num(k)
        glued = next((m for m in _GLUED.finditer(text, pos) if int(ascii_digits(m.group(1))) == k), None)
        if glued is not None:
            pos = glued.end()
            continue
        part = [m for m in _DIGIT_RUN.finditer(text, pos) if fk.startswith(m.group(2)) or fk.endswith(m.group(2))]
        if part:  # read in part
            m = part[-1] if len(want) == 1 else part[0]
            a, end = m.start(), m.end()
            if a >= 1 and text[a - 1] == "(" and not text[end:].lstrip(DIGITS).startswith(")"):
                a -= 1  # a "(" the OCR read before the raised digits
                while a > 0 and text[a - 1] == " ":
                    a -= 1
            j = _JUNK_AFTER.match(text, end)
            end = j.end() if j else end
            after = text[end:]
            lat = _LATIN_RUN.match(after)
            if lat:  # "واژه۲M": the marker follows the Latin word glued to it
                text = text[:a] + m.group(1) + lat.group(0) + fk + after[lat.end():]
                pos = a + len(m.group(1)) + lat.end() + len(fk)
                continue
            sep = " " if after[:1].isalpha() else ""  # "نام۲سپس": the next word glued to the marker
            text = text[:a] + fk + sep + after
            pos = a + len(fk) + len(sep)
            continue
        # misread: a run one digit off the marker ("۳۳" for ۳۲, "۱۷" for ۱۰۷), glued to its word or set apart after
        # closing punctuation or an image mark, and not part of a number of the text (after ":", ص, ج, ٪, or tied to
        # other digits by , ٫ / -); nothing once a marker of the line had to be appended at its end
        cand = None
        if not tail:
            runs = [(m.start(1), m.end(1)) for m in _GLUED4.finditer(text, pos)
                    if int(ascii_digits(m.group(1))) not in want and _near(m.group(1), fk)]
            runs += [(m.start(1), m.end(2)) for m in _DIGIT_RUN.finditer(text, pos)  # with its space: glued back
                     if m.group(1) and text[:m.start()].rstrip()[-1:] in _CLOSE + MARK
                     and int(ascii_digits(m.group(2))) not in want and _near(m.group(2), fk)]
            runs = sorted(r for r in runs if _free_number(text, *r))
            if runs:
                cand = runs[0]
        if cand is not None and MARK in text[pos:]:
            # the image search found a mark on the line: a misread candidate wins only where a marker stands (after
            # closing punctuation or the mark, or in the mark's word); a number of the running text does not
            a, end = cand
            lead = text[:a].rstrip(" ")[-1:]
            w0, w1 = text.rfind(" ", 0, a + (text[a] == " ")) + 1, text.find(" ", end)
            if lead not in _CLOSE + MARK and MARK not in text[w0:w1 if w1 >= 0 else None]:
                cand = None
        if cand is not None:
            a, end = cand
            j = _JUNK_AFTER.match(text, end)
            end = j.end() if j else end
            b = a
            while b > 0 and text[b - 1] == " ":
                b -= 1
            if b > 0 and text[b - 1] == MARK:  # the image search's mark for this same marker, just before it
                a, b = b - 1, b - 1
                text = text[:a] + text[cand[0]:]
                end -= cand[0] - a
            sep = " " if text[end:end + 1].isalpha() else ""  # the next word glued to the misread marker
            text = text[:a] + fk + sep + text[end:]
            pos = a + len(fk) + len(sep)
            continue
        if MARK in text[pos:]:
            i = text.index(MARK, pos)
            text = text[:i] + fk + text[i + 1:]
            pos = i + len(fk)
            continue
        tail.append(fk)  # written at the line's end after the loop: a later marker may still be where the OCR read it
    for fk in tail:
        text = text.rstrip()
        if text and text[-1] in DIGITS:
            text += "\u200c"  # never glue a marker onto other digits
        text += fk
    if getattr(l, "drop_marks", False):
        # the page's labels account for its notes: image marks left over are false, but only once every labelled
        # marker of the line is one the linker will find (else a mark may be how it links)
        from .structure import _MARKER, _marker_num  # not at the top: structure imports this module
        if set(want) <= {_marker_num(m) for m in _MARKER.finditer(text.replace(MARK, ""))}:
            text = text.replace(MARK, "")
    return text


_CLOSE = ".،؛:!?؟»)]\"'”’…"
_NUMBER_JOIN = ",٫/-–—،."  # between two digit runs: one number of the text (4,4 ۱۸۰۱-۱۸۹۰ ۱۳۹۵/۹ ۵.۲)


def _free_number(text, a, b):
    """The digit run TEXT[a:b] stands alone: not after ":" (a page or verse reference), ص, ج, ٪ or %, and not tied to
    other digits by , ٫ / or -."""
    before, after = text[:a].rstrip(" "), text[b:].lstrip(" ")
    if before[-1:] in (":", "ص", "ج", "٪", "%"):
        return False
    tied_before = len(before) >= 2 and before[-1] in _NUMBER_JOIN and before[-2] in DIGITS
    tied_after = len(after) >= 2 and after[0] in _NUMBER_JOIN and after[1] in DIGITS
    return not (tied_before or tied_after)
_NORM = str.maketrans({"\u200c": None, "\u0640": None, "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا",
                       **{chr(c): None for c in range(0x064B, 0x0653)}})  # ZWNJ, tatweel, letter variants, short vowels


def _find_word(text, word, pos):
    """(start, end) of WORD in TEXT at or after POS: as written, else with ZWNJ, short vowels and letter variants
    ignored; None when it is not there."""
    i = text.find(word, pos)
    if i >= 0:
        return i, i + len(word)
    target = word.translate(_NORM)
    if not target:
        return None
    norm, where = [], []  # the normalized text and, for each of its characters, its index in TEXT
    for k in range(pos, len(text)):
        c = text[k].translate(_NORM)
        norm.append(c)
        where += [k] * len(c)
    j = "".join(norm).find(target)
    return (where[j], where[j + len(target) - 1] + 1) if j >= 0 else None


def _place_after_words(text, want, words):
    """TEXT with each marker of WANT written right after the word the labeller said it follows (WORDS, in order):
    after the word's closing punctuation, in place of digits the OCR glued or spaced there (the marker misread,
    "۳۳" for ۳۲), split from a next word glued to it; other copies of the number glued elsewhere on the line go,
    and so does the image search's mark for the same marker (in the word's token). Marks the image search found
    elsewhere on the line stay: markers the labeller missed. None when a word is not found (the caller then falls
    back on the OCR's digits)."""
    from .markers import MARK
    pos, spans = 0, []
    for k, w in zip(want, words):
        fk = fa_num(k)
        glued = next((m for m in _GLUED.finditer(text, pos) if int(ascii_digits(m.group(1))) == k), None)
        if glued is not None:  # the OCR read the marker glued to its word: more precise than a named word
            spans.append((glued.start(), glued.end(), fk))
            pos = glued.end()
            continue
        w = w.strip().strip(_CLOSE + "«(“‘[ ")  # the word alone: labellers often add its punctuation, in another form
        if not w or is_digits(w):
            return None  # no word, or the marker itself given as the word
        hit = _find_word(text, w, pos)
        if hit is None:
            return None
        e = hit[1]
        t_end = e
        while t_end < len(text) and not text[t_end].isspace():
            t_end += 1
        if MARK in text[e:t_end]:  # the image search found this same marker after the word
            text = text[:e] + text[e:t_end].replace(MARK, "") + text[t_end:]
        fk = fa_num(k)
        p = hit[1]
        q = p
        while q < len(text) and text[q] in DIGITS:  # digits right after the word, before its punctuation
            q += 1
        if q == p:
            while p < len(text) and text[p] in _CLOSE:
                p += 1
            q = p
            if text[q:q + 1] == " " and q + 1 < len(text) and text[q + 1] in DIGITS:  # "گفت. ۱۷": spaced
                r = q + 1
                while r < len(text) and text[r] in DIGITS:
                    r += 1
                if r - q - 1 <= 4 and (r == len(text) or not text[r].isalpha()):
                    q = r
            while q < len(text) and text[q] in DIGITS:
                q += 1
        text = text[:p] + fk + text[q:]
        end = p + len(fk)
        if end < len(text) and text[end].isalpha():
            text = text[:end] + " " + text[end:]  # "گفت.»۱۰۸سپس": the next word glued to the marker
        spans.append((p, end, fk))
        pos = end
    placed = {fk for _, _, fk in spans}
    cut = [m.span() for m in _GLUED.finditer(text)  # the same numbers glued elsewhere on the line: copies the OCR
           if m.group(1) in placed and not any(a <= m.start() < b for a, b, _ in spans)]  # misplaced, cut from the end
    for a, b in reversed(cut):
        text = text[:a] + text[b:]
    return text


def contents_pages(pages):
    """The book pages the labeller calls the table of contents (or a list of figures)."""
    return {n for L, n in pages if getattr(L.page, "label_type", None) == "contents"}


def toc_entries(L):
    """The contents entries the labeller read on a contents page: (title, printed page or None, level)."""
    out = []
    for e in getattr(L.page, "label_toc", []) or []:
        title = " ".join((e.get("t") or "").split())
        if not title:
            continue
        pg = (e.get("pg") or "").strip(" .-–")
        out.append((title, int(ascii_digits(pg)) if is_digits(pg) else None, int(e.get("l") or 1)))
    return out


def _clean(text):
    from .markers import MARK
    t = " ".join(text.replace(MARK, "").split())
    return re.sub(rf"(?<=\S)[{DIGITS}]{{1,2}}$", "", t)  # a note marker on the title


def openings(pages):
    """{page number: start} (as `structure.chapter_openings` gives them) for the labelled pages headed by a unit's
    title (a level-1 heading among their first three lines, after the outline): its title, the label above it,
    the bylines and sub-headings under it, the rest of the page. A part's title page opens a part."""
    starts = {}
    for L, n in pages:
        if L.kind != "text" or not getattr(L, "labelled", False) or not L.body:
            continue
        body = sorted(L.body, key=lambda l: (l.y0, -l.x1))
        first = next((i for i, l in enumerate(body[:3]) if _role(l) == "heading" and l.level == 1), None)
        if first is None:
            continue
        run = []
        for l in body[first:]:
            if _role(l) not in ("heading", "byline", "epigraph"):
                break
            run.append(l)
        title_lines = [l for l in run if _role(l) == "heading" and l.level == 1]
        head = title_lines[0]
        after = [l for l in run if _role(l) == "heading" and l.level != 1]
        bylines = [l for l in run if _role(l) in ("byline", "epigraph")]
        title = _clean(getattr(head, "unit_title", "") or " ".join(l.text for l in title_lines))
        label = ""
        if len(title_lines) >= 2 and _LABEL.match(title_lines[0].text.strip()) and len(title_lines[0].text.split()) <= 3:
            label = _clean(title_lines[0].text)
        # the outline's kind when there is one (a chapter's opening page that lists its sections looks like a part's
        # page to a reader of one page), else the page's
        kind = head.unit_kind if hasattr(head, "unit_kind") else ("part" if getattr(L.page, "label_type", "") == "part" else "")
        kind = "part" if kind == "part" else "chapter"
        if kind == "part":
            starts[n] = dict(kind="part", title=title, after_h2=[], bylines=[], rest=[])
            continue
        # a note marker on the unit's title: kept, so the displayed heading links the note, when the title lines are
        # the whole title (a line the outline set to level 0 is gone from them: then the outline's title is shown)
        marked = [l for l in title_lines if getattr(l, "markers", None)]
        if marked and SequenceMatcher(None, _clean(" ".join(l.text for l in title_lines)), title).ratio() < 0.8:
            marked = []
        for l in marked:
            l.text, l.markers = marked_text(l), []
        starts[n] = dict(kind="chapter", title=title, label=label, title_lines=title_lines if marked else [], after_h2=after,
                         bylines=body[:first] + bylines, rest=body[first + len(run):])
    return starts


def endnotes(pages, starts):
    """{unit page: {number: (text, page)}}: the endnotes on the labelled pages, each list given to the unit it
    closes (the last unit opened before it)."""
    out, unit, last = {}, None, None
    for L, n in pages:
        if n in starts:
            unit = n
        lines = getattr(L, "endnote_lines", None)
        if not lines:
            continue
        lead, found = split(lines)
        notes = out.setdefault(unit, {})
        if lead and last in notes:
            text, page = notes[last]
            notes[last] = (text + " " + " ".join(lead), page)
        for k, text, _ in found:
            if k not in notes:
                notes[k] = (text, n)
            last = k
    return out
