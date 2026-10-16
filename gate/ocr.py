"""
Pluggable OCR with REAL confidence.

The old pipeline called pytesseract.image_to_string(), which returns text only,
and then fabricated a confidence from the string length. Both engines here
report the confidence their model actually produced.

Keeping both behind one interface also contains the EasyOCR dependency: if it is
missing or fails to initialise, get_engine() falls back to Tesseract and the
demo keeps working.
"""

import logging
import threading
from dataclasses import dataclass

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

import config

log = logging.getLogger(__name__)

if config.TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD


@dataclass(frozen=True)
class OcrResult:
    text: str
    char_conf: float      # 0..100, from the engine itself
    engine: str
    variant: str = ""
    psm: int = None


# =================================================
# PREPROCESSING VARIANTS
# -------------------------------------------------
# The old code built an adaptive-threshold image and then never used it, while
# OCR received a different, un-binarised crop. Here every variant produced is
# actually fed to the engine and scored.
# =================================================
def _upscale(crop, lines=1):
    height = max(1, crop.shape[0])
    # Each text line needs OCR_TARGET_HEIGHT pixels, so a two-line plate needs twice.
    scale = max(2.0, config.OCR_TARGET_HEIGHT * lines / height)
    return cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def _pad(img):
    """Tesseract needs a quiet margin around the glyphs."""
    return cv2.copyMakeBorder(img, config.OCR_PAD, config.OCR_PAD,
                              config.OCR_PAD, config.OCR_PAD,
                              cv2.BORDER_CONSTANT, value=255)


