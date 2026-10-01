# How ParisaOCR is put together

A page goes through four steps. Each module below is small and replaceable.

| Step | Module | What it does |
|---|---|---|
| Pages | `pages.py` | Collects inputs; a PDF's scanned pages are extracted with `pdfimages` (lossless; each page image is checked against a small `pdftoppm` render and turned or replaced if it is stored mirrored, rotated or in strips), born-digital pages rendered with `pdftoppm`. |
| Lines | `detectors.py`, `detect.py` | Finds text lines. Default: PP-OCRv6 small (`PaddleDetector`, ONNX Runtime, bundled model). Also PP-OCR v6 medium/tiny and v5 (downloaded by RapidOCR), Kraken's baseline segmenter, Surya (optional extra). Pages narrower than 1,600 px are enlarged and wider than 2,000 px reduced for detection; detections are cached per page. |
| Crops | `pipeline.py` | Cuts each line with a margin of 0.4 x the page's median line height sideways and half of it vertically (detector boxes hug the letter bodies; the dots of Persian letters lie outside), paints neighbouring lines inside the margin in the background colour, enlarges small crops. Boxes much taller than a line are marked as blocks. |
| Reading | `kraken_reader.py`, `bidi.py` | Stacks the crops of a batch into one image and reads them with the Kraken model in GPU/CPU batches; blocks are split into lines at the rows with least ink first. PP-OCR inputs are padded to the training width (kraken#810). The model's visual-order output is turned into reading order by `bidi.to_logical` with each line's own base direction, keeping ZWNJ (kraken#809). Word boxes run from space to space. Short, unsure lines (4 characters or fewer below 80% confidence) are dropped as non-text marks. |
| Order and output | `order.py`, `output.py` | Groups lines into columns read right to left; writes text, hOCR (line and word boxes on the original page) and JSONL. |
| Searchable PDF | `pdfout.py` | Builds an invisible text layer (reportlab, rendering mode 3): per line one run in display order (`bidi.to_display`), stretched to the line's width, in the bundled Vazirmatn font. For a PDF input the layer is laid over the original pages as a form XObject (pikepdf), placed in media-box coordinates through the page's /Rotate; pages with an existing text layer (100+ letters by `pdftotext`) are skipped unless `--pdf-text add`. For image inputs a new PDF holds the image and the layer. `PARISAOCR_PDF_VISIBLE=1` draws the layer in red, to check its placement. |

`reader.py` is the Tesseract alternative to the Kraken reader (`--model TESSDATA:LANG`);
`cut.py` (`parisaocr cut`) cuts the lines of scanned book pages into crops plus a
manifest for building training data; `fa_text.py` holds the Persian text
normalization shared with training and evaluation.

System programs: Poppler's `pdfinfo`, `pdfimages`, `pdftoppm` and `pdftotext` for PDF input;
Tesseract only for `--model TESSDATA:LANG` and `parisaocr cut`.
