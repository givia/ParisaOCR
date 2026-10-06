"""`parisaocr epub --review`: the review panel, a local page where a person checks the converted book and fixes it.

The panel shows the book as the converter made it, page by page: every OCR line with what the converter decided
(its role, a heading's level, a paragraph start, a note's number), how sure it is of that (`issues`), and the page as
it reads in the EPUB. The person can change any of it, line by line or for the whole page: roles, levels, paragraph
starts, note numbers, where a note's marker stands, a misread text, a heading the OCR missed, a contents page's
entries, the page's printed number; and for the book: where its parts, chapters and sections open (the marks file,
`marks`), its data (title, authors ...), its cover. The open issues come first, least sure first: notes without a
marker, contents entries no heading matches, lines the models doubt, lines the OCR read uncertainly.

Corrections go to OUT/NAME.review.json (`corrections`) and the marks to OUT/NAME.marks.json; every later conversion
of the book uses them. Rebuilding takes seconds: the OCR is kept.

The server binds 127.0.0.1 only: the page images never leave the machine. It answers only requests that name it as
their host (a page whose own domain is made to resolve to 127.0.0.1 is refused).
"""
import http.server
import json
import mimetypes
import os
import pathlib
import posixpath
import re
import threading
import time
import urllib.parse
import webbrowser
import zipfile

from PIL import Image

from . import corrections, decisions, issues as issues_mod, marks as marks_mod, source
from .. import progress

THUMB, LARGE = 180, 1400  # thumbnail and page-view widths in pixels
STATIC = pathlib.Path(__file__).resolve().parent / "static"
FONTS = pathlib.Path(__file__).resolve().parent.parent / "fonts"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".xhtml": "application/xhtml+xml", ".ttf": "font/ttf", ".otf": "font/otf", ".jpg": "image/jpeg",
         ".png": "image/png", ".svg": "image/svg+xml"}
DIGITS_SPACE = re.compile(r"[\d۰-۹٠-٩\s]+")


