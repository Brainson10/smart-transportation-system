import cv2
import atexit
import numpy as np
import re
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

# 🔥 PLATE VOTING
PLATE_VOTES = defaultdict(int)
VOTE_THRESHOLD = 2  # plate must appear 2 times

frame_count = 0
prev_gray = None
last_frame = None

# =================================================
# MAIN FRAME GENERATOR
# =================================================
def generate_frames(gate=None):
    global frame_count, prev_gray, last_frame, PLATE_VOTES

    while True:
        try:
            # 🔒 HARD PAUSE (FREEZE FRAME)
            if decision.PAUSED_FOR_MANUAL:
                if last_frame is not None:
                    yield _encode_frame(last_frame)
                else:
                    yield _black_frame()
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
                yield _encode_frame(frame)
                continue

            # ================= FRAME SKIP =================
            frame_count += 1
            if frame_count % FRAME_SKIP != 0:
                yield _encode_frame(frame)
                continue

            # ================= ANPR =================
            try:
                anpr_result = run_anpr(frame)
            except Exception as e:
                print(" ANPR ERROR:", e)
                yield _encode_frame(frame)
                continue

            # ================= PLATE SIZE FILTER =================
            if "bbox" in anpr_result:
                x, y, w, h = anpr_result["bbox"]
                if w * h < MIN_PLATE_AREA:
                    yield _encode_frame(frame)
                    continue

            raw_plate = anpr_result.get("vehicle_number")
            plate = normalize_vehicle_number(raw_plate)

            # 🔍 DEBUG (THIS IS CRITICAL)
            print("OCR RAW:", raw_plate)
            print("NORMALIZED:", plate)

            # ================= VOTING =================
            if plate:
                PLATE_VOTES[plate] += 1
                print("VOTE:", plate, PLATE_VOTES[plate])

                if PLATE_VOTES[plate] >= VOTE_THRESHOLD:
                    anpr_result["vehicle_number"] = plate

                    try:
                        final_decision = decide(anpr_result, gate)
                    except Exception as e:
                        print(" DECISION ERROR:", e)
                        final_decision = {
                            "vehicle_number": plate,
                            "confidence": anpr_result.get("confidence", 0),
                            "decision": "MANUAL CHECK",
                            "reason": "Decision engine error"
                        }

                    update_latest_result(final_decision)

                    # CLEAR ONLY AFTER FINAL LOCK
                    PLATE_VOTES.clear()

                    yield _encode_frame(frame)
                    continue

            #  Junk OCR → just continue streaming
            yield _encode_frame(frame)

        except Exception as e:
            print(" STREAM LOOP ERROR:", e)
            if last_frame is not None:
                yield _encode_frame(last_frame)
            else:
                yield _black_frame()
            continue

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

# =================================================
# PLATE NORMALIZER (FINAL & CORRECT)
# =================================================
def normalize_vehicle_number(raw):
    if not raw:
        return None

    text = raw.upper()
    text = re.sub(r"[^A-Z0-9]", "", text)

    #  extract flexible Indian plate first
    match = re.search(r"[A-Z]{2}[0-9]{2}[A-Z]{1,2}[0-9]{4}", text)
    if not match:
        return None

    plate = match.group(0)

    #  NOW fix OCR mistakes ONLY in numeric part
    state = plate[:2]
    rto = plate[2:4]
    series = plate[4:-4]
    number = plate[-4:]

    number = (
        number.replace("O", "0")
              .replace("I", "1")
              .replace("L", "1")
              .replace("S", "5")
              .replace("B", "8")
    )

    return state + rto + series + number

