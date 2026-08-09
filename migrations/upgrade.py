"""Lightweight schema upgrades for SQLite (add missing columns/tables)."""
import logging

from db import database
from models import ALL_MODELS

logger = logging.getLogger(__name__)


def _columns(table: str) -> set[str]:
    cursor = database.execute_sql(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def upgrade_schema():
    database.create_tables(ALL_MODELS, safe=True)
    alters = [
        ("users", "assigned_doctor_vk_id", "INTEGER"),
        ("subscriptions", "parent_subscription_id", "INTEGER"),
        ("subscriptions", "auto_renew", "INTEGER DEFAULT 0"),
        ("subscriptions", "recurrent_agreement_id", "INTEGER"),
        ("payments", "is_recurrent_charge", "INTEGER DEFAULT 0"),
    ]
    for table, col, col_type in alters:
        if table not in database.get_tables():
            continue
        if col not in _columns(table):
            database.execute_sql(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            logger.info("Added column %s.%s", table, col)
