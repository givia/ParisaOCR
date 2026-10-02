"""Turn the inputs of a run (image files, directories, a PDF) into a list of (page id, image path).

PDF pages become PNG files under OUT/pages/p-NNN.png. A scanned PDF (one big
image per page) is extracted losslessly with pdfimages; a born-digital PDF is
rendered with pdftoppm at --dpi. Mode "auto" picks by counting the pages that
carry an image at least 600 px wide. An extracted image that does not look like
the page (set in margins, in strips, or under a text stamp) is drawn where the
page places it before the page is rendered instead.
"""
import collections
import pathlib
import re
import resource
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

# Poppler's tools have no memory bound of their own: one malformed page made pdfimages grow to
# 22 GB and the kernel's OOM killer took the whole desktop session with it. Each call gets an
# address-space limit instead, so a pathological page fails alone (and is rendered or lost).
TOOL_MEMORY = 6 << 30


def run_tool(cmd, **kwargs):
    def limit():
        resource.setrlimit(resource.RLIMIT_AS, (TOOL_MEMORY, TOOL_MEMORY))
    return subprocess.run(cmd, preexec_fn=limit, **kwargs)


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".webp", ".bmp", ".pnm", ".ppm", ".pgm"}


def collect(inputs, out, pdf_mode="auto", dpi=300, first=None, last=None, redo=False):
    pages = []
    if sum(pathlib.Path(p).suffix.lower() == ".pdf" for p in inputs) > 1:
        sys.exit("parisaocr: one PDF per run (its pages are named p-NNN)")
    for p in inputs:
        p = pathlib.Path(p)
        if p.is_dir():
            pages += [(f.stem, f) for f in sorted(p.iterdir()) if f.suffix.lower() in IMAGE_SUFFIXES]
        elif p.suffix.lower() == ".pdf":
            pages += pdf_pages(p, pathlib.Path(out) / "pages", pdf_mode, dpi, first, last, redo)
        elif p.suffix.lower() in IMAGE_SUFFIXES:
            pages.append((p.stem, p))
        else:
            print(f"parisaocr: skipping {p} (not an image, directory or PDF)", file=sys.stderr)
    seen = set()
    for pid, _ in pages:
        if pid in seen:
            sys.exit(f"parisaocr: two pages would be called {pid!r}; rename the inputs")
        seen.add(pid)
    return pages


