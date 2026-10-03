# Changelog

## Unreleased

- `parisaocr epub --review`: a local page to mark a book's parts, chapters,
  sections and figure pages by hand — thumbnails of every page in reading
  order with the converter's decisions on them, titles prefilled from the OCR,
  a live table of contents, Rebuild. The marks (`OUT/NAME.marks.json`, with
  the converter's own decisions beside them) are the truth about openings for
  every later run of the book. Where the rules and models fail on a book, three
  minutes of marking make its chapters right.

## 0.3.0 (2026-10-02)

- EPUB 3 from a scanned book, experimental (`parisaocr epub book.pdf --out
  DIR`): chapters from the printed contents and the page layout, footnotes as
  popup notes linked to their markers (numbered per page or through the book,
  or with asterisks), paragraphs joined across pages, block quotes, verse
  (two hemistichs, and poems set line by line), figures and tables as images,
  the printed page numbers as the book's page list, pages put in order by
  their printed numbers (reversed runs, duplicate scans, missing pages). A
  report lists what was decided and where to check. Embeds Vazirmatn.
- Chapter openings are decided for the whole book: the contents are aligned
  with the pages (by title, page number or both), their entries told apart as
  chapters or sections, and the book's own opening template learned from the
  confirmed ones. Footnote areas are split by the chain of note numbers
  (numbering per page, per chapter or through the book; numbers next to an
  English term or lost by the OCR). Measured on 15 modern books.
- `parisaocr ocr`: short lines that are only a number (a page number, "...۲"
  in a table of contents) are kept even when read with less confidence.
- Learned line roles for the EPUB structure, bundled (`models/roles`, 5 MB):
  gradient-boosted trees over 46 layout features of each OCR line, trained
  on 4,018 sampled pages of 85 scanned Persian books whose lines were labelled
  by Gemini 3.8 Flash (role, footnote, heading, note start, heading level; see
  MODEL_CARD.md). The layout rules consult them: a footnote area on pages
  without a separator rule, chapter openings and their title blocks, heading
  evidence. On eight held-out books, against rules alone: chapters recall
  57 → 64, footnotes found 44 → 54 (pooled), linked 62 → 76, section headings
  46 → 63. `--roles DIR` swaps the models, `--roles none` keeps to the rules.
- Watermarks. A scan placed inside a larger page, or with a site's name typed
  over it, used to be rendered with the stamp burned in; the page is now drawn
  from its embedded images where the page places them (lossless, without the
  stamp), and a stamp found as text repeated over the scans is stripped from
  any page that still has to be rendered, and from the searchable PDF. A text
  layer of glyph codes (a broken earlier OCR) is replaced by ours there;
  `--pdf-text replace` does the same for every scan page. In `parisaocr epub`,
  a site's name burned into the scans (the same line at the same place in a
  margin of two or more pages) is left out of the text and whited out in figure
  crops; the report says so.

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
