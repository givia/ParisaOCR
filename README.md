# ParisaOCR (پریسا اوسی‌آر)

Open-source OCR for printed Persian. It reads scanned book pages about as well
as Google's Gemini Flash models, runs on your own machine (CPU or GPU, no
internet needed), and costs a few cents per thousand pages to run.

**Status: preview.** It works well on printed Persian books and
documents; see [Known limitations](#known-limitations). Feedback with example
pages is very welcome.

[فارسی](#فارسی) · [Model on Hugging Face](https://huggingface.co/givia/ParisaOCR) · [PyPI](https://pypi.org/project/parisaocr/)

## Install

```
pip install parisaocr
```

Python 3.10 to 3.13. The two models (text-line detector and recognizer, 22 MB)
are included. PyTorch is installed as a dependency of Kraken; a GPU is used
when one is available, otherwise the CPU (about 5 to 10 seconds per page).
For PDF input, Poppler's command-line tools must be installed
(`poppler-utils` on Debian and Ubuntu, `poppler` on Arch, Homebrew and conda-forge).

## Use

```
parisaocr ocr page.png                        # print the text
parisaocr ocr scans/ --out out                # out/txt/*.txt and out/hocr/*.hocr
parisaocr ocr book.pdf --out out --format txt,hocr,jsonl
parisaocr ocr book.pdf --out out --first 10 --last 20
parisaocr ocr book.pdf --out out --format pdf     # out/pdf/book.pdf: searchable
parisaocr epub book.pdf --out out                 # out/book.epub (experimental)
```

Inputs are images, directories of images, or a PDF (scanned pages are
extracted losslessly — a scan set inside a larger page, or under a site's name
typed over it, is drawn from the embedded image where the page places it,
without the stamp — and born-digital pages rendered). Outputs: plain text (one
line per printed line, columns right to left), hOCR with line and word boxes on
the original page, and JSONL with text, boxes and confidences.

### Searchable PDFs

`--format pdf` writes PDFs you can search, select and copy text in, with the
page images exactly as they were:

- **PDF input:** `out/pdf/NAME.pdf` is the original file with an invisible text
  layer over each page that was read. The scans are not re-encoded; page
  rotation is respected. Pages that already have a text layer (born-digital
  pages, or scans someone OCRed before) are left as they are; `--pdf-text add`
  adds ours to them too, e.g. over a poor earlier OCR layer, and `--pdf-text
  replace` puts ours in place of the text of every scan page. A text layer of
  glyph codes in another script than the book's (a broken OCR, found in many
  shared scans) is replaced in any case. A site's name stamped as text over
  every page is removed, from the page images as well as from this PDF.
- **Image input:** one PDF per image in `out/pdf/`, or all of them in one file
  with `--merge-pdf NAME`. The page size follows the image's dpi.

The text layer is stored the way word processors store Persian text (one run
per line in visual order), so PDF viewers find and copy it in reading order.
Tested with Poppler (`pdftotext`, used by Okular and Evince) and pdf.js
(Firefox): words and their order come out right; pdftotext sometimes moves
punctuation at the edge of a word, and pdf.js drops half-spaces when copying.
Tools that do not reorder right-to-left text (such as pdfminer) return each
line reversed, as they do for any Persian PDF.

### E-books (EPUB), experimental

```
parisaocr epub book.pdf --out out --title "…" --author "…"
```

turns a scanned Persian book into an EPUB 3 e-book, `out/book.epub`, that
reflows on phones and e-readers. Besides reading the pages, it works out the
book from the page layout and the printed table of contents, helped by small
line-role models (gradient-boosted trees over layout features, bundled; see
[MODEL_CARD.md](MODEL_CARD.md)) — no language model, nothing leaves your
machine:

- chapters, with their titles and the book's table of contents; section
  headings, paragraphs joined across pages, block quotes;
- footnotes as popup notes linked to their markers (numbered per page or
  through the book, or with asterisks);
- verse: two-hemistich lines, and poems set line by line with their stanzas;
- figures and tables as images cut from the page (`--tables html` for HTML
  tables, but OCR of table numbers is not reliable enough to trust);
- the printed page numbers as the e-book's page list; pages put in book order
  by their printed numbers (reversed runs, duplicate scans and missing pages
  are found and reported).

`out/book.report.md` lists what was decided and where to look first: pages
read with low confidence, notes whose marker was not found, contents entries
not found as chapters. The OCR is kept in `out/book.work`, so a second run
takes seconds. Set `EPUBCHECK_JAR` to an epubcheck jar to have the result
validated; `--roles none` uses the layout rules without the line-role models.

**Three minutes of your own marking make the chapters right whatever the
scan.** `--review` opens a local page (127.0.0.1, nothing leaves your machine)
with every page as a thumbnail in reading order and the converter's decisions
on them: parts, chapters and sections with their titles, figure pages. Click
to add or remove an opening, fix a title (the OCR of the page's first lines is
offered), set its level, then *Rebuild*. The marks are saved as
`out/book.marks.json`, used by every later run of the book, and the converter's
own decisions are kept beside them for comparison.

**With a Gemini API key, `--gemini` gives the best structure.** Every page
image goes to Google's Gemini with its OCR lines numbered, and Gemini says
what each line is: a chapter or section heading, a footnote or endnote, a
running header, a byline, and so on. It then decides the book's outline from
all the headings found and reads the title, authors, translators and publisher
from the cover, title and copyright pages. On 10 test books that played no
part in the development:

|                              | chapters found | of those right | footnotes found | of those right |
|------------------------------|---------------:|---------------:|----------------:|---------------:|
| layout rules + line roles    | 48%            | 86%            | 65%             | 70%            |
| `--gemini`                   | 86%            | 99.5%          | 89%             | 99%            |

Most chapters it misses are found as section headings instead: books where
the line between a chapter and a section is a judgement call. Put the key in
`GEMINI_API_KEY` or `~/.config/gemini/api_key`. Gemini 3.8 Flash costs about
$0.004 a page, about $1.20 for a 300-page book. The answers are kept in
`out/book.work/gemini-…`, so later runs and `--review` rebuilds cost nothing.
The page images and their OCR text are sent to Google; without `--gemini`
nothing leaves your machine.

This is experimental: it was developed on 23 books (novels, a poetry
anthology, histories with many footnotes, collections, a play, a book exported
from Word) and measured on 10 others, and will meet layouts it gets wrong. Typical errors: a chapter title with an OCR error
or cut short, a footnote linked at the end of its page instead of at its
marker, an unusual heading taken for a paragraph. Multi-column pages
(magazines) and dictionaries are not supported. Example pages of books it gets
wrong are very welcome in the issues.

From Python:

```python
from parisaocr import OCR

ocr = OCR()                         # loads the models once
print(ocr.text("page.png"))
for line in ocr.lines("page.png"):  # reading order
    print(line.bbox, line.conf, line.text)
```

## Accuracy

Measured on material the model never saw in training.

| Test set | ParisaOCR 0.1 | Gemini 3.8 Flash | Tesseract fas_print¹ |
|---|---|---|---|
| 32 book pages from 11 books, verified text: words found | **96.7%** | 94.7% | 94.7% |
| 750 lines from the same books: character error, ignoring spaces | 0.39% | 0.36% | 1.19% |
| same lines: word error | **6.1%** | 7.9% | 7.3% |
| 74 printed pages of [PersianML/persian-ocr-benchmark](https://huggingface.co/datasets/PersianML/persian-ocr-benchmark): words found | 77.9% | 81.7% | 71.1% |
| cost per 1,000 pages | about $0.14 (consumer GPU at $0.60/h) | $2.86 (list price) | about $0.10 |

¹ Our earlier Persian Tesseract model ([tessdata_contrib](https://github.com/tesseract-ocr/tessdata_contrib/pull/19)),
reading the lines found by the Surya detector.

On the benchmark the gap to Gemini comes mostly from very small,
low-resolution scans (lines under about 16 pixels high), where Gemini reads
about 78% of the words and ParisaOCR 74%. Many benchmark pages are hard even
for a human reader, and its page-level character error (about 40% for every
reader) mostly measures reading order on multi-column pages.

The verified test set is small (750 lines from 11 books), so treat the numbers
as a guide rather than a precise ranking. Details in [MODEL_CARD.md](MODEL_CARD.md).

## Known limitations

- **Printed Persian only.** Handwriting, Nastaliq calligraphy, manuscripts and
  Urdu are not supported.
- **Very small text** (lines under about 14 pixels high, i.e. low-resolution
  scans) loses accuracy; ParisaOCR prints a warning for such pages. Scan at
  300 dpi when you can.
- **Layout is simple.** Columns are read right to left and top to bottom;
  tables, captions and complex magazine layouts may come out in the wrong
  order. Text rotated sideways is not read.
- **Small ornaments** (a box, a dingbat) are sometimes read as a letter.
- **Spacing conventions.** The model follows standard Persian spelling for
  half-spaces (ZWNJ) and sometimes differs from the printed spacing.
- **Diacritics** (vowel marks) are read less reliably than letters.

## How it works

1. **Lines.** The PP-OCRv6 text detector (PaddleOCR, Apache-2.0) finds the
   text lines, run with ONNX Runtime.
2. **Crops.** Each line is cut out with a margin that keeps the dots above and
   below the letters; neighbouring lines inside the margin are painted over.
3. **Reading.** A Kraken recognition model (PP-OCR-style architecture, CTC)
   trained for Persian print reads each line. ParisaOCR turns the model's
   visual-order output into reading order itself, keeping the half-space
   (ZWNJ), and with each line's own direction, so English lines and footnotes
   come out right.
4. **Order.** Lines are grouped into columns, read right to left.

The model was trained on about 83,000 lines from about 200 scanned Persian
books, labelled automatically with Gemini, plus about 90,000 synthetic lines
(Persian and English text, special characters, low-resolution copies). See
[MODEL_CARD.md](MODEL_CARD.md), and the modules in [docs/architecture.md](docs/architecture.md).

## Other models and detectors

`--model` accepts another Kraken model (`kraken:model.safetensors`) or a
Tesseract model (`TESSDATA_DIR:LANG`); `--detector` accepts other PP-OCR sizes
(`ppocr:v6-medium`), Kraken's segmenter (`kraken`), or Surya (`surya`, after
`pip install parisaocr[surya]`; note that Surya's weights are free only for
research, personal use and small companies). `parisaocr cut` cuts the lines of
scanned books for building training data.

## Feedback

Please [open an issue](https://github.com/givia/ParisaOCR/issues/new/choose)
with the page (only if you are allowed to share it), the command you ran, and
what came out wrong.

## License

Apache License 2.0, for the code and the models. The bundled PP-OCRv6 detector
is PaddleOCR's (Apache-2.0); see [NOTICE](NOTICE).

---

## فارسی

پریسا اوسی‌آر یک اوسی‌آر متن‌باز برای متن چاپی فارسی است. صفحه‌های اسکن‌شدهٔ کتاب را
تقریباً هم‌اندازهٔ مدل‌های Gemini Flash گوگل می‌خواند، روی رایانهٔ خودتان اجرا می‌شود
(بدون نیاز به اینترنت، با CPU یا GPU) و هزینهٔ اجرایش برای هر هزار صفحه چند سنت است.

**وضعیت: پیش‌نمایش.** روی کتاب‌ها و اسناد چاپی فارسی خوب کار می‌کند. محدودیت‌ها
را پایین‌تر ببینید. بازخورد همراه با صفحهٔ نمونه بسیار ارزشمند است.

نصب:

```
pip install parisaocr
```

استفاده:

```
parisaocr ocr page.png                      # چاپ متن
parisaocr ocr book.pdf --out out            # out/txt و out/hocr
parisaocr ocr book.pdf --out out --format pdf   # out/pdf/book.pdf: پی‌دی‌اف جست‌وجوپذیر
```

با `--format pdf` خروجی یک پی‌دی‌اف جست‌وجوپذیر است: تصویر صفحه‌ها همان است که بود و
یک لایهٔ متن نامرئی روی آن قرار می‌گیرد، تا بتوانید در آن جست‌وجو کنید و متن را انتخاب
و کپی کنید.

کتاب الکترونیکی (EPUB، آزمایشی):

```
parisaocr epub book.pdf --out out --title "…" --author "…"
```

از کتاب اسکن‌شده یک کتاب EPUB می‌سازد که روی گوشی و کتاب‌خوان خوانده می‌شود: فصل‌ها
و فهرست، پاراگراف‌ها، پانویس‌ها (با پیوند به شمارهٔ پانویس در متن)، شعر، تصویرها و
جدول‌ها، و شمارهٔ صفحه‌های چاپی. گزارشی هم (`out/book.report.md`) می‌نویسد که نشان
می‌دهد کجا را باید بررسی کرد. این قابلیت آزمایشی است؛ اگر کتابی درست تبدیل نشد، لطفاً
در بخش Issues خبر بدهید.

محدودیت‌ها:

- فقط متن چاپی فارسی. دست‌نوشته، خط نستعلیق، نسخهٔ خطی و اردو پشتیبانی نمی‌شوند.
- متن خیلی ریز (اسکن با وضوح پایین) دقت کمتری دارد. در صورت امکان با ۳۰۰ dpi اسکن کنید.
- چیدمان صفحه ساده خوانده می‌شود. جدول‌ها و صفحه‌های مجله‌ای پیچیده ممکن است ترتیب
  درستی نداشته باشند.
- نیم‌فاصله‌ها طبق املای استاندارد نوشته می‌شوند و گاهی با چاپ کتاب فرق دارند.
- اِعراب‌ها کمتر از حروف دقیق خوانده می‌شوند.

بازخورد: لطفاً در [بخش Issues](https://github.com/givia/ParisaOCR/issues/new/choose) صفحه
(فقط اگر اجازهٔ انتشارش را دارید)، فرمانی که اجرا کردید و خطای خروجی را بفرستید.

مجوز: Apache 2.0، برای کد و مدل‌ها.
