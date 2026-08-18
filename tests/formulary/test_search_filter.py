"""Filter junk names and dedupe visible search labels."""
from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.formulary.common import normalize_name
from scripts.formulary.junk_names import is_junk_drug_name
from scripts.formulary.schema import init_schema
from services import dosage_service
from services.formulary_search import DrugHit, filter_search_hits, search_drugs


def _insert_drug(
    conn: sqlite3.Connection,
    *,
    name_en: str,
    name_ru: str,
    sources: list[str],
    aliases: list[tuple[str, str, str]],
) -> int:
    cur = conn.execute(
        """
        INSERT INTO drugs (canonical_name_en, canonical_name_ru, trade_names, sources)
        VALUES (?, ?, ?, ?)
        """,
        (name_en, name_ru, json.dumps([]), json.dumps(sources)),
    )
    drug_id = cur.lastrowid
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
    return drug_id


@pytest.fixture
def formulary_db(tmp_path, monkeypatch):
    db_path = tmp_path / "formulary.db"
    conn = sqlite3.connect(str(db_path))
    init_schema(conn)

    ids = {
        "mosapride": _insert_drug(
            conn,
            name_en="Mosapride",
            name_ru="Мозаприд",
            sources=["manual"],
            aliases=[
                ("Mosapride", "mosapride", "en"),
                ("Ганатон", "ганатон", "ru"),
            ],
        ),
        "domperidone": _insert_drug(
            conn,
            name_en="Domperidone",
            name_ru="Ганатон",
            sources=["carpenter"],
            aliases=[
                ("Domperidone", "domperidone", "en"),
                ("Ганатон", "ганатон", "ru"),
            ],
        ),
        "february": _insert_drug(
            conn,
            name_en="February",
            name_ru="Февраль",
            sources=["carpenter"],
            aliases=[("Февраль", "февраль", "ru")],
        ),
        "febantel": _insert_drug(
            conn,
            name_en="Febantel",
            name_ru="Фебантел",
            sources=["bsava"],
            aliases=[("Febantel", "febantel", "en"), ("Фебантел", normalize_name("Фебантел"), "ru")],
        ),
        "alk_phos": _insert_drug(
            conn,
            name_en="Alkaline phosphatase",
            name_ru="Щелочная фосфатаза",
            sources=["carpenter"],
            aliases=[("Щелочная фосфатаза", "щелочнаяфосфатаза", "ru")],
        ),
    }
    conn.commit()
    conn.close()

    monkeypatch.setattr("config.FORMULARY_DB", str(db_path))
    monkeypatch.setattr("config.FORMULARY_SEARCH_MIN_SCORE", 60.0)
    return db_path, ids


def test_junk_names_detected():
    assert is_junk_drug_name("Февраль")
    assert is_junk_drug_name("Щелочная фосфатаза")
    assert is_junk_drug_name("2nd-degree AV block")
    assert not is_junk_drug_name("Ганатон")
    assert not is_junk_drug_name("Фебантел")
    assert is_junk_drug_name("Огненные муравьи")
    assert is_junk_drug_name("масло")
    assert is_junk_drug_name("с")
    assert is_junk_drug_name("Ме")
    assert is_junk_drug_name("Кожа")
    assert is_junk_drug_name("Белок (%)")
    assert is_junk_drug_name("в/в, интрацеломически")
    assert is_junk_drug_name("Финч")


def test_dedupe_same_display_name_prefers_manual(formulary_db):
    _db, ids = formulary_db
    hits = search_drugs("Ганатон", limit=5)
    assert len(hits) == 1
    assert hits[0].drug_id == ids["mosapride"]
    assert hits[0].display_name == "Ганатон"


def test_febtal_search_drops_junk_keeps_drugs(formulary_db):
    _db, ids = formulary_db
    hits = search_drugs("Фебтал", limit=10)
    names = {h.display_name for h in hits}
    assert "Февраль" not in names
    assert "Щелочная фосфатаза" not in names
    assert any(normalize_name(n).startswith("фебант") for n in names)


def test_filter_search_hits_dedupes_labels():
    hits = [
        DrugHit(
            drug_id=1,
            canonical_name_en="Mosapride",
            canonical_name_ru="Мозаприд",
            matched_alias="Ганатон",
            score=100.0,
            sources=["manual"],
            is_exact=True,
        ),
        DrugHit(
            drug_id=2,
            canonical_name_en="Domperidone",
            canonical_name_ru="Ганатон",
            matched_alias="Ганатон",
            score=100.0,
            sources=["carpenter"],
            is_exact=True,
        ),
    ]
    filtered = filter_search_hits(hits)
    assert len(filtered) == 1
    assert filtered[0].drug_id == 1


def test_pick_search_hits_single_after_dedupe(formulary_db):
    _db, ids = formulary_db
    hits = search_drugs("Ганатон", limit=5)
    picked = dosage_service.pick_search_hits(hits)
    assert len(picked) == 1
    assert picked[0].drug_id == ids["mosapride"]
