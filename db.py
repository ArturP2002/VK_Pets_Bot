"""Database connection and initialization."""
import logging

from peewee import SqliteDatabase

import config

logger = logging.getLogger(__name__)

database = SqliteDatabase(
    config.DB_PATH,
    pragmas={
        "journal_mode": "wal",
        "cache_size": -1024 * 64,
        "foreign_keys": 1,
        "ignore_check_constraints": 0,
        "synchronous": 0,
    },
)


def init_db():
    """Connect and create tables."""
    from models import ALL_MODELS
    from migrations.upgrade import upgrade_schema

    database.connect(reuse_if_open=True)
    upgrade_schema()
    logger.info("Database initialized at %s", config.DB_PATH)


def close_db():
    if not database.is_closed():
        database.close()
