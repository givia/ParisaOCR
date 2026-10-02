"""`parisaocr epub`: a scanned Persian book (PDF) to EPUB 3, with a QA report.

OCR (skipped when its output is already there) → page layout → page order →
a second look at the images (fractions, note markers, verse) → book structure
→ EPUB → QA report → epubcheck (when Java and its jar are available).
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

from . import epub, layout, order, refine, report, source, structure

EPUBCHECK = os.environ.get("EPUBCHECK_JAR", "")


def epubcheck(path):
    jar = pathlib.Path(EPUBCHECK) if EPUBCHECK else None
    if not (shutil.which("java") and jar and jar.exists()):
        return "not run (set EPUBCHECK_JAR to epubcheck.jar to check the EPUB)"
    out = subprocess.run(["java", "-jar", str(jar), str(path)], capture_output=True, text=True)
    summary = next((l for l in (out.stdout + out.stderr).splitlines() if l.startswith("Messages:")), "no summary")
    problems = [l for l in (out.stdout + out.stderr).splitlines() if l.startswith(("ERROR", "FATAL", "WARNING"))]
    return summary + "".join(f"\n  - {p}" for p in problems[:20])


def pdf_pages(pdf):
    from ..pages import pdf_page_count
    return pdf_page_count(pdf)


META_KEYS = ("title", "subtitle", "author", "publisher", "language", "isbn")


def convert(opts, read):
    """OPTS: the `parisaocr epub` arguments; READ(inputs, out_dir, formats): runs ParisaOCR with them."""
    t0 = time.time()
    pdf = pathlib.Path(opts.book).expanduser().resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        sys.exit(f"parisaocr: not a PDF file: {pdf}")
    if opts.meta and not pathlib.Path(opts.meta).expanduser().is_file():
        sys.exit(f"parisaocr: no such metadata file: {opts.meta} (it is optional; --title, --author etc. work too)")
    name = opts.name or pdf.stem
    out = pathlib.Path(opts.out).expanduser().resolve()
    work = pathlib.Path(opts.work).expanduser().resolve() if opts.work else out / f"{name}.work"
    ocr_dir = source.ocr_dir_for(work, opts.model)
    n = pdf_pages(pdf)
    done = len(list((ocr_dir / "jsonl").glob("p-*.jsonl"))) if (ocr_dir / "jsonl").exists() else 0
    reused = done >= n and not opts.redo
    if not reused:
        read([str(pdf)], ocr_dir, "txt,hocr,jsonl")

    print("parisaocr: page layout", flush=True)
    ps = source.load(ocr_dir)
    latin = sum(l.latin for p in ps for l in p.lines)
    info = {}
    layouts = layout.analyze_all(ps, roles_dir=opts.roles, report=info)
    layout.orient_figures(layouts, ocr_dir, read)
    markers = sum(refine.refine(L) for L in layouts)
    ordered = order.order(layouts)
    print("parisaocr: book structure", flush=True)
    book = structure.build(ordered)

    meta = json.loads(pathlib.Path(opts.meta).expanduser().read_text(encoding="utf-8")) if opts.meta else {}
    for k in META_KEYS:
        if getattr(opts, k, None):
            meta[k] = getattr(opts, k)
    if not meta.get("title"):
        front = next((b.rows[0] for ch in book.chapters if ch.kind == "front" for b in ch.blocks if b.kind == "lines" and b.rows), None)
        meta["title"] = front or pdf.stem
    path = epub.write(book, out / f"{name}.epub", meta, tables=opts.tables)
    print(f"parisaocr: EPUB -> {path}", flush=True)
    check = epubcheck(path)
    text = report.build(ps, layouts, ordered, book, dict(
        title=meta["title"], pdf=pdf, epub=path, size=path.stat().st_size, epubcheck=check,
        seconds=time.time() - t0, ocr_reused=reused, latin=latin, markers=markers, **info,
        model=f"{ocr_dir.name.removeprefix('ocr-')} ({opts.model})"))
    (out / f"{name}.report.md").write_text(text, encoding="utf-8")
    print(f"parisaocr: report -> {out / f'{name}.report.md'} (pages and notes to check first)\nparisaocr: epubcheck: {check}")
    return path
