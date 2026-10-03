"""
Single source of truth for number-plate text: validation, OCR repair, scoring
and temporal voting.

This replaces two contradictory regexes that used to live in anpr.py (which
demanded a 2-letter series, making plates like MN06A1234 unreachable) and
camera.py (which allowed 1-2 letters but only "fixed" characters in a span the
regex had already forced to be digits, so every fix was a no-op).

Pure stdlib on purpose: no cv2, no torch, no Flask. That keeps the unit tests
fast and lets the offline tuning harness import it directly.
"""

import re
import time
from dataclasses import dataclass

import config

# Indian RTO state / union-territory codes. "BH" is the Bharat series.
STATE_CODES = frozenset(
    """AN AP AR AS BR CG CH DD DL DN GA GJ HP HR JH JK KA KL LA LD MH ML MN MP
       MZ NL OD OR PB PY RJ SK TN TR TS UK UP UT WB BH""".split()
)

# Standard: 2-letter state, 2-digit RTO, 0-3 letter series, 4-digit number.
# The 0-3 range is what finally admits single-letter series plates.
PLATE_RE = re.compile(r"^([A-Z]{2})(\d{2})([A-Z]{0,3})(\d{4})$")
# Bharat series: 22BH1234AB
BH_RE = re.compile(r"^(\d{2})(BH)(\d{4})([A-Z]{1,2})$")
BH_SEARCH_RE = re.compile(r"\d{2}BH\d{4}[A-Z]{1,2}")

# Glyph confusions, applied by POSITION: a character sitting in a letter slot is
# coerced toward letters, one in a digit slot toward digits.
LETTER_FIXES = {"0": "O", "1": "I", "2": "Z", "4": "A", "5": "S", "6": "G", "7": "T", "8": "B"}
DIGIT_FIXES = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "J": "1",
               "Z": "2", "A": "4", "S": "5", "G": "6", "T": "7", "B": "8"}

MIN_RAW_LENGTH = 8
MAX_PLATE_LENGTH = 11


@dataclass(frozen=True)
class PlateCandidate:
    text: str
    kind: str          # "standard" | "bh"
    state: str
    rto: str
    series: str
    number: str
    substitutions: int   # how many glyphs the repair had to change
    format_score: float  # 0..1, how plate-like the result is

    @property
    def is_bh(self):
        return self.kind == "bh"


def clean_ocr_text(raw):
    """Upper-case and strip everything that cannot appear in a plate."""
    if not raw:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(raw).upper())


def _coerce(segment, want):
    """
    Push a segment toward letters or digits, returning (fixed, substitutions).
    `want` is "alpha" or "digit".
    """
    table = LETTER_FIXES if want == "alpha" else DIGIT_FIXES
    valid = str.isalpha if want == "alpha" else str.isdigit
    out = []
    subs = 0
    for char in segment:
        if valid(char):
            out.append(char)
        elif char in table:
            out.append(table[char])
            subs += 1
        else:
            return None, subs
    return "".join(out), subs


def format_score(state, rto, series, kind):
    """How plausible this really is as a plate, independent of OCR confidence."""
    if kind == "bh":
        return 1.0
    score = 0.10
    if state in STATE_CODES:
        score += 0.50
    if len(series) in (1, 2):
        score += 0.25
    if rto != "00":
        score += 0.15
    return min(score, 1.0)


def _rank(candidate):
    """Sort key for competing readings of the same OCR string."""
    return (-candidate.substitutions, candidate.format_score, len(candidate.text))


def _bh_candidate(cleaned):
    match = BH_SEARCH_RE.search(cleaned)
    if not match:
        return None
    text = match.group(0)
    parsed = BH_RE.match(text)
    return PlateCandidate(
        text=text, kind="bh", state=parsed.group(1), rto=parsed.group(2),
        series=parsed.group(3), number=parsed.group(4),
        substitutions=0, format_score=1.0,
    )


def normalize_plate_detailed(raw):
    """
    Repair OCR output into a valid plate, or return None.

    Position-aware in BOTH directions, which is the actual fix for real misreads:
    EasyOCR reads a clean MN05AB1234 as "MNOSAB1234" (O for 0, S for 5). The old
    code rejected that outright because its regex ran before any repair.
    """
    cleaned = clean_ocr_text(raw)
    if len(cleaned) < MIN_RAW_LENGTH:
        return None

    # Bharat series first: the literal "BH" is unambiguous, so matching it before
    # the standard form stops 22BH1234AB being mangled by digit/letter coercion.
    bh = _bh_candidate(cleaned)
    if bh:
        return bh

    best = None
    # Longest first so a 3-letter series is preferred over a truncated read.
    for length in (11, 10, 9, 8):
        if length > len(cleaned):
            continue
        series_len = length - 8
        for start in range(0, len(cleaned) - length + 1):
            window = cleaned[start:start + length]
            state, s1 = _coerce(window[0:2], "alpha")
            if state is None:
                continue
            rto, s2 = _coerce(window[2:4], "digit")
            if rto is None:
                continue
            series, s3 = _coerce(window[4:4 + series_len], "alpha")
            if series is None:
                continue
            number, s4 = _coerce(window[length - 4:length], "digit")
            if number is None:
                continue

            text = state + rto + series + number
            if not PLATE_RE.match(text):
                continue

            subs = s1 + s2 + s3 + s4
            score = format_score(state, rto, series, "standard")
            candidate = PlateCandidate(
                text=text, kind="standard", state=state, rto=rto,
                series=series, number=number,
                substitutions=subs, format_score=score,
            )
            # Fewest repairs first, THEN plate-likeness, then longest match.
            # Substitutions must dominate: otherwise a 3-letter-series plate read
            # perfectly (DL01CAB1234) loses to a 2-letter reading of the same
            # text that needed a glyph changed, purely because of the series
            # bonus in format_score.
            if best is None or _rank(candidate) > _rank(best):
                best = candidate
    return best