class Panel:
    """The review panel's state: the conversion result RES (`convert.convert`), REBUILD(marks dict) -> a new result,
    CACHE_DIR for page images."""

    def __init__(self, res, rebuild, cache_dir):
        self.rebuild_fn = rebuild
        self.cache = pathlib.Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.done = threading.Event()
        self.building = False
        self.load(res)

    # --- the converted book ------------------------------------------------------------------------------------------

    def load(self, res, seconds=None):
        with self.lock:
            self.res = res
            self.book, self.ordered = res["book"], res["ordered"]
            self.layouts = {L.page.index: L for L in res["layouts"]}
            self.pages = {p.index: p for p in res["pages"]}
            self.n_of = {L.page.index: n for L, n in self.ordered.pages}
            self.pdf_of = {n: pdf for pdf, n in self.n_of.items()}
            self.dec = decisions.collect(res["layouts"], self.book, self.ordered)
            self.corr = corrections.load(res["corrections_path"])
            self.marks_path = res["marks_path"]
            self.built = time.time()
            self.build_seconds = seconds
            self.dirty = False
            self._rows, self._infos, self._issues = {}, {}, None
            self.hrefs = self._page_hrefs()

    def _page_hrefs(self):
        """{printed page: href in the EPUB} from its page-list."""
        out = {}
        try:
            with zipfile.ZipFile(self.res["epub"]) as z:
                nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
        except (OSError, KeyError, zipfile.BadZipFile):
            return out
        block = re.search(r'<nav epub:type="page-list".*?</nav>', nav, re.S)
        for href, n in re.findall(r'href="([^"]+#page-(\d+))"', block.group(0) if block else ""):
            out[int(n)] = "OEBPS/" + href
        return out

    def rows(self, pdf):
        if pdf not in self._rows:
            self._rows[pdf] = source.rows(self.res["ocr_dir"], pdf)
        return self._rows[pdf]

    def page_info(self, pdf):
        """A page as the panel shows it: its lines with the current reading (the person's where they changed it),
        the models' view, and the doubts."""
        p, L, d = self.pages[pdf], self.layouts[pdf], self.dec[pdf]
        rows = self.rows(pdf)
        corr = self.corr["pages"].get(pdf)
        line_of = {}
        for l in p.lines:
            for x in getattr(l, "parts", None) or [l]:
                if x.row >= 0:
                    line_of.setdefault(x.row, x)
            for jr in getattr(l, "joined", ()):
                line_of.setdefault(jr, l)
        human = corrections._match([it for it in corr["lines"] if it.get("h")], rows) if corr else {}
        lines = []
        for r, row in sorted(rows.items()):
            it = human.get(r)
            base = d["lines"].get(r) or {"r": "noise"}  # the converter's reading now
            cur = {k: base[k] for k in ("r", "l", "n", "p", "m", "a", "t") if k in base}
            h = list((it or {}).get("h") or [])
            for k in h:  # with the person's changes on top
                if k == "x":
                    continue
                if k in it:
                    cur[k] = it[k]
                else:
                    cur.pop(k, None)
            l = line_of.get(r)
            model = getattr(l, "model", None) if l is not None else None
            rd = 0.0 if "r" in h else issues_mod.role_doubt(model, cur["r"])
            td, weak = issues_mod.text_doubt(row["words"], row["conf"])
            if "x" in h:
                td = 0.0
            lines.append({"row": r, "bbox": row["bbox"], "text": row["text"], "x": (it or {}).get("x"),
                          "conf": row["conf"], "weak": weak, "words": row["words"], "d": cur, "h": h,
                          "model": model, "doubt": {"role": rd, "text": td}})
        page = {"type": d["type"], **({"pn": str(d["n"])} if d.get("n") else {})}
        ph = list((corr or {}).get("h") or [])
        for k in ph:  # what the person set for the page
            if k in (corr.get("page") or {}):
                page[k] = corr["page"][k]
        return {"pdf": pdf, "n": d["n"], "kind": d["kind"], "type": page.get("type", d["type"]), "page": page,
                "page_h": ph, "width": p.width, "height": p.height,
                "reviewed": corr is not None, "quality": getattr(L, "quality", None), "lines": lines,
                "regions": [{"kind": r.kind, "bbox": [int(v) for v in r.bbox]} for r in L.regions],
                "crop": [int(v) for v in L.crop] if getattr(L, "crop", None) else None, "rotate": getattr(L, "rotate", 0),
                "left": None if pdf in self.n_of else dict(zip(("kept", "number"), next(
                    ((k, n) for x, k, n in self.ordered.duplicates if x == pdf), (None, None)))),
                "missing": (corr if "missing" in ph else d).get("missing") or [],
                "toc": (corr if "toc" in ph else d).get("toc") or [],
                "href": self.href_of(pdf)}

    def page_infos(self):
        """Every page's info, kept until a change touches the page."""
        for pdf in self.pages:
            if pdf not in self._infos:
                self._infos[pdf] = self.page_info(pdf)
        return {pdf: self._infos[pdf] for pdf in sorted(self.pages)}

    def href_of(self, pdf):
        n = self.n_of.get(pdf)
        if n is None or not self.hrefs:
            return None
        if n in self.hrefs:
            return self.hrefs[n]
        lower = [k for k in self.hrefs if k <= n]
        return self.hrefs[max(lower)] if lower else self.hrefs[min(self.hrefs)]

    def notes_by_pdf(self):
        unlinked = {(n, num) for n, num, _ in self.book.report.get("unlinked_notes", [])}
        out = {}
        for ch in self.book.chapters:
            for note in ch.notes:
                pdf = self.pdf_of.get(note.page)
                if pdf is None:
                    continue
                endnote = note.id.startswith("e")
                how = "endnote" if endnote else "end of page" if (note.page, note.num) in unlinked else "marker"
                out.setdefault(pdf, []).append({"num": note.num, "text": note.text, "how": how, "id": note.id})
        return out

    def issues(self):
        if self._issues is None:
            self._issues = issues_mod.find(self)
        return self._issues

    def book_info(self):
        infos = self.page_infos()
        iss = self.issues()
        per_page = {}
        for x in iss:
            if not x["ignored"] and x["pdf"] is not None:
                per_page[x["pdf"]] = per_page.get(x["pdf"], 0) + 1
        pages = []
        for pdf, info in infos.items():
            doubts = [max(v for v in ln["doubt"].values() if v is not None) for ln in info["lines"]
                      if any(v is not None for v in ln["doubt"].values())]
            pages.append({"pdf": pdf, "n": info["n"], "kind": info["kind"], "type": info["type"],
                          "reviewed": info["reviewed"], "doubt": round(max(doubts, default=0.0), 3),
                          "unsure": sum(v >= 0.5 for v in doubts), "check": sum(0.2 <= v < 0.5 for v in doubts),
                          "issues": per_page.get(pdf, 0)})
        saved = marks_mod.load(self.marks_path)
        auto = self.book.report.get("starts", {})
        units = saved["pages"] if saved else {int(k): v for k, v in auto.items()}
        chapters = [{"pdf": pdf, "n": self.n_of.get(pdf), "kind": v["kind"], "title": v.get("title", "")}
                    for pdf, v in sorted(units.items())]
        sections = [{"pdf": self.pdf_of.get(n), "n": n, "title": t} for n, t, _ in self.book.report.get("headings", [])]
        meta = dict(self.res.get("meta") or {})
        cover = (self.corr["book"] or {}).get("cover")
        if cover is None and self.book.cover:
            cover = self.book.cover[0].page.index
        return {"title": self.res.get("title") or meta.get("title", ""), "epub": str(self.res["epub"]),
                "built": self.built, "seconds": self.build_seconds, "dirty": self.dirty, "building": self.building,
                "pages": pages, "chapters": chapters, "marked": bool(saved), "sections": sections,
                "meta": meta, "meta_h": (self.corr["book"] or {}).get("meta") or {}, "cover": cover,
                "contents": [{"title": e.title, "page": e.page, "level": e.level, "list": e.list} for e in self.book.toc],
                "summary": self.res.get("summary", ""), "issues": sum(1 for x in iss if not x["ignored"]),
                "reviewed": len(self.corr["pages"]), "history": len(self.corr["history"])}

    # --- changes -----------------------------------------------------------------------------------------------------

    def _changed(self, pdfs=None):
        """Save the corrections; the pages PDFS (all when None) are shown anew."""
        corrections.save(self.res["corrections_path"], self.corr)
        self.dirty = True
        if pdfs is None:
            self._infos = {}
        else:
            for pdf in pdfs:
                self._infos.pop(pdf, None)
        self._issues = None

    def edit_page(self, pdf, edits):
        with self.lock:
            corrections.edit_page(self.corr, pdf, self.dec[pdf], self.rows(pdf), edits)
            self._sync_marks(pdf, edits)
            self._changed([pdf])
            return self.page_info(pdf)

    def _sync_marks(self, pdf, edits):
        """Once the list of parts and chapters is saved (the marks file), it alone decides them: a line made a level-1
        heading on a page not in the list adds the page to it, as a chapter titled by its level-1 lines."""
        saved = marks_mod.load(self.marks_path)
        if not saved or pdf in saved["pages"]:
            return
        lines = (self.corr["pages"].get(pdf) or {}).get("lines", [])
        touched = {int(r) for r, f in (edits.get("lines") or {}).items() if {"r", "l"} & set(f or {})}
        heads = [it for it in lines if it["i"] in touched and it.get("r") == "heading" and it.get("l") == 1]
        if heads:
            title = " ".join((it.get("x") or it.get("t") or it.get("o") or "").strip()
                             for it in sorted(lines, key=lambda it: it["b"][1]) if it.get("r") == "heading" and it.get("l") == 1)
            pages = dict(saved["pages"])
            pages[pdf] = {"kind": "chapter", "title": title}
            marks_mod.save(self.marks_path, pages, self.book.report.get("starts", {}))

    def revert_page(self, pdf):
        with self.lock:
            corrections.revert_page(self.corr, pdf)
            self._changed([pdf])
            return self.page_info(pdf)

    def edit_book(self, edits):
        with self.lock:
            corrections.edit_book(self.corr, edits)
            self._changed([])

    def bulk(self, text, edits):
        """Set EDITS ({field: value}) on every line of the book whose text is TEXT (digits and spaces ignored): a
        running header repeated on every page, a stamp. -> the pages changed."""
        key = DIGITS_SPACE.sub("", text)
        changed = []
        if not key:
            return changed
        with self.lock:
            for pdf in sorted(self.pages):
                rows = self.rows(pdf)
                hits = [r for r, row in rows.items() if DIGITS_SPACE.sub("", row["text"]) == key]
                if hits:
                    corrections.edit_page(self.corr, pdf, self.dec[pdf], rows, {"lines": {r: dict(edits) for r in hits}})
                    changed.append(pdf)
            if changed:
                self._changed(changed)
        return changed

    def undo(self):
        with self.lock:
            entry = corrections.undo(self.corr)
            if entry is not None:
                self._changed([entry["pdf"]] if "pdf" in entry else [])
            return entry

    def ignore(self, issue_id, ignored):
        with self.lock:
            ign = set(self.corr.get("ignored") or [])
            (ign.add if ignored else ign.discard)(issue_id)
            self.corr["ignored"] = sorted(ign)
            corrections.save(self.res["corrections_path"], self.corr)
            self._issues = None

    def save_marks(self, pages):
        with self.lock:
            marks_mod.save(self.marks_path, pages, self.book.report.get("starts", {}))
            self.dirty = True

    def rebuild(self):
        with self.lock:
            if self.building:
                raise RuntimeError("a rebuild is running")
            self.building = True
        t0 = time.time()
        try:
            res = self.rebuild_fn(marks_mod.load(self.marks_path))
            self.load(res, round(time.time() - t0, 1))
        finally:
            self.building = False
        return {"epub": str(self.res["epub"]), "summary": self.res.get("summary", ""), "seconds": self.build_seconds}

    # --- images ------------------------------------------------------------------------------------------------------

    def image(self, pdf, width):
        page = self.pages.get(pdf)
        if page is None:
            return None
        f = self.cache / f"p-{pdf:03d}-{width}.jpg"
        if not f.exists():
            with Image.open(page.image) as im:
                im = im.convert("L") if im.mode in ("1", "I;16", "I") else im
                im.thumbnail((width, width * 3))
                im.convert("RGB").save(f, "JPEG", quality=82)
        return f.read_bytes()


