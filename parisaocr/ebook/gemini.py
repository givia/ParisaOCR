"""Gemini as the page labeller of `parisaocr epub --llm`.

Every page image goes to Gemini with its OCR lines tagged by number, and Gemini says what each line is (heading and
its level, footnote or endnote and its number, running header, page number, byline, quote, verse, ...), the page's
type and printed page number, and the entries of a contents page. Two questions about the whole book follow: the
bibliographic data from the cover, title and imprint pages, and the book's outline from every heading found (which
headings open a unit, of what kind, under what title). `labels` then lets these answers decide the structure.

The answers are kept in the book's work directory (gemini-MODEL/p-NNN.json, book_meta.json, book_outline.json), so a
second run, or a rebuild from `--review`, asks nothing again; pages Gemini could not answer are asked again on the
next run, except those it blocked. The key: GEMINI_API_KEY, or the file ~/.config/gemini/api_key (one line).
Gemini 3.8 Flash costs about $0.004 a page, $1.2 for a book of 300 pages.

Another model can take Gemini's place with the same questions: MODEL openrouter:VENDOR/NAME (e.g.
openrouter:google/gemma-4-31b-it) is asked through OpenRouter, whose OpenAI-style API is translated to and from
generateContent's here (class OpenRouter); key OPENROUTER_API_KEY or ~/.config/openrouter/api_key. OpenRouter is
asked to route only to providers that honour the JSON schema and neither keep nor train on what is sent.
"""
import base64
import http.client
import io
import json
import os
import pathlib
import random
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw, ImageFont

DEFAULT_MODEL = "gemini-3.8-flash"
API = "https://generativelanguage.googleapis.com/v1beta"
OPENROUTER = "https://openrouter.ai/api/v1"
# OpenRouter routes a model to one of its providers: only to those that support every parameter sent (the JSON
# schema), take no data for training and keep none (the pages are book scans). PARISAOCR_OPENROUTER_PROVIDER (JSON)
# adds to this, e.g. to name providers or quantizations; these three always hold.
OPENROUTER_PROVIDER = {"require_parameters": True, "data_collection": "deny", "zdr": True}
PRICES = {"gemini-3.8-flash": (0.75, 3.75), "gemini-3.7-flash": (0.75, 3.75)}  # USD per million tokens: input, output
_OPENROUTER_MODELS = {}  # OpenRouter's model list (id -> entry), read once
_OPENROUTER_LIST_FAILED = []  # [True] once reading the list failed: not asked again in this run
MAX_H = 2000  # the page image is sent at most this high
FONTS = ["/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
         "/usr/share/fonts/noto/NotoSans-Bold.ttf", str(pathlib.Path(__file__).resolve().parent.parent / "fonts" / "Vazirmatn-Bold.ttf")]
SAFETY = [{"category": c, "threshold": "BLOCK_NONE"} for c in (
    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]

ROLES = ["header", "pagenum", "heading", "byline", "body", "quote", "epigraph", "verse", "note", "endnote", "reference",
         "caption", "table", "figure", "contents", "noise", "other"]
PAGE_TYPES = ["cover", "title", "imprint", "dedication", "epigraph", "contents", "part", "opening", "text", "endnotes",
              "bibliography", "index", "figure", "table", "ad", "blank", "other"]
PAGE_SCHEMA = {"type": "OBJECT", "properties": {
    "page": {"type": "OBJECT", "properties": {
        "type": {"type": "STRING", "enum": PAGE_TYPES},
        "pn": {"type": "STRING", "description": "the printed page number as printed, empty when none"},
        "cols": {"type": "INTEGER", "description": "columns of running text"}},
        "required": ["type", "pn"], "propertyOrdering": ["type", "pn", "cols"]},
    "lines": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "i": {"type": "INTEGER"},
        "r": {"type": "STRING", "enum": ROLES},
        "l": {"type": "INTEGER", "description": "heading level 1-3 (headings only)"},
        "n": {"type": "INTEGER", "description": "note or endnote number where one starts, 0 where it continues"},
        "p": {"type": "BOOLEAN", "description": "a body line that starts a paragraph"},
        "m": {"type": "ARRAY", "items": {"type": "INTEGER"}, "description": "note markers on the line, in order"},
        "t": {"type": "STRING", "description": "a heading's or byline's text as printed, when the OCR text is wrong"}},
        "required": ["i", "r"], "propertyOrdering": ["i", "r", "l", "n", "p", "m", "t"]}},
    "missing": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "r": {"type": "STRING", "enum": ["heading", "byline", "pagenum", "other"]},
        "l": {"type": "INTEGER"}, "t": {"type": "STRING"},
        "before": {"type": "INTEGER", "description": "index of the listed line it stands above"}},
        "required": ["r", "t"], "propertyOrdering": ["r", "l", "t", "before"]}},
    "toc": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "t": {"type": "STRING"}, "a": {"type": "STRING"}, "pg": {"type": "STRING"}, "l": {"type": "INTEGER"}},
        "required": ["t", "l"], "propertyOrdering": ["t", "a", "pg", "l"]}}},
    "required": ["page", "lines"], "propertyOrdering": ["page", "lines", "missing", "toc"]}

