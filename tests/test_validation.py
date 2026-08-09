from services.validation import validate_email_address, validate_phone


def test_phone():
    ok, norm = validate_phone("89001234567")
    assert ok
    assert norm.startswith("+7")


def test_email():
    ok, norm = validate_email_address("test@example.com")
    assert ok
    assert "@" in norm
