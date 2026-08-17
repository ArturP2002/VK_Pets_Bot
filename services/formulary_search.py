"""Fuzzy + FTS5 drug name search over formulary.db."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

import config
from scripts.formulary.common import normalize_name, transliterate_ru
from scripts.formulary.junk_names import is_junk_drug_name
from scripts.formulary.schema import ensure_schema_extensions

logger = logging.getLogger(__name__)

FOREIGN_SOURCES = frozenset({"bsava", "carpenter"})


@dataclass
class DrugHit:
    drug_id: int
    canonical_name_en: str
    canonical_name_ru: str
    matched_alias: str
    score: float
    sources: list[str] = field(default_factory=list)
    is_exact: bool = False

    @property
    def display_name(self) -> str:
        """RU-only label for buttons and cards."""
        if self.matched_alias and _has_cyrillic(self.matched_alias):
            return self.matched_alias
        if self.canonical_name_ru and _has_cyrillic(self.canonical_name_ru):
            return self.canonical_name_ru
        if self.canonical_name_ru:
            return self.canonical_name_ru
        tr = transliterate_ru(self.canonical_name_en or "")
        if tr:
            return tr
        return f"Препарат #{self.drug_id}"


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


def _has_cyrillic(value: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", value or ""))


def _pick_ru_alias(aliases: list[str]) -> str:
    for alias in aliases:
        if _has_cyrillic(alias):
            return alias.strip()
    return ""


def _is_exact_alias_match(q_norm: str, q_tr: str, alias_norm: str) -> bool:
    return bool(alias_norm and (alias_norm == q_norm or alias_norm == q_tr))


def _is_exact_canonical_match(q_norm: str, q_tr: str, name_ru: str) -> bool:
    ru_norm = normalize_name(name_ru or "")
    return bool(ru_norm and (ru_norm == q_norm or ru_norm == q_tr))


def _adjust_score_for_query_lang(
    score: float,
    *,
    query_cyrillic: bool,
    alias: str,
    alias_lang: str,
    name_ru: str,
) -> float:
    """Boost RU hits and penalize EN-only hits for Cyrillic queries."""
    if not query_cyrillic:
        return score
    alias_cyrillic = _has_cyrillic(alias)
    ru_cyrillic = _has_cyrillic(name_ru or "")
    if alias_cyrillic or alias_lang == "ru" or ru_cyrillic:
        return min(100.0, score + 15.0)
    if alias_lang in ("en", "tr", "") and not ru_cyrillic:
        return max(0.0, score - 15.0)
    return score


def _source_priority(sources: list[str]) -> int:
    order = {"manual": 3, "bsava": 2, "carpenter": 1}
    return max((order.get(str(s).lower(), 0) for s in sources), default=0)


def _is_junk_hit(hit: DrugHit) -> bool:
    for name in (hit.canonical_name_en, hit.canonical_name_ru, hit.matched_alias):
        if name and is_junk_drug_name(name):
            return True
    return is_junk_drug_name(hit.display_name)


def _dedupe_by_display_name(hits: list[DrugHit]) -> list[DrugHit]:
    """One button per visible label; keep the best-ranked hit."""
    best: dict[str, DrugHit] = {}
    for hit in hits:
        key = normalize_name(hit.display_name)
        if not key:
            continue
        prev = best.get(key)
        if prev is None:
            best[key] = hit
            continue
        hit_key = (
            int(hit.is_exact),
            hit.score,
            _source_priority(hit.sources),
        )
        prev_key = (
            int(prev.is_exact),
            prev.score,
            _source_priority(prev.sources),
        )
        if hit_key > prev_key:
            best[key] = hit
    return sorted(
        best.values(),
        key=lambda h: (-int(h.is_exact), -h.score, h.display_name.lower()),
    )


def _store_hit(best: dict[int, DrugHit], hit: DrugHit) -> None:
    prev = best.get(hit.drug_id)
    if prev is None:
        best[hit.drug_id] = hit
        return
    if hit.is_exact and not prev.is_exact:
        best[hit.drug_id] = hit
        return
    if prev.is_exact and not hit.is_exact:
        return
    if hit.score > prev.score:
        best[hit.drug_id] = hit
        return
    if hit.score == prev.score and hit.is_exact and prev.is_exact:
        if _has_cyrillic(hit.matched_alias) and not _has_cyrillic(prev.matched_alias):
            best[hit.drug_id] = hit


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.FORMULARY_DB)
    if not path.exists():
        raise FileNotFoundError(
            f"Formulary DB not found at {path}. Run: python -m scripts.formulary"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    ensure_schema_extensions(conn)
    return conn


def _read_name_cache(conn: sqlite3.Connection, drug_id: int) -> tuple[str, list[str]] | None:
    try:
        row = conn.execute(
            "SELECT name_ru, aliases_json FROM drug_name_cache WHERE drug_id = ?",
            (drug_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    name_ru = (row["name_ru"] or "").strip()
    if not name_ru:
        return None
    aliases_raw = json.loads(row["aliases_json"] or "[]")
    aliases = [str(a).strip() for a in aliases_raw if str(a).strip()]
    return name_ru, aliases


def _write_name_cache(
    conn: sqlite3.Connection,
    drug_id: int,
    name_ru: str,
    aliases: list[str],
    *,
    source: str = "ai",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO drug_name_cache
            (drug_id, name_ru, aliases_json, source, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (drug_id, name_ru, json.dumps(aliases, ensure_ascii=False), source, now),
    )
    conn.commit()


def _persist_runtime_aliases(
    conn: sqlite3.Connection,
    drug_id: int,
    name_ru: str,
    aliases: list[str],
) -> None:
    """Add RU aliases + FTS rows so translated names become searchable."""
    display = name_ru
    candidates = [name_ru, *aliases]
    seen: set[str] = set()
    for alias in candidates:
        cleaned = (alias or "").strip()
        norm = normalize_name(cleaned)
        if not cleaned or not norm or norm in seen:
            continue
        seen.add(norm)
        lang = "ru" if _has_cyrillic(cleaned) else "en"
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO drug_aliases(drug_id, alias, alias_norm, lang)
            VALUES (?, ?, ?, ?)
            """,
            (drug_id, cleaned, norm, lang),
        )
        if cur.rowcount:
            alias_id = cur.lastrowid
            conn.execute(
                "INSERT OR IGNORE INTO drugs_fts(rowid, alias_norm, display_name) VALUES (?, ?, ?)",
                (alias_id, norm, display),
            )
    conn.commit()


