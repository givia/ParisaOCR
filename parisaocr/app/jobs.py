"""The app's jobs: what was asked (kind, inputs, options), where its files are, and the runs of its command.

A job is a folder in the app's home: job.json, input/ (files given through the page), out/ (what the command
writes) and logs/. Each run is the same `parisaocr` command a person would type, started as its own process with
PARISAOCR_PROGRESS=1: its `@progress` lines move the job's progress, everything else goes to the run's log. One run at
a time (they share the GPU); the others wait in a queue. A cancelled run is killed with its children, and running the
job again continues where it stopped (pages read and pages labelled are kept in out/).

A book whose structure a language model decides runs twice: first with --llm-estimate (the OCR, then the expected
cost; nothing is sent), then, once the person agrees to that cost on the page, for real.
"""
import collections
import json
import os
import pathlib
import queue
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time

from . import forms

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".pbm", ".pgm", ".ppm"}
COMMAND = [sys.executable, "-m", "parisaocr"]  # what a run starts, followed by the job's arguments
ACTIVE = ("queued", "running")
TAIL = 40


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def safe_name(name):
    """A file name from the browser, reduced to its last part without control characters or leading dots."""
    name = re.split(r"[\\/]", name or "")[-1]
    name = "".join(c for c in name if c.isprintable()).strip().lstrip(".")
    return name[:150] or "file"


