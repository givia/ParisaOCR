"""`parisaocr app`: the forms cover every command-line option, the server lets in only the page that holds its key,
and jobs run, wait for the cost to be agreed, get cancelled and come back after a restart. The runs use a stand-in
command (FAKE) except in the last test, which reads a real page."""
import http.client
import json
import os
import pathlib
import stat
import sys
import textwrap
import time
import urllib.parse
import zipfile

import pytest

from parisaocr.app import forms, jobs, server

DATA = pathlib.Path(__file__).parent / "data"

# A stand-in for `python -m parisaocr`: prints progress lines like the real command and writes its outputs.
FAKE = textwrap.dedent('''
    import json, os, pathlib, sys, time
    args = sys.argv[1:]
    def emit(**d):
        print("@progress " + json.dumps(d), file=sys.stderr, flush=True)
    out = pathlib.Path(next(a.split("=", 1)[1] for a in args if a.startswith("--out=")))
    print("fake:", " ".join(args), flush=True)
    if os.environ.get("FAKE_SLOW"):
        emit(stage="ocr", done=0, total=100)
        time.sleep(60)
    if args[0] == "epub":
        emit(stage="pages", done=3, total=3)
        emit(stage="ocr", done=3, total=3, lines=40)
        if "--llm-estimate" in args:
            emit(stage="estimate", pages=3, todo=3, cost=0.0123, model="gemini-3.8-flash", text="3 of 3 pages: about $0.01")
            sys.exit(0)
        if any(a.startswith("--llm") for a in args):
            emit(stage="llm", done=3, total=3, cost=0.0119)
        if "--review" in args:
            emit(stage="review", url="http://127.0.0.1:9/")
            time.sleep(60)
        if os.environ.get("FAKE_FAIL"):
            print("parisaocr: the fake conversion failed on purpose", flush=True)
            sys.exit(3)
        out.mkdir(parents=True, exist_ok=True)
        name = pathlib.Path(args[1]).stem
        (out / f"{name}.epub").write_bytes(b"PK fake")
        (out / f"{name}.report.md").write_text("# Conversion report\\n\\n## Structure\\n\\n- 2 chapters\\n", encoding="utf-8")
        emit(stage="report", epub=str(out / f"{name}.epub"), title="کتاب آزمایشی")
    else:
        (out / "txt").mkdir(parents=True, exist_ok=True)
        (out / "txt" / "page.txt").write_text("سلام\\n", encoding="utf-8")
        emit(stage="ocr", done=1, total=1, lines=1)
''')


def test_forms_cover_every_option():
    """Every option of ocr, epub and pages is on a form (or filled by the app), and the layout names no option the
    command line does not have."""
    for kind, cmd in forms.KINDS.items():
        assert forms.uncovered(kind) == [], kind
        dests = {a.dest for a in forms.subparser(cmd)._actions}
        assert {dest for _, dest, *_ in forms.LAYOUT[kind]} <= dests, kind
        assert forms.MANAGED[kind] <= dests, kind
        for section in forms.schema(kind):
            for f in section["fields"]:
                assert f["label_fa"] and f["label_en"] and f["flag"].startswith("--"), f


def test_command_from_form_values():
    args = forms.command("epub", ["/b/book.pdf"], "/o", {"llm": "gemini-3.8-flash", "title": "کتاب", "dpi": None,
                                                         "block_tall": True, "fallback": False, "cpu": True, "jobs": None,
                                                         "tables": "image"})
    assert args == ["epub", "/b/book.pdf", "--out=/o", "--title=کتاب", "--llm", "--cpu", "--no-fallback"]
    args = forms.command("epub", ["/b/book.pdf"], "/o", {"llm": "openrouter:qwen/qwen3.8-27b", "llm_jobs": 4})
    assert "--llm=openrouter:qwen/qwen3.8-27b" in args and "--llm-jobs=4" in args
    args = forms.command("ocr", ["/a.png", "/d"], "/o", {"format": "txt,pdf", "first": 3, "order": "rtl", "pdf_text": "add"})
    assert args == ["ocr", "/a.png", "/d", "--out=/o", "--format=txt,pdf", "--pdf-text=add", "--first=3"]
    assert forms.command("pages", ["/b.pdf"], "/o", {"pdf_mode": "render"}) == ["pages", "/b.pdf", "--out=/o/pages", "--pdf-mode=render"]
    for kind, inputs, values in (("ocr", ["/a.png"], {"order": "zigzag"}), ("epub", ["/a.pdf", "/b.pdf"], {}),
                                 ("ocr", ["-x.png"], {}), ("epub", ["/a.pdf"], {"llm_jobs": "many"})):
        with pytest.raises(ValueError):
            forms.command(kind, inputs, "/o", values)
    assert forms.shown(["epub", "/my books/a.pdf"]) == "parisaocr epub '/my books/a.pdf'"