def pdf_page_count(pdf):
    info = run_tool(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", info, re.M)
    if not m:
        sys.exit(f"parisaocr: pdfinfo cannot read {pdf}")
    return int(m.group(1))


def pdf_image_list(pdf):
    """Rows of `pdfimages -list`: page, image number, type, width, height."""
    out = run_tool(["pdfimages", "-list", str(pdf)], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0].isdigit() and f[1].isdigit():
            rows.append({"page": int(f[0]), "num": int(f[1]), "type": f[2], "width": int(f[3]), "height": int(f[4])})
    return rows


def pdf_pages(pdf, pages_dir, mode, dpi, first, last, redo):
    n = pdf_page_count(pdf)
    first, last = first or 1, min(last or n, n)
    wanted = list(range(first, last + 1))
    pages_dir.mkdir(parents=True, exist_ok=True)
    have = {p: pages_dir / f"p-{p:03d}.png" for p in wanted if (pages_dir / f"p-{p:03d}.png").exists()}
    if len(have) == len(wanted) and not redo:
        print(f"{pdf.name}: {len(wanted)} pages already in {pages_dir}", flush=True)
        return [(f"p-{p:03d}", have[p]) for p in wanted]
    # `pdfimages -list` walks the whole file (a minute on a 700-page scan), so it is only run
    # when the mode has to be decided or a page turns out to hold several images.
    rows = pdf_image_list(pdf) if mode == "auto" else None
    if mode == "auto":
        with_image = {r["page"] for r in rows if r["type"] == "image" and r["width"] >= 600}
        mode = "extract" if len(with_image & set(wanted)) >= 0.9 * len(wanted) else "render"
        print(f"{pdf.name}: {n} pages, {len(with_image)} carry a page-sized image -> {mode}", flush=True)
    tmp = pages_dir / ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    done = set()
    doc, text = None, {}  # the PDF opened with pikepdf, and what its pages draw as text (`pdf_text_stamps`)
    if mode == "extract":
        run_tool(["pdfimages", "-png", "-p", "-f", str(first), "-l", str(last), str(pdf), str(tmp / "img")],
                       stderr=subprocess.DEVNULL)
        kind = {(r["page"], r["num"]): r["type"] for r in rows} if rows else None
        by_page = collections.defaultdict(list)
        for f in tmp.glob("img-*.png"):
            m = re.match(r"img-(\d+)-(\d+)\.png", f.name)
            by_page[int(m.group(1))].append((int(m.group(2)), f))
        extracted = {}  # page -> [(image number, file)] in drawing order, for `compose`
        for page, files in by_page.items():
            if len(files) > 1 and kind is None:
                kind = {(r["page"], r["num"]): r["type"] for r in pdf_image_list(pdf)}
            # The page image, not a mask or a small decoration: prefer type "image", then the largest.
            files.sort(key=lambda nf: ((kind or {}).get((page, nf[0])) == "image", area(nf[1])), reverse=True)
            best = pages_dir / f"p-{page:03d}.png"
            files[0][1].replace(best)
            extracted[page] = sorted([(files[0][0], best)] + files[1:])
            done.add(page)
        # The raw image is not always the page as it is shown: some scans store it mirrored or
        # turned and flip it with the page's transform, others store the page as strips or put a
        # paper texture under vector text. Each page is compared with a small render; a flipped
        # image is turned back. One that does not match the page is drawn from its images where
        # the page places them (a scan set in margins, strips, a stencil over a background: the
        # lossless picture without the text objects drawn on top of it, such as a site's stamp),
        # and rendered only when that does not match the page either.
        thumbs = thumbnails(pdf, sorted(done), tmp)
        fixed, composed, rendered = collections.Counter(), 0, 0
        for page in sorted(done):
            path = pages_dir / f"p-{page:03d}.png"
            how = orientation(path, thumbs.get(page))
            if how is None:
                if doc is None:
                    doc = open_pdf(pdf)
                    text = pdf_text_stamps(doc) if doc else {}
                # A page whose only visible text is a stamp may differ from its drawing by that much;
                # one with other text (a title set in type over a picture) must match it closely.
                strict = text.get(page, {}).get("other", True)
                drawn = compose(doc, page, extracted[page], kind, thumbs.get(page), strict) if doc else None
                if drawn is not None:
                    drawn.save(path)
                    composed += 1
                else:
                    done.discard(page)
                    rendered += 1
            elif how != "as is":
                with Image.open(path) as im:
                    im.transpose(TURNS[how]).save(path)
                fixed[how] += 1
        if fixed or composed or rendered:
            parts = [str(dict(fixed)) if fixed else "",
                     f"{composed} pages drawn from their images as the page places them" if composed else "",
                     f"{rendered} pages do not match their image, rendered" if rendered else ""]
            print(f"{pdf.name}: " + "; ".join(p for p in parts if p), flush=True)
    missing = [p for p in wanted if p not in done]
    if missing:  # render mode, or scan pages without an embedded image
        source = pdf
        if mode == "extract":  # rendered without the text stamps the scan pages carry
            doc = doc or open_pdf(pdf)
            text = text or (pdf_text_stamps(doc) if doc else {})
            if any(text.get(p, {}).get("stamps") for p in missing):
                for p in missing:
                    if text.get(p, {}).get("stamps"):
                        strip_text(doc, doc.pages[p - 1], only=set(text[p]["stamps"]))
                source = tmp / "stripped.pdf"
                doc.save(str(source))
        for start, stop in runs(missing):
            run_tool(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(start), "-l", str(stop), str(source), str(tmp / "r")],
                           stderr=subprocess.DEVNULL)
        for f in tmp.glob("r-*.png"):
            page = int(f.stem.split("-")[-1])
            f.replace(pages_dir / f"p-{page:03d}.png")
            done.add(page)
    if doc is not None:
        doc.close()
    shutil.rmtree(tmp, ignore_errors=True)
    stamps = collections.Counter(s for t in text.values() for s in t["stamps"])
    for s, n in stamps.most_common():
        print(f"{pdf.name}: text stamp {stamp_label(s)} on {n} pages, left out of the page images", flush=True)
    lost = [p for p in wanted if p not in done]
    if lost:
        print(f"{pdf.name}: no image for pages {lost[:10]}{'...' if len(lost) > 10 else ''}", file=sys.stderr)
    print(f"{pdf.name}: {len(done)} pages -> {pages_dir}", flush=True)
    return [(f"p-{p:03d}", pages_dir / f"p-{p:03d}.png") for p in wanted if p in done]


