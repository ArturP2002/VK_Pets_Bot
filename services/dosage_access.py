"""Access control for the dosage module: trial / plan / free 2-per-24h."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import config
from models import User
from services import subscription_service


@dataclass
class DosageAccess:
    allowed: bool
    reason: str = "ok"
    has_subscription: bool = False
    used_in_window: int = 0
    remaining_free: int = 0
    apply_delay: bool = False


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.FORMULARY_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'dosage_hit',
            drug_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_user_created "
        "ON usage_events(user_id, created_at)"
    )
    conn.commit()
    return conn


def has_dosage_subscription(user: User) -> bool:
    """Unlimited dosage: active trial or plan `dosage`."""
    return subscription_service.can_use_feature(user, "drug_dosage")


def count_usage_last_24h(
    user_id: int,
    *,
    kind: str | None = None,
    db_path: str | Path | None = None,
) -> int:
    """Successful dosage hits (and ask-ai) in the rolling 24h window."""
    since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect(db_path)
    try:
        if kind:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM usage_events "
                "WHERE user_id = ? AND kind = ? AND created_at >= ?",
                (user_id, kind, since),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM usage_events "
                "WHERE user_id = ? AND created_at >= ?",
                (user_id, since),
            ).fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def record_usage(
    user_id: int,
    *,
    kind: str = "dosage_hit",
    drug_id: int | None = None,
    db_path: str | Path | None = None,
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO usage_events(user_id, kind, drug_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                user_id,
                kind,
                drug_id,
                datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def check_dosage_access(user: User) -> DosageAccess:
    """
    Free users: up to FORMULARY_FREE_LIMIT_PER_24H successful hits with delay.
    Subscription/trial: unlimited, no delay.
    """
    if has_dosage_subscription(user):
        return DosageAccess(
            allowed=True,
            reason="ok",
            has_subscription=True,
            used_in_window=0,
            remaining_free=-1,
            apply_delay=False,
        )

    used = count_usage_last_24h(user.vk_id)
    limit = config.FORMULARY_FREE_LIMIT_PER_24H
    remaining = max(0, limit - used)
    if remaining <= 0:
        return DosageAccess(
            allowed=False,
            reason="limit_exceeded",
            has_subscription=False,
            used_in_window=used,
            remaining_free=0,
            apply_delay=False,
        )
    return DosageAccess(
        allowed=True,
        reason="ok",
        has_subscription=False,
        used_in_window=used,
        remaining_free=remaining,
        apply_delay=True,
    )


def can_ask_ai(user: User) -> bool:
    """Ask AI only with remaining free quota or dosage subscription/trial."""
    return check_dosage_access(user).allowed
