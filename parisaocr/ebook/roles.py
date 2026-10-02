"""Per-line layout features and learned line roles.

Every OCR line gets a row of plain numbers computed from the line boxes, the
text and the page image alone; none of the rules of `layout` and `structure`
takes part, the models here are meant to replace them. The features are,
in column order (`NAMES`):

  x0 y0 x1 y1 w         the box, normalised by the page width and height
  h_book h_page         line height over the book's median (lines read with
                        conf >= 70) and over the page's median
  cx_off                box centre minus the page centre (negative = left half)
  rank rank_rev n_lines position of the line by y on the page, from the top and
                        from the bottom, and the page's line count
  gap_up gap_down       distance to the line above / below (first and last line:
                        to the page edge) in book line heights, clipped to +-60
  first_y y_vs_book_top where the page's first line is, and the same against the
                        book's 25th percentile (a chapter opening starts lower)
  n_chars n_words digit_share latin_share starts_digit starts_num_mark ends_punct
  has_label_word is_number_only star_start
                        the text's shape: length, digits and Latin letters,
                        a leading number or "1." / "۱-" mark, trailing
                        punctuation, a label word (فصل, بخش, پیوست ...), a bare
                        number, a leading asterisk
  conf                  OCR confidence 0-100
  repeat_share          share of the book's pages whose first two lines carry
                        this text with the digits removed: a running header
  ink ink_core          dark-pixel share of the box, and of its middle rows
  page_pos aspect       page / last page, page image W / H
  prev_* next_*         h_book, n_chars, latin_share, starts_digit and ink of the
                        line above / below by y (-1 at the page edge)
  rule_above rule_len rules_above
                        a printed horizontal rule in the band between the line
                        above and this line (the footnote separator), its length
                        over the text span, and the rules above this line on
                        the page in all
  tail_small small_run_up
                        the share of small type (h_book < 0.9) from this line to
                        the foot of the page, and the run of small lines directly
                        above it

The definitions are those of the training corpus's make_features.py, column
for column, so its models apply. The models, one per task, are gradient-boosted
tree ensembles (scikit-learn HistGradientBoostingClassifier, exported by the
training repo as .npz node arrays and evaluated here with numpy, exactly as
scikit-learn would) trained on lines labelled by Gemini 3.8 Flash on sampled
pages of 85 scanned Persian books: the 13-class line role (header, pagenum,
heading, body, quote, verse, note, endnote, caption, table, figure, contents,
other), binary footnote and heading models, whether a footnote line starts a
note, and the heading level 1-3. The bundled set is `BUNDLED`. `annotate`
attaches the predictions to the Line objects; what to do with them is the
consumer's decision.
"""
import math
import pathlib
import re
import sys
from collections import Counter

import numpy as np
from PIL import Image

BUNDLED = pathlib.Path(__file__).resolve().parent.parent / "models" / "roles"

NEIGHBOUR = ["h_book", "n_chars", "latin_share", "starts_digit", "ink"]
NAMES = [
    "x0", "y0", "x1", "y1",
    "w", "h_book", "h_page", "cx_off",
    "rank", "rank_rev", "n_lines",
    "gap_up", "gap_down",
    "first_y", "y_vs_book_top",
    "n_chars", "n_words", "digit_share", "latin_share", "starts_digit", "starts_num_mark", "ends_punct",
    "has_label_word", "is_number_only", "star_start", "conf",
    "repeat_share",
    "ink", "ink_core",
    "page_pos", "aspect",
    *("prev_" + n for n in NEIGHBOUR), *("next_" + n for n in NEIGHBOUR),
    "rule_above", "rule_len", "rules_above",
    "tail_small", "small_run_up",
]
# model file -> Line attribute: the role name, P(footnote), P(heading), P(starts a note), heading level
MODELS = [("role", "role"), ("note", "p_note"), ("heading", "p_head"), ("start", "p_start"), ("level", "level")]

