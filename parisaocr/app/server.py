"""`parisaocr app`: the whole process on a local web page.

The server binds 127.0.0.1 only, so the scans and page images never leave the machine (except what a language model
is sent, after the person agrees to its cost). It runs the same commands as the command line, through `jobs.Store`.

Other web pages open in the same browser must not be able to drive it: the address it prints carries a random key,
which the first visit turns into a cookie (SameSite=Strict, so other sites' requests go without it); every request
must carry that cookie and name this server as its Host (a page whose domain is made to resolve to 127.0.0.1 sends
its own name), and every POST must carry the X-ParisaOCR header, which a cross-site request cannot set.
"""
import http.server
import json
import mimetypes
import os
import pathlib
import posixpath
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
import zipfile

from .. import __version__
from . import forms, jobs

STATIC = pathlib.Path(__file__).resolve().parent / "static"
FONTS = pathlib.Path(__file__).resolve().parent.parent / "fonts"
SERVICES = {"gemini": ("GEMINI_API_KEY", "https://aistudio.google.com/apikey"),
            "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/settings/keys")}
TYPES = {".xhtml": "application/xhtml+xml", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".html": "text/html; charset=utf-8", ".ttf": "font/ttf",
         ".otf": "font/otf", ".woff2": "font/woff2", ".svg": "image/svg+xml", ".opf": "application/xml",
         ".ncx": "application/xml", ".md": "text/markdown; charset=utf-8", ".json": "application/json",
         ".txt": "text/plain; charset=utf-8", ".hocr": "text/html; charset=utf-8", ".jsonl": "text/plain; charset=utf-8"}
XHTML_NS, EPUB_NS = "http://www.w3.org/1999/xhtml", "http://www.idpf.org/2007/ops"


def key_file(service):
    return pathlib.Path.home() / ".config" / service / "api_key"


def key_status():
    return {s: {"env": bool(os.environ.get(env, "").strip()), "file": key_file(s).is_file(), "env_name": env,
                "file_path": str(key_file(s)), "url": url} for s, (env, url) in SERVICES.items()}


def save_key(service, key):
    """Write (or with an empty KEY remove) ~/.config/SERVICE/api_key, readable by the person only."""
    if service not in SERVICES:
        raise ValueError(f"unknown service {service!r}")
    f = key_file(service)
    key = (key or "").strip()
    if not key:
        f.unlink(missing_ok=True)
        return
    if not re.fullmatch(r"[!-~]+", key):
        raise ValueError("a key is one line of printable characters without spaces")
    f.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(key + "\n")
    os.chmod(f, 0o600)


def epub_toc(epub):
    """The contents of an EPUB from its navigation document: [{title, href, depth}] and the reading order."""
    with zipfile.ZipFile(epub) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
        opf_path = next(e.get("full-path") for e in container.iter() if e.tag.endswith("rootfile"))
        base = posixpath.dirname(opf_path)
        opf = ET.fromstring(z.read(opf_path))
        items = {e.get("id"): e for e in opf.iter() if e.tag.endswith("}item")}
        spine = [posixpath.join(base, items[e.get("idref")].get("href")) for e in opf.iter()
                 if e.tag.endswith("}itemref") and e.get("idref") in items]
        nav_item = next((e for e in items.values() if "nav" in (e.get("properties") or "").split()), None)
        toc = []
        if nav_item is not None:
            nav_path = posixpath.join(base, nav_item.get("href"))
            nav_dir = posixpath.dirname(nav_path)
            doc = ET.fromstring(z.read(nav_path))
            nav = next((n for n in doc.iter(f"{{{XHTML_NS}}}nav") if n.get(f"{{{EPUB_NS}}}type") == "toc"), None)

            def walk(ol, depth):
                for li in ol.findall(f"{{{XHTML_NS}}}li"):
                    a = li.find(f"{{{XHTML_NS}}}a")
                    if a is not None:
                        href = posixpath.normpath(posixpath.join(nav_dir, a.get("href", "")))
                        toc.append({"title": "".join(a.itertext()).strip(), "href": href, "depth": depth})
                    sub = li.find(f"{{{XHTML_NS}}}ol")
                    if sub is not None:
                        walk(sub, depth + 1)

            if nav is not None and nav.find(f"{{{XHTML_NS}}}ol") is not None:
                walk(nav.find(f"{{{XHTML_NS}}}ol"), 0)
    return {"toc": toc, "spine": spine}


