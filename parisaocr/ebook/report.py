"""QA report: what the pipeline decided and where it was unsure, for a person checking the EPUB."""
import collections


def _pages(ns):
    return ", ".join(str(n) for n in ns) if ns else "none"


def build(pages, layouts, ordered, book, extra):
    """Markdown text of the report. `extra` carries run facts: pdf, epub, seconds, latin (English lines), markers, epubcheck."""
    kinds = collections.Counter(L.kind for L in layouts)
    r = book.report
    notes = [n for ch in book.chapters for n in ch.notes]
    linked_in_text = len(notes) - len(r["unlinked_notes"])
    out = [f"# Conversion report: {extra.get('title', '')}", ""]
    out += [f"- Source: `{extra.get('pdf')}` ({len(layouts)} PDF pages)",
            f"- Output: `{extra.get('epub')}` ({extra.get('size', 0) / 1e6:.1f} MB)",
            f"- OCR: ParisaOCR, model `{extra.get('model', '')}`",
            "- Structure: " + (f"a page labeller's labels ({extra['roles'].split('(', 1)[1]}" if (extra.get("roles") or "").startswith("page labels")
                               else f"layout rules with learned line roles ({extra['roles']})" if extra.get("roles") else "layout rules only")
            + (f"; {extra['marks']} pages marked by hand (--review)" if extra.get("marks") else ""),
            *([f"- Page labeller: {extra['gemini']}"] if extra.get("gemini") else []),
            f"- epubcheck: {extra.get('epubcheck', 'not run')}",
            f"- Time: {extra.get('seconds', 0):.0f} s (OCR {'reused' if extra.get('ocr_reused') else 'run'})", ""]

    out += ["## Pages", "",
            f"- {kinds['text']} text, {kinds['figure']} figure, {kinds['blank']} blank",
            f"- Book pages {ordered.pages[0][1]}–{ordered.pages[-1][1]}; {ordered.inferred} page numbers inferred "
            "from their neighbours (chapter openings, maps, unread headers)"]
    for a, b, na, nb in ordered.reversed_runs:
        out.append(f"- PDF pages {a}–{b} were scanned in reverse (book pages {na}–{nb}); put back in order")
    for dropped, kept, n in ordered.duplicates:
        out.append(f"- Book page {n} was scanned twice (PDF {dropped} and {kept}); kept PDF {kept}")
    for a, b in ordered.missing:
        out.append(f"- **Book page{'s' if a != b else ''} {a}{'–' + str(b) if a != b else ''} missing from the scan**; "
                   "a note marks the gap")
    rotated = [L.page.index for L in layouts if L.kind == "figure" and L.rotate]
    if rotated:
        out.append(f"- Figure pages turned upright: PDF {_pages(rotated)}")
    low = sorted((L.quality, L.page.index) for L in layouts if L.kind == "text" and L.quality < 0.8)
    if low:
        out.append(f"- Pages read with low confidence (check these first): PDF {_pages([i for _, i in low])}")
    out.append("")

    out += ["## Structure", "",
            f"- Printed contents on book pages {_pages(r['toc_pages'])}: {len([e for e in book.toc if e.list == 'contents'])} "
            f"entries, {len([e for e in book.toc if e.list == 'figures'])} in the list of figures (used as captions)",
            f"- {sum(ch.kind == 'chapter' for ch in book.chapters)} chapters, {len(r['headings'])} section headings, "
            f"{r['quotes']} block quotes, {r['verse']} verse lines (two hemistichs), {r.get('poem', 0)} poem lines, {r['tables']} tables (as images), {r['figures']} figures", ""]
    out += ["| Page | Chapter | Paragraphs | Notes |", "|---:|---|---:|---:|"]
    for ch in book.chapters:
        paras = sum(b.kind in ("p", "quote", "bib") for b in ch.blocks)
        title = ch.title or {"front": "(front matter)", "part": "(part)"}.get(ch.kind, "")
        out.append(f"| {ch.page} | {title} | {paras} | {len(ch.notes)} |")
    out.append("")
    if r["toc_unmatched"]:
        out += ["Contents entries not found as a heading:", ""]
        out += [f"- p. {e.page}: {e.title}" for e in r["toc_unmatched"]]
        out.append("")

    out += ["## Footnotes", "",
            f"- {len(notes)} notes; {linked_in_text} linked at their marker "
            f"({extra.get('markers', 0)} of those markers found in the page image, where the OCR had dropped them)",
            f"- {len(r['unlinked_notes'])} notes whose marker could not be found; each is linked at the end of its page's text:"]
    out += [f"  - p. {p} note {k}: {t}" for p, k, t in r["unlinked_notes"]]
    stray = [s for s in r["stray_markers"] if not s[2].rstrip().endswith(("(" + s[1]))]
    if stray:
        out.append(f"- {len(stray)} digits after a word that look like markers but match no note (left in the text):")
        out += [f"  - p. {p}: …{ctx}…" for p, _, ctx in stray]
    out.append("")

    out += ["## Text", "",
            f"- {extra.get('latin', 0)} English (Latin-script) lines"]
    for text, ns in extra.get("stamps", {}).items():
        out.append(f"- Stamp «{text}» burned into the scans, left out of {len(ns)} pages")
    if r["dropped"]:
        out.append(f"- {len(r['dropped'])} lines dropped as unreadable (stamps, letterheads, handwriting):")
        out += [f"  - p. {p}: {t}" for p, t in r["dropped"]]
    out.append("")
    return "\n".join(out)
