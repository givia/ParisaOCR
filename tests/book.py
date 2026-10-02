"""A small made-up Persian book as a scanned PDF, for testing `parisaocr epub` without copyrighted pages.

Pages (book page = PDF page): 1 title page, 2 contents, 3 chapter one opening,
4 its second page with a running header and a footnote under a rule, 5 chapter
two opening, 6 its second page. Typeset with Pillow (Raqm shaping) in the
bundled Vazirmatn at 200 dpi, with a site's name stamped in the top left
corner of every page.
"""
import pathlib

from PIL import Image, ImageDraw, ImageFont

FONT = pathlib.Path(__file__).resolve().parent.parent / "parisaocr" / "fonts" / "Vazirmatn-Regular.ttf"
W, H, DPI = 1165, 1654, 200  # A5
MARGIN, SIZE, PITCH = 130, 30, 58
FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

CHAPTERS = [
    ("فصل اول", "باغ و باران", [
        "باران از نیمه‌شب آغاز شده بود و تا صبح یک لحظه هم بند نیامد. پدربزرگ کنار پنجره نشسته بود و به درخت‌های "
        "باغ نگاه می‌کرد که زیر باران سنگین خم شده بودند. او هر سال در همین روزها نهال تازه‌ای می‌کاشت و می‌گفت "
        "باغی که هر سال درخت تازه‌ای نگیرد کم‌کم پیر می‌شود.",
        "مادر چای را روی میز گذاشت و گفت که امسال باید زودتر به فکر هیزم زمستان باشیم. برادر کوچکم که تازه از "
        "خواب بیدار شده بود با تعجب به آسمان خاکستری نگاه کرد و پرسید چرا خورشید امروز بیرون نمی‌آید.",
        "نزدیک ظهر باران آرام‌تر شد. همسایه‌ها یکی‌یکی از خانه‌ها بیرون آمدند و جوی‌های کوچه را که از برگ و "
        "شاخه پر شده بود پاک کردند. بوی خاک خیس همه‌جا را پر کرده بود و صدای آب در ناودان‌ها مثل آوازی آرام "
        "شنیده می‌شد. پدربزرگ گفت این بو را از کودکی به یاد دارد و هیچ عطری به پای آن نمی‌رسد.",
        "عصر همان روز همه به باغ رفتیم. زمین نرم بود و پاها تا مچ در گل فرو می‌رفت. پدربزرگ جای نهال را با "
        "دقت انتخاب کرد، گودالی کند و ریشه‌ها را با احتیاط در خاک گذاشت. بعد دستی به تنهٔ باریک درخت کشید و "
        "گفت که اگر زنده باشیم ده سال دیگر زیر سایه‌اش چای خواهیم خورد.",
    ]),
    ("فصل دوم", "راه شهر", [
        "صبح روز بعد آسمان صاف بود. پدر تصمیم گرفت برای خرید به شهر برود و مرا هم با خود برد. جادهٔ خاکی "
        "پس از باران پر از چاله بود و ماشین کهنهٔ ما آهسته از میان آن‌ها می‌گذشت. در دو طرف جاده مزرعه‌های "
        "گندم تا افق ادامه داشت.",
        "در بازار شهر همه‌چیز پیدا می‌شد. فروشنده‌ها با صدای بلند جنس‌هایشان را معرفی می‌کردند و مردم با "
        "حوصله چانه می‌زدند. پدر چند کیسه آرد و مقداری میخ و طناب خرید و بعد به کتاب‌فروشی کوچکی رفتیم که "
        "صاحبش از دوستان قدیمی او بود.",
        "کتاب‌فروش پیرمردی لاغر با عینکی گرد بود. او کتابی دربارهٔ درختان میوه به پدر نشان داد و گفت که این "
        "کتاب را سال‌ها پیش برای خودش کنار گذاشته بود. پدر کتاب را خرید تا به پدربزرگ هدیه بدهد. در راه بازگشت "
        "خورشید پشت کوه‌ها پایین می‌رفت و آسمان رنگ نارنجی گرفته بود.",
        "وقتی به خانه رسیدیم پدربزرگ هنوز در باغ بود. کتاب را که دید خندید و گفت حالا دیگر بهانه‌ای برای "
        "بیکاری ندارد. آن شب تا دیروقت کنار چراغ نشست و صفحه‌به‌صفحه کتاب را خواند.",
    ]),
]
NOTE = "۱- این داستان بر پایهٔ خاطرات خانوادگی نوشته شده است."


