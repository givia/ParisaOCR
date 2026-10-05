"""The app's forms: every option of `parisaocr ocr`, `epub` and `pages`, grouped and labelled in Persian and English.

The options themselves (flags, defaults, choices, types, help) are read from the command line's own parser, so the
form and the command never disagree; this module only adds the grouping and the labels. An option that is neither
laid out here nor in MANAGED still reaches the form, under "other options" with the command line's help, so a new
command-line option is never out of the app's reach (tests/test_app.py checks that the layout covers them all).
The values of a form become the command's arguments (`command`), checked by the parser before anything runs.
"""
import argparse
import pathlib
import shlex

KINDS = {"epub": "epub", "ocr": "ocr", "pages": "pages"}  # job kind -> command

# options the app fills itself: inputs, output folders, and the review it runs on its own
MANAGED = {
    "epub": {"book", "out", "work", "review", "port", "llm_estimate"},
    "ocr": {"input", "out"},
    "pages": {"pdf", "out"},
}

SECTIONS = {
    "book": ("مشخصات کتاب", "Book details"),
    "structure": ("ساختار کتاب", "Book structure"),
    "output": ("خروجی", "Output"),
    "pdf": ("صفحه‌های PDF", "PDF pages"),
    "engine": ("موتور اوسی‌آر", "OCR engine"),
    "advanced": ("تنظیمات پیشرفتهٔ موتور", "Advanced engine settings"),
    "run": ("اجرا", "Run"),
    "other": ("گزینه‌های دیگر", "Other options"),
}

ENGINE = [
    ("engine", "model", "مدل بازشناسی", "Recognition model",
     "مدل پریسا اوسی‌آر همراه بسته (default)؛ مدل‌های دیگر: kraken:PATH، یا TESSDATA:LANG برای Tesseract",
     "default: the ParisaOCR model shipped with the package; or kraken:PATH, or TESSDATA:LANG for Tesseract",
     {"suggest": ["default"]}),
    ("engine", "detector", "آشکارساز سطر", "Line detector", "", "",
     {"suggest": ["ppocr:v6-small:1.3", "ppocr:v6-medium:1.3", "surya", "kraken"]}),
    ("engine", "cpu", "فقط با CPU (بدون GPU)", "CPU only (no GPU)", "", "", {}),
    ("advanced", "min_width", "کمترین پهنای صفحه برای آشکارسازی (پیکسل)", "Upscale narrower pages to (px)", "", "", {}),
    ("advanced", "max_width", "بیشترین پهنا برای آشکارسازی (۰ = بی‌حد)", "Reduce wider pages to (px, 0 = never)", "", "", {}),
    ("advanced", "batch", "صفحه در هر دسته", "Pages per detector batch", "", "", {}),
    ("advanced", "pad", "کمترین حاشیهٔ برش سطر (پیکسل)", "Least line-crop margin (px)", "", "", {}),
    ("advanced", "pad_frac", "حاشیه به نسبت ارتفاع سطر", "Margin as a fraction of the line height", "", "", {}),
    ("advanced", "min_height", "بزرگ‌کردن برش‌های کوتاه‌تر از (پیکسل)", "Enlarge crops shorter than (px)", "", "", {}),
    ("advanced", "block_tall", "کادرهای بلند به‌صورت بلوک متن خوانده شوند (Tesseract)",
     "Read tall boxes as text blocks (Tesseract)", "", "", {}),
    ("advanced", "fallback", "سطرهای تقریباً خالی دوباره خوانده شوند (Tesseract)",
     "Retry near-empty lines as single lines (Tesseract)", "", "", {}),
    ("advanced", "jobs", "پردازش‌های موازی Tesseract", "Parallel Tesseract processes", "", "", {}),
    ("run", "redo", "از نو (نتیجه‌های ذخیره‌شده نادیده گرفته شوند)", "Start over (ignore cached results)", "", "", {}),
]

PDF_PAGES = [
    ("pdf", "first", "از صفحهٔ", "First page", "", "", {}),
    ("pdf", "last", "تا صفحهٔ", "Last page", "", "", {}),
    ("pdf", "dpi", "DPI رندر", "Render DPI", "", "", {}),
]
PDF_MODE_CHOICES = {"auto": ("خودکار", "decide per file"), "extract": ("تصویر اسکن درون PDF", "extract the scan images"),
                    "render": ("رندر با DPI", "render at the DPI")}

