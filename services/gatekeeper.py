from models import User
from services.legal_service import has_all_consents, needs_clinic_ack
from services.user_service import profile_complete


class GatekeeperResult:
    def __init__(self, allowed: bool, reason: str | None = None):
        self.allowed = allowed
        self.reason = reason


def check_access(user: User, require_clinic_ack: bool = True) -> GatekeeperResult:
    if not has_all_consents(user):
        return GatekeeperResult(False, "legal")
    if not profile_complete(user):
        return GatekeeperResult(False, "registration")
    if require_clinic_ack and needs_clinic_ack(user):
        return GatekeeperResult(False, "clinic_ack")
    return GatekeeperResult(True)
