"""Copy main bot data from SQLite (DB_PATH) into PostgreSQL (DATABASE_URL).

Usage:
  DATABASE_URL=postgresql://user:pass@localhost:5432/exocare \\
    python -m migrations.migrate_sqlite_to_postgres

  # or:
  python main.py migrate-pg --sqlite data/VK_Pets_DB.db

Formulary (data/formulary.db) stays on SQLite — FTS5 / build pipeline.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from peewee import BooleanField, DateTimeField, ForeignKeyField, Model, SqliteDatabase
from playhouse.db_url import connect

import config
from models import ALL_MODELS

logger = logging.getLogger(__name__)


def _field_names(model: type[Model]) -> list[str]:
    return [f.name for f in model._meta.sorted_fields]


def _coerce_value(model: type[Model], key: str, value: Any) -> Any:
    if value is None:
        return None
    field = model._meta.fields.get(key)
    if field is None:
        return value
    if isinstance(field, BooleanField):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "t", "yes", "y"}
        return bool(value)
    if isinstance(field, DateTimeField) and isinstance(value, str):
        text = value.strip()
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return value
    if isinstance(field, ForeignKeyField) and key.endswith("_id"):
        return value
    return value


def _row_to_insert(model: type[Model], row: dict[str, Any]) -> dict[str, Any]:
    """Map Peewee .dicts() row to insert kwargs (FK columns as *_id)."""
    out: dict[str, Any] = {}
    for field in model._meta.sorted_fields:
        name = field.name
        if isinstance(field, ForeignKeyField):
            raw_key = field.column_name
            if raw_key in row:
                out[name] = _coerce_value(model, name, row[raw_key])
            elif name in row:
                out[name] = _coerce_value(model, name, row[name])
            elif f"{name}_id" in row:
                out[name] = row[f"{name}_id"]
            continue
        if name in row:
            out[name] = _coerce_value(model, name, row[name])
        elif field.column_name in row:
            out[name] = _coerce_value(model, name, row[field.column_name])
    return out


def _reset_postgres_sequences(dst) -> None:
    for model in ALL_MODELS:
        table = model._meta.table_name
        pk = model._meta.primary_key
        if pk is None or not getattr(pk, "auto_increment", False):
            continue
        col = pk.column_name
        dst.execute_sql(
            f"""
            SELECT setval(
                pg_get_serial_sequence(%s, %s),
                COALESCE((SELECT MAX("{col}") FROM "{table}"), 1),
                true
            )
            """,
            (table, col),
        )


def migrate(
    *,
    sqlite_path: str | Path,
    database_url: str,
    truncate: bool = True,
) -> dict[str, int]:
    sqlite_path = Path(sqlite_path)
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {sqlite_path}")
    if not database_url.strip():
        raise ValueError("DATABASE_URL is required")

    src = SqliteDatabase(str(sqlite_path))
    dst = connect(database_url.strip())

    src.bind(ALL_MODELS)
    src.connect(reuse_if_open=True)

    snapshots: dict[type[Model], list[dict[str, Any]]] = {}
    for model in ALL_MODELS:
        if model.table_exists():
            snapshots[model] = list(model.select().dicts())
        else:
            snapshots[model] = []
            logger.warning("Source table missing: %s", model._meta.table_name)
    src.close()

    dst.bind(ALL_MODELS)
    dst.connect(reuse_if_open=True)
    dst.create_tables(ALL_MODELS, safe=True)

    # Apply additive upgrades against the destination connection.
    import db as db_module

    prev = db_module.database
    db_module.database = dst
    try:
        from migrations.upgrade import upgrade_schema

        upgrade_schema()
    finally:
        db_module.database = prev

    counts: dict[str, int] = {}
    # Disable FK checks for the load (Postgres); SQLite N/A here.
    if dst.__class__.__name__ == "PostgresqlDatabase":
        dst.execute_sql("SET session_replication_role = 'replica'")

    try:
        with dst.atomic():
            if truncate:
                for model in reversed(ALL_MODELS):
                    model.delete().execute()

            for model in ALL_MODELS:
                rows = snapshots.get(model) or []
                inserted = 0
                for row in rows:
                    payload = _row_to_insert(model, row)
# Also fix insert to use **payload consistently
                    model.insert(**payload).execute()
                    inserted += 1
                counts[model._meta.table_name] = inserted
                logger.info("Migrated %s → %s rows", model._meta.table_name, inserted)

            _reset_postgres_sequences(dst)
    finally:
        if dst.__class__.__name__ == "PostgresqlDatabase":
            try:
                dst.execute_sql("SET session_replication_role = 'origin'")
            except Exception:
                pass

    dst.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Migrate bot SQLite → PostgreSQL")
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=Path(config.DB_PATH),
        help="Source SQLite file (default: config.DB_PATH)",
    )
    parser.add_argument(
        "--database-url",
        default=config.DATABASE_URL,
        help="Target Postgres URL (default: config.DATABASE_URL)",
    )
    parser.add_argument(
        "--no-truncate",
        action="store_true",
        help="Do not wipe Postgres tables before insert",
    )
    args = parser.parse_args(argv)
    counts = migrate(
        sqlite_path=args.sqlite,
        database_url=args.database_url,
        truncate=not args.no_truncate,
    )
    total = sum(counts.values())
    print(f"Migrated {total} rows across {len(counts)} tables → PostgreSQL")
    for table, n in counts.items():
        print(f"  {table}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