PAGE_PROMPT = """You see one scanned page of a printed Persian book. Every OCR line on the page is marked with a small numbered tag (a white box with a black border and a black number), placed just outside the right end of the line when the margin is free, otherwise over the line's right end. The same lines are listed below in reading order as "index: OCR text"; lines whose OCR is doubtful are marked "(uncertain)". The OCR text may have errors: judge from the image (position, size, boldness, indentation, gaps) and use the text only as a help.

1. The page as a whole, "page":
- type, one of: cover (the book's cover); title (a title page or half-title: the book's title, author, publisher); imprint (copyright page, «شناسنامه», cataloguing data, ISBN); dedication; epigraph (a motto or quotation page); contents (the book's table of contents, or a list of figures or tables, including its «فهرست» page); part (the title page of a part of the book, nearly empty); opening (a unit of the book — chapter, preface, introduction, article, story, poem, appendix, afterword — begins on this page under its title); text (running text continued from the page before, possibly with section headings); endnotes (a list of notes collected at the end of a chapter, article or the book); bibliography (references, sources); index (an index of names or subjects); figure (a page that is a picture, map or photo); table (a page that is one table); ad (the publisher's list of other books, advertisements); blank; other.
- pn: the page number printed on the page, exactly as printed (Persian or Latin digits), or "" when none is printed.
- cols: the number of columns of running text (1 for ordinary pages).

2. Every listed line, "lines", exactly one role r each:
- header: a running header (the book, part, chapter or author name repeated at the top or in the margin of pages), possibly with the page number in the same line.
- pagenum: a printed page number on its own.
- heading: a title or heading, with a level l: 1 = the title of a unit that starts on this page (a chapter «فصل سوم», a part «بخش دوم», a preface «مقدمه», an appendix «پیوست», a bibliography, an index, an afterword; in a collection — essays, articles, stories, poems, interviews — the title of the piece that begins here, however small it is set; in a play an act or scene that begins here); 2 = a section heading inside a unit; 3 = a heading below level 2. A unit title set over several lines (the label line «فصل سوم» + the title + a subtitle) is several heading lines, all l = 1. Give the same level to headings of the same kind wherever they stand.
- byline: the author's or translator's name under (or over) the title of a unit or a piece, «به قلم …», «ترجمهٔ …».
- body: ordinary running text, including an abstract and a question or answer in an interview.
- quote: a block quotation set apart (indented, often smaller type).
- epigraph: a motto under a title, or its attribution line.
- verse: poetry (hemistichs, centred or in two columns, or poem lines).
- note: a footnote line at the FOOT of the page, under the running text of that same page (or under a quote, verse or table on it), separated by a rule or a gap, in smaller type; its number usually matches a marker on this page. Every line at the foot of a page with running text above it is a note, never an endnote, however many or long the notes are, and also when it only cites a source (author, title, year, page).
- endnote: a line of a list of notes collected at the END of a chapter, article or the book, apart from the running text: under a heading such as «پانویس‌ها», «یادداشت‌ها», «پی‌نوشت‌ها», «منابع و یادداشت‌ها», or continuing such a list from the page before. Such a list fills the page, or the rest of the page after a chapter's last paragraph; no running text follows it on the page.
- reference: an entry of a bibliography or list of sources at the end of the book or a chapter (also when such entries stand between endnotes as their own entries); a footnote that cites a source is a note, not a reference.
- caption: the caption of a figure or a table.
- table: text inside a table.
- figure: text inside a picture, map or diagram, or the OCR of a decorative element.
- contents: an entry of a table of contents or list of figures, or the «فهرست» heading of that page.
- noise: a stamp, a library seal, a watermark, handwriting, scanner noise, garbage.
- other: anything else (title page and copyright page text, a dedication, publisher's text, a signature and date under a preface).
Fields of a line, only where they apply:
- n (note and endnote lines): the number printed at the start of the note if this line STARTS a note (Persian or Latin digits, as an integer; «*» = 1, «**» = 2, «***» = 3); 0 if the line continues a note.
- p (body lines): true when the line starts a new paragraph.
- m (body, quote, verse, heading, caption lines): the note markers this line carries — superscript numbers or asterisks after a word, referring to a footnote or endnote — as integers in reading order («*» = 1). Omit when the line has none.
- t (heading and byline lines): the line's text exactly as printed, only when the listed OCR text is wrong or incomplete; omit it when the OCR text is right.

3. "missing": headings and bylines printed on the page that have NO numbered tag (the OCR did not find them, often large decorative titles): {"r", "l", "t": the text as printed, "before": the index of the listed line they stand above}. Usually empty.

4. "toc", only on a contents page: its entries in order, {"t": the title as printed, "a": the author if the entry names one, "pg": the page number as printed, "l": 1 for a part or chapter, 2 for a section under it, 3 below}.

Answer as JSON {"page": {...}, "lines": [...] with every index from 0 to %d exactly once, "missing": [...], "toc": [...]}.

Lines in reading order (index: OCR text):
"""

META_FIELDS = ("title", "subtitle", "authors", "translators", "editors", "publisher", "place", "year", "edition", "isbn",
               "original_title", "original_language", "series")
NAMES = ("authors", "translators", "editors")
# every field required: with optional fields the model filled only title, year and translators (authors in 2 of 33 books)
META_SCHEMA = {"type": "OBJECT", "properties": {k: {"type": "ARRAY", "items": {"type": "STRING"}} if k in NAMES else {"type": "STRING"}
                                                for k in META_FIELDS},
               "required": list(META_FIELDS), "propertyOrdering": list(META_FIELDS)}
META_PROMPT = """These are the cover, title and copyright (imprint, «شناسنامه») pages of a printed Persian book, with the OCR text of each below. Give the book's bibliographic data exactly as printed (Persian as printed; digits as printed): title, subtitle, authors, translators («ترجمه»، «برگردان»، «مترجم»), editors («به کوشش»، «گردآوری»، «ویراستار»، «تدوین»), publisher, place of publication, year of publication, edition («چاپ اول»), ISBN («شابک»), the original title and language for a translation, the series.
Answer every field: "none" for a field these pages do not print, an empty list when they name nobody. The author's name often stands alone on the cover or the title page, above or below the title, or after «نوشته»، «نویسنده»، «اثر»، «به قلم»، «سروده»، «تألیف»; a translated book has both its author and its translators. The publisher often follows «انتشارات»، «نشر»، «ناشر», or stands at the foot of the title page.

OCR text of the pages:
"""

OUTLINE_SCHEMA = {"type": "OBJECT", "properties": {"headings": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "id": {"type": "INTEGER"},
    "level": {"type": "INTEGER", "description": "1 opens a unit, 2 a section, 3 below, 0 not a heading"},
    "kind": {"type": "STRING", "enum": ["part", "chapter", "front", "back", "none"]},
    "title": {"type": "STRING"}, "author": {"type": "STRING"}},
    "required": ["id", "level"], "propertyOrdering": ["id", "level", "kind", "title", "author"]}}}, "required": ["headings"]}
