"""
Confidence fusion -- the replacement for `min(70 + len(plate) * 3, 95)`, which
was always exactly 95 and made the MANUAL CHECK branch unreachable.
"""

import config
from gate.plate_utils import fuse_confidence


def test_is_always_an_int_in_range():
    for args in [(0, 0, 0, 0), (1, 100, 1, 0), (0.5, 50, 0.5, 3), (2, 200, 9, 99)]:
        value = fuse_confidence(*args)
        assert isinstance(value, int)
        assert 0 <= value <= 100


def test_not_a_constant():
    """The whole point: the number must move with the evidence."""
    assert fuse_confidence(0.9, 95, 1.0, 0) != fuse_confidence(0.3, 40, 0.3, 3)


def test_monotonic_in_each_input():
    assert fuse_confidence(0.9, 80, 1.0, 0) > fuse_confidence(0.3, 80, 1.0, 0)
    assert fuse_confidence(0.8, 95, 1.0, 0) > fuse_confidence(0.8, 50, 1.0, 0)
    assert fuse_confidence(0.8, 80, 1.0, 0) > fuse_confidence(0.8, 80, 0.2, 0)


def test_substitutions_reduce_confidence():
    clean = fuse_confidence(0.8, 90, 1.0, 0)
    repaired = fuse_confidence(0.8, 90, 1.0, 3)
    assert repaired < clean


def test_reference_cases_land_on_the_expected_side_of_the_threshold():
    clean = fuse_confidence(0.85, 88, 1.0, 0)
    marginal = fuse_confidence(0.50, 55, 0.6, 2)
    assert clean >= config.MANUAL_CHECK_CONFIDENCE     # auto-accepted
    assert marginal < config.MANUAL_CHECK_CONFIDENCE   # routed to MANUAL CHECK
