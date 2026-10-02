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
