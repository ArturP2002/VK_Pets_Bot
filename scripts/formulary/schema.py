"""SQLite schema for data/formulary.db."""
from __future__ import annotations

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drugs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name_en TEXT NOT NULL,
    canonical_name_ru TEXT NOT NULL DEFAULT '',
    trade_names TEXT NOT NULL DEFAULT '[]',
    pom_note TEXT NOT NULL DEFAULT '',
    formulations TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    use_text TEXT NOT NULL DEFAULT '',
    safety_handling TEXT NOT NULL DEFAULT '',
    contraindications TEXT NOT NULL DEFAULT '',
    adverse_reactions TEXT NOT NULL DEFAULT '',
    drug_interactions TEXT NOT NULL DEFAULT '',
    full_text_en TEXT NOT NULL DEFAULT '',
    full_text_ru TEXT NOT NULL DEFAULT '',
    sources TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_drugs_name_en_norm
    ON drugs (canonical_name_en COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS drug_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_id INTEGER NOT NULL REFERENCES drugs(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_norm TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT '',
    UNIQUE(drug_id, alias_norm)
);

CREATE INDEX IF NOT EXISTS idx_aliases_norm ON drug_aliases(alias_norm);

CREATE TABLE IF NOT EXISTS doses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_id INTEGER NOT NULL REFERENCES drugs(id) ON DELETE CASCADE,
    taxa TEXT NOT NULL DEFAULT 'other',
    species_note TEXT NOT NULL DEFAULT '',
    indication TEXT NOT NULL DEFAULT '',
    route TEXT NOT NULL DEFAULT '',
    dose_min REAL,
    dose_max REAL,
    dose_unit TEXT NOT NULL DEFAULT '',
    frequency TEXT NOT NULL DEFAULT '',
    duration TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_doses_drug ON doses(drug_id);
CREATE INDEX IF NOT EXISTS idx_doses_taxa ON doses(taxa);

CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL DEFAULT 'dosage_hit',
    drug_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_user_created
    ON usage_events(user_id, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS drugs_fts USING fts5(
    alias_norm,
    display_name,
    content='',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS drug_name_cache (
    drug_id INTEGER PRIMARY KEY REFERENCES drugs(id) ON DELETE CASCADE,
    name_ru TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'ai',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_schema(conn) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', '2')"
    )
    conn.commit()


def ensure_schema_extensions(conn) -> None:
    """Apply additive schema changes to existing formulary.db files."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS drug_name_cache (
            drug_id INTEGER PRIMARY KEY REFERENCES drugs(id) ON DELETE CASCADE,
            name_ru TEXT NOT NULL,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT 'ai',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
