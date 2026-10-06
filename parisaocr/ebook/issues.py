"""What the review panel asks a person to look at first: how sure the converter is of each line, and the book's open
issues (a note whose marker was never found, a contents entry no heading matches ...), least sure first.

The doubt of a line, from 0 (sure) to 1 (unsure), has two sides, shown apart:
- its role: from the line-role models (`roles.annotate`, kept in `Line.model`). Measured on 8 books they never trained
  on, against Gemini's labels (2026-10-05): P(footnote) separates footnote lines almost perfectly (AUROC 0.995) and
  three quarters of its mistakes fall where it hesitates (between 0.1 and 0.9); P(heading) is close behind (0.98);
  the role model's top probability ranks its mistakes well (0.90) but is too sure below 0.99, so it counts for less.
  Where the models disagree with the decision taken (a labeller's or the rules'), the line is in doubt as well.
- its text: from the OCR's weakest word on the line. Measured on 750 hand-checked lines: it finds the misread lines
  only roughly (AUROC 0.74; the least sure 10% of lines hold 41% of them), better than the line's average.
A line a person set by hand is sure on the side they set. "check" from 0.2, "unsure" from 0.5.
"""
import re

from .textutil import latin_ratio

MODEL_ROLES = {"header", "pagenum", "heading", "body", "quote", "verse", "note", "endnote", "caption", "table",
               "figure", "contents", "other"}
CLEAR = {"body", "heading", "note", "header", "pagenum", "quote", "verse"}  # where a disagreement means something


def level(doubt):
    return "unsure" if doubt >= 0.5 else "check" if doubt >= 0.2 else "sure"


def _edge(p):
    """How far a probability is from deciding: 0 at 0 or 1, 1 at 0.5."""
    return 1 - abs(2 * float(p) - 1)


def role_doubt(model, role):
    """Doubt about a line's ROLE given the models' view MODEL (`Line.model`), or None without models."""
    if not model:
        return None
    parts = []
    if "note" in model:
        parts.append(_edge(model["note"]))
    if "heading" in model:
        parts.append(_edge(model["heading"]))
    if "role_p" in model:
        parts.append(0.6 * min(1.0, max(0.0, (0.99 - model["role_p"]) / 0.49)))
    same = {"pagenum": "header"}  # a page number in the running head's row: one row, both readings right
    m = model.get("role")
    if m in CLEAR and role in CLEAR and same.get(m, m) != same.get(role, role):
        parts.append(0.5)  # the models read the line otherwise
    return round(max(parts), 3) if parts else None


def text_doubt(words, conf):
    """Doubt about a line's text from the OCR's confidences (0-100): its weakest word, else the line's."""
    weak = min((w["conf"] if isinstance(w, dict) else w.conf for w in words), default=conf)
    return round(min(1.0, max(0.0, (97 - weak) / 25)), 3), weak


def _norm(t):
    return re.sub(r"\W+", "", t or "")


def find(panel):
    """The open issues of the book the panel shows: [{"id", "kind", "pdf", "row"?, "doubt", "text", "detail"}], least
    sure first. Kinds: role, text (a line); note (a note without its marker), marker (a marker without its note),
    numbering (a gap in a page's note numbers), contents (an entry of the printed contents no heading matches),
    chapter (a chapter the printed contents do not list), page (a page read with low confidence), left (a page left out
    of the book: taken for a second scan of a page with its number)."""
    out = []
    took = {d: (k, n) for d, k, n in panel.ordered.duplicates}
    for pdf, p in panel.pages.items():
        if pdf in panel.n_of or not any(l.text.strip() for l in p.lines):
            continue
        k, n = took.get(pdf, (None, None))
        out.append({"id": f"left:{pdf}", "kind": "left", "pdf": pdf, "doubt": 0.85, "text": "",
                    "detail": {"kept": k, "number": n}})
    for pdf, info in panel.page_infos().items():
        for ln in info["lines"]:
            if ln["d"]["r"] in ("noise",) and not ln["text"].strip():
                continue
            if ln["doubt"]["role"] is not None and ln["doubt"]["role"] >= 0.2:
                out.append({"id": f"role:{pdf}:{ln['row']}", "kind": "role", "pdf": pdf, "row": ln["row"],
                            "doubt": ln["doubt"]["role"], "text": ln["text"][:80],
                            "detail": {"role": ln["d"]["r"], "model": (ln.get("model") or {}).get("role")}})
            if ln["doubt"]["text"] >= 0.3 and ln["d"]["r"] not in ("noise", "figure", "table"):
                out.append({"id": f"text:{pdf}:{ln['row']}", "kind": "text", "pdf": pdf, "row": ln["row"],
                            "doubt": ln["doubt"]["text"], "text": ln["text"][:80], "detail": {"weak": ln["weak"]}})
        text_lines = [ln for ln in info["lines"] if ln["d"]["r"] not in ("noise", "figure", "table", "header", "pagenum")]
        weak = [ln for ln in text_lines if ln["doubt"]["text"] >= 0.3]
        if len(text_lines) >= 5 and len(weak) >= 0.3 * len(text_lines):  # much of the page read uncertainly
            share = len(weak) / len(text_lines)
            out.append({"id": f"page:{pdf}", "kind": "page", "pdf": pdf, "doubt": round(min(0.95, 0.4 + share / 2), 3),
                        "text": "", "detail": {"weak_lines": len(weak), "lines": len(text_lines)}})
    report = panel.book.report
    for n, num, text in report.get("unlinked_notes", []):
        pdf = panel.pdf_of.get(n)
        if pdf is not None:
            out.append({"id": f"note:{pdf}:{num}", "kind": "note", "pdf": pdf, "doubt": 0.9, "text": text,
                        "detail": {"num": num}})
    for n, mark, context in report.get("stray_markers", []):
        pdf = panel.pdf_of.get(n)
        if pdf is not None:
            out.append({"id": f"marker:{pdf}:{_norm(context)[:30]}", "kind": "marker", "pdf": pdf, "doubt": 0.6,
                        "text": context, "detail": {"mark": mark}})
    for pdf, notes in panel.notes_by_pdf().items():
        nums = sorted(x["num"] for x in notes if x["num"])
        gaps = [k for k in range(nums[0], nums[-1]) if k not in nums] if nums else []
        if gaps:
            out.append({"id": f"numbering:{pdf}", "kind": "numbering", "pdf": pdf, "doubt": 0.7,
                        "text": "", "detail": {"missing": gaps}})
    contents = [e for e in panel.book.toc if e.list == "contents"]
    for e in report.get("toc_unmatched", []):
        pdf = panel.pdf_of.get(e.page)
        out.append({"id": f"contents:{_norm(e.title)[:40]}", "kind": "contents", "pdf": pdf, "doubt": 0.8,
                    "text": e.title, "detail": {"page": e.page, "level": e.level}})
    if len(contents) >= 3:
        from .structure import _similar
        for ch in panel.book.chapters:
            if ch.kind != "chapter" or not ch.title:
                continue
            if not any(_similar(ch.title, e.title) > 0.6 for e in contents):
                pdf = panel.pdf_of.get(ch.page)
                out.append({"id": f"chapter:{pdf}", "kind": "chapter", "pdf": pdf, "doubt": 0.5, "text": ch.title,
                            "detail": {}})
    ignored = set(panel.corr.get("ignored") or [])
    for x in out:
        x["ignored"] = x["id"] in ignored
        x["ltr"] = latin_ratio(x.get("text") or "") > 0.5
    out.sort(key=lambda x: (x["ignored"], -x["doubt"], x["pdf"] or 0, x.get("row", -1)))
    return out
