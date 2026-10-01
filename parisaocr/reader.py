"""Line recognition. `TesseractReader` is the only code that knows Tesseract exists.

read(image, mode) returns the text lines found in one line image as lists of
words with boxes in that image's pixel coordinates. A raw-line crop gives one
list; a block crop (mode "6") may give several.
"""
import os
import subprocess
from dataclasses import dataclass

from .fa_text import canonical


@dataclass
class Word:
    text: str
    bbox: tuple  # x0, y0, x1, y1
    conf: float  # 0-100


class TesseractReader:
    RAW_LINE, SINGLE_LINE, BLOCK = "13", "7", "6"

    def __init__(self, tessdata, lang, extra_args=()):
        self.tessdata, self.lang, self.extra = str(tessdata), lang, list(extra_args)
        self.env = {**os.environ, "OMP_THREAD_LIMIT": "1"}  # one thread per process; we parallelize over lines

    def describe(self):
        return f"tesseract {self.lang} ({self.tessdata})"

    def read(self, image_path, mode):
        cmd = ["tesseract", str(image_path), "-", "--tessdata-dir", self.tessdata, "-l", self.lang, "--psm", mode,
               *self.extra, "-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=self.env)
        if proc.returncode:
            raise RuntimeError(f"tesseract exited {proc.returncode} on {image_path}: {proc.stderr.strip()[-300:]}")
        lines, current, key = [], [], None
        for row in proc.stdout.splitlines()[1:]:
            f = row.split("\t")
            if len(f) < 12 or f[0] != "5":
                continue
            text = canonical(f[11])
            if not text:
                continue
            k = (f[2], f[3], f[4])  # block, paragraph, line
            if k != key:
                if current:
                    lines.append(current)
                current, key = [], k
            x, y, w, h = (int(v) for v in f[6:10])
            current.append(Word(text, (x, y, x + w, y + h), float(f[10])))
        if current:
            lines.append(current)
        return lines


def line_text(words):
    return canonical(" ".join(w.text for w in words))


def alnum(words):
    return sum(c.isalnum() for w in words for c in w.text)
