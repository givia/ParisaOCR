"""The 0.5.0 footnote repairs, on made-up lines: a low-confidence speck is not joined into a line, the note-number
re-read gives nothing on a page without numbered notes, and marker linking never overwrites a number of the text."""
import json

from PIL import Image

from parisaocr.ebook import labels, markernum, markers, notenum, source
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


def _line(text, y=100):
    """A right-to-left line whose words sit from x=990 leftwards, 100 px each."""
    words = [source.Word(w, (900 - 100 * i, y, 990 - 100 * i, y + 40), 95.0) for i, w in enumerate(text.split())]
    return source.Line(text, (900 - 100 * (len(words) - 1), y, 990, y + 40), 95.0, words)


def test_markers_read_on_their_own(monkeypatch):
    """A raised mark read on its own (`markernum`) stands for the marker only where the line has no number of the
    page's notes there, and only as one of them; a number the line reader read elsewhere on the page stays its own."""
    x = 650  # over the fourth word, "خواند"
    for text, reread, want in (
            ("این کتاب را خواند و رفت", "۱۰۵", "این کتاب را خواند" + markers.reread_mark("۱۰۵") + " و رفت"),  # unread
            ("این کتاب را خواند۱۰ و رفت", "۱۰۵", "این کتاب را خواند۱۰۵ و رفت"),  # a digit lost by the line reader
            ("این کتاب را خواند ۱۰ و", "۱۰۵", "این کتاب را خواند۱۰۵ و"),  # read as a word of its own, next to the mark
            ("این کتاب را خواند۱۰۶ و رفت", "۱۰۵", "این کتاب را خواند۱۰۶ و رفت"),  # the line's number is a note: kept
            ("این کتاب را خواند و رفت", None, "این کتاب را خواند" + MARK + " و رفت")):  # no reading: as before
        l = _line(text)
        markers._insert(l, x, MARK, reread, frozenset({105, 106}))
        assert l.text == want, (text, l.text)

    class P:
        index, width, height, image = 1, 1000, 1400, "p-001.png"

    class L:
        page = P()
        body = [_line("واحساسی بکار می برد اصولاً شاعری نغمه", 100), _line("قطعاتی نظیرا خیلوس درمقابل محراب۴ و", 200)]
        notes = [source.Line("۴ یادداشت", (100, 1200, 900, 1240), 95.0, [])]

    marks = [(L.body[0], (510, 530, 110))]  # a tanwin over "اصولاً" taken for a raised mark
    monkeypatch.setattr(markernum, "enabled", lambda: True)
    monkeypatch.setattr(markernum, "read", lambda page, jobs: {k: "۴" for k, _ in jobs})
    reads, notes = markers._reread(L, marks)
    assert notes == {4} and reads == {}  # "۴" is read on the page already (محراب۴): the mark does not take note 4


def test_reread_markers_link_or_vanish():
    """A marker read on its own is one character (`markers.reread_mark`): it links the page's note of its number if
    that one is free; otherwise it is an unread mark (the next free note, or nothing). It never shows: a vowel sign
    taken for a mark must not become a digit in the text."""
    from parisaocr.ebook import structure
    asm = structure.Assembler([], {}, [])
    asm.new_chapter("فصل", 1)
    five, six = structure.Note("n1-5", 1, 5, "یادداشت"), structure.Note("n1-6", 1, 6, "دیگری")
    asm.notes_by_page[1] = {5: five, 6: six}
    r = markers.reread_mark
    items = asm.inline("کتاب" + r("۹") + " را خواند و" + r("۶") + " رفت و" + r("۷") + " آمد", 1)
    refs = [x.note for x in items if isinstance(x, structure.NoteRef)]
    text = "".join(x for x in items if isinstance(x, str))
    # ۹ is no note here: as an unread mark it takes the next one, 5; ۶ links note 6; ۷ finds no note after it and goes
    assert refs == [five, six] and text == "کتاب را خواند و رفت و آمد"
    # where the text is not linked (front matter, a table cell, a heading), the mark goes as MARK does
    assert ("دیباچه" + r("۴") + " کتاب").translate(structure._UNMARK) == "دیباچه کتاب"


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
