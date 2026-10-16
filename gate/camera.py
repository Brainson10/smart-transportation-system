"""
MJPEG streaming and the detection worker.

The key architectural change: streaming and inference are separate threads.
Previously generate_frames() ran YOLO + OCR inline, so every slow OCR call
stalled the video feed, and three code paths `continue`d with no sleep at all --
busy-spinning the CPU while re-encoding the same frozen JPEG.

Per-gate state also replaces module globals, so two gates can no longer corrupt
each other's votes, and reset_detection_state() actually resets things.

This round adds:
  * a repeat-plate cooldown, so a car still in view after "Next Vehicle" is
    not decided (and fined, and texted) a second time;
  * evidence taken from the BEST sighting's own frame (it used to pair the
    best crop with whatever frame happened to be current at lock time);
  * the exact locked frame frozen on screen while the gate waits;
  * a camera source per gate, from the Cameras admin page.
"""

import dataclasses
import logging
import re
import threading
import time
from datetime import datetime

import cv2
import numpy as np

import config
from backend.db import authority_db
from gate import capture, decision
from gate.anpr import annotate, run_anpr
from gate.decision import decide
from gate.plate_utils import PlateVoter, RecentPlates, hamming1, normalize_plate

log = logging.getLogger(__name__)

# Re-exported for backwards compatibility: this used to live in this module.
normalize_vehicle_number = normalize_plate

# How often a running worker / stream re-reads which camera feeds its gate, so
# a source assigned on the Cameras page takes effect without a restart.
SOURCE_REFRESH_SECONDS = 5.0


class DetectionState:
    """Everything one gate's detection worker needs, guarded by its own lock."""

    def __init__(self, gate, label=None):
        self.gate = gate
        self.label = label or gate
        self.lock = threading.Lock()
        self.voter = PlateVoter()
        self.recent = RecentPlates()
        self.detections = []
        self.last_frame = None        # last annotated stream frame
        self.locked_frame = None      # annotated frame of the last lock
        self.last_infer_ts = 0.0
        self.last_motion_ts = 0.0
        self.prev_gray = None
        self.best_by_plate = {}       # plate -> (anpr result, raw frame)
        self.infer_ms = 0.0
        self.notice = None            # one-line status for the console
        self.stop_event = threading.Event()
        self.thread = None

    def reset(self):
        with self.lock:
            self.voter.reset()
            self.detections = []
            self.prev_gray = None
            self.best_by_plate.clear()
            self.last_infer_ts = 0.0
            self.last_motion_ts = 0.0
            self.locked_frame = None
            self.notice = None


_STATES = {}
_STATES_LOCK = threading.Lock()


def _state_key(gate):
    return decision.gate_key(gate)


def get_state(gate=None):
    key = _state_key(gate)
    with _STATES_LOCK:
        state = _STATES.get(key)
        if state is None:
            state = DetectionState(key, label=(gate or "default").strip())
            _STATES[key] = state
        return state


def reset_detection_state(gate=None):
    """Real reset, called on Next Vehicle. gate=None resets every gate."""
    with _STATES_LOCK:
        states = list(_STATES.values()) if gate is None else [_STATES.get(_state_key(gate))]
    for state in states:
        if state is not None:
            state.reset()
            log.debug("detection state reset (gate=%s)", state.gate)


def clear_cooldown(gate):
    get_state(gate).recent.clear()


def mark_processed(gate, plate):
    """Start the cooldown for a plate decided outside the worker (operator check)."""
    if plate:
        get_state(gate).recent.add(plate)


# =================================================
# CAMERA SOURCE PER GATE
# =================================================
def camera_source_for_gate(gate):
    """
    The source of the first ONLINE camera assigned to this gate, or None (which
    means the global ANPR_CAMERA_SOURCE).
    """
    try:
        with authority_db() as conn:
            row = conn.execute("""
                SELECT source FROM cameras
                WHERE LOWER(TRIM(location)) = LOWER(TRIM(?)) AND status = 'ONLINE'
                  AND source IS NOT NULL AND TRIM(source) != ''
                ORDER BY id LIMIT 1
            """, (decision.canonical_location(gate),)).fetchone()
    except Exception as exc:
        log.debug("camera lookup failed for %r: %s", gate, exc)
        return None
    return row["source"].strip() if row else None


