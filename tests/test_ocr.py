"""End to end on a rendered page (Persian Wikipedia text, CC BY-SA 4.0, in the open font Sahel)."""
import collections
import pathlib

import pytest

pytest.importorskip("kraken")
DATA = pathlib.Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def ocr():
    from parisaocr import OCR
    return OCR(cpu=True)


def word_recall(gold, out):
    from parisaocr.fa_text import relaxed
    g, o = collections.Counter(relaxed(gold).split()), collections.Counter(relaxed(out).split())
    return sum((g & o).values()) / sum(g.values())


def test_reads_a_page(ocr):
    text = ocr.text(DATA / "sahel-200dpi.png")
    gold = (DATA / "sahel-200dpi.gt.txt").read_text(encoding="utf-8")
    assert len(text.splitlines()) == len(gold.strip().splitlines())
    assert word_recall(gold, text) > 0.95


def test_lines_have_boxes_and_confidence(ocr):
    lines = ocr.lines(DATA / "sahel-200dpi.png")
    assert lines and all(len(l.bbox) == 4 and 0 <= l.conf <= 100 for l in lines)
    assert all(l.words for l in lines)
