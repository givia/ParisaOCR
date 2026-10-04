# Changelog

## 0.5.1 (2026-10-04)

- **The labeller's options are renamed:** `--llm` (Gemini 3.8 Flash) or
  `--llm MODEL` (a Gemini model, or `openrouter:VENDOR/NAME`) in place of
  `--gemini` and `--gemini-model`, and `--llm-estimate`, `--llm-jobs` in place
  of `--gemini-estimate`, `--gemini-jobs`. The old names still work. A book
  path put right after `--llm` (`--llm book.pdf`) is refused with a message,
  not taken for a model.

## 0.5.0 (2026-10-04)

- **Cheaper labellers through OpenRouter:** `--gemini --gemini-model
  openrouter:VENDOR/MODEL` asks an open model the same questions as Gemini,
  for example `openrouter:qwen/qwen3.8-27b` or
  `openrouter:google/gemma-4-31b-it` (key in `OPENROUTER_API_KEY` or
  `~/.config/openrouter/api_key`). Requests go only to providers that keep no
  data, take none for training and enforce the answer's format
  (`PARISAOCR_OPENROUTER_PROVIDER` can name providers; it cannot weaken those
  settings). Reasoning is switched off where it is optional, as for Gemini.
- **Footnotes linked at their markers more often:** a marker the OCR read one
  digit off ("۳۳" for ۳۲) is replaced instead of doubled (a number of the text
  is left alone unless it is one digit off the marker and stands where a
  marker stands), digits glued to the next word
  are split, markers in list heads and chapter titles are linked, a raised
  number boxed as its own line is joined to its line, and a false image mark
  (a straight quote taken for a raised digit) no longer takes a note on a
  labelled page. On 23 development books with Gemini labels: 325 of 343
  footnotes linked after the right word, against 290 without these changes.
- **Note numbers re-read from the image:** in the default path, footnote
  numbers the line reader lost or cut ("10" for a raised "106") are re-read
  from the image with the same model, its decoding limited to digits; a page
  without numbered notes gets none. Cached with the OCR; `PARISAOCR_NOTENUM=0`
  turns it off; if it fails, the book is converted without it. Plain OCR output
  (`parisaocr ocr`) is unchanged from 0.4.0.
- **The page labeller's prompt** says more sharply what a footnote, an endnote
  and a reference are (models had called a page's own footnotes endnotes, and
  citation footnotes references); `--gemini` answers are checked more strictly.
- `parisaocr epub --gemini` prints what the book should cost before sending
  anything (calibrated on Gemini, within about 6% on the books measured; a
  rough guide for OpenRouter models), and `--gemini-estimate` prints only that.
- Fixes: the EPUB's navigation has no empty lists and lists a part's sections
  under the part; a part's page number is no longer negative.

## 0.4.0 (2026-10-03)

- `parisaocr epub --gemini`: Gemini decides the book's structure. Every page
  image goes to Gemini with its OCR lines numbered; it says what each line is
  (heading and level, footnote or endnote and its number, running header,
  page number, byline, quote, verse, …), the page's type and printed number,
  and the entries of a contents page. Two questions about the whole book
  follow: the outline (which headings open a part or chapter, which are
  sections, the titles as the reader should see them) and the bibliographic
  data from the cover, title and copyright pages (title, authors, translators,
  editors, publisher, year, ISBN). On 10 test books not used in development:
  chapters found 86% (precision 99.5%), footnotes found 89% (precision 99%),
  against 48% (86%) and 65% (70%) for the layout rules with the line-role
  models. Needs a Gemini API key (`GEMINI_API_KEY` or
  `~/.config/gemini/api_key`); about $0.004 a page with Gemini 3.8 Flash. The
  answers are kept in the work directory, so later runs ask nothing; pages
  Gemini blocks are read by the layout rules. `--gemini-model`,
  `--gemini-jobs`.
- `parisaocr epub --labels DIR`: the same structure from labels made
  elsewhere (the files `--gemini` writes).
- `parisaocr epub --review`: a local page to mark a book's parts, chapters,
  sections and figure pages by hand — thumbnails of every page in reading
  order with the converter's decisions on them, titles prefilled from the OCR,
  a live table of contents, Rebuild. The marks (`OUT/NAME.marks.json`, with
  the converter's own decisions beside them) are the truth about openings for
  every later run of the book. Where the rules and models fail on a book, three
  minutes of marking make its chapters right.
- Translators and editors are written to the EPUB as contributors (MARC roles
  `trl`, `edt`); a part with text of its own keeps its notes.

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
