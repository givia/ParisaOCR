"""From ordered page layouts to a book: front matter, chapters, paragraphs, notes.

Everything is decided from line geometry relative to the page's text block,
checked against the printed table of contents where the book has one:

- The contents pages are parsed into (title, page, level) entries and left out
  of the text; a list of maps or figures gives the captions of figure pages.
- A chapter opens on a page without a running header whose text starts low.
  Its title block is the lines above the first line of running text; the
  tallest of them are the title, and a label line ("فصل اول") above them joins
  it. A contents entry on that page corrects or replaces the title.
- A section heading is a short line of body size after a gap of a line or more,
  or a line that matches a contents entry for that page.
- A block quote is indented on both sides; verse is a line split by a wide gap
  in the middle (`layout.hemistich_gap`).
- A paragraph starts at an indented line or after a short line; a page that
  starts with an unindented line continues the paragraph from the page before.
- Two-column lists (bibliographies) pair each author with the lines beside and
  below it.

Notes are read from below the separator rule, numbered as the page prints
them; a line whose number does not continue the sequence is a continuation
line ("ص ۱۱-۲۲"). Markers are the digits glued to a word ("غیره.۱") and the
marks `markers` found in the image; a marker links to the note with its
number on the same page, and an unnumbered mark to the next unlinked note.
"""
import collections
import difflib
import re
from dataclasses import dataclass, field

import numpy as np

from .markers import MARK
from .notes import split_notes
from .source import Line
from .textutil import DIGITS, ascii_digits, fa_num, latin_ratio, normalize, to_int


# ---- the book model ---------------------------------------------------------------------------

@dataclass
class Note:
    id: str
    page: int
    num: int
    text: str
    ltr: bool = False
    linked: bool = False


@dataclass
class NoteRef:
    note: Note


@dataclass
class PageMark:
    number: int


@dataclass
class Span:
    """Left-to-right text inside a right-to-left paragraph."""
    text: str


@dataclass
class Block:
    kind: str  # p quote verse poem h2 byline caption sep table figure gap bib lines mark
    items: list = field(default_factory=list)  # inline content: str | Span | NoteRef | PageMark
    ltr: bool = False
    rows: list = field(default_factory=list)  # verse: [(first, second)], poem: [items | "" (stanza break)], table: [[cell]], lines: [text]
    image: tuple = None  # figure: (layout, crop box, rotation)
    lead: str = ""  # bib: the author


@dataclass
class Chapter:
    title: str  # for the table of contents
    page: int
    label: str = ""  # "فصل اول"
    heading: list = field(default_factory=list)  # inline items of the displayed title
    blocks: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    kind: str = "chapter"  # chapter | front | part
    part: str = ""  # a group heading from the contents ("ضمایم چاپ دوم")


@dataclass
class TocEntry:
    title: str
    page: int
    level: int
    list: str = "contents"  # contents | figures


@dataclass
class Book:
    chapters: list
    toc: list
    cover: object = None
    report: dict = field(default_factory=dict)


# ---- helpers ----------------------------------------------------------------------------------

_MARKER = re.compile(rf"(?<=[^\s{DIGITS}/\-(])([{DIGITS}]{{1,3}})(?=[\s.،؛:!?؟»«)\]]|$)"
                     rf"|(?<=[^\s*])(\*{{1,3}})(?=[\s.،؛:!?؟»«)\]]|$)|{MARK}"
                     # loose: a marker the OCR set apart from its word, "کرمچاله‌ها ۱،", "گردید. ۱۵۲ مدّتی"; taken only
                     # when the page has an unlinked note with that number
                     rf"|(?<=[^\s{DIGITS}])\s([{DIGITS}]{{1,3}})(?=[.،؛:!?؟»)\]]|\s*$)"
                     rf"|(?<=[.،؛:!؟»)])\s([{DIGITS}]{{1,3}})(?=\s)")
_LABEL_WORDS = "فصل|بخش|قسمت|گفتار|دفتر|پیوست|ضمیمه"
_LABEL = re.compile(rf"^({_LABEL_WORDS})\s+\S+")
_LEADERS = re.compile(r"(?:\s*[.…·])+\s*$|(?:\s*[.…·]){2,}")
_END_PUNCT = (".", "،", ":", "؛", "!", "»", ")")
_FRACTION = re.compile(rf"[{DIGITS}]+/[{DIGITS}]+")
_FIGURE_LIST = re.compile(r"تصاویر|تصویرها|عکس|نقشه|اشکال|شکل|نمودار|جدول|جداول")  # "فهرست نقشه‌ها" etc.


def _marker_num(m):
    """The note number a `_MARKER` match stands for: its digits, or the count of its asterisks
    ("*", "**" number a page's notes 1, 2); None for a mark found in the image."""
    if m.group(1) or m.group(3) or m.group(4):
        return to_int(m.group(1) or m.group(3) or m.group(4))
    return len(m.group(2)) if m.group(2) else None


def _letters(s):
    return "".join(c for c in s if c.isalpha())


def _similar(a, b):
    a, b = _letters(a), _letters(b)
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0


def _title_start(a, b):
    """One title is the other's beginning, letters compared (a long title cut short in the contents)."""
    a, b = sorted((_letters(a), _letters(b)), key=len)
    return len(a) >= 5 and difflib.SequenceMatcher(None, a, b[:len(a)], autojunk=False).ratio() >= 0.85


def _same_title(a, b):
    """Titles with the same number, if any, and nearly the same letters ("فصل ۱" is not "فصل ۱۰")."""
    digits = [ascii_digits("".join(c for c in x if c in DIGITS)) for x in (a, b)]
    return digits[0] == digits[1] and _similar(a, b) >= 0.8


def _centered(line, width, tol=0.08):
    """Centred on the page (not only inside the text block): both margins wide and about equal."""
    left, right = line.x0 / width, 1 - line.x1 / width
    return left > 0.12 and right > 0.12 and abs(left - right) < tol


def _rows(lines, tol=0.5):
    """Lines grouped into rows by vertical centre, each row ordered right to left."""
    rows = []
    for l in sorted(lines, key=lambda l: l.yc):
        row = next((r for r in rows if abs(r[0].yc - l.yc) < tol * max(r[0].h, l.h)), None)
        if row:
            row.append(l)
        else:
            rows.append([l])
    return [sorted(r, key=lambda l: -l.x1) for r in rows]


# ---- table of contents ------------------------------------------------------------------------

def _toc_number(text):
    """A page number in a contents line: "..۱۰۲", "- ۲۱۵", "۳.۹" (a Persian zero is a dot)."""
    t = text.strip(" .…-–—:")
    if not t or len(t) > 5 or not all(c in DIGITS + "." for c in t):
        return None
    return int(ascii_digits(t.replace(".", "0")))


def _trailing_number(text):
    m = re.search(rf"(?:[.…\s\-–|]+)([{DIGITS}][{DIGITS}.]*)\s*$", text)
    if m and _toc_number(m.group(1)) is not None:
        return text[:m.start()], _toc_number(m.group(1))
    return text, None


def is_toc_page(L, prev_is_toc, early=False):
    """A contents page: page numbers beside most entries (in a column, or after leader dots or a bar),
    under a heading "فهرست ...", continuing a contents page, or rising down the page ("فصل‌های کتاب"
    headings exist too; an index's numbers do not rise). Near the start of the book (EARLY), a page headed
    "فهرست" whose lines are short entries is a contents page without page numbers, and so is a page of short
    entries that continues one."""
    if L.kind != "text" or len(L.body) < 5:
        return False
    W = L.page.width
    if early:
        lines = sorted(L.body, key=lambda l: l.y0)
        widest = max(l.w for l in lines)
        short = sum(l.w < 0.85 * widest for l in lines[1:]) >= 0.7 * (len(lines) - 1)
        headed = lines[0].text.strip().startswith("فهرست") and len(lines[0].text.split()) <= 3
        if short and len(lines) >= 5 and (headed or prev_is_toc):
            return True
    nums = [l for l in L.body if _toc_number(l.text) is not None and l.w < 0.12 * W]
    trailing = [l for l in L.body if _trailing_number(l.text)[1] is not None and re.search(r"[.…]{3,}|\|", l.text)]
    if len(nums) + len(trailing) < max(4, 0.3 * len(L.body) / 2):
        return False
    if prev_is_toc or any(l.text.strip().startswith("فهرست") for l in sorted(L.body, key=lambda l: l.y0)[:3]):
        return True
    pages = [_toc_number(l.text) if l in nums else _trailing_number(l.text)[1] for l in sorted(nums + trailing, key=lambda l: l.y0)]
    rising = sum(b >= a for a, b in zip(pages, pages[1:]))
    return len(pages) >= 5 and len(pages) >= 0.5 * len(L.body) and rising >= 0.8 * (len(pages) - 1)


