import logging
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

FRONTEND_DIR = BASE_DIR / "frontend"
TEMPLATE_DIR = FRONTEND_DIR / "templates"
STATIC_DIR = FRONTEND_DIR / "static"

# Runtime data (SQLite DBs) lives under DATA_DIR. It defaults to the project
# folder, so existing paths are unchanged; tests and fresh environments can point
# it somewhere else. Code assets (templates, model files) stay under BASE_DIR.
DATA_DIR = Path(os.environ.get("SMART_TRANSPORT_DATA_DIR") or BASE_DIR)

VEHICLE_DB = DATA_DIR / "database.db"
AUTHORITY_DB = DATA_DIR / "authority" / "authority.db"
AUTHORITY_MODEL = BASE_DIR / "authority" / "model.pkl"
BACKUP_DIR = DATA_DIR / "backups"
INSTANCE_DIR = DATA_DIR / "instance"
LOG_DIR = BASE_DIR / "logs"
SMS_LOG_FILE = LOG_DIR / "sms_logs.txt"

# =================================================
# AUTH
# -------------------------------------------------
# SECRET_KEY signs session cookies. If unset, backend/auth.py generates one and
# persists it to INSTANCE_DIR/secret_key so sessions survive restarts.
# GATE_PASSWORD is the shared gate-operator password; the demo default is kept.
# =================================================
SECRET_KEY = os.environ.get("SECRET_KEY") or None
GATE_PASSWORD = os.environ.get("GATE_PASSWORD") or "gate123"
SESSION_LIFETIME_HOURS = 8
# Set to 1 when serving over HTTPS so the session cookie is never sent in clear.
SESSION_COOKIE_SECURE = (os.environ.get("SESSION_COOKIE_SECURE") or "").lower() in ("1", "true", "yes")
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 300

# Off by default: Flask's debug mode enables the Werkzeug debugger, which can
# execute arbitrary code from the browser. Use FLASK_DEBUG=1 only on your machine.
DEBUG = (os.environ.get("FLASK_DEBUG") or "").lower() in ("1", "true", "yes")
HOST = os.environ.get("HOST") or "127.0.0.1"
PORT = int(os.environ.get("PORT") or 5000)


