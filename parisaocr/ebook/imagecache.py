"""What the page images and the line-role models said, kept for the next conversion in the same process.

The review panel converts the whole book again on every rebuild, with the same page images and the same OCR. Most of
a conversion is reading the page images (their ink, for rules, pictures, raised note markers and verse gaps) and
running the line-role models over every line, and none of that changes with a person's corrections: they are
applied after it. The results are kept here, keyed by the image file (path, size, modification time) and by the
lines they were computed for, so a rebuild reads again only what an edit changed: a page whose lines a person split
or added. A conversion in a new process starts empty (nothing is written to disk).

`Ink` is a page's ink read on first use, so a page whose results are all kept is never read.
"""
import os
import threading
from collections import OrderedDict

import numpy as np
from PIL import Image

LIMIT = 400_000  # entries; a 500-page book uses some 30,000
_store = OrderedDict()
_lock = threading.Lock()
_MISSING = object()


def image_key(path):
    """The image file as it is now: a rewritten image is another key."""
    try:
        st = os.stat(path)
    except OSError:
        return (str(path), None, None)
    return (str(path), st.st_size, st.st_mtime_ns)


def get(kind, key, compute):
    """The value kept for (KIND, KEY), or COMPUTE()'s, kept. Values are shared: never change one in place."""
    k = (kind, key)
    with _lock:
        v = _store.get(k, _MISSING)
        if v is not _MISSING:
            _store.move_to_end(k)
            return v
    v = compute()
    with _lock:
        _store[k] = v
        while len(_store) > LIMIT:
            _store.popitem(last=False)
    return v


def clear():
    with _lock:
        _store.clear()


def ink(path):
    """The dark pixels of an image (gray below 128)."""
    with Image.open(path) as im:
        return np.asarray(im.convert("L")) < 128


class Ink:
    """A page image's ink, read the first time `array` is used. ARRAY: the ink when the caller has it already."""

    def __init__(self, path, array=None):
        self.path = path
        self.key = image_key(path)
        self._array = array

    @property
    def array(self):
        if self._array is None:
            self._array = ink(self.path)
        return self._array
