"""The review panel (`parisaocr epub --review`) on a small made-up book (tests/book.py): every OCR line gets the
converter's decision; a person's corrections (roles, levels, paragraph starts, note numbers, markers, texts, page types,
pictures, the printed page number, the book's data and cover) decide the next conversion, can be undone and reverted, and
are found again after a new OCR; the issues come least sure first; the panel's server serves and saves all of it."""
import argparse
import json
import re
import urllib.error
import urllib.request
import zipfile

import pytest

pytest.importorskip("kraken")
from book import make_book  # noqa: E402

from parisaocr.ebook import corrections, decisions, issues, labels, marks as marks_mod, review, source  # noqa: E402


@pytest.fixture(scope="module")
def book(tmp_path_factory):
    from parisaocr import cli
    from parisaocr.ebook.convert import convert
    tmp = tmp_path_factory.mktemp("review")
    pdf = make_book(tmp / "book.pdf")
    opts = cli.llm_options(cli.parser().parse_args(["epub", str(pdf), "--out", str(tmp / "out"), "--cpu"]))
    opts.confidence = True
    defaults = vars(cli.parser().parse_args(["ocr", "-"]))

    def read(inputs, out, formats):
        o = argparse.Namespace(**defaults)
        for k in cli.ENGINE_KEYS:
            setattr(o, k, getattr(opts, k))
        o.input, o.out, o.format = list(inputs), str(out), formats
        cli.cmd_ocr(o)

    def run(marks=None):
        return convert(opts, read, marks=marks)

    return argparse.Namespace(tmp=tmp, run=run, res=run())


@pytest.fixture(autouse=True)
def clean(book):
    """Every test starts from the converter's own decisions."""
    for f in (book.res["corrections_path"], book.res["marks_path"]):
        f.unlink(missing_ok=True)
    yield
    for f in (book.res["corrections_path"], book.res["marks_path"]):
        f.unlink(missing_ok=True)


def text_of(res):
    z = zipfile.ZipFile(res["epub"])
    return "".join(z.read(n).decode() for n in sorted(z.namelist()) if n.startswith("OEBPS/text/"))


def page4(res):
    """Page 4: a running header, body lines, and a footnote linked from a marker in the text."""
    d = decisions.collect(res["layouts"], res["book"], res["ordered"])[4]
    rows = source.rows(res["ocr_dir"], 4)
    return d, rows


def test_every_line_has_a_decision(book):
    dec = decisions.collect(book.res["layouts"], book.res["book"], book.res["ordered"])
    assert set(dec) == {1, 2, 3, 4, 5, 6}
    for pdf, d in dec.items():
        rows = source.rows(book.res["ocr_dir"], pdf)
        undecided = [r for r in rows if r not in d["lines"]]
        assert all("example-books" in rows[r]["text"] for r in undecided), (pdf, undecided)  # the site's stamp only
        assert all(x["r"] in decisions.ROLES for x in d["lines"].values())
    d4 = dec[4]
    assert d4["type"] == "text" and "header" in {x["r"] for x in d4["lines"].values()}
    assert [x for x in d4["lines"].values() if x["r"] == "note"] == [{"r": "note", "n": 1}]
    assert any(x.get("p") for x in d4["lines"].values() if x["r"] == "body")
    assert dec[3]["type"] == "opening" and any(x["r"] == "heading" and x.get("l") == 1 for x in dec[3]["lines"].values())