def _font(size):
    return ImageFont.truetype(str(FONT), size)


def _rtl(draw, right, y, text, font):
    """Right-aligned right-to-left text whose right edge is at RIGHT; returns its width."""
    width = draw.textlength(text, font=font, direction="rtl")
    draw.text((right - width, y), text, font=font, fill=0, direction="rtl")
    return width


def _center(draw, y, text, font):
    width = draw.textlength(text, font=font, direction="rtl")
    draw.text(((W - width) / 2, y), text, font=font, fill=0, direction="rtl")


def _wrap(draw, text, font, width, indent):
    lines, cur = [], ""
    for word in text.split(" "):
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font, direction="rtl") > width - (indent if not lines else 0):
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur]


STAMP = "www.example-books.com"  # a site's name burned into every scan, as shared copies often have


def _page():
    im = Image.new("L", (W, H), 255)
    draw = ImageDraw.Draw(im)
    draw.text((20, 14), STAMP, font=_font(24), fill=0)
    return im, draw


def _justified(draw, right, left, y, line, font):
    """A line set word by word from RIGHT to LEFT with the spaces stretched to fill the measure."""
    words = line.split(" ")
    widths = [draw.textlength(w, font=font, direction="rtl") for w in words]
    gap = (right - left - sum(widths)) / max(1, len(words) - 1)
    x = right
    for word, width in zip(words, widths):
        draw.text((x - width, y), word, font=font, fill=0, direction="rtl")
        x -= width + gap


def _body(draw, paragraphs, y, font, marker_in=None):
    """Justified paragraphs from Y down; the first line of each indented. Returns the y after the text."""
    right, width = W - MARGIN, W - 2 * MARGIN
    for k, para in enumerate(paragraphs):
        if marker_in is not None and k == marker_in[0]:
            para = para.replace(marker_in[1], marker_in[1] + "۱", 1)  # a note marker glued to the word
        lines = _wrap(draw, para, font, width, 45)
        for i, line in enumerate(lines):
            r = right - (45 if i == 0 else 0)
            if i < len(lines) - 1:
                _justified(draw, r, MARGIN, y, line, font)
            else:
                _rtl(draw, r, y, line, font)
            y += PITCH
    return y


def make_book(path):
    font, big, small = _font(SIZE), _font(52), _font(24)
    pages = []

    im, d = _page()  # 1: title page
    _center(d, 520, "باغ پدربزرگ", _font(64))
    _center(d, 660, "داستان", font)
    _center(d, 760, "نوشتهٔ نویسندهٔ آزمایشی", font)
    pages.append(im)

    im, d = _page()  # 2: contents
    _center(d, 200, "فهرست مطالب", big)
    y = 340
    for (label, title, _), page in zip(CHAPTERS, (3, 5)):
        entry = f"{label}: {title}"
        _rtl(d, W - MARGIN, y, entry, font)
        num = str(page).translate(FA)
        width = d.textlength(num, font=font)
        d.text((MARGIN, y), num, font=font, fill=0)
        dots_from = W - MARGIN - d.textlength(entry, font=font, direction="rtl") - 20
        d.text((MARGIN + width + 20, y), "." * int((dots_from - MARGIN - width - 20) / 9), font=font, fill=0)
        y += 80
    pages.append(im)

    for c, (label, title, paras) in enumerate(CHAPTERS):
        first = 2 * c + 3
        im, d = _page()  # chapter opening: no running header, the text starts a third down the page
        _center(d, 480, label, font)
        _center(d, 550, title, big)
        _body(d, paras[:2], 720, font)
        pages.append(im)

        im, d = _page()  # its second page: running header with the page number, maybe a footnote
        _center(d, 70, title, small)
        d.text((MARGIN, 70), str(first + 1).translate(FA), font=small, fill=0)
        _body(d, paras[2:], 170, font, marker_in=(0, "خاک") if c == 0 else None)
        if c == 0:
            d.line((W - MARGIN - 300, H - 260, W - MARGIN, H - 260), fill=0, width=2)
            _rtl(d, W - MARGIN, H - 235, NOTE, small)
        pages.append(im)

    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=DPI)
    return path


if __name__ == "__main__":
    import sys
    make_book(sys.argv[1] if len(sys.argv) > 1 else "book.pdf")
