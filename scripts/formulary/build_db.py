"""Merge extract JSONL → SQLite formulary.db + Chroma chunks."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.formulary.common import (
    merge_unique,
    normalize_name,
    project_root,
    raw_dir,
    read_jsonl,
    transliterate_ru,
    write_jsonl,
)
from scripts.formulary.extract_bsava import extract as extract_bsava
from scripts.formulary.extract_carpenter import extract as extract_carpenter
from scripts.formulary.extract_manual_ru import extract as extract_manual
from scripts.formulary.schema import init_schema

logger = logging.getLogger(__name__)

SECTION_CHUNK_FIELDS = (
    ("formulations", "formulations"),
    ("action", "action"),
    ("use", "use"),
    ("safety_handling", "safety_handling"),
    ("contraindications", "contraindications"),
    ("adverse_reactions", "adverse_reactions"),
    ("drug_interactions", "drug_interactions"),
)


def _prefer_text(existing: str, new: str) -> str:
    existing = (existing or "").strip()
    new = (new or "").strip()
    if not existing:
        return new
    if not new:
        return existing
    if new in existing:
        return existing
    if existing in new:
        return new
    # Prefer longer monograph-style text
    return existing if len(existing) >= len(new) else new


def _has_latin(value: str) -> bool:
    return bool(re.search(r"[A-Za-z]", value or ""))


def _merge_drug(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    if not base.get("canonical_name_en") and incoming.get("canonical_name_en"):
        base["canonical_name_en"] = incoming["canonical_name_en"]
    if incoming.get("canonical_name_en") and _has_latin(incoming["canonical_name_en"]):
        if not _has_latin(base.get("canonical_name_en", "")):
            base["canonical_name_en"] = incoming["canonical_name_en"]

    if incoming.get("canonical_name_ru") and (
        incoming.get("source") == "manual" or not base.get("canonical_name_ru")
    ):
        base["canonical_name_ru"] = incoming["canonical_name_ru"]

    base["trade_names"] = merge_unique(
        list(base.get("trade_names") or []) + list(incoming.get("trade_names") or [])
    )
    base["aliases"] = merge_unique(
        list(base.get("aliases") or []) + list(incoming.get("aliases") or [])
    )
    for field in (
        "pom_note",
        "formulations",
        "action",
        "use",
        "safety_handling",
        "contraindications",
        "adverse_reactions",
        "drug_interactions",
        "full_text_en",
        "full_text_ru",
    ):
        if field == "full_text_ru" and incoming.get("full_text_ru"):
            base[field] = _prefer_text(base.get(field, ""), incoming["full_text_ru"])
            continue
        if incoming.get("source") == "bsava" and incoming.get(field):
            base[field] = _prefer_text(base.get(field, ""), incoming[field])
        else:
            base[field] = _prefer_text(base.get(field, ""), incoming.get(field, ""))

    base["doses"] = list(base.get("doses") or []) + list(incoming.get("doses") or [])
    sources = list(base.get("sources") or [])
    for s in incoming.get("sources") or [incoming.get("source")]:
        if s and s not in sources:
            sources.append(s)
    base["sources"] = sources
    return base


def _match_key(drug: dict[str, Any]) -> str:
    for candidate in (
        drug.get("canonical_name_en"),
        drug.get("canonical_name_ru"),
        *(drug.get("aliases") or []),
        *(drug.get("trade_names") or []),
    ):
        norm = normalize_name(candidate or "")
        if norm and _has_latin(candidate or ""):
            return norm
    return normalize_name(drug.get("canonical_name_en") or drug.get("canonical_name_ru") or "")


def merge_sources(
    bsava: list[dict[str, Any]],
    carpenter: list[dict[str, Any]],
    manual: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """BSAVA skeleton → Carpenter doses → manual RU fills."""
    by_key: dict[str, dict[str, Any]] = {}
    alias_index: dict[str, str] = {}

    def resolve_key(drug: dict[str, Any]) -> str:
        candidates = []
        for c in (
            drug.get("canonical_name_en"),
            drug.get("canonical_name_ru"),
            *(drug.get("aliases") or []),
            *(drug.get("trade_names") or []),
        ):
            n = normalize_name(c or "")
            if n:
                candidates.append(n)
                tr = transliterate_ru(c or "")
                if tr:
                    candidates.append(tr)
        for c in candidates:
            if c in alias_index:
                return alias_index[c]
        primary = _match_key(drug)
        return primary

    def ingest(rows: list[dict[str, Any]], priority_label: str) -> None:
        for drug in rows:
            key = resolve_key(drug)
            if not key:
                continue
            if key not in by_key:
                by_key[key] = {
                    "canonical_name_en": drug.get("canonical_name_en") or "",
                    "canonical_name_ru": drug.get("canonical_name_ru") or "",
                    "trade_names": list(drug.get("trade_names") or []),
                    "aliases": list(drug.get("aliases") or []),
                    "pom_note": drug.get("pom_note") or "",
                    "formulations": drug.get("formulations") or "",
                    "action": drug.get("action") or "",
                    "use": drug.get("use") or "",
                    "safety_handling": drug.get("safety_handling") or "",
                    "contraindications": drug.get("contraindications") or "",
                    "adverse_reactions": drug.get("adverse_reactions") or "",
                    "drug_interactions": drug.get("drug_interactions") or "",
                    "full_text_en": drug.get("full_text_en") or "",
                    "full_text_ru": drug.get("full_text_ru") or "",
                    "doses": list(drug.get("doses") or []),
                    "sources": list(drug.get("sources") or [drug.get("source")]),
                }
            else:
                _merge_drug(by_key[key], drug)
            # refresh alias index
            merged = by_key[key]
            for alias in merge_unique(
                [
                    merged.get("canonical_name_en", ""),
                    merged.get("canonical_name_ru", ""),
                    *(merged.get("aliases") or []),
                    *(merged.get("trade_names") or []),
                ]
            ):
                n = normalize_name(alias)
                if n:
                    alias_index[n] = key
                tr = transliterate_ru(alias)
                if tr:
                    alias_index[tr] = key
            logger.debug("Merged %s via %s → %s", drug.get("canonical_name_en"), priority_label, key)

    ingest(bsava, "bsava")
    ingest(carpenter, "carpenter")
    ingest(manual, "manual")
    return list(by_key.values())


def _alias_rows(drug: dict[str, Any]) -> list[tuple[str, str, str]]:
    values = merge_unique(
        [
            drug.get("canonical_name_en", ""),
            drug.get("canonical_name_ru", ""),
            *(drug.get("trade_names") or []),
            *(drug.get("aliases") or []),
        ]
    )
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for value in values:
        norm = normalize_name(value)
        if norm and norm not in seen:
            seen.add(norm)
            lang = "ru" if any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in value) else "en"
            rows.append((value, norm, lang))
        tr = transliterate_ru(value)
        if tr and tr not in seen:
            seen.add(tr)
            rows.append((tr, tr, "tr"))
    return rows


def write_sqlite(drugs: list[dict[str, Any]], db_path: Path) -> dict[str, int]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    init_schema(conn)
    now = datetime.now(timezone.utc).isoformat()
    drug_count = 0
    alias_count = 0
    dose_count = 0

    for drug in drugs:
        cur = conn.execute(
            """
            INSERT INTO drugs (
                canonical_name_en, canonical_name_ru, trade_names, pom_note,
                formulations, action, use_text, safety_handling, contraindications,
                adverse_reactions, drug_interactions, full_text_en, full_text_ru,
                sources, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                drug.get("canonical_name_en") or "",
                drug.get("canonical_name_ru") or "",
                json.dumps(drug.get("trade_names") or [], ensure_ascii=False),
                drug.get("pom_note") or "",
                drug.get("formulations") or "",
                drug.get("action") or "",
                drug.get("use") or "",
                drug.get("safety_handling") or "",
                drug.get("contraindications") or "",
                drug.get("adverse_reactions") or "",
                drug.get("drug_interactions") or "",
                drug.get("full_text_en") or "",
                drug.get("full_text_ru") or "",
                json.dumps(drug.get("sources") or [], ensure_ascii=False),
                now,
            ),
        )
        drug_id = cur.lastrowid
        drug_count += 1
        drug["_id"] = drug_id

        for alias, alias_norm, lang in _alias_rows(drug):
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO drug_aliases(drug_id, alias, alias_norm, lang)
                VALUES (?, ?, ?, ?)
                """,
                (drug_id, alias, alias_norm, lang),
            )
            if cur.rowcount:
                alias_count += 1

        for dose in drug.get("doses") or []:
            conn.execute(
                """
                INSERT INTO doses (
                    drug_id, taxa, species_note, indication, route,
                    dose_min, dose_max, dose_unit, frequency, duration, raw_text, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    drug_id,
                    dose.get("taxa") or "other",
                    dose.get("species_note") or "",
                    dose.get("indication") or "",
                    dose.get("route") or "",
                    dose.get("dose_min"),
                    dose.get("dose_max"),
                    dose.get("dose_unit") or "",
                    dose.get("frequency") or "",
                    dose.get("duration") or "",
                    dose.get("raw_text") or "",
                    dose.get("source") or "",
                ),
            )
            dose_count += 1

    # Rebuild FTS properly keyed by alias id for search joins
    conn.execute("DELETE FROM drugs_fts")
    for row in conn.execute(
        "SELECT id, alias_norm, drug_id FROM drug_aliases ORDER BY id"
    ):
        alias_id, alias_norm, drug_id = row
        display = conn.execute(
            "SELECT COALESCE(NULLIF(canonical_name_ru,''), canonical_name_en) FROM drugs WHERE id=?",
            (drug_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO drugs_fts(rowid, alias_norm, display_name) VALUES (?, ?, ?)",
            (alias_id, alias_norm, display),
        )

    conn.commit()
    conn.close()
    return {"drugs": drug_count, "aliases": alias_count, "doses": dose_count}


def _chunk_text(text: str, max_chars: int = 1800) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            split_at = text.rfind(". ", start, end)
            if split_at > start + max_chars // 3:
                end = split_at + 1
        chunks.append(text[start:end].strip())
        start = end
    return [c for c in chunks if c]


def build_chroma(drugs: list[dict[str, Any]], chroma_dir: Path) -> int:
    try:
        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except ImportError:
        logger.warning("chromadb/sentence-transformers not installed; skipping vectors")
        return 0

    import config as app_config

    chroma_dir.mkdir(parents=True, exist_ok=True)
    model_name = app_config.FORMULARY_EMBEDDING_MODEL
    try:
        embedding_fn = SentenceTransformerEmbeddingFunction(
            model_name=model_name,
            local_files_only=True,
        )
    except Exception:
        embedding_fn = SentenceTransformerEmbeddingFunction(model_name=model_name)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    try:
        client.delete_collection(app_config.FORMULARY_CHROMA_COLLECTION)
    except Exception:
        pass
    collection = client.get_or_create_collection(
        name=app_config.FORMULARY_CHROMA_COLLECTION,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for drug in drugs:
        drug_id = drug.get("_id")
        if not drug_id:
            continue
        name = drug.get("canonical_name_en") or drug.get("canonical_name_ru") or ""
        sources = ",".join(drug.get("sources") or [])
        for section, field in SECTION_CHUNK_FIELDS:
            for i, chunk in enumerate(_chunk_text(drug.get(field, ""))):
                ids.append(f"{drug_id}:{section}:{i}")
                documents.append(f"{name} — {section}\n{chunk}")
                metadatas.append(
                    {
                        "drug_id": int(drug_id),
                        "section": section,
                        "source": sources,
                        "lang": "en" if section != "use" or not drug.get("full_text_ru") else "mixed",
                        "drug_name": name,
                    }
                )
        # RU full text
        for i, chunk in enumerate(_chunk_text(drug.get("full_text_ru", ""))):
            ids.append(f"{drug_id}:full_ru:{i}")
            documents.append(chunk)
            metadatas.append(
                {
                    "drug_id": int(drug_id),
                    "section": "full_text_ru",
                    "source": sources,
                    "lang": "ru",
                    "drug_name": name,
                }
            )
        # Dose summary chunk
        dose_lines = [
            f"[{d.get('source')}|{d.get('taxa')}] {d.get('raw_text')}"
            for d in (drug.get("doses") or [])[:40]
            if d.get("raw_text")
        ]
        if dose_lines:
            for i, chunk in enumerate(_chunk_text("\n".join(dose_lines), max_chars=1500)):
                ids.append(f"{drug_id}:doses:{i}")
                documents.append(f"{name} — doses\n{chunk}")
                metadatas.append(
                    {
                        "drug_id": int(drug_id),
                        "section": "doses",
                        "source": sources,
                        "lang": "en",
                        "drug_name": name,
                    }
                )

    # Upsert in batches
    batch = 200
    for i in range(0, len(ids), batch):
        collection.upsert(
            ids=ids[i : i + batch],
            documents=documents[i : i + batch],
            metadatas=metadatas[i : i + batch],
        )
    logger.info("Chroma: upserted %s chunks", len(ids))
    return len(ids)


def build(
    *,
    db_path: Path,
    chroma_dir: Path,
    skip_chroma: bool = False,
    reuse_raw: bool = False,
) -> dict[str, int]:
    raw = raw_dir()
    if reuse_raw and (raw / "bsava.jsonl").exists():
        bsava = read_jsonl(raw / "bsava.jsonl")
    else:
        bsava = extract_bsava()
        write_jsonl(raw / "bsava.jsonl", bsava)

    if reuse_raw and (raw / "carpenter.jsonl").exists():
        carpenter = read_jsonl(raw / "carpenter.jsonl")
    else:
        carpenter = extract_carpenter()
        write_jsonl(raw / "carpenter.jsonl", carpenter)

    if reuse_raw and (raw / "manual_ru.jsonl").exists():
        manual = read_jsonl(raw / "manual_ru.jsonl")
    else:
        manual = extract_manual()
        write_jsonl(raw / "manual_ru.jsonl", manual)

    merged = merge_sources(bsava, carpenter, manual)
    stats = write_sqlite(merged, db_path)
    stats["chunks"] = 0 if skip_chroma else build_chroma(merged, chroma_dir)
    stats["merged"] = len(merged)
    return stats


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = project_root()
    parser = argparse.ArgumentParser(description="Build formulary SQLite + Chroma")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("FORMULARY_DB", str(root / "data" / "formulary.db"))),
    )
    parser.add_argument(
        "--chroma",
        type=Path,
        default=Path(
            os.getenv("FORMULARY_CHROMA_DIR", str(root / "data" / "chroma_formulary"))
        ),
    )
    parser.add_argument("--skip-chroma", action="store_true")
    parser.add_argument(
        "--reuse-raw",
        action="store_true",
        help="Reuse data/formulary_raw/*.jsonl if present",
    )
    args = parser.parse_args(argv)
    stats = build(
        db_path=args.db,
        chroma_dir=args.chroma,
        skip_chroma=args.skip_chroma,
        reuse_raw=args.reuse_raw,
    )
    print(
        "Built formulary: "
        f"{stats['merged']} merged drugs, {stats['drugs']} rows, "
        f"{stats['aliases']} aliases, {stats['doses']} doses, "
        f"{stats['chunks']} chunks → {args.db}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
