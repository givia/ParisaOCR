"""ParisaOCR: open-source OCR for printed Persian.

A text-line detector (PP-OCRv6, run with ONNX Runtime) finds the lines of a
page; a Kraken recognition model trained for Persian print reads each line; the
lines are ordered into right-to-left columns and written as text, hOCR (with
line and word boxes on the original page) or JSONL. Both models ship with the
package, so it runs offline, on a CPU or a GPU.

    parisaocr ocr page.png                      # print the text
    parisaocr ocr book.pdf --out out            # out/txt, out/hocr
    parisaocr ocr book.pdf --out out --format pdf   # out/pdf/book.pdf, searchable
    parisaocr epub book.pdf --out out           # out/book.epub (experimental)

    from parisaocr import OCR
    print(OCR().text("page.png"))

The recognition model and the detector are replaceable (`--model`, `--detector`):
Tesseract models and the Surya detector are supported for comparison, and
`parisaocr cut` cuts the lines of scanned books for building training data.
"""
__version__ = "0.7.1"


def __getattr__(name):  # the interface imports torch and Kraken; only load them when asked for
    if name == "OCR":
        from .api import OCR
        return OCR
    raise AttributeError(name)