def test_corrections_decide_the_next_conversion(book):
    d, rows = page4(book.res)
    body = [r for r in sorted(d["lines"]) if d["lines"][r]["r"] == "body"]
    marked = next(r for r in body if re.search(r"[^\s۰-۹]۱(?=[\s.،]|$)", rows[r]["text"]))
    target = next(r for r in body if r != marked and len(rows[r]["text"].split()) >= 4)
    word = rows[target]["text"].split()[1]
    quoted = next(r for r in body if r not in (marked, target))  # each edit on a line of its own
    headed = next(r for r in reversed(body) if r not in (marked, target, quoted))
    data = corrections.load(book.res["corrections_path"])
    corrections.edit_page(data, 4, d, rows, {"lines": {
        quoted: {"r": "quote", "p": True, "x": "متنی که ویراستار درست کرد"},
        headed: {"r": "heading", "l": 2},
        marked: {"x": re.sub(r"(?<=[^\s۰-۹])۱(?=[\s.،]|$)", "", rows[marked]["text"])},  # the OCR's marker taken away
        target: {"m": [1], "a": [word]},  # ... and put after another word
    }})
    corrections.save(book.res["corrections_path"], data)
    res = book.run()
    html = text_of(res)
    quote = re.search(r"<blockquote[^>]*>(.*?)</blockquote>", html, re.S)
    assert quote and "متنی که ویراستار درست کرد" in quote.group(1)
    tail = rows[headed]["text"].strip()[:12]
    assert re.search(r"<h2[^>]*>[^<]*" + re.escape(tail), html)
    assert re.search(re.escape(word) + r'<sup><a class="noteref"', html)  # the note linked after the chosen word
    assert "pages corrected by hand" in res["report"].read_text(encoding="utf-8")
    # undo the last change, then take the whole page back
    assert corrections.undo(data)["pdf"] == 4 and data["pages"] == {}
    corrections.save(book.res["corrections_path"], data)
    html = text_of(book.run())
    assert "ویراستار" not in html and re.search(r"<blockquote", html) is None


def test_page_fields_book_data_and_cover(book):
    d, rows = page4(book.res)
    body = [r for r in sorted(d["lines"]) if d["lines"][r]["r"] == "body"]
    box = [min(rows[r]["bbox"][0] for r in body[:2]) - 4, min(rows[r]["bbox"][1] for r in body[:2]) - 4,
           max(rows[r]["bbox"][2] for r in body[:2]) + 4, max(rows[r]["bbox"][3] for r in body[:2]) + 4]
    data = corrections.load(book.res["corrections_path"])
    dec = decisions.collect(book.res["layouts"], book.res["book"], book.res["ordered"])
    corrections.edit_page(data, 4, d, rows, {"page": {"figures": [box], "pn": "40"},
                                            "lines": {r: {"r": "figure"} for r in body[:2]}})
    corrections.edit_page(data, 6, dec[6], source.rows(book.res["ocr_dir"], 6), {"page": {"type": "figure", "rotate": 90}})
    corrections.edit_book(data, {"meta": {"title": "عنوان درست", "author": "نویسندهٔ درست"}, "cover": 1})
    corrections.save(book.res["corrections_path"], data)
    res = book.run()
    z = zipfile.ZipFile(res["epub"])
    assert len([n for n in z.namelist() if n.startswith("OEBPS/images/fig")]) == 2  # the drawn picture, the figure page
    assert next(L for L in res["layouts"] if L.page.index == 6).rotate == 90
    opf = z.read("OEBPS/content.opf").decode()
    assert "عنوان درست" in opf and "نویسندهٔ درست" in opf
    assert "OEBPS/text/cover.xhtml" in z.namelist()
    nav = z.read("OEBPS/nav.xhtml").decode()
    assert "#page-40" in nav  # the printed page number set by hand


def unique_word(text, skip=1):
    """A word of TEXT (after the first SKIP) found once in it, as the panel's phrase for a clicked word is."""
    return next(w for w in text.split()[skip:] if len(w) > 2 and text.count(w) == 1 and not re.search("[۰-۹]", w))


def test_a_persons_markers_are_exact():
    P, N = labels.PERSON, labels.NOT_MARKER
    # after the person's words, not where the OCR glued the number (that copy goes); other digits stay text
    assert labels._exact_markers("آمد۲ و رفت. سپس گفت", [2], ["رفت."]) == "آمد و رفت." + P + "۲ سپس گفت"
    assert labels._exact_markers("در سال ۱۳۵۷ آمد۳ و رفت", [], []) == "در سال ۱۳۵۷ آمد" + N + "۳ و رفت"
    # the digits the OCR read right after the words are the marker as read (misread: replaced); words gone: at the end
    assert labels._exact_markers("گفت.۳۳ سپس", [32], ["گفت."]) == "گفت." + P + "۳۲ سپس"
    assert labels._exact_markers("متنی دیگر", [4], ["واژه‌ای"]) == "متنی دیگر" + P + "۴"


