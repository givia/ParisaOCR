"""Text handling: normalization and the reading/display order conversion (no models needed)."""
import pytest

from parisaocr.fa_text import canonical, relaxed

pytest.importorskip("kraken")
from parisaocr.bidi import base_direction, to_display, to_logical  # noqa: E402


@pytest.mark.parametrize("text", [
    "به عنوان نمونه نگاه کنید به: Handler and Lange 1978.",
    "1. Vercingétorix",
    "در ۷۵٪ از نمونه‌ها (حدود ۱۲٬۵۰۰ مورد)",
    "می‌خورد، «نه!» گفت.",
    "Smith (1998), pp. 12-34.",
])
def test_display_round_trip_keeps_text_and_zwnj(text):
    shown = to_display(text)
    assert shown.count("‌") == text.count("‌")
    assert to_logical(shown)[0] == text


def test_base_direction():
    assert base_direction("1. Vercingétorix") == "L"
    assert base_direction("Chase (1987) گفته که وی") == "R"


def test_canonical():
    assert canonical("كتاب يك") == "کتاب یک"            # Arabic kaf/yeh -> Persian
    assert canonical("او — آرش") == "او - آرش"      # dashes of any length -> "-"
    assert canonical("خانه‌ٔ من") == "خانۀ من"  # heh + hamza -> one code point
    assert relaxed("نامه‌ای ۲۰") == "نامه ای 20"
