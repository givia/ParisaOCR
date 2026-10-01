"""Reading order <-> display order for one text line, with each line's own base direction.

Recognition models see a line image from left to right, so they are trained on
the display order of the text and their output is turned back into reading
order. Kraken can do both itself, but (mittagessen/kraken#809) its bidi code
deletes ZWNJ, the Persian half-space, and it gives every line the same base
direction, so a Latin-only line such as "1. Vercingétorix" is trained as
"Vercingétorix .1". Here the Unicode bidi algorithm of kraken.lib.bidi runs
with ZWNJ protected (swapped for U+00A6, a neutral that stays in place between
two letters) and with the base direction taken from the line itself.
"""
import unicodedata

ZWNJ, PLACEHOLDER = "‌", "¦"


def base_direction(text):
    """"L" for a line with more Latin than Arabic-script letters, else "R"."""
    latin = sum(1 for c in text if c.isalpha() and unicodedata.bidirectional(c) == "L")
    arabic = sum(1 for c in text if unicodedata.bidirectional(c) in ("R", "AL"))
    return "L" if latin > arabic else "R"


def to_display(text, base=None):
    """Display (left-to-right visual) order of a line in reading order; ZWNJ kept."""
    from kraken.lib.bidi import get_display
    base = base or base_direction(text)
    return get_display(text.replace(ZWNJ, PLACEHOLDER), base_dir=base).replace(PLACEHOLDER, ZWNJ)


def to_logical(display, base=None):
    """Reading order of a line in display order, and for each character of the result the index
    of the display character it came from (to carry character positions along)."""
    from kraken.lib.bidi import get_display_map
    base = base or base_direction(display)
    text, order = get_display_map(display.replace(ZWNJ, PLACEHOLDER), base_dir=base)
    return text.replace(PLACEHOLDER, ZWNJ), order
