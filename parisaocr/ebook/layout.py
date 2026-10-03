"""Per-page layout from ParisaOCR's line boxes and the page image.

A page is `blank`, `figure` (a full-page map, photo, calligraphy, or a scan too
damaged to read) or `text`. A text page is split into a running header (with
the printed page number) or a page number at the foot, body lines, footnote
lines below the separator rule, and regions: ruled tables and embedded figures.

Everything here is geometry, measured relative to the page (scans in one PDF
come at different resolutions): ink in the binarized page, rules found as long
straight runs of ink outside the text-line boxes, and the text block's margins
and line pitch from the body lines. Decisions that need more than one page
(header text that repeats, page order, chapters) are made in `order` and
`structure`.
"""
import difflib
import json
import os
import pathlib
import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from . import labels, roles
from .source import Line
from .textutil import DIGITS, ascii_digits, is_digits

GOOD_CONF = 85  # a line ParisaOCR read with at least this confidence counts as text


@dataclass
class Region:
    kind: str  # "table" | "figure"
    bbox: tuple
    lines: list = field(default_factory=list)


@dataclass
class Layout:
    page: object
    kind: str = "text"  # "text" | "blank" | "figure"
    header: object = None
    footer: object = None  # a page number printed at the foot of the page
    number: int = None  # printed page number, chosen by `order` from the candidates
    number_candidates: list = field(default_factory=list)
    body: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    regions: list = field(default_factory=list)
    rule_y: int = None
    rotate: int = 0  # degrees (counter-clockwise, as PIL's rotate) that make a figure page upright; see orient_figures
    crop: tuple = None  # figure pages: the bounding box of the picture
    left: float = 0
    right: float = 0
    lh: float = 0  # median body line height
    pitch: float = 0  # median distance between consecutive body lines
    top: float = 0  # first body line, as a fraction of the page height
    book_top: float = 0.08  # the same on a usual page of the book (a chapter opening starts lower)
    quality: float = 0  # share of confidently read lines, for choosing between duplicate scans

    @property
    def width(self):
        return max(1.0, self.right - self.left)


def ink_mask(page):
    with Image.open(page.image) as im:
        return np.asarray(im.convert("L")) < 128


def _runs(bool_rows, min_len):
    """For each row of a 2-D boolean array: (row, start, end) of its longest run, if at least min_len."""
    out = []
    for y, row in enumerate(bool_rows):
        if row.sum() < min_len:
            continue
        d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        i = int(np.argmax(ends - starts))
        if ends[i] - starts[i] >= min_len:
            out.append((y, int(starts[i]), int(ends[i])))
    return out