# =================================================
# LOGGING / CV CONFIG HELPERS
# =================================================
def _env_str(name, default):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_int(name, default):
    try:
        return int(_env_str(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(_env_str(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# =================================================
# CAMERA / CCTV SOURCE
# -------------------------------------------------
# The simulation runs on the laptop webcam (index 0). A real CCTV deployment
# only needs ANPR_CAMERA_SOURCE set to an rtsp:// URL -- no code change.
# A video file path is also accepted, which makes demos repeatable.
# =================================================
CAMERA_SOURCE = _env_str("ANPR_CAMERA_SOURCE", "0")
CAMERA_WIDTH = _env_int("ANPR_CAMERA_WIDTH", 1280)
CAMERA_HEIGHT = _env_int("ANPR_CAMERA_HEIGHT", 720)
CAMERA_FPS = _env_int("ANPR_CAMERA_FPS", 30)
# BUFFERSIZE=1 keeps RTSP near real-time; without it OpenCV serves stale frames.
CAMERA_BUFFERSIZE = _env_int("ANPR_CAMERA_BUFFERSIZE", 1)
CAMERA_LOOP_VIDEO = _env_bool("ANPR_CAMERA_LOOP_VIDEO", True)
CAMERA_RECONNECT_DELAY = _env_float("ANPR_CAMERA_RECONNECT_DELAY", 1.0)
CAMERA_RECONNECT_MAX_DELAY = _env_float("ANPR_CAMERA_RECONNECT_MAX_DELAY", 10.0)


def resolve_camera_source(source=None):
    """Webcam index as int, file path / rtsp URL left as str."""
    source = CAMERA_SOURCE if source is None else source
    if isinstance(source, int):
        return source
    text = str(source).strip()
    return int(text) if text.isdigit() else text


# =================================================
# MJPEG STREAM
# =================================================
STREAM_FPS = _env_int("ANPR_STREAM_FPS", 15)
STREAM_JPEG_QUALITY = _env_int("ANPR_STREAM_JPEG_QUALITY", 80)

# =================================================
# YOLO PLATE DETECTOR
# =================================================
YOLO_MODEL_PATH = BASE_DIR / "gate" / "models" / "license_plate_detector.pt"
# 0.25 is safe (was 0.35): OCR validity + vote accumulation are the real gate now.
YOLO_CONF = _env_float("ANPR_YOLO_CONF", 0.25)
YOLO_IOU = _env_float("ANPR_YOLO_IOU", 0.5)
YOLO_IMGSZ = _env_int("ANPR_YOLO_IMGSZ", 640)
YOLO_MAX_DET = _env_int("ANPR_YOLO_MAX_DET", 5)
# yolov8n at 640 is ~40ms on Apple-silicon CPU, faster than DETECT_INTERVAL needs,
# and CPU avoids MPS warm-up cost. Set ANPR_DEVICE=mps to compare.
YOLO_DEVICE = _env_str("ANPR_DEVICE", "cpu")
# Plate detectors crop tight and clip outer glyphs; pad before OCR.
BOX_PAD_RATIO = _env_float("ANPR_BOX_PAD_RATIO", 0.04)
MIN_PLATE_AREA = _env_int("ANPR_MIN_PLATE_AREA", 900)
MIN_PLATE_WIDTH = _env_int("ANPR_MIN_PLATE_WIDTH", 60)

# =================================================
# DETECTION TRIGGER
# -------------------------------------------------
# Inference runs on a fixed interval regardless of motion, so a vehicle that
# STOPS at the gate is still read. Motion is only a cost-saver, and is off by
# default: MOTION_FORCE_INTERVAL guarantees inference even on a still scene.
# =================================================
DETECT_INTERVAL = _env_float("ANPR_DETECT_INTERVAL", 0.25)
MOTION_ENABLED = _env_bool("ANPR_MOTION_ENABLED", False)
MOTION_THRESHOLD = _env_int("ANPR_MOTION_THRESHOLD", 1500)
MOTION_FORCE_INTERVAL = _env_float("ANPR_MOTION_FORCE_INTERVAL", 1.5)

# =================================================
# OCR
# =================================================
OCR_ENGINE = _env_str("ANPR_OCR_ENGINE", "easyocr")   # easyocr | tesseract
OCR_PSMS = (7, 8, 13)
OCR_CHAR_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
OCR_TARGET_HEIGHT = _env_int("ANPR_OCR_TARGET_HEIGHT", 64)
OCR_MIN_CHAR_CONF = _env_float("ANPR_OCR_MIN_CHAR_CONF", 45.0)
OCR_PAD = _env_int("ANPR_OCR_PAD", 10)
OCR_EARLY_EXIT_CONF = _env_float("ANPR_OCR_EARLY_EXIT_CONF", 85.0)
TESSERACT_CMD = os.environ.get("TESSERACT_CMD")
# "auto" picks MPS/CUDA when present (~2.7x faster than CPU on Apple silicon),
# otherwise CPU. Force with ANPR_EASYOCR_GPU=0 or 1.
EASYOCR_GPU = _env_str("ANPR_EASYOCR_GPU", "auto")

# =================================================
# CONFIDENCE FUSION
# -------------------------------------------------
# Replaces the old fabricated `min(70 + len(plate) * 3, 95)`, which was always
# exactly 95 and made the MANUAL CHECK branch unreachable.
# =================================================
CONF_W_YOLO = _env_float("ANPR_CONF_W_YOLO", 0.30)
CONF_W_OCR = _env_float("ANPR_CONF_W_OCR", 0.50)
CONF_W_FORMAT = _env_float("ANPR_CONF_W_FORMAT", 0.20)
CONF_SUBSTITUTION_PENALTY = _env_float("ANPR_CONF_SUB_PENALTY", 0.05)
MANUAL_CHECK_CONFIDENCE = _env_int("ANPR_MANUAL_CHECK_CONFIDENCE", 70)

# =================================================
# TEMPORAL VOTING
# =================================================
VOTE_WINDOW = _env_float("ANPR_VOTE_WINDOW", 6.0)
VOTE_THRESHOLD = _env_float("ANPR_VOTE_THRESHOLD", 2.0)
VOTE_MIN_SIGHTINGS = _env_int("ANPR_VOTE_MIN_SIGHTINGS", 2)
VOTE_MERGE_DISTANCE = _env_int("ANPR_VOTE_MERGE_DISTANCE", 1)

# =================================================
# EVIDENCE + LOGGING
# =================================================
SAVE_DETECTION_CROPS = _env_bool("ANPR_SAVE_CROPS", True)
DETECTION_DIR = LOG_DIR / "detections"
LOG_LEVEL = _env_str("ANPR_LOG_LEVEL", "INFO")


def setup_logging():
    """Idempotent logging setup; replaces print() debugging on the hot path."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    else:
        root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