def test_markers_by_hand_win(book):
    d, rows = page4(book.res)
    body = [r for r in sorted(d["lines"]) if d["lines"][r]["r"] == "body"]
    marked = next(r for r in body if re.search(r"[^\s۰-۹]۱(?=[\s.،]|$)", rows[r]["text"]))
    glued = next(w for w in rows[marked]["text"].split() if re.search(r"[^\s۰-۹]۱$", w))
    other = unique_word(rows[marked]["text"])
    data = corrections.load(book.res["corrections_path"])
    corrections.edit_page(data, 4, d, rows, {"lines": {marked: {"m": [1], "a": [other]}}})  # the same line: moved
    corrections.save(book.res["corrections_path"], data)
    html = text_of(book.run())
    assert re.search(re.escape(other) + r'<sup><a class="noteref"', html)
    assert glued not in html and html.count('<sup><a class="noteref"') == 1
    target = next(r for r in body if r != marked and len(rows[r]["text"].split()) >= 4)
    word = unique_word(rows[target]["text"])
    data = corrections.empty()
    corrections.edit_page(data, 4, d, rows, {"lines": {target: {"m": [1], "a": [word]}}})  # another line: the OCR's
    corrections.save(book.res["corrections_path"], data)                                   # number there is text
    html = text_of(book.run())
    assert re.search(re.escape(word) + r'<sup><a class="noteref"', html) and html.count('<sup><a class="noteref"') == 1


def test_split_and_added_lines(book):
    d, rows = page4(book.res)
    body = [r for r in sorted(d["lines"]) if d["lines"][r]["r"] == "body"]
    notes = [r for r in sorted(d["lines"]) if d["lines"][r]["r"] == "note"]
    line = next(r for r in body if len(rows[r]["text"].split()) >= 6 and "۱" not in rows[r]["text"])
    words = rows[line]["text"].split()
    first, second = " ".join(words[:3]), " ".join(words[3:])
    target = next(r for r in body if r != line and len(rows[r]["text"].split()) >= 4 and "۱" not in rows[r]["text"])
    word = unique_word(rows[target]["text"])
    added = "یادداشت دومی که اوسی‌آر نخوانده بود"
    data = corrections.load(book.res["corrections_path"])
    corrections.edit_page(data, 4, d, rows, {
        "lines": {line: {"x": first}, target: {"m": [2], "a": [word]}},  # a line split in two; a second note's marker
        "missing": [{"r": "body", "t": second, "after": line, "p": False},
                    {"r": "note", "n": 2, "t": added, "after": notes[-1], "p": False}]})
    corrections.save(book.res["corrections_path"], data)
    res = book.run()
    html = text_of(res)
    plain = re.sub(r"<[^>]+>", "", html)
    assert first + " " + second in plain  # the two parts read on as one line did
    assert added in plain and re.search(re.escape(word) + r'<sup><a class="noteref"', html)
    assert html.count('<sup><a class="noteref"') == 2
    again = decisions.collect(res["layouts"], res["book"], res["ordered"])[4]["missing"]  # shown again on the panel
    assert {(m["r"], m["t"], m.get("after")) for m in again} == {("body", second, line), ("note", added, notes[-1])}
    assert next(m for m in again if m["r"] == "note")["n"] == 2


