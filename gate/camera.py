import cv2
import atexit
import numpy as np

from gate.anpr import run_anpr
from gate.decision import decide, update_latest_result

cap = cv2.VideoCapture(0)
print("Camera opened:", cap.isOpened())

@atexit.register
def cleanup():
    cap.release()

# ===============================
# ADVANCED SETTINGS
# ===============================
FRAME_SKIP = 5
MOTION_THRESHOLD = 1500
MIN_PLATE_AREA = 1200

frame_count = 0
prev_gray = None


def generate_frames(gate=None):
    global frame_count, prev_gray

    while True:
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

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        # -----------------------------
        # MOTION DETECTION
        # -----------------------------
        motion_detected = False
        if prev_gray is not None:
            delta = cv2.absdiff(prev_gray, gray)
            thresh = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)[1]
            motion_score = cv2.countNonZero(thresh)

            if motion_score > MOTION_THRESHOLD:
                motion_detected = True

        prev_gray = gray

        # -----------------------------
        # NO MOTION → WAIT
        # -----------------------------
        if not motion_detected:
            update_latest_result({
                "decision": "WAITING",
                "reason": "No vehicle detected"
            })
            yield _encode_frame(frame)
            continue

        # -----------------------------
        # FRAME SKIPPING
        # -----------------------------
        frame_count += 1
        if frame_count % FRAME_SKIP != 0:
            yield _encode_frame(frame)
            continue

        # -----------------------------
        # RUN ANPR
        # -----------------------------
        anpr_result = run_anpr(frame)

        # Plate size filtering (if bbox exists)
        if "bbox" in anpr_result:
            x, y, w, h = anpr_result["bbox"]
            if w * h < MIN_PLATE_AREA:
                update_latest_result({
                    "decision": "WAITING",
                    "reason": "Vehicle too far"
                })
                yield _encode_frame(frame)
                continue

        final_decision = decide(anpr_result)
        update_latest_result(final_decision)

        yield _encode_frame(frame)


# ===============================
# HELPERS
# ===============================
def _encode_frame(frame):
    ret, buffer = cv2.imencode(".jpg", frame)
    return (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
    )


def _black_frame():
    black = np.zeros((480, 640, 3), dtype=np.uint8)
    ret, buffer = cv2.imencode(".jpg", black)
    return (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
    )


def generate_demo_frames(video_path):
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        print("Demo video not found")
        return

    while True:
        success, frame = cap.read()

        if not success:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # 🔁 loop demo
            continue

        # 👉 SAME ANPR PIPELINE CAN BE CALLED HERE
        # anpr_result = run_anpr(frame)
        # decision = decide(anpr_result)

        ret, buffer = cv2.imencode(".jpg", frame)
        frame = buffer.tobytes()

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )
