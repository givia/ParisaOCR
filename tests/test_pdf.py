"""Searchable PDF output: the text layer is found by a PDF text extractor, for images and for rotated PDF pages."""
import collections
import re
import shutil
import subprocess

import pytest

pytest.importorskip("kraken")
pytest.importorskip("pikepdf")
from test_ocr import DATA  # noqa: E402

needs_poppler = pytest.mark.skipif(not shutil.which("pdftotext"), reason="needs poppler's pdftotext")


def pdf_text(path):
    return subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], capture_output=True, text=True).stdout


def word_recall(gold, out):
    """Share of the gold words found, letters and digits only: what a search finds. Extractors move
    punctuation at the edges of right-to-left words around (pdftotext turns "(E مینور)" into ")E مینور(")."""
    from parisaocr.fa_text import relaxed
    g = collections.Counter(re.findall(r"\w+", relaxed(gold)))
    o = collections.Counter(re.findall(r"\w+", relaxed(out)))
    return sum((g & o).values()) / sum(g.values())


@needs_poppler
def test_image_to_searchable_pdf(tmp_path):
    from parisaocr.cli import main
    main(["ocr", str(DATA / "sahel-200dpi.png"), "--out", str(tmp_path), "--format", "txt,pdf", "--cpu"])
    gold = (DATA / "sahel-200dpi.gt.txt").read_text(encoding="utf-8")
    assert word_recall(gold, pdf_text(tmp_path / "pdf" / "sahel-200dpi.pdf")) > 0.9


@needs_poppler
@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_rotated_pdf_page(tmp_path, rotate):
    """A scan stored turned, with /Rotate set so that it displays upright: the layer must follow."""
    import pikepdf
    from PIL import Image
    from reportlab.pdfgen.canvas import Canvas
    from parisaocr.cli import main
    from parisaocr.pdfout import _display_to_user
    image = DATA / "sahel-200dpi.png"
    w_px, h_px = Image.open(image).size
    w, h = w_px * 72 / 200, h_px * 72 / 200
    wu, hu = (h, w) if rotate in (90, 270) else (w, h)
    src = tmp_path / "in.pdf"
    c = Canvas(str(src), pagesize=(wu, hu))
    c.transform(*_display_to_user(rotate, (0, 0, wu, hu)))
    c.drawImage(str(image), 0, 0, w, h)
    c.showPage()
    c.save()
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.pages[0].Rotate = rotate
        pdf.save(src)
    main(["ocr", str(src), "--out", str(tmp_path / "out"), "--format", "pdf", "--cpu"])
    gold = (DATA / "sahel-200dpi.gt.txt").read_text(encoding="utf-8")
    assert word_recall(gold, pdf_text(tmp_path / "out" / "pdf" / "in.pdf")) > 0.9


@needs_poppler
def test_glyph_code_layer_and_stamp_replaced(tmp_path):
    """A shared copy of a scan: an invisible text layer of glyph codes (letters no search can use) under the
    page image and a site's name typed over it on every page. Ours replaces the layer; the stamp goes."""
    from PIL import Image
    from reportlab.pdfgen.canvas import Canvas
    from parisaocr.cli import main
    image = DATA / "sahel-200dpi.png"
    w_px, h_px = Image.open(image).size
    w, h = w_px * 72 / 200, h_px * 72 / 200
    src = tmp_path / "in.pdf"
    c = Canvas(str(src), pagesize=(w, h))
    for _ in range(3):
        t = c.beginText(20, h / 2)
        t.setTextRenderMode(3)
        t.setFont("Helvetica", 12)
        t.textOut("xkqzv jwpqx " * 20)
        c.drawText(t)
        c.drawImage(str(image), 0, 0, w, h)
        c.setFont("Courier", 10)
        c.drawString(2, h - 12, "www.example-books.com")
        c.showPage()
    c.save()
    main(["ocr", str(src), "--out", str(tmp_path / "out"), "--format", "pdf", "--cpu"])
    text = pdf_text(tmp_path / "out" / "pdf" / "in.pdf")
    gold = (DATA / "sahel-200dpi.gt.txt").read_text(encoding="utf-8")
    assert word_recall(gold, text) > 0.9
    assert "xkqzv" not in text and "example-books" not in text
