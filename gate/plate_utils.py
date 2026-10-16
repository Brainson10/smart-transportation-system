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


def hamming1(a, b):
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


_hamming1 = hamming1   # old private name


class PlateVoter:
    """
    Confidence-weighted votes inside a rolling time window.

    Replaces `PLATE_VOTES = defaultdict(int)` with VOTE_THRESHOLD = 2, which had
    no expiry at all: a plate seen once now and once ten minutes later would lock
    the gate, and counts from one vehicle leaked into the next.

    Each sighting keeps the exact text that was read, so a lock is resolved by a
    per-character weighted majority (consensus) rather than by whichever spelling
    happened to arrive first and became the key.

    window / threshold / min_sightings fall back to `config` at call time, so the
    admin System page can retune them without restarting the worker.
    """

    def __init__(self, window=None, threshold=None, min_sightings=None,
                 merge_distance=None, time_fn=time.monotonic):
        self._window = window
        self._threshold = threshold
        self._min_sightings = min_sightings
        self._merge_distance = merge_distance
        self._time = time_fn
        self._votes = {}   # key -> list[(timestamp, weight, plate_as_read)]

    @property
    def window(self):
        return config.VOTE_WINDOW if self._window is None else self._window

    @property
    def threshold(self):
        return config.VOTE_THRESHOLD if self._threshold is None else self._threshold

    @property
    def min_sightings(self):
        return config.VOTE_MIN_SIGHTINGS if self._min_sightings is None else self._min_sightings

    @property
    def merge_distance(self):
        return config.VOTE_MERGE_DISTANCE if self._merge_distance is None else self._merge_distance

    def _expire(self, now):
        cutoff = now - self.window
        for plate in list(self._votes):
            kept = [v for v in self._votes[plate] if v[0] >= cutoff]
            if kept:
                self._votes[plate] = kept
            else:
                del self._votes[plate]   # bounded memory, unlike the old dict

    @staticmethod
    def _weight(votes):
        return sum(v[1] for v in votes)

    def _merge_key(self, plate):
        """Fold a read differing by a single glyph into the heavier existing key."""
        if self.merge_distance < 1 or plate in self._votes:
            return plate
        best, best_weight = plate, -1.0
        for known, votes in self._votes.items():
            if hamming1(known, plate):
                weight = self._weight(votes)
                if weight > best_weight:
                    best, best_weight = known, weight
        return best

    def consensus(self, key):
        """
        Per-position weighted majority over the sightings merged under `key`.

        If MN05AB1239 arrived first (and became the key) and MN05AB1234 was then
        read twice, the key alone would lock the misread; the consensus picks
        '4' for the last glyph. Falls back to the key if the result is not a
        valid plate.
        """
        votes = [v for v in self._votes.get(key, []) if len(v[2]) == len(key)]
        if not votes:
            return key
        chars = []
        for index in range(len(key)):
            tally = {}
            for _, weight, plate in votes:
                tally[plate[index]] = tally.get(plate[index], 0.0) + weight
            chars.append(max(sorted(tally), key=lambda c: tally[c]))
        text = "".join(chars)
        return text if is_valid_plate(text) else key

    def add(self, plate, confidence):
        """Record a sighting; return the (consensus) plate once locked, else None."""
        if not plate:
            return None
        now = self._time()
        self._expire(now)

        key = self._merge_key(plate)
        self._votes.setdefault(key, []).append((now, max(0.0, confidence) / 100.0, plate))

        votes = self._votes[key]
        # Both gates matter: weight stops low-confidence noise, min_sightings
        # stops a single lucky high-confidence frame from locking on its own.
        if self._weight(votes) >= self.threshold and len(votes) >= self.min_sightings:
            return self.consensus(key)
        return None

    def best(self):
        now = self._time()
        self._expire(now)
        if not self._votes:
            return None, 0.0
        plate = max(self._votes, key=lambda p: self._weight(self._votes[p]))
        return plate, self._weight(self._votes[plate])

    def leader(self):
        """The plate closest to locking, for the gate console's progress bar."""
        plate, weight = self.best()
        if plate is None:
            return None
        return {
            "plate": self.consensus(plate),
            "weight": round(weight, 2),
            "threshold": self.threshold,
            "sightings": len(self._votes[plate]),
            "min_sightings": self.min_sightings,
            "progress": round(min(1.0, weight / self.threshold) if self.threshold else 1.0, 2),
        }

    def snapshot(self):
        """Current tallies, for the debug overlay."""
        self._expire(self._time())
        return {p: round(self._weight(v), 2) for p, v in self._votes.items()}

    def reset(self):
        self._votes.clear()


