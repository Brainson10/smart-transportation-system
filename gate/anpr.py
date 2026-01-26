import cv2
import pytesseract
from pytesseract import TesseractError
from ultralytics import YOLO
import os

# =================================================
# LOAD YOLO MODEL (ONCE)
# =================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "models", "license_plate_detector.pt")

model = YOLO(MODEL_PATH)

# =================================================
# ANPR FUNCTION
# =================================================
def run_anpr(frame):
    results = model(frame, conf=0.4, iou=0.5, verbose=False)

    for r in results:
        if r.boxes is None:
            continue

        for box in r.boxes:
            h, w, _ = frame.shape
            x1 = max(0, int(box.xyxy[0][0]))
            y1 = max(0, int(box.xyxy[0][1]))
            x2 = min(w, int(box.xyxy[0][2]))
            y2 = min(h, int(box.xyxy[0][3]))

            plate = frame[y1:y2, x1:x2]

            if plate is None or plate.size == 0:
                continue

            if (x2 - x1) * (y2 - y1) < 1500:
                continue

            gray = cv2.cvtColor(plate, cv2.COLOR_BGR2GRAY)
            gray = cv2.bilateralFilter(gray, 11, 17, 17)
            _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)

            try:
                text = pytesseract.image_to_string(
                    thresh,
                    config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                ).strip()
            except TesseractError:
                continue

            if not text or len(text) < 6:
                continue

            confidence = min(60 + len(text) * 5, 95)

            return {
                "status": "PLATE_DETECTED",
                "vehicle_number": text,
                "confidence": confidence,
                "bbox": (x1, y1, x2 - x1, y2 - y1)
            }

    return {
        "status": "NO_VEHICLE",
        "vehicle_number": None,
        "confidence": 0
    }
