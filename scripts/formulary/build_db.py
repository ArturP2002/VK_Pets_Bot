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

TRANSLATION_BATCH_SIZE = 25
FOREIGN_SOURCES = frozenset({"bsava", "carpenter"})
TRANSLATION_CACHE_FILENAME = "name_translations.jsonl"


def _has_cyrillic(value: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", value or ""))


def _translation_cache_path() -> Path:
    return raw_dir() / TRANSLATION_CACHE_FILENAME


def load_name_translation_cache() -> dict[str, dict[str, Any]]:
    """Key: normalized canonical_name_en → {canonical_name_ru, search_aliases, ...}."""
    cache: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(_translation_cache_path()):
        en = (row.get("canonical_name_en") or "").strip()
        key = normalize_name(en)
        if key and row.get("canonical_name_ru"):
            cache[key] = row
    return cache


def save_name_translation_cache(entries: dict[str, dict[str, Any]]) -> None:
    if not entries:
        return
    path = _translation_cache_path()
    write_jsonl(path, entries.values())


def drug_needs_ai_translation(drug: dict[str, Any]) -> bool:
    if (drug.get("canonical_name_ru") or "").strip():
        return False
    sources = {str(s).lower() for s in (drug.get("sources") or [])}
    if not sources & FOREIGN_SOURCES:
        return False
    en = (drug.get("canonical_name_en") or "").strip()
    if not en or _has_cyrillic(en):
        return False
    from scripts.formulary.junk_names import is_junk_drug_name

    if is_junk_drug_name(en):
        return False
    return True


def apply_translation_to_drug(
    drug: dict[str, Any],
    translation: dict[str, Any],
    *,
    source: str = "ai",
) -> bool:
    """Apply RU name + aliases; never overwrite existing manual canonical_name_ru."""
    if (drug.get("canonical_name_ru") or "").strip():
        return False
    name_ru = (translation.get("canonical_name_ru") or "").strip()
    if not name_ru:
        return False
    drug["canonical_name_ru"] = name_ru
    aliases = list(drug.get("aliases") or [])
    for alias in translation.get("search_aliases") or []:
        cleaned = (alias or "").strip()
        if cleaned and cleaned not in aliases:
            aliases.append(cleaned)
    if name_ru not in aliases:
        aliases.append(name_ru)
    drug["aliases"] = merge_unique(aliases)
    drug["_name_translation"] = {
        "name_ru": name_ru,
        "aliases": list(translation.get("search_aliases") or []),
        "source": source,
    }
    return True


def translate_drug_names_batch(
    drugs: list[dict[str, Any]],
    *,
    cache: dict[str, dict[str, Any]] | None = None,
    use_llm: bool = True,
) -> dict[str, dict[str, Any]]:
    """
    Fill canonical_name_ru for BSAVA/Carpenter drugs without RU names.
    Updates cache dict in place and returns it.
    """
    cache = cache if cache is not None else load_name_translation_cache()
    pending = [d for d in drugs if drug_needs_ai_translation(d)]
    if not pending:
        return cache

    for drug in pending:
        key = normalize_name(drug.get("canonical_name_en") or "")
        if key and key in cache:
            apply_translation_to_drug(drug, cache[key], source=cache[key].get("source", "cache"))

    still_pending = [d for d in pending if not (d.get("canonical_name_ru") or "").strip()]
    if not still_pending:
        return cache

    if not use_llm:
        for drug in still_pending:
            en = drug.get("canonical_name_en") or ""
            tr = transliterate_ru(en)
            if tr:
                apply_translation_to_drug(
                    drug,
                    {"canonical_name_ru": tr.capitalize() if tr.islower() else tr, "search_aliases": []},
                    source="translit",
                )
        return cache

    try:
        from services import llm_client
    except ImportError:
        llm_client = None  # type: ignore[assignment]

    if not llm_client or not llm_client.is_configured():
        logger.warning(
            "LLM is not configured (set LLM_PROVIDER and the matching API key); "
            "skipping AI drug name translation "
            "(%s drugs will use transliteration fallback)",
            len(still_pending),
        )
        return translate_drug_names_batch(drugs, cache=cache, use_llm=False)

    for i in range(0, len(still_pending), TRANSLATION_BATCH_SIZE):
        batch = still_pending[i : i + TRANSLATION_BATCH_SIZE]
        try:
            results = llm_client.translate_drug_names(batch)
        except Exception as exc:
            logger.warning("AI name translation batch failed: %s", exc)
            for drug in batch:
                en = drug.get("canonical_name_en") or ""
                tr = transliterate_ru(en)
                if tr:
                    apply_translation_to_drug(
                        drug,
                        {
                            "canonical_name_ru": tr.capitalize() if tr.islower() else tr,
                            "search_aliases": [],
                        },
                        source="translit",
                    )
            continue

        for drug, result in zip(batch, results):
            key = normalize_name(drug.get("canonical_name_en") or "")
            if not key:
                continue
            entry = {
                "canonical_name_en": drug.get("canonical_name_en") or "",
                "canonical_name_ru": result.get("canonical_name_ru") or "",
                "search_aliases": list(result.get("search_aliases") or []),
                "source": "ai",
            }
            if entry["canonical_name_ru"]:
                cache[key] = entry
                apply_translation_to_drug(drug, entry, source="ai")

    save_name_translation_cache(cache)
    return cache


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


# Explicit INN spelling variants only — do not fuzzy-merge (Aciclovir ≠ Famciclovir).
INN_SPELLING_CANON = {
    "aciclovir": "acyclovir",
    "valaciclovir": "valacyclovir",
    "acriflavin": "acriflavine",
    "famcyclovir": "famciclovir",
}

_TMP_SMX_CANON = "sulfamethoxazole trimethoprim"
_TMP_SMX_KEYS = {
    "sulfamethoxazole trimethoprim",
    "trimethoprim sulfamethoxazole",
    "trimethoprim sulfa",
    "sulfa trimethoprim",
    "cotrimoxazole",
    "co trimoxazole",
    "tmp smx",
    "tmp/smx",
    "smz tmp",
    "smx tmp",
    "biseptol",
    "бисептол",
    "bactrim",
    "бактрим",
    "ко тримоксазол",
    "котримоксазол",
}

_MIN_LOOKUP_KEY_LEN = 3

_TRAILING_CONTINUATION_RE = re.compile(
    r"(?:\s*\(\s*|\s+)(?:cont(?:inued)?['\u2018\u2019]?d?\.?|продолж(?:ение)?\.?)\s*\)?\s*$",
    re.I,
)
_CLOSED_PAREN_RE = re.compile(r"^(?P<inn>.+?)\s*\(\s*(?P<inner>[^)]+?)\s*\)\s*$")
_UNCLOSED_PAREN_RE = re.compile(r"^(?P<inn>.+?)\s*\(\s*(?P<inner>[^)]*?)\s*$")
_CONTINUATION_INNER_RE = re.compile(
    r"^(?:cont(?:inued)?['\u2018\u2019]?d?\.?|продолж(?:ение)?\.?)$",
    re.I,
)

_QUALIFIER_ABBREV = frozenset(
    {"sr", "xr", "pr", "mr", "la", "xl", "cr", "er", "ir", "dr", "hcl"}
)
_SALT_OR_FORM_WORDS = frozenset(
    {
        "hydrochloride",
        "hydrobromide",
        "maleate",
        "acetate",
        "sodium",
        "potassium",
        "phosphate",
        "sulfate",
        "sulphate",
        "chloride",
        "hydrate",
        "mesylate",
        "clavulanate",
        "clavulanic",
        "ophthalmic",
        "topical",
        "injectable",
        "depot",
        "compound",
        "sustained",
        "prolonged",
        "modified",
        "extended",
        "controlled",
        "delayed",
        "immediate",
        "release",
        "acting",
    }
)


def _spelling_canonical(norm: str) -> str:
    mapped = INN_SPELLING_CANON.get(norm, norm)
    collapsed = re.sub(r"[+/]+", " ", mapped)
    collapsed = collapsed.replace("-", " ")
    collapsed = re.sub(r"\s+", " ", collapsed).strip()
    if collapsed in _TMP_SMX_KEYS:
        return _TMP_SMX_CANON
    tokens = set(collapsed.split())
    if {"sulfamethoxazole", "trimethoprim"} <= tokens or {"sulfa", "trimethoprim"} <= tokens:
        return _TMP_SMX_CANON
    if "trimethoprim" in tokens and tokens & {
        "sulphonamide",
        "sulfonamide",
        "sulphonamides",
        "sulfonamides",
        "sulpha",
    }:
        return _TMP_SMX_CANON
    return mapped


def _is_page_crumb(inner: str) -> bool:
    text = inner.strip(" .,-–—")
    return (not text) or bool(re.fullmatch(r"[A-Za-zА-Яа-яЁё]", text))


def _is_qualifier(inner: str) -> bool:
    n = normalize_name(inner)
    if not n:
        return False
    if n in _QUALIFIER_ABBREV:
        return True
    tokens = set(n.replace("-", " ").split())
    if tokens & _SALT_OR_FORM_WORDS:
        return True
    if "release" in n or "acting" in n:
        return True
    return False


def _looks_like_trade(inner: str) -> bool:
    """True for Title-Case / Cyrillic trade names, not lowercase INN fragments."""
    text = inner.strip()
    if not text or len(text) < 3 or len(text) > 40:
        return False
    if _is_qualifier(text) or _CONTINUATION_INNER_RE.match(text):
        return False
    parts = [p.strip() for p in re.split(r"[,;/]", text) if p.strip()]
    if not parts or len(parts) > 4:
        return False
    for part in parts:
        if not re.search(r"[A-Za-zА-Яа-яЁё]", part):
            return False
        if re.fullmatch(r"[a-z][a-z0-9\-]+", part) and len(part) >= 5:
            return False
        if not (re.match(r"^[A-ZА-ЯЁ]", part) or re.search(r"[А-Яа-яЁё]", part)):
            return False
    return True


def _strip_name_noise(name: str) -> str:
    text = (name or "").strip()
    prev = None
    while text and prev != text:
        prev = text
        text = _TRAILING_CONTINUATION_RE.sub("", text).strip(" -–—,;")
    return text


def _split_inn_and_trade(name: str) -> tuple[str, str | None]:
    """Split 'Enrofloxacin (Baytril)' → INN + trade; keep formulation variants intact."""
    text = (name or "").strip()
    if not text:
        return "", None
    m = _CLOSED_PAREN_RE.fullmatch(text) or _UNCLOSED_PAREN_RE.fullmatch(text)
    if not m:
        return text, None
    inn = m.group("inn").strip(" -–—,")
    inner = m.group("inner").strip(" -–—,.")
    if not inn:
        return text, None
    if _CONTINUATION_INNER_RE.match(inner) or _is_page_crumb(inner):
        return inn, None
    if _is_qualifier(inner):
        return text, None
    if _looks_like_trade(inner):
        return inn, inner
    return text, None


def inn_match_key(name: str) -> str:
    """Normalized merge key: suffix-stripped INN with spelling variants applied."""
    stripped = _strip_name_noise(name)
    inn, _trade = _split_inn_and_trade(stripped)
    return _spelling_canonical(normalize_name(inn or name or ""))


def _display_inn(name: str) -> str:
    stripped = _strip_name_noise(name)
    inn, _trade = _split_inn_and_trade(stripped)
    return (inn or stripped or name or "").strip()


def _name_quality(name: str, *, prefer_latin: bool) -> tuple:
    n = (name or "").strip()
    inn = _display_inn(n)
    return (
        1 if (prefer_latin and _has_latin(n)) else 0,
        0 if _TRAILING_CONTINUATION_RE.search(n) else 1,
        0 if "(" in n else 1,
        len(normalize_name(inn)),
        -len(n),
    )


def _prefer_display_name(existing: str, new: str, *, prefer_latin: bool) -> str:
    existing = (existing or "").strip()
    new = (new or "").strip()
    if not existing:
        return _display_inn(new) or new
    if not new:
        return _display_inn(existing) or existing
    existing_q = _name_quality(existing, prefer_latin=prefer_latin)
    new_q = _name_quality(new, prefer_latin=prefer_latin)
    winner = existing if existing_q >= new_q else new
    return _display_inn(winner) or winner


def _iter_trade_parts(trade: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,;/]", trade) if p.strip()]


def _prepare_drug(drug: dict[str, Any]) -> dict[str, Any]:
    """Clean continuation/crumbs, lift INN(trade) parentheticals into trade_names."""
    out = dict(drug)
    trades = list(drug.get("trade_names") or [])
    aliases = list(drug.get("aliases") or [])
    for field in ("canonical_name_en", "canonical_name_ru"):
        raw = (out.get(field) or "").strip()
        if not raw:
            continue
        stripped = _strip_name_noise(raw)
        inn, trade = _split_inn_and_trade(stripped)
        if raw != inn:
            aliases.append(raw)
        if inn and inn != raw:
            aliases.append(inn)
        if trade:
            for part in _iter_trade_parts(trade):
                trades.append(part)
                aliases.append(part)
        if inn:
            out[field] = inn
    out["trade_names"] = merge_unique(trades)
    out["aliases"] = merge_unique(aliases)
    return out


def _keys_from_text(value: str) -> list[str]:
    keys: list[str] = []
    if not (value or "").strip():
        return keys
    stripped = _strip_name_noise(value)
    inn, _trade = _split_inn_and_trade(stripped)
    seen: set[str] = set()
    for piece in (value, stripped, inn):
        if not piece:
            continue
        for candidate in (normalize_name(piece), transliterate_ru(piece)):
            if not candidate or len(candidate) < _MIN_LOOKUP_KEY_LEN:
                continue
            for mapped in (candidate, _spelling_canonical(candidate)):
                if mapped and mapped not in seen:
                    seen.add(mapped)
                    keys.append(mapped)
    return keys


def _lookup_keys(drug: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for value in (
        drug.get("canonical_name_en"),
        drug.get("canonical_name_ru"),
        *(drug.get("aliases") or []),
        *(drug.get("trade_names") or []),
    ):
        for key in _keys_from_text(value or ""):
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def _merge_drug(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    base["canonical_name_en"] = _prefer_display_name(
        base.get("canonical_name_en") or "",
        incoming.get("canonical_name_en") or "",
        prefer_latin=True,
    )

    new_ru = (incoming.get("canonical_name_ru") or "").strip()
    if new_ru:
        if incoming.get("source") == "manual" or not (base.get("canonical_name_ru") or "").strip():
            base["canonical_name_ru"] = _prefer_display_name(
                base.get("canonical_name_ru") or "",
                new_ru,
                prefer_latin=False,
            )

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
    """Primary merge key from cleaned INN — not trade names (Baytril ≠ a new drug)."""
    for candidate in (
        drug.get("canonical_name_en"),
        drug.get("canonical_name_ru"),
        *(drug.get("aliases") or []),
    ):
        key = inn_match_key(candidate or "")
        if key:
            return key
    return inn_match_key(drug.get("canonical_name_en") or drug.get("canonical_name_ru") or "")


def merge_sources(
    bsava: list[dict[str, Any]],
    carpenter: list[dict[str, Any]],
    manual: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """BSAVA skeleton → Carpenter doses → manual RU fills. One INN → one card."""
    by_key: dict[str, dict[str, Any]] = {}
    alias_index: dict[str, str] = {}

    def resolve_key(drug: dict[str, Any]) -> str:
        for candidate in _lookup_keys(drug):
            if candidate in alias_index:
                return alias_index[candidate]
        return _match_key(drug)

    def index_drug(key: str, merged: dict[str, Any]) -> None:
        alias_index[key] = key
        for mapped in _lookup_keys(merged):
            alias_index[mapped] = key

    def ingest(rows: list[dict[str, Any]], priority_label: str) -> None:
        from scripts.formulary.junk_names import is_junk_drug_name

        for raw in rows:
            drug = _prepare_drug(raw)
            en = (drug.get("canonical_name_en") or "").strip()
            ru = (drug.get("canonical_name_ru") or "").strip()
            if en and is_junk_drug_name(en) and (not ru or is_junk_drug_name(ru)):
                continue
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
                    "source": drug.get("source") or (drug.get("sources") or [""])[0],
                }
            else:
                _merge_drug(by_key[key], drug)
            merged = by_key[key]
            index_drug(key, merged)
            logger.debug("Merged %s via %s → %s", drug.get("canonical_name_en"), priority_label, key)

    ingest(bsava, "bsava")
    ingest(carpenter, "carpenter")
    ingest(manual, "manual")
    _drop_cross_inn_aliases(list(by_key.values()))
    return list(by_key.values())


def _drop_cross_inn_aliases(drugs: list[dict[str, Any]]) -> None:
    """Do not keep another drug's INN as a trade analog (Метоклопрамид ≠ Mosapride)."""
    from scripts.formulary.common import fold_match_key

    owned: dict[str, int] = {}
    for idx, drug in enumerate(drugs):
        for value in (drug.get("canonical_name_en"), drug.get("canonical_name_ru")):
            key = fold_match_key(value or "")
            if key:
                owned[key] = idx
    for idx, drug in enumerate(drugs):
        drug["aliases"] = [
            alias
            for alias in (drug.get("aliases") or [])
            if owned.get(fold_match_key(alias), idx) == idx
        ]
        drug["trade_names"] = [
            trade
            for trade in (drug.get("trade_names") or [])
            if owned.get(fold_match_key(trade), idx) == idx
        ]


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

        translation_meta = drug.get("_name_translation")
        if translation_meta and translation_meta.get("name_ru"):
            conn.execute(
                """
                INSERT OR REPLACE INTO drug_name_cache
                    (drug_id, name_ru, aliases_json, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    drug_id,
                    translation_meta["name_ru"],
                    json.dumps(translation_meta.get("aliases") or [], ensure_ascii=False),
                    translation_meta.get("source") or "ai",
                    now,
                ),
            )

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
    translate_drug_names_batch(merged)
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
