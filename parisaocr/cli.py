"""Command line: `parisaocr ocr` and `parisaocr cut`."""
import argparse
import os
import pathlib
import shlex
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from . import __version__, output
from .detect import detect_pages
from .detectors import make_detector
from .order import order
from .pages import collect
from .pipeline import Options, recognize_batch
from .reader import TesseractReader

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUNDLED_MODEL = pathlib.Path(__file__).resolve().parent / "models" / "parisaocr-fa-0.1.safetensors"
DEFAULT_TESSDATA = os.environ.get("PARISAOCR_TESSDATA", str(ROOT / "dist/tessdata_contrib/fas_print/best"))
DEFAULT_LANG = os.environ.get("PARISAOCR_LANG", "fas_print")
SMALL_TEXT_PX = 14  # median line height (original page pixels) below which accuracy drops clearly


def engine_args(p, default_model="default"):
    g = p.add_argument_group("engine")
    g.add_argument("--model", default=default_model,
                   help="recognition model: default (the ParisaOCR model shipped with the package), "
                        "kraken:MODEL.safetensors (another Kraken model), or TESSDATA_DIR:LANG[:EXTRA ARGS] for Tesseract "
                        f"(the cut command defaults to the fas_print Tesseract model, {DEFAULT_TESSDATA}:{DEFAULT_LANG})")
    g.add_argument("--detector", default="ppocr:v6-small:1.3",
                   help="line detector: ppocr[:VERSION-SIZE[:UNCLIP]] (PaddleOCR weights via RapidOCR on ONNX Runtime, "
                        "Apache-2.0; default ppocr:v6-small:1.3), surya (needs the surya extra; its weights are free "
                        "only for research, personal use and small companies), kraken (Kraken's default segmenter)")
    g.add_argument("--min-width", type=int, default=1600, help="upscale narrower pages to this width before detection")
    g.add_argument("--max-width", type=int, default=2000, help="reduce wider pages to this width for detection only (0 = never)")
    g.add_argument("--batch", type=int, default=8, help="pages per detector batch")
    g.add_argument("--cpu", action="store_true", help="run the detector on the CPU")
    g.add_argument("--pad", type=int, default=6, help="least margin around a line crop, in working pixels (vertical: half)")
    g.add_argument("--pad-frac", type=float, default=0.4,
                   help="margin as a fraction of the page's median line height; the larger of --pad and this is used")
    g.add_argument("--min-height", type=int, default=40, help="enlarge crops shorter than this many pixels")
    g.add_argument("--no-block-tall", dest="block_tall", action="store_false", help="read tall boxes in raw-line mode too")
    g.add_argument("--no-fallback", dest="fallback", action="store_false", help="no single-line retry of near-empty results")
    g.add_argument("--jobs", type=int, default=os.cpu_count(), help="parallel Tesseract processes")
    g.add_argument("--redo", action="store_true", help="ignore cached detection and existing output")


def build_engine(opts):
    if opts.model == "default" or opts.model.startswith("kraken:"):
        import torch
        from .kraken_reader import KrakenReader
        path = BUNDLED_MODEL if opts.model == "default" else opts.model.split(":", 1)[1]
        reader = KrakenReader(path, "cuda:0" if torch.cuda.is_available() and not opts.cpu else "cpu")
        fallback, short_conf = False, 80.0  # the single-line retry is a Tesseract remedy
    else:
        tessdata, lang, *extra = opts.model.split(":", 2)
        reader = TesseractReader(tessdata, lang, shlex.split(extra[0]) if extra else [])
        fallback, short_conf = opts.fallback, 0.0
    detector = make_detector(opts.detector, opts.min_width, opts.max_width, opts.batch, opts.cpu)
    options = Options(opts.pad, opts.pad_frac, opts.min_height, opts.block_tall, fallback, opts.jobs, short_conf)
    return detector, reader, options


