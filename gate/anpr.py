import cv2
import pytesseract
from pytesseract import TesseractError
from ultralytics import YOLO
import os
import re

# =================================================
# LOAD YOLO MODEL (ONCE)
# =================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "models", "license_plate_detector.pt")

model = YOLO(MODEL_PATH)

# =================================================
# PLATE REGEX (INDIAN FORMAT)
# =================================================
PLATE_REGEX = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z]{2}[0-9]{4}")

def extract_plate(text):
    """
    Extract valid plate from noisy OCR output
    """
    if not text:
        return None

    cleaned = (
        text.upper()
        .replace(" ", "")
        .replace("\n", "")
        .replace("\f", "")
    )

    match = PLATE_REGEX.search(cleaned)
    return match.group(0) if match else None


# =================================================
# ANPR FUNCTION
# =================================================
def run_anpr(frame):
    results = model(frame, conf=0.35, iou=0.5, verbose=False)

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

            area = (x2 - x1) * (y2 - y1)
            if area < 1200:
                continue

            # ================= PREPROCESS =================
            gray = cv2.cvtColor(plate, cv2.COLOR_BGR2GRAY)

            gray = cv2.resize(
                gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC
            )

            gray = cv2.GaussianBlur(gray, (5, 5), 0)

            thresh = cv2.adaptiveThreshold(
                gray,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                11,
                2
            )

            # ================= OCR =================
            plate_img = frame[y1:y2, x1:x2]

            if plate_img is None or plate_img.size == 0:
                continue

            h, w, _ = plate_img.shape
            if w * h < 2000:
                continue

            gray = cv2.cvtColor(plate_img, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            gray = cv2.bilateralFilter(gray, 11, 17, 17)

            custom_config = r"--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            text = pytesseract.image_to_string(gray, config=custom_config)

            print("OCR RAW >>>", repr(text))

            # ================= EXTRACT PLATE =================
            plate_number = extract_plate(text)

            if not plate_number:
                continue

            confidence = min(70 + len(plate_number) * 3, 95)

            return {
                "status": "PLATE_DETECTED",
                "vehicle_number": plate_number,
                "confidence": confidence,
                "bbox": (x1, y1, x2 - x1, y2 - y1)
            }

    return {
        "status": "NO_VEHICLE",
        "vehicle_number": None,
        "confidence": 0
    }