class Handler(http.server.BaseHTTPRequestHandler):
    review = None  # the Panel, set by `start`

    def log_message(self, *args):
        pass

    def _send(self, body, kind="application/json; charset=utf-8", code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _local(self):
        """Only requests addressed to this server by its loopback name: a web page whose own domain is made to
        resolve to 127.0.0.1 (DNS rebinding) sends its domain as Host and is refused."""
        port = self.server.server_address[1]
        if self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return True
        self._send(b"forbidden", "text/plain", 403)
        return False

    def do_GET(self):
        if not self._local():
            return
        r = self.review
        url = urllib.parse.urlsplit(self.path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        try:
            if path in ("/", "/index.html"):
                return self._send((STATIC / "panel.html").read_bytes(), TYPES[".html"])
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                f = FONTS / name[6:] if name.startswith("fonts/") else STATIC / name
                if "/" in name.removeprefix("fonts/") or not f.is_file():
                    return self._send(b"not found", "text/plain", 404)
                return self._send(f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
            if path in ("/api/book", "/api/state"):
                return self._send(r.book_info())
            if path == "/api/issues":
                return self._send(r.issues())
            m = re.fullmatch(r"/api/page/(\d+)", path)
            if m:
                pdf = int(m.group(1))
                if pdf not in r.pages:
                    return self._send(b"no such page", "text/plain", 404)
                info = r.page_info(pdf)
                info["notes"] = r.notes_by_pdf().get(pdf, [])
                info["issues"] = [x for x in r.issues() if x["pdf"] == pdf]
                return self._send(info)
            if path.startswith("/img/"):
                pdf = int(path[5:].split(".")[0])
                width = int(query.get("w", [LARGE if "large" in query else THUMB])[0])
                data = r.image(pdf, max(80, min(width, 2400)))
                if data is None:
                    return self._send(b"no such page", "text/plain", 404)
                return self._send(data, "image/jpeg")
            if path.startswith("/epub/"):
                inner = posixpath.normpath(urllib.parse.unquote(path[len("/epub/"):]))
                try:
                    with zipfile.ZipFile(r.res["epub"]) as z:
                        data = z.read(inner)
                except (KeyError, OSError, zipfile.BadZipFile):
                    return self._send(b"not found", "text/plain", 404)
                kind = TYPES.get(posixpath.splitext(inner)[1].lower()) or mimetypes.guess_type(inner)[0] or "application/octet-stream"
                return self._send(data, kind)
            return self._send(b"not found", "text/plain", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001  the page shows it; the server stays up
            self._send({"ok": False, "error": f"{type(e).__name__}: {e}"}, code=500)

    def do_POST(self):
        if not self._local():
            return
        r = self.review
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
            path = urllib.parse.urlsplit(self.path).path
            m = re.fullmatch(r"/api/page/(\d+)(/(revert|accept))?", path)
            if m:
                pdf = int(m.group(1))
                if pdf not in r.pages:
                    return self._send({"ok": False, "error": "no such page"}, code=404)
                if m.group(3) == "revert":
                    info = r.revert_page(pdf)
                else:
                    info = r.edit_page(pdf, body if m.group(3) is None else {})
                return self._send({"ok": True, "page": info})
            if path == "/api/book":
                r.edit_book(body)
                return self._send({"ok": True})
            if path == "/api/bulk":
                return self._send({"ok": True, "pages": r.bulk(body.get("text", ""), body.get("edits") or {})})
            if path == "/api/marks":
                r.save_marks(body.get("pages", {}))
                return self._send({"ok": True})
            if path == "/api/undo":
                entry = r.undo()
                return self._send({"ok": True, "undone": {k: v for k, v in entry.items() if k in ("pdf", "when")}
                                   if entry else None})
            if path == "/api/ignore":
                r.ignore(body.get("id", ""), bool(body.get("ignored", True)))
                return self._send({"ok": True})
            if path in ("/api/rebuild", "/api/build"):
                if body.get("pages") is not None:  # marks sent with the rebuild
                    r.save_marks(body["pages"])
                return self._send({"ok": True, **r.rebuild()})
            if path == "/api/quit":
                r.done.set()
                return self._send({"ok": True})
            return self._send({"ok": False, "error": "not found"}, code=404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            self._send({"ok": False, "error": f"{type(e).__name__}: {e}"}, code=500)


def start(review, port=0):
    """The panel's server on 127.0.0.1:PORT (0: any free port), in a daemon thread -> (server, url)."""
    Handler.review = review
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def serve(review, port=0, open_browser=True):
    """Serve the review panel on 127.0.0.1 until the person clicks Done (or Ctrl-C)."""
    server, url = start(review, port)
    print(f"parisaocr: review panel at {url} (fix what needs fixing, then Rebuild; Done closes it)", flush=True)
    progress.emit("review", url=url)
    if open_browser and os.environ.get("PARISAOCR_NO_BROWSER") != "1":  # the app shows the page itself
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        while not review.done.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    time.sleep(0.3)  # let the reply to Done reach the page
    server.shutdown()
    return url