def area(path):
    with Image.open(path) as im:
        return im.width * im.height


# ---- a page drawn from its images --------------------------------------------------------------

def open_pdf(pdf):
    """The PDF opened for reading its pages' content, or None when it cannot be."""
    try:
        import pikepdf
        return pikepdf.open(str(pdf))
    except Exception:
        return None


def _mul(a, b):
    """PDF matrix A followed by B, both as [a b c d e f]."""
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]


def _inherited(obj, key, default):
    """A page attribute that may come from an ancestor in the page tree (/Rotate, /MediaBox)."""
    node = obj
    for _ in range(64):
        if node is None:
            break
        if key in node:
            return node[key]
        node = node.get("/Parent")
    return default


def placements(stream, resources=None, ctm=None, depth=0):
    """The images a page (or form XObject) STREAM draws, in drawing order: {width, height, ctm (image
    unit square -> page user space), mask (a stencil mask), fill (the grey a stencil is painted with)}."""
    import pikepdf
    resources = stream.get("/Resources") if resources is None else resources
    ctm = ctm or [1, 0, 0, 1, 0, 0]
    stack, fill, out = [], 0, []
    for operands, op in pikepdf.parse_content_stream(stream):
        op = str(op)
        if op == "q":
            stack.append((ctm, fill))
        elif op == "Q":
            if stack:
                ctm, fill = stack.pop()
        elif op == "cm" and len(operands) == 6:
            ctm = _mul([float(v) for v in operands], ctm)
        elif op == "g" and len(operands) == 1:
            fill = round(255 * float(operands[0]))
        elif op == "rg" and len(operands) == 3:
            r, g, b = (float(v) for v in operands)
            fill = round(255 * (0.3 * r + 0.59 * g + 0.11 * b))
        elif op == "k" and len(operands) == 4:
            c, m, y, k = (float(v) for v in operands)
            fill = round(255 * max(0.0, 1 - min(1.0, 0.3 * c + 0.59 * m + 0.11 * y + k)))
        elif op == "Do" and operands and resources is not None:
            xobjects = resources.get("/XObject")
            xo = xobjects.get(str(operands[0])) if xobjects is not None else None
            if xo is None:
                continue
            if xo.get("/Subtype") == "/Image":
                out.append(dict(width=int(xo.Width), height=int(xo.Height), ctm=ctm,
                                mask=bool(xo.get("/ImageMask", False)), fill=fill))
            elif xo.get("/Subtype") == "/Form" and depth < 3:
                matrix = [float(v) for v in xo.get("/Matrix", [1, 0, 0, 1, 0, 0])]
                out += placements(xo, xo.get("/Resources", resources), _mul(matrix, ctm), depth + 1)
    return out


