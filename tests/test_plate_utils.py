"""
Plate text repair and validation. Pure functions, so no camera, model or Flask.
"""

import pytest

from gate.plate_utils import (
    clean_ocr_text, format_score, is_valid_plate,
    normalize_plate, normalize_plate_detailed,
)


@pytest.mark.parametrize("raw, expected", [
    # already clean
    ("MN05AB1234", "MN05AB1234"),
    # the real EasyOCR misread observed on this project: O for 0, S for 5
    ("MNOSAB1234", "MN05AB1234"),
    # the real Tesseract misread: I for 1 as well
    ("MNOSABI234", "MN05AB1234"),
    # single-letter series -- unreachable under the old anpr.py regex
    ("KA01A1234", "KA01A1234"),
    ("MN06A1234", "MN06A1234"),
    # three-letter series
    ("DL01CAB1234", "DL01CAB1234"),
    # letters bleeding into the numeric tail
    ("HR26DKB337", "HR26DK8337"),
    # surrounding noise and separators
    ("IND MN 05 AB 1234", "MN05AB1234"),
    ("  mn05ab1234  ", "MN05AB1234"),
    ("*MN05AB1234*", "MN05AB1234"),
])
def test_normalize_recovers_plate(raw, expected):
    assert normalize_plate(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "ABC", "1234", "XX", "!!!!!!!!", "AB"])
def test_normalize_rejects_junk(raw):
    assert normalize_plate(raw) is None


def test_bh_series_is_not_mangled():
    """The literal BH must be matched before digit/letter coercion runs."""
    candidate = normalize_plate_detailed("22BH1234AB")
    assert candidate is not None
    assert candidate.text == "22BH1234AB"
    assert candidate.kind == "bh"
    assert candidate.substitutions == 0


def test_substitutions_are_counted_exactly():
    assert normalize_plate_detailed("MN05AB1234").substitutions == 0
    assert normalize_plate_detailed("MNOSAB1234").substitutions == 2


def test_format_score_prefers_real_state_code():
    real = normalize_plate_detailed("MN05AB1234")
    assert real.format_score == pytest.approx(1.0)
    # XX is not an RTO code, so this must score lower than a genuine plate
    assert format_score("XX", "05", "AB", "standard") < real.format_score


def test_clean_ocr_text_strips_non_alphanumerics():
    assert clean_ocr_text("mn-05 ab/1234\n") == "MN05AB1234"
    assert clean_ocr_text(None) == ""


def test_is_valid_plate():
    assert is_valid_plate("MN05AB1234")
    assert is_valid_plate("22BH1234AB")
    assert not is_valid_plate("MNOSAB1234")   # needs repair first
    assert not is_valid_plate("")
