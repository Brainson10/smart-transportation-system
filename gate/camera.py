import cv2
import atexit
import numpy as np
from collections import defaultdict

from gate.anpr import run_anpr
from gate import decision
from gate.decision import decide, update_latest_result

# =================================================
# CAMERA INIT
# =================================================
cap = cv2.VideoCapture(0)
print("Camera opened:", cap.isOpened())

@atexit.register
def cleanup():
    cap.release()

# =================================================
# CONFIG
# =================================================
FRAME_SKIP = 2
MOTION_THRESHOLD = 1500
MIN_PLATE_AREA = 700

# 🔥 PLATE VOTING (KEY FIX)
PLATE_VOTES = defaultdict(int)
VOTE_THRESHOLD = 3   # plate must appear 3 times

frame_count = 0
prev_gray = None
last_frame = None


# =================================================
# FRAME GENERATOR
# =================================================
def generate_frames(gate=None):
    global frame_count, prev_gray, last_frame, PLATE_VOTES

    while True:

        # 🔒 HARD PAUSE AFTER FINAL DECISION
        if decision.PAUSED_FOR_MANUAL:
            yield _encode_frame(last_frame)
            continue

        if not cap.isOpened():
            update_latest_result({
                "decision": "WAITING",
                "reason": "Camera not accessible"
            })
            yield _black_frame()
            continue

        success, frame = cap.read()
        if not success:
            update_latest_result({
                "decision": "WAITING",
                "reason": "No frame from camera"
            })
            continue

        last_frame = frame.copy()

        # ================= MOTION DETECTION =================
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        motion_detected = False
        if prev_gray is not None:
            delta = cv2.absdiff(prev_gray, gray)
            thresh = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)[1]
            if cv2.countNonZero(thresh) > MOTION_THRESHOLD:
                motion_detected = True

        prev_gray = gray

        if not motion_detected:
            update_latest_result({
                "decision": "WAITING",
                "reason": "No vehicle detected"
            })
            yield _encode_frame(frame)
            continue

        # ================= FRAME SKIP =================
        frame_count += 1
        if frame_count % FRAME_SKIP != 0:
            yield _encode_frame(frame)
            continue

        # ================= ANPR =================
        anpr_result = run_anpr(frame)

        if "bbox" in anpr_result:
            x, y, w, h = anpr_result["bbox"]
            if w * h < MIN_PLATE_AREA:
                yield _encode_frame(frame)
                continue

        raw_plate = anpr_result.get("vehicle_number")
        plate = normalize_plate_for_vote(raw_plate)

        if plate:
            PLATE_VOTES[plate] += 1

            # Pick most frequent plate
            best_plate = max(PLATE_VOTES, key=PLATE_VOTES.get)

            if PLATE_VOTES[best_plate] >= VOTE_THRESHOLD:
                anpr_result["vehicle_number"] = best_plate

                final_decision = decide(anpr_result, gate)
                update_latest_result(final_decision)

                PLATE_VOTES.clear()
                yield _encode_frame(frame)
                continue
            else:
                update_latest_result({
                    "decision": "WAITING",
                    "reason": "Stabilizing number plate"
                })
                yield _encode_frame(frame)
                continue

        # No valid plate this frame
        yield _encode_frame(frame)


# =================================================
# FRAME ENCODERS
# =================================================
def _encode_frame(frame):
    ret, buffer = cv2.imencode(".jpg", frame)
    return (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n\r\n"
        + buffer.tobytes()
        + b"\r\n"
    )


def _black_frame():
    black = np.zeros((480, 640, 3), dtype=np.uint8)
    ret, buffer = cv2.imencode(".jpg", black)
    return (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n\r\n"
        + buffer.tobytes()
        + b"\r\n"
    )

import re

def normalize_plate_for_vote(text):
    if not text:
        return None

    t = text.upper()
    t = re.sub(r'[^A-Z0-9]', '', t)

    # Fix common OCR confusions
    t = t.replace('O', '0')
    t = t.replace('I', '1')
    t = t.replace('L', '1')
    t = t.replace('Z', '2')
    t = t.replace('B', '8')

    # Indian plate heuristic: last 4 digits must be numbers
    if len(t) >= 6:
        return t[-10:]  # keep last 10 chars

    return None