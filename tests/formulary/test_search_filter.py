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


def test_junk_fragments_and_species_names():
    assert is_junk_drug_name("ICI")
    assert is_junk_drug_name("кислота")
    assert is_junk_drug_name("Самец")
    assert is_junk_drug_name("Свинка морская")
    assert is_junk_drug_name("Guinea pig")
    assert not is_junk_drug_name("Амоксициллин")
    assert not is_junk_drug_name("Co-amoxiclav")


@pytest.fixture
def fragment_db(tmp_path, monkeypatch):
    db_path = tmp_path / "fragments.db"
    conn = sqlite3.connect(str(db_path))
    init_schema(conn)
    _insert_drug(
        conn,
        name_en="ICI",
        name_ru="ICI",
        sources=["carpenter"],
        aliases=[("ICI", "ici", "en")],
    )
    _insert_drug(
        conn,
        name_en="acid",
        name_ru="кислота",
        sources=["carpenter"],
        aliases=[("acid", "acid", "en"), ("кислота", "кислота", "ru")],
    )
    _insert_drug(
        conn,
        name_en="Amoxicillin",
        name_ru="Амоксициллин",
        sources=["bsava"],
        aliases=[
            ("Amoxicillin", "amoxicillin", "en"),
            ("Амоксициллин", normalize_name("Амоксициллин"), "ru"),
        ],
    )
    _insert_drug(
        conn,
        name_en="Co-amoxiclav",
        name_ru="Амоксициллин+клавулановая кислота",
        sources=["curated"],
        aliases=[
            ("Co-amoxiclav", "co-amoxiclav", "en"),
            ("Амоксициллин+клавулановая кислота", normalize_name("Амоксициллин+клавулановая кислота"), "ru"),
            ("Амоксиклав", normalize_name("Амоксиклав"), "ru"),
        ],
    )
    _insert_drug(
        conn,
        name_en="Essential fatty acids",
        name_ru="Ненасыщенные жирные кислоты",
        sources=["carpenter"],
        aliases=[
            ("Essential fatty acids", "essential fatty acids", "en"),
            ("Ненасыщенные жирные кислоты", normalize_name("Ненасыщенные жирные кислоты"), "ru"),
        ],
    )
    _insert_drug(
        conn,
        name_en="Oregano essential oil",
        name_ru="Эфирное масло орегано",
        sources=["carpenter"],
        aliases=[
            ("Oregano essential oil", "oregano essential oil", "en"),
            ("Эфирное масло орегано", normalize_name("Эфирное масло орегано"), "ru"),
        ],
    )
    _insert_drug(
        conn,
        name_en="Male",
        name_ru="Самец",
        sources=["carpenter"],
        aliases=[("Male", "male", "en"), ("Самец", "самец", "ru")],
    )
    _insert_drug(
        conn,
        name_en="Guinea pig",
        name_ru="Свинка морская",
        sources=["carpenter"],
        aliases=[
            ("Guinea pig", "guinea pig", "en"),
            ("Свинка морская", normalize_name("Свинка морская"), "ru"),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("config.FORMULARY_DB", str(db_path))
    monkeypatch.setattr("config.FORMULARY_SEARCH_MIN_SCORE", 60.0)
    return db_path


def test_expanded_coamox_query_skips_fragments(fragment_db):
    hits = dosage_service.pick_search_hits(
        search_drugs("Amoxicillin clavulanic acid", limit=8)
    )
    labels = {h.display_name.lower() for h in hits}
    assert "ici" not in labels
    assert "кислота" not in labels
    assert "acid" not in labels


def test_essentiale_does_not_offer_unrelated_words(fragment_db):
    hits = dosage_service.pick_search_hits(search_drugs("Эссенциале", limit=8))
    blob = " ".join(h.display_name.lower() for h in hits)
    assert "самец" not in blob
    assert "орегано" not in blob
    assert "кислот" not in blob
    assert hits == []


def test_guinea_pig_is_not_a_search_hit(fragment_db):
    assert search_drugs("Свинка морская", limit=5) == []


def test_rehome_itopride_does_not_copy_mosapride_doses():
    from scripts.formulary.build_db import rehome_itopride_brands

    drugs = rehome_itopride_brands(
        [
            {
                "canonical_name_en": "Mosapride",
                "canonical_name_ru": "Мозаприд",
                "aliases": ["Мозаприд", "Ганатон", "Итомед", "Итоприд-Вертекс", "Гасмотин"],
                "trade_names": ["Ганатон", "Гасмотин"],
                "doses": [{"dose_min": 0.5, "dose_unit": "mg/kg"}],
            },
            {
                "canonical_name_en": "Domperidone",
                "canonical_name_ru": "Домперидон",
                "aliases": ["Домперидон", "Ганатон", "Мотилиум"],
                "trade_names": ["Ганатон", "Мотилиум"],
                "doses": [],
            },
        ]
    )
    by_en = {drug["canonical_name_en"]: drug for drug in drugs}
    assert "Ганатон" not in by_en["Mosapride"]["aliases"]
    assert "Итомед" not in by_en["Mosapride"]["aliases"]
    assert "Гасмотин" in by_en["Mosapride"]["aliases"]
    assert "Ганатон" not in by_en["Domperidone"]["trade_names"]
    assert "Мотилиум" in by_en["Domperidone"]["trade_names"]
    itopride = by_en["Itopride"]
    assert itopride["doses"] == []
    assert "Ганатон" in itopride["aliases"]
    assert "Итоприд" in itopride["aliases"]


def test_sqlite_itopride_search_is_one_card(tmp_path, monkeypatch):
    from scripts.formulary.build_db import apply_clinic_kb_fixes

    db_path = tmp_path / "itopride.db"
    conn = sqlite3.connect(str(db_path))
    init_schema(conn)
    mosapride_id = _insert_drug(
        conn,
        name_en="Mosapride",
        name_ru="Мозаприд",
        sources=["curated"],
        aliases=[
            ("Мозаприд", "мозаприд", "ru"),
            ("Ганатон", "ганатон", "ru"),
            ("Итомед", "итомед", "ru"),
        ],
    )
    conn.execute(
        """
        INSERT INTO doses (drug_id, dose_min, dose_max, dose_unit, raw_text, source)
        VALUES (?, 0.5, 1.0, 'mg/kg', '0.5-1 мг/кг', 'curated')
        """,
        (mosapride_id,),
    )
    _insert_drug(
        conn,
        name_en="Domperidone",
        name_ru="Домперидон",
        sources=["curated"],
        aliases=[("Домперидон", "домперидон", "ru"), ("Ганатон", "ганатон", "ru")],
    )
    conn.commit()
    apply_clinic_kb_fixes(conn)
    conn.close()

    monkeypatch.setattr("config.FORMULARY_DB", str(db_path))
    monkeypatch.setattr("config.FORMULARY_SEARCH_MIN_SCORE", 60.0)
    ganaton = dosage_service.pick_search_hits(search_drugs("Ганатон", limit=5))
    itopride = dosage_service.pick_search_hits(search_drugs("Итоприд", limit=5))
    assert len(ganaton) == 1
    assert ganaton[0].canonical_name_en == "Itopride"
    assert len(itopride) == 1
    assert itopride[0].drug_id == ganaton[0].drug_id

    check = sqlite3.connect(str(db_path))
    dose_count = check.execute(
        "SELECT COUNT(*) FROM doses WHERE drug_id = ?",
        (ganaton[0].drug_id,),
    ).fetchone()[0]
    check.close()
    assert dose_count == 0


def test_amoxiclav_resolves_to_single_coamox_card(fragment_db):
    hits = dosage_service.search_with_analogs("Амоксиклав", limit=5)
    assert len(hits) == 1
    assert "клавулан" in hits[0].canonical_name_ru.lower() or hits[0].canonical_name_en == "Co-amoxiclav"


def test_pick_search_hits_single_after_dedupe(formulary_db):
    _db, ids = formulary_db
    hits = search_drugs("Ганатон", limit=5)
    picked = dosage_service.pick_search_hits(hits)
    assert len(picked) == 1
    assert picked[0].drug_id == ids["mosapride"]