class RecentPlates:
    """
    Plates already decided at one gate, ignored until their cooldown ends.

    A plate one glyph away from a cooling plate is treated as the same vehicle:
    otherwise a single misread frame would slip past and re-decide it.
    """

    def __init__(self, cooldown=None, time_fn=time.monotonic):
        self._cooldown = cooldown
        self._time = time_fn
        self._seen = {}   # plate -> decided-at timestamp

    @property
    def cooldown(self):
        return config.REPEAT_COOLDOWN if self._cooldown is None else self._cooldown

    def _expire(self, now):
        for plate in [p for p, ts in self._seen.items() if now - ts >= self.cooldown]:
            del self._seen[plate]

    def add(self, plate):
        if plate:
            self._seen[plate] = self._time()

    def remaining(self, plate):
        """Seconds until `plate` may be decided again; 0 when it is not cooling."""
        if not plate:
            return 0.0
        now = self._time()
        self._expire(now)
        matches = [ts for known, ts in self._seen.items()
                   if known == plate or hamming1(known, plate)]
        if not matches:
            return 0.0
        return max(0.0, self.cooldown - (now - max(matches)))

    def snapshot(self):
        now = self._time()
        self._expire(now)
        return {p: round(self.cooldown - (now - ts), 1) for p, ts in self._seen.items()}

    def clear(self):
        self._seen.clear()


# =================================================
# NEAR-MATCH AGAINST THE REGISTRY
# -------------------------------------------------
# Pairs OCR genuinely confuses. A registered plate that differs from the read
# by one of these is a much more likely misread than an arbitrary difference.
# =================================================
CONFUSABLE = frozenset(
    {frozenset(pair) for pair in list(LETTER_FIXES.items()) + list(DIGIT_FIXES.items())}
    | {frozenset(pair) for pair in ("OD", "OQ", "DQ", "EF", "MN", "HN", "VW", "UV",
                                     "CG", "PR", "KX", "38", "68", "69", "17")}
)


def _edit_distance1(a, b):
    """
    None if a and b are more than one edit apart, else (kind, confusable):
    kind is 'same' | 'sub' | 'indel'.
    """
    if a == b:
        return "same", True
    if len(a) == len(b):
        diffs = [(x, y) for x, y in zip(a, b) if x != y]
        if len(diffs) != 1:
            return None
        return "sub", frozenset(diffs[0]) in CONFUSABLE
    if abs(len(a) - len(b)) != 1:
        return None
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    for index in range(len(long_)):
        if long_[:index] + long_[index + 1:] == short:
            return "indel", False
    return None


def find_near_match(plate, registered):
    """
    The registered plate a read most plausibly was, or None.

    Only ever a SUGGESTION for the operator (the gate routes it to MANUAL CHECK):
    silently snapping to it could admit the wrong vehicle.
    """
    plate = clean_ocr_text(plate)
    if not plate:
        return None
    ranked = []
    for candidate in registered:
        candidate = clean_ocr_text(candidate)
        result = _edit_distance1(plate, candidate)
        if result is None or result[0] == "same":
            continue
        kind, confusable = result
        # Confusable substitutions first, then any substitution, then indels.
        rank = 0 if (kind == "sub" and confusable) else 1 if kind == "sub" else 2
        ranked.append((rank, candidate))
    if not ranked:
        return None
    ranked.sort()
    best_rank = ranked[0][0]
    top = [c for r, c in ranked if r == best_rank]
    # Two equally good candidates: do not guess between them.
    return top[0] if len(top) == 1 else None


# =================================================
# DISPLAY
# =================================================
_CREDENTIALS_RE = re.compile(r"^([a-z][a-z0-9+.-]*://)([^:/@]+):([^@/]+)@", re.IGNORECASE)


def mask_source(source):
    """rtsp://user:secret@host -> rtsp://user:***@host, for screens and logs."""
    return _CREDENTIALS_RE.sub(r"\1\2:***@", str(source if source is not None else ""))


# Backwards-compatible alias: camera.normalize_vehicle_number used to live here.
normalize_vehicle_number = normalize_plate