DIGITS = "0123456789" "۰۱۲۳۴۵۶۷۸۹" "٠١٢٣٤٥٦٧٨٩"
DIGIT_SET = frozenset(DIGITS)
RE_DIGIT = re.compile("[" + DIGITS + "]")
RE_NUM_MARK = re.compile(r"^[\s(\[«\"']*[0-9۰-۹]{1,3}\s*[.\-–)،:]")
RE_STAR = re.compile(r"^\s*[*٭]")
LEAD_STRIP = " \t\xa0()[]{}«»\"'"
END_PUNCT = frozenset(".،؛:!؟»\"")
LABEL_WORDS = ("فصل بخش پیوست ضمیمه فهرست مقدمه پیشگفتار یادداشت گفتار درس قسمت پرده صحنه "
               "کتاب‌نامه منابع نمایه").split()
RE_LABEL = re.compile(r"(?<![\w‌])(?:" + "|".join(map(re.escape, LABEL_WORDS)) + r")(?![\w‌])")
NUM_ONLY_STRIP = frozenset(" .…-–—()")
ARABIC_MAP = str.maketrans({"ي": "ی", "ك": "ک"})
GAP_CAP = 60.0
SMALL_H = 0.9           # h_book below this is "small type" (footnotes)
RULE_MIN_LEN = 0.15     # a rule row's longest dark run, as a share of the page's text span width
RULE_MAX_THICK = 6      # rows of ink a rule may have (max over columns)
RULE_MAX_EXTENT = 12    # rows a group of rule rows may persist over; longer is a figure or a black bar
RULE_MERGE_GAP = 2      # blank rows allowed inside one rule (a scan may lose a row of a thick rule)
RULE_EDGE = 8           # a dark group starting within this many rows of the image top is the scan's border


def norm_repeat(text):
    """Text for the running-header count: digits removed, whitespace collapsed, ي/ك -> ی/ک."""
    return " ".join(RE_DIGIT.sub("", text.translate(ARABIC_MAP)).split())


def text_features(text):
    t = " ".join((text or "").split())
    ns = t.replace(" ", "")
    n_ns = len(ns)
    digits = sum(c in DIGIT_SET for c in ns)
    latin = sum(("a" <= c <= "z") or ("A" <= c <= "Z") for c in ns)
    lead = t.lstrip(LEAD_STRIP)
    rest = "".join(c for c in t if c not in NUM_ONLY_STRIP)
    return {
        "n_chars": len(t),
        "n_words": len(t.split()),
        "digit_share": digits / n_ns if n_ns else 0.0,
        "latin_share": latin / n_ns if n_ns else 0.0,
        "starts_digit": int(bool(lead) and lead[0] in DIGIT_SET),
        "starts_num_mark": int(bool(RE_NUM_MARK.match(t))),
        "ends_punct": int(bool(t) and t[-1] in END_PUNCT),
        "has_label_word": int(bool(RE_LABEL.search(t.translate(ARABIC_MAP)))),
        "is_number_only": int(bool(rest) and all(c in DIGIT_SET for c in rest)),
        "star_start": int(bool(RE_STAR.match(t))),
    }


def ink_features(dark, bbox, W, H):
    """(ink, ink_core) of a box on the page's boolean dark-pixel array: the share of dark pixels in the
    box, and in the middle half of its rows."""
    x0, y0, x1, y1 = bbox
    X0, Y0 = max(0, int(math.floor(x0))), max(0, int(math.floor(y0)))
    X1, Y1 = min(W, int(math.ceil(x1))), min(H, int(math.ceil(y1)))
    if X1 <= X0 or Y1 <= Y0:
        return 0.0, 0.0
    q = (Y1 - Y0) // 4
    a, b = Y0 + q, Y1 - q
    if b <= a:
        a, b = Y0, Y1
    return float(dark[Y0:Y1, X0:X1].mean()), float(dark[a:b, X0:X1].mean())


def longest_runs(rows):
    """Per row of a boolean 2-D array, the length of its longest run of True."""
    R, C = rows.shape
    out = np.zeros(R, dtype=np.int64)
    if R == 0 or C == 0:
        return out
    pad = np.zeros((R, C + 2), dtype=np.int8)
    pad[:, 1:-1] = rows
    d = np.diff(pad, axis=1)
    r_idx, starts = np.nonzero(d == 1)  # row-major, so the k-th start of a row pairs with its k-th end
    _, ends = np.nonzero(d == -1)
    np.maximum.at(out, r_idx, ends - starts)
    return out


