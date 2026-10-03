"""
Automatic Number Plate Recognition: YOLO plate detection + OCR + real scoring.

What changed versus the original implementation:
  * The model is loaded lazily, not at import, so nothing heavy happens just by
    importing this module (the Flask reloader imports it twice).
  * The old code built an adaptive-threshold image and never used it, then
    re-cropped and built a DIFFERENT image for OCR. All preprocessing now lives
    in ocr.preprocess_variants() and every variant is actually scored.
  * Confidence was `min(70 + len(plate) * 3, 95)`, and the regex only matched
    10-character strings, so it was ALWAYS exactly 95. It is now fused from
    YOLO's box confidence, the OCR engine's own confidence, and how plate-like
    the text is.
  * The first matching box used to win; now every box is scored and the best
    read is returned.
"""

import logging
import threading
from dataclasses import dataclass, field

import cv2
import numpy as np
from ultralytics import YOLO

import config
from gate import ocr
from gate.plate_utils import fuse_confidence, normalize_plate_detailed

log = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()


@dataclass
class PlateDetection:
    bbox: tuple               # (x, y, w, h) in frame coordinates
    det_conf: float           # YOLO box confidence, 0..1
    plate: str = None         # canonical text, e.g. "MN05AB1234"
    raw_text: str = ""        # what OCR actually said, for logs and debugging
    ocr_conf: float = 0.0     # 0..100
    format_score: float = 0.0
    substitutions: int = 0
    confidence: int = 0       # fused 0..100
    variant: str = ""
    crop: np.ndarray = field(default=None, repr=False)

    @property
    def readable(self):
        return bool(self.plate)


def _get_model():
    """Lazy singleton; falls back to CPU if the requested device is unusable."""
    global _model
    with _model_lock:
        if _model is not None:
            return _model

        model = YOLO(str(config.YOLO_MODEL_PATH))
        device = config.YOLO_DEVICE
        try:
            model.to(device)
        except Exception as exc:
            log.warning("cannot use device %r (%s); using cpu", device, exc)
            device = "cpu"
            model.to(device)

        # Warm-up so the first real frame is not the slow one.
        try:
            model(np.zeros((config.YOLO_IMGSZ, config.YOLO_IMGSZ, 3), np.uint8),
                  imgsz=config.YOLO_IMGSZ, device=device, verbose=False)
        except Exception as exc:
            log.debug("warm-up inference failed: %s", exc)

        log.info("YOLO plate detector loaded (device=%s, imgsz=%d)",
                 device, config.YOLO_IMGSZ)
        _model = model
        return _model


def detect_plates(frame):
    """All plate boxes, padded and size-filtered, best detection confidence first."""
    if frame is None or frame.size == 0:
        return []

    model = _get_model()
    results = model(frame, conf=config.YOLO_CONF, iou=config.YOLO_IOU,
                    imgsz=config.YOLO_IMGSZ, max_det=config.YOLO_MAX_DET,
                    device=config.YOLO_DEVICE, verbose=False)

    height, width = frame.shape[:2]
    boxes = []
    for result in results:
        if result.boxes is None:
            continue
        for box in result.boxes:
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
            det_conf = float(box.conf[0]) if box.conf is not None else 0.0

            # Plate detectors crop tight and clip the outer glyphs; pad a little.
            pad_x = (x2 - x1) * config.BOX_PAD_RATIO
            pad_y = (y2 - y1) * config.BOX_PAD_RATIO
            x1 = max(0, int(x1 - pad_x))
            y1 = max(0, int(y1 - pad_y))
            x2 = min(width, int(x2 + pad_x))
            y2 = min(height, int(y2 + pad_y))

            w, h = x2 - x1, y2 - y1
            if w <= 0 or h <= 0:
                continue
            if w * h < config.MIN_PLATE_AREA or w < config.MIN_PLATE_WIDTH:
                continue
            boxes.append(((x1, y1, w, h), det_conf))

    boxes.sort(key=lambda item: item[1], reverse=True)
    return boxes