OUTLINE_PROMPT = """Below are the headings of a printed Persian book, found page by page by a reader who looked at each page alone, and the book's printed table of contents. Decide the book's outline as a whole.

For every heading id give its level:
- 1: it opens a unit of the book: a part, a chapter, a preface or introduction, an article, story, poem or interview in a collection, an appendix, a bibliography, an index, an afterword; in a play, the list of characters and each act and each scene that begins on a page (when a page repeats the act's label over a new scene, the act label and the scene heading together are that scene's title, e.g. «پرده دوم: صحنه دوم»);
- 2: a section heading inside a unit; 3: a heading below a section;
- 0: not a heading of the book (a running header or a repeated title taken for a heading, a stray line).
The page-by-page reader sometimes gives headings of the same kind different levels on different pages; make them consistent: headings of the same kind and numbering («فصل …», «۳. …», «یادداشت …») get the same level, and the printed contents show which entries are units and which are sections. A unit that the contents list opens where its heading stands.
For level 1 also give kind (part, chapter, front for a preface/introduction/foreword before the main text, back for an appendix/bibliography/index/afterword), title (the unit's title as the reader should see it, as printed: a label and its title joined with ": ", e.g. «فصل ۳: تو استثناء نیستی»; when one unit's title runs over several heading ids, give the whole title on the first id and level 0 to the others), and author when a byline names one. Give every id exactly once.

Printed table of contents (page of the list: entries as title | author | printed page | level):
%s

Headings (id | PDF page | printed page | page type | heads the page? | level found | text | bylines):
%s
"""


class ApiError(Exception):
    pass


def is_openrouter(model):
    return model.startswith("openrouter:")


def labels_dirname(model):
    """The work directory's folder for a labeller's answers: gemini-MODEL, characters a path cannot hold replaced
    (openrouter:google/gemma-4-31b-it -> gemini-openrouter-google-gemma-4-31b-it)."""
    return "gemini-" + re.sub(r"[^\w.-]+", "-", model)


def load_key(model=DEFAULT_MODEL):
    """GEMINI_API_KEY from the environment, else ~/.config/gemini/api_key (one line); for an openrouter:... model
    OPENROUTER_API_KEY, else ~/.config/openrouter/api_key."""
    if is_openrouter(model):
        service, env, what, url = "openrouter", "OPENROUTER_API_KEY", f"an OpenRouter API key for {model}", "https://openrouter.ai/settings/keys"
    else:
        service, env, what, url = "gemini", "GEMINI_API_KEY", "a Gemini API key", "https://aistudio.google.com/apikey"
    key = os.environ.get(env, "").strip()
    f = pathlib.Path.home() / ".config" / service / "api_key"
    if not key and f.exists():
        try:
            raw = f.read_bytes()
            key = (raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")).strip()
        except (OSError, UnicodeDecodeError) as e:
            sys.exit(f"parisaocr: ~/.config/{service}/api_key cannot be read ({type(e).__name__}); save it as one line of text")
    if not key:
        sys.exit(f"parisaocr: --llm needs {what}: set {env}, or put the key in ~/.config/{service}/api_key "
                 f"(one line); keys are made at {url}")
    if not re.fullmatch(r"[!-~]+", key):  # sent in an HTTP header and masked in errors: only the key, one line
        sys.exit(f"parisaocr: {env} or ~/.config/{service}/api_key must hold the key alone, on one line")
    return key


def read_rows(ocr_dir, page):
    """The OCR jsonl rows of a page in file order (a row's position is the index the labels refer to)."""
    f = pathlib.Path(ocr_dir) / "jsonl" / f"p-{page:03d}.jsonl"
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] if f.exists() else []


def reading_order(boxes):
    return sorted(range(len(boxes)), key=lambda i: (boxes[i][1], -boxes[i][2]))


def _font(px):
    for f in FONTS:
        if os.path.exists(f):
            return ImageFont.truetype(f, px)
    return ImageFont.load_default(px)


def _intersects(a, b, margin=0.0):
    """Rectangles a and b overlap; a positive margin demands a gap of that size, a negative one tolerates that much overlap."""
    return a[0] < b[2] + margin and b[0] < a[2] + margin and a[1] < b[3] + margin and b[1] < a[3] + margin


def overlay(image_path, rows):
    """The page downscaled to MAX_H with every line's index tagged at its right edge: (jpeg bytes, (w, h)).

    Font size 1.2 x the median line height, capped so that the tags of lines one pitch apart do not overlap (digits are
    about 0.75 em tall); a tag that would still overlap a placed tag or another line's box moves sideways: further
    right, else inside the line's box at its right end, else further left.
    """
    im = Image.open(image_path)
    s = min(1.0, MAX_H / im.height)
    if s < 1.0:
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    im = im.convert("RGB")
    W, H = im.size
    boxes = [[v * s for v in r["bbox"]] for r in rows]
    order = reading_order(boxes)
    med_h = statistics.median([b[3] - b[1] for b in boxes]) if boxes else 0.03 * H
    med_h = max(med_h, 8.0)
    pitches = [boxes[b][1] - boxes[a][1] for a, b in zip(order, order[1:])
               if boxes[b][1] - boxes[a][1] > 0.3 * med_h and min(boxes[a][2], boxes[b][2]) > max(boxes[a][0], boxes[b][0])]
    pitch = statistics.median(pitches) if pitches else 1.6 * med_h
    pad = max(2, round(0.08 * med_h))
    border = max(1, round(med_h / 25))
    gap = max(3, round(0.15 * med_h))
    px = max(12, min(round(1.2 * med_h), int((0.95 * pitch - 2 * pad - 2 * border) / 0.75)))
    font = _font(px)
    draw = ImageDraw.Draw(im)
    placed = []
    for i in order:
        x0, y0, x1, y1 = boxes[i]
        l, t, r, b = font.getbbox(str(i))
        tw, th = r - l + 2 * pad, b - t + 2 * pad
        ty = min(max(0.0, (y0 + y1) / 2 - th / 2), H - th)
        cands = [tx for tx in (x1 + gap + k * (tw + gap) for k in range(3)) if tx + tw <= W]  # outside, then further right
        cands += [tx for tx in (x1 - tw - k * (tw + gap) for k in range(3)) if tx >= 0]  # inside, then further left
        if not cands:
            cands = [max(0.0, min(x1 - tw, W - tw))]
        others = [boxes[j] for j in range(len(boxes)) if j != i]

        def clashes(tx):
            rect = (tx, ty, tx + tw, ty + th)
            return sum(_intersects(rect, p, 1) for p in placed) + sum(_intersects(rect, o, -0.15 * med_h) for o in others)
        tx = min(cands, key=lambda c: (clashes(c), cands.index(c)))
        rect = (tx, ty, tx + tw, ty + th)
        placed.append(rect)
        draw.rectangle(rect, fill="white", outline="black", width=border)
        draw.text((tx + pad - l, ty + pad - t), str(i), fill="black", font=font)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue(), (W, H)


