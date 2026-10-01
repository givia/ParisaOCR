"""Text-line detection. `LineDetector` wraps Surya; replace it to try another detector.

A detection record (cached as JSON) holds the page image path, the size of the
image the detector saw (`width`, `height`), the factor it was scaled by
(`scale`, 1.0 when the page was left alone) and the line boxes in those
detection coordinates. Small pages are upscaled to `min_width` first: the
detector misses lines on low-resolution scans otherwise; pages wider than
`max_width` are reduced for detection only.
"""
import json
import os
import pathlib

from PIL import Image


class LineDetector:
    def __init__(self, min_width=1600, max_width=2000, batch=8, cpu=False):
        self.min_width, self.max_width, self.batch, self.cpu = min_width, max_width, batch, cpu
        if cpu:
            os.environ["TORCH_DEVICE"] = "cpu"
        self._model = None

    def model(self):
        if self._model is None:
            from surya.detection import DetectionPredictor  # slow import, only when detection is needed
            # local(): this process owns the model. The default constructor talks to a shared
            # server subprocess that outlives the client (it was found still holding GPU memory
            # minutes after a run) and that crashed on batches of large pages.
            self._model = DetectionPredictor.local()
        return self._model

    def prepare(self, image):
        """The RGB image the detector will see and the factor it was scaled by.

        Small pages are enlarged (the detector misses lines on them), very large
        scans are reduced (the detector works at a fixed internal size anyway,
        and a batch of 400-dpi pages overflows its server).
        """
        im = image.convert("RGB")
        f = 1.0
        if 0 < im.width < self.min_width:
            f = self.min_width / im.width
        elif self.max_width and im.width > self.max_width:
            f = self.max_width / im.width
        if f != 1.0:
            im = im.resize((round(im.width * f), round(im.height * f)), Image.LANCZOS)
        return im, f

    def detect(self, images):
        """Line boxes for each RGB image, as dicts with bbox, polygon, confidence (detection coordinates)."""
        out = []
        for i in range(0, len(images), self.batch):
            for res in self.model()(images[i:i + self.batch]):
                out.append([{"bbox": [round(v) for v in b.bbox],
                             "polygon": [[round(x), round(y)] for x, y in b.polygon],
                             "confidence": round(float(b.confidence), 3)} for b in res.bboxes])
        return out


def detect_pages(detector, pages, cache_dir=None, redo=False):
    """Detection records for (page id, image path) pairs, batch by batch, using CACHE_DIR/PID.json when present."""
    if cache_dir:
        cache_dir = pathlib.Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(pages), detector.batch):
        chunk, records, todo = pages[i:i + detector.batch], {}, []
        for pid, path in chunk:
            cached = cache_dir / f"{pid}.json" if cache_dir else None
            if cached and cached.exists() and not redo:
                records[pid] = json.loads(cached.read_text(encoding="utf-8"))
            else:
                todo.append((pid, path))
        if todo:
            images, recs = [], []
            for pid, path in todo:
                with Image.open(path) as page:
                    im, f = detector.prepare(page)
                    recs.append({"page": pid, "image": str(path), "width": im.width, "height": im.height, "scale": f,
                                 "orig_width": page.width, "orig_height": page.height})
                images.append(im)
            for rec, lines in zip(recs, detector.detect(images)):
                rec["lines"] = lines
                records[rec["page"]] = rec
                if cache_dir:
                    (cache_dir / f"{rec['page']}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        yield [records[pid] for pid, _ in chunk]
