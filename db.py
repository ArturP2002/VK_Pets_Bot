"""Database connection and initialization (SQLite or PostgreSQL)."""
from __future__ import annotations

import logging

from peewee import SqliteDatabase

import config

logger = logging.getLogger(__name__)


def _make_database():
    """Prefer DATABASE_URL (Postgres); fall back to DB_PATH SQLite."""
    url = (getattr(config, "DATABASE_URL", "") or "").strip()
    if url:
        from playhouse.db_url import connect

        db = connect(url)
        logger.info("Using database URL backend: %s", db.__class__.__name__)
        return db
    return SqliteDatabase(
        config.DB_PATH,
        pragmas={
            "journal_mode": "wal",
            "cache_size": -1024 * 64,
            "foreign_keys": 1,
            "ignore_check_constraints": 0,
            "synchronous": 0,
        },
    )


database = _make_database()


def is_postgres() -> bool:
    return database.__class__.__name__ == "PostgresqlDatabase"


def init_db():
    """Connect and create tables."""
    from migrations.upgrade import upgrade_schema

    database.connect(reuse_if_open=True)
    upgrade_schema()
    target = (getattr(config, "DATABASE_URL", "") or "").strip() or config.DB_PATH
    logger.info("Database initialized (%s)", target)


def close_db():
    if not database.is_closed():
        database.close()