def image_part(path, max_side=1600):
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}


class Gemini:
    """generateContent over REST with retries; the thinking config falls back when the model refuses it."""
    THINKING = [{"thinkingLevel": "LOW", "includeThoughts": False}, None]

    def __init__(self, model, key):
        self.model, self.key, self.level, self.lock = model, key, 0, threading.Lock()

    def generate(self, contents, schema):
        for attempt in range(8):
            with self.lock:
                thinking = self.THINKING[self.level]
            gc = {"temperature": 0, "maxOutputTokens": 16384, "responseMimeType": "application/json", "responseSchema": schema}
            if thinking is not None:
                gc["thinkingConfig"] = thinking
            body = json.dumps({"contents": contents, "generationConfig": gc, "safetySettings": SAFETY}).encode()
            req = urllib.request.Request(f"{API}/models/{self.model}:generateContent", data=body,
                                         headers={"x-goog-api-key": self.key, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    resp = json.load(r)
                if isinstance(resp, dict):
                    return resp
                raise ValueError("the reply is not a JSON object")
            except urllib.error.HTTPError as e:
                msg = _body(e).replace(self.key, "<KEY>")
                if e.code == 400 and thinking is not None and "thinking" in msg.lower():
                    with self.lock:
                        if self.level < len(self.THINKING) - 1 and self.THINKING[self.level] == thinking:
                            self.level += 1
                    continue
                if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep((15 * (attempt + 1) if e.code == 429 else 2 ** (attempt + 1)) + random.random() * 2)
                    continue
                raise ApiError(f"HTTP {e.code}: {' '.join(msg.split())[:300]}")
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as e:
                if attempt < 5:  # a dropped connection, a reply cut short or not JSON: ask again
                    time.sleep(2 ** (attempt + 1) + random.random() * 2)
                    continue
                raise ApiError(f"{type(e).__name__}: {str(e).replace(self.key, '<KEY>')[:300]}")
        raise ApiError("gave up after 8 attempts")


def json_schema(s):
    """A generateContent response schema (an OpenAPI subset: upper-case types, propertyOrdering) as JSON Schema."""
    out = {}
    for k, v in s.items():
        if k == "type":
            out[k] = v.lower()
        elif k == "properties":
            out[k] = {name: json_schema(sub) for name, sub in v.items()}
        elif k == "items":
            out[k] = json_schema(v)
        elif k != "propertyOrdering":
            out[k] = v
    return out


class OpenRouter:
    """OpenRouter's chat/completions (an OpenAI-style API in front of many providers) behind the interface of
    Gemini: generate() takes generateContent's contents and schema and returns a reply in its shape (candidates,
    finishReason, usageMetadata, plus the cost OpenRouter charged), so that the rest of the labeller does not change."""
    FINISH = {"stop": "STOP", "length": "MAX_TOKENS", "content_filter": "SAFETY"}

    def __init__(self, model, key):
        self.model, self.key, self.name = model, key, model.split(":", 1)[1]
        # the pages are labelled without reasoning, as Gemini's are with thinkingLevel LOW: a model that reasons by
        # default (qwen3.8-27b, at its highest effort) is told not to, unless reasoning is mandatory for it;
        # PARISAOCR_OPENROUTER_EXTRA (JSON) adds or replaces request fields, e.g. {"reasoning": {"effort": "low"}}
        models = openrouter_models()
        if not models:
            print("parisaocr: OpenRouter's model list could not be read: the model's own reasoning default applies", flush=True)
        r = (models.get(self.name.split(":")[0]) or {}).get("reasoning") or {}
        self.extra = {"reasoning": {"enabled": False}} if r.get("default_enabled") and not r.get("mandatory") else {}
        extra = _env_json("PARISAOCR_OPENROUTER_EXTRA")
        core = sorted(set(extra) & {"model", "messages", "response_format", "stream"})
        if core:
            sys.exit(f"parisaocr: PARISAOCR_OPENROUTER_EXTRA cannot set {', '.join(core)}")
        self.extra.update(extra)
        override = {**_env_json("PARISAOCR_OPENROUTER_PROVIDER"), **(self.extra.pop("provider", None) or {})}
        weak = [k for k, v in OPENROUTER_PROVIDER.items() if k in override and override[k] != v]
        if weak:
            sys.exit(f"parisaocr: the OpenRouter routing cannot change {', '.join(weak)}: the pages go only to providers "
                     "that keep no data and honour the JSON schema")
        self.provider = {**override, **OPENROUTER_PROVIDER}

    def body(self, contents, schema):
        messages = []
        for c in contents:
            if c["role"] == "model":
                messages.append({"role": "assistant", "content": "".join(p.get("text", "") for p in c["parts"])})
                continue
            parts = []
            for p in c["parts"]:
                if "inlineData" in p:
                    d = p["inlineData"]
                    parts.append({"type": "image_url", "image_url": {"url": f"data:{d['mimeType']};base64,{d['data']}"}})
                else:
                    parts.append({"type": "text", "text": p["text"]})
            messages.append({"role": "user", "content": parts})
        return {"model": self.name, "messages": messages, "temperature": 0, "max_tokens": 16384,
                # strict: providers that otherwise take the schema as a hint enforce it (Mancer dropped "i" without it)
                "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": json_schema(schema)}},
                "provider": self.provider, **self.extra}

    @staticmethod
    def failure(resp):
        """(code, message) of a failure OpenRouter reports inside an HTTP 200 reply (a provider that failed, also
        mid-generation), else (None, None)."""
        choices = resp.get("choices") or []
        err = resp.get("error") or next((c["error"] for c in choices if c.get("error")), None)
        if err is None and any(c.get("finish_reason") == "error" for c in choices):
            err = {"message": "the provider stopped with an error"}
        if err is None and choices and choices[0].get("finish_reason") is None:
            m = choices[0].get("message") or {}
            if not _text(m.get("content")).strip() and not m.get("refusal"):
                err = {"message": "the provider returned no content"}  # a cold start or scale-up (OpenRouter's docs)
        if err is None:
            return None, None
        code = err.get("code") if isinstance(err, dict) else None
        return (code if isinstance(code, int) else 502), json.dumps(err, ensure_ascii=False)

    def as_gemini(self, resp):
        cands = [{"content": {"parts": [{"text": _text((c.get("message") or {}).get("content"))}]},
                  "finishReason": self.FINISH.get(c.get("finish_reason"), c.get("finish_reason"))} for c in resp.get("choices") or []]
        u = resp.get("usage") or {}
        meta = {"promptTokenCount": u.get("prompt_tokens", 0), "candidatesTokenCount": u.get("completion_tokens", 0)}
        if u.get("cost") is not None:
            meta["cost"] = u["cost"]
        provider = resp.get("provider") or (resp.get("openrouter_metadata") or {}).get("provider")
        return {"candidates": cands, "usageMetadata": meta, "modelVersion": resp.get("model"), "provider": provider}

    RETRY = (408, 429, 500, 502, 503, 504, 524, 529)  # timeouts, rate limits, providers down or overloaded
    # errors about this request's content, not the account or the routing: the page is recorded as blocked (the
    # layout rules read it) instead of stopping the book (label_book stops on other "HTTP 4..." errors)
    PAGE_ERRORS = ("content_policy_violation", "refusal", "invalid_image", "image_too_large", "image_too_small",
                   "unsupported_image_format", "context_length_exceeded", "invalid_prompt")

    @staticmethod
    def error_type(body):
        """error.metadata.error_type of an OpenRouter error body, else None."""
        try:
            d = json.loads(body)
        except ValueError:
            return None
        d = d.get("error", d) if isinstance(d, dict) else None
        md = d.get("metadata") if isinstance(d, dict) else None
        return md.get("error_type") if isinstance(md, dict) else None

    def generate(self, contents, schema):
        data = json.dumps(self.body(contents, schema)).encode()
        for attempt in range(8):
            req = urllib.request.Request(f"{OPENROUTER}/chat/completions", data=data,
                                         headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                                                  "X-OpenRouter-Metadata": "enabled"})  # names the provider that answered
            wait = None
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    resp = json.load(r)
                code, msg = self.failure(resp)
                if code is None:
                    return self.as_gemini(resp)
            except urllib.error.HTTPError as e:
                code, msg = e.code, _body(e)
                wait = e.headers.get("Retry-After") if e.headers else None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as e:
                code, msg = None, f"{type(e).__name__}: {e}"
            kind = self.error_type(msg) if code else None
            msg = " ".join(msg.replace(self.key, "<KEY>").split())[:300]
            if kind in self.PAGE_ERRORS:
                raise ApiError(f"blocked: {kind} (HTTP {code}: {msg})")
            if (code is None or code in self.RETRY) and attempt < 5:
                try:
                    pause = min(120.0, float(wait))
                except (TypeError, ValueError):
                    pause = 15 * (attempt + 1) if code == 429 else 2 ** (attempt + 1)
                time.sleep(pause + random.random() * 2)
                continue
            raise ApiError(f"HTTP {code}: {msg}" if code else msg)
        raise ApiError("gave up after 8 attempts")