def small_text_warning(rec):
    """A note on stderr when a page's text is so small that accuracy drops (median line height in page pixels)."""
    heights = sorted((l["bbox"][3] - l["bbox"][1]) / rec.get("scale", 1.0) for l in rec["lines"])
    if heights and heights[len(heights) // 2] < SMALL_TEXT_PX:
        print(f"parisaocr: {rec['page']}: text lines are only ~{heights[len(heights) // 2]:.0f} px high; "
              "accuracy drops on such small text (a 300 dpi scan of the page helps)", file=sys.stderr, flush=True)


def cmd_ocr(opts):
    if opts.out is None:  # no output directory: read into a temporary one and print the text
        with tempfile.TemporaryDirectory(prefix="parisaocr-out-") as tmp:
            opts.out, opts.format, opts.quiet = tmp, "txt", True
            pages = cmd_ocr(opts)
            for i, (pid, _) in enumerate(pages):
                if len(pages) > 1:
                    print(("\n" if i else "") + f"# {pid}")
                sys.stdout.write((pathlib.Path(tmp) / "txt" / f"{pid}.txt").read_text(encoding="utf-8"))
        return
    log = (lambda *a, **k: None) if getattr(opts, "quiet", False) else (lambda *a, **k: print(*a, **k, flush=True))
    out = pathlib.Path(opts.out)
    formats = [f.strip() for f in opts.format.split(",") if f.strip()]
    bad = set(formats) - {"txt", "hocr", "jsonl", "pdf"}
    if bad:
        sys.exit(f"parisaocr: unknown format {', '.join(bad)}")
    pages = collect(opts.input, out, opts.pdf, opts.dpi, opts.first, opts.last, opts.redo)
    if not pages:
        sys.exit("parisaocr: no pages to read")
    want_pdf = "pdf" in formats
    formats = [f for f in formats if f != "pdf"]
    if want_pdf and "jsonl" not in formats:
        formats.append("jsonl")  # the searchable PDF is built from the per-page lines and boxes
    for f in formats:
        (out / f).mkdir(parents=True, exist_ok=True)
    todo = [(pid, p) for pid, p in pages if opts.redo or not all((out / f / f"{pid}.{f}").exists() for f in formats)]
    log(f"{len(pages)} pages, {len(todo)} to read -> {out}/{{{','.join(formats + ['pdf'] * want_pdf)}}}")
    if not todo:
        if want_pdf:
            write_pdfs(opts, out, pages, log)
        return pages
    detector, reader, options = build_engine(opts)
    started, n_lines, done = time.time(), 0, 0

    def finish(records, lines_of):
        n = 0
        for rec in records:
            pid = rec["page"]
            small_text_warning(rec)
            cols = order(lines_of[pid], opts.order)
            n += sum(len(c) for c in cols)
            if "txt" in formats:
                (out / "txt" / f"{pid}.txt").write_text(output.text(cols), encoding="utf-8")
            if "hocr" in formats:
                (out / "hocr" / f"{pid}.hocr").write_text(
                    output.hocr(pid, pathlib.Path(rec["image"]).name, rec.get("orig_width", rec["width"]),
                                rec.get("orig_height", rec["height"]), cols, reader.describe()), encoding="utf-8")
            if "jsonl" in formats:
                (out / "jsonl" / f"{pid}.jsonl").write_text(output.jsonl(pid, cols), encoding="utf-8")
        return n

    with tempfile.TemporaryDirectory(prefix="parisaocr-") as tmp, ThreadPoolExecutor(1) as cpu:
        tmp = pathlib.Path(tmp)
        pending = None
        # Detection (GPU) of one batch overlaps with recognition (CPU) of the previous one.
        for records in detect_pages(detector, todo, out / "det", opts.redo):
            if pending:
                n_lines += pending.result()
                done += opts.batch
                log(f"  {min(done, len(todo))}/{len(todo)} pages, {n_lines} lines, {time.time() - started:.0f} s")
            pending = cpu.submit(lambda recs=records: finish(recs, recognize_batch(recs, reader, options, tmp)))
        if pending:
            n_lines += pending.result()
    log(f"{len(todo)} pages, {n_lines} lines in {time.time() - started:.0f} s")
    if want_pdf:
        write_pdfs(opts, out, pages, log)
    return pages


def write_pdfs(opts, out, pages, log):
    """Searchable PDFs in OUT/pdf: the input PDF with a text layer, and/or the image inputs as PDFs."""
    from PIL import Image
    from . import pdfout
    (out / "pdf").mkdir(exist_ok=True)
    pdf_inputs = [pathlib.Path(p) for p in opts.input if pathlib.Path(p).suffix.lower() == ".pdf"]
    pages_dir = (out / "pages").resolve()
    from_pdf = [(pid, p) for pid, p in pages if pathlib.Path(p).resolve().parent == pages_dir]
    images = [(pid, p) for pid, p in pages if (pid, p) not in from_pdf]

    def lines_of(pid):
        f = out / "jsonl" / f"{pid}.jsonl"
        return pdfout.read_jsonl(f) if f.exists() else []

    if pdf_inputs and from_pdf:
        src = pdf_inputs[0]
        layers = {}
        for pid, image in from_pdf:
            with Image.open(image) as im:
                layers[int(pid.split("-")[1])] = (im.width, im.height, lines_of(pid))
        dst = out / "pdf" / src.name
        done = pdfout.overlay_pdf(src, layers, dst, keep_text=opts.pdf_text == "skip")
        skipped = len(layers) - len(done)
        log(f"searchable PDF -> {dst} (text layer on {len(done)} pages"
            + (f"; {skipped} already had text and were left as they are (--pdf-text add to add ours)" if skipped else "")
            + ")")
    if images:
        if opts.merge_pdf:
            dst = out / "pdf" / (opts.merge_pdf if opts.merge_pdf.lower().endswith(".pdf") else opts.merge_pdf + ".pdf")
            pdfout.images_to_pdf([(p, lines_of(pid)) for pid, p in images], dst)
            log(f"searchable PDF -> {dst} ({len(images)} pages)")
        else:
            for pid, p in images:
                pdfout.images_to_pdf([(p, lines_of(pid))], out / "pdf" / f"{pid}.pdf")
            log(f"searchable PDFs -> {out / 'pdf'} ({len(images)} files)")


def cmd_pages(opts):
    """Only turn a PDF into page images (OUT/p-NNN.png), e.g. to prepare a book for `parisaocr cut`."""
    from .pages import pdf_pages
    out = pathlib.Path(opts.out)
    got = pdf_pages(pathlib.Path(opts.pdf), out, opts.pdf_mode, opts.dpi, opts.first, opts.last, opts.redo)
    print(f"{len(got)} pages in {out}")


def cmd_cut(opts):
    from .cut import cut
    detector, reader, options = build_engine(opts)
    with tempfile.TemporaryDirectory(prefix="parisaocr-") as tmp:
        cut(opts.book_dir, opts.out or pathlib.Path(opts.book_dir) / "lines", detector, reader, options,
            test=opts.test, train=opts.train, exclude=opts.exclude, seed=opts.seed, min_lines=opts.min_lines,
            min_page_conf=opts.min_page_conf, min_conf=opts.min_conf, pad=opts.crop_pad, redo=opts.redo, tmp=pathlib.Path(tmp),
            test_pages=opts.test_pages, train_pages=opts.train_pages)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="parisaocr", description=__doc__)
    ap.add_argument("--version", action="version", version=f"parisaocr {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("ocr", help="read pages (images, a directory of images, or one PDF)",
                       description="Writes OUT/txt/PAGE.txt, OUT/hocr/PAGE.hocr and/or OUT/jsonl/PAGE.jsonl; "
                                   "PDF pages are rendered to OUT/pages first; detection is cached in OUT/det.")
    o.add_argument("input", nargs="+")
    o.add_argument("--out", help="output directory; without it the text is printed")
    o.add_argument("--format", default="txt,hocr",
                   help="comma-separated: txt, hocr, jsonl, pdf (with --out). pdf: searchable PDF, the input PDF with "
                        "an invisible text layer, or one PDF per input image (see --merge-pdf)")
    o.add_argument("--merge-pdf", metavar="NAME", help="with --format pdf: put all input images into one PDF, OUT/pdf/NAME.pdf")
    o.add_argument("--pdf-text", choices=["skip", "add"], default="skip",
                   help="with --format pdf and a PDF input: leave pages that already have a text layer alone (skip), "
                        "or add ours to them too (add; e.g. over a poor earlier OCR layer)")
    o.add_argument("--order", choices=["rtl", "raster"], default="rtl", help="line order: right-to-left columns, or top to bottom")
    o.add_argument("--pdf", choices=["auto", "extract", "render"], default="auto",
                   help="PDF pages: extract the embedded scan images, render at --dpi, or decide per file")
    o.add_argument("--dpi", type=int, default=300)
    o.add_argument("--first", type=int, help="first PDF page")
    o.add_argument("--last", type=int, help="last PDF page")
    engine_args(o)
    o.set_defaults(func=cmd_ocr)

    p = sub.add_parser("pages", help="extract or render a PDF's pages to OUT/p-NNN.png (no OCR)")
    p.add_argument("pdf")
    p.add_argument("--out", required=True)
    p.add_argument("--pdf-mode", choices=["auto", "extract", "render"], default="auto")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--first", type=int)
    p.add_argument("--last", type=int)
    p.add_argument("--redo", action="store_true")
    p.set_defaults(func=cmd_pages)

    c = sub.add_parser("cut", help="cut the lines of a book (BOOK_DIR/p-NNN.png) for labelling",
                       description="Writes LINES/test|train/p-NNN_LL.png crops and LINES/manifest.json (default LINES = BOOK_DIR/lines).")
    c.add_argument("book_dir")
    c.add_argument("--out", help="lines directory (default BOOK_DIR/lines)")
    c.add_argument("--test", type=int, default=12, help="test pages")
    c.add_argument("--train", type=int, default=24, help="train pages")
    c.add_argument("--exclude", default="", help="page ranges to leave out, e.g. 1-11,93-97")
    c.add_argument("--test-pages", default="", help="use exactly these pages as test, e.g. 30,45 (no random choice, no filters)")
    c.add_argument("--train-pages", default="", help="use exactly these pages as train, e.g. 18,50-52")
    c.add_argument("--seed", type=int, default=0)
    c.add_argument("--min-lines", type=int, default=15, help="pages with fewer detected lines are not candidates")
    c.add_argument("--min-page-conf", type=float, default=80, help="skip pages whose mean word confidence is lower (0 = keep all)")
    c.add_argument("--min-conf", type=float, default=60, help="draft words below this confidence are listed for checking")
    c.add_argument("--crop-pad", type=int, default=10, help="margin of the saved line crops, in page pixels (vertical: half)")
    engine_args(c, default_model=f"{DEFAULT_TESSDATA}:{DEFAULT_LANG}")  # page filters are calibrated on Tesseract confidences
    c.set_defaults(func=cmd_cut)

    opts = ap.parse_args(argv)
    opts.func(opts)


if __name__ == "__main__":
    main()
