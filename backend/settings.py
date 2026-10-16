"""
Runtime tunables an admin can change from the System page without a restart.

Each one overrides a `config` attribute that the pipeline reads at CALL time
(never cached at import), so applying a value is just setattr on `config`.
Saved values live in the `settings` table and are re-applied at startup;
environment variables still provide the defaults.
"""

import logging
from dataclasses import dataclass

import config
from backend.db import vehicle_db

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tunable:
    key: str            # config attribute name, also the settings.key
    label: str
    kind: type          # int | float | bool
    minimum: float = 0
    maximum: float = 0
    unit: str = ""
    help: str = ""

    def parse(self, raw):
        """Form/DB text -> typed value, or raise ValueError with a readable message."""
        if self.kind is bool:
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
        try:
            value = self.kind(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{self.label} must be a number.") from None
        if not self.minimum <= value <= self.maximum:
            raise ValueError(f"{self.label} must be between {self.minimum:g} and {self.maximum:g}.")
        return value


TUNABLES = [
    Tunable("MANUAL_CHECK_CONFIDENCE", "Manual-check confidence", int, 0, 100, "%",
            "Reads below this go to MANUAL CHECK instead of being auto-decided."),
    Tunable("VOTE_THRESHOLD", "Lock threshold", float, 0.5, 10, "votes",
            "Confidence-weighted sightings needed before a plate locks."),
    Tunable("REPEAT_COOLDOWN", "Repeat-plate cooldown", float, 0, 600, "s",
            "The same plate is ignored at a gate for this long after a decision."),
    Tunable("DETECT_INTERVAL", "Detection interval", float, 0.05, 5, "s",
            "Seconds between inference attempts per gate."),
    Tunable("MIN_SHARPNESS", "Minimum sharpness", float, 0, 300, "",
            "Blurrier crops skip OCR. 0 disables the check."),
    Tunable("SAVE_DETECTION_CROPS", "Save evidence images", bool,
            help="Store the frame and plate crop for every locked read."),
]
BY_KEY = {t.key: t for t in TUNABLES}


def current_values():
    return {t.key: getattr(config, t.key) for t in TUNABLES}


def apply_saved():
    """Push saved values onto `config`. Unknown or invalid rows are skipped."""
    try:
        with vehicle_db() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
    except Exception as exc:          # table missing before init: nothing to apply
        log.debug("settings not loaded: %s", exc)
        return
    for row in rows:
        tunable = BY_KEY.get(row["key"])
        if tunable is None:
            continue
        try:
            setattr(config, tunable.key, tunable.parse(row["value"]))
        except ValueError as exc:
            log.warning("ignoring saved setting %s=%r: %s", row["key"], row["value"], exc)


def save(form):
    """Validate every tunable from a form, then persist and apply them together."""
    values = {}
    for tunable in TUNABLES:
        raw = form.get(tunable.key, "") if tunable.kind is not bool else form.get(tunable.key)
        values[tunable.key] = tunable.parse(raw if raw is not None else "")
    with vehicle_db() as conn:
        with conn:
            conn.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(key, str(int(v) if isinstance(v, bool) else v)) for key, v in values.items()])
    for key, value in values.items():
        setattr(config, key, value)
    log.info("settings updated: %s", values)
    return values
