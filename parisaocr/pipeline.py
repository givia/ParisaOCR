"""From a detection record to recognized lines with page coordinates.

Every detected box is cropped from the (upscaled) page with a small margin taken
from the page itself, enlarged to at least `min_height` pixels, and read in
raw-line mode. A box much taller than the page's typical line is a merged
paragraph and is read in block mode, which yields several lines. A raw-line
result with fewer than three letters or digits is retried in single-line mode
on a white-padded copy (raw-line mode sometimes returns only joiners for a
readable crop). Word boxes come back in the coordinates of the original page.
"""
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageOps

from .reader import Word, alnum, line_text


@dataclass
class Options:
    pad: int = 6            # least horizontal margin around a box, in working pixels (vertical: half)
    pad_frac: float = 0.4   # horizontal margin as a fraction of the page's median line height (the larger of the two wins)
    min_height: int = 40    # crops shorter than this are enlarged
    block_tall: bool = True  # boxes > 1.8x the median line height are read as blocks
    fallback: bool = True   # retry near-empty raw-line results in single-line mode
    jobs: int = 8
    # Lines of at most 4 characters read with less than this mean confidence are dropped: they
    # come from marks that are not text (a box, a rule end, letters of a sideways running head).
    # Real short lines (page numbers, "است.") are read with 93-100 by the Kraken model. 0 = keep all.
    short_conf: float = 0.0


@dataclass
class Crop:
    index: int             # position in the detection record
    path: object
    mode: str
    origin: tuple          # top-left of the crop in working coordinates
    zoom: float            # enlargement applied to the crop
    scale: float           # page -> working coordinates (the enlarged page, or 1.0)
    border: int = 0        # white padding added for the fallback pass

    def to_page(self, x, y):
        return (round(((x - self.border) / self.zoom + self.origin[0]) / self.scale),
                round(((y - self.border) / self.zoom + self.origin[1]) / self.scale))


@dataclass
class Line:
    bbox: tuple            # page coordinates
    text: str
    words: list = field(default_factory=list)
    conf: float = 0.0      # mean word confidence, 0-100
    det_conf: float = 0.0  # detector confidence of the box
    box: int = 0           # index of the detected box this line came from


