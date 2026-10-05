"""Machine-readable progress for `parisaocr app`.

With PARISAOCR_PROGRESS=1 in the environment (the app sets it for the commands it runs), every step also prints one
line `@progress {json}` on stderr: the stage, how far it is, and what it found. Without it nothing is printed, so the
command line's own output stays as it is.
"""
import contextlib
import json
import os
import sys

_renamed = {}


def enabled():
    return os.environ.get("PARISAOCR_PROGRESS") == "1"


@contextlib.contextmanager
def renamed(old, new):
    """Inside the block, stage OLD is reported as NEW: a later step that reuses the OCR on a few pages (the figure
    pages read again turned) is not taken for the book's OCR."""
    _renamed[old] = new
    try:
        yield
    finally:
        _renamed.pop(old, None)


def emit(stage, done=None, total=None, **extra):
    """One progress line: STAGE (pages, ocr, pdf, notenum, estimate, llm, layout, figures, structure, epub, report,
    review), DONE of TOTAL when the stage counts something, and any other facts as JSON values."""
    if not enabled():
        return
    d = {"stage": _renamed.get(stage, stage)}
    if done is not None:
        d["done"] = done
    if total is not None:
        d["total"] = total
    d.update({k: (str(v) if isinstance(v, os.PathLike) else v) for k, v in extra.items()})
    print("@progress " + json.dumps(d, ensure_ascii=False), file=sys.stderr, flush=True)