def test_a_part_page_keeps_its_text(book):
    marks_mod.save(book.res["marks_path"], {3: {"kind": "chapter", "title": "فصل اول: باغ و باران"},
                                            5: {"kind": "part", "title": "فصل دوم: راه شهر"}})
    res = book.run()
    assert [ch.kind for ch in res["book"].chapters if ch.kind != "front"] == ["chapter", "part"]
    plain = re.sub(r"<[^>]+>", "", text_of(res))
    assert "صبح روز بعد آسمان صاف بود" in plain  # the text under the part's title on its page
    assert not re.search(r"<p[^>]*>\s*(فصل دوم|راه شهر)\s*</p>", text_of(res))  # its title lines: the title, not text


def test_a_page_kept_in_the_book():
    from types import SimpleNamespace
    from parisaocr.ebook import order
    nums = [None, 1, 2, 3, 3, 4, 5]  # PDF page 5 read as 3 again: a second scan of page 3, to the converter
    pages = [SimpleNamespace(page=SimpleNamespace(index=i + 1, lines=[]), kind="text", quality=1.0 - 0.1 * (i == 4),
                             number_candidates=[k] if k else []) for i, k in enumerate(nums)]
    o = order.order(pages)
    assert [p.page.index for p, _ in o.pages] == [1, 2, 3, 4, 6, 7] and o.duplicates == [(5, 4, 3)]
    pages[4].keep_by_hand = True  # "Keep this page in the book": after PDF page 4, without a number of its own
    o = order.order(pages)
    assert [p.page.index for p, _ in o.pages] == [1, 2, 3, 4, 5, 6, 7] and o.duplicates == []
    assert dict((p.page.index, n) for p, n in o.pages)[5] == 3.5


def test_pages_with_one_number():
    from types import SimpleNamespace
    from parisaocr.ebook import order
    words = lambda k: [SimpleNamespace(text=" ".join(f"واژه{k}x{j}" for j in range(12)))]  # a page's own text

    def ordered(nums, texts):
        return order.order([SimpleNamespace(page=SimpleNamespace(index=i + 1, lines=texts[i]), kind="text", quality=1.0,
                                            number_candidates=[k]) for i, k in enumerate(nums)])
    # PDF page 12 misread as 4: page 4 (PDF 4, its neighbours agree) keeps the number, PDF 12 stays, unnumbered
    o = ordered([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 4, 13], [words(i) for i in range(13)])
    keys = {p.page.index: n for p, n in o.pages}
    assert keys[4] == 4 and 11 < keys[12] < 13 and o.duplicates == []
    # page 4 scanned twice (PDF 4 and 5 read alike): the second scan goes
    nums, texts = [1, 2, 3, 4, 4, 5, 6, 7, 8, 9, 10, 11], [words(i) for i in range(12)]
    texts[4] = words(3)
    o = ordered(nums, texts)
    assert len(o.duplicates) == 1 and o.duplicates[0][2] == 4 and len(o.pages) == 11
    # PDF 5 reads otherwise: another page that took the number 4 stays, without it, after PDF 4
    texts[4] = words(4)
    o = ordered(nums, texts)
    keys = {p.page.index: n for p, n in o.pages}
    assert o.duplicates == [] and len(o.pages) == 12 and 4 in (keys[4], keys[5]) and keys[4] < keys[5]  # in PDF order


def test_lines_found_again_after_a_new_ocr(book):
    d, rows = page4(book.res)
    data = corrections.load(book.res["corrections_path"])
    some = sorted(rows)[3]
    corrections.edit_page(data, 4, d, rows, {"lines": {some: {"r": "quote"}}})
    shifted = {r + 1: row for r, row in rows.items()}  # the rows numbered anew, the same boxes
    g = corrections.labels_for(data, lambda pdf: shifted)[4]
    assert g["lines"][some + 1]["r"] == "quote" and g["lost"] == 0
    moved = {r: dict(row, bbox=[v + 2000 for v in row["bbox"]]) for r, row in rows.items()}  # nothing where it was
    assert corrections.labels_for(data, lambda pdf: moved)[4]["lost"] == len(rows)


