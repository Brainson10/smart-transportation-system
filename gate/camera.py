"""
MJPEG streaming and the detection worker.

The key architectural change: streaming and inference are now separate threads.
Previously generate_frames() ran YOLO + OCR inline, so every slow OCR call
stalled the video feed, and three code paths `continue`d with no sleep at all --
busy-spinning the CPU while re-encoding the same frozen JPEG.

Per-gate state also replaces module globals, so two gates can no longer corrupt
each other's votes, and reset_detection_state() actually resets things (the old
resume_detection rebound imported names, which only touched locals and left
gate.camera's globals stale).
"""

import logging
import threading
import time
from datetime import datetime

import cv2
import numpy as np

import config
from gate import capture, decision
from gate.anpr import annotate, run_anpr
from gate.decision import decide, update_latest_result
from gate.plate_utils import PlateVoter, normalize_plate

log = logging.getLogger(__name__)

# Re-exported for backwards compatibility: this used to live in this module.
normalize_vehicle_number = normalize_plate


class DetectionState:
    """Everything one gate's detection worker needs, guarded by its own lock."""

    def __init__(self, gate):
        self.gate = gate
        self.lock = threading.Lock()
        self.voter = PlateVoter()
        self.detections = []
        self.frozen_frame = None
        self.last_infer_ts = 0.0
        self.last_motion_ts = 0.0
        self.prev_gray = None
        self.best_by_plate = {}
        self.infer_ms = 0.0
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


_STATES = {}
_STATES_LOCK = threading.Lock()


def _state_key(gate):
    return (gate or "default").strip().lower()


def get_state(gate=None):
    key = _state_key(gate)
    with _STATES_LOCK:
        state = _STATES.get(key)
        if state is None:
            state = DetectionState(key)
            _STATES[key] = state
        return state


def reset_detection_state(gate=None):
    """
    Real reset, called by decision.resume_detection.

    The old code did `from gate.camera import PLATE_VOTES, frame_count, prev_gray`
    then `frame_count = 0; prev_gray = None`, which rebound local names only --
    gate.camera's globals survived, so the frame after a resume was motion-diffed
    against a frame from before the pause.
    """
    with _STATES_LOCK:
        states = list(_STATES.values()) if gate is None else [_STATES.get(_state_key(gate))]
    for state in states:
        if state is not None:
            state.reset()
            log.debug("detection state reset (gate=%s)", state.gate)


# =================================================
# MOTION (a cost-saver only, never a gate)
# =================================================
def _motion(state, frame):
    """
    Cheap frame-difference motion check.

    The original code used this as a hard gate AND refreshed prev_gray every
    frame, so a vehicle that stopped at the gate produced no delta and was never
    OCR'd -- detection only ever ran while the plate was moving, and therefore
    blurriest. prev_gray is now only refreshed on a still scene, so "arrived and
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
def _save_evidence(frame, detection, plate, confidence):
    if not config.SAVE_DETECTION_CROPS:
        return None
    try:
        now = datetime.now()
        folder = config.DETECTION_DIR / now.strftime("%Y%m%d")
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{now.strftime('%H%M%S')}_{plate}_{confidence}"
        path = folder / f"{stem}.jpg"
        cv2.imwrite(str(path), frame)
        if detection is not None and detection.crop is not None and detection.crop.size:
            cv2.imwrite(str(folder / f"{stem}_crop.jpg"), detection.crop)
        return str(path)
    except Exception as exc:
        log.warning("could not save evidence: %s", exc)
        return None


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


def _worker_loop(state):
    stream = capture.get_stream(state.gate)
    last_seq = -1

    while not state.stop_event.is_set():
        if decision.is_paused():
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
                update_latest_result({
                    "decision": "WAITING",
                    "reason": "Plate detected, not readable yet",
                })
            continue

        confidence = result["confidence"]
        with state.lock:
            # Keep the BEST sighting of each plate, not the last: a marginal
            # final frame should not drag a good read into MANUAL CHECK.
            previous = state.best_by_plate.get(plate)
            if previous is None or confidence > previous["confidence"]:
                state.best_by_plate[plate] = result
            locked = state.voter.add(plate, confidence)

        if not locked:
            log.debug("vote %s conf=%d tally=%s", plate, confidence,
                      state.voter.snapshot())
            continue

        with state.lock:
            best = state.best_by_plate.get(locked, result)
            best_detection = next(
                (d for d in best["detections"] if d.plate == locked), None
            )
            state.voter.reset()
            state.best_by_plate.clear()

        image_path = _save_evidence(frame, best_detection, locked, best["confidence"])
        log.info("LOCKED %s conf=%d raw=%r evidence=%s",
                 locked, best["confidence"], best["raw_text"], image_path)

        try:
            decide({
                "status": best["status"],
                "vehicle_number": locked,
                "confidence": best["confidence"],
                "bbox": best["bbox"],
                "raw_text": best["raw_text"],
            }, state.gate)
        except Exception:
            log.exception("decision engine failed")
            update_latest_result({
                "vehicle_number": locked,
                "confidence": best["confidence"],
                "decision": "MANUAL CHECK",
                "reason": "Decision engine error",
            })


# =================================================
# MJPEG STREAM (no inference here)
# =================================================
def _overlay(state, frame, stream):
    with state.lock:
        detections = list(state.detections)
        tally = state.voter.snapshot()
        infer_ms = state.infer_ms
    lines = [
        f"gate={state.gate}  source={config.CAMERA_SOURCE}  device={config.YOLO_DEVICE}",
        f"engine={config.OCR_ENGINE}  infer={infer_ms:.0f}ms  frames={stream.stats()['frames']}",
    ]
    if tally:
        lines.append(f"votes: {tally}")
    return annotate(frame, detections, lines)


def generate_frames(gate=None):
    """
    MJPEG generator. Paces to STREAM_FPS and never runs inference, so the feed
    stays smooth no matter how long OCR takes.
    """
    state = get_state(gate)
    stream = capture.get_stream(state.gate)
    start_detection_worker(gate)

    interval = 1.0 / max(1, config.STREAM_FPS)

    while True:
        started = time.monotonic()
        try:
            if decision.is_paused():
                # Show the frozen frame WITH its detection box still drawn.
                frozen = state.frozen_frame
                yield _encode_frame(frozen) if frozen is not None else _black_frame("PAUSED")
                time.sleep(0.5)
                continue

            frame, _ = stream.read(timeout=1.0)
            if frame is None:
                update_latest_result({
                    "decision": "WAITING",
                    "reason": "Camera not accessible",
                })
                yield _black_frame("CAMERA OFFLINE")
                time.sleep(0.5)
                continue

            annotated = _overlay(state, frame, stream)
            state.frozen_frame = annotated
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
