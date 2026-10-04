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

from . import epub, labels, layout, marks as marks_mod, order, refine, report, source, structure

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


def convert(opts, read, marks=None):
    """OPTS: the `parisaocr epub` arguments; READ(inputs, out_dir, formats): runs ParisaOCR with them.
    MARKS: hand marks for the structure (`marks.load`); without them, the book's marks file is used if there
    is one. Returns what `review` needs: the EPUB path, the Book, the ordered pages and the layouts."""
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
    if getattr(opts, "gemini_estimate", False):
        from . import gemini
        expected = gemini.plan(ocr_dir, work / gemini.labels_dirname(opts.gemini_model), opts.gemini_model)[2]
        who = "OpenRouter" if gemini.is_openrouter(opts.gemini_model) else "Gemini"
        print(f"parisaocr: {who}: {expected}; nothing was sent (run with --llm to label them)")
        raise SystemExit(0)

    if (opts.model == "default" or opts.model.startswith("kraken:")) and os.environ.get("PARISAOCR_NOTENUM", "1") != "0" \
            and not source.notenum_current(ocr_dir):
        try:  # note numbers the line reader lost, re-read from the image (cached with the OCR)
            from . import notenum
            from ..cli import BUNDLED_MODEL
            import torch
            device = "cpu" if getattr(opts, "cpu", False) or not torch.cuda.is_available() else "cuda:0"
            notenum.reread(ocr_dir, BUNDLED_MODEL if opts.model == "default" else opts.model[7:], device)
        except Exception as e:  # noqa: BLE001  a help, not a step the book needs: it is converted without them
            print(f"parisaocr: note numbers not re-read ({type(e).__name__}: {str(e)[:200]}); converting without them",
                  flush=True)
    print("parisaocr: page layout", flush=True)
    ps = source.load(ocr_dir)
    latin = sum(l.latin for p in ps for l in p.lines)
    marks_path = marks_mod.path_for(out, name)
    if marks is None:
        marks = marks_mod.load(marks_path)
    info = {}
    labels_dir = getattr(opts, "labels", None) or os.environ.get("PARISAOCR_LABELS")
    if getattr(opts, "gemini", False) and not labels_dir:  # answers are kept in the work directory: a rerun asks nothing
        from . import gemini
        labels_dir = str(work / gemini.labels_dirname(opts.gemini_model))
        info["gemini"] = gemini.label_book(ocr_dir, labels_dir, model=opts.gemini_model, jobs=opts.gemini_jobs)
    layouts = layout.analyze_all(ps, roles_dir=opts.roles, report=info, labels_dir=labels_dir)
    if marks:
        marks_mod.apply_figures(marks, layouts)
    layout.orient_figures(layouts, ocr_dir, read)
    markers = sum(refine.refine(L) for L in layouts)
    ordered = order.order(layouts)
    print("parisaocr: book structure", flush=True)
    book = structure.build(ordered, marks)

    meta = json.loads(pathlib.Path(opts.meta).expanduser().read_text(encoding="utf-8")) if opts.meta else {}
    for k in META_KEYS:
        if getattr(opts, k, None):
            meta[k] = getattr(opts, k)
    if labels_dir:  # what the labeller read on the cover, title and imprint pages fills what the user did not give
        found = labels.book_meta(labels_dir.replace("{slug}", name))
        people = {"author": found.get("authors"), "translator": found.get("translators"), "editor": found.get("editors")}
        for k, v in list(people.items()) + [(k, found.get(k)) for k in ("title", "subtitle", "publisher", "isbn", "year")]:
            v = "، ".join(x for x in v if x) if isinstance(v, list) else v
            if v and not meta.get(k):
                meta[k] = v
    if not meta.get("title"):
        front = next((b.rows[0] for ch in book.chapters if ch.kind == "front" for b in ch.blocks if b.kind == "lines" and b.rows), None)
        meta["title"] = front or pdf.stem
    path = epub.write(book, out / f"{name}.epub", meta, tables=opts.tables)
    print(f"parisaocr: EPUB -> {path}", flush=True)
    check = epubcheck(path)
    text = report.build(ps, layouts, ordered, book, dict(
        title=meta["title"], pdf=pdf, epub=path, size=path.stat().st_size, epubcheck=check,
        seconds=time.time() - t0, ocr_reused=reused, latin=latin, markers=markers, **info,
        marks=len(marks["pages"]) if marks and marks.get("reviewed") else 0,
        model=f"{ocr_dir.name.removeprefix('ocr-')} ({opts.model})"))
    (out / f"{name}.report.md").write_text(text, encoding="utf-8")
    print(f"parisaocr: report -> {out / f'{name}.report.md'} (pages and notes to check first)\nparisaocr: epubcheck: {check}")
    summary = "\n".join(l.lstrip("- ") for l in text.splitlines() if l.startswith(("- Structure", "- ")) and ("chapters," in l or "Structure" in l))
    return dict(epub=path, report=out / f"{name}.report.md", summary=summary, title=meta["title"], book=book,
                ordered=ordered, layouts=layouts, marks_path=marks_path, work=work)