def draw_page(files, places, box, rotate, invert_masks=False):
    """The page as a viewer shows it, drawn on white from its extracted images (FILES, one per entry
    of PLACES, in drawing order), at the resolution of its largest image: the scan where the page
    puts it, without the text and vector objects. A stencil mask is painted in its fill grey where
    its extracted file is white, or where it is black with INVERT_MASKS (`compose` keeps the polarity
    that matches the page)."""
    x0, y0, x1, y1 = box
    images = [Image.open(f) for f in files]
    scale = max((im.width / max(1e-6, (p["ctm"][0] ** 2 + p["ctm"][1] ** 2) ** 0.5)
                 for im, p in zip(images, places)), default=300 / 72)
    scale = min(max(scale, 100 / 72), 1200 / 72)  # pixels per point
    size = (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale)))
    colour = any(im.mode not in ("1", "L") and not p["mask"] for im, p in zip(images, places))
    mode = "RGB" if colour else "L"
    canvas = Image.new(mode, size, "white")
    for im, p in zip(images, places):
        a, b, c, d, e, f = p["ctm"]
        w, h = im.size
        # image pixel (col, row) -> unit square (col/w, 1 - row/h) -> user space -> canvas pixel
        forward = np.array([[scale * a / w, -scale * c / h, scale * (c + e - x0)],
                            [-scale * b / w, scale * d / h, scale * (y1 - d - f)],
                            [0, 0, 1]])
        try:
            inverse = np.linalg.inv(forward)
        except np.linalg.LinAlgError:
            continue
        data = tuple(float(v) for v in inverse[:2].ravel())
        grey = im.convert("L")
        if p["mask"]:
            painted = grey.point(lambda v: 255 - v) if invert_masks else grey
            layer = Image.new(mode, size, (p["fill"],) * (3 if colour else 1))
            mask = painted.transform(size, Image.AFFINE, data, resample=Image.NEAREST, fillcolor=0)
        else:
            source = grey if im.mode == "1" else im.convert(mode)
            layer = source.convert(mode).transform(size, Image.AFFINE, data, resample=Image.BICUBIC, fillcolor="white")
            mask = Image.new("L", im.size, 255).transform(size, Image.AFFINE, data, resample=Image.NEAREST, fillcolor=0)
        canvas.paste(layer, (0, 0), mask)
        im.close()
    turn = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}.get(rotate % 360)
    return canvas.transpose(turn) if turn else canvas


def compose(doc, number, files, kind, thumb, strict=True):
    """Page NUMBER of the open PDF DOC drawn from its extracted images (FILES: [(image number, path)];
    KIND: {(page, image number): pdfimages type} or None) when the drawing looks like the rendered
    page THUMB (see `fit` for STRICT); else None."""
    try:
        page = doc.pages[number - 1]
        places = placements(page.obj)
        drawn = [f for n, f in files if kind is None or kind.get((number, n)) in ("image", "stencil")]
        if not places or len(drawn) != len(places):
            # Match the files to the drawn images by size (inline images, unused XObjects...).
            sizes = {}
            for f in drawn:
                with Image.open(f) as im:
                    sizes.setdefault(im.size, []).append(f)
            drawn = [sizes[(p["width"], p["height"])].pop(0) for p in places if sizes.get((p["width"], p["height"]))]
            if not drawn or len(drawn) != len(places):
                return None
        box = [float(v) for v in _inherited(page.obj, "/MediaBox", [0, 0, 612, 792])]
        rotate = int(_inherited(page.obj, "/Rotate", 0))
        best, best_fit = None, (False, 0.0)
        for invert in ([False, True] if any(p["mask"] for p in places) else [False]):
            image = draw_page(drawn, places, box, rotate, invert_masks=invert)
            score = fit(image, thumb, strict)
            if score > best_fit:
                best, best_fit = image, score
        return best if best_fit[0] else None
    except Exception:
        return None