def margins(pad, pad_frac, line_height):
    """(horizontal, vertical) margin around a line box: the larger of a fixed pixel count and a
    share of the typical line height. Detector boxes hug the letter bodies; on a 300-dpi scan
    a few pixels leave the dots outside the crop, while a margin that scales with the text does not."""
    px = max(pad, round(pad_frac * line_height))
    return px, max(2, px // 2)


def expand(boxes, px, py, width, height):
    """Crop rectangles for line boxes: each box grown by (px, py) and clipped to the page."""
    return [(max(0, x0 - px), max(0, y0 - py), min(width, x1 + px), min(height, y1 + py)) for x0, y0, x1, y1 in boxes]


def line_crop(im, rect, own, boxes):
    """Crop RECT from IM with every other line box painted over in the background colour.

    The margin has to be generous (the dots of a line sit outside the detector's box), but on
    dense pages it then reaches into the neighbouring lines, and a crop that holds a slice of
    the next line is misread. Erasing the neighbours' boxes keeps both: our dots in the gap
    stay, their letter bodies go. The part of a neighbour that overlaps our own box is left alone.
    """
    crop = im.crop(rect)
    cx, cy = rect[0], rect[1]
    fills = []
    for other in boxes:
        if other is own:
            continue
        u0, v0, u1, v1 = max(other[0], rect[0]), max(other[1], rect[1]), min(other[2], rect[2]), min(other[3], rect[3])
        if u1 <= u0 or v1 <= v0:
            continue
        oc = ((other[1] + other[3]) / 2, (other[0] + other[2]) / 2)
        if oc[0] < own[1]:      # neighbour above: stop at our top edge
            v1 = min(v1, own[1])
        elif oc[0] > own[3]:    # below
            v0 = max(v0, own[3])
        elif oc[1] < own[0]:    # to the left
            u1 = min(u1, own[0])
        else:                   # to the right
            u0 = max(u0, own[2])
        if u1 > u0 and v1 > v0:
            fills.append((u0 - cx, v0 - cy, u1 - cx, v1 - cy))
    if fills:
        border = list(crop.crop((0, 0, crop.width, 1)).getdata()) + list(crop.crop((0, crop.height - 1, crop.width, crop.height)).getdata())
        background = sorted(border)[len(border) // 2] if crop.mode == "L" else 255
        draw = ImageDraw.Draw(crop)
        for f in fills:
            draw.rectangle(f, fill=background)
    return crop


def crops_for(record, opts, tmp):
    """Write the line crops of one page to TMP and describe them."""
    with Image.open(record["image"]) as page:
        im = page.convert("L")
    scale = record.get("scale", 1.0)
    # Crops come from the enlarged page when the page was small (that is what the detector saw),
    # but from the original page when it was reduced for detection only.
    work = scale if scale > 1.0 else 1.0
    if work != 1.0:
        im = im.resize((record["width"], record["height"]), Image.LANCZOS)
    boxes = [[round(v * work / scale) for v in l["bbox"]] for l in record["lines"]]
    heights = [b[3] - b[1] for b in boxes]
    median_h = statistics.median(heights) if heights else 0
    crops = []
    px, py = margins(opts.pad, opts.pad_frac, median_h)
    rects = expand(boxes, px, py, im.width, im.height)
    for k, ((x0, y0, x1, y1), rect) in enumerate(zip(boxes, rects)):
        if x1 - x0 < 8 or y1 - y0 < 6:
            continue
        tall = opts.block_tall and (y1 - y0) > 1.8 * median_h and (y1 - y0) > 2 * opts.min_height // 3
        origin = rect[:2]
        crop = line_crop(im, rect, boxes[k], boxes)
        zoom = 1.0
        if crop.height < opts.min_height:
            zoom = opts.min_height / crop.height
            crop = crop.resize((round(crop.width * zoom), opts.min_height), Image.LANCZOS)
        path = tmp / f"{record['page']}_{k:03d}.png"
        crop.save(path)
        crops.append(Crop(k, path, "6" if tall else "13", origin, zoom, work))
    return crops


def safe_read(reader, path, mode):
    """An engine failure loses one crop, not the page: Tesseract aborts on some degenerate crops
    ("Image too small to scale" when the line it finds is a speck, SIGFPE in the line normalizer)."""
    try:
        return reader.read(path, mode)
    except RuntimeError as e:
        print(f"parisaocr: {e}", file=sys.stderr, flush=True)
        return []


def read_crop(crop, reader, opts):
    """Lines of words in crop coordinates; sets crop.border when the fallback result was kept."""
    lines = safe_read(reader, crop.path, crop.mode)
    if crop.mode == reader.RAW_LINE and opts.fallback and sum(alnum(l) for l in lines) < 3:
        alt_path = crop.path.with_name(crop.path.stem + "_alt.png")
        with Image.open(crop.path) as im:
            ImageOps.expand(im, border=20, fill=255).save(alt_path)
        alt = safe_read(reader, alt_path, reader.SINGLE_LINE)
        if sum(alnum(l) for l in alt) > sum(alnum(l) for l in lines):
            lines, crop.border = alt, 20
    return lines


def assemble(record, crops, results):
    """Recognized lines of a page in page coordinates, in detection order."""
    scale = record.get("scale", 1.0)
    out = []
    for crop, lines in zip(crops, results):
        det = record["lines"][crop.index]
        bx0, by0, bx1, by1 = (round(v / scale) for v in det["bbox"])
        for words in lines:
            text = line_text(words)
            if not text:
                continue
            mapped = [Word(w.text, (*crop.to_page(w.bbox[0], w.bbox[1]), *crop.to_page(w.bbox[2], w.bbox[3])), w.conf) for w in words]
            if crop.mode == "6" and len(lines) > 1:  # each line of a block gets the box of its words
                bbox = (min(w.bbox[0] for w in mapped), min(w.bbox[1] for w in mapped),
                        max(w.bbox[2] for w in mapped), max(w.bbox[3] for w in mapped))
            else:
                bbox = (bx0, by0, bx1, by1)
            conf = statistics.mean(w.conf for w in mapped) if mapped else 0.0
            out.append(Line(bbox, text, mapped, round(conf, 1), det.get("confidence", 0.0), crop.index))
    return out


def recognize_batch(records, reader, opts, tmp):
    """{page id: [Line]} for a batch of detection records; crops are read in parallel."""
    per_page = {rec["page"]: crops_for(rec, opts, tmp) for rec in records}
    jobs = [c for crops in per_page.values() for c in crops]
    if hasattr(reader, "read_many"):  # a batched (GPU) engine reads all crops of the batch in one call
        results = dict(zip((id(c) for c in jobs), reader.read_many([(c.path, c.mode) for c in jobs])))
    else:
        with ThreadPoolExecutor(max(1, opts.jobs)) as pool:
            results = dict(zip((id(c) for c in jobs), pool.map(lambda c: read_crop(c, reader, opts), jobs)))
    out = {}
    for rec in records:
        crops = per_page[rec["page"]]
        lines = assemble(rec, crops, [results[id(c)] for c in crops])
        if opts.short_conf:
            lines = [l for l in lines if len(l.text) > 4 or l.conf >= opts.short_conf]
        out[rec["page"]] = lines
        for c in crops:
            c.path.unlink(missing_ok=True)
            c.path.with_name(c.path.stem + "_alt.png").unlink(missing_ok=True)
    return out