def _body(e):
    """The text of an HTTP error's body ("" when it cannot be read)."""
    try:
        return e.read().decode(errors="replace")
    except Exception:  # noqa: BLE001  a body cut short or already closed
        return ""


def client_for(model, key):
    return OpenRouter(model, key) if is_openrouter(model) else Gemini(model, key)


def _env_json(name):
    """The JSON object in environment variable NAME, {} when unset; a value that is not a JSON object stops the run."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError as e:
        sys.exit(f"parisaocr: {name} is not valid JSON ({e})")
    if not isinstance(value, dict):
        sys.exit(f"parisaocr: {name} must be a JSON object")
    return value


def _text(content):
    """A chat message's content as text: a string, or the text parts of a list of parts."""
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ""


def openrouter_models():
    """OpenRouter's public model list (id -> entry; no key needed), read once; {} when it cannot be read."""
    if not _OPENROUTER_MODELS and not _OPENROUTER_LIST_FAILED:
        try:
            with urllib.request.urlopen(f"{OPENROUTER}/models", timeout=60) as r:
                _OPENROUTER_MODELS.update({m["id"]: m for m in json.load(r).get("data", [])})
        except Exception:  # noqa: BLE001  without the list there is no check and no price, nothing worse
            _OPENROUTER_LIST_FAILED.append(True)
    return _OPENROUTER_MODELS


