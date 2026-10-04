"""Note numbers re-read from the page image.

Footnote numbers are small and raised, and the line reader drops them or some of their digits ("10" for a raised
"106", "۲۲۴" for "۴۴۴"). For every small-type line in the lower part of a page whose text starts without a number,
or with one of one or two digits, the start of the line (its right end, or its left end for a left-to-right line) is
read again by the same recognition model with its decoding restricted to digits and note signs (* ( ) [ ] - .). A
number read with good confidence is kept in OCR_DIR/notenum.json ({page: {row: number}}), which `source.load` applies:
put at the start of a line that has none, or in place of a leading number it extends.
"""
import json
import pathlib
import re
import tempfile
import types

from PIL import Image

from . import source

ALLOWED = set("0123456789۰۱۲۳۴۵۶۷۸۹*()-.[]")
NUMBER = re.compile(r"^[\s(\[]*(\*{1,3}|[0-9۰-۹]{1,4})")
MIN_CONF = 80.0


def digits_reader(model_path, device):
    """A KrakenReader whose decoding can only produce ALLOWED characters (and the CTC blank)."""
    import torch
    from ..kraken_reader import KrakenReader
    reader = KrakenReader(model_path, device=device)
    net = reader.model.net
    allowed = [0] + [lab[0] for lab, ch in net.codec.l2c.items() if isinstance(ch, str) and ch in ALLOWED]

    def digits_only(self, line, lens=None):
        if lens is None:
            lens = torch.full((line.shape[0],), line.shape[3], dtype=torch.long)
        if reader.pad_to and line.shape[3] < reader.pad_to:
            line = torch.nn.functional.pad(line, (0, reader.pad_to - line.shape[3]))
        logits, olens = self.nn(line, lens)
        # the full softmax, then what is not a digit or a note sign goes to the CTC blank: a letter stays "nothing
        # here" instead of becoming its nearest digit with a high confidence (ا, ر, و read as ۱)
        probs = (logits / self._inf_config.temperature).softmax(1)
        keep = torch.zeros(probs.shape[1], dtype=torch.bool, device=probs.device)
        keep[allowed] = True
        shape = (1, -1, *([1] * (probs.dim() - 2)))
        rest = (probs * (~keep).view(shape)).sum(1, keepdim=True)
        probs = probs * keep.view(shape)
        probs[:, :1] = probs[:, :1] + rest
        self.outputs = probs.detach().squeeze(2)
        return [self.codec.decode(locs) for locs in self._inf_config.decoder(self.outputs, olens)], olens
    net._rec_predict = types.MethodType(digits_only, net)
    return reader


def _ascii(num):
    return num.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))


