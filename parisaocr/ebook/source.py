"""OCR stage: read the PDF with ParisaOCR (in this process) and load its per-page JSONL.

The OCR output is kept per recognition model in WORK/ocr-NAME, so a second run
of the same book takes seconds and switching models reruns only recognition.
Lines that are mostly Latin letters are marked `latin`: they are typeset left
to right, and are not searched for note markers or verse.
"""
import json
import pathlib
import sys
from dataclasses import dataclass, field

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


def load(ocr_dir):
    ocr_dir = pathlib.Path(ocr_dir)
    pages = []
    for img in sorted((ocr_dir / "pages").glob("p-*.png")):
        pid = img.stem
        with Image.open(img) as im:
            w, h = im.size
        lines = []
        jl = ocr_dir / "jsonl" / f"{pid}.jsonl"
        if jl.exists():
            for row in map(json.loads, jl.read_text(encoding="utf-8").splitlines()):
                text = row["text"].strip()
                if not text:
                    continue
                words = [Word(x["text"], tuple(x["bbox"]), x["conf"]) for x in row["words"]]
                lines.append(Line(text, tuple(row["bbox"]), row["conf"], words, row["column"], latin=is_latin(text)))
        pages.append(Page(int(pid.split("-")[1]), pid, img, w, h, lines))
    if not pages:
        sys.exit(f"parisaocr: no page images under {ocr_dir}/pages")
    return pages