def _merge_runs(runs, max_thick):
    """Join runs on adjacent rows into rules: (position, start, end, thickness); thicker bands are not rules."""
    rules, cur = [], None
    for y, s, e in runs:
        if cur and y - cur[1] <= 1 and s < cur[3] and e > cur[2]:
            cur = [cur[0], y, min(cur[2], s), max(cur[3], e)]
        else:
            if cur:
                rules.append(cur)
            cur = [y, y, s, e]
    if cur:
        rules.append(cur)
    return [((a + b) // 2, s, e, b - a + 1) for a, b, s, e in rules if b - a + 1 <= max_thick]


def find_rules(mask, lines):
    """Horizontal and vertical rules outside the text lines.

    Three neighbouring rows are OR-ed so a rule tilted by a pixel or two over its
    length still forms one run; the text boxes are erased first so the long
    kashida-joined baselines of Persian words cannot pass for rules.
    """
    m = mask.copy()
    for ln in lines:
        x0, y0, x1, y1 = ln.bbox
        m[max(0, y0 + 2):max(0, y1 - 2), max(0, x0 + 2):max(0, x1 - 2)] = False
    H, W = m.shape
    rows = m[:-2] | m[1:-1] | m[2:]
    hr = _merge_runs(_runs(rows, int(0.12 * W)), max_thick=10)
    cols = (m[:, :-2] | m[:, 1:-1] | m[:, 2:]).T
    vr = _merge_runs(_runs(cols, int(0.06 * H)), max_thick=10)
    return hr, vr


_DIGIT_RUN = re.compile(f"[{DIGITS}]+")


def page_number_candidates(text):
    """Possible printed page numbers in a running header.

    The number sits at one end of the header next to an ornament (◘ in this
    book), and the recognizer often reads the ornament as a digit or a letter
    glued to the number: "۴۱۴ما چگونه" on page 14, "است؟۳۹۵" on page 39,
    "B ۴۴۲ا ما" on page 442. The digit run at each end of the text gives three
    candidates (the run, and the run without its first or last digit); `order`
    keeps the one that agrees with the neighbouring pages.
    """
    junk = " -–—.:،؛BGOoا"
    cands = []
    runs = list(_DIGIT_RUN.finditer(text))
    for m, outside in ((runs[0], text[:runs[0].start()]), (runs[-1], text[runs[-1].end():])) if runs else ():
        r = ascii_digits(m.group())
        if outside.strip(junk) or len(r) > 5:
            continue  # not at the end of the line: a number inside the text, not the page number
        for c in (r, r[1:], r[:-1]):
            if c and int(c) > 0 and int(c) not in cands:
                cands.append(int(c))
    return cands


def _dense_blocks(mask, block=8, density=0.15):
    """8x8-pixel blocks that are at least 15% ink: pictures and letters, but not scattered scanner specks."""
    H, W = mask.shape
    h, w = H // block, W // block
    return mask[:h * block, :w * block].reshape(h, block, w, block).mean(axis=(1, 3)) > density


_ARABIC = re.compile(r"[\u0600-\u06FF]")


def _readable(rows):
    """How much text ParisaOCR read with confidence: Arabic-script letters in lines of 80% or more."""
    return sum(len(_ARABIC.findall(r["text"])) for r in rows if r["conf"] >= 80 and len(r["text"].strip()) >= 3)


def orient_figures(layouts, ocr_dir, read):
    """Turn figure pages upright. Maps are often printed sideways; each figure page is read again turned
    90° each way, and a turn wins when ParisaOCR reads at least twice as much text with confidence.
    (Tesseract's orientation detection misjudges the small labels of Persian maps.)
    READ(inputs, out_dir, formats) runs ParisaOCR, as `convert` sets it up."""
    figs = [L for L in layouts if L.kind == "figure"]
    rot = pathlib.Path(ocr_dir) / "rotated"
    rot.mkdir(parents=True, exist_ok=True)
    todo = []
    for L in figs:
        for angle in (90, 270):
            img = rot / f"{L.page.id}-r{angle}.png"
            if not (rot / "jsonl" / f"{img.stem}.jsonl").exists():
                with Image.open(L.page.image) as im:
                    im.rotate(angle, expand=True, fillcolor=255 if im.mode in ("1", "L") else (255, 255, 255)).save(img)
                todo.append(str(img))
    if todo:
        read(todo, rot, "jsonl")
    undecided = []
    for L in figs:
        scores = {0: _readable([{"text": l.text, "conf": l.conf} for l in L.page.lines])}
        for angle in (90, 270):
            f = rot / "jsonl" / f"{L.page.id}-r{angle}.jsonl"
            scores[angle] = _readable(map(json.loads, f.read_text(encoding="utf-8").splitlines())) if f.exists() else 0
        best = max(scores, key=scores.get)
        L.rotate = best if best and scores[best] >= max(20, 2 * scores[0]) else 0
        if scores[best] < 20:
            undecided.append(L)
    # too little text to tell (a world map with a few labels): follow the figure page next to it
    decided = [L for L in figs if L not in undecided]
    for L in undecided:
        near = [D for D in decided if abs(D.page.index - L.page.index) <= 2]
        if near:
            L.rotate = min(near, key=lambda D: abs(D.page.index - L.page.index)).rotate


def _is_noise(line):
    """Specks and dirt the detector boxed: one or two letters read with low confidence."""
    t = line.text.strip()
    return line.conf < 50 or (len(t) <= 2 and not is_digits(t) and line.conf < 90)


def analyze(page, header_like=lambda text: False, top_zone=0.1, foot_zone=0.88):
    """TOP_ZONE, FOOT_ZONE: how far down the page (as a share of its height) a running header may start
    and a page number at the foot may end; they depend on the scan's margins (see `analyze_all`)."""
    L = Layout(page)
    H, W = page.height, page.width
    lines = sorted((l for l in page.lines if not _is_noise(l)), key=lambda l: (l.y0, -l.x1))
    good = [l for l in lines if l.conf >= GOOD_CONF and len(l.text) >= 2]
    L.quality = sum(l.conf for l in good) / max(1, sum(len(l.text) > 0 for l in page.lines)) / 100 * min(1.0, len(good) / 10)
    mask = ink_mask(page)
    hr, vr = find_rules(mask, good) if good else ([], [])

    # Ink that no confidently read line or rule accounts for: pictures, maps, calligraphy, torn scans.
    m = mask.copy()
    for y, s, e, t in hr:
        m[max(0, y - t - 2):y + t + 3, s:e] = False
    for x, s, e, t in vr:
        m[s:e, max(0, x - t - 2):x + t + 3] = False
    dense = _dense_blocks(m)
    covered = np.zeros_like(dense)
    for l in good:
        covered[l.y0 // 8:l.y1 // 8 + 1, l.x0 // 8:l.x1 // 8 + 1] = True
    outside = dense & ~covered
    if not good or outside.mean() > 0.04:
        if dense.mean() < 0.003:
            L.kind = "blank"
            return L
        ys, xs = np.nonzero(outside)
        y0, y1 = np.percentile(ys, 1) * 8, np.percentile(ys, 99) * 8
        x0, x1 = np.percentile(xs, 1) * 8, np.percentile(xs, 99) * 8
        box = (int(x0) - 8, int(y0) - 8, int(x1) + 16, int(y1) + 16)
        beside = [l for l in good if not _overlaps(l.bbox, box)]
        # A large picture is a figure page unless the page carries running text beside it (a chapter
        # opening under an illustration); then the picture is a region of a text page.
        if not good or len(good) < 5 or ((y1 - y0) * (x1 - x0) > 0.35 * H * W and len(beside) < 5):
            L.kind = "figure"
            ys, xs = np.nonzero(_dense_blocks(mask))
            L.crop = (max(0, int(xs.min() * 8) - 8), max(0, int(ys.min() * 8) - 8),
                      min(W, int(xs.max() * 8) + 16), min(H, int(ys.max() * 8) + 16))
            return L
        L.regions.append(Region("figure", box))

    # Running header: the top row, narrower than the text block, carrying a page number or a text that
    # repeats at the top of other pages. The detector often boxes its parts apart (a centred title and
    # the page number at the outer edge); they are joined right to left.
    top = lines[0]
    row = _row_of(lines, top)
    block = float(np.percentile([l.w for l in lines], 90))  # the width of the text block
    narrow = all(l.w < 0.75 * block for l in row) or (  # a title beside the page number may be long
        len(row) >= 2 and any(is_digits(l.text.strip(" -–—.")) for l in row) and all(l.w < 0.92 * block for l in row))
    if top.y0 < top_zone * H and len(lines) > 3 and len(row) <= 3 and narrow:
        head = _joined(row)
        cands = page_number_candidates(head.text)
        if cands or header_like(head.text):
            L.header, L.number_candidates = head, cands
            lines = [l for l in lines if l not in row]

    # Page number at the foot of the page: a bottom row of short pieces, one of them only a number
    # ("۱۲", "- ۱۲ -"); the others are stamps and shelf marks ("5-583"), without Persian words (a last
    # contents entry, "فهرست منابع ...... ۴۷۵", is not a page number).
    bottom = max(lines, key=lambda l: l.y1)
    row = _row_of(lines, bottom)
    numbers = [t for t in (l.text.strip(" -–—.()[]") for l in row) if is_digits(t) and len(t) <= 4]
    if bottom.y1 > foot_zone * H and len(lines) > 3 and len(numbers) == 1 and all(l.w < 0.3 * W for l in row) \
            and sum(len(_ARABIC.findall(l.text)) for l in row if not is_digits(l.text.strip(" -–—.()[]"))) <= 2:
        L.footer = _joined(row)
        if not L.number_candidates and int(ascii_digits(numbers[0])) > 0:
            L.number_candidates = [int(ascii_digits(numbers[0]))]
        lines = [l for l in lines if l not in row]

    # Tables: two or more vertical rules; the table spans them and the horizontal rules they cross.
    # Straight lines inside a picture (posts, masts, frames) are not a table: when the rules lie in a
    # figure region, the region grows to take them in (they were left out of its ink).
    if len(vr) >= 2:
        vy0, vy1 = min(s for _, s, _, _ in vr), max(e for _, _, e, _ in vr)
        vx0, vx1 = min(x for x, _, _, _ in vr), max(x for x, _, _, _ in vr)
        hs = [r for r in hr if vy0 - 12 <= r[0] <= vy1 + 12 and r[2] > vx0 - 12 and r[1] < vx1 + 12]
        box = (min([vx0] + [r[1] for r in hs]), vy0, max([vx1] + [r[2] for r in hs]), vy1)
        drawing = next((r for r in L.regions if r.kind == "figure" and _overlap_share(box, r.bbox) > 0.3), None)
        if drawing is not None:
            drawing.bbox = (min(drawing.bbox[0], box[0]), min(drawing.bbox[1], box[1]),
                            max(drawing.bbox[2], box[2]), max(drawing.bbox[3], box[3]))
        else:
            L.regions.append(Region("table", box))
            L.regions = [r for r in L.regions if r.kind == "table" or not _overlaps(r.bbox, L.regions[-1].bbox)]
        hr = [r for r in hr if r not in hs]

    # Footnote separator: the lowest remaining horizontal rule in the lower part of the page with text below it.
    for y, s, e, _ in sorted(hr, reverse=True):
        if y > 0.35 * H and any(l.y0 > y for l in lines) and any(l.y1 < y for l in lines):
            L.rule_y = y
            break
    if L.rule_y is not None:
        L.notes = [l for l in lines if l.yc > L.rule_y]
        lines = [l for l in lines if l.yc <= L.rule_y]

    for r in L.regions:
        x0, y0, x1, y1 = r.bbox
        r.lines = [l for l in lines if x0 - 5 <= (l.x0 + l.x1) / 2 <= x1 + 5 and y0 - 5 <= l.yc <= y1 + 5]
        lines = [l for l in lines if l not in r.lines]
    L.body = lines
    if L.rule_y is None:
        split_unruled_notes(L)
    learned_notes(L)
    metrics(L)
    return L


def learned_notes(L):
    """When the lines carry learned roles (`roles.annotate`), the footnote area is the tail of the page the
    note model marks: from the first line it is sure of (p >= 0.5) down, as long as every line below keeps
    some probability. The rules' area is kept where a separator rule was found (the rules are strong there)
    and the model asked only on pages without one; PARISAOCR_ROLES_MODE=learned asks it on every page."""
    all_lines = sorted(L.body + L.notes, key=lambda l: l.y0)
    if not all_lines or any(getattr(l, "p_note", None) is None for l in all_lines):
        return
    if os.environ.get("PARISAOCR_ROLES_MODE", "hybrid") == "hybrid" and L.rule_y is not None:
        return
    # a page number the rules did not take as the footer sits under the notes; it is not part of the tail
    lines = [l for l in all_lines if getattr(l, "role", "") != "pagenum" and not is_digits(l.text.strip(" -–—.()[]"))]
    start = None
    for i, l in enumerate(lines):
        tail = lines[i:]
        if l.p_note >= 0.5 and len(tail) < len(lines) and all(x.p_note >= 0.3 for x in tail) \
                and sum(x.p_note for x in tail) / len(tail) >= 0.6 and (len(tail) > 1 or l.p_note >= 0.85):
            start = i
            break
    L.notes = lines[start:] if start is not None else []
    L.body = [l for l in all_lines if l not in L.notes]


def metrics(L):
    """Margins, line height and pitch of the text block. The margins are taken at the 80th percentile of
    the wide lines' ends, so indented first lines and block quotes do not pull them inwards."""
    lines = L.body
    wide = [l for l in lines if l.w > 0.5 * L.page.width] or lines
    if wide:
        L.right = float(np.percentile([l.x1 for l in wide], 80))
        L.left = float(np.percentile([l.x0 for l in wide], 20))
        L.lh = float(np.median([l.h for l in wide]))
        steps = [b.yc - a.yc for a, b in zip(lines, lines[1:]) if 0 < b.yc - a.yc < 2.5 * L.lh]
        L.pitch = float(np.median(steps)) if steps else 1.3 * L.lh
        sure = [l for l in lines if l.conf >= 80] or lines  # not a watermark or a stamp read as a word
        L.top = min(l.y0 for l in sure) / L.page.height


_NOTE_LINE = re.compile(rf"^[\s\-–]*(?:[{DIGITS}]{{1,2}}\s*[-–—.~]|\*{{1,3}})")  # "۱- …", "*…"


def split_unruled_notes(L):
    """Footnotes on a page whose separator rule did not survive the scan: the lines below a gap in the
    lower part of the page, starting with a note number set smaller or tighter than the text above, or
    starting with asterisks."""
    lines = sorted(L.body, key=lambda l: l.y0)
    if len(lines) < 4:
        return

    def pitch(ls):
        steps = [b.yc - a.yc for a, b in zip(ls, ls[1:]) if b.yc > a.yc]
        return float(np.median(steps)) if steps else None

    for j in range(2, len(lines)):
        above, below = lines[:j], lines[j:]
        lh = float(np.median([l.h for l in above]))
        if lines[j].y0 < 0.5 * L.page.height or not _NOTE_LINE.match(lines[j].text) or lines[j].y0 - lines[j - 1].y1 < 0.6 * lh:
            continue
        smaller = float(np.median([l.h for l in below])) < 0.88 * lh
        p_above, p_below = pitch(above), pitch(below)
        tighter = p_above is not None and p_below is not None and p_below < 0.85 * p_above
        stars = lines[j].text.lstrip(" -–").startswith("*")  # "*ماشا مخفف …": a note even in text-size type
        if smaller or tighter or stars:
            L.notes = below
            L.body = above
            return


def _row_of(lines, first):
    """The lines beside FIRST (overlapping it vertically by half the smaller height), right to left."""
    return sorted((l for l in lines if min(l.y1, first.y1) - max(l.y0, first.y0) > 0.5 * min(l.h, first.h)),
                  key=lambda l: -l.x1)


def _joined(row):
    """The pieces of one printed row as one line, read right to left."""
    if len(row) == 1:
        return row[0]
    box = (min(l.x0 for l in row), min(l.y0 for l in row), max(l.x1 for l in row), max(l.y1 for l in row))
    return Line(" ".join(l.text for l in row), box, min(l.conf for l in row), [w for l in row for w in l.words])


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _overlap_share(a, b):
    """The share of box A's area that box B covers."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    area = (a[2] - a[0]) * (a[3] - a[1])
    return w * h / area if w > 0 and h > 0 and area > 0 else 0.0


def _norm_header(text):
    return "".join(c for c in text if c.isalpha() and not c.isascii())


STAMP_TEXT = re.compile(r"www\.|https?://|\.(?:com|net|org|ir|me|info|co)\b|@\w{3,}|t\.me/|telegram", re.I)


def burned_stamps(pages):
    """A stamp burned into the scans — a site, e-mail or channel name of up to 60 characters in a margin
    of the page (the top or bottom 12%, the left or right 10%), at the same place on two or more pages —
    is taken out of the pages' lines and its boxes noted in Page.stamps (figure crops white them out).
    Returns {text: [PDF pages]}."""
    groups = []  # [text key, (y, x) as shares of the page, [(page, line)]]
    for p in pages:
        for l in p.lines:
            t = l.text.strip()
            margin = l.y1 <= 0.12 * p.height or l.y0 >= 0.88 * p.height or l.x1 <= 0.1 * p.width or l.x0 >= 0.9 * p.width
            if not (margin and 3 <= len(t) <= 60 and STAMP_TEXT.search(t)):
                continue
            key, pos = re.sub(r"\s+", "", t.lower()), (l.y0 / p.height, l.x0 / p.width)
            for g in groups:
                if abs(g[1][0] - pos[0]) <= 0.02 and abs(g[1][1] - pos[1]) <= 0.05 \
                        and difflib.SequenceMatcher(None, g[0], key).ratio() >= 0.8:
                    g[2].append((p, l))
                    break
            else:
                groups.append([key, pos, [(p, l)]])
    found = {}
    for _, _, hits in groups:
        if len({p.index for p, _ in hits}) < 2:
            continue
        for p, l in hits:
            p.lines.remove(l)
            p.stamps.append(l.bbox)
        text = max((l.text.strip() for _, l in hits), key=len)
        found.setdefault(text, []).extend(sorted({p.index for p, _ in hits}))
    return found


def analyze_all(pages, roles_dir=None, report=None, labels_dir=None):
    """Analyze every page. Headers are recognized by a page number or by text repeated on 3+ other pages.
    Where a header or a page number may sit depends on the scan's margins: the zones are set from where
    the first and the last line of a usual page of this book lie.

    ROLES_DIR: the learned line-role models (`roles.annotate`) the layout and structure rules consult —
    the bundled ones by default, or the PARISAOCR_ROLES environment variable ("{slug}" in it stands for
    the book's directory name), or "none" for the rules alone. REPORT, a dict, collects "stamps" (the stamps
    burned into the scans that were left out, `burned_stamps`) and "roles" (which models were used).
    LABELS_DIR (or PARISAOCR_LABELS, "{slug}" as above): a page labeller's labels (`labels`), which then decide
    the structure of the pages they cover; the line-role models are not used."""
    found = burned_stamps(pages)
    slug = pages[0].image.resolve().parents[3].name if pages else ""
    labels_dir = labels_dir or os.environ.get("PARISAOCR_LABELS")
    labelled = labels.attach(pages, pathlib.Path(labels_dir.replace("{slug}", slug)).expanduser()) if labels_dir else 0
    roles_dir = "none" if labelled else str(roles_dir or os.environ.get("PARISAOCR_ROLES") or roles.BUNDLED)
    used = None
    if roles_dir != "none":
        model_dir = pathlib.Path(roles_dir.replace("{slug}", slug)).expanduser()
        roles.annotate(pages, lambda p: p.image, model_dir)
        used = roles.describe(model_dir)
    if labelled:
        used = f"page labels ({labelled} of {len(pages)} pages)"
    if report is not None:
        report.update(stamps=found, roles=used)
    # quartiles rather than medians: in a short book chapter openings (starting low) can be half the pages
    full = [p for p in pages if sum(not _is_noise(l) for l in p.lines) >= 8]
    first = float(np.percentile([min(l.y0 for l in p.lines if not _is_noise(l)) / p.height for p in full], 25)) if full else 0.06
    last = float(np.percentile([max(l.y1 for l in p.lines if not _is_noise(l)) / p.height for p in full], 75)) if full else 0.92
    top_zone, foot_zone = first + 0.04, last - 0.04
    counts = {}
    for p in pages:
        if p.lines:
            top = min(p.lines, key=lambda l: l.y0)
            if top.y0 < top_zone * p.height:
                k = _norm_header(_joined(_row_of(p.lines, top)).text)
                counts[k] = counts.get(k, 0) + 1
    frequent = [k for k, n in counts.items() if n >= 4 and len(k) >= 4]

    def header_like(text):
        k = _norm_header(text)
        return len(k) >= 4 and any(difflib.SequenceMatcher(None, k, f).ratio() >= 0.8 for f in frequent)

    layouts = [analyze(p, header_like, top_zone, foot_zone) for p in pages]
    for L in layouts:
        labels.relayout(L, metrics)
    tops = [L.top for L in layouts if L.kind == "text" and len(L.body) >= 8]
    book_top = float(np.percentile(tops, 25)) if tops else 0.08
    for L in layouts:
        L.book_top = book_top
    return layouts


def hemistich_gap(mask, line, width):
    """x of the gap between the two halves of a verse line: a blank run near the middle of the line at
    least three times wider than any word space. Word boxes abut each other, so this reads the ink."""
    cols = mask[line.y0:line.y1, line.x0:line.x1].any(axis=0)
    if cols.size < 20:
        return None
    d = np.diff(np.concatenate(([1], cols.astype(np.int8), [1])))
    starts, ends = np.flatnonzero(d == -1)[1:], np.flatnonzero(d == 1)[1:]
    if len(starts) < 3:
        return None
    runs = ends - starts
    i = int(np.argmax(runs))
    second = np.partition(runs, -2)[-2]
    mid = line.x0 + (starts[i] + ends[i]) / 2
    rel = (line.x1 - mid) / max(1, line.w)
    if runs[i] >= 0.03 * width and runs[i] >= 3 * second and 0.3 < rel < 0.7:
        return float(mid)
    return None
