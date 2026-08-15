"""Fuzzy + FTS5 drug name search over formulary.db."""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

import config
from scripts.formulary.common import normalize_name, transliterate_ru

logger = logging.getLogger(__name__)


@dataclass
class DrugHit:
    drug_id: int
    canonical_name_en: str
    canonical_name_ru: str
    matched_alias: str
    score: float
    sources: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        if self.canonical_name_ru and self.canonical_name_en:
            return f"{self.canonical_name_ru} ({self.canonical_name_en})"
        return self.canonical_name_ru or self.canonical_name_en or self.matched_alias


@dataclass
class DrugRecord:
    id: int
    canonical_name_en: str
    canonical_name_ru: str
    trade_names: list[str]
    pom_note: str
    formulations: str
    action: str
    use: str
    safety_handling: str
    contraindications: str
    adverse_reactions: str
    drug_interactions: str
    full_text_en: str
    full_text_ru: str
    sources: list[str]
    doses: list[dict[str, Any]]
    aliases: list[str]


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.FORMULARY_DB)
    if not path.exists():
        raise FileNotFoundError(
            f"Formulary DB not found at {path}. Run: python -m scripts.formulary"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def get_drug(drug_id: int, db_path: str | Path | None = None) -> DrugRecord | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM drugs WHERE id = ?", (drug_id,)).fetchone()
        if not row:
            return None
        doses = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM doses WHERE drug_id = ? ORDER BY taxa, source, id",
                (drug_id,),
            ).fetchall()
        ]
        aliases = [
            r["alias"]
            for r in conn.execute(
                "SELECT alias FROM drug_aliases WHERE drug_id = ? ORDER BY id",
                (drug_id,),
            ).fetchall()
        ]
        return DrugRecord(
            id=row["id"],
            canonical_name_en=row["canonical_name_en"],
            canonical_name_ru=row["canonical_name_ru"],
            trade_names=json.loads(row["trade_names"] or "[]"),
            pom_note=row["pom_note"],
            formulations=row["formulations"],
            action=row["action"],
            use=row["use_text"],
            safety_handling=row["safety_handling"],
            contraindications=row["contraindications"],
            adverse_reactions=row["adverse_reactions"],
            drug_interactions=row["drug_interactions"],
            full_text_en=row["full_text_en"],
            full_text_ru=row["full_text_ru"],
            sources=json.loads(row["sources"] or "[]"),
            doses=doses,
            aliases=aliases,
        )
    finally:
        conn.close()


