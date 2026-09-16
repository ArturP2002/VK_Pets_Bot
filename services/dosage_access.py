"""Access control for the dosage module — free for all users."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import config
from models import User


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
    """Module is free — always treated as unlocked."""
    return True


def count_usage_last_24h(
    user_id: int,
    *,
    kind: str | None = None,
    db_path: str | Path | None = None,
) -> int:
    """Successful dosage hits (and ask-ai) in the rolling 24h window (analytics)."""
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
    """Dosage formulary is free for everyone: unlimited, no delay."""
    return DosageAccess(
        allowed=True,
        reason="free",
        has_subscription=True,
        used_in_window=0,
        remaining_free=-1,
        apply_delay=False,
    )


def can_ask_ai(user: User) -> bool:
    """Ask AI is included in the free dosage module."""
    return True
