"""Page output: plain text, hOCR, JSONL."""
import html
import json

from . import __version__


def text(columns):
    return "".join(line.text + "\n" for col in columns for line in col)


def jsonl(page_id, columns):
    rows = []
    for c, col in enumerate(columns):
        for line in col:
            rows.append({"page": page_id, "column": c, "bbox": list(line.bbox), "text": line.text, "conf": line.conf,
                         "words": [{"text": w.text, "bbox": list(w.bbox), "conf": round(w.conf, 1)} for w in line.words]})
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def hocr(page_id, image_name, width, height, columns, engine):
    """hOCR in Tesseract's layout: ocr_page > ocr_carea (one per column) > ocr_par > ocr_line > ocrx_word.

    Titles follow Tesseract's quoting (double quotes on lines, single on words)
    so tools written for its output, including scripts/correct_lines.py, parse it.
    """
    def bbox(items):
        return f"bbox {min(i.bbox[0] for i in items)} {min(i.bbox[1] for i in items)} {max(i.bbox[2] for i in items)} {max(i.bbox[3] for i in items)}"

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"',
           '    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">',
           '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="fa" lang="fa">',
           " <head>", f"  <title>{html.escape(page_id)}</title>",
           '  <meta http-equiv="Content-Type" content="text/html;charset=utf-8"/>',
           f"  <meta name='ocr-system' content='parisaocr {__version__}; {html.escape(engine)}'/>",
           "  <meta name='ocr-capabilities' content='ocr_page ocr_carea ocr_par ocr_line ocrx_word'/>",
           " </head>", " <body>",
           f"  <div class='ocr_page' id='page_1' title='image \"{html.escape(str(image_name))}\"; bbox 0 0 {width} {height}; ppageno 0'>"]
    n_line = n_word = 0
    for c, col in enumerate(columns, 1):
        if not col:
            continue
        out.append(f"   <div class='ocr_carea' id='block_1_{c}' title=\"{bbox(col)}\">")
        out.append(f"    <p class='ocr_par' id='par_1_{c}' lang='fas' dir='rtl' title=\"{bbox(col)}\">")
        for line in col:
            n_line += 1
            x0, y0, x1, y1 = line.bbox
            words = []
            for w in line.words:
                n_word += 1
                words.append(f"<span class='ocrx_word' id='word_1_{n_word}' title='bbox {w.bbox[0]} {w.bbox[1]} {w.bbox[2]} {w.bbox[3]}; "
                             f"x_wconf {round(w.conf)}'>{html.escape(w.text)}</span>")
            out.append(f"     <span class='ocr_line' id='line_1_{n_line}' title=\"bbox {x0} {y0} {x1} {y1}; x_conf {line.conf}\">"
                       + " ".join(words) + "</span>")
        out.append("    </p>")
        out.append("   </div>")
    out += ["  </div>", " </body>", "</html>", ""]
    return "\n".join(out)
