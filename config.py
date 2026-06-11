from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

FRONTEND_DIR = BASE_DIR / "frontend"
TEMPLATE_DIR = FRONTEND_DIR / "templates"
STATIC_DIR = FRONTEND_DIR / "static"

VEHICLE_DB = BASE_DIR / "database.db"
AUTHORITY_DB = BASE_DIR / "authority" / "authority.db"
AUTHORITY_MODEL = BASE_DIR / "authority" / "model.pkl"
GATE_DB = BASE_DIR / "gate" / "gate_security.db"
LOG_DIR = BASE_DIR / "logs"
SMS_LOG_FILE = LOG_DIR / "sms_logs.txt"