def reveal(folder):
    """Open FOLDER in the system's file manager."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    elif os.name == "nt":
        os.startfile(str(folder))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class App:
    def __init__(self, home):
        self.store = jobs.Store(home)
        self.token = secrets.token_urlsafe(24)
        self.done = threading.Event()
        self.device = None
        self.schemas = {k: forms.schema(k) for k in forms.KINDS}
        threading.Thread(target=self._probe_device, daemon=True).start()

    def _probe_device(self):
        """GPU or CPU, asked in a child process so that the app itself never loads torch."""
        code = ("import json, torch; ok = torch.cuda.is_available(); "
                "print(json.dumps({'cuda': ok, 'name': torch.cuda.get_device_name(0) if ok else None}))")
        try:
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
            self.device = json.loads(out.stdout.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            self.device = {"cuda": False, "name": None, "unknown": True}

    def info(self):
        return {"version": __version__, "home": str(self.store.home), "device": self.device, "keys": key_status(),
                "llm_presets": forms.LLM_PRESETS, "platform": sys.platform}


class Handler(http.server.BaseHTTPRequestHandler):
    app = None  # set by `start`
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # --- plumbing -----------------------------------------------------------------------------------------------

    @property
    def cookie_name(self):
        return f"parisaocr_app_{self.server.server_address[1]}"  # cookies are per host, not per port

    def _host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _cookie_ok(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == self.cookie_name and secrets.compare_digest(v, self.app.token):
                return True
        return False

    def _send(self, body, kind="application/json; charset=utf-8", code=200, headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message, code=400):
        self._send({"ok": False, "error": message}, code=code)

    def _file(self, path, kind=None, download=False, name=None):
        path = pathlib.Path(path)
        size = path.stat().st_size
        kind = kind or TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if download:
            n = name or path.name
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(n)}")
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1 << 20)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}

    def _job_path(self, job_id, rel):
        """A path inside the job's folder, or None (no climbing out of it)."""
        base = self.app.store.dir(job_id).resolve()
        p = (base / urllib.parse.unquote(rel)).resolve()
        return p if p == base or base in p.parents else None

    # --- requests -----------------------------------------------------------------------------------------------

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        if not self._host_ok():
            return self._send("forbidden", "text/plain", 403)
        if path == "/api/ping":
            return self._send({"app": "parisaocr"})
        if path == "/" and query.get("token") and secrets.compare_digest(query["token"][0], self.app.token):
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"{self.cookie_name}={self.app.token}; Path=/; HttpOnly; SameSite=Strict")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not self._cookie_ok():
            return self._send(LOCKED, "text/html; charset=utf-8", 403)
        try:
            self.route_get(path, query)
        except KeyError:
            self._error("no such job", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001  the page shows it; the server stays up
            self._error(f"{type(e).__name__}: {e}", 500)

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        if not (self._host_ok() and self._cookie_ok() and self.headers.get("X-ParisaOCR") == "1"):
            return self._send("forbidden", "text/plain", 403)
        try:
            self.route_post(path, query)
        except KeyError:
            self._error("no such job", 404)
        except ValueError as e:
            self._error(str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            self._error(f"{type(e).__name__}: {e}", 500)

    def route_get(self, path, query):
        app, store = self.app, self.app.store
        if path == "/":
            return self._file(STATIC / "index.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            f = (FONTS / name[len("fonts/"):]) if name.startswith("fonts/") else STATIC / name
            if "/" in name.removeprefix("fonts/") or not f.is_file():
                return self._send("not found", "text/plain", 404)
            return self._file(f)
        if path == "/api/info":
            return self._send(app.info())
        if path == "/api/schema":
            return self._send(app.schemas)
        if path == "/api/jobs":
            return self._send(store.listing())
        m = re.fullmatch(r"/api/jobs/([\w-]+)(?:/(.*))?", path)
        if not m:
            return self._send("not found", "text/plain", 404)
        job_id, rest = m.group(1), m.group(2) or ""
        job = store.get(job_id)
        if rest == "":
            return self._send(store.snapshot(job))
        if rest == "log":
            runs = job["runs"]
            name = query.get("name", [None])[0]
            if name == "review":
                f = store.dir(job_id) / "logs" / "review.log"
            elif runs:
                k = int(query.get("run", [len(runs)])[0])
                f = store.dir(job_id) / runs[max(1, min(k, len(runs))) - 1]["log"]
            else:
                return self._send({"text": "", "size": 0})
            data = f.read_bytes() if f.exists() else b""
            return self._send({"text": data[-262144:].decode("utf-8", "replace"), "size": len(data)})
        if rest.startswith("file/"):
            p = self._job_path(job_id, rest[5:])
            if p is None or not p.is_file():
                return self._send("not found", "text/plain", 404)
            return self._file(p, download="download" in query)
        if rest.startswith("zip/"):
            p = self._job_path(job_id, rest[4:])
            if p is None or not p.is_dir():
                return self._send("not found", "text/plain", 404)
            return self._zip(p, f"{store.title(job)}-{p.name}.zip")
        if rest == "pages":
            return self._send([{"pid": pid, "image": img is not None} for pid, img in store.pages(job)])
        m = re.fullmatch(r"page/([\w.-]+)\.jpg", rest)
        if m:
            return self._page_image(job, m.group(1), int(query.get("w", ["1000"])[0]))
        m = re.fullmatch(r"text/([\w.-]+)", rest)
        if m:
            text = store.page_text(job, m.group(1))
            return self._send({"text": text})
        if rest == "text-all":
            parts = []
            for pid, _ in store.pages(job):
                t = store.page_text(job, pid)
                if t is not None:
                    parts.append(f"# {pid}\n{t}")
            return self._send("\n".join(parts), "text/plain; charset=utf-8",
                              headers=[("Content-Disposition", "attachment; filename*=UTF-8''"
                                        + urllib.parse.quote(f"{store.title(job)}.txt"))])
        if rest in ("epub-toc",) or rest.startswith("epub/"):
            f = next((store.dir(job_id) / x["path"] for x in store.results(job) if x["kind"] == "epub"), None)
            if f is None:
                return self._send("not found", "text/plain", 404)
            if rest == "epub-toc":
                return self._send(epub_toc(f))
            inner = urllib.parse.unquote(rest[5:])
            try:
                with zipfile.ZipFile(f) as z:
                    data = z.read(inner)
            except KeyError:
                return self._send("not found", "text/plain", 404)
            kind = TYPES.get(posixpath.splitext(inner)[1].lower()) or mimetypes.guess_type(inner)[0] or "application/octet-stream"
            return self._send(data, kind)
        return self._send("not found", "text/plain", 404)

    def _zip(self, folder, name):
        with tempfile.TemporaryFile() as tmp:
            with zipfile.ZipFile(tmp, "w") as z:
                for f in sorted(folder.rglob("*")):
                    if f.is_file():
                        stored = f.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".epub")
                        z.write(f, f.relative_to(folder), compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
            size = tmp.tell()
            tmp.seek(0)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}")
            self.end_headers()
            shutil.copyfileobj(tmp, self.wfile, 1 << 20)

    def _page_image(self, job, pid, width):
        from PIL import Image
        width = max(100, min(width, 2400))
        img = dict(self.app.store.pages(job)).get(pid)
        if img is None:
            return self._send("not found", "text/plain", 404)
        cache = self.app.store.dir(job["id"]) / ".cache"
        cache.mkdir(exist_ok=True)
        f = cache / f"{pid}-{width}.jpg"
        if not f.exists() or f.stat().st_mtime < img.stat().st_mtime:
            with Image.open(img) as im:
                im = im.convert("L") if im.mode in ("1", "I;16", "I") else im
                im.thumbnail((width, width * 4))
                im.convert("RGB").save(f, "JPEG", quality=82)
        return self._file(f, "image/jpeg")

    def route_post(self, path, query):
        app, store = self.app, self.app.store
        if path == "/api/command":
            b = self._body()
            paths = list(b.get("paths") or []) + list(b.get("uploads") or [])
            kind = b.get("kind")
            if kind not in forms.KINDS:
                raise ValueError("unknown kind")
            out = pathlib.Path("OUT")
            try:
                args = forms.command(kind, paths or ["INPUT"], out, b.get("options") or {})
            except ValueError as e:
                return self._send({"ok": False, "error": str(e)})
            return self._send({"ok": True, "command": forms.shown(args)})
        if path == "/api/jobs":
            b = self._body()
            job = store.create(b.get("kind"), b.get("name") or "", b.get("options") or {}, b.get("paths") or [])
            return self._send(store.snapshot(job))
        if path == "/api/keys":
            b = self._body()
            save_key(b.get("service"), b.get("key"))
            return self._send({"ok": True, "keys": key_status()})
        if path == "/api/quit":
            self._send({"ok": True})
            app.done.set()
            return
        m = re.fullmatch(r"/api/jobs/([\w-]+)/([\w-]+)", path)
        if not m:
            return self._send("not found", "text/plain", 404)
        job_id, action = m.groups()
        job = store.get(job_id)
        if action == "upload":
            n = int(self.headers.get("Content-Length") or 0)
            f = store.upload(job_id, query.get("name", ["file"])[0], self.rfile, n)
            return self._send({"ok": True, "name": f.name})
        if action == "update":
            b = self._body()
            store.update(job_id, b.get("name"), b.get("options"), b.get("paths"))
        elif action == "remove-input":
            name = jobs.safe_name(self._body().get("name"))
            if job["status"] in jobs.ACTIVE:
                raise ValueError("the job is running")
            (store.dir(job_id) / "input" / name).unlink(missing_ok=True)
        elif action == "start":
            store.start(job_id)
        elif action == "confirm":
            store.confirm(job_id, bool(self._body().get("llm", True)))
        elif action == "cancel":
            store.cancel(job_id)
        elif action == "review":
            store.review(job_id)
        elif action == "review-close":
            store.close_review(job_id)
        elif action == "delete":
            store.delete(job_id)
            return self._send({"ok": True})
        elif action == "reveal":
            folder = store.out(job)
            reveal(folder if folder.exists() else store.dir(job_id))
        else:
            return self._send("not found", "text/plain", 404)
        return self._send({"ok": True, "job": store.snapshot(store.get(job_id))})


LOCKED = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>ParisaOCR</title>
<style>body{font:16px/1.6 system-ui,sans-serif;max-width:40em;margin:15vh auto;padding:0 16px;color:#222}
p[dir=rtl]{font-family:Vazirmatn,Tahoma,sans-serif}</style></head><body>
<p dir="rtl">این صفحه کلید ندارد. نشانی‌ای را باز کنید که <code>parisaocr app</code> در ترمینال چاپ کرده است
(کلید در همان نشانی است). اگر برنامه را دوباره اجرا کرده‌اید، نشانی تازه را باز کنید.</p>
<p>This page has no key. Open the address that <code>parisaocr app</code> printed in the terminal (it carries the key).
If you started the app again, open the new address.</p></body></html>"""


def start(app, port=0):
    """The app's server on 127.0.0.1:PORT in a daemon thread -> (server, address with the key)."""
    Handler.app = app
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/?token={app.token}"


def running(home):
    """The address of an app already serving HOME, if one answers."""
    f = pathlib.Path(home) / ".app.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        port = urllib.parse.urlsplit(d["url"]).port
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
            if json.loads(r.read()).get("app") == "parisaocr":
                return d["url"]
    except Exception:  # noqa: BLE001
        return None
    return None


def main(home=None, port=0, open_browser=True):
    home = pathlib.Path(home or os.environ.get("PARISAOCR_HOME") or pathlib.Path.home() / "ParisaOCR").expanduser().resolve()
    url = running(home)
    if url:
        print(f"parisaocr: the app is already running for {home}: {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        return
    app = App(home)
    server, url = start(app, port)
    marker = home / ".app.json"
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # holds the key: the person's eyes only
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"url": url, "pid": os.getpid(), "started": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
    print(f"parisaocr: app at {url}\nparisaocr: jobs and their files in {home}; Ctrl-C (or Quit on the page) stops it",
          flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        while not app.done.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    print("parisaocr: stopping the app", flush=True)
    app.store.shutdown()
    server.shutdown()
    marker.unlink(missing_ok=True)
