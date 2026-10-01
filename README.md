# ParisaOCR (پریسا اوسی‌آر)

Open-source OCR for printed Persian. It reads scanned book pages about as well
as Google's Gemini Flash models, runs on your own machine (CPU or GPU, no
internet needed), and costs a few cents per thousand pages to run.

**Status: preview (0.1).** It works well on printed Persian books and
documents; see [Known limitations](#known-limitations). Feedback with example
pages is very welcome.

[فارسی](#فارسی)

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
```

Inputs are images, directories of images, or a PDF (scanned pages are
extracted losslessly, born-digital pages rendered). Outputs: plain text (one
line per printed line, columns right to left), hOCR with line and word boxes on
the original page, and JSONL with text, boxes and confidences.

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

**وضعیت: پیش‌نمایش (۰٫۱).** روی کتاب‌ها و اسناد چاپی فارسی خوب کار می‌کند. محدودیت‌ها
را پایین‌تر ببینید. بازخورد همراه با صفحهٔ نمونه بسیار ارزشمند است.

نصب:

```
pip install parisaocr
```

استفاده:

```
parisaocr ocr page.png                 # چاپ متن
parisaocr ocr book.pdf --out out       # out/txt و out/hocr
```

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
