"""`parisaocr epub --review`: a local page where the reader marks the book's structure by hand.

Every page of the book is shown as a thumbnail in reading order with what the
converter decided: part, chapter and section openings with their titles, and
figure pages. The reader adds or removes openings, fixes a title (the OCR of
the page's first lines is offered), sets the level, and rebuilds the EPUB;
the marks are saved next to it (NAME.marks.json, see `marks`) and are used by
every later conversion of the book. Three minutes for a book, and the chapters
are right regardless of what the rules and models could see.

The server binds 127.0.0.1 only: the page images never leave the machine.
"""
import http.server
import io
import json
import os
import pathlib
import threading
import time
import webbrowser

from PIL import Image

from . import marks as marks_mod
from .. import progress

THUMB, LARGE = 180, 1100  # thumbnail and page-view widths in pixels


class Review:
    """What the page shows and does. PAGES: [{pdf, n, kind, auto, lines, image}] in reading order
    (`pages_of`); MARKS_PATH: the marks file; REBUILD(marks dict) -> {"epub", "summary"}."""

    def __init__(self, title, pages, marks_path, rebuild, cache_dir):
        self.title, self.pages, self.marks_path, self.rebuild = title, pages, marks_path, rebuild
        self.by_pdf = {p["pdf"]: p for p in pages}
        self.cache = pathlib.Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.done = threading.Event()

    def state(self):
        saved = marks_mod.load(self.marks_path)
        current = saved["pages"] if saved else {str(p["pdf"]): p["auto"] for p in self.pages if p["auto"]}
        return {"title": self.title, "pages": [{k: v for k, v in p.items() if k != "image"} for p in self.pages],
                "marks": current, "reviewed": bool(saved), "marks_path": str(self.marks_path)}

    def image(self, pdf, width):
        page = self.by_pdf.get(pdf)
        if page is None:
            return None
        f = self.cache / f"p-{pdf:03d}-{width}.jpg"
        if not f.exists():
            with Image.open(page["image"]) as im:
                im = im.convert("L") if im.mode == "1" else im
                im.thumbnail((width, width * 3))
                im.convert("RGB").save(f, "JPEG", quality=80)
        return f.read_bytes()

    def save(self, pages):
        with self.lock:
            marks_mod.save(self.marks_path, pages, {str(p["pdf"]): p["auto"] for p in self.pages if p["auto"]})

    def build(self, pages):
        self.save(pages)
        t0 = time.time()
        with self.lock:
            out = self.rebuild(marks_mod.load(self.marks_path))
        out["seconds"] = round(time.time() - t0, 1)
        return out


def pages_of(ordered, book, layouts):
    """The review's page list from a conversion: reading order, the converter's own decisions as `auto`."""
    auto = book.report.get("starts", {})
    out = []
    for L, n in ordered.pages:
        p = L.page
        lines = [l.text for l in sorted(L.body, key=lambda l: l.y0) if l.conf >= 70][:5]
        a = auto.get(p.index)
        if a is None and L.kind == "figure":
            a = {"kind": "figure", "title": ""}
        out.append({"pdf": p.index, "n": n, "kind": L.kind, "auto": a, "lines": lines, "image": str(p.image)})
    return out