def rules_in_band(band, min_len, skip_top=0):
    """The longest dark run of each printed rule in a band of the dark array (rows x the text span's columns):
    rows whose longest dark run is >= min_len, grouped with gaps of up to RULE_MERGE_GAP rows; a group is a
    rule when it persists over at most RULE_MAX_EXTENT rows, its ink is at most RULE_MAX_THICK rows thick in
    every column (a skewed thin rule passes, a black bar does not) and it starts at or below row skip_top."""
    counts = band.sum(axis=1)
    cand = np.nonzero(counts >= min_len)[0]  # a row's longest run cannot exceed its dark count
    if not len(cand):
        return []
    lr = longest_runs(band[cand])
    keep = lr >= min_len
    hit, lr = cand[keep], lr[keep]
    rules, i = [], 0
    while i < len(hit):
        j = i
        while j + 1 < len(hit) and hit[j + 1] - hit[j] <= RULE_MERGE_GAP + 1:
            j += 1
        top, bot = int(hit[i]), int(hit[j])
        if (top >= skip_top and bot - top + 1 <= RULE_MAX_EXTENT
                and int(band[top:bot + 1].sum(axis=0).max()) <= RULE_MAX_THICK):
            rules.append(int(lr[i:j + 1].max()))
        i = j + 1
    return rules


def rule_features(dark, boxes, W, H):
    """(rule_above, rule_len, rules_above) per box of `boxes` (the page's lines sorted by y0): the rules in
    the band between the lowest line bottom so far and each line's top (the first band starts at the page top,
    where the scan's border is not a rule), within the columns of the page's text span."""
    X0 = max(0, int(math.floor(min(b[0] for b in boxes))))
    X1 = min(W, int(math.ceil(max(b[2] for b in boxes))))
    span = X1 - X0
    if span <= 0:
        return [(0, 0.0, 0)] * len(boxes)
    min_len = RULE_MIN_LEN * span
    out, total, floor_y = [], 0, 0
    for b in boxes:
        a, bot = floor_y, min(H, int(math.floor(b[1])))
        rules = rules_in_band(dark[a:bot, X0:X1], min_len, RULE_EDGE if a == 0 else 0) if bot > a else []
        total += len(rules)
        out.append((int(bool(rules)), max(rules) / span if rules else 0.0, total))
        floor_y = max(floor_y, min(H, int(math.ceil(b[3]))))
    return out


