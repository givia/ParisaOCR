"""The embedded font: Vazirmatn Regular and Bold (SIL OFL 1.1, bundled)."""
import pathlib

FONT_DIR = pathlib.Path(__file__).resolve().parent.parent / "fonts"
PLAIN = {"normal": "Vazirmatn-Regular.ttf", "bold": "Vazirmatn-Bold.ttf"}


def embedded(font_dir=FONT_DIR):
    """{family: {weight: (file name in the EPUB, bytes)}}."""
    font_dir = pathlib.Path(font_dir)
    return {"Vazirmatn": {w: (f, (font_dir / f).read_bytes()) for w, f in PLAIN.items()}}