class Handler(http.server.BaseHTTPRequestHandler):
    review = None  # set by `serve`

    def log_message(self, *args):
        pass

    def _send(self, body, kind="application/json; charset=utf-8", code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
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
        path, _, query = self.path.partition("?")
        if path == "/":
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._send(r.state())
        elif path.startswith("/img/"):
            try:
                pdf = int(path[5:].split(".")[0])
                width = LARGE if "large" in query else THUMB
                data = r.image(pdf, width)
            except (ValueError, OSError):
                data = None
            if data is None:
                self._send(b"no such page", "text/plain", 404)
            else:
                self._send(data, "image/jpeg")
        else:
            self._send(b"not found", "text/plain", 404)

    def do_POST(self):
        if not self._local():
            return
        r = self.review
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        if self.path == "/api/marks":
            r.save(body.get("pages", {}))
            self._send({"ok": True})
        elif self.path == "/api/build":
            try:
                out = r.build(body.get("pages", {}))
                self._send({"ok": True, **out})
            except Exception as e:  # the page shows the error; the server stays up
                self._send({"ok": False, "error": f"{type(e).__name__}: {e}"})
        elif self.path == "/api/quit":
            self._send({"ok": True})
            r.done.set()
        else:
            self._send(b"not found", "text/plain", 404)


def start(review, port=0):
    """The review server on 127.0.0.1:PORT (0: any free port), in a daemon thread -> (server, url)."""
    Handler.review = review
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def serve(review, port=0, open_browser=True):
    """Serve the review page on 127.0.0.1 until the reader clicks Done (or Ctrl-C)."""
    server, url = start(review, port)
    print(f"parisaocr: review page at {url} (mark the openings, then Rebuild; Done closes it)", flush=True)
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
    server.shutdown()
    return url


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ParisaOCR review</title>
<style>
:root { --chapter: #2e7d32; --part: #6a1b9a; --section: #1565c0; --figure: #ef6c00; --bg: #fafafa; --ink: #222; }
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.4 system-ui, sans-serif; color: var(--ink); background: var(--bg); display: grid;
       grid-template-rows: auto 1fr; grid-template-columns: 1fr 380px; height: 100vh; }
header { grid-column: 1 / 3; display: flex; gap: 16px; align-items: center; padding: 8px 14px; background: #fff;
         border-bottom: 1px solid #ddd; }
header h1 { font-size: 16px; margin: 0; font-weight: 600; }
header .counts { color: #666; }
header .spacer { flex: 1; }
button { font: inherit; padding: 6px 12px; border: 1px solid #bbb; border-radius: 6px; background: #fff; cursor: pointer; }
button.primary { background: var(--chapter); color: #fff; border-color: var(--chapter); }
button:disabled { opacity: .5; cursor: default; }
#grid { overflow: auto; padding: 12px; display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px;
        align-content: start; }
.tile { position: relative; border: 3px solid transparent; border-radius: 6px; background: #fff; cursor: pointer;
        box-shadow: 0 1px 2px rgba(0,0,0,.15); }
.tile img { display: block; width: 100%; height: auto; border-radius: 3px; }
.tile .num { position: absolute; top: 4px; left: 4px; background: rgba(0,0,0,.6); color: #fff; font-size: 11px;
             padding: 1px 5px; border-radius: 3px; }
.tile .label { position: absolute; left: 0; right: 0; bottom: 0; font-size: 11px; padding: 2px 5px; color: #fff;
               white-space: nowrap; overflow: hidden; text-overflow: ellipsis; direction: rtl; border-radius: 0 0 3px 3px; }
.tile.chapter { border-color: var(--chapter); } .tile.chapter .label { background: var(--chapter); }
.tile.part { border-color: var(--part); } .tile.part .label { background: var(--part); }
.tile.section { border-color: var(--section); } .tile.section .label { background: var(--section); }
.tile.figure { border-color: var(--figure); } .tile.figure .label { background: var(--figure); }
.tile.selected { outline: 3px solid #111; outline-offset: 1px; }
aside { border-left: 1px solid #ddd; background: #fff; overflow: auto; display: flex; flex-direction: column; }
#detail { padding: 12px; border-bottom: 1px solid #eee; }
#detail img { width: 100%; height: auto; border: 1px solid #ddd; margin-bottom: 8px; cursor: zoom-in; }
#detail .kinds { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
#detail .kinds label { border: 1px solid #ccc; border-radius: 6px; padding: 4px 8px; cursor: pointer; }
#detail .kinds input { margin-right: 4px; }
#title { width: 100%; font: inherit; font-size: 16px; padding: 6px; direction: rtl; }
.sugg { direction: rtl; text-align: right; color: #444; cursor: pointer; padding: 3px 6px; border-radius: 4px; }
.sugg:hover { background: #eef; }
#toc { padding: 12px; direction: rtl; text-align: right; }
#toc h2 { font-size: 13px; color: #666; margin: 0 0 6px; direction: ltr; text-align: left; }
#toc div { padding: 2px 0; cursor: pointer; }
#toc .part { font-weight: 600; color: var(--part); }
#toc .section { padding-right: 18px; color: var(--section); }
#toc .page { color: #999; font-size: 12px; margin-left: 6px; }
#status { color: #666; font-size: 13px; }
#zoom { position: fixed; inset: 0; background: rgba(0,0,0,.85); display: none; align-items: center; justify-content: center; }
#zoom img { max-width: 96vw; max-height: 96vh; }
kbd { border: 1px solid #ccc; border-radius: 3px; padding: 0 4px; font-size: 12px; background: #f5f5f5; }
</style>
</head>
<body>
<header>
  <h1 id="book"></h1>
  <span class="counts" id="counts"></span>
  <span class="spacer"></span>
  <span id="status"></span>
  <button id="rebuild" class="primary">Rebuild EPUB</button>
  <button id="done">Done</button>
</header>
<main id="grid"></main>
<aside>
  <div id="detail"><em>Click a page. <kbd>←</kbd> <kbd>→</kbd> move, <kbd>Space</kbd> chapter on/off, <kbd>Enter</kbd> edit the title.</em></div>
  <div id="toc"><h2>Table of contents</h2><div id="toclist"></div></div>
</aside>
<div id="zoom"><img id="zoomimg" alt=""></div>
<script>
const KINDS = ["none", "part", "chapter", "section", "figure"];
let state = null, marks = {}, selected = null, saveTimer = null;

async function api(path, body) {
  const r = await fetch(path, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {});
  return r.json();
}
function kindOf(pdf) { const m = marks[pdf]; return m ? m.kind : "none"; }
function titleOf(pdf) { const m = marks[pdf]; return m && m.title ? m.title : ""; }

function render() {
  const grid = document.getElementById("grid");
  if (!grid.children.length) {
    for (const p of state.pages) {
      const t = document.createElement("div");
      t.className = "tile"; t.dataset.pdf = p.pdf;
      t.innerHTML = `<img loading="lazy" src="/img/${p.pdf}.jpg" alt=""><span class="num">${p.n}</span><span class="label"></span>`;
      t.onclick = () => select(p.pdf);
      grid.appendChild(t);
    }
  }
  for (const t of grid.children) {
    const pdf = +t.dataset.pdf, k = kindOf(pdf);
    t.className = "tile " + (k === "none" ? "" : k) + (pdf === selected ? " selected" : "");
    t.querySelector(".label").textContent = k === "none" ? "" : (titleOf(pdf) || k);
  }
  const n = Object.values(marks);
  document.getElementById("counts").textContent =
    `${state.pages.length} pages · ${n.filter(m => m.kind === "part").length} parts · ${n.filter(m => m.kind === "chapter").length} chapters · ` +
    `${n.filter(m => m.kind === "section").length} sections · ${n.filter(m => m.kind === "figure").length} figures`;
  renderToc();
}

function renderToc() {
  const list = document.getElementById("toclist");
  list.innerHTML = "";
  const byPdf = Object.fromEntries(state.pages.map(p => [p.pdf, p]));
  for (const p of state.pages) {
    const k = kindOf(p.pdf);
    if (k === "none" || k === "figure") continue;
    const d = document.createElement("div");
    d.className = k;
    d.innerHTML = `${titleOf(p.pdf) || "(untitled)"}<span class="page">${p.n}</span>`;
    d.onclick = () => select(p.pdf);
    list.appendChild(d);
  }
  if (!list.children.length) list.innerHTML = "<em>No openings marked yet.</em>";
}

function select(pdf) {
  selected = pdf;
  const p = state.pages.find(x => x.pdf === pdf);
  const k = kindOf(pdf);
  const d = document.getElementById("detail");
  d.innerHTML = `
    <div><b>PDF page ${p.pdf}</b> · book page ${p.n} · ${p.kind}${p.auto ? ` · the converter said: <i>${p.auto.kind}${p.auto.title ? " — " + p.auto.title : ""}</i>` : ""}</div>
    <img id="pageimg" src="/img/${p.pdf}.jpg?large" alt="">
    <div class="kinds">${KINDS.map(x => `<label><input type="radio" name="kind" value="${x}" ${x === k ? "checked" : ""}>${x}</label>`).join("")}</div>
    <input id="title" placeholder="title as it should appear in the contents" value="${esc(titleOf(pdf))}" ${k === "none" || k === "figure" ? "disabled" : ""}>
    <div id="sugg">${p.lines.map(l => `<div class="sugg" title="use as title">${esc(l)}</div>`).join("")}</div>`;
  d.querySelectorAll("input[name=kind]").forEach(r => r.onchange = () => setKind(pdf, r.value));
  d.querySelector("#title").oninput = e => { setTitle(pdf, e.target.value); };
  d.querySelectorAll(".sugg").forEach(s => s.onclick = () => {
    const cur = d.querySelector("#title");
    if (cur.disabled) setKind(pdf, "chapter");
    const t = document.getElementById("title");
    t.value = t.value ? t.value + " " + s.textContent : s.textContent;
    setTitle(pdf, t.value);
  });
  d.querySelector("#pageimg").onclick = () => { document.getElementById("zoomimg").src = `/img/${p.pdf}.jpg?large`; document.getElementById("zoom").style.display = "flex"; };
  render();
  const tile = document.querySelector(`.tile[data-pdf="${pdf}"]`);
  if (tile) tile.scrollIntoView({block: "nearest"});
}

function esc(s) { return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;"); }

function setKind(pdf, kind) {
  if (kind === "none") delete marks[pdf];
  else marks[pdf] = {kind, title: kind === "figure" ? "" : titleOf(pdf)};
  const p = state.pages.find(x => x.pdf === pdf);
  if (kind !== "none" && kind !== "figure" && !marks[pdf].title && p.auto && p.auto.title) marks[pdf].title = p.auto.title;
  select(pdf);
  if (kind !== "none" && kind !== "figure") { const t = document.getElementById("title"); t.focus(); }
  queueSave();
}
function setTitle(pdf, title) { if (marks[pdf]) { marks[pdf].title = title; render(); queueSave(); } }

function queueSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => { await api("/api/marks", {pages: marks}); setStatus("marks saved"); }, 600);
}
function setStatus(s) { document.getElementById("status").textContent = s; }

document.getElementById("rebuild").onclick = async () => {
  const b = document.getElementById("rebuild");
  b.disabled = true; setStatus("rebuilding the EPUB…");
  const r = await api("/api/build", {pages: marks});
  b.disabled = false;
  setStatus(r.ok ? `EPUB rebuilt in ${r.seconds} s → ${r.epub}` : `failed: ${r.error}`);
  if (r.ok && r.summary) alert(r.summary);
};
document.getElementById("done").onclick = async () => { await api("/api/marks", {pages: marks}); await api("/api/quit", {}); setStatus("closed — you can close this tab"); };
document.getElementById("zoom").onclick = () => { document.getElementById("zoom").style.display = "none"; };

document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" && e.key !== "Escape") { if (e.key === "Enter") e.target.blur(); return; }
  if (selected === null) return;
  const i = state.pages.findIndex(p => p.pdf === selected);
  if (e.key === "ArrowRight" && i + 1 < state.pages.length) select(state.pages[i + 1].pdf);
  else if (e.key === "ArrowLeft" && i > 0) select(state.pages[i - 1].pdf);
  else if (e.key === " ") { e.preventDefault(); setKind(selected, kindOf(selected) === "chapter" ? "none" : "chapter"); }
  else if (e.key === "Enter") { const t = document.getElementById("title"); if (t && !t.disabled) t.focus(); }
  else if (e.key === "Escape") { document.getElementById("zoom").style.display = "none"; }
});

(async () => {
  state = await api("/api/state");
  marks = {};
  for (const [k, v] of Object.entries(state.marks)) marks[+k] = {kind: v.kind, title: v.title || ""};
  document.getElementById("book").textContent = state.title;
  document.title = `${state.title} — ParisaOCR review`;
  setStatus(state.reviewed ? "marks loaded from " + state.marks_path : "showing the converter's decisions; nothing saved yet");
  render();
})();
</script>
</body>
</html>
"""