LLM_PRESETS = [
    {"value": "gemini-3.8-flash", "key": "gemini",
     "fa": "Gemini 3.8 Flash: بهترین کیفیت؛ حدود ۱٫۲۵ دلار برای کتابی ۳۰۰ صفحه‌ای",
     "en": "Gemini 3.8 Flash: best quality; about $1.25 for a 300-page book"},
    {"value": "openrouter:qwen/qwen3.8-27b", "key": "openrouter",
     "fa": "Qwen3.8 27B از راه OpenRouter: حدود ۰٫۵ دلار", "en": "Qwen3.8 27B through OpenRouter: about $0.50"},
    {"value": "openrouter:google/gemma-4-31b-it", "key": "openrouter",
     "fa": "Gemma 4 31B از راه OpenRouter: حدود ۰٫۱۵ دلار", "en": "Gemma 4 31B through OpenRouter: about $0.15"},
    {"value": "gemma-4-31b-it", "key": "gemini", "jobs": 2,
     "fa": "Gemma 4 31B رایگان با کلید Gemini: کند (دو صفحه هم‌زمان)",
     "en": "Gemma 4 31B, free with a Gemini key: slow (two pages at a time)"},
]

LAYOUT = {
    "epub": [
        ("book", "name", "نام فایل خروجی", "Output file name", "بدون پسوند؛ اگر خالی بماند نام PDF", "without extension; the PDF's name if empty", {}),
        ("book", "title", "عنوان", "Title", "", "", {}),
        ("book", "subtitle", "زیرعنوان", "Subtitle", "", "", {}),
        ("book", "author", "نویسنده", "Author", "", "", {}),
        ("book", "publisher", "ناشر", "Publisher", "", "", {}),
        ("book", "language", "زبان", "Language", "اگر خالی بماند fa", "fa if empty", {}),
        ("book", "isbn", "شابک (ISBN)", "ISBN", "", "", {}),
        ("book", "meta", "فایل JSON مشخصات", "Metadata JSON file", "مسیر فایلی با title، author و … روی همین رایانه",
         "path of a file with title, author, ... on this computer", {}),
        ("structure", "llm", "تعیین ساختار", "Structure decided by", "", "", {"widget": "llm", "presets": LLM_PRESETS}),
        ("structure", "llm_jobs", "صفحه‌های هم‌زمان برای مدل زبانی", "Pages asked at the same time", "پیش‌فرض ۸", "default 8", {"needs": "llm"}),
        ("structure", "labels", "پوشهٔ برچسب‌های موجود", "Existing labels folder",
         "برچسب‌هایی که قبلاً یک مدل زبانی نوشته؛ به‌جای قاعده‌ها ساختار را تعیین می‌کنند",
         "labels a language model wrote earlier: they decide the structure instead of the layout rules", {}),
        ("structure", "roles", "مدل‌های نقش سطر", "Line-role models", "خالی: مدل‌های همراه بسته؛ none: فقط قاعده‌ها",
         "empty: the bundled ones; none: the layout rules alone", {}),
        ("structure", "tables", "جدول‌ها", "Tables", "", "",
         {"choices": {"image": ("تصویر بریده از صفحه", "images cut from the page"), "html": ("جدول HTML", "HTML tables")}}),
        *ENGINE,
    ],
    "ocr": [
        ("output", "format", "قالب‌های خروجی", "Output formats", "", "", {"widget": "formats", "choices": {
            "txt": ("متن ساده (txt)", "plain text (txt)"),
            "hocr": ("hOCR: متن با جای کلمه‌ها", "hOCR: text with word boxes"),
            "jsonl": ("JSONL: سطرها با کادر و اطمینان", "JSONL: lines with boxes and confidence"),
            "pdf": ("PDF جست‌وجوپذیر", "searchable PDF")}}),
        ("output", "merge_pdf", "یک PDF برای همهٔ تصویرها (نام فایل)", "One PDF for all input images (name)",
         "فقط برای ورودی تصویری، با قالب PDF", "image inputs with the PDF format only", {"needs": "pdf"}),
        ("output", "pdf_text", "صفحه‌هایی که از قبل متن دارند", "Pages that already have text", "برای ورودی PDF، با قالب PDF",
         "a PDF input with the PDF format", {"needs": "pdf", "choices": {
             "skip": ("دست نخورند", "leave them"), "add": ("متن ما هم اضافه شود", "add ours too"),
             "replace": ("متن ما جایگزین شود", "put ours in place")}}),
        ("output", "order", "ترتیب سطرها", "Line order", "", "",
         {"choices": {"rtl": ("ستون‌ها از راست به چپ", "right-to-left columns"), "raster": ("از بالا به پایین", "top to bottom")}}),
        ("pdf", "pdf", "گرفتن صفحه‌ها از PDF", "PDF pages", "", "", {"choices": PDF_MODE_CHOICES}),
        *PDF_PAGES,
        *ENGINE,
    ],
    "pages": [
        ("pdf", "pdf_mode", "گرفتن صفحه‌ها از PDF", "PDF pages", "", "", {"choices": PDF_MODE_CHOICES}),
        *PDF_PAGES,
        ("run", "redo", "از نو (صفحه‌های موجود نادیده گرفته شوند)", "Start over (ignore existing pages)", "", "", {}),
    ],
}


