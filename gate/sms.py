from datetime import datetime

from config import LOG_DIR, SMS_LOG_FILE

LOG_DIR.mkdir(exist_ok=True)


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