def read_plate(crop, det_conf, bbox):
    """OCR one plate crop across every preprocessing variant; keep the best read."""
    detection = PlateDetection(bbox=bbox, det_conf=det_conf, crop=crop)
    engine = ocr.get_engine()

    best_score = -1.0
    best_raw = ("", 0.0)      # fallback text even when nothing parses

    for variant, image in ocr.preprocess_variants(crop):
        for result in engine.read(image, variant):
            if result.char_conf > best_raw[1]:
                best_raw = (result.text, result.char_conf)
            if result.char_conf < config.OCR_MIN_CHAR_CONF:
                continue

            candidate = normalize_plate_detailed(result.text)
            if candidate is None:
                continue

            score = (0.60 * (result.char_conf / 100.0)
                     + 0.25 * candidate.format_score
                     + 0.15 * max(0.0, 1.0 - candidate.substitutions / 4.0))
            if score > best_score:
                best_score = score
                detection.plate = candidate.text
                detection.raw_text = result.text
                detection.ocr_conf = result.char_conf
                detection.format_score = candidate.format_score
                detection.substitutions = candidate.substitutions
                detection.variant = variant
                detection.confidence = fuse_confidence(
                    det_conf, result.char_conf,
                    candidate.format_score, candidate.substitutions,
                )

        # A confident, well-formed read makes the remaining variants pointless:
        # 1-2 OCR calls in the good case instead of up to 15.
        if (detection.readable
                and detection.ocr_conf >= config.OCR_EARLY_EXIT_CONF
                and detection.format_score >= 0.9):
            break

    if not detection.readable:
        detection.raw_text = best_raw[0]
        detection.ocr_conf = best_raw[1]

    return detection


def run_anpr(frame):
    """
    Same entry point and dict keys as before, so decision.decide() is untouched.

    `PLATE_UNREADABLE` is new: it lets the caller distinguish "a plate is there
    but I cannot read it yet" from "no vehicle", which the old code conflated.
    """
    detections = []
    for bbox, det_conf in detect_plates(frame):
        x, y, w, h = bbox
        crop = frame[y:y + h, x:x + w]
        if crop is None or crop.size == 0:
            continue
        detection = read_plate(crop, det_conf, bbox)
        detections.append(detection)
        log.debug("box=%s det=%.2f raw=%r plate=%s ocr=%.1f conf=%d variant=%s",
                  bbox, det_conf, detection.raw_text, detection.plate,
                  detection.ocr_conf, detection.confidence, detection.variant)

    readable = [d for d in detections if d.readable]
    if readable:
        best = max(readable, key=lambda d: d.confidence)
        status = "PLATE_DETECTED"
    elif detections:
        best = max(detections, key=lambda d: d.det_conf)
        status = "PLATE_UNREADABLE"
    else:
        best = None
        status = "NO_VEHICLE"

    return {
        "status": status,
        "vehicle_number": best.plate if best else None,
        "confidence": best.confidence if best else 0,
        "bbox": best.bbox if best else None,
        "det_conf": round(best.det_conf, 3) if best else 0.0,
        "ocr_conf": round(best.ocr_conf, 1) if best else 0.0,
        "raw_text": best.raw_text if best else "",
        "detections": detections,
    }


# =================================================
# OVERLAY
# =================================================
GREEN = (0, 200, 0)
AMBER = (0, 170, 255)
WHITE = (255, 255, 255)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def annotate(frame, detections, extra_lines=()):
    """Draw boxes, plate text and confidence so the demo is visibly working."""
    if frame is None:
        return frame
    canvas = frame

    for detection in detections or []:
        x, y, w, h = detection.bbox
        colour = GREEN if detection.readable else AMBER
        cv2.rectangle(canvas, (x, y), (x + w, y + h), colour, 2)

        label = (f"{detection.plate} {detection.confidence}%"
                 if detection.readable
                 else f"? {detection.raw_text or 'unreadable'}")
        (tw, th), _ = cv2.getTextSize(label, FONT, 0.6, 2)
        top = max(0, y - th - 8)
        cv2.rectangle(canvas, (x, top), (x + tw + 8, top + th + 8), colour, -1)
        cv2.putText(canvas, label, (x + 4, top + th + 2), FONT, 0.6, WHITE, 2)

    for index, line in enumerate(extra_lines):
        cv2.putText(canvas, str(line), (10, 24 + index * 22),
                    FONT, 0.55, (0, 0, 0), 3)
        cv2.putText(canvas, str(line), (10, 24 + index * 22),
                    FONT, 0.55, (255, 255, 0), 1)

    return canvas