def _fts_candidates(conn: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    tokens = [t for t in normalize_name(query).split() if len(t) > 1]
    if not tokens:
        return []
    # Prefix match each token
    match = " AND ".join(f'"{t}"*' for t in tokens)
    try:
        return conn.execute(
            """
            SELECT a.drug_id, a.alias, a.alias_norm,
                   d.canonical_name_en, d.canonical_name_ru, d.sources
            FROM drugs_fts f
            JOIN drug_aliases a ON a.id = f.rowid
            JOIN drugs d ON d.id = a.drug_id
            WHERE drugs_fts MATCH ?
            LIMIT ?
            """,
            (match, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _load_alias_corpus(conn: sqlite3.Connection) -> list[tuple[int, str, str, str, str, str]]:
    """(drug_id, alias, alias_norm, name_en, name_ru, sources_json)."""
    return [
        (
            r["drug_id"],
            r["alias"],
            r["alias_norm"],
            r["canonical_name_en"],
            r["canonical_name_ru"],
            r["sources"],
        )
        for r in conn.execute(
            """
            SELECT a.drug_id, a.alias, a.alias_norm,
                   d.canonical_name_en, d.canonical_name_ru, d.sources
            FROM drug_aliases a
            JOIN drugs d ON d.id = a.drug_id
            """
        ).fetchall()
    ]


def search_drugs(
    query: str,
    *,
    limit: int = 5,
    min_score: float | None = None,
    db_path: str | Path | None = None,
) -> list[DrugHit]:
    """
    Hybrid search: exact/normalized alias → FTS5 → RapidFuzz ranking.
    Scores are 0..100. Default threshold from config.FORMULARY_SEARCH_MIN_SCORE.
    """
    q = (query or "").strip()
    if not q:
        return []
    threshold = (
        config.FORMULARY_SEARCH_MIN_SCORE if min_score is None else float(min_score)
    )
    q_norm = normalize_name(q)
    q_tr = transliterate_ru(q)

    conn = _connect(db_path)
    try:
        corpus = _load_alias_corpus(conn)
        if not corpus:
            return []

        # Exact / substring prefilter
        exact_ids: dict[int, tuple[float, str]] = {}
        for drug_id, alias, alias_norm, *_rest in corpus:
            if alias_norm == q_norm or alias_norm == q_tr:
                exact_ids[drug_id] = (100.0, alias)
            elif q_norm and (q_norm in alias_norm or alias_norm in q_norm):
                exact_ids.setdefault(drug_id, (92.0, alias))

        fts_rows = _fts_candidates(conn, q, limit=50)
        for row in fts_rows:
            exact_ids.setdefault(row["drug_id"], (88.0, row["alias"]))

        # RapidFuzz over alias_norm
        choices = {i: row[2] for i, row in enumerate(corpus)}
        fuzzy = process.extract(
            q_norm or q_tr,
            choices,
            scorer=fuzz.WRatio,
            limit=max(limit * 8, 20),
        )
        # Also try transliteration query
        if q_tr and q_tr != q_norm:
            fuzzy += process.extract(
                q_tr,
                choices,
                scorer=fuzz.WRatio,
                limit=max(limit * 4, 10),
            )

        best: dict[int, DrugHit] = {}

        def consider(drug_id: int, score: float, alias: str, meta_idx: int | None = None) -> None:
            if score < threshold:
                return
            if meta_idx is not None:
                _, _, _, name_en, name_ru, sources_json = corpus[meta_idx]
            else:
                row = next((c for c in corpus if c[0] == drug_id), None)
                if not row:
                    return
                _, _, _, name_en, name_ru, sources_json = row
            hit = DrugHit(
                drug_id=drug_id,
                canonical_name_en=name_en,
                canonical_name_ru=name_ru,
                matched_alias=alias,
                score=float(score),
                sources=json.loads(sources_json or "[]"),
            )
            prev = best.get(drug_id)
            if not prev or hit.score > prev.score:
                best[drug_id] = hit

        for drug_id, (score, alias) in exact_ids.items():
            consider(drug_id, score, alias)

        for alias_norm, score, idx in fuzzy:
            drug_id, alias, *_ = corpus[idx]
            consider(drug_id, float(score), alias, meta_idx=idx)

        hits = sorted(best.values(), key=lambda h: (-h.score, h.display_name.lower()))
        return hits[:limit]
    finally:
        conn.close()


def format_drug_context(drug: DrugRecord, *, max_doses: int = 40) -> str:
    """Compact structured context for Claude dosage_brief / dosage_qa."""
    lines = [
        f"ID: {drug.id}",
        f"EN: {drug.canonical_name_en}",
        f"RU: {drug.canonical_name_ru}",
        f"Trade: {', '.join(drug.trade_names)}",
        f"Sources: {', '.join(drug.sources)}",
        f"POM: {drug.pom_note}",
        f"Formulations: {drug.formulations}",
        f"Action: {drug.action}",
        f"Use: {drug.use}",
        f"Safety: {drug.safety_handling}",
        f"Contraindications: {drug.contraindications}",
        f"Adverse: {drug.adverse_reactions}",
        f"Interactions: {drug.drug_interactions}",
        "DOSES:",
    ]
    for d in drug.doses[:max_doses]:
        lines.append(
            f"- [{d.get('source')}|{d.get('taxa')}] "
            f"{d.get('species_note')}: {d.get('raw_text')} "
            f"(min={d.get('dose_min')} max={d.get('dose_max')} {d.get('dose_unit')})"
        )
    return "\n".join(lines)
