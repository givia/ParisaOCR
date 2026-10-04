"""The 0.5.0 footnote repairs, on made-up lines: a low-confidence speck is not joined into a line, the note-number
re-read gives nothing on a page without numbered notes, and marker linking never overwrites a number of the text."""
import json

from PIL import Image

from parisaocr.ebook import labels, notenum, source
from parisaocr.ebook.markers import MARK
from parisaocr.ebook.structure import _MARKER, _marker_num


def _ocr_dir(tmp_path, rows):
    d = tmp_path / "ocr"
    (d / "pages").mkdir(parents=True)
    (d / "jsonl").mkdir()
    Image.new("L", (1000, 1400), 255).save(d / "pages" / "p-001.png")
    (d / "jsonl" / "p-001.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return d


def _row(text, bbox, conf=95, order=True):
    r = {"page": "p-001", "column": 0, "bbox": bbox, "text": text, "conf": conf,
         "words": [{"text": w, "bbox": bbox, "conf": conf} for w in text.split()]}
    if order:
        r["order"] = 2
    return r


def test_specks_are_not_joined(tmp_path):
    body = [_row("متن اصلی کتاب در این سطر نوشته شده است و ادامه دارد", [100, 100 + 60 * k, 900, 150 + 60 * k]) for k in range(6)]
    note = _row("یادداشتی کوتاه در پایین صفحه برای این متن", [100, 1200, 860, 1235])
    speck = _row("9", [870, 1198, 900, 1232], conf=37)  # a speck read as a digit beside a note line
    pages = source.load(_ocr_dir(tmp_path, body + [note, speck]))
    texts = [l.text for l in pages[0].lines]
    assert note["text"] in texts and all(l.conf >= 90 for l in pages[0].lines if l.text == note["text"])


def test_note_numbers_need_numbered_notes_on_the_page(tmp_path):
    pages = source.load(_ocr_dir(tmp_path, [_row("یک سطر از متن بدون هیچ پانویسی در صفحه", [100, 900, 900, 940])]))
    p = pages[0]
    assert not notenum.fits(p, p.lines[0], "1")  # no numbered note line: a "1" read from a letter is not taken


def test_marker_linking_guards():
    class L:
        pass
    l = L()
    l.text, l.markers = "فرستاد" + MARK + " و روز ۱۵ آمد", [1]
    assert labels.marked_text(l) == "فرستاد۱ و روز ۱۵ آمد"  # the image mark, not the date
    l.text, l.markers = "متن پایان", [3, 4]
    found = {_marker_num(m) for m in _MARKER.finditer(labels.marked_text(l))}
    assert {3, 4} <= found  # two markers appended at the line's end are both linked
    assert labels._place_after_words("ج۷ زر الف ب", [5, 7], ["الف", "ب"]) == "ج زر الف۵ ب۷"


def test_misread_markers_conservative():
    """A marker read one digit off is replaced; numbers of the text (a percentage, a page or verse reference, a date
    joined to the marker) are never overwritten: the marker then goes at the line's end."""
    class L:
        pass

    def mark(text, want):
        l = L()
        l.text, l.markers = text, want
        return labels.marked_text(l)
    assert mark("این کتاب نایاب به‌شمار می‌آمد. ۱۷", [107]) == "این کتاب نایاب به‌شمار می‌آمد.۱۰۷"
    assert mark("موضوع بررسی شده.۳۳ و سپس", [32]) == "موضوع بررسی شده.۳۲ و سپس"
    assert mark("گفته بود." + MARK + " ۱۶۳ و رفت", [263]) == "گفته بود.۲۶۳ و رفت"
    assert mark("رشد سالانه ٪۴,۴. بود", [35]).startswith("رشد سالانه ٪۴,۴. بود")
    assert mark("(نویسنده، ۱۳۸۶: ۴۴) آمده", [45]).startswith("(نویسنده، ۱۳۸۶: ۴۴) آمده")
    assert mark("نگاه کنید به ص۳۹۷). و", [398]).startswith("نگاه کنید به ص۳۹۷). و")