def fit(image, thumb, strict=True, match=0.5, lost=0.005, extra=0.015):
    """(whether IMAGE passes for the rendered page THUMB, their Pearson correlation). It passes when
    the correlation reaches MATCH or, unless STRICT, on a page too empty for a correlation to mean
    much (a motto, a half-title), when nearly all its ink is in the render (at most LOST of the page)
    and the render adds little (at most EXTRA of the page: a stamp)."""
    if thumb is None:
        return True, 1.0
    g = image.convert("L")
    g.thumbnail((4 * thumb.width, 4 * thumb.width))
    if abs(g.width / g.height / (thumb.width / thumb.height) - 1) > 0.15:
        return False, 0.0
    g = g.resize(thumb.size, Image.BILINEAR)
    r = similarity(g, thumb)
    if r >= match or strict:
        return r >= match, r
    ink_g, ink_t = np.asarray(g) < 200, np.asarray(thumb) < 200
    return (ink_g & ~ink_t).mean() <= lost and (ink_t & ~ink_g).mean() <= extra, r


# ---- text drawn on the pages: stamps -------------------------------------------------------------

TEXT_OPS = {"Tj": 0, "'": 0, '"': 2, "TJ": 0}  # text-showing operators and which operand is the text


def _shown(operands, op):
    """What a text-showing operator draws, as a latin-1 string: a key, readable for a simple font."""
    import pikepdf
    o = operands[TEXT_OPS[op]] if len(operands) > TEXT_OPS[op] else None
    if isinstance(o, pikepdf.Array):
        return "".join(bytes(x).decode("latin-1") for x in o if isinstance(x, pikepdf.String))
    return bytes(o).decode("latin-1") if isinstance(o, pikepdf.String) else ""


def stamp_label(s):
    return repr(s) if s.isascii() and s.isprintable() else f"<{len(s)} bytes>"


def page_text(page, box):
    """What a page shows as text: {"scan": it draws a page-sized image (600+ px wide), "over": the
    strings drawn on top of that image, "before": those drawn before it (or without one) that no
    image covers}. Invisible text (render mode 3, 7) does not count."""
    import pikepdf
    area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
    xobjects = (page.obj.get("/Resources") or {}).get("/XObject") or {}
    ctm, mode, stack = [1, 0, 0, 1, 0, 0], 0, []
    scan, before, over = False, [], []
    for operands, op in pikepdf.parse_content_stream(page):
        op = str(op)
        if op == "q":
            stack.append((ctm, mode))
        elif op == "Q":
            if stack:
                ctm, mode = stack.pop()
        elif op == "cm" and len(operands) == 6:
            ctm = _mul([float(v) for v in operands], ctm)
        elif op == "Tr" and operands:
            mode = int(operands[0])
        elif op == "Do" and operands:
            xo = xobjects.get(str(operands[0]))
            if xo is not None and xo.get("/Subtype") == "/Image":
                if int(xo.get("/Width", 0)) >= 600:
                    scan = True
                a, b, c, d = ctm[:4]
                if abs(a * d - b * c) >= 0.95 * area:
                    before = []  # hidden under it
        elif op in TEXT_OPS and mode not in (3, 7):
            s = _shown(operands, op)
            if s.strip():
                (over if scan else before).append(s)
    return {"scan": scan, "over": over, "before": before}


def pdf_text_stamps(doc, min_pages=3, share=0.3):
    """The text stamps of a scanned PDF (a site's name drawn over the scan of every page): the strings
    drawn on top of the page image on at least SHARE of the scan pages that carry a few such strings,
    and on MIN_PAGES. {page number: `page_text` of the page + "stamps": [its stamp strings, wherever
    on the page], "other": it shows text besides its stamps}."""
    info = {}
    for n, page in enumerate(doc.pages, 1):
        try:
            box = [float(v) for v in _inherited(page.obj, "/MediaBox", [0, 0, 612, 792])]
            info[n] = page_text(page, box)
        except Exception:
            info[n] = {"scan": False, "over": [], "before": ["?"]}
    counts, scans = collections.Counter(), 0
    for t in info.values():
        if t["scan"] and 0 < len(t["over"]) <= 12:
            scans += 1
            counts.update(set(t["over"]))
    stamps = {s for s, c in counts.items() if c >= max(min_pages, share * scans) and len(s.strip()) >= 3}
    for t in info.values():
        shown = t["over"] + t["before"]
        t["stamps"] = sorted({s for s in shown if s in stamps})
        t["other"] = any(s not in stamps for s in shown)
    return info


