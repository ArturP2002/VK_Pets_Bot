"""Search ranking: exact pick, RU display names, Ganaton/Тримедат cases."""
from __future__ import annotations

import json
import re
import sqlite3

import pytest

from scripts.formulary.common import normalize_name
from scripts.formulary.schema import init_schema
from services import dosage_service
from services.formulary_search import DrugHit, format_brief_ru, get_drug, search_drugs


def _seed_manual_drug_db(db_path, *, drug_id_hint: int | None = None) -> dict[str, int]:
    """Seed Mosapride/Ганатон and Тримедат manual entries like production data."""
    conn = sqlite3.connect(str(db_path))
    init_schema(conn)

    drugs = [
        (
            "Mosapride",
            "Мозаприд",
            ["manual"],
            [
                ("Mosapride", "mosapride", "en"),
                ("Ганатон", "ганатон", "ru"),
                ("ganaton", "ganaton", "tr"),
            ],
        ),
        (
            "Тримедат",
            "Тримедат",
            ["manual"],
            [
                ("Тримедат", "тримедат", "ru"),
                ("trimedat", "trimedat", "tr"),
            ],
        ),
        (
            "Trimethoprim",
            "",
            ["bsava"],
            [
                ("Trimethoprim", "trimethoprim", "en"),
            ],
        ),
    ]

    ids: dict[str, int] = {}
    for name_en, name_ru, sources, aliases in drugs:
        cur = conn.execute(
            """
            INSERT INTO drugs (canonical_name_en, canonical_name_ru, trade_names, sources)
            VALUES (?, ?, ?, ?)
            """,
            (name_en, name_ru, json.dumps([]), json.dumps(sources)),
        )
        drug_id = cur.lastrowid
        ids[name_en] = drug_id
        display = name_ru or name_en
        for alias, norm, lang in aliases:
            acur = conn.execute(
                "INSERT INTO drug_aliases(drug_id, alias, alias_norm, lang) VALUES (?,?,?,?)",
                (drug_id, alias, norm, lang),
            )
            conn.execute(
                "INSERT INTO drugs_fts(rowid, alias_norm, display_name) VALUES (?,?,?)",
                (acur.lastrowid, norm, display),
            )
    conn.commit()
    conn.close()
    return ids


@pytest.fixture
def formulary_db(tmp_path, monkeypatch):
    db_path = tmp_path / "formulary.db"
    ids = _seed_manual_drug_db(db_path)
    monkeypatch.setattr("config.FORMULARY_DB", str(db_path))
    monkeypatch.setattr("config.FORMULARY_SEARCH_MIN_SCORE", 60.0)
    return db_path, ids


def test_ganaton_exact_single_hit(formulary_db):
    _db, ids = formulary_db
    hits = search_drugs("Ганатон", limit=5)
    assert hits, "Ганатон must be found via alias_norm == ганатон"
    assert hits[0].drug_id == ids["Mosapride"]
    assert hits[0].is_exact
    assert hits[0].display_name == "Ганатон"
    picked = dosage_service.pick_search_hits(hits)
    assert len(picked) == 1
    assert picked[0].drug_id == ids["Mosapride"]


def test_trimedat_exact_beats_trimethoprim_fuzzy(formulary_db):
    _db, ids = formulary_db
    hits = search_drugs("Тримедат", limit=5)
    assert hits
    exact = [h for h in hits if h.is_exact]
    assert len(exact) == 1
    assert exact[0].drug_id == ids["Тримедат"]
    assert exact[0].display_name == "Тримедат"
    picked = dosage_service.pick_search_hits(hits)
    assert len(picked) == 1
    assert picked[0].drug_id == ids["Тримедат"]


def test_display_name_is_cyrillic_only(formulary_db):
    _db, ids = formulary_db
    hits = search_drugs("Ганатон", limit=1)
    assert hits
    name = hits[0].display_name
    assert re.search(r"[А-Яа-яЁё]", name)
    assert "Mosapride" not in name
    assert "(" not in name


def test_format_brief_ru_no_en_title(formulary_db):
    _db, ids = formulary_db
    drug = get_drug(ids["Mosapride"])
    assert drug is not None
    text = format_brief_ru(drug)
    assert "Международное название" not in text
    assert "Название (EN)" not in text
    assert "Mosapride" not in text.split("\n")[0]
    assert "Мозаприд" in text or "💊" in text


def test_canonical_name_ru_exact_match(formulary_db):
    _db, ids = formulary_db
    hits = search_drugs("Мозаприд", limit=3)
    assert hits and hits[0].is_exact
    assert hits[0].drug_id == ids["Mosapride"]


def test_drug_hit_display_prefers_matched_ru_alias():
    hit = DrugHit(
        drug_id=1,
        canonical_name_en="Mosapride",
        canonical_name_ru="Мозаприд",
        matched_alias="Ганатон",
        score=100.0,
        is_exact=True,
    )
    assert hit.display_name == "Ганатон"
