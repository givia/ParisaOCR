"""Searchable PDF: the page images with an invisible text layer over the recognized lines.

PDF input: a text layer is laid over each read page of the original file. The
scans are not touched, page rotation (/Rotate) is respected,
and pages that already carry text are left as they are.
Image input: a new PDF with the image as the page and the text layer on top.

The text layer looks to a PDF viewer like the text of a Persian document made
with a word processor: one run of text per line, in display (left-to-right
visual) order, written in invisible rendering mode and stretched to the
width of the line on the page. Viewers turn display order back into reading
order when searching, selecting or copying, as they do for any Persian PDF.
Display order comes from parisaocr.bidi, which keeps the half-space (ZWNJ)
and gives each line its own direction, so Latin lines stay left to right.
The font is Vazirmatn (SIL Open Font License 1.1, bundled; it covers every
character the model writes); its glyphs are never drawn, only their Unicode
mapping is used.
"""
import io
import json
import os
import pathlib
import subprocess

FONTS = pathlib.Path(__file__).resolve().parent / "fonts"
FONT_FILE = FONTS / "Vazirmatn-Regular.ttf"
FONT_NAME = "ParisaOCR-Vazirmatn"
VISIBLE = os.environ.get("PARISAOCR_PDF_VISIBLE") == "1"  # draw the text layer in red, for checking its placement


def _font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT_NAME, str(FONT_FILE)))
    return FONT_NAME


def read_jsonl(path):
    return [json.loads(l) for l in pathlib.Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def draw_lines(canvas, lines, scale_x, scale_y, page_height):
    """Invisible text of one page on a reportlab canvas.

    lines: the page's records as `parisaocr ocr --format jsonl` writes them (boxes in image pixels);
    scale_x, scale_y: points per image pixel; page_height: in points (PDF y runs upward).
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from .bidi import to_display
    font = _font()
    for line in lines:
        text = line.get("text", "").strip()
        if not text:
            continue
        x0, y0, x1, y1 = line["bbox"]
        height = (y1 - y0) * scale_y
        width = (x1 - x0) * scale_x
        size = max(1.0, 0.75 * height)
        shown = to_display(text)
        natural = stringWidth(shown, font, size)
        if natural <= 0 or width <= 0:
            continue
        t = canvas.beginText()
        if VISIBLE:  # debugging: draw the layer in red to see where it lies
            t.setTextRenderMode(0)
            canvas.setFillColorRGB(0.85, 0, 0)
        else:
            t.setTextRenderMode(3)                    # invisible: searchable and selectable, not drawn
        t.setFont(font, size)
        t.setHorizScale(100.0 * width / natural)      # the run spans exactly the line on the page
        t.setTextOrigin(x0 * scale_x, page_height - y1 * scale_y + 0.2 * height)
        t.textOut(shown)
        canvas.drawText(t)


def text_layer(pages):
    """A PDF (bytes) with one page per item of PAGES: (width_pt, height_pt, image_w_px, image_h_px, lines)."""
    from reportlab.pdfgen.canvas import Canvas
    buf = io.BytesIO()
    c = Canvas(buf, pageCompression=1)
    for width, height, img_w, img_h, lines in pages:
        c.setPageSize((width, height))
        draw_lines(c, lines, width / img_w, height / img_h, height)
        c.showPage()
    c.save()
    return buf.getvalue()


def images_to_pdf(items, out_path):
    """A searchable PDF from page images: ITEMS are (image path, jsonl records) in page order.
    The page size follows the image's dpi (300 when the file does not say)."""
    from PIL import Image
    from reportlab.pdfgen.canvas import Canvas
    c = Canvas(str(out_path), pageCompression=1)
    for image, lines in items:
        with Image.open(image) as im:
            w_px, h_px = im.size
            dpi = im.info.get("dpi", (300, 300))
        dx, dy = (float(dpi[0]) or 300.0), (float(dpi[1]) or 300.0)
        if dx < 50 or dy < 50:  # nonsense resolution in the file header
            dx = dy = 300.0
        width, height = w_px * 72.0 / dx, h_px * 72.0 / dy
        c.setPageSize((width, height))
        c.drawImage(str(image), 0, 0, width, height)
        draw_lines(c, lines, width / w_px, height / h_px, height)
        c.showPage()
    c.save()


def page_has_text(pdf, number, min_letters=100):
    """True when page NUMBER (1-based) of PDF already carries a real text layer: a born-digital page or
    a scan someone has OCRed. A few letters (a watermark, a stamp, a page number) do not count."""
    out = subprocess.run(["pdftotext", "-f", str(number), "-l", str(number), "-q", str(pdf), "-"],
                         capture_output=True, text=True).stdout
    return sum(c.isalpha() for c in out) >= min_letters


def _display_to_user(rotate, box):
    """Matrix taking coordinates on the page as displayed (origin bottom left) to PDF user space."""
    x0, y0, x1, y1 = box
    return {0: (1, 0, 0, 1, x0, y0),
            90: (0, 1, -1, 0, x1, y0),
            180: (-1, 0, 0, -1, x1, y1),
            270: (0, -1, 1, 0, x0, y1)}[rotate % 360]


def overlay_pdf(src_pdf, pages, out_path, keep_text=True):
    """Copy SRC_PDF to OUT_PATH with an invisible text layer on the given pages.

    PAGES: {page number (1-based): (image_w_px, image_h_px, jsonl records)}, where the image is the page's
    media box as displayed (rotation applied), as parisaocr.pages extracts or renders it. With KEEP_TEXT, pages that already carry text are left alone;
    otherwise the layer is added to them too (on top of what is there). Returns the numbers of the pages
    that got a text layer.
    """
    import pikepdf
    pdf = pikepdf.open(str(src_pdf))
    todo = {n: v for n, v in sorted(pages.items())
            if 1 <= n <= len(pdf.pages) and not (keep_text and page_has_text(src_pdf, n))}
    geometry = {}
    for n in todo:
        page = pdf.pages[n - 1]
        # The page images are the media box as displayed: pdfimages' page-sized scan covers it, and
        # pdftoppm renders it (not the crop box) with /Rotate applied. The crop box only limits what a
        # viewer shows, so the layer is placed in media-box coordinates.
        box = [float(v) for v in page.mediabox]
        rotate = int(page.obj.get("/Rotate", 0)) % 360
        w, h = box[2] - box[0], box[3] - box[1]
        geometry[n] = (box, rotate, (h, w) if rotate in (90, 270) else (w, h))
    layer = pikepdf.open(io.BytesIO(text_layer(
        [(*geometry[n][2], img_w, img_h, lines) for n, (img_w, img_h, lines) in todo.items()])))
    for (n, _), text_page in zip(todo.items(), layer.pages):
        box, rotate, _ = geometry[n]
        page = pdf.pages[n - 1]
        form = pdf.copy_foreign(text_page.as_form_xobject())
        name = page.add_resource(form, pikepdf.Name.XObject, prefix="ParisaOCR")
        a, b, c, d, e, f = _display_to_user(rotate, box)
        # The page's own content in a q/Q pair, so a graphics state it leaves behind (scaling,
        # unbalanced saves) cannot move the text layer drawn after it.
        page.contents_add(pikepdf.Stream(pdf, b"q\n"), prepend=True)
        page.contents_add(pikepdf.Stream(pdf, f"\nQ q {a} {b} {c} {d} {e} {f} cm {name} Do Q\n".encode()), prepend=False)
    pdf.save(str(out_path))
    return list(todo)
