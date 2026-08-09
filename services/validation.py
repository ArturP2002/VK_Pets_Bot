import re

from email_validator import validate_email, EmailNotValidError


PHONE_RE = re.compile(r"^\+?7?\d{10,11}$")


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return f"+{digits}"
    return None


def validate_phone(raw: str) -> tuple[bool, str | None]:
    normalized = normalize_phone(raw)
    if not normalized:
        return False, None
    return True, normalized


def validate_email_address(raw: str) -> tuple[bool, str | None]:
    try:
        valid = validate_email(raw.strip(), check_deliverability=False)
        return True, valid.normalized
    except EmailNotValidError:
        return False, None