def candidates(page):
    """(line, crop box) for the page's lines that may have lost their note number: small type, lower part of the
    page, no leading number or one of one or two digits. The crop is the line's start edge — outside the box, where a
    raised number the detector cut off sits, and a little inside it — or, for a leading number, that word's box."""
    lines = [l for l in page.lines if len(l.text) >= 6]
    if not lines:
        return []
    hs = sorted(l.y1 - l.y0 for l in lines)
    med = hs[len(hs) // 2]
    out = []
    for l in lines:
        h = l.y1 - l.y0
        if h > 0.95 * med or l.y0 < 0.4 * page.height:
            continue
        m = source.LEAD_NUMBER.match(l.text.strip())
        if m and (m.group(1).startswith("*") or len(m.group(1)) >= 3):
            continue
        first = l.words[0].bbox if (m and l.words) else None
        if l.latin:
            inner = (first[2] + 0.2 * h) if first else (l.x0 + 0.8 * h)
            box = (l.x0 - 1.2 * h, l.y0 - 0.35 * h, inner, l.y1 + 0.2 * h)
        else:
            inner = (first[0] - 0.2 * h) if first else (l.x1 - 0.8 * h)
            box = (inner, l.y0 - 0.35 * h, l.x1 + 1.2 * h, l.y1 + 0.2 * h)
        box = (max(0, int(box[0])), max(0, int(box[1])), min(page.width, int(box[2])), min(page.height, int(box[3])))
        if box[2] - box[0] > 4 and box[3] - box[1] > 4:
            out.append((l, box))
    return out


def fits(page, line, num):
    """Whether a re-read number fits the page's notes: between the note numbers the lines above and below it start
    with (small-type lines with a leading number). A page with no numbered note line gets none: there a letter
    read as a digit would put a false number into the text."""
    if num.startswith("*"):
        return True
    n = int(_ascii(num))
    known = []
    for l in page.lines:
        if l is line:
            continue
        m = source.LEAD_NUMBER.match(l.text.strip())
        if m and not m.group(1).startswith("*") and l.y0 >= 0.4 * page.height:
            known.append((l.y0, int(_ascii(m.group(1)))))
    above = [k for y, k in known if y < line.y0]
    below = [k for y, k in known if y > line.y0]
    if not above and not below:
        return False
    lo, hi = (above[-1] if above else None), (below[0] if below else None)
    if lo is not None and not (lo < n <= lo + 3):
        return False
    if hi is not None and not (hi - 3 <= n < hi):
        return False
    return True


def reread(ocr_dir, model_path, device="cpu", log=print):
    """Re-read the note numbers of a book's OCR output and write OCR_DIR/notenum.json. -> numbers kept."""
    ocr_dir = pathlib.Path(ocr_dir)
    pages = source.load(ocr_dir, note_numbers=False)
    jobs = [(p, l, box) for p in pages for l, box in candidates(p)]
    found = {}
    if jobs:
        log(f"parisaocr: re-reading the note numbers of {len(jobs)} lines from the page images")
        reader = digits_reader(model_path, device)
        with tempfile.TemporaryDirectory() as tmp:
            items, by_page = [], {}
            for k, (p, l, box) in enumerate(jobs):
                if p.index not in by_page:
                    by_page[p.index] = Image.open(p.image).convert("L")
                f = pathlib.Path(tmp) / f"{k}.png"
                by_page[p.index].crop(box).save(f)
                items.append((str(f), reader.RAW_LINE))
                if len(by_page) > 4:  # keep a few decoded pages only
                    by_page.pop(next(iter(by_page)))
            results = reader.read_many(items)
        used = {}  # page -> the note numbers its lines already start with, or were given: one line each
        for p in pages:
            used[p.index] = {_ascii(m.group(1)) for x in p.lines if (m := source.LEAD_NUMBER.match(x.text.strip()))}
        for (p, l, box), words in sorted(zip(jobs, results), key=lambda jw: (jw[0][0].index, jw[0][1].y0)):
            flat = [w for line in words for w in line]
            got = " ".join(w.text for w in flat).strip()
            m = NUMBER.match(got)
            if not m or not flat or min(w.conf for w in flat) < MIN_CONF:
                continue
            num = m.group(1)
            lead = source.LEAD_NUMBER.match(l.text.strip())
            if len(num) > 1 and _ascii(num).startswith("0"):
                continue  # no note number starts with 0: a period read as a Persian zero ("۱." read as "۰۱")
            if re.search("[0-9]", num) and re.search("[۰-۹]", num):
                continue  # Latin and Persian digits mixed: a period read as a Persian zero ("1." read as "1۰")
            if lead and _ascii(num) == _ascii(lead.group(1)) + "0" and l.text.strip()[lead.end():lead.end() + 1] == ".":
                continue  # the zero it adds is the period printed after the number
            if not num.startswith("*") and _ascii(num) in _ascii(l.text):
                continue  # digits the line reader already read inside the line (a citation, a year)
            if lead is not None and not (num != lead.group(1) and _ascii(num).startswith(_ascii(lead.group(1)))
                                         and len(num) > len(lead.group(1))):
                continue
            if not fits(p, l, num) or (not num.startswith("*") and _ascii(num) in used[p.index]):
                continue
            used[p.index].add(_ascii(num))
            found.setdefault(str(p.index), {})[str(l.row)] = num
    (ocr_dir / "notenum.json").write_text(json.dumps({"version": source.NOTENUM_VERSION, "model": str(model_path), "pages": found},
                                                     ensure_ascii=False), encoding="utf-8")
    kept = sum(len(v) for v in found.values())
    log(f"parisaocr: note numbers re-read from the image: {kept} of {len(jobs)} candidate lines")
    return kept
