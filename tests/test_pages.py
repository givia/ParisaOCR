"""Page images from a PDF that sets the scan inside a larger page and types a site's name over it."""
import shutil

import pytest

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
    pytest.importorskip("pikepdf")
    pytest.importorskip("reportlab")
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


def multipage_tiff(path, n=3):
    """The sample scan N times in one TIFF, page K with a black bar K * 40 px wide at the top left,
    and a small reduced-resolution copy stored after the first page (as some scanners write)."""
    from PIL import Image, ImageDraw, TiffImagePlugin
    scan = Image.open(DATA / "sahel-200dpi.png").convert("L")
    frames = []
    for k in range(1, n + 1):
        im = scan.copy()
        ImageDraw.Draw(im).rectangle((0, 0, 40 * k - 1, 9), fill=0)
        frames.append(im)
    frames.insert(1, scan.resize((scan.width // 8, scan.height // 8)))
    with TiffImagePlugin.AppendingTiffWriter(str(path), True) as tf:
        for k, im in enumerate(frames):
            ifd = TiffImagePlugin.ImageFileDirectory_v2()
            ifd[254] = int(k == 1)  # NewSubfileType: a reduced-resolution image
            im.save(tf, format="TIFF", tiffinfo=ifd, dpi=(200, 200), compression="tiff_lzw")
            tf.newFrame()
    return path


def bar(path):
    import numpy as np
    from PIL import Image
    return int((np.asarray(Image.open(path).convert("L"))[5] < 128)[:400].sum())


def test_every_page_of_a_multipage_tiff(tmp_path):
    from PIL import Image
    from parisaocr.pages import collect
    tif = multipage_tiff(tmp_path / "scan.tif")
    with Image.open(tif) as im:
        assert im.n_frames == 4
    out = tmp_path / "out"
    got = collect([tif], out)
    assert [pid for pid, _ in got] == ["scan-001", "scan-002", "scan-003"]
    assert [bar(p) for _, p in got] == [40, 80, 120], "the frames are not the pages, in order"
    with Image.open(got[0][1]) as im, Image.open(DATA / "sahel-200dpi.png") as scan:
        assert im.size == scan.size and round(im.info["dpi"][0]) == 200
    # a second run reuses the pages; --first/--last pick pages as for a PDF
    stamp = got[0][1].stat().st_mtime_ns
    assert collect([tif], out) == got and got[0][1].stat().st_mtime_ns == stamp
    assert [pid for pid, _ in collect([tif], out, first=2, last=9)] == ["scan-002", "scan-003"]


def test_multipage_tiffs_in_a_directory_next_to_single_images(tmp_path):
    import shutil as sh
    from PIL import Image
    from parisaocr.pages import collect
    d = tmp_path / "in"
    d.mkdir()
    multipage_tiff(d / "a.tif", 2)
    multipage_tiff(d / "b.tiff", 2)
    sh.copy(DATA / "sahel-200dpi.png", d / "c.png")
    Image.open(DATA / "sahel-200dpi.png").save(d / "d.tif")
    got = collect([d], tmp_path / "out")
    assert [pid for pid, _ in got] == ["a-001", "a-002", "b-001", "b-002", "c", "d"]
    assert got[4][1] == d / "c.png" and got[5][1] == d / "d.tif", "single images are read where they are"
