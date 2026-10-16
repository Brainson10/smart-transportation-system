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


# ---------------- near-match suggestions ----------------
from gate.plate_utils import find_near_match, mask_source  # noqa: E402

REGISTERED = ["MN01AB1234", "MN01CD5678", "UP19EQ1001"]


@pytest.mark.parametrize("read,expected", [
    ("MN01AB1284", "MN01AB1234"),     # 3 -> 8, a classic OCR confusion
    ("MN01A81234", "MN01AB1234"),     # B -> 8
    ("MN01AB234", "MN01AB1234"),      # one glyph dropped
    ("MN01CD5679", "MN01CD5678"),     # non-confusable substitution still suggested
    ("KA09ZZ9999", None),             # nothing close
    ("MN01AB1234", None),             # exact match is not a "near" match
    ("", None),
])
def test_find_near_match(read, expected):
    assert find_near_match(read, REGISTERED) == expected


def test_near_match_refuses_to_guess_between_equal_candidates():
    assert find_near_match("MN01AB1230", ["MN01AB1231", "MN01AB1239"]) is None


def test_confusable_substitution_beats_an_arbitrary_one():
    # 8 vs B is a known confusion; 8 vs X is not.
    assert find_near_match("MN01A81234", ["MN01AB1234", "MN01AX1234"]) == "MN01AB1234"


@pytest.mark.parametrize("source,expected", [
    ("rtsp://admin:s3cret@10.0.0.5:554/stream1", "rtsp://admin:***@10.0.0.5:554/stream1"),
    ("rtsp://10.0.0.5/stream", "rtsp://10.0.0.5/stream"),
    ("samples/gate.mp4", "samples/gate.mp4"),
    (0, "0"),
    (None, ""),
])
def test_mask_source_hides_camera_passwords(source, expected):
    assert mask_source(source) == expected