def parse_toc(L, list_kind):
    """Entries of one contents page, and the list kind in force at its end ("contents" or "figures")."""
    W = L.page.width
    nums = [l for l in L.body if _toc_number(l.text) is not None and l.w < 0.12 * W]
    # left out: the column heading ("صفحه") and lines in another script (an imprint of the original
    # edition printed under the contents)
    rows = [r for r in _rows([l for l in L.body if l not in nums])
            if " ".join(l.text for l in r).strip() not in ("صفحه", "صفحات", "ص")
            and latin_ratio(" ".join(l.text for l in r)) <= 0.5]

    def numbered(r):
        return any(abs(n.yc - r[0].yc) < 0.6 * max(n.h, r[0].h) for n in nums) \
            or _trailing_number(" ".join(l.text for l in r))[1] is not None
    entry_rows = [r for r in rows if numbered(r)] or rows  # the margins of the list are those of its entries
    right = max((r[0].x1 for r in entry_rows), default=0)
    left = min((min(l.x0 for l in r) for r in entry_rows), default=0)
    # a row of a contents page set in two columns holds two entries: read right to left, each title ends at
    # the page number to its left
    split_rows = []
    for r in rows:
        nums_in = sorted((n for n in nums if abs(n.yc - r[0].yc) < 0.6 * max(n.h, r[0].h)), key=lambda n: -n.x1)
        if len(nums_in) >= 2:
            parts, cur = [], []
            for x in sorted(r + nums_in, key=lambda l: -l.x1):
                if x in nums_in:
                    if cur:
                        parts.append((cur, x))
                    cur = []
                else:
                    cur.append(x)
            if cur:
                parts.append((cur, None))
            if len(parts) >= 2:  # a title on each side of a page number: two entries
                split_rows += [(seg, num) for seg, num in parts]
            else:
                split_rows.append((r, None))
        else:
            split_rows.append((r, None))
    if any(num is not None for _, num in split_rows):
        # column by column: entries of the right column first
        split_rows.sort(key=lambda sn: (-round(sn[0][0].x1 / (0.25 * W)), sn[0][0].yc))
    raw = []
    used = set()
    for r, pair_num in split_rows:
        text = " ".join(l.text for l in r).strip()
        if text.startswith("فهرست") and _centered(r[0], W, 0.15) and len(r) == 1:
            list_kind = "figures" if _FIGURE_LIST.search(text) else "contents"
            continue
        num = pair_num or min((n for n in nums if id(n) not in used and abs(n.yc - r[0].yc) < 0.6 * max(n.h, r[0].h)),
                              key=lambda n: abs(n.yc - r[0].yc), default=None)
        if num is not None:
            used.add(id(num))
        page = _toc_number(num.text) if num else None
        if page is None:
            text, page = _trailing_number(text)
        leaders = bool(re.search(r"[.…]\s*$", text))
        title = _LEADERS.sub(" ", text).strip(" .…:|")
        level = 1 if right - r[0].x1 < 0.04 * W else 2
        full = min(l.x0 for l in r) - left < 0.05 * W
        raw.append((title, page, level, leaders, full, list_kind, r[0].yc))
    # a page number whose title the OCR did not read: an entry without a title, placed by its number
    raw += [("", _toc_number(n.text), 1, False, False, list_kind, n.yc) for n in nums if id(n) not in used]
    if not any(num is not None for _, num in split_rows):
        raw = sorted(raw, key=lambda x: x[6])
    raw = [x[:6] for x in raw]
    entries = []
    for i, (title, page, level, leaders, full, kind) in enumerate(raw):
        if entries and entries[-1].page is None and entries[-1].level == level and raw[i - 1][4] \
                and not raw[i - 1][3] and page is not None:
            entries[-1].title += " " + title  # a title wrapped onto a second row
            entries[-1].page = page
            continue
        entries.append(TocEntry(title, page, level, kind))
    return entries, list_kind


def _drop_unordered(entries):
    """A contents page number smaller than one listed before it is misread, usually cut short ("۹" for
    "۳۰۹" on a small scan); it is dropped rather than sending a chapter to the wrong page."""
    for kind in ("contents", "figures"):
        top = 0
        for e in entries:
            if e.list == kind and e.page is not None:
                if e.page < top:
                    e.page = None
                else:
                    top = e.page


def _number_bare_labels(entries):
    """Chapters listed by their label alone ("فصل") when the OCR lost the chapter number after it (large
    or bold digits): numbered in the order of the list, which is the order of the chapters."""
    groups = {}
    for e in entries:
        m = re.fullmatch(rf"({_LABEL_WORDS})(?:\s+[{DIGITS}]{{1,3}})?", e.title.strip())
        if e.list == "contents" and m:
            groups.setdefault(m.group(1), []).append(e)
    for label, es in groups.items():
        if len(es) >= 2 and any(e.title.strip() == label for e in es):
            for i, e in enumerate(es, 1):
                e.title = f"{label} {fa_num(i)}"


