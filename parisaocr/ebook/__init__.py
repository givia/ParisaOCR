"""Scanned Persian book (PDF) → EPUB 3: `parisaocr epub book.pdf --out DIR`.

ParisaOCR reads the pages; everything after that is geometry and the book's own
printed table of contents, without a language model:

1. OCR (`source`): ParisaOCR's line boxes, text and confidences per page.
2. Layout (`layout`): each page is text, figure or blank; running header and
   printed page number, footnotes below the separator rule (or, when the rule did
   not survive the scan, below a gap in smaller or tighter type), ruled tables,
   figures. Sideways maps are turned by reading them again at 90° and 270°.
3. Order (`order`): pages sorted by printed page number, chosen by agreement
   with neighbouring pages; reversed runs, duplicate scans and missing pages.
4. Refine (`refine`, `markers`): stacked fractions, footnote markers the OCR
   dropped (small raised digits found in the image), verse lines.
5. Structure (`structure`, `notes`): contents parsed (also without page
   numbers, or in two columns) and aligned with the pages; chapter openings
   decided for the whole book (`structure.book_openings`: the contents, and
   how this book's own openings look); section headings, paragraphs joined
   across pages, block quotes, verse, signatures, bibliography entries,
   captions; footnote areas split into numbered notes (`notes`) and linked
   to their markers.
6. EPUB (`epub`, `fonts`): one XHTML file per chapter, popup footnotes, the
   printed page numbers as a page list, right-to-left page progression,
   embedded Vazirmatn.
7. Report (`report`): what was decided and where it was unsure, for checking.
"""