def resolve_display_name_ru(
    drug: DrugRecord | DrugHit,
    *,
    db_path: str | Path | None = None,
    use_llm: bool = True,
) -> str:
    """
    Resolve a Russian display name: DB field → cache → optional LLM → transliteration.
    Persists runtime translations to drug_name_cache and drug_aliases.
    """
    drug_id = drug.id if isinstance(drug, DrugRecord) else drug.drug_id
    name_ru = (drug.canonical_name_ru or "").strip()
    if name_ru and _has_cyrillic(name_ru):
        return name_ru

    aliases = drug.aliases if isinstance(drug, DrugRecord) else []
    alias_ru = _pick_ru_alias(aliases)
    if alias_ru:
        return alias_ru

    sources = drug.sources if isinstance(drug, DrugRecord) else (drug.sources or [])
    name_en = drug.canonical_name_en or ""

    conn = _connect(db_path)
    try:
        cached = _read_name_cache(conn, drug_id)
        if cached:
            cached_name, _cached_aliases = cached
            if cached_name:
                return cached_name

        foreign = bool({str(s).lower() for s in sources} & FOREIGN_SOURCES)
        if use_llm and foreign and name_en and not _has_cyrillic(name_en):
            try:
                from services import llm_client

                if llm_client.is_configured():
                    payload = {
                        "canonical_name_en": name_en,
                        "action": drug.action if isinstance(drug, DrugRecord) else "",
                        "formulations": drug.formulations if isinstance(drug, DrugRecord) else "",
                        "sources": list(sources),
                    }
                    results = llm_client.translate_drug_names([payload])
                    if results:
                        translated = (results[0].get("canonical_name_ru") or "").strip()
                        extra = [
                            str(a).strip()
                            for a in (results[0].get("search_aliases") or [])
                            if str(a).strip()
                        ]
                        if translated:
                            _write_name_cache(conn, drug_id, translated, extra, source="ai")
                            _persist_runtime_aliases(conn, drug_id, translated, extra)
                            if isinstance(drug, DrugRecord):
                                drug.canonical_name_ru = translated
                            elif isinstance(drug, DrugHit):
                                drug.canonical_name_ru = translated
                            return translated
            except Exception as exc:
                logger.warning("Runtime drug name translation failed for #%s: %s", drug_id, exc)

        tr = transliterate_ru(name_en)
        if tr:
            return tr
        return f"Препарат #{drug_id}"
    finally:
        conn.close()


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
        record = DrugRecord(
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
        if not _has_cyrillic(record.canonical_name_ru or ""):
            record.canonical_name_ru = resolve_display_name_ru(record, db_path=db_path)
        return record
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


def _load_alias_corpus(
    conn: sqlite3.Connection,
) -> list[tuple[int, str, str, str, str, str, str]]:
    """(drug_id, alias, alias_norm, lang, name_en, name_ru, sources_json)."""
    return [
        (
            r["drug_id"],
            r["alias"],
            r["alias_norm"],
            r["lang"],
            r["canonical_name_en"],
            r["canonical_name_ru"],
            r["sources"],
        )
        for r in conn.execute(
            """
            SELECT a.drug_id, a.alias, a.alias_norm, a.lang,
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
    query_cyrillic = _has_cyrillic(q)

    conn = _connect(db_path)
    try:
        corpus = _load_alias_corpus(conn)
        if not corpus:
            return []

        # Exact / substring prefilter (alias_norm + canonical_name_ru)
        exact_ids: dict[int, tuple[float, str, bool]] = {}
        for drug_id, alias, alias_norm, lang, name_en, name_ru, _sources in corpus:
            if _is_exact_alias_match(q_norm, q_tr, alias_norm):
                prev = exact_ids.get(drug_id)
                if prev is None or (
                    _has_cyrillic(alias) and not _has_cyrillic(prev[1])
                ):
                    exact_ids[drug_id] = (100.0, alias, True)
            elif q_norm and (q_norm in alias_norm or alias_norm in q_norm):
                exact_ids.setdefault(drug_id, (92.0, alias, False))

        for row in conn.execute(
            "SELECT id, canonical_name_ru FROM drugs WHERE canonical_name_ru != ''"
        ):
            name_ru = row["canonical_name_ru"]
            if _is_exact_canonical_match(q_norm, q_tr, name_ru):
                prev = exact_ids.get(row["id"])
                if prev is None or (
                    _has_cyrillic(name_ru) and not _has_cyrillic(prev[1])
                ):
                    exact_ids[row["id"]] = (100.0, name_ru, True)

        fts_rows = _fts_candidates(conn, q, limit=50)
        for row in fts_rows:
            exact_ids.setdefault(row["drug_id"], (88.0, row["alias"], False))

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

        def consider(
            drug_id: int,
            score: float,
            alias: str,
            meta_idx: int | None = None,
            *,
            is_exact: bool = False,
        ) -> None:
            if meta_idx is not None:
                _, _, _, lang, name_en, name_ru, sources_json = corpus[meta_idx]
            else:
                row = next((c for c in corpus if c[0] == drug_id), None)
                if not row:
                    return
                _, _, _, lang, name_en, name_ru, sources_json = row
            score = _adjust_score_for_query_lang(
                float(score),
                query_cyrillic=query_cyrillic,
                alias=alias,
                alias_lang=lang or "",
                name_ru=name_ru or "",
            )
            if score < threshold:
                return
            hit = DrugHit(
                drug_id=drug_id,
                canonical_name_en=name_en,
                canonical_name_ru=name_ru,
                matched_alias=alias,
                score=score,
                sources=json.loads(sources_json or "[]"),
                is_exact=is_exact,
            )
            _store_hit(best, hit)

        for drug_id, (score, alias, is_exact) in exact_ids.items():
            consider(drug_id, score, alias, is_exact=is_exact)

        for alias_norm, score, idx in fuzzy:
            drug_id, alias, norm, lang, name_en, name_ru, _sources = corpus[idx]
            fuzzy_exact = _is_exact_alias_match(q_norm, q_tr, norm)
            consider(
                drug_id,
                float(score),
                alias,
                meta_idx=idx,
                is_exact=fuzzy_exact,
            )

        hits = sorted(best.values(), key=lambda h: (-h.score, h.display_name.lower()))
        for hit in hits:
            if not _has_cyrillic(hit.canonical_name_ru or ""):
                hit.canonical_name_ru = resolve_display_name_ru(
                    hit, db_path=db_path, use_llm=False
                )
        return filter_search_hits(hits)[:limit]
    finally:
        conn.close()


def filter_search_hits(hits: list[DrugHit]) -> list[DrugHit]:
    """Remove junk entries and duplicate visible labels."""
    cleaned = [h for h in hits if not _is_junk_hit(h)]
    return _dedupe_by_display_name(cleaned)


def format_drug_context(drug: DrugRecord, *, max_doses: int = 40) -> str:
    """Compact structured context for dosage_brief / dosage_qa (source language)."""
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


TAXA_RU = {
    "fish": "Рыбы",
    "mammals": "Млекопитающие",
    "birds": "Птицы",
    "reptiles": "Рептилии",
    "amphibians": "Амфибии",
    "invertebrates": "Беспозвоночные",
    "other": "Другие / прочие",
}

SOURCE_RU = {
    "bsava": "BSAVA",
    "carpenter": "Carpenter",
    "manual": "ручной справочник (RU)",
}


def _taxa_ru(taxa: str | None) -> str:
    key = (taxa or "other").lower()
    return TAXA_RU.get(key, taxa or "прочие")


def _source_ru(source: str | None) -> str:
    key = (source or "").lower()
    return SOURCE_RU.get(key, source or "неизвестно")


def format_brief_ru(drug: DrugRecord, *, max_doses: int = 35) -> str:
    """
    User-facing card summary in Russian (no LLM).
    Empty EN monograph fields are omitted; doses are always listed with RU labels.
    """
    title = (
        drug.canonical_name_ru
        if _has_cyrillic(drug.canonical_name_ru or "")
        else resolve_display_name_ru(drug, use_llm=False)
    )
    lines: list[str] = [f"💊 {title}"]
    if drug.trade_names:
        lines.append(f"Торговые названия: {', '.join(drug.trade_names)}")
    if drug.sources:
        lines.append(f"Источники: {', '.join(_source_ru(s) for s in drug.sources)}")
    lines.append("")

    sections = [
        ("Формы выпуска", drug.formulations),
        ("Действие", drug.action),
        ("Применение", drug.use or drug.full_text_ru),
        ("Меры предосторожности", drug.safety_handling),
        ("Противопоказания", drug.contraindications),
        ("Побочные эффекты", drug.adverse_reactions),
        ("Лекарственные взаимодействия", drug.drug_interactions),
    ]
    has_text = False
    for title_ru, value in sections:
        text = (value or "").strip()
        if not text:
            continue
        has_text = True
        lines.append(f"{title_ru}:")
        lines.append(text)
        lines.append("")

    if drug.pom_note and drug.pom_note.strip():
        lines.append(f"Статус / POM: {drug.pom_note.strip()}")
        lines.append("")

    if drug.doses:
        lines.append("Дозы:")
        by_taxa: dict[str, list[dict[str, Any]]] = {}
        for d in drug.doses[:max_doses]:
            by_taxa.setdefault(d.get("taxa") or "other", []).append(d)
        for taxa, rows in by_taxa.items():
            lines.append(f"▸ {_taxa_ru(taxa)}")
            for d in rows:
                species = (d.get("species_note") or "").strip()
                raw = (d.get("raw_text") or "").strip()
                dmin, dmax, unit = d.get("dose_min"), d.get("dose_max"), d.get("dose_unit") or ""
                src = _source_ru(d.get("source"))
                head = f"  • {species}: " if species else "  • "
                if raw:
                    lines.append(f"{head}{raw} [{src}]")
                elif dmin is not None or dmax is not None:
                    if dmin is not None and dmax is not None and dmin != dmax:
                        dose_s = f"{dmin}–{dmax} {unit}".strip()
                    else:
                        dose_s = f"{dmin if dmin is not None else dmax} {unit}".strip()
                    lines.append(f"{head}{dose_s} [{src}]")
                else:
                    lines.append(f"{head}(текст дозы не разобран) [{src}]")
        if len(drug.doses) > max_doses:
            lines.append(f"  … ещё {len(drug.doses) - max_doses} записей в справочнике")
        lines.append("")
    elif not has_text:
        lines.append("В карточке пока нет структурированных доз и описания.")
        lines.append("")

    lines.append("Можно открыть «Калькулятор дозы» для расчёта.")
    return "\n".join(lines).strip()