def normalize_plate(raw):
    """Canonical plate string, or None. Thin wrapper over the detailed form."""
    candidate = normalize_plate_detailed(raw)
    return candidate.text if candidate else None


def is_valid_plate(text):
    if not text:
        return False
    text = clean_ocr_text(text)
    return bool(PLATE_RE.match(text) or BH_RE.match(text))


def fuse_confidence(det_conf, ocr_conf, fmt_score, substitutions=0):
    """
    The real confidence, 0-100 int (the dashboard template appends "%").

    Blends YOLO's box confidence, the OCR engine's own confidence and how
    plate-like the text is, then penalises each glyph the repair had to change.
    """
    det_conf = max(0.0, min(float(det_conf), 1.0))
    ocr_conf = max(0.0, min(float(ocr_conf), 100.0)) / 100.0
    fmt_score = max(0.0, min(float(fmt_score), 1.0))

    raw = (config.CONF_W_YOLO * det_conf
           + config.CONF_W_OCR * ocr_conf
           + config.CONF_W_FORMAT * fmt_score)
    raw *= max(0.0, 1.0 - config.CONF_SUBSTITUTION_PENALTY * max(0, int(substitutions)))
    return int(round(100 * max(0.0, min(raw, 1.0))))


def _hamming1(a, b):
    """True when two equal-length strings differ in at most one position."""
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        if x != y:
            diff += 1
            if diff > 1:
                return False
    return diff <= 1


class PlateVoter:
    """
    Confidence-weighted votes inside a rolling time window.

    Replaces `PLATE_VOTES = defaultdict(int)` with VOTE_THRESHOLD = 2, which had
    no expiry at all: a plate seen once now and once ten minutes later would lock
    the gate, and counts from one vehicle leaked into the next.
    """

    def __init__(self, window=None, threshold=None, min_sightings=None,
                 merge_distance=None, time_fn=time.monotonic):
        self.window = config.VOTE_WINDOW if window is None else window
        self.threshold = config.VOTE_THRESHOLD if threshold is None else threshold
        self.min_sightings = (config.VOTE_MIN_SIGHTINGS
                              if min_sightings is None else min_sightings)
        self.merge_distance = (config.VOTE_MERGE_DISTANCE
                               if merge_distance is None else merge_distance)
        self._time = time_fn
        self._votes = {}   # plate -> list[(timestamp, weight)]

    def _expire(self, now):
        cutoff = now - self.window
        for plate in list(self._votes):
            kept = [v for v in self._votes[plate] if v[0] >= cutoff]
            if kept:
                self._votes[plate] = kept
            else:
                del self._votes[plate]   # bounded memory, unlike the old dict

    def _merge_key(self, plate):
        """Fold a read differing by a single glyph into the heavier existing key."""
        if self.merge_distance < 1 or plate in self._votes:
            return plate
        best, best_weight = plate, -1.0
        for known, votes in self._votes.items():
            if _hamming1(known, plate):
                weight = sum(w for _, w in votes)
                if weight > best_weight:
                    best, best_weight = known, weight
        return best

    def add(self, plate, confidence):
        """Record a sighting; return the plate once it is locked, else None."""
        if not plate:
            return None
        now = self._time()
        self._expire(now)

        key = self._merge_key(plate)
        self._votes.setdefault(key, []).append((now, max(0.0, confidence) / 100.0))

        votes = self._votes[key]
        # Both gates matter: weight stops low-confidence noise, min_sightings
        # stops a single lucky high-confidence frame from locking on its own.
        if sum(w for _, w in votes) >= self.threshold and len(votes) >= self.min_sightings:
            return key
        return None

    def best(self):
        now = self._time()
        self._expire(now)
        if not self._votes:
            return None, 0.0
        plate = max(self._votes, key=lambda p: sum(w for _, w in self._votes[p]))
        return plate, sum(w for _, w in self._votes[plate])

    def snapshot(self):
        """Current tallies, for the debug overlay."""
        self._expire(self._time())
        return {p: round(sum(w for _, w in v), 2) for p, v in self._votes.items()}

    def reset(self):
        self._votes.clear()


# Backwards-compatible alias: camera.normalize_vehicle_number used to live here.
normalize_vehicle_number = normalize_plate