def test_progress_lines(monkeypatch, capsys):
    from parisaocr import progress
    monkeypatch.delenv("PARISAOCR_PROGRESS", raising=False)
    progress.emit("ocr", 1, 2)
    assert capsys.readouterr().err == ""
    monkeypatch.setenv("PARISAOCR_PROGRESS", "1")
    progress.emit("ocr", 1, 2, lines=5, path=pathlib.Path("/x"))
    line = capsys.readouterr().err.strip()
    assert jobs.progress_event(line) == {"stage": "ocr", "done": 1, "total": 2, "lines": 5, "path": "/x"}
    with progress.renamed("ocr", "figures"):  # the figure pages read again are not the book's OCR
        progress.emit("ocr", 4, 4)
    progress.emit("ocr", 2, 2)
    assert [jobs.progress_event(l)["stage"] for l in capsys.readouterr().err.splitlines()] == ["figures", "ocr"]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    script = tmp_path / "fake_parisaocr.py"
    script.write_text(FAKE, encoding="utf-8")
    monkeypatch.setattr(jobs, "COMMAND", [sys.executable, str(script)])
    monkeypatch.delenv("FAKE_SLOW", raising=False)
    monkeypatch.delenv("FAKE_FAIL", raising=False)
    return script


def wait(store, job_id, statuses, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        job = store.get(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.1)
    raise AssertionError(f"{job_id} stayed {store.get(job_id)['status']}")


def test_book_with_a_language_model_waits_for_the_cost(fake, tmp_path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    store = jobs.Store(tmp_path / "home")
    try:
        job = store.create("epub", "", {"llm": "gemini-3.8-flash", "redo": True}, [str(pdf)])
        store.start(job["id"])
        job = wait(store, job["id"], {"waiting", "failed"})
        assert job["status"] == "waiting", job.get("error")
        assert job["estimate"]["todo"] == 3 and job["estimate"]["cost"] == pytest.approx(0.0123)
        assert job["runs"][0]["mode"] == "estimate" and "--llm-estimate" in job["runs"][0]["cmd"]
        assert store.results(job) == []  # nothing converted, nothing sent
        store.confirm(job["id"])
        job = wait(store, job["id"], {"done", "failed"})
        assert job["status"] == "done", job.get("error")
        assert "--llm" in job["runs"][1]["cmd"] and "--llm-estimate" not in job["runs"][1]["cmd"]
        assert "--redo" in job["runs"][0]["cmd"] and "--redo" not in job["runs"][1]["cmd"]  # the OCR is not read twice
        assert job["stages"]["llm"]["cost"] == pytest.approx(0.0119) and job["stages"]["ocr"]["state"] == "done"
        assert job["result"]["title"] == "کتاب آزمایشی"
        snap = store.snapshot(job)
        assert [f["kind"] for f in snap["files"]] == ["epub", "report"] and snap["title"] == "کتاب آزمایشی"
        # the review never asks the model again: the labels already given are used instead
        labels = store.out(job) / "book.work" / "gemini-gemini-3.8-flash"
        labels.mkdir(parents=True)
        review = store.args(job, "review")
        assert f"--labels={labels}" in review and "--review" in review and not any(a.startswith("--llm") for a in review)
    finally:
        store.shutdown()


def test_going_on_without_the_model_and_failures(fake, tmp_path, monkeypatch):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    store = jobs.Store(tmp_path / "home")
    try:
        job = store.create("epub", "", {"llm": "gemini-3.8-flash"}, [str(pdf)])
        store.start(job["id"])
        wait(store, job["id"], {"waiting"})
        monkeypatch.setenv("FAKE_FAIL", "1")
        store.confirm(job["id"], use_llm=False)
        job = wait(store, job["id"], {"done", "failed"})
        assert job["status"] == "failed" and job["options"]["llm"] is None
        assert job["error"] == "parisaocr: the fake conversion failed on purpose"
        assert "--llm" not in job["runs"][-1]["cmd"]
    finally:
        store.shutdown()


def test_cancel_and_restart(fake, tmp_path, monkeypatch):
    img = tmp_path / "page.png"
    img.write_bytes(b"not really a png")
    monkeypatch.setenv("FAKE_SLOW", "1")
    home = tmp_path / "home"
    store = jobs.Store(home)
    try:
        a = store.create("ocr", "slow", {"format": "txt"}, [str(img)])
        b = store.create("ocr", "next", {"format": "txt"}, [str(img)])
        store.start(a["id"])
        store.start(b["id"])
        wait(store, a["id"], {"running"})
        end = time.time() + 10
        while not store.get(a["id"])["stages"] and time.time() < end:
            time.sleep(0.1)
        assert store.get(b["id"])["status"] == "queued"  # one run at a time
        proc = store.runner.current[1]
        store.cancel(b["id"])  # a queued job is dropped before it runs
        store.cancel(a["id"])
        assert wait(store, a["id"], {"cancelled"})["stages"]["ocr"]["total"] == 100
        proc.wait(10)
        assert proc.returncode is not None  # the run was killed, not left behind
        assert store.get(b["id"])["status"] == "cancelled" and not store.get(b["id"])["runs"]
        with pytest.raises(ValueError):
            store.cancel(a["id"])
        monkeypatch.delenv("FAKE_SLOW")
        store.start(a["id"])
        assert wait(store, a["id"], {"done", "failed"})["status"] == "done"
        assert len(store.get(a["id"])["runs"]) == 2
        # a job that was running when the app stopped is marked interrupted when it starts again
        c = store.create("ocr", "", {"format": "txt"}, [str(img)])
        c["status"] = "running"
        store.save(c)
    finally:
        store.shutdown()
    again = jobs.Store(home)
    try:
        assert again.get(c["id"])["status"] == "interrupted"
        assert again.get(a["id"])["status"] == "done" and again.get(a["id"])["runs"][0]["mode"] == "full"
        again.delete(a["id"])
        assert not (home / a["id"]).exists() and img.exists()  # the job's folder goes; the input it was given stays
    finally:
        again.shutdown()


def test_uploads_and_inputs(tmp_path):
    import io
    store = jobs.Store(tmp_path / "home")
    try:
        job = store.create("ocr", "", {}, [])
        data = b"x" * 3000
        assert store.upload(job["id"], "../../evil.png", io.BytesIO(data), len(data)).name == "evil.png"
        assert store.upload(job["id"], "C:\\scans\\evil.png", io.BytesIO(data), len(data)).name == "evil-2.png"
        assert store.upload(job["id"], "book.pdf", io.BytesIO(data), len(data)).name == "book.pdf"
        with pytest.raises(ValueError):
            store.upload(job["id"], "cut.png", io.BytesIO(b"short"), 100)
        with pytest.raises(ValueError):
            store.upload(job["id"], "empty.png", io.BytesIO(b""), 0)
        d = store.dir(job["id"]) / "input"
        assert sorted(p.name for p in d.iterdir()) == ["book.pdf", "evil-2.png", "evil.png"]
        assert store.inputs(store.get(job["id"])) == [d / "book.pdf", d]  # the PDF, and the folder of images
        with pytest.raises(ValueError):
            store.create("ocr", "", {}, [str(tmp_path / "missing.pdf")])
        with pytest.raises(ValueError):
            store.create("cut", "", {}, [])
    finally:
        store.shutdown()


def test_api_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert server.key_status()["gemini"] == {**server.key_status()["gemini"], "env": False, "file": False}
    server.save_key("gemini", "  AIza-test_key ")
    f = tmp_path / ".config" / "gemini" / "api_key"
    assert f.read_text() == "AIza-test_key\n" and stat.S_IMODE(f.stat().st_mode) == 0o600
    assert server.key_status()["gemini"]["file"] is True
    for bad in ("two words", "line\nbreak"):
        with pytest.raises(ValueError):
            server.save_key("gemini", bad)
    with pytest.raises(ValueError):
        server.save_key("elsewhere", "key")
    server.save_key("gemini", "")
    assert not f.exists()


def test_epub_contents(tmp_path):
    epub = tmp_path / "b.epub"
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                   '<item id="nav" href="nav.xhtml" properties="nav"/><item id="c1" href="text/ch01.xhtml"/>'
                   '<item id="c2" href="text/ch02.xhtml"/></manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        z.writestr("OEBPS/nav.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                   '<nav epub:type="toc"><ol><li><a href="text/ch01.xhtml">فصل اول</a><ol><li><a href="text/ch01.xhtml#s1">بخش</a></li></ol></li>'
                   '<li><a href="text/ch02.xhtml">فصل دوم</a></li></ol></nav><nav epub:type="page-list"><ol><li><a href="text/ch01.xhtml#p1">1</a></li></ol></nav></body></html>')
    d = server.epub_toc(epub)
    assert d["spine"] == ["OEBPS/text/ch01.xhtml", "OEBPS/text/ch02.xhtml"]
    assert [(x["title"], x["href"], x["depth"]) for x in d["toc"]] == [
        ("فصل اول", "OEBPS/text/ch01.xhtml", 0), ("بخش", "OEBPS/text/ch01.xhtml#s1", 1), ("فصل دوم", "OEBPS/text/ch02.xhtml", 0)]


@pytest.fixture
def app(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(server.App, "_probe_device", lambda self: setattr(self, "device", {"cuda": False, "name": None}))
    a = server.App(tmp_path / "home")
    srv, url = server.start(a)
    a.port, a.url = srv.server_address[1], url
    yield a
    a.store.shutdown()
    srv.shutdown()


def request(app, method, path, body=None, cookie=True, host=None, header=True, raw=None):
    c = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    h = {"Host": host or f"127.0.0.1:{app.port}"}
    if cookie:
        h["Cookie"] = f"parisaocr_app_{app.port}={app.token}"
    if header and method == "POST":
        h["X-ParisaOCR"] = "1"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, dict(r.getheaders()), r.read()
    c.close()
    return out


def test_server_lets_in_only_the_page_with_the_key(app):
    assert request(app, "GET", "/api/jobs", cookie=False)[0] == 403
    status, headers, _ = request(app, "GET", "/?token=" + urllib.parse.quote(app.token), cookie=False)
    assert status == 303 and f"parisaocr_app_{app.port}={app.token}" in headers["Set-Cookie"]
    assert "SameSite=Strict" in headers["Set-Cookie"] and "HttpOnly" in headers["Set-Cookie"]
    assert request(app, "GET", "/?token=wrong", cookie=False)[0] == 403
    assert request(app, "GET", "/api/jobs")[0] == 200
    assert request(app, "GET", "/api/jobs", host="attacker.example:80")[0] == 403  # DNS rebinding
    assert request(app, "POST", "/api/jobs", {"kind": "ocr"}, header=False)[0] == 403  # cross-site form
    assert request(app, "POST", "/api/keys", {"service": "gemini", "key": ""}, cookie=False)[0] == 403
    assert request(app, "GET", "/api/ping", cookie=False)[0] == 200
    status, headers, body = request(app, "GET", "/")
    assert status == 200 and b"/static/app.js" in body
    assert request(app, "GET", "/static/app.js")[0] == 200
    assert request(app, "GET", "/static/fonts/Vazirmatn-Regular.ttf")[0] == 200
    for path in ("/static/../server.py", "/static/fonts/../../cli.py"):
        assert request(app, "GET", path)[0] == 404


def test_server_runs_a_job(app, tmp_path):
    status, _, body = request(app, "POST", "/api/jobs", {"kind": "ocr", "name": "صفحه", "options": {"format": "txt"}})
    job = json.loads(body)
    assert status == 200 and job["status"] == "draft" and job["command"] is None
    status, _, body = request(app, "POST", f"/api/jobs/{job['id']}/upload?name=" + urllib.parse.quote("صفحه ۱.png"), raw=b"fake image")
    assert status == 200 and json.loads(body)["name"] == "صفحه ۱.png"
    status, _, body = request(app, "POST", f"/api/jobs/{job['id']}/start", {})
    assert status == 200, body
    end = time.time() + 30
    while time.time() < end:
        snap = json.loads(request(app, "GET", f"/api/jobs/{job['id']}")[2])
        if snap["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert snap["status"] == "done", snap
    assert [f["kind"] for f in snap["files"]] == ["txt"]
    assert json.loads(request(app, "GET", f"/api/jobs/{job['id']}/text/page")[2])["text"] == "سلام\n"
    status, headers, body = request(app, "GET", f"/api/jobs/{job['id']}/file/out/txt/page.txt?download=1")
    assert status == 200 and body.decode() == "سلام\n" and "attachment" in headers["Content-Disposition"]
    status, _, body = request(app, "GET", f"/api/jobs/{job['id']}/zip/out/txt")
    assert status == 200 and zipfile.ZipFile(__import__("io").BytesIO(body)).namelist() == ["page.txt"]
    for path in ("file/job.json/../../../etc/passwd", "file/%2Fetc%2Fpasswd", "file/..%2F..%2Fx"):
        assert request(app, "GET", f"/api/jobs/{job['id']}/{path}")[0] == 404
    log = json.loads(request(app, "GET", f"/api/jobs/{job['id']}/log")[2])["text"]
    assert "fake: ocr" in log and "@progress" not in log
    r = json.loads(request(app, "POST", "/api/command", {"kind": "ocr", "options": {"order": "zigzag"}, "uploads": ["a.png"]})[2])
    assert r["ok"] is False and "zigzag" in r["error"]
    r = json.loads(request(app, "POST", "/api/command", {"kind": "epub", "options": {"llm": "gemini-3.8-flash"}, "uploads": ["کتاب.pdf"]})[2])
    assert r == {"ok": True, "command": "parisaocr epub 'کتاب.pdf' --out=OUT --llm"}
    assert request(app, "GET", "/api/jobs/nope")[0] == 404
    assert json.loads(request(app, "POST", f"/api/jobs/{job['id']}/delete", {})[2])["ok"]
    assert json.loads(request(app, "GET", "/api/jobs")[2]) == []


def test_app_command_line():
    from parisaocr.cli import parser
    o = parser().parse_args(["app", "--home", "/tmp/h", "--port", "8123", "--no-browser"])
    assert (o.home, o.port, o.no_browser) == ("/tmp/h", 8123, True)


def test_real_ocr_job(tmp_path):
    """The real command, run by the app's runner on one page."""
    pytest.importorskip("kraken")
    store = jobs.Store(tmp_path / "home")
    try:
        job = store.create("ocr", "", {"format": "txt,jsonl", "cpu": True}, [str(DATA / "sahel-200dpi.png")])
        store.start(job["id"])
        job = wait(store, job["id"], {"done", "failed"}, timeout=300)
        assert job["status"] == "done", (job.get("error"), job.get("tail"))
        assert job["stages"]["ocr"]["state"] == "done" and job["stages"]["ocr"]["total"] == 1
        text = store.page_text(job, "sahel-200dpi")
        assert text and len(text.split()) > 50
        assert [p for p, img in store.pages(job)] == ["sahel-200dpi"]
    finally:
        store.shutdown()
