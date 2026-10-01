"""Line recognition with a Kraken model: the second engine behind the reader seam.

`KrakenReader.read_many(items)` takes (image path, mode) pairs and returns, for
each image, its text lines as lists of `Word`s in that image's pixel
coordinates, the same shape `TesseractReader.read` returns. All crops of a
call are stacked into one sheet and recognized in GPU batches.

Mode BLOCK marks a box much taller than the page's lines (the detector merged
several lines); it is split into lines along the rows with the least ink
before recognition.

Workarounds for Kraken 7.1.1:
- mittagessen/kraken#810: PP-OCR models are trained on inputs right-padded to
  max_width (2560 px) and invent characters at the line end when they are not;
  inputs are padded the same way at inference.
- mittagessen/kraken#809: Kraken's bidi reordering deletes ZWNJ. Models trained
  on data from scripts/kraken_zwnj.py write U+00A6 instead; it is mapped back.

Each line's base direction comes from its recognized text: a line with more
Latin than Arabic-script letters is reordered left to right (a footnote such
as "1. Vercingetorix"), every other line right to left.
"""
import threading
import types

import numpy as np
from PIL import Image

from .bidi import PLACEHOLDER, ZWNJ, to_logical
from .fa_text import canonical
from .reader import Word
SHEET_GAP = 16      # white rows between stacked crops
SHEET_LINES = 96    # crops per recognition call


def _pad_input(net, width):
    """Pad every input batch on the right to WIDTH, as the PP-OCR training collation does (#810)."""
    import torch
    import torch.nn.functional as F
    predict = net._rec_predict

    def padded(self, line, lens=None):
        if lens is None:
            lens = torch.full((line.shape[0],), line.shape[3], dtype=torch.long)
        if line.shape[3] < width:
            line = F.pad(line, (0, width - line.shape[3]))
        return predict(line, lens)
    net._rec_predict = types.MethodType(padded, net)


