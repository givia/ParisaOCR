"""Footnote markers re-read from the page image, each on its own.

A marker of two or three digits is small and raised, and the line reader loses some of its digits, the hairline ۱
and the dot-like ۰ above all ("۱۰" for "۱۰۵", "۱۳" for "۱۱۳"): it scales a line to its own height as a whole, so a
raised digit comes out a few pixels across. Cropped to the mark's own height, its digits are scaled up two to three
times. `markers.add_markers` asks for the raised marks it finds on a page with footnotes where the line has no note
number of the page; each is read by the recognition model with its decoding limited to digits
(`notenum.digits_reader`), and a number that is one of the page's notes then stands for the marker. The readings are
kept in OCR_DIR/markernum.json, so a book's marks are read once (PARISAOCR_MARKERNUM=0: none are read).

A mark is read padded to 640 pixels, not to the 2,560 the model's lines are padded to (a long line read unpadded
gains letters at its edge, kraken#810): on 600 marks of the test set the readings are the same 583 times and right as
often (519 against 517), and a mark takes 31 ms on a CPU instead of 226.
"""
import json
import os
import pathlib
import re
import tempfile
import threading

from PIL import Image

VERSION = 2
PAD_TO = 640  # the width a mark is padded to for the reader
MIN_CONF = 50.0  # low: the number must also be one of the page's notes (`markers.add_markers`)
_RUN = re.compile(r"[0-9]+|[۰-۹]+")
_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_settings = {"model": None, "device": None}
_reader = None
_lock = threading.Lock()
_caches = {}


def configure(model_path, device):
    """The model and device of the re-reading (`convert` gives the book's own); unset, the bundled model, on the GPU
    if there is one."""
    _settings.update(model=str(model_path) if model_path else None, device=device)


def enabled():
    return os.environ.get("PARISAOCR_MARKERNUM", "1") != "0"


def _model():
    if _settings["model"]:
        return _settings["model"]
    from ..cli import BUNDLED_MODEL
    return str(BUNDLED_MODEL)


def _get_reader():
    global _reader
    if _reader is None:
        from .notenum import digits_reader
        device = _settings["device"]
        if device is None:
            import torch
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        _reader = digits_reader(_model(), device)
        _reader.pad_to = PAD_TO
    return _reader


class _Cache:
    """OCR_DIR/markernum.json: {"version", "model", "reads": {key: digits or null}}."""

    def __init__(self, ocr_dir):
        self.path = pathlib.Path(ocr_dir) / "markernum.json"
        self.model = pathlib.Path(_model()).name
        self.reads = {}
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if d.get("version") == VERSION and d.get("model") == self.model:
                self.reads = d.get("reads") or {}
        except (OSError, ValueError):
            pass

    def save(self):
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")  # each process its own: conversions of a book may run side by side
        tmp.write_text(json.dumps({"version": VERSION, "model": self.model, "reads": self.reads}, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.path)


def _cache_for(page):
    ocr_dir = pathlib.Path(page.image).resolve().parent.parent  # OCR_DIR/pages/p-NNN.png
    c = _caches.get(ocr_dir)
    if c is None or c.model != pathlib.Path(_model()).name:
        c = _caches[ocr_dir] = _Cache(ocr_dir)
    return c


def key(page, line, box):
    """A mark's key: the page, the line's box, the crop's sides (stable across conversions of the same OCR)."""
    return f"{page.index}:{','.join(str(int(v)) for v in line.bbox)}:{box[0]}:{box[2]}"


def read(page, jobs):
    """{key: digits (Persian) or None} for JOBS [(key, crop box)] on PAGE (`read_many`)."""
    return read_many([(page, k, b) for k, b in jobs])


def _number(words):
    """The digits a mark's words carry: one run of at most three digits read with confidence, not starting with 0
    (a period read as a zero), else None. A period or a bracket the crop caught beside them is left out."""
    text = "".join(w.text for w in words)
    runs = _RUN.findall(text)
    if len(runs) != 1 or len(runs[0]) > 3 or min(w.conf for w in words) < MIN_CONF:
        return None
    d = runs[0].translate(_TO_FA)
    return None if len(d) > 1 and d.startswith("۰") else d


def read_many(jobs):
    """{key: digits (Persian) or None} for JOBS [(page, key, crop box)], of any pages: the kept readings, the others
    read now and kept."""
    out, todo, seen = {}, [], set()
    for page, k, box in jobs:
        c = _cache_for(page)
        if k in c.reads:
            out[k] = c.reads[k]
        elif k not in seen:
            seen.add(k)
            todo.append((page, k, box))
    if not todo:
        return out
    todo.sort(key=lambda j: str(j[0].image))  # a page image opened once
    with tempfile.TemporaryDirectory() as tmp:
        paths, img, cur = [], None, None
        for i, (page, _, box) in enumerate(todo):
            if page.image != cur:
                with Image.open(page.image) as im:
                    img = im.convert("L")
                cur = page.image
            f = pathlib.Path(tmp) / f"{i}.png"
            img.crop(box).save(f)
            paths.append(str(f))
        with _lock:
            reader = _get_reader()
            results = reader.read_many([(p, reader.RAW_LINE) for p in paths])
    touched = {}
    for (page, k, _), lines in zip(todo, results):
        words = [w for line in lines for w in line]
        c = _cache_for(page)
        out[k] = c.reads[k] = _number(words) if words else None
        touched[id(c)] = c
    for c in touched.values():
        c.save()
    return out