def deskew(gray):
    """Straighten a tilted plate; skip implausible angles to avoid 90-degree flips."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    points = cv2.findNonZero(binary)
    if points is None or len(points) < 10:
        return gray

    angle = cv2.minAreaRect(points)[-1]
    if angle > 45:
        angle -= 90
    elif angle < -45:
        angle += 90
    if abs(angle) < 1.5 or abs(angle) > 20:
        return gray

    h, w = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, matrix, (w, h),
                          flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def is_two_line(crop):
    """Square-ish crops are two-line plates (common on Indian two-wheelers)."""
    if crop is None or crop.size == 0:
        return False
    height, width = crop.shape[:2]
    return height > 0 and width / height < config.TWO_LINE_ASPECT


def split_rows(img):
    """
    Row index that best separates the two text lines of a two-line plate.

    Horizontal projection of dark ink, searched only in the middle band so the
    plate border or a bolt hole near the edge is never chosen.
    """
    height = img.shape[0]
    ink = (255 - img).astype(np.float32).sum(axis=1) if img.ndim == 2 else \
        (255 - cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)).astype(np.float32).sum(axis=1)
    low, high = int(height * 0.3), int(height * 0.7)
    if high <= low:
        return height // 2
    return low + int(np.argmin(ink[low:high]))


def join_two_lines(img):
    """
    Put the bottom line to the right of the top line: a two-line plate becomes
    the single text line that every OCR mode (and the plate regex) expects.
    """
    cut = split_rows(img)
    top, bottom = img[:cut], img[cut:]
    if top.size == 0 or bottom.size == 0:
        return img
    height = max(top.shape[0], bottom.shape[0])

    def fit(part):
        scale = height / part.shape[0]
        return cv2.resize(part, (max(1, int(part.shape[1] * scale)), height),
                          interpolation=cv2.INTER_CUBIC)

    gap = np.full((height, max(4, height // 3)) + img.shape[2:], 255, dtype=img.dtype)
    return np.hstack([fit(top), gap, fit(bottom)])


def _gamma(gray, gamma):
    table = np.array([((i / 255.0) ** gamma) * 255 for i in range(256)], dtype=np.uint8)
    return cv2.LUT(gray, table)


def preprocess_variants(crop):
    """Ordered cheap -> expensive, so the caller can early-exit on a good read."""
    if crop is None or crop.size == 0:
        return []

    two_line = is_two_line(crop)
    big = _upscale(crop, lines=2 if two_line else 1)
    gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY) if big.ndim == 3 else big
    gray = cv2.bilateralFilter(gray, 11, 17, 17)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)

    blurred = cv2.GaussianBlur(clahe, (3, 3), 0)
    _, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    if otsu.mean() < 127:                      # keep glyphs dark on light
        otsu = cv2.bitwise_not(otsu)

    adaptive = cv2.adaptiveThreshold(clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 25, 15)
    adaptive = cv2.morphologyEx(adaptive, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    skewed = deskew(clahe)
    _, deskewed = cv2.threshold(skewed, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    if deskewed.mean() < 127:
        deskewed = cv2.bitwise_not(deskewed)

    variants = [
        ("gray", _pad(gray)),
        ("clahe", _pad(clahe)),
        ("otsu", _pad(otsu)),
        ("adaptive", _pad(adaptive)),
        ("deskew", _pad(deskewed)),
    ]

    # Night / underexposed plates: lift the shadows before thresholding.
    if gray.mean() < 80:
        bright = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(_gamma(gray, 0.5))
        variants.insert(1, ("gamma", _pad(bright)))

    # Two-line plates: the joined single line goes first, because every other
    # variant asks the engine to read two lines as one.
    if two_line:
        variants.insert(0, ("twoline", _pad(join_two_lines(otsu))))
        variants.insert(1, ("twoline-clahe", _pad(join_two_lines(clahe))))

    return variants


# =================================================
# TESSERACT
# =================================================
def _aggregate(data):
    """
    Length-weighted mean of Tesseract's own per-word confidences.

    This is the signal image_to_string() throws away. conf is -1 for empty
    boxes, which must be skipped or the average is meaningless.
    """
    texts, total_conf, total_len = [], 0.0, 0
    for text, conf in zip(data.get("text", []), data.get("conf", [])):
        text = (text or "").strip()
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            continue
        if not text or conf < 0:
            continue
        texts.append(text)
        total_conf += conf * len(text)
        total_len += len(text)
    if not total_len:
        return "", 0.0
    return "".join(texts), total_conf / total_len


class TesseractEngine:
    name = "tesseract"

    def __init__(self, psms=None, whitelist=None):
        self.psms = tuple(psms or config.OCR_PSMS)
        self.whitelist = whitelist or config.OCR_CHAR_WHITELIST

    def read(self, img, variant=""):
        results = []
        for psm in self.psms:
            # NOTE: with --oem 3 (LSTM) Tesseract 5 only partially honours
            # tessedit_char_whitelist, so plate_utils remains the real filter.
            cfg = (f"--oem 3 --psm {psm} "
                   f"-c tessedit_char_whitelist={self.whitelist}")
            try:
                data = pytesseract.image_to_data(img, config=cfg, output_type=Output.DICT)
            except Exception as exc:
                log.warning("tesseract failed (psm=%s): %s", psm, exc)
                continue
            text, conf = _aggregate(data)
            if text:
                results.append(OcrResult(text, conf, self.name, variant, psm))
        return results


# =================================================
# EASYOCR
# =================================================
def order_ocr_boxes(raw):
    """
    EasyOCR results in reading order: lines top-to-bottom, then left-to-right.

    EasyOCR does not guarantee this, and a two-line plate joined bottom-first
    ("1234" + "MN05AB") never parses. Boxes whose vertical centres are within
    half a text height of each other are the same line.
    """
    items = []
    for item in raw or []:
        if len(item) < 3:
            continue
        points = np.asarray(item[0], dtype=np.float32).reshape(-1, 2)
        ys, xs = points[:, 1], points[:, 0]
        items.append((float(ys.mean()), float(xs.min()), float(ys.max() - ys.min()), item))
    if not items:
        return []

    items.sort(key=lambda t: t[0])
    heights = sorted(t[2] for t in items)
    tolerance = max(1.0, heights[len(heights) // 2] * 0.5)

    lines, current, anchor = [], [], None
    for entry in items:
        if anchor is None or abs(entry[0] - anchor) <= tolerance:
            current.append(entry)
            anchor = entry[0] if anchor is None else anchor
        else:
            lines.append(current)
            current, anchor = [entry], entry[0]
    lines.append(current)

    return [entry[3] for line in lines for entry in sorted(line, key=lambda t: t[1])]


class EasyOcrEngine:
    name = "easyocr"

    @staticmethod
    def _resolve_gpu(setting):
        """"auto" -> use an accelerator when one exists; else honour the flag."""
        if isinstance(setting, bool):
            return setting
        text = str(setting).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        try:
            import torch
            return bool(torch.cuda.is_available() or torch.backends.mps.is_available())
        except Exception:
            return False

    def __init__(self, gpu=None):
        import easyocr   # local import: optional dependency
        self._gpu = self._resolve_gpu(config.EASYOCR_GPU if gpu is None else gpu)
        self._reader = easyocr.Reader(["en"], gpu=self._gpu, verbose=False)
        # One Reader serves every gate's worker thread and is not thread-safe.
        self._lock = threading.Lock()

    def read(self, img, variant=""):
        try:
            with self._lock:
                raw = self._reader.readtext(img, allowlist=config.OCR_CHAR_WHITELIST)
        except Exception as exc:
            log.warning("easyocr failed: %s", exc)
            return []

        results = []
        for item in order_ocr_boxes(raw):
            text, conf = item[1], item[2]
            text = (text or "").strip()
            if text:
                results.append(OcrResult(text, float(conf) * 100.0, self.name, variant))

        # A plate split across boxes ("MN05" + "AB1234") only parses when joined.
        if len(results) > 1:
            joined = "".join(r.text for r in results)
            mean = sum(r.char_conf for r in results) / len(results)
            results.append(OcrResult(joined, mean, self.name, variant))
        return results


# =================================================
# ENGINE SELECTION
# =================================================
_ENGINE_CACHE = {}


def get_engine(name=None):
    """Cached engine. Falls back to Tesseract rather than breaking the demo."""
    name = (name or config.OCR_ENGINE).strip().lower()
    if name in _ENGINE_CACHE:
        return _ENGINE_CACHE[name]

    engine = None
    if name == "easyocr":
        try:
            engine = EasyOcrEngine()
            log.info("OCR engine: easyocr (gpu=%s)", engine._gpu)
        except Exception as exc:
            log.warning("easyocr unavailable (%s); falling back to tesseract", exc)
    if engine is None:
        engine = TesseractEngine()
        log.info("OCR engine: tesseract (psms=%s)", engine.psms)

    _ENGINE_CACHE[name] = engine
    return engine
