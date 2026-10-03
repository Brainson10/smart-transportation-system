"""
Tesseract confidence aggregation. Operates on hand-built image_to_data dicts, so
no tesseract binary is needed.
"""

from gate.ocr import _aggregate


def test_skips_empty_text_and_negative_confidence():
    data = {"text": ["", "MN05", "   ", "AB1234"], "conf": ["-1", "90", "-1", "80"]}
    text, conf = _aggregate(data)
    assert text == "MN05AB1234"
    # length-weighted: (90*4 + 80*6) / 10
    assert conf == 84.0


def test_returns_zero_for_an_empty_result():
    assert _aggregate({"text": ["", "  "], "conf": ["-1", "-1"]}) == ("", 0.0)
    assert _aggregate({}) == ("", 0.0)


def test_tolerates_unparseable_confidence_values():
    data = {"text": ["MN05AB1234", "junk"], "conf": ["95", "not-a-number"]}
    text, conf = _aggregate(data)
    assert text == "MN05AB1234"
    assert conf == 95.0


def test_longer_words_dominate_the_mean():
    """A 1-char high-confidence fragment must not outweigh the real plate text."""
    data = {"text": ["X", "MN05AB1234"], "conf": ["99", "60"]}
    _, conf = _aggregate(data)
    assert conf < 70.0
