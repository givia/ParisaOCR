# Changelog

## 0.2.0 (2026-10-01)

- Searchable PDF output (`--format pdf`): the input PDF with an invisible text
  layer over each read page (scans untouched, page rotation respected, pages
  that already have text left alone unless `--pdf-text add`), or PDFs made
  from input images (`--merge-pdf NAME` for one file). The layer is stored as
  word processors store Persian text, so viewers search and copy it in reading
  order. Bundles the Vazirmatn font (SIL OFL 1.1) for it.

## 0.1.0 (2026-10-01), preview

First public release.

- Page OCR for printed Persian: PP-OCRv6 small line detection (bundled, ONNX
  Runtime) and a Kraken recognizer trained on about 200 scanned Persian books
  plus synthetic Persian, English and special-character lines (bundled).
- Command line `parisaocr ocr` (images, directories, PDFs; text, hOCR, JSONL)
  and Python interface `parisaocr.OCR`.
- Own reading-order handling: half-spaces (ZWNJ) kept, English lines and
  footnotes in their own direction.
- Warning for pages whose text is too small to read reliably.
- Alternative engines for comparison: Tesseract models, Surya and Kraken
  segmenters, other PP-OCR detector sizes.