def stream_for_gate(gate):
    """The (started) capture stream that feeds this gate."""
    return capture.get_stream(_state_key(gate), camera_source_for_gate(gate))


class _StreamRef:
    """Re-resolves a gate's stream every SOURCE_REFRESH_SECONDS."""

    def __init__(self, gate):
        self.gate = gate
        self.stream = None
        self.resolved_at = 0.0

    def get(self):
        now = time.monotonic()
        if self.stream is None or now - self.resolved_at > SOURCE_REFRESH_SECONDS:
            self.stream = stream_for_gate(self.gate)
            self.resolved_at = now
        return self.stream


def live_status(gate):
    """Reading progress, cooldowns and camera health for the gate console."""
    state = get_state(gate)
    stream = capture.peek_stream(camera_source_for_gate(gate))
    with state.lock:
        reading = state.voter.leader()
        cooldown = state.recent.snapshot()
        notice = state.notice
        infer_ms = state.infer_ms
    camera = stream.stats() if stream else {
        "name": state.gate, "source": None, "opened": False, "healthy": False,
        "frames": 0, "fps": 0.0, "reconnects": 0, "last_error": "not started", "age": None,
    }
    return {
        "reading": reading,
        "cooldown": cooldown,
        "notice": notice,
        "infer_ms": round(infer_ms),
        "worker": bool(state.thread and state.thread.is_alive()),
        "camera": camera,
    }


def worker_overview():
    """Every gate worker this process runs, for the admin System page."""
    with _STATES_LOCK:
        states = list(_STATES.values())
    rows = []
    for state in states:
        stream = capture.peek_stream(camera_source_for_gate(state.label))
        rows.append({
            "gate": state.label,
            "worker": bool(state.thread and state.thread.is_alive()),
            "paused": decision.is_paused(state.gate),
            "infer_ms": round(state.infer_ms),
            "cooldown": state.recent.snapshot(),
            "camera": stream.stats() if stream else None,
        })
    return rows


# =================================================
# MOTION (a cost-saver only, never a gate)
# =================================================
def _motion(state, frame):
    """
    Cheap frame-difference motion check.

    The original code used this as a hard gate AND refreshed prev_gray every
    frame, so a vehicle that stopped at the gate produced no delta and was never
    OCR'd. prev_gray is now only refreshed on a still scene, so "arrived and
    stopped" still registers, and MOTION_FORCE_INTERVAL guarantees inference
    regardless.
    """
    gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (21, 21), 0)
    if state.prev_gray is None:
        state.prev_gray = gray
        return True

    delta = cv2.absdiff(state.prev_gray, gray)
    thresh = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)[1]
    moving = cv2.countNonZero(thresh) > config.MOTION_THRESHOLD
    if not moving:
        state.prev_gray = gray
    return moving


# =================================================
# EVIDENCE
# =================================================
def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text or "")).strip("-")[:40] or "gate"


def _save_evidence(frame, detection, plate, confidence, gate="gate"):
    """
    Write the frame and plate crop of the sighting that won.
    Returns (frame_path, crop_path) relative to DETECTION_DIR, or (None, None).
    """
    if not config.SAVE_DETECTION_CROPS or frame is None:
        return None, None
    try:
        now = datetime.now()
        day = now.strftime("%Y%m%d")
        folder = config.DETECTION_DIR / day
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{now.strftime('%H%M%S_%f')}_{_slug(gate)}_{plate}_{confidence}"
        if not cv2.imwrite(str(folder / f"{stem}.jpg"), frame):
            return None, None
        crop_path = None
        if detection is not None and detection.crop is not None and detection.crop.size:
            if cv2.imwrite(str(folder / f"{stem}_crop.jpg"), detection.crop):
                crop_path = f"{day}/{stem}_crop.jpg"
        return f"{day}/{stem}.jpg", crop_path
    except Exception as exc:
        log.warning("could not save evidence: %s", exc)
        return None, None