def _fix_toc_numbers(entries, max_page):
    """A leader dot next to the number reads as a Persian zero: "۱۸۰۰" for 180. Such numbers are too
    large for the book or break the order of the list."""
    for kind in ("contents", "figures"):
        es = [e for e in entries if e.list == kind and e.page is not None]
        for i, e in enumerate(es):
            nxt = next((x.page for x in es[i + 1:] if x.page <= max_page), None)
            if e.page % 10 == 0 and (e.page > max_page or (nxt is not None and e.page > nxt and e.page // 10 <= nxt)):
                e.page //= 10
            if e.page > max_page:
                e.page = None


# ---- chapter openings -------------------------------------------------------------------------

def title_block(L):
    """(before, title, after, rest) of a chapter opening: the lines above the first line of running text,
    split around the title (its tallest lines, with a label line such as "فصل اول" above them)."""
    body = [l for l in sorted(L.body, key=lambda l: l.y0) if (l.h >= 0.5 * L.lh or len(l.text) > 2) and l.conf >= 85]
    pitch = L.pitch or 1.3 * L.lh
    k = next((i for i, l in enumerate(body) if l.w >= 0.75 * L.width and l.h < 1.3 * L.lh
              and (i + 1 == len(body) or body[i + 1].yc - l.yc < 1.25 * pitch)), len(body))
    head = body[:k]
    if not head:
        return None
    big = [l.h >= 1.1 * L.lh for l in head]
    if any(big):
        a = b = big.index(True)
        if _LABEL.match(head[a].text) and a + 1 < len(head) and big[a + 1]:
            b += 1  # a large label ("فصل ۳") and the title under it
        # further title lines follow closely; a large line after a gap is an epigraph or a subtitle
        while b + 1 < len(head) and big[b + 1] and head[b + 1].y0 - head[b].y1 < 0.8 * head[b].h:
            b += 1
    else:
        a = b = next((i for i, l in enumerate(head) if _centered(l, L.page.width, 0.15)), 0)
    if a == b and _LABEL.match(head[a].text) and len(head[a].text.split()) <= 3 and a + 1 < len(head):
        b += 1  # a label alone ("فصل اوّل"): the line under it is the title, in type no larger than the text
    while a > 0 and _LABEL.match(head[a - 1].text) and head[a].y0 - head[a - 1].y1 < 4 * L.lh:
        a -= 1
    return head[:a], head[a:b + 1], head[b + 1:], body[k:]


# ---- two-column lists -------------------------------------------------------------------------

def _two_columns(L, lines):
    """For a list set in two columns (author | entry), the x range between them; None otherwise."""
    W = L.page.width
    if len(lines) < 6:
        return None
    narrow = [l for l in lines if l.w < 0.65 * W]  # long author lines may cross the gutter
    if len(narrow) < 0.6 * len(lines):
        return None
    cover = np.zeros(W + 1, int)
    for l in narrow:
        cover[max(0, l.x0):l.x1] += 1
    ok = cover <= max(1, int(0.08 * len(narrow)))
    ok[:int(0.15 * W)] = False
    ok[int(0.85 * W):] = False
    d = np.diff(np.concatenate(([0], ok.astype(np.int8), [0])))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    if not len(starts):
        return None
    i = int(np.argmax(ends - starts))
    a, b = int(starts[i]), int(ends[i])
    if b - a < 0.04 * W:
        return None
    right = [l for l in lines if l.x0 >= a]
    left = [l for l in lines if l.x1 <= b]
    if len(right) < 3 or len(left) < 3:
        return None
    return a, b


def _join_rows(lines):
    """Pieces of one printed line that the OCR returned separately (split around a stacked fraction),
    joined right to left; a fraction bar read as "-" after the fraction is dropped."""
    rows = []
    for l in sorted(lines, key=lambda l: l.y0):
        for r in rows:
            y0, y1 = max(x.y0 for x in r), min(x.y1 for x in r)
            if min(y1, l.y1) - max(y0, l.y0) > 0.5 * min(l.h, min(x.h for x in r)) \
                    and all(min(l.x1, x.x1) - max(l.x0, x.x0) < 0.2 * min(l.h, x.h) for x in r):
                r.append(l)
                break
        else:
            rows.append([l])
    out = []
    for r in rows:
        if len(r) == 1:
            out.append(r[0])
            continue
        r.sort(key=lambda l: -l.x1)
        texts = []
        for i, l in enumerate(r):
            t = l.text.strip()
            if i and _FRACTION.fullmatch(texts[-1]) and t.startswith("-"):
                t = t[1:].strip()
            texts.append(t)
        box = (min(l.x0 for l in r), min(l.y0 for l in r), max(l.x1 for l in r), max(l.y1 for l in r))
        words = [w for l in r for w in sorted(l.words, key=lambda w: -w.bbox[2])]
        out.append(Line(" ".join(texts), box, min(l.conf for l in r), words, latin=all(l.latin for l in r)))
    return sorted(out, key=lambda l: l.y0)


# ---- the assembler ----------------------------------------------------------------------------

class Assembler:
    def __init__(self, toc, fig_captions, missing, indent=0.04, poetry=False):
        self.toc = [e for e in toc if e.list == "contents"]
        self.fig_captions = fig_captions
        self.missing = missing
        self.indent = indent  # the book's paragraph indent, as a share of the text width
        self.poetry = poetry  # a book of verse: its pages are verse unless set as justified prose
        self.chapters = []
        self.chapter = None
        self.para = None  # the open paragraph Block
        self.para_lines, self.para_strong = 0, False  # its number of lines; a quote: clearly indented
        self.deferred = []  # figures waiting for the open paragraph to end
        self.notes_by_page = {}
        self.last_note = None
        self.max_note = 0  # the highest note number read so far
        self.last_linked = {}
        self.explicit = {}
        self.prev_ended = True
        self.report = {"headings": [], "unlinked_notes": [], "stray_markers": [], "toc_unmatched": [], "dropped": [],
                       "verse": 0, "poem": 0, "tables": 0, "figures": 0, "quotes": 0}

    # -- output --
    def close_para(self):
        if self.para is not None:
            if self.para.kind == "quote" and self.para_lines == 1 and not self.para_strong:
                # one line indented a little more than the paragraphs: a short paragraph (dialogue), as
                # the indents of one book vary by a percent or two
                self.para.kind = "p"
                self.report["quotes"] -= 1
            self.chapter.blocks.append(self.para)
            self.para = None
        if self.deferred:
            self.chapter.blocks += self.deferred
            self.deferred = []

    def add(self, block):
        self.close_para()
        self.chapter.blocks.append(block)

    def new_chapter(self, title, page, kind="chapter", label="", heading=None):
        self.close_para()
        self.chapter = Chapter(normalize(title), page, label, heading or [], kind=kind)
        self.chapters.append(self.chapter)

    # -- inline text and footnote markers --
    def inline(self, text, page_no, ltr=False):
        text = normalize(text)
        if ltr:
            text = text.replace(MARK, "")
            return [Span(text)] if text else []
        notes = self.notes_by_page.get(page_no, {})
        items, pos = [], 0
        for m in _MARKER.finditer(text):
            last = self.last_linked.get(page_no, 0)
            following = next((notes[k] for k in sorted(notes) if k > last and not notes[k].linked
                              and k not in self.explicit.get(page_no, ())), None)
            if m.group(0) == MARK:
                note = following
                if note is None:
                    items.append(text[pos:m.start()])
                    pos = m.end()
                    continue
            elif m.group(3) or m.group(4):
                note = notes.get(_marker_num(m))
                if note is None or note.linked:
                    continue  # an ordinary number in the text
            else:
                note = notes.get(_marker_num(m))
                if note is None or note.linked:
                    # after closing punctuation a digit is a marker, misread ("۲" for "۴")
                    note = following if text[m.start() - 1] in ".»،؛:!؟)" else None
                    if note is None:
                        if notes:
                            self.report["stray_markers"].append((page_no, m.group(0), text[max(0, m.start() - 25):m.end() + 5]))
                        continue
            items += [text[pos:m.start()], NoteRef(note)]
            note.linked = True
            self.last_linked[page_no] = note.num
            pos = m.end()
        items.append(text[pos:])
        return [x for x in items if x != ""]

    def para_append(self, text, page_no, kind="p", ltr=False, strong=False):
        if self.para is None:
            self.para = Block(kind, [], ltr)
            self.para_lines, self.para_strong = 0, strong
        elif self.para.items:
            last = self.para.items[-1]
            if not (isinstance(last, str) and last.endswith((" ", "\u200c"))):
                self.para.items.append(" ")
            self.para.ltr = self.para.ltr and ltr
        if self.para.ltr:
            self.para.items.append(text.replace(MARK, ""))  # a left-to-right paragraph needs no spans
        else:
            self.para.items += self.inline(text, page_no, ltr)
        self.para_lines += 1

    # -- notes --
    def read_notes(self, L, n):
        """The page's notes, split by `notes.split_notes` (numbers per page, per chapter or through the book)."""
        lines = [l for row in _rows(L.notes) for l in row]
        lead, found = split_notes(lines, self.max_note, L.width)
        if lead:
            if self.last_note is not None and self.last_note.page >= n - 2:
                self.last_note.text += " " + " ".join(lead)  # a note continued from the previous page
            else:
                found = [(0, " ".join(lead), False)] + found
        out = {}
        for k, text, stars in found:
            if k in out:
                out[k].text += " " + text
                continue
            out[k] = Note(f"n{n}-{k}", n, k, text)
        if 0 in out and len(out) == 1:
            # a page's only note, its number not read: number 1, so a marker found in the image links to it
            note = out.pop(0)
            note.num, note.id = 1, f"n{n}-1"
            out[1] = note
        numbered = [k for k, _, stars in found if not stars and k]
        if numbered:
            self.max_note = numbered[-1]  # the numbering in force: it may restart (per page, per chapter)
        for note in out.values():
            note.ltr = latin_ratio(note.text) > 0.5
        if out:
            self.last_note = out[max(out)]
        self.notes_by_page[n] = out
        self.explicit[n] = {_marker_num(m) for l in L.body for m in _MARKER.finditer(l.text)
                            if m.group(0) != MARK and _marker_num(m) in out}
        return list(out.values())

    def link_leftovers(self, n):
        """Notes whose marker the page lost are attached at the end of the page's text."""
        for note in self.notes_by_page.get(n, {}).values():
            if note.linked:
                continue
            target = self.para or next((b for b in reversed(self.chapter.blocks) if b.kind in ("p", "quote", "bib")), None)
            if target is None:
                continue
            target.items.append(NoteRef(note))
            note.linked = True
            self.report["unlinked_notes"].append((n, note.num, note.text[:50]))

    # -- headings --
    def toc_score(self, text, n):
        return max((_similar(text, e.title) for e in self.toc if e.page is not None and abs(e.page - n) <= 1), default=0.0)

    # -- a text page --
    def text_page(self, L, n, lines):
        lines = [l for l in sorted(lines, key=lambda l: l.y0) if not (l.h < 0.5 * L.lh and len(l.text) <= 2)]
        cols = _two_columns(L, lines)
        if cols:
            self.list_page(L, n, lines, cols)
            return
        W = L.page.width
        lines = _join_rows(lines)
        if _hanging_page(L, lines):
            self.hanging_page(L, n, lines)
            return
        if _poem_page(L, lines, self.indent) or (self.poetry and len(lines) >= 4 and _full_share(L, lines) < 0.4):
            self.poem_page(L, n, lines)
            return
        regions = sorted(L.regions, key=lambda r: r.bbox[1])
        prev, prev_y1, after_table = None, None, None
        skip = set()
        for idx, l in enumerate(lines):
            if idx in skip:
                continue
            while regions and regions[0].bbox[1] < l.y0:
                r = regions.pop(0)
                self.region(r, L, n)
                prev, prev_y1, after_table = None, r.bbox[3], r if r.kind == "table" else None
            ind = (L.right - l.x1) / L.width
            sl = (l.x0 - L.left) / L.width
            # space above the line, in line heights, as it would be in a book set with tight leading (0.2 = no
            # extra space): modern books set lines far apart, and their normal spacing is not a gap
            gap = ((l.y0 - prev_y1) - max(0.0, (L.pitch or L.lh) - L.lh)) / L.lh + 0.2 if prev_y1 is not None else None
            text = l.text.strip()
            ltr = l.latin or latin_ratio(text) > 0.5
            nxt = lines[idx + 1] if idx + 1 < len(lines) else None
            prev_y1 = l.y1

            if (re.fullmatch(r"[*٭✽✱\s]+", text) and text.count("*") + text.count("٭") >= 2) or \
                    (l.w < 0.2 * L.width and l.h < 0.7 * L.lh and (l.conf < 85 or "*" in text)):
                # a section break: asterisks, or an ornament ("❋ ❋ ❋") the recognizer made letters of
                if not (self.chapter.blocks and self.chapter.blocks[-1].kind == "sep" and self.para is None):
                    self.add(Block("sep"))
                prev = None
                continue
            if l.conf < 80:
                self.report["dropped"].append((n, text))  # stamps, letterheads, handwriting, map labels
                continue
            if ind > 0.4 and sl < 0.1 and not ltr:
                # a short line against the left margin: a signature, a place and date
                self.add(Block("sign", self.inline(text, n)))
                prev = None
                continue
            table_below = any(r.kind == "table" and 0 < r.bbox[1] - l.y1 < 3 * L.lh for r in regions)
            if after_table is not None and gap is not None and gap < 1.5 and sl > 0.3 \
                    and self.para is None and self.chapter.blocks and self.chapter.blocks[-1].kind == "table":
                self.chapter.blocks[-1].items += ["\n"] + self.inline(text, n)  # the table's source line
                prev = None
                continue
            if table_below and _centered(l, W, 0.25):
                self.add(Block("caption", self.inline(text, n)))  # the table's number and title
                prev = None
                continue
            after_table = None
            if l.split is not None:
                ws = sorted(l.words, key=lambda w: -w.bbox[2])
                first = " ".join(w.text for w in ws if (w.bbox[0] + w.bbox[2]) / 2 > l.split)
                second = " ".join(w.text for w in ws if (w.bbox[0] + w.bbox[2]) / 2 <= l.split)
                if self.para is None or self.para.kind != "verse":
                    self.close_para()
                    self.para = Block("verse")
                self.para.rows.append((first.replace(MARK, ""), second.replace(MARK, "")))
                self.report["verse"] += 1
                prev = l
                continue
            score = self.toc_score(text, n)
            geo = (gap is not None and gap >= 1.0 and l.h >= 0.97 * L.lh and l.conf >= 95 and sl > 0.05
                   and len(text) < 100 and not text.endswith(_END_PUNCT) and (ind < 0.07 or _centered(l, W))
                   and len(_letters(text)) >= 3 * sum(c in DIGITS for c in text))  # not a row of figures
            learned_head = getattr(l, "p_head", None) is not None and l.p_head >= 0.5 and l.conf >= 85 and len(text) < 100
            if not ltr and (geo or learned_head or (score >= 0.6 and sl > 0.05 and (gap is None or gap > 0.5))):
                if nxt is not None and nxt.h >= 0.97 * L.lh and (nxt.x0 - L.left) / L.width > 0.3 \
                        and nxt.y0 - l.y1 < 0.6 * L.lh and self.toc_score(text + " " + nxt.text, n) > score + 0.1:
                    text += " " + nxt.text.strip()
                    skip.add(idx + 1)
                    prev_y1 = nxt.y1
                self.add(Block("h2", self.inline(text, n)))
                self.report["headings"].append((n, text, round(score, 2)))
                prev = None
                continue
            if sl > 0.04 and (ind > self.indent + 0.02 or (ind > 0.75 * self.indent and l.h < 0.92 * L.lh and l.w > 0.5 * L.width)):
                # indented further than a paragraph (or as far, in smaller type: told on long lines, as the box of
                # a short line is often lower than the type), and not reaching the left margin
                new = (self.para is None or self.para.kind != "quote" or (gap is not None and gap > 0.9)
                       or (prev is not None and l.x1 < prev.x1 - 0.02 * L.width))
                if new:
                    self.close_para()
                    self.report["quotes"] += 1
                self.para_append(text, n, "quote", ltr, strong=ind > self.indent + 0.04)
                prev = l
                continue
            starts = (ind > min(0.05, max(0.015, 0.6 * self.indent)) or self.para is None or self.para.kind != "p"
                      or (prev is not None and (prev.x0 - L.left) / L.width > 0.06)
                      or (prev is None and self.prev_ended) or (gap is not None and gap > 0.9))
            if starts:
                self.close_para()
            self.para_append(text, n, "p", ltr)
            prev = l
        for r in regions:
            self.region(r, L, n)
        last = lines[-1] if lines else None
        self.prev_ended = (last is None or (last.x0 - L.left) / L.width > 0.06
                           or bool(L.regions and L.regions[-1].bbox[1] > last.y1))

    def hanging_page(self, L, n, lines):
        """A list set with hanging indents (a bibliography): an unindented line starts an entry, indented
        lines continue it (indented on the left for an English entry); a centred line heads the list."""
        for l in lines:
            text = l.text.strip()
            if l.conf < 80:
                self.report["dropped"].append((n, text))
                continue
            ltr = latin_ratio(text) > 0.5
            ind, sl = (L.right - l.x1) / L.width, (l.x0 - L.left) / L.width
            if ind > 0.1 and sl > 0.1 and abs(ind - sl) < 0.08 and len(text) < 60:
                self.add(Block("h2", self.inline(text, n)))
                self.report["headings"].append((n, text, 0))
                continue
            if (sl if ltr else ind) <= 0.02 or self.para is None or self.para.kind != "bib":
                self.close_para()
                self.para = Block("bib", [], ltr)
                self.para_lines, self.para_strong = 0, False
            self.para_append(text, n, "bib", ltr)
        for r in L.regions:
            self.region(r, L, n)
        self.prev_ended = False

    def poem_page(self, L, n, lines):
        """A page of verse set line by line: each printed line stays a line; a gap starts a new stanza."""
        prev = None
        for l in lines:
            if l.conf < 80:
                self.report["dropped"].append((n, l.text.strip()))
                continue
            if self.para is None or self.para.kind != "poem":
                self.close_para()
                self.para = Block("poem")
            elif prev is not None and l.y0 - prev.y1 > 0.9 * L.lh:
                self.para.rows.append("")  # a stanza break
            self.para.rows.append(self.inline(l.text.strip(), n, latin_ratio(l.text) > 0.5))
            self.report["poem"] += 1
            prev = l
        for r in L.regions:
            self.region(r, L, n)
        self.prev_ended = True

    def list_page(self, L, n, lines, cols):
        """A bibliography page: authors in one column, their works beside them in the other."""
        a, b = cols
        right = [l for l in lines if l.x0 >= a]
        left = [l for l in lines if l.x1 <= b]
        crossing = [l for l in lines if l not in right and l not in left]
        heads, texts = (right, left) if np.median([l.w for l in right]) < np.median([l.w for l in left]) else (left, right)
        rtl_text = texts is left
        heads = sorted(heads + crossing, key=lambda l: l.y0)
        first_y = heads[0].y0 if heads else 1e9
        # centred lines above the first author are the list's headings ("فهرست منابع فارسی")
        for l in [l for l in texts if l.y1 < first_y - 0.3 * L.lh and _centered(l, L.page.width, 0.2)
                  and l.h >= 1.05 * np.median([t.h for t in texts]) and not l.text.strip().endswith(_END_PUNCT)]:
            self.add(Block("h2", self.inline(l.text, n)))
            self.report["headings"].append((n, l.text, 0))
            texts.remove(l)
        edge_l = np.percentile([l.x0 for l in texts], 10) if texts else 0
        edge_r = np.percentile([l.x1 for l in texts], 90) if texts else 0
        owner = {}
        for t in texts:
            hs = [h for h in heads if h.y0 <= t.y0 + 0.5 * L.lh]
            owner.setdefault(id(hs[-1]) if hs else None, []).append(t)
        for t in owner.get(None, []):  # an entry continued from the previous page
            self.para_append(t.text, n, "bib", latin_ratio(t.text) > 0.5)
        for h in heads:
            ltr = latin_ratio(h.text) > 0.5
            self.close_para()
            self.para = Block("bib", [], ltr, lead=h.text.strip())
            prev = None
            for t in sorted(owner.get(id(h), []), key=lambda l: l.y0):
                gap = ((prev.x0 - edge_l) if rtl_text else (edge_r - prev.x1)) if prev is not None else 0
                if gap > 0.08 * L.page.width:  # the previous line ended short: a new work by the same author
                    self.close_para()
                    self.para = Block("bib", [], ltr)
                self.para_append(t.text, n, "bib", ltr)
                prev = t
        self.prev_ended = False

    def region(self, r, L, n):
        if r.kind == "table":
            rows = _rows(r.lines, 0.4)
            xs = sorted((l.x0 + l.x1) / 2 for row in rows for l in row)
            cols, cur = [], xs[:1]
            for x in xs[1:]:
                if x - cur[-1] > 0.05 * L.page.width:
                    cols.append(float(np.mean(cur)))
                    cur = [x]
                else:
                    cur.append(x)
            if cur:
                cols.append(float(np.mean(cur)))
            cols.sort(reverse=True)  # right to left
            if 2 <= len(cols) <= 12 and len(rows) >= 2:
                table = []
                for row in rows:
                    cells = [""] * len(cols)
                    for l in row:
                        k = int(np.argmin([abs((l.x0 + l.x1) / 2 - c) for c in cols]))
                        cells[k] = (cells[k] + " " + l.text.replace(MARK, "")).strip()
                    table.append(cells)
                self.close_para()
                caption = []
                while self.chapter.blocks and self.chapter.blocks[-1].kind == "caption":
                    caption = self.chapter.blocks.pop().items + (["\n"] if caption else []) + caption
                self.add(Block("table", caption, rows=table, image=(L, r.bbox, 0)))
                self.report["tables"] += 1
                return
        self.deferred.append(Block("figure", image=(L, r.bbox, 0)))
        self.report["figures"] += 1

    # -- the whole book --
    def run(self, pages, starts):
        for L, n in pages:
            for a, b in self.missing:
                if b == n - 1 and self.chapter is not None:
                    note = (f"[صفحات {fa_num(a)}–{fa_num(b)} در نسخهٔ اسکن‌شده موجود نیست]" if a != b
                            else f"[صفحهٔ {fa_num(a)} در نسخهٔ اسکن‌شده موجود نیست]")
                    self.add(Block("gap", [note]))
            front = n not in starts and (self.chapter is None or self.chapter.kind == "front")
            notes = self.read_notes(L, n) if L.kind == "text" and not front else []
            lines = L.body
            if n in starts and starts[n]["kind"] == "part":
                self.new_chapter(starts[n]["title"], n, kind="part")
                self.add(Block("mark", [PageMark(n)]))
                continue
            if n in starts:
                s = starts[n]
                if s["kind"] == "chapter":
                    heading = (self.inline(" ".join(l.text for l in s["title_lines"]), n) if s["title_lines"]
                               else [s["title"]])
                    self.new_chapter(s["title"], n, label=s["label"], heading=heading)
                elif self.chapter is None:  # a titled section before the first chapter
                    self.new_chapter("", n, kind="front")
                for l in s["after_h2"]:
                    self.add(Block("h2", self.inline(l.text, n)))
                    self.report["headings"].append((n, l.text, 1.0))
                for l in s["bylines"]:
                    self.add(Block("byline", self.inline(l.text, n)))
                lines = s["rest"]
            elif self.chapter is None:
                self.new_chapter("", n, kind="front")
            self.chapter.notes += notes
            if self.chapter.kind != "front" and n >= 1:
                mark = PageMark(n)
                if self.para is not None and self.para.kind == "poem":
                    self.para.rows.append([mark])
                elif self.para is not None:
                    self.para.items.append(mark)
                else:
                    self.add(Block("mark", [mark]))
            if L.kind == "figure":
                cap = self.fig_captions.get(n, "")
                fig = Block("figure", [cap] if cap else [], image=(L, L.crop, L.rotate))
                self.report["figures"] += 1
                if self.chapter.kind == "front":
                    self.add(fig)
                else:
                    self.deferred.append(fig)
                continue
            if L.kind == "blank":
                continue
            if self.chapter.kind == "front":
                rows = _rows(L.body + L.notes + [x for r in L.regions for x in r.lines])
                self.add(Block("lines", rows=[" ".join(l.text for l in r) for r in rows]))
                continue
            self.text_page(L, n, lines)
            self.link_leftovers(n)
        self.close_para()


def _hanging_page(L, lines):
    """A list with hanging indents: many lines indented, and an indented line follows a line that runs to
    the far margin (an entry wrapping), where in prose it follows a short line (a paragraph's end)."""
    if len(lines) < 6 or L.width <= 1:
        return False
    def indented(l):
        ltr = latin_ratio(l.text) > 0.5
        return ((l.x0 - L.left) if ltr else (L.right - l.x1)) / L.width > 0.02
    def full(l):
        ltr = latin_ratio(l.text) > 0.5
        return ((L.right - l.x1) if ltr else (l.x0 - L.left)) / L.width < 0.03
    cont = [i for i in range(1, len(lines)) if indented(lines[i])]
    return len(cont) >= 3 and len(cont) >= 0.25 * len(lines) and sum(full(lines[i - 1]) for i in cont) >= 0.7 * len(cont)


def _poem_page(L, lines, indent):
    """A page of verse set line by line, not justified: fewer than half of the lines reach both margins,
    many end short of the left one, and a short line is followed by an unindented one (in prose a short
    line ends a paragraph, and the next line starts indented)."""
    if len(lines) < 6 or L.width <= 1 or _full_share(L, lines) >= 0.5:
        return False
    def aligned(l):  # set against the right margin, or centred
        ind, sl = (L.right - l.x1) / L.width, (l.x0 - L.left) / L.width
        return ind < 0.02 or abs(sl - ind) < 0.03
    if sum(aligned(l) for l in lines) < 0.8 * len(lines):
        return False
    short = [i for i, l in enumerate(lines[:-1]) if (l.x0 - L.left) / L.width > 0.06]
    if len(short) < 0.3 * (len(lines) - 1):
        return False
    indented = sum((L.right - lines[i + 1].x1) / L.width > 0.6 * indent for i in short)
    return indented < 0.3 * len(short)


def _full_share(L, lines):
    """The share of lines that reach both margins of the text block (most lines of justified prose)."""
    return sum((l.x0 - L.left) / L.width < 0.03 and (L.right - l.x1) / L.width < 0.03 for l in lines) / max(1, len(lines))


def is_poetry(pages, indent):
    """A book of verse: a quarter or more of its text pages are clearly set line by line."""
    text = [L for L, _ in pages if L.kind == "text" and len(L.body) >= 6]
    verse = sum(_poem_page(L, _join_rows(sorted(L.body, key=lambda l: l.y0)), indent) for L in text)
    return bool(text) and verse >= 0.25 * len(text)


def chapter_openings(pages, contents):
    """{page: opening} for the pages that open a chapter, and for chapter-like openings the contents
    do not list (kind "section": their title becomes a section heading)."""
    contents = [e for e in contents if e.title]  # entries whose title was not read serve `book_openings` only
    known = sum(e.page is not None for e in contents) >= 3
    first_text = min((n for L, n in pages if L.kind == "text"), default=0)
    starts, pending = {}, []
    for L, n in pages:
        if L.kind != "text" or not L.body:
            continue
        entry = next((e for e in contents if e.page == n and e.level == 1), None)
        sure = [l for l in sorted(L.body, key=lambda l: l.y0) if l.conf >= 85]
        label_first = bool(sure) and bool(_LABEL.match(sure[0].text)) and len(sure[0].text.split()) <= 4 \
            and sure[0].h >= 1.1 * L.lh  # a large "فصل نخست" heading the page
        learned = _learned_open(L)
        opening = learned or (L.header is None and (L.top > L.book_top + 0.07 or label_first))  # or starts lower
        tb = ((_learned_title_block(L) if learned else None) or title_block(L)) if opening else None
        if tb is None:
            if entry is not None and L.header is None:
                pending.append((n, L, entry))  # the contents say a chapter starts here; decided below
            elif entry is not None:
                # a book that prints its running header on chapter openings too (a word processor's): the
                # contents entry for this page, found among the page's first lines, opens the chapter
                k = next((i for i, l in enumerate(sure[:6]) if _similar(l.text, entry.title) >= 0.7
                          or _title_start(l.text, entry.title)), None)
                if k is not None:
                    starts[n] = dict(kind="chapter", title=entry.title, label="", title_lines=[sure[k]], after_h2=[],
                                     bylines=sure[:k], rest=[l for l in L.body if l not in sure[:k + 1]])
            continue
        before, title, after, rest = tb
        text = " ".join(l.text for l in title).replace(MARK, "")
        lead = title[0].text.strip() if _LABEL.match(title[0].text) and len(title) > 1 else ""
        main_text = " ".join(l.text for l in (title[1:] if lead else title)).replace(MARK, "")
        near = [e for e in contents if e.page is not None and abs(e.page - n) <= 1]
        best = max(near, key=lambda e: _similar(text, e.title), default=None)
        # listed in the contents: the whole title, its label ("فصل پنجم" on a row of its own there), or the
        # start of its main title (the contents may cut it short)
        listed = any(_similar(text, e.title) >= 0.8 or (lead and _same_title(lead, e.title)) or _title_start(main_text, e.title)
                     for e in contents)
        if known and not near and not listed and not _LABEL.match(text):  # "فصل ۷" opens a chapter regardless
            # not in the contents: a titled section inside the chapter (a second bibliography, say)
            starts[n] = dict(kind="section", after_h2=title + after, bylines=before, rest=rest)
            continue
        if n == first_text and entry is None and best is None:
            continue  # a title page, not a chapter
        if entry is not None and _similar(text, entry.title) < 0.8:
            # the contents name this chapter differently: their title, and the page's title lines as headings
            starts[n] = dict(kind="chapter", title=entry.title, label="", title_lines=[], after_h2=title + after,
                             bylines=before, rest=rest)
            continue
        label = title[0].text.strip() if _LABEL.match(title[0].text) and len(title) > 1 else ""
        if label and best is not None and re.search(f"[{DIGITS}]", label) and _LABEL.match(best.title):
            label = best.title.split(":")[0].strip()  # "فصل ۴" misread for "فصل ششم"
        main = title[1:] if label else title

        def name(lines):
            t = " ".join(l.text for l in lines).replace(MARK, "")
            t = re.sub(rf"(?<=\S)[{DIGITS}]{{1,2}}$", "", t)  # a note marker on the title
            return f"{label}: {t}" if label else t
        k = len(main)
        if best is not None and _similar(text, best.title) >= 0.5:
            # title lines past the ones the contents entry covers are a subtitle
            k = max(range(1, len(main) + 1), key=lambda j: (_similar(name(main[:j]), best.title), -j))
        h2 = [l for l in after if any(_similar(l.text, e.title) >= 0.6 for e in near if e.level == 2)]
        title, title_lines = name(main[:k]), main[:k]
        if entry is not None and len(title) < len(entry.title):
            # the contents say more ("فصل ۲" where the page's large "۲" was not read): their title
            title, title_lines, label = entry.title, [], ""
        starts[n] = dict(kind="chapter", title=title, label=label, title_lines=title_lines, after_h2=h2,
                         bylines=main[k:] + [l for l in after if l not in h2] + before, rest=rest)
    # A chapter the contents place on a page without a title block (a facsimile, a picture page), unless
    # its title opened a chapter elsewhere: then this page number was misread.
    for n, L, entry in pending:
        if not any(_same_title(entry.title, x) for st in starts.values() if st["kind"] == "chapter"
                   for x in (st["title"], st.get("label", ""), st["title"].split(": ", 1)[-1])):
            starts[n] = dict(kind="chapter", title=entry.title, label="", title_lines=[], after_h2=[], bylines=[], rest=L.body)
    _titles_from_contents(starts, contents)
    return starts


def _titles_from_contents(starts, contents):
    """A chapter opening whose title the OCR could not read (display type) shows only its label ("فصل ۷"):
    its title is the one contents entry between those of the chapters before and after it."""
    def entry_index(st):
        main = st["title"].split(": ", 1)[-1]
        return next((i for i, e in enumerate(contents) if _same_title(main, e.title) or _title_start(main, e.title)), None)
    chapters = [st for n, st in sorted(starts.items()) if st["kind"] == "chapter"]
    for i, st in enumerate(chapters):
        if not re.fullmatch(rf"({_LABEL_WORDS})\s+\S+", st["title"].strip()):
            continue
        before = entry_index(chapters[i - 1]) if i > 0 else -1
        after = entry_index(chapters[i + 1]) if i + 1 < len(chapters) else len(contents)
        if before is not None and after is not None and after - before == 2:
            st["title"] = f"{st['title']}: {contents[before + 1].title}"
            st["title_lines"] = []


# ---- book-level openings ------------------------------------------------------------------------

_NUMBERED_ENTRY = re.compile(rf"^\s*[{DIGITS}]{{1,2}}(?:\s*[-–.]|\s+|(?=[^\s{DIGITS}/]))")  # "1-خطرات", "2خیریه": numbered chapters


def _sure_lines(L):
    return [l for l in sorted(L.body, key=lambda l: l.y0) if l.conf >= 85 and len(_letters(l.text)) >= 2]


def _first_word(L):
    sure = _sure_lines(L)
    words = sure[0].text.split() if sure else []
    return _letters(words[0]) if words and len(_letters(words[0])) >= 2 else ""


def _page_features(L):
    """Binary observations of a page that say how a chapter opening looks in a given book."""
    sure = _sure_lines(L)
    first = sure[0] if sure else None
    return {"no_header": L.header is None,
            "low": L.top > L.book_top + 0.07,
            "big": bool(first) and first.h >= 1.15 * L.lh,
            "short": 0 < len(sure) <= 3,
            "open": _learned_open(L)}


# ---- learned line roles (roles.annotate), consulted when the lines carry them -------------------

def _learned(L):
    return bool(L.body) and all(getattr(l, "p_head", None) is not None for l in L.body)


def _learned_open(L):
    """The learned roles say the page opens a unit: a heading of level 1 among its first three sure lines."""
    return _learned(L) and any(l.p_head >= 0.65 and getattr(l, "level", 0) == 1 for l in _sure_lines(L)[:3])


def _learned_title_block(L):
    """(before, title, after, rest) from the learned roles: the heading lines heading the page, the level-1
    ones as the title and the others (sub-headings under it) after it; None when the page does not start
    with a level-1 heading."""
    if not _learned(L):
        return None
    body = [l for l in sorted(L.body, key=lambda l: l.y0) if (l.h >= 0.5 * L.lh or len(l.text) > 2) and l.conf >= 85]
    k = 0
    while k < len(body) and body[k].p_head >= 0.5:
        k += 1
    head = body[:k]
    if not any(getattr(l, "level", 0) == 1 for l in head):
        return None
    title = [l for l in head if l.level == 1]
    if len(title) == 1 and _LABEL.match(title[0].text) and len(title[0].text.split()) <= 3 and head[0] is title[0] \
            and len(body) > 1 and not (body[1].w >= 0.75 * L.width and body[1].h < 1.3 * L.lh):
        # a label alone ("فصل سوم"): the line under it is the title, whatever the model made of it, unless it
        # is a line of running text (then the title was display type the OCR lost; the contents may supply it)
        title, k = body[:2], max(k, 2)
        head = body[:k]
    return [], title, [l for l in head if l not in title], body[k:]


def _title_at(L, title, depth=4):
    """Index of the line among the page's first sure lines that carries TITLE (alone or joined with the next
    line), or None."""
    sure = _sure_lines(L)[:depth]
    bare_label = re.fullmatch(rf"({_LABEL_WORDS})\s+\S+", title.strip())
    for i, l in enumerate(sure):
        if bare_label:  # "فصل هشتم" must not match "فصل هفتم": the label and its number word exactly
            if _letters(l.text).startswith(_letters(title)) and _same_title(" ".join(l.text.split()[:2]), title):
                return i
            continue
        texts = [l.text] + ([l.text + " " + sure[i + 1].text] if i + 1 < len(sure) else [])
        if any(_similar(t, title) >= 0.8 or _title_start(t, title) or _same_title(t, title) for t in texts):
            return i
    return None


def _align_contents(pages, contents):
    """{entry index: page number}: each contents entry on the page whose first lines carry its title, keeping
    the order of the list. The printed page number decides between candidates (in some books it belongs to
    another edition, so it is not required). A title found on a run of pages (a running header the layout
    did not tell apart) counts at the run's first page."""
    by_n = {n: L for L, n in pages if L.kind == "text" and L.body}
    order = sorted(by_n)
    cands = []
    for i, e in enumerate(contents):
        if not e.title and e.page in by_n:
            cands.append([e.page])  # an entry whose title was not read: its page number
            continue
        if len(_letters(e.title)) < 3:
            cands.append([])
            continue
        hits = [n for n in order if _title_at(by_n[n], e.title) is not None]
        runs = [n for k, n in enumerate(hits) if k == 0 or order.index(n) - order.index(hits[k - 1]) > 1]
        if e.page is not None and e.page in by_n and e.page not in runs and _title_at(by_n[e.page], e.title, 8) is not None:
            runs.append(e.page)
        cands.append(sorted(set(runs)))
    # longest chain of entries with non-decreasing pages; ties: closer to the printed page numbers
    best = {}
    for i, cs in enumerate(cands):
        for n in cs:
            cost = abs(n - contents[i].page) if contents[i].page is not None else 0
            prev = max(((best[j][0], best[j][1], best[j][2]) for j in best if j[0] < i and j[1] <= n),
                       key=lambda t: (t[0], -t[1]), default=(0, 0, []))
            best[(i, n)] = (prev[0] + 1, prev[1] + min(cost, 50), prev[2] + [(i, n)])
    if not best:
        return {}
    _, _, chain = max(best.values(), key=lambda t: (t[0], -t[1]))
    return dict(chain)


def _entry_roles(contents, aligned, by_n):
    """'unit' or 'section' for each aligned entry. With labelled chapters in the contents ("فصل ...",
    "1- ..."), the entries between the first and the last labelled one are sections; a book whose aligned
    entries mostly sit inside the page (not at its top) lists sections, not chapters."""
    labelled = [i for i, e in enumerate(contents) if _LABEL.match(e.title) or _NUMBERED_ENTRY.match(e.title)
                or re.fullmatch(rf"({_LABEL_WORDS})\s+\S+", e.title.strip())]
    roles = {}
    for i, n in aligned.items():
        if not contents[i].title:  # no title to find: a unit where the page opens like one
            L = by_n[n]
            roles[i] = "unit" if (L.header is None and title_block(L) is not None) or _page_features(L)["low"] \
                or _learned_open(L) else "section"
            continue
        at = _title_at(by_n[n], contents[i].title)
        roles[i] = "unit" if at is not None and at <= 1 else "section"
        page_label = at is not None and bool(_LABEL.match(_sure_lines(by_n[n])[at].text))  # the page says "فصل سوم"
        if len(labelled) >= 2 and labelled[0] < i < labelled[-1] and i not in labelled and not page_label:
            roles[i] = "section"
    tops = sum(r == "unit" for r in roles.values())
    if len(roles) >= 10 and tops < 0.5 * len(roles) and len(labelled) < 2:
        roles = {i: "section" for i in roles}
    return roles


def book_openings(pages, contents, starts):
    """The chapter openings of the whole book: the page-by-page `starts` checked against the contents and
    against the way this book's own openings look.

    1. The contents are aligned with the pages (`_align_contents`) and their entries told apart as units or
       sections (`_entry_roles`).
    2. Anchors: openings the contents confirm (an entry's title at the top of the page). From them, the book's
       template: the page features (`_page_features`) and the first word ("فصل", "پرده") that most of them share.
    3. With three anchors or more, a page-by-page opening the contents do not confirm stays only if it fits the
       template; otherwise its title becomes a section heading. Contents units the page-by-page pass missed are
       added, and so are pages that fit the template and begin with its first word (scenes, poems the contents
       leave out). With fewer anchors the page-by-page result stands.
    """
    by_n = {n: L for L, n in pages if L.kind == "text" and L.body}
    aligned = _align_contents(pages, contents)
    roles = _entry_roles(contents, aligned, by_n)
    def line_height(i, n):  # the title over its byline when both are listed ("در حاشیه": author, title)
        k = _title_at(by_n[n], contents[i].title) if contents[i].title else None
        return _sure_lines(by_n[n])[k].h if k is not None else 0
    unit_pages = {}
    for i, n in sorted(aligned.items()):
        if roles.get(i) == "unit" and (n not in unit_pages or line_height(i, n) > line_height(unit_pages[n], n)):
            unit_pages[n] = i
    section_pages = {n for i, n in aligned.items() if roles.get(i) == "section"} - set(unit_pages)
    anchors = sorted(unit_pages)
    if len(anchors) < 3:
        # too few openings to learn from; a page-by-page opening the contents list as a section is one
        out = {n: (dict(kind="section", after_h2=st.get("title_lines") or [], bylines=st.get("bylines", []),
                        rest=st.get("rest", by_n[n].body if n in by_n else []))
                   if st["kind"] == "chapter" and n in section_pages else st)
               for n, st in starts.items()}
        return _body_start(out, aligned, contents, by_n)
    feats = {n: _page_features(by_n[n]) for n in by_n}
    shared = {k for k in feats[anchors[0]] if sum(feats[n][k] for n in anchors) >= 0.8 * len(anchors)}
    words = collections.Counter(_first_word(by_n[n]) for n in anchors)
    word, count = words.most_common(1)[0] if words else ("", 0)
    elsewhere = sum(_first_word(by_n[n]) == word for n in by_n if n not in unit_pages)
    if not word or count < 3 or count < 0.5 * len(anchors) or elsewhere > 0.05 * len(by_n):
        word = ""

    others = [n for n in by_n if n not in unit_pages]
    # the learned "open" adds openings (`fits`) but never demotes one: a title the model misses is still a title
    telling = {k for k in shared if k != "open" and sum(feats[n][k] for n in others) <= 0.3 * max(1, len(others))}

    def fits(n):  # looks like this book's openings (to add a page the contents leave out)
        return bool(shared or word) and all(feats[n][k] for k in shared) and (not word or _first_word(by_n[n]) == word)

    def plausible(n):  # does not differ from them in what sets them apart from other pages (to keep one); the
        # first word is not asked of front and back matter (before the first or after the last anchor)
        inside = anchors[0] < n < anchors[-1]
        return all(feats[n][k] for k in telling) and (not word or not inside or _first_word(by_n[n]) == word)

    # contents that list (almost) every entry they hold on its page are taken as complete: an opening they do
    # not confirm is a section (a table caption, a numbered heading set large)
    titled = [i for i, e in enumerate(contents) if len(_letters(e.title)) >= 3]
    complete = len(titled) >= 5 and sum(i in aligned for i in titled) >= 0.9 * len(titled)
    # an entry whose title was not found on its page (a facsimile, an OCR slip) still confirms the opening the
    # page-by-page pass found on the page its number gives
    by_number = {e.page for i, e in enumerate(contents) if i not in aligned and e.page is not None}
    out = {}
    for n, st in starts.items():
        labelled = bool(_LABEL.match(st.get("title", "")) or _LABEL.match(st.get("label", "") + " x"))
        if st["kind"] != "chapter" or n in unit_pages or labelled or n in by_number:  # "بخش سوم: ..." opens a part
            out[n] = st
        elif plausible(n) and n not in section_pages and not complete:
            out[n] = st
        else:  # not an opening of this book: its title is a section heading
            out[n] = dict(kind="section", after_h2=st.get("title_lines") or [], bylines=st.get("bylines", []),
                          rest=st.get("rest", by_n[n].body if n in by_n else []))
    for n, i in unit_pages.items():
        if n in out and out[n]["kind"] == "chapter":
            continue
        L = by_n[n]
        sure = _sure_lines(L)
        k = _title_at(L, contents[i].title) if contents[i].title else None
        if k is None:  # the contents entry has no title we could read: the page's own title block
            tb = title_block(L)
            if tb is None:
                continue
            before, title, after, rest = tb
            out[n] = dict(kind="chapter", title=" ".join(l.text for l in title).replace(MARK, ""), label="",
                          title_lines=title, after_h2=[], bylines=after + before, rest=rest)
            continue
        out[n] = dict(kind="chapter", title=contents[i].title, label="", title_lines=[sure[k]], after_h2=[],
                      bylines=sure[:k], rest=[l for l in L.body if l not in sure[:k + 1]])
    if word:
        for n, L in by_n.items():
            if n in out or not fits(n):
                continue
            tb = title_block(L)
            if tb is None:
                continue
            before, title, after, rest = tb
            text = " ".join(l.text for l in title).replace(MARK, "")
            out[n] = dict(kind="chapter", title=text, label="", title_lines=title, after_h2=[], bylines=after + before,
                          rest=rest)
    return _body_start(out, aligned, contents, by_n)


def _body_start(out, aligned, contents, by_n):
    """A book whose contents list only sections still has a body: it starts at the first contents entry, which
    opens it as a chapter (otherwise the whole book would be front matter)."""
    if any(st["kind"] == "chapter" for st in out.values()) or not aligned:
        return out
    i, n = min(aligned.items(), key=lambda x: (x[1], x[0]))
    L = by_n[n]
    sure = _sure_lines(L)
    k = _title_at(L, contents[i].title) if contents[i].title else None
    if k is None:
        return out
    out[n] = dict(kind="chapter", title=contents[i].title, label="", title_lines=[sure[k]], after_h2=[],
                  bylines=sure[:k], rest=[l for l in L.body if l not in sure[:k + 1]])
    return out


def paragraph_indent(pages):
    """The book's paragraph indent as a share of the text width: the most common right indent of a line
    that follows a line ending short (4% when the book shows too few). Books differ (4% to 8%), and a
    block quote is told from a paragraph by being indented further."""
    bins = collections.Counter()
    for L, _ in pages:
        if L.kind != "text" or L.width <= 1 or not L.lh:
            continue
        lines = sorted(L.body, key=lambda l: l.y0)
        for a, b in zip(lines, lines[1:]):
            ind = (L.right - b.x1) / L.width
            if (a.x0 - L.left) / L.width > 0.06 and 0 <= b.y0 - a.y1 < L.lh and 0.015 <= ind <= 0.15:
                bins[round(ind * 200)] += 1  # bins of 0.5%
    if sum(bins.values()) < 20:
        return 0.04
    return max(bins, key=lambda k: bins[k - 1] + 2 * bins[k] + bins[k + 1]) / 200


def build(ordered):
    pages = list(ordered.pages)
    max_page = max((n for _, n in pages), default=0)

    # the printed contents: parsed, then left out of the text
    toc, toc_pages, prev, kind = [], set(), False, "contents"
    for k, (L, n) in enumerate(pages):
        is_toc = is_toc_page(L, prev, early=k < 15)
        if is_toc:
            toc_pages.add(n)
            entries, kind = parse_toc(L, kind)
            toc += entries
        prev = is_toc
    _fix_toc_numbers(toc, max_page)
    _drop_unordered(toc)
    _number_bare_labels(toc)
    contents = [e for e in toc if e.list == "contents"]
    fig_captions = {e.page: e.title for e in toc if e.list == "figures" and e.page is not None}
    pages = [(L, n) for L, n in pages if n not in toc_pages]
    cover = pages[0] if pages and pages[0][0].kind == "figure" else None
    if cover:
        pages = pages[1:]

    starts = book_openings(pages, contents, chapter_openings(pages, contents))

    # a top-level contents entry without a page number groups the chapters listed under it ("ضمایم چاپ دوم")
    parts = {}
    for i, e in enumerate(contents):
        if e.level == 1 and e.page is None:
            under = []
            for x in contents[i + 1:]:
                if x.level == 1:
                    break
                under.append(x)
            pages_under = [x.page for x in under if x.page is not None]
            if len(pages_under) >= 2 and all(p in starts and starts[p]["kind"] == "chapter" for p in pages_under):
                parts.update({p: e.title for p in pages_under})
                # its title page: a page before the first chapter of the part with little more than the title
                for L, n in pages:
                    if n < min(pages_under) and n not in starts and L.kind == "text" and 0 < len(L.body) <= 2 \
                            and any(_similar(l.text, e.title) >= 0.6 or _similar(l.text, e.title.split("(")[0]) >= 0.8
                                    for l in L.body):
                        starts[n] = dict(kind="part", title=e.title, after_h2=[], bylines=[], rest=[])

    indent = paragraph_indent(pages)
    asm = Assembler(toc, fig_captions, ordered.missing, indent, is_poetry(pages, indent))
    asm.run(pages, starts)
    for ch in asm.chapters:
        ch.part = parts.get(ch.page, "")
    found = [t for _, t, _ in asm.report["headings"]] + [ch.title for ch in asm.chapters]
    asm.report["toc_unmatched"] = [e for e in contents if e.page is not None and not any(_similar(e.title, t) > 0.6 for t in found)]
    asm.report["toc_pages"] = sorted(toc_pages)
    return Book(asm.chapters, toc, cover, asm.report)