def features(pages, image_of=None):
    """The features of every line of every page: (NAMES, X float32 [n, len(NAMES)], [(page, line)] per row).
    PAGES as `source.load` gives them, IMAGE_OF(page) the page's PNG (None: page.image; a page whose image is
    gone gets -1 in the ink and rule columns). Rows come in the pages' order and, within a page, in the page's
    line order; a page without lines gives none."""
    image_of = image_of or (lambda page: page.image)
    n_pages = max((p.index for p in pages), default=0)
    # Pass 1, one image decode per page: ink and rules per line, the lines sorted by y.
    recs = []
    for p in pages:
        if not p.lines:
            continue
        path = pathlib.Path(image_of(p))
        if path.exists():
            with Image.open(path) as im:
                W, H = im.size
                dark = np.asarray(im.convert("L")) < 128
        else:
            W, H, dark = p.width, p.height, None
        rows = [{"line": l, "idx": k, "h": max(1e-6, l.y1 - l.y0), "conf": float(l.conf or 0.0)} for k, l in enumerate(p.lines)]
        srt = sorted(rows, key=lambda r: r["line"].y0)  # stable: ties keep the page's order
        if dark is None:
            for r in rows:
                r.update(ink=-1.0, ink_core=-1.0, rule_above=-1, rule_len=-1.0, rules_above=-1)
        else:
            for r in rows:
                r["ink"], r["ink_core"] = ink_features(dark, r["line"].bbox, W, H)
            for r, (above, length, n_above) in zip(srt, rule_features(dark, [r["line"].bbox for r in srt], W, H)):
                r.update(rule_above=above, rule_len=length, rules_above=n_above)
        recs.append({"page": p, "W": W, "H": H, "srt": srt, "first_y": srt[0]["line"].y0 / H})
    if not recs:
        return list(NAMES), np.zeros((0, len(NAMES)), np.float32), []
    # Book-level statistics.
    heights = [r["h"] for q in recs for r in q["srt"] if r["conf"] >= 70] or [r["h"] for q in recs for r in q["srt"]]
    med_book = float(np.median(heights)) or 1.0
    q25_first_y = float(np.percentile([q["first_y"] for q in recs], 25))
    repeats = Counter()
    for q in recs:
        for t in {norm_repeat(r["line"].text) for r in q["srt"][:2]}:
            if len(t) >= 3:
                repeats[t] += 1
    # Pass 2: the features, per page in reading order, written out in the page's line order.
    X, index = [], []
    for q in recs:
        W, H, srt, n = q["W"], q["H"], q["srt"], len(q["srt"])
        med_page = float(np.median([r["h"] for r in srt])) or 1.0
        feats = []
        for k, r in enumerate(srt):
            l = r["line"]
            x0, y0, x1, y1 = l.bbox
            gap_up = (y0 - srt[k - 1]["line"].y1) / med_book if k else y0 / med_book
            gap_down = (srt[k + 1]["line"].y0 - y1) / med_book if k < n - 1 else (H - y1) / med_book
            d = {"x0": x0 / W, "y0": y0 / H, "x1": x1 / W, "y1": y1 / H, "w": (x1 - x0) / W,
                 "h_book": r["h"] / med_book, "h_page": r["h"] / med_page, "cx_off": (x0 + x1) / 2 / W - 0.5,
                 "rank": k, "rank_rev": n - 1 - k, "n_lines": n,
                 "gap_up": max(-GAP_CAP, min(GAP_CAP, gap_up)), "gap_down": max(-GAP_CAP, min(GAP_CAP, gap_down)),
                 "first_y": q["first_y"], "y_vs_book_top": q["first_y"] - q25_first_y}
            d.update(text_features(l.text))
            d["conf"] = r["conf"]
            nt = norm_repeat(l.text)
            d["repeat_share"] = repeats[nt] / len(recs) if len(nt) >= 3 else 0.0
            d["ink"], d["ink_core"] = r["ink"], r["ink_core"]
            d["page_pos"] = q["page"].index / n_pages if n_pages else 0.0
            d["aspect"] = W / H
            d["rule_above"], d["rule_len"], d["rules_above"] = r["rule_above"], r["rule_len"], r["rules_above"]
            feats.append(d)
        for k, d in enumerate(feats):
            for prefix, nb in (("prev_", feats[k - 1] if k else None), ("next_", feats[k + 1] if k + 1 < n else None)):
                for name in NEIGHBOUR:
                    d[prefix + name] = nb[name] if nb is not None else -1
        small = [d["h_book"] < SMALL_H for d in feats]
        run = 0
        for k, d in enumerate(feats):
            d["small_run_up"] = run
            run = run + 1 if small[k] else 0
        tail = 0
        for k in range(n - 1, -1, -1):
            tail += int(small[k])
            feats[k]["tail_small"] = tail / (n - k)
        for r, d in sorted(zip(srt, feats), key=lambda rd: rd[0]["idx"]):
            X.append([d[c] for c in NAMES])
            index.append((q["page"], r["line"]))
    return list(NAMES), np.array(X, dtype=np.float32), index