def split_lines(im, min_share=0.03):
    """Row ranges (y0, y1) of the text lines in a tall crop, cut at the rows with the least ink.

    A row is "empty" when its ink is below 15% of a typical text row. Runs of empty rows away
    from the edges become cuts, placed at the emptiest row of the run. Pieces with almost no ink
    (a stray dot row) are merged into their neighbour. One range when no cut is found.
    """
    a = np.asarray(im, dtype=np.float32)
    lo, bg = np.percentile(a, 1), np.percentile(a, 60)
    if bg - lo < 30:
        return [(0, im.height)]
    ink = (a < (lo + bg) / 2).sum(axis=1).astype(np.float32)
    k = max(1, im.height // 80)
    smooth = np.convolve(ink, np.ones(k) / k, mode="same")
    typical = np.percentile(smooth, 90)
    if typical <= 0:
        return [(0, im.height)]
    low = smooth < 0.15 * typical
    edge = max(2, im.height // 20)
    cuts, y = [], edge
    while y < im.height - edge:
        if low[y]:
            y1 = y
            while y1 < im.height - edge and low[y1]:
                y1 += 1
            cuts.append(y + int(np.argmin(smooth[y:y1])))
            y = y1
        y += 1
    bounds = [0, *cuts, im.height]
    pieces = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    total = ink.sum() or 1.0
    share = lambda p: ink[p[0]:p[1]].sum() / total
    merged = []
    for p in pieces:
        if merged and (share(p) < min_share or share(merged[-1]) < min_share):
            merged[-1] = (merged[-1][0], p[1])   # a piece with almost no ink joins its neighbour
        else:
            merged.append(p)
    return merged


class KrakenReader:
    RAW_LINE, SINGLE_LINE, BLOCK = "13", "7", "6"   # the mode tags the pipeline uses

    def __init__(self, model_path, device="cuda:0", pad_to="auto", batch_size=16):
        import torch
        # TF32 matrix products on GPUs that have them: faster, and Lightning stops printing advice about it.
        torch.set_float32_matmul_precision("high")
        from kraken.configs import RecognitionInferenceConfig
        from kraken.ketos.util import to_ptl_device
        from kraken.tasks import RecognitionTaskModel
        self.path = str(model_path)
        self.model = RecognitionTaskModel.load_model(self.path)
        net = self.model.net
        is_ppocr = "ppocr" in type(net).__name__.lower()
        self.pad_to = (2560 if is_ppocr else 0) if pad_to == "auto" else int(pad_to)
        if self.pad_to:
            _pad_input(net, self.pad_to)
        accelerator, dev = to_ptl_device(device)
        # Records come back in display order; each line is reordered with its own base direction.
        self.config = RecognitionInferenceConfig(accelerator=accelerator, device=dev, bidi_reordering=False,
                                                 num_line_workers=0, batch_size=batch_size)
        self.lock = threading.Lock()   # the model keeps per-call state; one call at a time

    def describe(self):
        return f"kraken {self.path}"

    def read(self, image_path, mode):
        return self.read_many([(image_path, mode)])[0]

    def read_many(self, items):
        pieces = []   # (item index, piece image, y offset of the piece in the item image)
        for i, (path, mode) in enumerate(items):
            with Image.open(path) as im:
                g = im.convert("L")
            spans = split_lines(g) if mode == self.BLOCK else [(0, g.height)]
            for y0, y1 in spans:
                pieces.append((i, g.crop((0, y0, g.width, y1)), y0))
        results = [[] for _ in items]
        for start in range(0, len(pieces), SHEET_LINES):
            chunk = pieces[start:start + SHEET_LINES]
            for (i, _, yoff), words in zip(chunk, self._recognize(chunk)):
                if words:
                    results[i].append([Word(w.text, (w.bbox[0], w.bbox[1] + yoff, w.bbox[2], w.bbox[3] + yoff), w.conf)
                                       for w in words])
        return results

    def _recognize(self, chunk):
        from kraken.containers import BBoxLine, Segmentation
        width = max(p[1].width for p in chunk)
        height = sum(p[1].height + SHEET_GAP for p in chunk)
        sheet = Image.new("L", (width, height), 255)
        boxes, y = [], 0
        for _, im, _ in chunk:
            sheet.paste(im, (0, y))
            boxes.append((0, y, im.width, y + im.height))
            y += im.height + SHEET_GAP
        seg = Segmentation(type="bbox", imagename="sheet", text_direction="horizontal-tb", script_detection=False,
                           lines=[BBoxLine(id=f"l{k}", bbox=b) for k, b in enumerate(boxes)])
        with self.lock:
            records = list(self.model.predict(im=sheet, segmentation=seg, config=self.config))
        return [self._words(rec, box) for rec, box in zip(records, boxes)]

    def _words(self, rec, box):
        """Words of one recognized line in logical order, boxes relative to the piece image.

        CTC places each character at a single column, so a word's box runs from the space before
        it to the space after it in display order (or halfway to the neighbouring character, or
        the line end); vertically it takes the whole line.
        """
        # Models trained on scripts/kraken_zwnj.py data write the placeholder for ZWNJ; newer ones a real
        # ZWNJ. Both are turned into reading order by parisaocr.bidi, which keeps the ZWNJ (#809).
        disp = rec.prediction.replace(PLACEHOLDER, ZWNJ)
        if not disp.strip():
            return []
        xs = [cut[0][0] - box[0] for cut in rec.cuts]
        width, height = box[2] - box[0], box[3] - box[1]
        logical, order = to_logical(disp)  # order[i]: display index of logical char i
        words, cur = [], []
        for ch, idx in zip(logical, order):
            if ch.isspace():
                if cur:
                    words.append(cur)
                cur = []
            else:
                cur.append((ch, idx))
        if cur:
            words.append(cur)

        def edge(i, step):
            j = i + step
            if not 0 <= j < len(disp):
                return 0 if step < 0 else width
            return xs[j] if disp[j].isspace() else (xs[i] + xs[j]) / 2

        out = []
        for w in words:
            wtext = canonical("".join(c for c, _ in w))
            if not wtext:
                continue
            lo, hi = min(i for _, i in w), max(i for _, i in w)
            x0, x1 = max(0, edge(lo, -1)), min(width, edge(hi, 1))
            conf = 100 * float(np.mean([rec.confidences[i] for _, i in w]))
            out.append(Word(wtext, (int(round(x0)), 0, int(round(x1)), height), round(conf, 1)))
        return out