def _best_sighting(best_by_plate, locked):
    """Highest-confidence sighting of the locked plate (or a one-glyph variant)."""
    matches = [entry for plate, entry in best_by_plate.items()
               if plate == locked or hamming1(plate, locked)]
    pool = matches or list(best_by_plate.values())
    if not pool:
        return None
    return max(pool, key=lambda entry: entry[0]["confidence"])


# =================================================
# DETECTION WORKER
# =================================================
def start_detection_worker(gate=None):
    """Idempotent: one worker thread per gate."""
    state = get_state(gate)
    with state.lock:
        if state.thread and state.thread.is_alive():
            return state
        state.stop_event.clear()
        state.thread = threading.Thread(
            target=_worker_loop, args=(state,), name=f"anpr-{state.gate}", daemon=True
        )
        state.thread.start()
    log.info("detection worker started (gate=%s)", state.gate)
    return state


def _unreadable_reason(result):
    detections = result["detections"]
    if detections and all(d.blurry for d in detections):
        return "Plate blurred - waiting for a sharper frame"
    return "Plate detected, not readable yet"


def _worker_loop(state):
    source = _StreamRef(state.label)
    last_seq, last_stream = -1, None

    while not state.stop_event.is_set():
        stream = source.get()
        if stream is not last_stream:
            last_seq, last_stream = -1, stream

        if decision.is_paused(state.gate):
            time.sleep(0.2)
            continue

        frame, seq = stream.read_new(last_seq, timeout=1.0)
        if frame is None:
            continue
        last_seq = seq

        now = time.monotonic()
        if now - state.last_infer_ts < config.DETECT_INTERVAL:
            continue

        # Motion only RAISES the rate; a still scene is still inferred every
        # MOTION_FORCE_INTERVAL seconds, so a stopped vehicle is always read.
        if config.MOTION_ENABLED:
            moving = _motion(state, frame)
            if moving:
                state.last_motion_ts = now
            elif now - state.last_infer_ts < config.MOTION_FORCE_INTERVAL:
                continue

        state.last_infer_ts = now
        started = time.monotonic()
        try:
            result = run_anpr(frame)
        except Exception:
            log.exception("ANPR failed")
            continue
        state.infer_ms = (time.monotonic() - started) * 1000.0

        with state.lock:
            state.detections = result["detections"]

        plate = result["vehicle_number"]
        if not plate:
            if result["status"] == "PLATE_UNREADABLE":
                state.notice = _unreadable_reason(result)
                decision.set_waiting(state.notice, state.gate)
            else:
                state.notice = None
            continue

        cooling = state.recent.remaining(plate)
        if cooling > 0:
            # Same vehicle still at the barrier: never decide it twice.
            state.notice = f"{plate} already processed - ignored for {cooling:.0f}s more"
            decision.set_waiting(f"{plate} already processed (Rescan to read it again)",
                                 state.gate)
            continue
        state.notice = None

        confidence = result["confidence"]
        with state.lock:
            # Keep the BEST sighting of each plate (with its own frame), not the
            # last: a marginal final frame must not drag a good read into
            # MANUAL CHECK or pair a crop with an unrelated frame.
            previous = state.best_by_plate.get(plate)
            if previous is None or confidence > previous[0]["confidence"]:
                state.best_by_plate[plate] = (result, frame)
            locked = state.voter.add(plate, confidence)

        if not locked:
            log.debug("vote %s conf=%d tally=%s", plate, confidence, state.voter.snapshot())
            continue

        with state.lock:
            best_result, best_frame = _best_sighting(state.best_by_plate, locked) or (result, frame)
            best_detection = next(
                (d for d in best_result["detections"] if d.plate == best_result["vehicle_number"]),
                None)
            state.voter.reset()
            state.best_by_plate.clear()

        frame_path, crop_path = _save_evidence(best_frame, best_detection, locked,
                                               best_result["confidence"], state.label)
        shown = [dataclasses.replace(best_detection, plate=locked)] if best_detection else []
        state.locked_frame = annotate(best_frame.copy(), shown,
                                      status=f"{state.label}  |  LOCKED {locked}")
        state.recent.add(locked)
        log.info("LOCKED %s conf=%d raw=%r evidence=%s",
                 locked, best_result["confidence"], best_result["raw_text"], frame_path)

        try:
            decide({
                "status": best_result["status"],
                "vehicle_number": locked,
                "confidence": best_result["confidence"],
                "bbox": best_result["bbox"],
                "raw_text": best_result["raw_text"],
                "evidence_path": frame_path,
                "crop_path": crop_path,
            }, state.gate)
        except Exception:
            log.exception("decision engine failed")
            decision.set_paused(True, state.gate)
            decision.update_latest_result({
                "vehicle_number": locked,
                "confidence": best_result["confidence"],
                "decision": decision.MANUAL,
                "reason": "Decision engine error",
            }, state.gate)


