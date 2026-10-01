---
license: apache-2.0
language:
- fa
- en
library_name: kraken
pipeline_tag: image-to-text
tags:
- ocr
- persian
- farsi
- text-recognition
- kraken
- ppocr
---

# ParisaOCR recognition model 0.1

A text-line recognizer for printed Persian, the reading model of
[ParisaOCR](https://github.com/givia/ParisaOCR). It reads one cropped line
image at a time; ParisaOCR adds line detection (PP-OCRv6), cropping and reading
order, and is the intended way to use it:

```
pip install parisaocr
parisaocr ocr page.png
```

## Model

- Architecture: Kraken 7.1's `ppocrv6` recognizer (PP-OCR-style convolutional
  backbone with an SVTR neck and a CTC head), variant `small`, about 3 million
  parameters; input lines are scaled to 96 pixels high.
- Output: Persian and Latin letters, Persian and Latin digits, punctuation,
  common symbols (٪ % ٫ ٬ × ÷ ° ± $ € £ and more), diacritics and the
  zero-width non-joiner (ZWNJ, half-space); 182 classes.
- File: `parisaocr-fa-0.1.safetensors`, 12 MB.

**Use it through ParisaOCR.** Plain `kraken ocr` gives worse results with this
model, for two reasons we reported to Kraken: PP-OCR models must see inputs
padded to the training width (mittagessen/kraken#810, otherwise characters are
invented at the start of Persian lines), and Kraken's bidi reordering deletes
the ZWNJ (mittagessen/kraken#809). The model was trained on labels put into
visual order by ParisaOCR's own reordering (`parisaocr/bidi.py`, with each
line's own base direction), which ParisaOCR also applies to its output.

## Training data

| Part | Lines per epoch |
|---|---|
| Real lines from 195 scanned Persian books, each seen twice | 83,016 × 2 |
| Low-resolution copies of a third of those lines (as on 60-100 dpi scans) | 27,395 |
| Synthetic lines rendered from Persian Wikipedia text in about 45 fonts | 39,859 |
| Synthetic English, mixed Persian-English, special-character and diacritic lines (Persian and English Wikipedia text) | 51,070 |

- **Books.** About 200 scanned Persian books, mostly 20th-century and modern
  print, from 60 to over 600 dpi, from a public online collection whose
  copyright status varies and is unknown to us. Twelve pages per book were
  used. The scans, line images and labels are not redistributed; only the
  trained model is.
- **Labels.** Each real line was transcribed once, automatically, by Google
  Gemini (3.8 Flash for most books, 3.7 Flash for 40), reading line images in
  sheets of eight, in standard Persian spelling. Labels were not checked by
  people. On a human-verified test set the teacher's character error, ignoring
  spaces, is 0.25-0.36%, a rough measure of the label noise.
- **Synthetic lines** were rendered with Tesseract's text2image and degraded to
  look like scans (blur, noise, resolution loss, JPEG, binarization). Wikipedia
  text is licensed CC BY-SA 4.0.
- **Training.** From scratch for 12 epochs on a first set (round one, about
  100 books), fine-tuned for 4 epochs with the ZWNJ kept, then fine-tuned for 4
  epochs on the set above (round two). One RTX 4070 Ti, about 15 GPU hours in
  total. Validation used 6 whole books held out of training.

## Evaluation

All test material is from books and fonts that were not used for training.

| Test set | This model | Gemini 3.8 Flash | Gemini 3.7 Flash | Tesseract fas_print |
|---|---|---|---|---|
| 750 human-verified lines, 11 books: character error, ignoring spaces | 0.39% | 0.36% | 0.25% | 1.19% |
| same: character error | 1.28% | 1.62% | 1.34% | 1.67% |
| same: word error | 6.1% | 7.9% | 6.8% | 7.3% |
| 32 pages of those books (ParisaOCR pipeline): words found | 96.7% | 94.7% (whole page) | | 94.7% |
| 946 English lines, unseen fonts: character error | 0.24% | | | |
| 1,000 mixed Persian-English lines, unseen fonts: character error | 4.8% | | | |
| 992 lines dense in special characters, unseen fonts: character error | 6.0% | | | |
| 74 printed pages, PersianML/persian-ocr-benchmark: words found | 77.9% | 81.7% | | 71.1% |

The verified lines keep the spacing of the print; the model and Gemini write
standard spelling, so measures that count spaces penalize both slightly. The
verified set is small, so differences of a few tenths of a percent are within
noise. The three synthetic line sets are rendered, not scanned.

## Limitations

- Printed Persian only: not handwriting, Nastaliq, manuscripts or Urdu.
- Accuracy drops on very small text (lines under about 14 pixels high).
- Diacritics and rare symbols are less reliable than letters.
- Half-spaces follow standard spelling, which can differ from the print.
- A label noise floor from the automatic teacher.

## License

Apache License 2.0.
