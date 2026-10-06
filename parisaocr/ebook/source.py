"""OCR stage: read the PDF with ParisaOCR (in this process) and load its per-page JSONL.

The OCR output is kept per recognition model in WORK/ocr-NAME, so a second run
of the same book takes seconds and switching models reruns only recognition.
Lines that are mostly Latin letters are marked `latin`: they are typeset left
to right, and are not searched for note markers or verse.
"""
import json
import os
import re
import pathlib
import sys
from dataclasses import dataclass, field, replace

from PIL import Image

from .textutil import LATIN, latin_ratio


@dataclass
class Word:
    text: str
    bbox: tuple
    conf: float


@dataclass
class Line:
    text: str
    bbox: tuple  # x0, y0, x1, y1 on the page image
    conf: float
    words: list
    column: int = 0
    latin: bool = False  # mostly Latin letters: an English line
    split: float = None  # verse: x of the gap between the two hemistichs
    row: int = -1  # its row in the page's OCR jsonl (what a page labeller's line index refers to)

    @property
    def x0(self): return self.bbox[0]
    @property
    def y0(self): return self.bbox[1]
    @property
    def x1(self): return self.bbox[2]
    @property
    def y1(self): return self.bbox[3]
    @property
    def h(self): return self.bbox[3] - self.bbox[1]
    @property
    def w(self): return self.bbox[2] - self.bbox[0]
    @property
    def yc(self): return (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class Page:
    index: int  # 1-based position in the PDF
    id: str
    image: pathlib.Path
    width: int
    height: int
    lines: list = field(default_factory=list)
    stamps: list = field(default_factory=list)  # boxes of a stamp burned into the scan, taken out of `lines`
    labelled: bool = False  # a page labeller's labels are on its lines (`labels.attach`)


def model_name(model):
    """A directory name for a recognition model spec: the bundled model's file name for "default",
    the file name for kraken:PATH, the language for a Tesseract TESSDATA:LANG."""
    if model == "default":
        from ..cli import BUNDLED_MODEL
        return BUNDLED_MODEL.stem
    if model.startswith("kraken:"):
        return pathlib.Path(model[7:]).stem
    return model.split(":")[1] if ":" in model else pathlib.Path(model).stem


def ocr_dir_for(work, model):
    """The OCR output of one recognition model: WORK/ocr-NAME. A new one borrows the page images and the
    line detection of another (they do not depend on the recognition model), so only recognition runs again."""
    work = pathlib.Path(work)
    name = model_name(model)
    ocr_dir = work / f"ocr-{name}"
    if not ocr_dir.exists():
        ocr_dir.mkdir(parents=True)
        donor = next((d for d in sorted(work.glob("ocr-*")) if d != ocr_dir and (d / "det").is_dir() and (d / "pages").is_dir()), None)
        if donor:
            for sub in ("pages", "det"):
                (ocr_dir / sub).symlink_to((donor / sub).resolve(), target_is_directory=True)
    (ocr_dir / "model.txt").write_text(f"{name} {model}\n")
    return ocr_dir


def is_latin(text):
    return len(LATIN.findall(text)) >= 3 and latin_ratio(text) >= 0.5


NUMBER_BOX = re.compile(r"^[(\[«]?(?:\*{1,3}|[0-9۰-۹]{1,4})[)\].\-–:]?$")
LEAD_NUMBER = re.compile(r"^[(\[«]?([0-9۰-۹]{1,4}|\*{1,3})")


def join_number_boxes(lines):
    """A note number the detector boxed on its own (a raised "۱۷" or "*" set apart from its line) is joined to the
    start of the small-type line it stands beside: right of a right-to-left line, left of a left-to-right one, on its
    height. Not on a page of number boxes (contents with the page numbers boxed apart, tables)."""
    out, used = [], set()
    # a box read with low confidence is a speck the layout drops as noise (conf < 50), not a note number
    nums = [l for l in lines if NUMBER_BOX.match(l.text.strip()) and re.search(r"[0-9۰-۹*]", l.text) and l.conf >= 50]
    if len(nums) >= 5 and len(nums) >= 0.25 * len(lines):
        return lines  # a page of number boxes is a contents page or a table: its numbers are page numbers, not notes'
    hs = sorted(l.y1 - l.y0 for l in lines if len(l.text) >= 6)
    med = hs[len(hs) // 2] if hs else 0
    for n in nums:
        best, gap_best = None, None
        for l in lines:
            if l is n or id(l) in used or NUMBER_BOX.match(l.text.strip()) or len(l.text) < 6:
                continue
            h = l.y1 - l.y0
            if med and h > 0.97 * med:
                continue  # notes are set in small type; a body-size line beside a number is something else
            if min(n.y1, l.y1) - max(n.y0, l.y0) < 0.4 * (n.y1 - n.y0) or n.y1 - n.y0 > 1.15 * h:
                continue
            gap = (n.x0 - l.x1) if not l.latin else (l.x0 - n.x1)
            if -0.3 * h <= gap <= 1.2 * h and (gap_best is None or gap < gap_best):
                best, gap_best = l, gap
        if best is not None:
            used.add(id(best))
            best.unjoined = (best.text, list(best.words), best.bbox, replace(n))  # labels.attach may undo the join
            best.text = n.text.strip() + " " + best.text
            best.words = [Word(n.text.strip(), n.bbox, n.conf)] + list(best.words)
            best.bbox = (min(best.x0, n.x0), min(best.y0, n.y0), max(best.x1, n.x1), max(best.y1, n.y1))
            best.joined = getattr(best, "joined", ()) + (n.row,)  # labels made on the rows as read refer to it
            n.text = ""
    return [l for l in lines if l.text]


def apply_note_numbers(lines, found):
    """Note numbers re-read from the image (notenum.py; {row: number}): put at the start of a line that has none,
    or in place of a leading number they extend (the line reader kept "10" of a raised "106")."""
    for l in lines:
        num = found.get(str(l.row))
        if not num:
            continue
        m = LEAD_NUMBER.match(l.text.strip())
        if m is None:
            l.text = f"{num} {l.text}"
        elif m.group(1) != num and num.startswith(m.group(1)):
            l.text = num + l.text.strip()[m.end():]


NOTENUM_VERSION = 2  # of notenum.json (notenum.py); an older file is read again, not used


def notenum_current(ocr_dir):
    """Whether OCR_DIR holds note numbers re-read by the current notenum."""
    nf = pathlib.Path(ocr_dir) / "notenum.json"
    try:
        return json.loads(nf.read_text(encoding="utf-8")).get("version") == NOTENUM_VERSION
    except (OSError, ValueError):
        return False


def rows(ocr_dir, index):
    """The OCR rows of PDF page INDEX as read: {row: {"text", "bbox", "conf", "words"}}, empty rows left out."""
    jl = pathlib.Path(ocr_dir) / "jsonl" / f"p-{index:03d}.jsonl"
    out = {}
    if jl.exists():
        for k, row in enumerate(map(json.loads, (r for r in jl.read_text(encoding="utf-8").splitlines() if r.strip()))):
            if row["text"].strip():
                out[k] = {"text": row["text"].strip(), "bbox": list(row["bbox"]), "conf": row["conf"],
                          "words": [{"text": w["text"], "bbox": list(w["bbox"]), "conf": w["conf"]} for w in row["words"]]}
    return out


def load(ocr_dir, note_numbers=True):
    ocr_dir = pathlib.Path(ocr_dir)
    pages = []
    found = {}
    if note_numbers and notenum_current(ocr_dir):
        found = json.loads((ocr_dir / "notenum.json").read_text(encoding="utf-8")).get("pages", {})
    for img in sorted((ocr_dir / "pages").glob("p-*.png")):
        pid = img.stem
        with Image.open(img) as im:
            w, h = im.size
        lines = []
        jl = ocr_dir / "jsonl" / f"{pid}.jsonl"
        if jl.exists():
            for k, row in enumerate(map(json.loads, (r for r in jl.read_text(encoding="utf-8").splitlines() if r.strip()))):
                text = row["text"].strip()
                if not text:
                    continue
                words = [Word(x["text"], tuple(x["bbox"]), x["conf"]) for x in row["words"]]
                lines.append(Line(text, tuple(row["bbox"]), row["conf"], words, row["column"], latin=is_latin(text), row=k))
        if os.environ.get("PARISAOCR_LINE_REPAIR", "1") != "0":  # 0: the OCR lines as read (for comparisons)
            lines = join_number_boxes(lines)
            if found.get(str(int(pid.split("-")[1]))):
                apply_note_numbers(lines, found[str(int(pid.split("-")[1]))])
        pages.append(Page(int(pid.split("-")[1]), pid, img, w, h, lines))
    if not pages:
        sys.exit(f"parisaocr: no page images under {ocr_dir}/pages")
    return pages
