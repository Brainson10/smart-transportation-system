import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(os.path.dirname(BASE_DIR), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

SMS_LOG_FILE = os.path.join(LOG_DIR, "sms_logs.txt")


def send_sms(phone, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log = (
        f"[{timestamp}]\n"
        f"To: {phone}\n"
        f"Message: {message}\n"
        f"{'-'*40}\n"
    )

    # Write to file
    with open(SMS_LOG_FILE, "a") as f:
        f.write(log)

    # Also print to console (for demo)
    print("📨 SMS SENT")
    print(log)