def subparser(cmd, cls=argparse.ArgumentParser):
    from ..cli import parser
    ap = parser(cls)
    return next(a for a in ap._actions if isinstance(a, argparse._SubParsersAction)).choices[cmd]


def _kind(a):
    if isinstance(a, argparse._StoreTrueAction):
        return "check"
    if isinstance(a, argparse._StoreFalseAction):
        return "check"  # the form shows the positive side: checked = the default, unchecked = the --no-... flag
    if a.type in (int, float):
        return "number"
    if a.choices:
        return "select"
    return "text"


def _default(a):
    if isinstance(a, argparse._StoreFalseAction):
        return True
    return a.default


def schema(kind):
    """The form of a job KIND: [{section, title_fa, title_en, fields: [{dest, flag, kind, default, choices, ...}]}]."""
    sub = subparser(KINDS[kind])
    actions = {a.dest: a for a in sub._actions if a.option_strings and a.help != argparse.SUPPRESS
               and not isinstance(a, (argparse._HelpAction, argparse._VersionAction))}
    laid = {}
    for section, dest, fa, en, desc_fa, desc_en, extra in LAYOUT[kind]:
        a = actions.get(dest)
        if a is None:
            continue
        f = {"dest": dest, "flag": max(a.option_strings, key=len), "kind": _kind(a), "default": _default(a),
             "label_fa": fa, "label_en": en, "desc_fa": desc_fa, "desc_en": desc_en, "help": a.help or "", **extra}
        if a.choices and "choices" not in extra:
            f["choices"] = {c: (c, c) for c in a.choices}
        if a.type is float:
            f["step"] = "any"
        laid.setdefault(section, []).append(f)
    for dest, a in actions.items():  # what the layout does not name yet: shown with the command line's own help
        if dest in MANAGED[kind] or any(dest == d for _, d, *_ in LAYOUT[kind]):
            continue
        f = {"dest": dest, "flag": max(a.option_strings, key=len), "kind": _kind(a), "default": _default(a),
             "label_fa": max(a.option_strings, key=len), "label_en": max(a.option_strings, key=len),
             "desc_fa": "", "desc_en": "", "help": a.help or ""}
        if a.choices:
            f["choices"] = {c: (c, c) for c in a.choices}
        laid.setdefault("other", []).append(f)
    return [{"section": s, "title_fa": SECTIONS[s][0], "title_en": SECTIONS[s][1], "fields": laid[s]}
            for s in SECTIONS if s in laid]


def uncovered(kind):
    """Options of the command that neither the layout nor MANAGED names (they land under "other options")."""
    return [f["dest"] for s in schema(kind) if s["section"] == "other" for f in s["fields"]]


class _Refuse(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)

    def exit(self, status=0, message=None):
        raise ValueError((message or "").strip() or f"exit {status}")


def option_args(kind, values):
    """Form VALUES ({dest: value}) as command-line arguments; values equal to the default are left out."""
    sub = subparser(KINDS[kind])
    out = []
    for a in sub._actions:
        if not a.option_strings or a.dest not in values or a.dest in MANAGED[kind]:
            continue
        v, flag = values[a.dest], max(a.option_strings, key=len)
        if isinstance(a, argparse._StoreTrueAction):
            out += [flag] if v else []
        elif isinstance(a, argparse._StoreFalseAction):
            out += [] if v else [flag]
        elif a.nargs == "?":  # --llm [MODEL]
            if v not in (None, "", False):
                out.append(flag if v == a.const else f"{flag}={v}")
        elif v not in (None, "") and str(v) != str(a.default):
            out.append(f"{flag}={v}")
    return out


def command(kind, inputs, out, values, extra=()):
    """The `parisaocr` arguments of a job: its inputs, its output folder, the form's values and EXTRA flags,
    checked by the command line's parser (ValueError with the parser's message when it refuses them)."""
    cmd = KINDS[kind]
    inputs = [str(p) for p in inputs]
    if kind == "epub":
        if len(inputs) != 1:
            raise ValueError("a book is one PDF")
        args = [cmd, inputs[0], f"--out={out}"]
    elif kind == "pages":
        if len(inputs) != 1:
            raise ValueError("one PDF")
        args = [cmd, inputs[0], f"--out={pathlib.Path(out) / 'pages'}"]
    else:
        if not inputs:
            raise ValueError("no input")
        args = [cmd, *inputs, f"--out={out}"]
    args += option_args(kind, values) + list(extra)
    if any(a.startswith("-") for a in inputs):
        raise ValueError("an input path cannot start with '-'")
    subparser(cmd, _Refuse).parse_args(args[1:])
    return args


def shown(args):
    """The command as the person would type it."""
    return shlex.join(["parisaocr", *args])
