"""Turn the inputs of a run (image files, directories, a PDF) into a list of (page id, image path).

PDF pages become PNG files under OUT/pages/p-NNN.png. A scanned PDF (one big
image per page) is extracted losslessly with pdfimages; a born-digital PDF is
rendered with pdftoppm at --dpi. Mode "auto" picks by counting the pages that
carry an image at least 600 px wide.
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
    if mode == "extract":
        run_tool(["pdfimages", "-png", "-p", "-f", str(first), "-l", str(last), str(pdf), str(tmp / "img")],
                       stderr=subprocess.DEVNULL)
        kind = {(r["page"], r["num"]): r["type"] for r in rows} if rows else None
        by_page = collections.defaultdict(list)
        for f in tmp.glob("img-*.png"):
            m = re.match(r"img-(\d+)-(\d+)\.png", f.name)
            by_page[int(m.group(1))].append((int(m.group(2)), f))
        for page, files in by_page.items():
            if len(files) > 1 and kind is None:
                kind = {(r["page"], r["num"]): r["type"] for r in pdf_image_list(pdf)}
            # The page image, not a mask or a small decoration: prefer type "image", then the largest.
            files.sort(key=lambda nf: ((kind or {}).get((page, nf[0])) == "image", area(nf[1])), reverse=True)
            files[0][1].replace(pages_dir / f"p-{page:03d}.png")
            done.add(page)
        # The raw image is not always the page as it is shown: some scans store it mirrored or
        # turned and flip it with the page's transform, others store the page as strips or put a
        # paper texture under vector text. Each page is compared with a small render; a flipped
        # image is turned back, one that does not match the page is replaced by a render.
        thumbs = thumbnails(pdf, sorted(done), tmp)
        fixed, rendered = collections.Counter(), 0
        for page in sorted(done):
            path = pages_dir / f"p-{page:03d}.png"
            how = orientation(path, thumbs.get(page))
            if how is None:
                done.discard(page)
                rendered += 1
            elif how != "as is":
                with Image.open(path) as im:
                    im.transpose(TURNS[how]).save(path)
                fixed[how] += 1
        if fixed or rendered:
            print(f"{pdf.name}: {dict(fixed) or ''} {f'{rendered} pages do not match their image, rendered' if rendered else ''}".strip(), flush=True)
    missing = [p for p in wanted if p not in done]
    if missing:  # render mode, or scan pages without an embedded image
        for start, stop in runs(missing):
            run_tool(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(start), "-l", str(stop), str(pdf), str(tmp / "r")],
                           stderr=subprocess.DEVNULL)
        for f in tmp.glob("r-*.png"):
            page = int(f.stem.split("-")[-1])
            f.replace(pages_dir / f"p-{page:03d}.png")
            done.add(page)
    shutil.rmtree(tmp, ignore_errors=True)
    lost = [p for p in wanted if p not in done]
    if lost:
        print(f"{pdf.name}: no image for pages {lost[:10]}{'...' if len(lost) > 10 else ''}", file=sys.stderr)
    print(f"{pdf.name}: {len(done)} pages -> {pages_dir}", flush=True)
    return [(f"p-{p:03d}", pages_dir / f"p-{p:03d}.png") for p in wanted if p in done]


def area(path):
    with Image.open(path) as im:
        return im.width * im.height


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
