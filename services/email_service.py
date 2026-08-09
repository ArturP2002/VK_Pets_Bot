import logging
import smtplib
from email.mime.text import MIMEText

import config

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_FROM)


def send_email(to_address: str, subject: str, body: str) -> bool:
    if not to_address or not is_configured():
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = config.SMTP_FROM
    msg["To"] = to_address
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            if config.SMTP_USER:
                server.starttls()
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.SMTP_FROM, [to_address], msg.as_string())
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_address)
        return False