def strip_text(doc, page, only=None):
    """Remove text objects (BT ... ET) from PAGE of the open DOC: all of them, or those that show one of
    the strings in ONLY. Returns how many were removed."""
    import pikepdf
    kept, block, drop, removed = [], None, False, 0
    for inst in pikepdf.parse_content_stream(page):
        operands, op = inst
        op = str(op)
        if op == "BT":
            block, drop = [], only is None
        if block is None:
            kept.append(inst)
            continue
        block.append(inst)
        if op in TEXT_OPS and only is not None and _shown(operands, op) in only:
            drop = True
        if op == "ET":
            if drop:
                removed += 1
            else:
                kept += block
            block = None
    if block:
        kept += block
    if removed:
        page.obj["/Contents"] = doc.make_stream(pikepdf.unparse_content_stream(kept))
    return removed


TURNS = {"mirrored": Image.Transpose.FLIP_LEFT_RIGHT, "upside down": Image.Transpose.FLIP_TOP_BOTTOM,
         "rotated 180": Image.Transpose.ROTATE_180, "rotated 90": Image.Transpose.ROTATE_90,
         "rotated 270": Image.Transpose.ROTATE_270, "transposed": Image.Transpose.TRANSPOSE,
         "transverse": Image.Transpose.TRANSVERSE}


def thumbnails(pdf, pages, tmp, size=256):
    """{page: small grey render} for the given pages: what a viewer shows."""
    for start, stop in runs(pages):
        run_tool(["pdftoppm", "-scale-to", str(size), "-gray", "-png", "-f", str(start), "-l", str(stop), str(pdf), str(tmp / "t")],
                       stderr=subprocess.DEVNULL)
    out = {}
    for f in tmp.glob("t-*.png"):
        with Image.open(f) as im:
            out[int(f.stem.split("-")[-1])] = im.convert("L")
        f.unlink()
    return out


def similarity(a, b):
    """Pearson correlation of two grey images of equal size (1 = the same picture)."""
    x = np.asarray(a, dtype=np.float32).ravel()
    y = np.asarray(b, dtype=np.float32).ravel()
    x, y = x - x.mean(), y - y.mean()
    d = float(np.sqrt((x * x).sum() * (y * y).sum()))
    return float((x * y).sum()) / d if d else 0.0


def orientation(path, thumb, match=0.5, margin=0.05):
    """How the extracted image must be turned to look like the rendered page ("as is", a key of
    TURNS), or None when no turn of it matches the page well (then the page is rendered)."""
    if thumb is None:
        return "as is"
    with Image.open(path) as im:
        g = im.convert("L")
        g.thumbnail((4 * thumb.width, 4 * thumb.width))
    aspect = thumb.width / thumb.height
    scores = {}
    for how, turn in (("as is", None), *TURNS.items()):
        t = g if turn is None else g.transpose(turn)
        if abs(t.width / t.height / aspect - 1) > 0.15:
            continue
        scores[how] = similarity(t.resize(thumb.size, Image.BILINEAR), thumb)
    if not scores:
        return None
    best = max(scores, key=scores.get)
    if scores[best] < match:
        return None
    if best != "as is" and scores[best] - scores.get("as is", -1) < margin:
        return "as is"
    return best


def runs(numbers):
    """Consecutive runs of a sorted list of ints as (start, stop) pairs."""
    out = []
    for n in numbers:
        if out and out[-1][1] == n - 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return [tuple(r) for r in out]