def resolve_model(model, key):
    """MODEL when models.list knows it, else the newest gemini-*-flash (not lite, not preview) that is listed. An
    openrouter:... model is never replaced: one OpenRouter does not list, or that does not read images, stops the run."""
    if is_openrouter(model):
        models = openrouter_models()
        name = model.split(":", 1)[1].split(":")[0]  # a routing variant (:floor, :nitro, :exacto) is its model
        if models and name not in models:
            sys.exit(f"parisaocr: OpenRouter does not offer {name} (see https://openrouter.ai/models)")
        if models and "image" not in (models[name].get("architecture") or {}).get("input_modalities", []):
            sys.exit(f"parisaocr: {name} on OpenRouter does not read images")
        return model
    try:
        req = urllib.request.Request(f"{API}/models?pageSize=200", headers={"x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=60) as r:
            names = [m["name"].split("/", 1)[1] for m in json.load(r).get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            sys.exit(f"parisaocr: Gemini refused the API key (HTTP {e.code})")
        return model
    except Exception:  # noqa: BLE001  a listing failure must not stop the run
        return model
    if model in names:
        return model
    flash = [(tuple(int(x) for x in m.group(1).split(".")), n) for n in names if (m := re.fullmatch(r"gemini-(\d+(?:\.\d+)*)-flash", n))]
    if not flash:
        sys.exit(f"parisaocr: Gemini does not offer {model}, nor any gemini-*-flash model")
    pick = max(flash)[1]
    print(f"parisaocr: {model} is not offered; using {pick}", flush=True)
    return pick


def answer_of(resp):
    """(text, finish reason, error) of a generateContent response; error names a block or a bad finish."""
    pf = resp.get("promptFeedback") or {}
    if pf.get("blockReason"):
        return "", None, f"blocked: {pf['blockReason']}"
    cands = resp.get("candidates") or []
    if not cands:
        return "", None, "no candidates"
    c = cands[0]
    finish = c.get("finishReason")
    text = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts", []) if not p.get("thought"))
    if finish not in (None, "STOP", "MAX_TOKENS"):
        return text, finish, f"finish {finish}"
    return text, finish, None


def _loads(text):
    """json.loads(TEXT); failing that, the JSON object that starts at its first "{", ignoring what follows it
    (gemma-4-31b-it puts a stray ``` after an answer that follows the schema)."""
    try:
        return json.loads(text)
    except ValueError:
        i = text.find("{")
        if i < 0:
            raise
        return json.JSONDecoder().raw_decode(text, i)[0]


def usage_of(resp):
    u = resp.get("usageMetadata") or {}
    out = {"input": u.get("promptTokenCount", 0), "output": u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)}
    if u.get("cost") is not None:
        out["cost"] = u["cost"]  # USD, as OpenRouter charged it
    return out


def add_usage(total, usage):
    for k, v in usage.items():
        total[k] = total.get(k, 0) + v


def price_of(model):
    """(input, output) USD per million tokens: PRICES, or for an openrouter:... model OpenRouter's list price; None
    when not known."""
    if model in PRICES:
        return PRICES[model]
    if is_openrouter(model):
        name = model.split(":", 1)[1]
        models = openrouter_models()
        p = (models.get(name) or models.get(name.split(":")[0]) or {}).get("pricing") or {}
        try:
            return float(p["prompt"]) * 1e6, float(p["completion"]) * 1e6
        except (KeyError, TypeError, ValueError):
            return None
    return None


def cost(usage, model):
    """USD for USAGE: what OpenRouter charged when it said so, else {"input", "output"} tokens at the model's price;
    None when that is not known."""
    if "cost" in usage:
        return usage["cost"]
    p = price_of(model)
    return (usage["input"] * p[0] + usage["output"] * p[1]) / 1e6 if p else None


def estimate(ocr_dir, pages):
    """The tokens ({"input", "output"}) that labelling PAGES and the two book questions should take. Fitted on 1,693
    pages of 33 books labelled by gemini-3.8-flash (retries included), whose total it matches: input 545 + the prompt's
    characters / 2.74, output 94 + 16.9 per OCR line; a page without lines is not sent. The book questions add about
    8,000 input and 3,000 output tokens (more for a book with many headings)."""
    usage = {"input": 8000, "output": 3000}
    for p in pages:
        rows = read_rows(ocr_dir, p)
        if rows:
            usage["input"] += 545 + len(page_prompt(rows)) / 2.74
            usage["output"] += 94 + 16.9 * len(rows)
    return {k: round(v) for k, v in usage.items()}


def plan(ocr_dir, labels_dir, model=DEFAULT_MODEL):
    """(pages of the book, pages still to label, a sentence with the expected cost of labelling them)."""
    ocr_dir, labels_dir = pathlib.Path(ocr_dir), pathlib.Path(labels_dir)
    pages = sorted(int(f.stem[2:]) for f in (ocr_dir / "jsonl").glob("p-*.jsonl"))
    todo = [p for p in pages if _retry(labels_dir / f"p-{p:03d}.json")]
    if not todo:
        return pages, todo, f"all {len(pages)} pages already labelled by Gemini (no cost)"
    usage = estimate(ocr_dir, todo)
    c = cost(usage, model)
    money = f"about ${c:.2f}" if c is not None else f"about {usage['input'] + usage['output']:,} tokens (no price known for {model})"
    return pages, todo, f"{len(todo)} of {len(pages)} pages to label with {model}: {money}"


# --- page labels -------------------------------------------------------------------------------------------------

def page_prompt(rows):
    boxes = [r["bbox"] for r in rows]
    lines = [f"{i}: {' '.join(rows[i]['text'].split())}{' (uncertain)' if rows[i]['conf'] < 60 else ''}" for i in reading_order(boxes)]
    return PAGE_PROMPT % (len(rows) - 1) + "\n".join(lines) + "\n"


def validate(text, n):
    """(answer, problems): the cleaned answer and the complaints ([] = valid)."""
    try:
        d = _loads(text)
        items = d["lines"]
        if not isinstance(items, list):
            raise TypeError("lines is not a list")
    except (ValueError, KeyError, TypeError) as e:
        return {}, [f"the answer is not the expected JSON ({type(e).__name__})"]
    page = d.get("page") if isinstance(d.get("page"), dict) else {}
    out = {"page": {"type": page.get("type") if page.get("type") in PAGE_TYPES else "other",
                    "pn": str(page.get("pn") or ""), "cols": page.get("cols") if type(page.get("cols")) is int else 1}}
    seen, dup, problems = {}, [], []
    # what Gemini's schema enforcement guarantees, checked for a model behind OpenRouter that may take the schema
    # as a hint (a page type or a contents list replaced here would otherwise be stored as a valid answer)
    if not page:
        problems.append('no "page" object')
    elif page.get("type") not in PAGE_TYPES:
        problems.append(f"unknown page type {page.get('type')!r}")
    for k in ("missing", "toc"):
        if d.get(k) is not None and not isinstance(d.get(k), list):
            problems.append(f'"{k}" is not a list')
    for it in items:
        i = it.get("i") if isinstance(it, dict) else None
        if type(i) is not int or not 0 <= i < n:
            problems.append(f"unknown index {i!r}")
            continue
        if i in seen:
            dup.append(i)
            continue
        r = it.get("r")
        if r not in ROLES:
            problems.append(f"index {i}: unknown role {r!r}")
            continue
        rec = {"i": i, "r": r}
        if r == "heading" and it.get("l") not in (1, 2, 3):
            problems.append(f"index {i}: heading level {it.get('l')!r}")
        if r == "heading" and type(it.get("l")) is int:
            rec["l"] = it["l"]
        if r in ("note", "endnote") and type(it.get("n")) is int:
            rec["n"] = it["n"]
        if r == "body" and it.get("p") is True:
            rec["p"] = True
        if isinstance(it.get("m"), list) and all(type(x) is int and x > 0 for x in it["m"]) and it["m"]:
            rec["m"] = it["m"]
            if isinstance(it.get("a"), list) and all(isinstance(x, str) for x in it["a"]) and len(it["a"]) == len(it["m"]):
                rec["a"] = it["a"]  # the word each marker follows (asked for in the targeted labels)
        if r in ("heading", "byline") and isinstance(it.get("t"), str) and it["t"].strip():
            rec["t"] = it["t"].strip()
        seen[i] = rec
    missing = [i for i in range(n) if i not in seen]
    if missing:
        problems.append(f"missing indices {missing}")
    if dup:
        problems.append(f"duplicate indices {sorted(set(dup))}")
    out["lines"] = [seen[i] for i in sorted(seen)]
    def typed(e, strings, ints):
        return all(e.get(k) is None or isinstance(e[k], str) for k in strings) and \
            all(e.get(k) is None or type(e[k]) is int for k in ints)
    out["missing"] = [m for m in d.get("missing") or [] if isinstance(m, dict) and isinstance(m.get("t"), str) and m["t"].strip()
                      and typed(m, ("r",), ("l", "before"))]
    out["toc"] = [e for e in d.get("toc") or [] if isinstance(e, dict) and isinstance(e.get("t"), str) and typed(e, ("a", "pg"), ("l",))]
    return out, problems


def label_page(client, ocr_dir, page):
    """Gemini's labels of one page, as stored in p-NNN.json: the answer plus "valid", "usage", "model"."""
    rows = read_rows(ocr_dir, page)
    result = {"page": {"type": "blank", "pn": "", "cols": 1}, "lines": [], "missing": [], "toc": [], "valid": False,
              "usage": {"input": 0, "output": 0}, "model": client.model}
    if not rows:
        return dict(result, valid=True, note="no lines")
    jpeg, size = overlay(pathlib.Path(ocr_dir) / "pages" / f"p-{page:03d}.png", rows)
    result["image"] = list(size)
    contents = [{"role": "user", "parts": [{"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(jpeg).decode()}},
                                           {"text": page_prompt(rows)}]}]
    try:
        for attempt in range(2):
            resp = client.generate(contents, PAGE_SCHEMA)
            add_usage(result["usage"], usage_of(resp))
            if resp.get("provider"):
                result["provider"] = resp["provider"]  # the OpenRouter provider that answered
            text, finish, err = answer_of(resp)
            if err:
                result["error"] = err
                break
            answer, problems = validate(text, len(rows))
            result.update(answer)
            result.pop("problems", None)
            if not problems:
                result["valid"] = True
                break
            result["problems"] = problems
            if attempt == 0:
                contents = contents + [{"role": "model", "parts": [{"text": text}]}, {"role": "user", "parts": [{"text":
                    "Your answer is invalid: " + "; ".join(problems) + f". Answer again with every index from 0 to "
                    f"{len(rows) - 1} exactly once, each with its role."}]}]
    except ApiError as e:
        result["error"] = str(e)
    return result


# --- the two questions about the whole book ----------------------------------------------------------------------

def load_pages(labels_dir):
    out = {}
    for f in sorted(pathlib.Path(labels_dir).glob("p-*.json")):
        g = json.loads(f.read_text(encoding="utf-8"))
        if g.get("valid"):
            out[int(f.stem[2:])] = g
    return out


def _ask(client, parts, schema):
    """(answer, usage, error) of a one-turn question; an answer not shaped as SCHEMA's object is an error (it would
    be kept and read by every later conversion)."""
    resp = client.generate([{"role": "user", "parts": parts}], schema)
    text, finish, err = answer_of(resp)
    if not err:
        try:
            answer = _loads(text)
        except ValueError:
            return None, usage_of(resp), f"not JSON (finish {finish})"
        heads = answer.get("headings") if isinstance(answer, dict) else None
        if not isinstance(answer, dict) or (schema is OUTLINE_SCHEMA and not (
                isinstance(heads, list) and all(isinstance(h, dict) and type(h.get("id")) is int and type(h.get("level")) is int
                                                and all(h.get(k) is None or isinstance(h[k], str) for k in ("kind", "title", "author"))
                                                for h in heads))):
            return None, usage_of(resp), "not the expected JSON"
        if schema is META_SCHEMA:  # fields of the wrong type are dropped (labels.book_meta reads strings and lists)
            answer = {k: v for k, v in answer.items() if isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v))}
        return answer, usage_of(resp), None
    return None, usage_of(resp), err


def meta(client, ocr_dir, pages):
    """The bibliographic data from the cover, title and imprint pages (those among the first 12 and last 4 pages that
    the page labels call so, at most 4; else the first 3 pages) -> (record for book_meta.json, usage, error)."""
    nums = sorted(pages)
    window = nums[:12] + nums[-4:]
    chosen = [p for p in dict.fromkeys(window) if pages[p]["page"]["type"] in ("cover", "title", "imprint")][:4] or nums[:3]
    parts, texts = [], []
    for p in chosen:
        parts.append(image_part(pathlib.Path(ocr_dir) / "pages" / f"p-{p:03d}.png"))
        texts.append(f"--- page {p}:\n" + "\n".join(r["text"] for r in read_rows(ocr_dir, p)))
    parts.append({"text": META_PROMPT + "\n".join(texts)})
    answer, usage, err = _ask(client, parts, META_SCHEMA)
    return {"pages": chosen, "answer": answer, "model": client.model}, usage, err


def outline_input(ocr_dir, pages):
    """(headings, toc lines): every heading line of the page labels and every "missing" heading, with ids."""
    heads, toc = [], []
    for p in sorted(pages):
        g = pages[p]
        rows = read_rows(ocr_dir, p)
        if g["page"]["type"] == "contents":
            for e in g.get("toc") or []:
                toc.append(f"p{p}: {e.get('t', '')} | {e.get('a', '')} | {e.get('pg', '')} | {e.get('l', '')}")
        order = sorted(range(len(rows)), key=lambda i: (rows[i]["bbox"][1], -rows[i]["bbox"][2]))
        lab = {it["i"]: it for it in g["lines"]}
        content = [i for i in order if lab.get(i, {}).get("r") not in ("header", "pagenum", "noise")]
        bylines = [(lab[i].get("t") or rows[i]["text"]) for i in content if lab.get(i, {}).get("r") == "byline"]
        for k, m in enumerate(g.get("missing") or []):
            if m.get("r") == "heading":
                heads.append(dict(page=p, row=-1, k=k, level=m.get("l") or 0, text=m["t"], top=True, pn=g["page"]["pn"],
                                  type=g["page"]["type"], bylines=bylines))
        for pos, i in enumerate(content):
            it = lab.get(i, {})
            if it.get("r") != "heading":
                continue
            heads.append(dict(page=p, row=i, k=-1, level=it.get("l") or 0, text=it.get("t") or rows[i]["text"], top=pos < 3,
                              pn=g["page"]["pn"], type=g["page"]["type"], bylines=bylines))
    for n, h in enumerate(heads):
        h["id"] = n
    return heads, toc


def outline(client, ocr_dir, pages):
    """The book's outline from all headings found -> (record for book_outline.json, usage, error)."""
    heads, toc = outline_input(ocr_dir, pages)
    listing = "\n".join(f"{h['id']} | {h['page']} | {h['pn']} | {h['type']} | {'yes' if h['top'] else 'no'} | {h['level']} | "
                        f"{' '.join(h['text'].split())} | {'; '.join(h['bylines'])}" for h in heads)
    if not heads:
        return {"headings": [], "answer": {"headings": []}, "model": client.model}, {"input": 0, "output": 0}, None
    answer, usage, err = _ask(client, [{"text": OUTLINE_PROMPT % ("\n".join(toc) or "(none found)", listing)}], OUTLINE_SCHEMA)
    return {"headings": heads, "answer": answer, "model": client.model}, usage, err


# --- a whole book ------------------------------------------------------------------------------------------------

def _retry(f):
    """A stored page answer to ask again: none yet, or an error that was not a block."""
    if not f.exists():
        return True
    g = json.loads(f.read_text(encoding="utf-8"))
    err = g.get("error")
    return bool(err) and not err.startswith(("blocked", "finish "))


def label_book(ocr_dir, labels_dir, model=DEFAULT_MODEL, jobs=8):
    """Label every page of the OCR output OCR_DIR that has no answer yet in LABELS_DIR, then ask the two book questions
    once all pages are answered. -> a one-line summary for the report."""
    ocr_dir, labels_dir = pathlib.Path(ocr_dir), pathlib.Path(labels_dir)
    labels_dir.mkdir(parents=True, exist_ok=True)
    pages, todo, expected = plan(ocr_dir, labels_dir, model)
    usage, lock, stop = {"input": 0, "output": 0}, threading.Lock(), threading.Event()
    who, where = ("OpenRouter", "OpenRouter and the provider it routes them to") if is_openrouter(model) else ("Gemini", "Google")
    client = None
    if todo:
        key = load_key(model)
        client = client_for(resolve_model(model, key), key)
        print(f"parisaocr: {who}: {expected} (the page images and their OCR text are sent to {where})", flush=True)
        done, step = [0], max(10, len(todo) // 10)

        def one(p):
            if stop.is_set():
                return
            res = label_page(client, ocr_dir, p)
            with lock:
                add_usage(usage, res["usage"])
                err = res.get("error") or ""
                if err.startswith("HTTP 4"):  # the quota is used up, or the key or model was refused: stop asking
                    if not stop.is_set():
                        print(f"parisaocr: {who} stopped answering: {err[:200]}", flush=True)
                    stop.set()
                    return
                (labels_dir / f"p-{p:03d}.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
                done[0] += 1
                if done[0] % step == 0 or done[0] == len(todo):
                    c = cost(usage, client.model)
                    print(f"parisaocr: {who} {done[0]}/{len(todo)} pages" + (f", ${c:.2f}" if c is not None else ""), flush=True)

        with ThreadPoolExecutor(max(1, jobs)) as pool:
            list(pool.map(one, todo))
    labelled = load_pages(labels_dir)
    blocked = [p for p in pages if p not in labelled and not _retry(labels_dir / f"p-{p:03d}.json")]
    waiting = [p for p in pages if p not in labelled and p not in blocked]
    notes = []
    if waiting:
        notes.append(f"{len(waiting)} pages not answered yet (run again to finish them; the book questions wait until then)")
    else:
        stamp = sorted(labelled)
        for name, ask in (("book_meta.json", meta), ("book_outline.json", outline)):
            f = labels_dir / name
            old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
            if old.get("labelled") == stamp:
                continue
            if client is None:
                key = load_key(model)
                client = client_for(resolve_model(model, key), key)
            try:
                rec, u, err = ask(client, ocr_dir, labelled)
            except ApiError as e:
                rec, u, err = None, {"input": 0, "output": 0}, str(e)
            add_usage(usage, u)
            if err:
                notes.append(f"{name.split('.')[0].replace('book_', 'the book ')} question failed ({err[:120]}); run again to retry")
                continue
            rec["labelled"] = stamp
            f.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    if blocked:
        notes.append(f"{len(blocked)} pages blocked by {who} (pages {', '.join(map(str, blocked[:10]))}{', …' if len(blocked) > 10 else ''}; "
                     "the layout rules read them)")
    used = client.model if client else model
    c = cost(usage, used)
    spent = f"${c:.2f} this run" if c is not None else f"{usage['input'] + usage['output']} tokens this run"
    return f"{used}, {len(labelled)} of {len(pages)} pages labelled, {spent}" + "".join(f"; {n}" for n in notes)
