"""`parisaocr epub` on a small made-up book (tests/book.py): chapters, footnotes, page list, text."""
import collections
import html
import re
import zipfile

import pytest

pytest.importorskip("kraken")
from book import CHAPTERS, NOTE, STAMP, make_book  # noqa: E402


def words(text):
    from parisaocr.fa_text import relaxed
    return collections.Counter(re.findall(r"\w+", relaxed(text)))


@pytest.fixture(scope="module")
def epub(tmp_path_factory):
    from parisaocr.cli import main
    tmp = tmp_path_factory.mktemp("epub")
    main(["epub", str(make_book(tmp / "book.pdf")), "--out", str(tmp / "out"), "--author", "نویسندهٔ آزمایشی", "--cpu"])
    z = zipfile.ZipFile(tmp / "out" / "book.epub")
    z.report = (tmp / "out" / "book.report.md").read_text(encoding="utf-8")
    z.tmp = tmp
    return z


def test_package(epub):
    assert epub.namelist()[0] == "mimetype" and epub.read("mimetype") == b"application/epub+zip"
    opf = epub.read("OEBPS/content.opf").decode()
    assert 'page-progression-direction="rtl"' in opf and "نویسندهٔ آزمایشی" in opf
    assert any(n.startswith("OEBPS/fonts/") for n in epub.namelist())


def test_chapters_and_page_list(epub):
    nav = epub.read("OEBPS/nav.xhtml").decode()
    toc = re.search(r'<nav epub:type="toc".*?</nav>', nav, re.S).group(0)
    for label, title, _ in CHAPTERS:
        assert title in toc
    pages = re.findall(r'href="[^"]*#page-(\d+)"', nav)
    assert {"4", "6"} <= set(pages)  # the printed page numbers of the running headers


def test_text_and_footnote(epub):
    body = "".join(epub.read(n).decode() for n in sorted(epub.namelist()) if re.match(r"OEBPS/text/ch\d+\.xhtml", n))
    assert 'epub:type="footnote"' in body and 'epub:type="noteref"' in body
    note = re.search(r'<aside epub:type="footnote".*?</aside>', body, re.S).group(0)
    assert "خاطرات" in note
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    gold = words(" ".join(p for _, _, paras in CHAPTERS for p in paras) + " " + NOTE)
    got = words(text)
    assert sum((gold & got).values()) / sum(gold.values()) > 0.95
    assert "باغ و باران 4" not in text and "۴ باغ" not in text  # the running header is not part of the text


def test_burned_in_stamp(epub):
    """The site's name stamped into every page image is left out of the text and noted in the report."""
    body = "".join(epub.read(n).decode() for n in sorted(epub.namelist()) if re.match(r"OEBPS/text/.*\.xhtml", n))
    assert STAMP.split(".")[1] not in body
    assert re.search(r"Stamp «[^»]+» burned into the scans, left out of [2-6] pages", epub.report), epub.report


def test_hand_marks_decide_the_openings(epub):
    """A reviewed marks file replaces the converter's openings: typed titles, a part, a figure page as cover."""
    import json
    from parisaocr.cli import main
    out = epub.tmp / "out"
    marks = {"pages": {"1": {"kind": "figure"}, "2": {"kind": "part", "title": "دفتر یکم"},
                       "3": {"kind": "chapter", "title": "فصل اول: باغ و باران (ویراسته)"},
                       "5": {"kind": "chapter", "title": "فصل دوم: راه شهر"},
                       "6": {"kind": "section", "title": "بازگشت به خانه"}}}
    (out / "marked.marks.json").write_text(json.dumps(marks, ensure_ascii=False), encoding="utf-8")
    main(["epub", str(epub.tmp / "book.pdf"), "--out", str(out), "--name", "marked", "--work", str(out / "book.work"), "--cpu"])
    z = zipfile.ZipFile(out / "marked.epub")
    nav = z.read("OEBPS/nav.xhtml").decode()
    assert "باغ و باران (ویراسته)" in nav and "راه شهر" in nav and "دفتر یکم" in nav and "بازگشت به خانه" in nav
    assert "OEBPS/text/cover.xhtml" in z.namelist()
    report = (out / "marked.report.md").read_text(encoding="utf-8")
    assert "5 pages marked by hand" in report
    body = "".join(re.sub(r"<title>.*?</title>", "", z.read(n).decode()) for n in z.namelist() if re.match(r"OEBPS/text/ch\d+\.xhtml", n))
    assert body.count("(ویراسته)") == 1  # the typed title is the heading, and the page's own title lines are not repeated


def test_review_page_and_api(epub, tmp_path):
    """The review server: the page, the state, saving marks, a thumbnail, a rebuild through the callback."""
    import json
    import urllib.request
    from parisaocr.ebook import review
    images = sorted((epub.tmp / "out" / "book.work" / "ocr-parisaocr-fa-0.1" / "pages").glob("p-*.png"))
    assert images
    pages = [{"pdf": i + 1, "n": i + 1, "kind": "text", "auto": {"kind": "chapter", "title": "ج"} if i == 2 else None,
              "lines": ["سطر اول", "سطر دوم"], "image": str(f)} for i, f in enumerate(images)]
    built = []
    r = review.Review("کتاب", pages, tmp_path / "m.marks.json", lambda m: built.append(m) or {"epub": "x.epub", "summary": "ok"},
                      tmp_path / "cache")
    server, url = review.start(r)
    try:
        def get(path):
            with urllib.request.urlopen(url.rstrip("/") + path) as resp:
                return resp.headers.get("Content-Type", ""), resp.read()

        def post(path, data):
            req = urllib.request.Request(url.rstrip("/") + path, json.dumps(data).encode(), {"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        assert b"ParisaOCR review" in get("/")[1]
        state = json.loads(get("/api/state")[1])
        assert state["title"] == "کتاب" and len(state["pages"]) == len(images) and state["marks"] == {"3": {"kind": "chapter", "title": "ج"}}
        assert get("/img/1.jpg")[0] == "image/jpeg" and get("/img/1.jpg?large")[0] == "image/jpeg"
        assert post("/api/marks", {"pages": {"3": {"kind": "chapter", "title": "فصل"}, "1": {"kind": "figure", "title": ""}}})["ok"]
        saved = json.loads((tmp_path / "m.marks.json").read_text(encoding="utf-8"))
        assert saved["reviewed"] and saved["pages"]["3"]["title"] == "فصل" and saved["auto"]["3"]["title"] == "ج"
        out = post("/api/build", {"pages": {"5": {"kind": "chapter", "title": "پنج"}}})
        assert out["ok"] and out["epub"] == "x.epub" and built[0]["pages"] == {5: {"kind": "chapter", "title": "پنج"}}
        assert post("/api/quit", {})["ok"] and r.done.is_set()
    finally:
        server.shutdown()
