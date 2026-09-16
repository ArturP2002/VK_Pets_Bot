"""Lightweight schema upgrades (SQLite or PostgreSQL)."""
from __future__ import annotations

import logging

from models import ALL_MODELS

logger = logging.getLogger(__name__)


def _db():
    import db as db_module

    return db_module.database


def _is_postgres() -> bool:
    return _db().__class__.__name__ == "PostgresqlDatabase"


def _columns(table: str) -> set[str]:
    database = _db()
    if _is_postgres():
        cursor = database.execute_sql(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
            """,
            (table,),
        )
        return {row[0] for row in cursor.fetchall()}
    cursor = database.execute_sql(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _bool_sql() -> str:
    return "BOOLEAN DEFAULT FALSE" if _is_postgres() else "INTEGER DEFAULT 0"


def upgrade_schema():
    database = _db()
    database.create_tables(ALL_MODELS, safe=True)
    bool_sql = _bool_sql()
    alters = [
        ("users", "assigned_doctor_vk_id", "INTEGER"),
        ("subscriptions", "parent_subscription_id", "INTEGER"),
        ("subscriptions", "auto_renew", bool_sql),
        ("subscriptions", "recurrent_agreement_id", "INTEGER"),
        ("payments", "is_recurrent_charge", bool_sql),
    ]
    for table, col, col_type in alters:
        if table not in database.get_tables():
            continue
        if col not in _columns(table):
            database.execute_sql(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {col_type}')
            logger.info("Added column %s.%s", table, col)
