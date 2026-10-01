"""Python interface: read a page image and get its text or its lines.

    from parisaocr import OCR
    ocr = OCR()                       # loads the detector and the recognition model once
    print(ocr.text("page.png"))       # the page's text, lines in reading order
    for line in ocr.lines("page.png"):
        print(line.bbox, line.conf, line.text)

`OCR(model=..., detector=..., device=...)` takes the same model and detector
specifications as the command line (`parisaocr ocr --help`).
"""
import pathlib
import tempfile
from types import SimpleNamespace

from PIL import Image

from . import output
from .cli import build_engine
from .order import order
from .pipeline import recognize_batch


class OCR:
    def __init__(self, model="default", detector="ppocr:v6-small:1.3", cpu=False, line_order="rtl"):
        opts = SimpleNamespace(model=model, detector=detector, cpu=cpu, min_width=1600, max_width=2000, batch=1,
                               pad=6, pad_frac=0.4, min_height=40, block_tall=True, fallback=True, jobs=8)
        self.detector, self.reader, self.options = build_engine(opts)
        self.line_order = line_order

    def columns(self, image):
        """Lines of one page (a path or a PIL image) as reading-order columns of `pipeline.Line`."""
        with tempfile.TemporaryDirectory(prefix="parisaocr-") as tmp:
            tmp = pathlib.Path(tmp)
            if not isinstance(image, (str, pathlib.Path)):
                path = tmp / "page.png"
                image.save(path)
            else:
                path = pathlib.Path(image)
            with Image.open(path) as page:
                im, f = self.detector.prepare(page)
                rec = {"page": "page", "image": str(path), "width": im.width, "height": im.height, "scale": f,
                       "orig_width": page.width, "orig_height": page.height}
            rec["lines"] = self.detector.detect([im])[0]
            lines = recognize_batch([rec], self.reader, self.options, tmp)["page"]
        return order(lines, self.line_order)

    def lines(self, image):
        """The lines of one page in reading order (text, bbox on the page, mean confidence, words)."""
        return [line for col in self.columns(image) for line in col]

    def text(self, image):
        """The text of one page: one line per printed line, columns right to left."""
        return output.text(self.columns(image))