class Forest:
    """A gradient-boosted tree ensemble as the training repo's export_models.py writes it from a fitted
    scikit-learn HistGradientBoostingClassifier: the trees (iteration-major, then class) as node arrays padded
    to the largest tree, with the feature names, the classes, the per-class baseline and the trees per
    iteration `k` (1 for a binary model). `raw`, `proba` and `predict` give what scikit-learn's would, a
    missing value (NaN) going the way the node learned."""

    def __init__(self, path):
        d = np.load(path, allow_pickle=False)
        self.names = [str(n) for n in d["names"]]
        self.classes = d["classes"].tolist()
        self.labels = [str(s) for s in d["labels"]] if "labels" in d else None  # class names (the role model)
        self.baseline = d["baseline"].astype(np.float64).ravel()
        self.k = int(d["k"])
        self.feature, self.threshold, self.left, self.right = d["feature"], d["threshold"], d["left"], d["right"]
        self.missing_left, self.value, self.leaf = d["missing_left"], d["value"], d["leaf"]
        self.meta = {key: str(d[key]) for key in ("version", "trained", "books", "labeller") if key in d}

    def raw(self, X, chunk=64):
        """The raw scores [n, k]: baseline plus the leaf values of every tree, CHUNK trees at a time."""
        X = np.asarray(X, dtype=np.float64)
        n, rows = len(X), np.arange(len(X))
        out = np.tile(self.baseline, (n, 1))
        for t0 in range(0, len(self.feature), chunk):
            t = np.arange(t0, min(len(self.feature), t0 + chunk))
            tt = t[:, None]
            idx = np.zeros((len(t), n), dtype=np.int64)
            for _ in range(256):
                leaf = self.leaf[tt, idx]
                if leaf.all():
                    break
                x = X[rows[None, :], self.feature[tt, idx]]
                go_left = np.where(np.isnan(x), self.missing_left[tt, idx], x <= self.threshold[tt, idx])
                idx = np.where(leaf, idx, np.where(go_left, self.left[tt, idx], self.right[tt, idx]))
            values = self.value[tt, idx]
            for c in range(self.k):
                out[:, c] += values[t % self.k == c].sum(axis=0)
        return out

    def proba(self, X):
        raw = self.raw(X)
        if self.k == 1:  # binary: the logistic of the single score, classes [0, 1]
            p = 1 / (1 + np.exp(-raw[:, 0]))
            return np.stack([1 - p, p], axis=1)
        e = np.exp(raw - raw.max(axis=1, keepdims=True))
        return e / e.sum(axis=1, keepdims=True)

    def predict(self, X):
        return np.asarray(self.classes)[self.proba(X).argmax(axis=1)]


def describe(model_dir):
    """What the models in MODEL_DIR are, for a report ("models 0.1, 85 books"); None without a role model."""
    path = pathlib.Path(model_dir) / "role.npz"
    if not path.exists():
        return None
    meta = Forest(path).meta
    return f"models {meta.get('version', '?')}" + (f", {meta['books']} books" if meta.get("books") else "")


def annotate(pages, image_of=None, model_dir=BUNDLED):
    """Computes the features (PAGES and IMAGE_OF as for `features`) and attaches the predictions of the models
    in MODEL_DIR (role.npz, note.npz, heading.npz, start.npz, level.npz) to every Line: role (the 13-class
    role's name), p_note, p_head, p_start (probabilities), level (1-3, for every line; 0 when there is no level
    model). A missing model leaves its attribute unset, with a warning. -> lines annotated."""
    names, X, index = features(pages, image_of)
    if not index:
        return 0
    model_dir = pathlib.Path(model_dir)
    for name, attr in MODELS:
        path = model_dir / f"{name}.npz"
        if not path.exists():
            print(f"parisaocr: no {path}: line.{attr} {'is 0' if name == 'level' else 'not set'}", file=sys.stderr)
            if name == "level":
                for _, l in index:
                    l.level = 0
            continue
        m = Forest(path)
        if m.names != names:
            missing, extra = [n for n in m.names if n not in names], [n for n in names if n not in m.names]
            raise ValueError(f"{path} was trained on {len(m.names)} features, this code computes {len(names)}: "
                             + (f"not computed here {missing}; not in the model {extra}" if missing or extra
                                else "the same names in a different order") + "; retrain the models")
        if name == "role":
            vals = [m.labels[int(c)] for c in m.predict(X)]
        elif name == "level":
            vals = [int(v) for v in m.predict(X)]
        else:
            vals = m.proba(X)[:, m.classes.index(1)].tolist()
        for (_, l), v in zip(index, vals):
            setattr(l, attr, v)
    return len(index)
