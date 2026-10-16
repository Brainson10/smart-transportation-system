"""
Two-line plates and OCR box ordering. Synthetic images, no OCR engine needed.
"""

import cv2
import numpy as np

import config
from gate import ocr
from gate.anpr import in_roi, roi_pixels, sharpness


def _two_line_plate():
    img = np.full((120, 200, 3), 255, np.uint8)
    cv2.putText(img, "MN05AB", (10, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3)
    cv2.putText(img, "1234", (40, 105), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3)
    return img


def test_square_crop_is_two_line_and_wide_crop_is_not():
    assert ocr.is_two_line(_two_line_plate())
    assert not ocr.is_two_line(np.full((40, 200, 3), 255, np.uint8))


def test_split_falls_between_the_two_text_lines():
    gray = cv2.cvtColor(_two_line_plate(), cv2.COLOR_BGR2GRAY)
    cut = ocr.split_rows(gray)
    # line 1 ink ends ~row 47, line 2 starts ~row 75
    assert 45 <= cut <= 78


def test_joined_two_line_plate_is_one_wide_line():
    gray = cv2.cvtColor(_two_line_plate(), cv2.COLOR_BGR2GRAY)
    joined = ocr.join_two_lines(gray)
    assert joined.shape[1] > joined.shape[0] * 4      # now a single text line


def test_two_line_variants_come_first():
    names = [name for name, _ in ocr.preprocess_variants(_two_line_plate())]
    assert names[:2] == ["twoline", "twoline-clahe"]
    wide = np.full((40, 200, 3), 255, np.uint8)
    assert "twoline" not in [name for name, _ in ocr.preprocess_variants(wide)]


def test_dark_crop_gets_a_gamma_variant():
    dark = np.full((40, 200, 3), 30, np.uint8)
    cv2.putText(dark, "MN05AB1234", (5, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (10, 10, 10), 2)
    assert "gamma" in [name for name, _ in ocr.preprocess_variants(dark)]


def _box(x, y, w=50, h=30):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def test_ocr_boxes_are_ordered_lines_then_left_to_right():
    raw = [(_box(0, 60), "1234", 0.9), (_box(60, 12), "AB", 0.9), (_box(0, 10), "MN05", 0.9)]
    assert [r[1] for r in ocr.order_ocr_boxes(raw)] == ["MN05", "AB", "1234"]


def test_ocr_box_ordering_tolerates_junk():
    assert ocr.order_ocr_boxes([]) == []
    assert ocr.order_ocr_boxes([("bad",)]) == []


def test_sharpness_drops_with_blur_and_ignores_contrast():
    crisp = _two_line_plate()
    blurred = cv2.GaussianBlur(crisp, (41, 41), 0)
    faded = (crisp.astype(np.float32) * 0.3 + 120).astype(np.uint8)
    assert sharpness(crisp) > 3 * config.MIN_SHARPNESS
    assert sharpness(blurred) < config.MIN_SHARPNESS
    assert abs(sharpness(faded) - sharpness(crisp)) < 0.2 * sharpness(crisp)


def test_roi_filters_boxes_by_centre():
    roi = roi_pixels((100, 200, 3), (0.5, 0.0, 1.0, 1.0))     # right half
    assert roi == (100, 0, 200, 100)
    assert in_roi((120, 10, 40, 20), roi)
    assert not in_roi((10, 10, 40, 20), roi)
    assert in_roi((10, 10, 40, 20), None)


def test_parse_roi():
    assert config.parse_roi("0.1,0.2,0.9,1") == (0.1, 0.2, 0.9, 1.0)
    assert config.parse_roi("0.5,0.5,0.4,0.9") is None       # inverted
    assert config.parse_roi("abc") is None and config.parse_roi(None) is None
