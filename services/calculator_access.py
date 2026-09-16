"""Access control for the dose calculator module — free for all users."""
from __future__ import annotations

from models import User


def has_calculator_access(user: User) -> bool:
    """Calculator is free for everyone."""
    return True


def check_calculator_access(user: User) -> tuple[bool, str]:
    return True, "ok"