# =================================================
# MJPEG STREAM (no inference here)
# =================================================
def _overlay(state, frame, stream):
    with state.lock:
        detections = list(state.detections)
        leader = state.voter.leader()
        tally = state.voter.snapshot()
        infer_ms = state.infer_ms
        notice = state.notice

    status = f"{state.label}  |  {datetime.now().strftime('%H:%M:%S')}"
    if leader:
        status += f"  |  reading {leader['plate']}  {int(leader['progress'] * 100)}%"
    elif notice:
        status += f"  |  {notice}"
    else:
        status += "  |  scanning"

    lines = []
    if config.DEBUG_OVERLAY:
        stats = stream.stats()
        lines = [
            f"source={stream.display_source}  device={config.YOLO_DEVICE}",
            f"engine={config.OCR_ENGINE}  infer={infer_ms:.0f}ms  fps={stats['fps']}",
        ]
        if tally:
            lines.append(f"votes: {tally}")
    return annotate(frame, detections, lines, status=status)


def generate_frames(gate=None):
    """
    MJPEG generator. Paces to STREAM_FPS and never runs inference, so the feed
    stays smooth no matter how long OCR takes.
    """
    state = get_state(gate)
    source = _StreamRef(state.label)
    start_detection_worker(gate)

    interval = 1.0 / max(1, config.STREAM_FPS)

    while True:
        started = time.monotonic()
        try:
            stream = source.get()
            if decision.is_paused(state.gate):
                # Show the frame that locked, with its box, while the gate waits.
                frozen = state.locked_frame if state.locked_frame is not None else state.last_frame
                yield _encode_frame(frozen) if frozen is not None else _black_frame("PAUSED")
                time.sleep(0.5)
                continue

            frame, _ = stream.read(timeout=1.0)
            if frame is None or (stream.stats()["age"] or 0) > 5.0:
                decision.set_waiting("Camera not accessible", state.gate)
                yield _black_frame("CAMERA OFFLINE")
                time.sleep(0.5)
                continue

            annotated = _overlay(state, frame, stream)
            state.last_frame = annotated
            yield _encode_frame(annotated)

            time.sleep(max(0.0, interval - (time.monotonic() - started)))

        except GeneratorExit:
            raise
        except Exception:
            log.exception("stream loop error")
            yield _black_frame("STREAM ERROR")
            time.sleep(0.5)


# =================================================
# FRAME ENCODERS
# =================================================
def _part(buffer):
    return (b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + buffer.tobytes()
            + b"\r\n")


def _encode_frame(frame):
    ok, buffer = cv2.imencode(
        ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), config.STREAM_JPEG_QUALITY]
    )
    if not ok:                      # the old code ignored this return value
        return _black_frame("ENCODE ERROR")
    return _part(buffer)


def _black_frame(message="NO SIGNAL"):
    """A placeholder that says WHY there is no picture."""
    black = np.zeros((480, 640, 3), dtype=np.uint8)
    (tw, _), _ = cv2.getTextSize(message, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
    cv2.putText(black, message, ((640 - tw) // 2, 240),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (60, 60, 220), 2)
    ok, buffer = cv2.imencode(".jpg", black)
    return _part(buffer) if ok else b""