def test_doubts():
    assert issues.role_doubt(None, "body") is None
    assert issues.role_doubt({"role": "body", "role_p": 0.999, "note": 0.001, "heading": 0.002}, "body") < 0.05
    assert issues.role_doubt({"role": "note", "role_p": 0.6, "note": 0.55, "heading": 0.01}, "body") >= 0.5
    assert issues.role_doubt({"role": "pagenum", "role_p": 0.999, "note": 0.0, "heading": 0.0}, "header") < 0.05
    assert issues.text_doubt([{"conf": 99.0}, {"conf": 100.0}], 99.5)[0] == 0.0
    doubt, weak = issues.text_doubt([{"conf": 99.0}, {"conf": 72.0}], 90.0)
    assert weak == 72.0 and doubt == 1.0
    assert issues.level(0.6) == "unsure" and issues.level(0.3) == "check" and issues.level(0.05) == "sure"


def test_panel_server(book, tmp_path):
    panel = review.Panel(book.run(), lambda marks: book.run(marks), tmp_path / "cache")  # the EPUB as this test starts
    server, url = review.start(panel)

    def get(path, host=None):
        req = urllib.request.Request(url.rstrip("/") + path, headers={"Host": host} if host else {})
        with urllib.request.urlopen(req) as r:
            return r.headers.get("Content-Type", ""), r.read()

    def post(path, data):
        req = urllib.request.Request(url.rstrip("/") + path, json.dumps(data).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    try:
        assert b"panel.js" in get("/")[1] and get("/static/panel.js")[0].startswith("text/javascript")
        assert get("/static/fonts/Vazirmatn-Regular.ttf")[0] == "font/ttf"
        with pytest.raises(urllib.error.HTTPError) as e:
            get("/api/book", host="attacker.example:80")
        assert e.value.code == 403
        b = json.loads(get("/api/book")[1])
        assert len(b["pages"]) == 6 and b["reviewed"] == 0 and b["chapters"] and not b["dirty"]
        iss = json.loads(get("/api/issues")[1])
        assert iss == sorted(iss, key=lambda x: (x["ignored"], -x["doubt"], x["pdf"] or 0, x.get("row", -1)))
        p = json.loads(get("/api/page/4")[1])
        assert p["notes"] and p["notes"][0]["how"] == "marker" and p["href"].endswith("#page-4")
        assert all({"row", "bbox", "text", "d", "doubt", "words"} <= set(ln) for ln in p["lines"])
        assert get("/img/4.jpg")[0] == "image/jpeg"
        assert get("/epub/" + p["href"].split("#")[0])[0].startswith("application/xhtml+xml")
        body = [ln for ln in p["lines"] if ln["d"]["r"] == "body"]
        r = post("/api/page/4", {"lines": {str(body[0]["row"]): {"r": "verse"}}})
        assert r["page"]["reviewed"] and next(ln for ln in r["page"]["lines"] if ln["row"] == body[0]["row"])["d"]["r"] == "verse"
        assert json.loads(get("/api/book")[1])["dirty"]
        assert post("/api/undo", {})["undone"]["pdf"] == 4
        assert not json.loads(get("/api/page/4")[1])["reviewed"]
        header = next(ln for ln in p["lines"] if ln["d"]["r"] == "header" and not ln["text"].strip().isdigit())
        assert post("/api/bulk", {"text": header["text"], "edits": {"r": "noise"}})["pages"]  # every page with that header
        assert post("/api/ignore", {"id": iss[0]["id"], "ignored": True})["ok"] if iss else True
        assert post("/api/marks", {"pages": {"5": {"kind": "chapter", "title": "فصل پنجم"}}})["ok"]
        assert post("/api/book", {"meta": {"title": "کتاب تازه"}})["ok"]
        out = post("/api/rebuild", {})
        assert out["ok"] and out["seconds"] is not None
        b = json.loads(get("/api/book")[1])
        assert not b["dirty"] and b["title"] == "کتاب تازه" and [c["pdf"] for c in b["chapters"]] == [5]
        assert post("/api/quit", {})["ok"] and panel.done.wait(5)
    finally:
        server.shutdown()
