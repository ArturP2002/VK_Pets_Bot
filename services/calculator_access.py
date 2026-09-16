"""Access control for the dose calculator module."""
from __future__ import annotations

from models import User
from services import rbac, subscription_service


def has_calculator_access(user: User) -> bool:
    """Doctors/admins: free. Others: active trial or plan `calculator`."""
    if rbac.is_doctor(user.vk_id):
        return True
    return subscription_service.can_use_feature(user, "dose_calculator")


def check_calculator_access(user: User) -> tuple[bool, str]:
    if has_calculator_access(user):
        return True, "ok"
    return False, "no_access"