class Store:
    """The jobs in HOME (one folder each) and the runner that works through them."""

    def __init__(self, home):
        self.home = pathlib.Path(home).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs = {}
        for f in sorted(self.home.glob("*/job.json")):
            try:
                job = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if job.get("status") in ACTIVE:  # the app stopped while it ran or waited
                job["status"] = "interrupted"
            job["review"] = None
            self.jobs[job["id"]] = job
        self.runner = Runner(self)
        self.reviews = {}  # job id -> the review's process

    # --- jobs ---------------------------------------------------------------------------------------------------

    def dir(self, job_id):
        return self.home / job_id

    def save(self, job):
        job["updated"] = now()
        d = self.dir(job["id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, d / "job.json")

    def create(self, kind, name, options, paths):
        if kind not in forms.KINDS:
            raise ValueError(f"unknown job kind {kind!r}")
        paths = [str(pathlib.Path(p).expanduser().resolve()) for p in paths if str(p).strip()]
        for p in paths:
            if not pathlib.Path(p).exists():
                raise ValueError(f"no such file or folder: {p}")
        job_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        job = {"id": job_id, "kind": kind, "name": name or "", "created": now(), "paths": paths,
               "options": dict(options or {}), "status": "draft", "stage": None, "stages": {}, "estimate": None,
               "error": None, "runs": [], "review": None, "result": None, "tail": []}
        with self.lock:
            for sub in ("input", "out", "logs"):
                (self.dir(job_id) / sub).mkdir(parents=True, exist_ok=True)
            self.jobs[job_id] = job
            self.save(job)
        return job

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return job

    def snapshot(self, job):
        with self.lock:
            d = json.loads(json.dumps(job))
        d["inputs"] = [str(p) for p in self.inputs(job)]
        d["uploads"] = [f.name for f in self.uploads(job)]
        d["files"] = self.results(job)
        d["title"] = self.title(job)
        d["dir"] = str(self.dir(job["id"]))
        d["review_log"] = (self.dir(job["id"]) / "logs" / "review.log").exists()
        try:
            d["command"] = forms.shown(self.args(job))
        except ValueError as e:
            d["command"], d["command_error"] = None, str(e)
        return d

    def listing(self):
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True)
            return [{k: j.get(k) for k in ("id", "kind", "name", "created", "updated", "status", "stage", "error")}
                    | {"progress": j["stages"].get(j["stage"]) if j.get("stage") else None,
                       "title": self.title(j)} for j in jobs]

    def title(self, job):
        if job.get("name"):
            return job["name"]
        if (job.get("result") or {}).get("title"):
            return job["result"]["title"]
        ins = self.inputs(job)
        return ins[0].stem if len(ins) == 1 else f"{len(ins)} inputs" if ins else job["id"]

    def update(self, job_id, name=None, options=None, paths=None):
        with self.lock:
            job = self.get(job_id)
            if job["status"] in ACTIVE:
                raise ValueError("the job is running; cancel it first")
            if name is not None:
                job["name"] = name
            if options is not None:
                job["options"] = dict(options)
            if paths is not None:
                fresh = [str(pathlib.Path(p).expanduser().resolve()) for p in paths if str(p).strip()]
                for p in fresh:
                    if not pathlib.Path(p).exists():
                        raise ValueError(f"no such file or folder: {p}")
                job["paths"] = fresh
            self.save(job)
            return job

    def delete(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job["status"] in ACTIVE or job_id in self.reviews:
                raise ValueError("the job is running; cancel it first")
            del self.jobs[job_id]
        shutil.rmtree(self.dir(job_id), ignore_errors=True)  # its own folder only: paths it was given stay untouched

    def upload(self, job_id, filename, stream, length):
        """Store LENGTH bytes from STREAM as the job's input file FILENAME (renamed if taken) -> its path."""
        job = self.get(job_id)
        if job["status"] in ACTIVE:
            raise ValueError("the job is running")
        if length <= 0:
            raise ValueError(f"{filename} is empty")
        d = self.dir(job_id) / "input"
        d.mkdir(parents=True, exist_ok=True)
        name = safe_name(filename)
        stem, suffix = os.path.splitext(name)
        target, k = d / name, 2
        while target.exists():
            target, k = d / f"{stem}-{k}{suffix}", k + 1
        part = target.with_name(target.name + ".part")
        left = length
        with open(part, "wb") as f:
            while left > 0:
                chunk = stream.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            part.unlink(missing_ok=True)
            raise ValueError("the upload was cut off")
        os.replace(part, target)
        return target

    # --- what a job reads and writes -----------------------------------------------------------------------------

    def uploads(self, job):
        d = self.dir(job["id"]) / "input"
        return sorted(f for f in d.iterdir() if f.is_file() and not f.name.endswith(".part")) if d.exists() else []

    def inputs(self, job):
        """The command's inputs: the paths given, the uploaded PDF, and the input folder for uploaded images."""
        ups = self.uploads(job)
        pdfs = [f for f in ups if f.suffix.lower() == ".pdf"]
        images = [f for f in ups if f.suffix.lower() in IMAGE_SUFFIXES]
        out = [pathlib.Path(p) for p in job["paths"]] + pdfs
        if images:
            out.append(self.dir(job["id"]) / "input")
        return out

    def out(self, job):
        return self.dir(job["id"]) / "out"

    def book_name(self, job):
        ins = self.inputs(job)
        return (job["options"].get("name") or "").strip() or (ins[0].stem if ins else "book")

    def args(self, job, mode="full"):
        """The job's `parisaocr` arguments for a run: full, estimate (the expected labelling cost only) or review."""
        values, extra = dict(job["options"]), []
        last = job["runs"][-1] if job["runs"] else None
        if mode == "full" and last and last["mode"] == "estimate" and last.get("code") == 0:
            values["redo"] = False  # the estimate run started over already; this one goes on from its OCR
        if job["kind"] == "epub":
            if mode == "estimate":
                extra.append("--llm-estimate")
            elif mode == "review":
                values["redo"] = False  # the review rebuilds from what is there; it never starts the OCR over
                if values.get("llm"):  # the labels already given; pages without one are not asked again
                    from ..ebook.gemini import labels_dirname
                    labels = self.out(job) / f"{self.book_name(job)}.work" / labels_dirname(values["llm"])
                    values["llm"] = None
                    if labels.is_dir():
                        values["labels"] = str(labels)
                extra += ["--review", "--port=0"]
        return forms.command(job["kind"], self.inputs(job), self.out(job), values, extra)

    def results(self, job):
        """The job's output files the page offers, relative to the job folder."""
        out, base = self.out(job), self.dir(job["id"])
        files = []

        def add(path, kind, **extra):
            if path.exists():
                st = path.stat()
                files.append({"path": str(path.relative_to(base)), "kind": kind, "size": st.st_size if path.is_file() else None,
                              "mtime": int(st.st_mtime), **extra})

        if job["kind"] == "epub":
            name = self.book_name(job)
            add(out / f"{name}.epub", "epub")
            add(out / f"{name}.report.md", "report")
            add(out / f"{name}.marks.json", "marks")
        else:
            for fmt in ("txt", "hocr", "jsonl"):
                d = out / fmt
                if d.is_dir():
                    n = sum(1 for _ in d.glob(f"*.{fmt}"))
                    if n:
                        add(d, fmt, count=n)
            if (out / "pdf").is_dir():
                for f in sorted((out / "pdf").glob("*.pdf")):
                    add(f, "pdf")
            d = out / "pages"
            if d.is_dir():
                n = sum(1 for _ in d.glob("p-*.png"))
                if n:
                    add(d, "pages", count=n)
        return files

    def pages(self, job):
        """[(page id, image path or None)] of an OCR or pages job, in page order."""
        out = self.out(job)
        images = {}
        for p in self.inputs(job):
            if p.is_dir():
                images.update({f.stem: f for f in sorted(p.iterdir()) if f.suffix.lower() in IMAGE_SUFFIXES})
            elif p.suffix.lower() in IMAGE_SUFFIXES:
                images[p.stem] = p
        if (out / "pages").is_dir():
            images.update({f.stem: f for f in (out / "pages").glob("p-*.png")})
        ids = set(images)
        for fmt in ("txt", "jsonl"):
            if (out / fmt).is_dir():
                ids |= {f.stem for f in (out / fmt).glob(f"*.{fmt}")}
        return [(pid, images.get(pid)) for pid in sorted(ids)]

    def page_text(self, job, pid):
        out = self.out(job)
        f = out / "txt" / f"{pid}.txt"
        if f.exists():
            return f.read_text(encoding="utf-8")
        f = out / "jsonl" / f"{pid}.jsonl"
        if f.exists():
            rows = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
            return "\n".join(r.get("text", "") for r in rows) + "\n"
        return None

    # --- runs ---------------------------------------------------------------------------------------------------

    def start(self, job_id):
        """Queue the job: a book with a language model is first estimated (nothing is sent), unless it was agreed."""
        with self.lock:
            job = self.get(job_id)
            if job["status"] in ACTIVE:
                raise ValueError("the job is already running")
            if job_id in self.reviews:
                raise ValueError("the review is open; close it first")
            if not self.inputs(job):
                raise ValueError("the job has no input")
            mode = "estimate" if job["kind"] == "epub" and job["options"].get("llm") else "full"
            self.args(job, mode)  # refuses bad options before anything is queued
            job["estimate"], job["error"] = None, None
            self.queue(job, mode)

    def confirm(self, job_id, use_llm=True):
        """The person agreed to the estimated cost (or chose to go on without the language model)."""
        with self.lock:
            job = self.get(job_id)
            if job["status"] != "waiting":
                raise ValueError("nothing is waiting for an answer")
            if not use_llm:
                job["options"]["llm"] = None
            self.queue(job, "full")

    def queue(self, job, mode):
        job["status"], job["mode"], job["error"] = "queued", mode, None
        self.save(job)
        self.runner.jobs.put((job["id"], mode))

    def cancel(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job["status"] == "waiting":
                job["status"] = "cancelled"
                self.save(job)
                return
            if job["status"] not in ACTIVE:
                raise ValueError("the job is not running")
            job["status"] = "cancelled"
            self.save(job)
        self.runner.kill(job_id)

    def review(self, job_id):
        """Start the review page of a book (the same conversion with --review) -> its process; the page's address
        arrives as a progress line and is kept in job["review"]."""
        with self.lock:
            job = self.get(job_id)
            if job["kind"] != "epub":
                raise ValueError("only a book has a review")
            if job["status"] in ACTIVE:
                raise ValueError("the job is running")
            if job_id in self.reviews:
                return
            args = self.args(job, "review")
            job["review"] = {"url": None, "started": now()}
            self.save(job)
            log = self.dir(job_id) / "logs" / "review.log"
            proc = spawn(args, self.dir(job_id))
            self.reviews[job_id] = proc
        threading.Thread(target=self._follow_review, args=(job_id, proc, log), daemon=True).start()

    def _follow_review(self, job_id, proc, log):
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"\n# {now()} {forms.shown(proc.args[len(COMMAND):])}\n")
            for line in proc.stdout:
                ev = progress_event(line)
                if ev and ev["stage"] == "review":
                    with self.lock:
                        job = self.jobs.get(job_id)
                        if job is not None:
                            job["review"] = {"url": ev.get("url"), "started": now()}
                elif not ev:
                    f.write(line)
                    f.flush()
            code = proc.wait()
        with self.lock:
            self.reviews.pop(job_id, None)
            job = self.jobs.get(job_id)
            if job is not None:
                if code not in (0, -signal.SIGTERM) and not (job.get("review") or {}).get("url"):
                    job["error"] = "the review could not start (see logs/review.log)"
                job["review"] = None
                self.save(job)

    def close_review(self, job_id):
        proc = self.reviews.get(job_id)
        if proc is not None:
            kill_tree(proc)

    def shutdown(self):
        self.runner.stop()
        for proc in list(self.reviews.values()):
            kill_tree(proc)


def spawn(args, cwd):
    env = dict(os.environ, PARISAOCR_PROGRESS="1", PARISAOCR_NO_BROWSER="1", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    kw = {"start_new_session": True} if os.name == "posix" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return subprocess.Popen([*COMMAND, *args], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", bufsize=1, **kw)


def kill_tree(proc, grace=5.0):
    """End a run and everything it started (Poppler tools, worker processes)."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        pass


def progress_event(line):
    if not line.startswith("@progress "):
        return None
    try:
        ev = json.loads(line[10:])
    except ValueError:
        return None
    return ev if isinstance(ev, dict) and "stage" in ev else None


class Runner(threading.Thread):
    """Runs the queued jobs one at a time."""

    def __init__(self, store):
        super().__init__(daemon=True)
        self.store, self.jobs = store, queue.Queue()
        self.current = None  # (job id, process)
        self.stopping = False
        self.start()

    def kill(self, job_id):
        cur = self.current
        if cur and cur[0] == job_id:
            kill_tree(cur[1])

    def stop(self):
        self.stopping = True
        cur = self.current
        if cur:
            kill_tree(cur[1])
        self.jobs.put(None)

    def run(self):
        while not self.stopping:
            item = self.jobs.get()
            if item is None:
                break
            job_id, mode = item
            with self.store.lock:
                job = self.store.jobs.get(job_id)
                if job is None or job["status"] != "queued" or job.get("mode") != mode:
                    continue  # cancelled or deleted while it waited
            try:
                self.run_job(job, mode)
            except Exception as e:  # noqa: BLE001  one job's failure must not stop the runner
                with self.store.lock:
                    job["status"], job["error"] = "failed", f"{type(e).__name__}: {e}"
                    self.store.save(job)

    def run_job(self, job, mode):
        store = self.store
        with store.lock:
            args = store.args(job, mode)
            follows_estimate = mode == "full" and job["runs"] and job["runs"][-1]["mode"] == "estimate" \
                and job["runs"][-1].get("code") == 0
            if not follows_estimate:
                job["stages"], job["stage"] = {}, None
            n = len(job["runs"]) + 1
            log = store.dir(job["id"]) / "logs" / f"run-{n}.log"
            run = {"mode": mode, "cmd": forms.shown(args), "started": now(), "ended": None, "code": None,
                   "log": str(log.relative_to(store.dir(job["id"])))}
            job["runs"].append(run)
            job["status"], job["error"], job["tail"] = "running", None, []
            store.save(job)
            proc = spawn(args, store.dir(job["id"]))
            self.current = (job["id"], proc)
        tail, last_save = collections.deque(maxlen=TAIL), 0.0
        with open(log, "w", encoding="utf-8") as f:
            f.write(f"# {run['started']} {run['cmd']}\n")
            for line in proc.stdout:
                ev = progress_event(line)
                with store.lock:
                    if ev:
                        self.on_progress(job, ev)
                    else:
                        f.write(line)
                        f.flush()
                        text = line.rstrip("\n")
                        if text.strip():
                            tail.append(text[:500])
                            job["tail"] = list(tail)
                    if time.time() - last_save > 2:
                        store.save(job)
                        last_save = time.time()
            code = proc.wait()
        self.current = None
        with store.lock:
            run["ended"], run["code"] = now(), code
            if job["status"] == "cancelled":
                pass
            elif self.stopping:  # the app is closing: running the job again continues it
                job["status"] = "interrupted"
            elif code == 0:
                for st in job["stages"].values():
                    st["state"] = "done"
                if mode == "estimate":
                    est = job.get("estimate")
                    if est is None:
                        job["status"], job["error"] = "failed", "no cost estimate came back"
                    elif est.get("todo", 0) == 0:
                        store.queue(job, "full")  # every page already labelled: nothing to agree to
                        return
                    else:
                        job["status"] = "waiting"
                else:
                    job["status"] = "done"
            else:
                job["status"] = "failed"
                job["error"] = failure(list(tail), code)
            store.save(job)

    def on_progress(self, job, ev):
        stage = ev.pop("stage")
        if stage == "estimate":
            job["estimate"] = ev
            return
        if stage == "report":
            job["result"] = {k: ev.get(k) for k in ("epub", "report", "title")}
        prev = job.get("stage")
        if prev and prev != stage and prev in job["stages"]:
            job["stages"][prev]["state"] = "done"
        st = job["stages"].setdefault(stage, {"t0": time.time()})
        st.update(ev)
        st["t"] = time.time()
        total, done = st.get("total"), st.get("done")
        st["state"] = "done" if total is not None and done is not None and done >= total else "active"
        job["stage"] = stage


def failure(tail, code):
    """The message to show for a failed run: the command's own last complaint, else the end of a traceback."""
    for line in reversed(tail):
        s = line.strip()
        if s.startswith("parisaocr") or re.match(r"^\w+(Error|Exception)\b", s):
            return s[:500]
    return (tail[-1].strip()[:500] if tail else "") or f"the command stopped with exit code {code}"
