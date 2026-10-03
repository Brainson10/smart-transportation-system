"""
SIMULATED SMS. Nothing is sent to a phone: each "message" is appended to
logs/sms_logs.txt so a demo can never text the real numbers stored in the
vehicles table. Swap send_sms() for a provider call to make it real.
"""

import logging
import threading
from datetime import datetime

from config import LOG_DIR, SMS_LOG_FILE

log = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()


def send_sms(phone, message):
    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = (
        f"[{timestamp}] SIMULATED SMS\n"
        f"To: {phone}\n"
        f"Message: {message}\n"
        f"{'-' * 40}\n"
    )
    with _WRITE_LOCK, open(SMS_LOG_FILE, "a") as handle:
        handle.write(entry)
    log.info("simulated SMS to %s logged to %s", phone, SMS_LOG_FILE.name)
    return True
