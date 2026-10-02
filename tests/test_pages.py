"""Page images from a PDF that sets the scan inside a larger page and types a site's name over it."""
import shutil

import pytest

pytest.importorskip("pikepdf")
pytest.importorskip("reportlab")
from test_ocr import DATA  # noqa: E402

needs_poppler = pytest.mark.skipif(not shutil.which("pdfimages"), reason="needs poppler's pdfimages")
STAMP = "www.example-books.com"


def stamped_pdf(path, scans=10, blank=1):
    """SCANS pages with the sample scan set in wide margins, then BLANK pages without an image, each with
    the stamp typed at the top: what a download site makes of a scanned book."""
    from PIL import Image
    from reportlab.pdfgen.canvas import Canvas
    image = DATA / "sahel-200dpi.png"
    w_px, h_px = Image.open(image).size
    w, h = w_px * 72 / 200, h_px * 72 / 200
    c = Canvas(str(path), pagesize=(w + 200, h + 300))
    for n in range(scans + blank):
        if n < scans:
            c.drawImage(str(image), 100, 150, w, h)
        c.setFont("Courier", 10)
        c.drawString(2, h + 300 - 14, STAMP)
        c.showPage()
    c.save()
    return path


@needs_poppler
def test_scan_in_margins_under_a_text_stamp(tmp_path):
    import numpy as np
    import pikepdf
    from PIL import Image
    from parisaocr.pages import pdf_pages, pdf_text_stamps
    pdf = stamped_pdf(tmp_path / "in.pdf")
    info = pdf_text_stamps(pikepdf.open(pdf))
    assert info[1]["stamps"] == [STAMP] and not info[1]["other"] and info[1]["scan"]
    assert info[11]["stamps"] == [STAMP] and not info[11]["scan"]

    got = pdf_pages(pdf, tmp_path / "pages", "auto", 300, None, None, False)
    assert len(got) == 11
    ink = []
    for pid, path in got:
        g = np.asarray(Image.open(path).convert("L")) < 128
        assert g[: int(0.04 * g.shape[0])].mean() < 1e-4, f"{pid}: the stamp is in the page image"
        ink.append(g.mean())
    assert all(i > 0.005 for i in ink[:10]), "the scan pages lost their scan"
    assert ink[10] < 1e-5, "the blank page is not blank"
    # the scan is where the page puts it: the drawn page keeps the scan's resolution and the margins
    with Image.open(got[0][1]) as im, Image.open(DATA / "sahel-200dpi.png") as scan:
        assert abs(im.width / (scan.width + 200 * 200 / 72) - 1) < 0.02
