"""EPUB 3 writer: one XHTML file per chapter, popup footnotes, right-to-left, an embedded font.

Footnotes are `<aside epub:type="footnote">` at the end of their chapter, referenced
by `<a epub:type="noteref">`, and renumbered through each chapter (the print
numbered them per page). Printed page numbers become
`epub:type="pagebreak"` anchors listed in the navigation's page-list, with
the print edition's ISBN as `dc:source` when the front matter prints one.
Figures are cropped from the page images: bilevel scans as PNG, greyscale and
colour as JPEG. Tables are images by default: the OCR of their numbers is not
reliable enough to typeset them.
"""
import datetime
import html
import pathlib
import re
import shutil
import tempfile
import uuid
import zipfile

from PIL import Image, ImageDraw

from . import fonts
from .structure import NoteRef, PageMark, Span
from .textutil import fa_num

def font_css(families):
    faces = [f'@font-face {{ font-family: "{family}"; font-weight: {weight}; font-style: normal; src: url("fonts/{name}"); }}'
             for family, weights in families.items() for weight, (name, _) in weights.items()]
    body = ", ".join(f'"{family}"' for family in families)
    return "\n".join(faces) + f'\nbody {{ font-family: {body}, sans-serif; }}\n'


CSS = """body { text-align: justify; line-height: 1.85; margin: 0 3%; }
p { margin: 0; text-indent: 1.4em; }
h1 { font-size: 1.45em; font-weight: bold; text-align: center; line-height: 1.6; margin: 2.5em 0 1.2em; }
h1 .label { display: block; font-size: 0.72em; font-weight: normal; margin-bottom: 0.4em; }
h2 { font-size: 1.12em; font-weight: bold; text-align: right; margin: 1.5em 0 0.6em; }
.part { font-size: 1.6em; margin-top: 30%; }
p.byline { text-align: center; text-indent: 0; margin: 0.3em 0; font-size: 0.95em; }
p.byline + p, h1 + p, h2 + p { margin-top: 1em; }
blockquote { margin: 0.8em 1.8em; font-size: 0.93em; }
blockquote p { text-indent: 0; }
blockquote p + p { text-indent: 1.4em; }
.verse { margin: 1em 0; }
p.beyt { text-indent: 0; text-align: center; }
.poem { margin: 1em 0; }
.poem .stanza { margin: 0 0 1em; }
.poem p { text-indent: 0; text-align: right; }
.poem p[dir="ltr"] { text-align: left; }
p.beyt span.m { display: inline-block; width: 47%; text-align: center; }
p.caption { text-align: center; text-indent: 0; font-size: 0.9em; margin: 0.4em 0; }
p.sep { text-align: center; text-indent: 0; margin: 1em 0; }
p.sign { text-align: left; text-indent: 0; margin: 0.4em 0; }
p.gap { text-align: center; text-indent: 0; margin: 1.2em 0; color: #666; }
figure { margin: 1.2em 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; }
figcaption { font-size: 0.88em; text-align: center; margin-top: 0.4em; }
table { border-collapse: collapse; margin: 1em auto; font-size: 0.9em; }
td { border: 1px solid #888; padding: 0.2em 0.5em; text-align: center; }
p.bib { text-indent: -2em; padding-right: 2em; margin: 0.35em 0; text-align: right; }
p.bib[dir="ltr"] { text-indent: -2em; padding-left: 2em; padding-right: 0; text-align: left; }
p.bib .author { font-weight: bold; }
p.bib.more { margin-top: 0; }
a.noteref { text-decoration: none; }
sup { font-size: 0.7em; line-height: 0; }
hr.notes { margin: 2em 30% 0.8em 0; border: none; border-top: 1px solid #999; }
aside { font-size: 0.86em; margin: 0.3em 0; }
aside p { text-indent: 0; }
aside a.back { text-decoration: none; }
.titlepage { text-align: center; margin-top: 12%; }
.titlepage p { text-indent: 0; margin: 0.5em 0; }
.cover { text-align: center; margin: 0; }
.cover img { max-width: 100%; max-height: 100%; }
"""

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}" dir="rtl">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<link rel="stylesheet" type="text/css" href="../style.css"/>
</head>
<body{body_attrs}>
{body}
</body>
</html>
"""


def esc(s):
    return html.escape(s, quote=False)


def attr(s):
    return html.escape(s, quote=True)


class Writer:
    def __init__(self, book, meta, tables="image"):
        self.book = book
        self.meta = meta
        self.lang = meta.get("language", "fa")
        self.tables = tables
        self.files = {}  # path inside OEBPS -> bytes
        self.images = {}  # path inside OEBPS -> media type
        self.pages = []  # (label, href)
        self.nav = []  # (title, href, [(title, href)], part)

    # -- images --
    def image(self, layout, box, rotate, name):
        with Image.open(layout.page.image) as im:
            im.load()
            for x0, y0, x1, y1 in layout.page.stamps:  # a site's name burned into the scan
                white = 255 if im.mode in ("1", "L") else (255, 255, 255)
                ImageDraw.Draw(im).rectangle((x0 - 2, y0 - 2, x1 + 2, y1 + 2), fill=white)
            if box:
                x0, y0, x1, y1 = box
                im = im.crop((max(0, x0), max(0, y0), min(im.width, x1), min(im.height, y1)))
            if rotate:
                im = im.rotate(rotate, expand=True, fillcolor=255 if im.mode in ("1", "L") else (255, 255, 255))
            if im.mode == "1":
                path, kind, fmt, opts = f"images/{name}.png", "image/png", "PNG", {"optimize": True}
            else:
                im = im.convert("L" if im.mode in ("L", "LA", "I") else "RGB")
                path, kind, fmt, opts = f"images/{name}.jpg", "image/jpeg", "JPEG", {"quality": 82, "optimize": True}
            with tempfile.SpooledTemporaryFile() as f:
                im.save(f, fmt, **opts)
                f.seek(0)
                self.files[path] = f.read()
        self.images[path] = kind
        return path

    # -- inline content --
    def inline(self, items, refs):
        out = []
        for x in items:
            if x == "\n":
                out.append("<br/>")
            elif isinstance(x, str):
                out.append(esc(x))
            elif isinstance(x, Span):
                out.append(f'<span dir="ltr" lang="en">{esc(x.text)}</span>')
            elif isinstance(x, NoteRef):
                refs.append(x.note)
                k = fa_num(len(refs))
                out.append(f'<sup><a class="noteref" epub:type="noteref" role="doc-noteref" id="r-{x.note.id}" '
                           f'href="#{x.note.id}">{k}</a></sup>')
            elif isinstance(x, PageMark):
                out.append(self.pagebreak(x.number))
        return "".join(out).strip()

    def pagebreak(self, n):
        pid = f"page-{n}"
        self.pages.append((fa_num(n), f"{self.href}#{pid}"))
        return f'<span epub:type="pagebreak" role="doc-pagebreak" id="{pid}" aria-label="{fa_num(n)}"></span>'

    # -- blocks --
    def blocks(self, chapter, refs, sections):
        out, i = [], 0
        bl = chapter.blocks
        while i < len(bl):
            b = bl[i]
            k = b.kind
            ltr = ' dir="ltr" lang="en"' if b.ltr else ""
            if k == "p":
                out.append(f"<p{ltr}>{self.inline(b.items, refs)}</p>")
            elif k == "quote":
                group = []
                while i < len(bl) and bl[i].kind == "quote":
                    q = bl[i]
                    qd = ' dir="ltr" lang="en"' if q.ltr else ""
                    group.append(f"<p{qd}>{self.inline(q.items, refs)}</p>")
                    i += 1
                out.append("<blockquote>\n" + "\n".join(group) + "\n</blockquote>")
                continue
            elif k == "verse":
                rows = "\n".join(f'<p class="beyt"><span class="m">{esc(a)}</span> <span class="m">{esc(c)}</span></p>'
                                 for a, c in b.rows)
                out.append(f'<div class="verse">\n{rows}\n</div>')
            elif k == "poem":
                stanzas, cur = [], []
                for row in b.rows + [""]:
                    if row == "":
                        if cur:
                            stanzas.append('<div class="stanza">\n' + "\n".join(cur) + "\n</div>")
                        cur = []
                    elif all(isinstance(x, PageMark) for x in row):
                        cur.append(self.inline(row, refs))  # a page break between two lines
                    else:
                        d = ' dir="ltr" lang="en"' if any(isinstance(x, Span) for x in row) else ""
                        cur.append(f"<p{d}>{self.inline(row, refs)}</p>")
                out.append('<div class="poem">\n' + "\n".join(stanzas) + "\n</div>")
            elif k == "h2":
                sid = f"s{len(sections) + 1}"
                text = self.inline(b.items, refs)
                sections.append((re.sub(r"<[^>]+>", "", text), f"{self.href}#{sid}"))
                out.append(f'<h2 id="{sid}">{text}</h2>')
            elif k in ("byline", "caption", "gap", "sign"):
                out.append(f'<p class="{k}">{self.inline(b.items, refs)}</p>')
            elif k == "sep":
                out.append('<p class="sep">* * *</p>')
            elif k == "mark":
                out.append(self.inline(b.items, refs))
            elif k == "bib":
                more = "" if b.lead else " more"
                lead = f'<span class="author">{esc(b.lead)}</span> ' if b.lead else ""
                out.append(f'<p class="bib{more}"{ltr}>{lead}{self.inline(b.items, refs)}</p>')
            elif k == "table" and self.tables == "html":
                rows = "\n".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in b.rows)
                cap = f"<caption>{self.inline(b.items, refs)}</caption>\n" if b.items else ""
                out.append(f"<table>\n{cap}{rows}\n</table>")
            elif k in ("table", "figure"):
                L, box, rot = b.image
                self.figno += 1
                src = self.image(L, box, rot, f"fig{self.figno:03d}")
                cap = self.inline(b.items, refs) if b.items else ""
                alt = re.sub(r"<[^>]+>", " ", cap).split("  ")[0].strip() or ("جدول" if k == "table" else "تصویر")
                figcap = f"\n<figcaption>{cap}</figcaption>" if cap else ""
                out.append(f'<figure>\n<img src="../{src}" alt="{attr(alt)}"/>{figcap}\n</figure>')
            elif k == "lines":
                out.append('<div class="titlepage">\n' + "\n".join(f"<p>{esc(r)}</p>" for r in b.rows) + "\n</div>")
            i += 1
        return out

    def notes(self, refs, chapter):
        out = []
        if refs:
            out.append('<hr class="notes"/>')
        for k, note in enumerate(refs, 1):
            d = ' dir="ltr" lang="en"' if note.ltr else ""
            out.append(f'<aside epub:type="footnote" role="doc-footnote" id="{note.id}"{d}>'
                       f'<p><a class="back" href="#r-{note.id}">{fa_num(k)}.</a> {esc(note.text)}</p></aside>')
        orphans = [n for n in chapter.notes if n not in refs]
        for note in orphans:
            d = ' dir="ltr" lang="en"' if note.ltr else ""
            out.append(f'<p class="caption"{d}>{esc(note.text)}</p>')
        return out

    def chapter_file(self, chapter, index):
        name = "front" if chapter.kind == "front" else f"ch{index:02d}"
        self.href = f"text/{name}.xhtml"
        refs, sections = [], []
        body = []
        if chapter.kind == "front":
            body += self.blocks(chapter, refs, sections)
            attrs = ' epub:type="frontmatter"'
        elif chapter.kind == "part":
            body.append(f'<section epub:type="part" role="doc-part">\n<h1 class="part">{esc(chapter.title)}</h1>')
            body += self.blocks(chapter, refs, sections)
            body.append("</section>")
            attrs = ' epub:type="bodymatter"'
        else:
            label = f'<span class="label">{esc(chapter.label)}</span> ' if chapter.label else ""
            heading = self.inline(chapter.heading, refs) if chapter.heading else esc(chapter.title)
            body.append(f'<section epub:type="chapter" role="doc-chapter">\n<h1>{label}{heading}</h1>')
            body += self.blocks(chapter, refs, sections)
            body += self.notes(refs, chapter)
            body.append("</section>")
            attrs = ' epub:type="bodymatter"'
        title = chapter.title or self.meta.get("title", "")
        self.files[self.href] = XHTML.format(lang=self.lang, title=esc(title), body_attrs=attrs,
                                             body="\n".join(body)).encode()
        if chapter.kind != "front":
            self.nav.append((chapter.title, self.href, sections, chapter.title if chapter.kind == "part" else chapter.part))
        return self.href

    # -- package --
    def write(self, out_path, font_dir=fonts.FONT_DIR):
        book, meta = self.book, self.meta
        self.figno = 0
        spine = []
        if book.cover:
            L, n = book.cover
            self.cover = self.image(L, None, 0, "cover")
            self.files["text/cover.xhtml"] = XHTML.format(
                lang=self.lang, title=esc(meta.get("title", "")), body_attrs=' epub:type="cover"',
                body=f'<div class="cover"><img src="../{self.cover}" alt="{attr(meta.get("title", "جلد"))}"/></div>').encode()
            spine.append("text/cover.xhtml")
        else:
            self.cover = None
        k = 0
        for ch in book.chapters:
            if ch.kind != "front":
                k += 1
            spine.append(self.chapter_file(ch, k))
        self.files["nav.xhtml"] = self.nav_file(spine).encode()
        self.files["toc.ncx"] = self.ncx(spine).encode()
        families = fonts.embedded(font_dir)
        self.files["style.css"] = (font_css(families) + CSS).encode()
        self.fonts = []
        for weights in families.values():
            for name, data in weights.values():
                self.files[f"fonts/{name}"] = data
                self.fonts.append(name)
        self.files["content.opf"] = self.opf(spine).encode()

        out_path = pathlib.Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            z.writestr("META-INF/container.xml", CONTAINER, compress_type=zipfile.ZIP_DEFLATED)
            for path, data in self.files.items():
                z.writestr(f"OEBPS/{path}", data, compress_type=zipfile.ZIP_DEFLATED)
        shutil.move(tmp, out_path)
        return out_path

    def nav_file(self, spine):
        items, part_open = [], None
        for i, (title, href, sections, part) in enumerate(self.nav):
            if part != part_open:
                if part_open:
                    items.append("</ol></li>")
                part_open = part
                if part and part == title:  # the part's own title page
                    items.append(f'<li><a href="{href}">{esc(part)}</a><ol>')
                    continue
                if part:
                    items.append(f"<li><span>{esc(part)}</span><ol>")
            sub = ""
            if sections:
                sub = "<ol>" + "".join(f'<li><a href="{h}">{esc(t)}</a></li>' for t, h in sections) + "</ol>"
            items.append(f'<li><a href="{href}">{esc(title)}</a>{sub}</li>')
        if part_open:
            items.append("</ol></li>")
        if not items:  # no chapters found: the book as one entry
            items.append(f'<li><a href="{spine[-1]}">{esc(self.meta.get("title", "متن"))}</a></li>')
        pages = "\n".join(f'<li><a href="{h}">{esc(label)}</a></li>' for label, h in self.pages)
        first = next((h for _, h, _, _ in self.nav), spine[0])
        landmarks = []
        if self.cover:
            landmarks.append('<li><a epub:type="cover" href="text/cover.xhtml">جلد</a></li>')
        landmarks.append(f'<li><a epub:type="bodymatter" href="{first}">آغاز متن</a></li>')
        body = (f'<nav epub:type="toc" role="doc-toc" id="toc">\n<h1>فهرست</h1>\n<ol>\n' + "\n".join(items) + "\n</ol>\n</nav>\n"
                f'<nav epub:type="landmarks" hidden="">\n<ol>\n' + "\n".join(landmarks) + "\n</ol>\n</nav>\n"
                + (f'<nav epub:type="page-list" hidden="">\n<ol>\n{pages}\n</ol>\n</nav>' if pages else ""))
        return XHTML.format(lang=self.lang, title="فهرست", body_attrs="", body=body).replace("../style.css", "style.css")

    def ncx(self, spine):
        points, k = [], 0
        for title, href, sections, part in self.nav or [(self.meta.get("title", "متن"), spine[-1], [], "")]:
            k += 1
            sub = []
            for t, h in sections:
                k += 1
                sub.append(f'<navPoint id="np{k}" playOrder="{k}"><navLabel><text>{esc(t)}</text></navLabel>'
                           f'<content src="{h}"/></navPoint>')
            points.append(f'<navPoint id="np{k - len(sub)}" playOrder="{k - len(sub)}"><navLabel><text>{esc(title)}</text>'
                          f'</navLabel><content src="{href}"/>{"".join(sub)}</navPoint>')
        return f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="{self.lang}">
<head><meta name="dtb:uid" content="{self.uid}"/><meta name="dtb:depth" content="2"/>
<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>
<docTitle><text>{esc(self.meta.get("title", ""))}</text></docTitle>
<navMap>
{chr(10).join(points)}
</navMap>
</ncx>
"""

    @property
    def uid(self):
        return "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "ebook:" + self.meta.get("title", "") + self.meta.get("author", "")))

    def opf(self, spine):
        m = self.meta
        md = [f'<dc:identifier id="uid">{self.uid}</dc:identifier>',
              f'<dc:title id="title">{esc(m.get("title", "بی‌عنوان"))}</dc:title>',
              '<meta refines="#title" property="title-type">main</meta>']
        if m.get("subtitle"):
            md += [f'<dc:title id="subtitle">{esc(m["subtitle"])}</dc:title>',
                   '<meta refines="#subtitle" property="title-type">subtitle</meta>']
        if m.get("author"):
            md += [f'<dc:creator id="author">{esc(m["author"])}</dc:creator>',
                   '<meta refines="#author" property="role" scheme="marc:relators">aut</meta>']
        if m.get("publisher"):
            md.append(f'<dc:publisher>{esc(m["publisher"])}</dc:publisher>')
        if m.get("isbn"):
            md.append(f'<dc:source>urn:isbn:{esc(m["isbn"])}</dc:source>')
        md.append(f'<dc:language>{self.lang}</dc:language>')
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        md.append(f'<meta property="dcterms:modified">{now}</meta>')
        if self.cover:
            md.append('<meta name="cover" content="cover-image"/>')
        manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                    '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                    '<item id="css" href="style.css" media-type="text/css"/>']
        manifest += [f'<item id="font{i}" href="fonts/{f}" media-type="font/ttf"/>' for i, f in enumerate(self.fonts)]
        for i, (path, kind) in enumerate(self.images.items()):
            iid = "cover-image" if path == self.cover else f"img{i}"
            props = ' properties="cover-image"' if path == self.cover else ""
            manifest.append(f'<item id="{iid}" href="{path}" media-type="{kind}"{props}/>')
        ids = []
        for i, path in enumerate(spine):
            iid = f"x{i}"
            ids.append(iid)
            manifest.append(f'<item id="{iid}" href="{path}" media-type="application/xhtml+xml"/>')
        itemrefs = "\n".join(f'<itemref idref="{i}"/>' for i in ids)
        return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang="{self.lang}" dir="rtl">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
{chr(10).join(md)}
</metadata>
<manifest>
{chr(10).join(manifest)}
</manifest>
<spine toc="ncx" page-progression-direction="rtl">
{itemrefs}
</spine>
</package>
"""


CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""


def find_isbn(book):
    """The print edition's ISBN, if the front matter prints one."""
    for ch in book.chapters:
        if ch.kind != "front":
            break
        for b in ch.blocks:
            for row in b.rows if b.kind == "lines" else []:
                m = re.search(r"ISBN[:\s]*([0-9Xx][0-9Xx\-]{8,16})", row)
                if m:
                    return m.group(1).replace("-", "")
    return None


def write(book, out_path, meta, tables="image", font_dir=fonts.FONT_DIR):
    meta = dict(meta)
    meta.setdefault("isbn", find_isbn(book))
    return Writer(book, meta, tables).write(out_path, font_dir)
