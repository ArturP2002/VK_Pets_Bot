"""Unit tests for formulary parsers / search helpers (no full PDF ingest)."""
from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.formulary.common import normalize_name, parse_dose_numbers, transliterate_ru
from scripts.formulary.extract_bsava import clean_bsava_text, parse_monograph
from scripts.formulary.extract_manual_ru import parse_block
from scripts.formulary.schema import init_schema


def test_normalize_and_transliterate():
    assert normalize_name("Diamox®") == "diamox"
    assert "diakarb" in transliterate_ru("Диакарб")


def test_parse_dose_numbers():
    assert parse_dose_numbers("2.5–5.0 mg/kg i.m. q3–7days")[:3] == (2.5, 5.0, "mg/kg")
    assert parse_dose_numbers("6 mg/l by immersion")[:3] == (6.0, 6.0, "mg/l")
    assert parse_dose_numbers("10-15 мг/кг")[:3] == (10.0, 15.0, "mg/kg")


def test_bsava_acetazolamide_monograph():
    body = """\
(Diamox*, Diamox SR*) POM
Formulations: Injectable: 500 mg vial. Oral: 250 mg tablets.
Action: Systemic carbonic anhydrase inhibitor.
Use: Treatment of gas bubble disease in seahorses.
Safety and handling: Normal precautions should be observed.
Contraindications: No information available.
Adverse reactions: Neurological signs and mortality have been associated with high doses.
Drug interactions: No information available.
DOSES
Fish: Seahorses: 6 mg/l by immersion, change daily for 4–8 days; 2.5–5.0 mg/kg i.m. q3–7days.
Mammals, Birds, Reptiles, Amphibians: No information available.
"""
    drug = parse_monograph("Acetazolamide", body)
    assert drug["canonical_name_en"] == "Acetazolamide"
    assert "Diamox" in drug["trade_names"][0]
    assert drug["pom_note"].startswith("POM")
    assert "carbonic anhydrase" in drug["action"]
    assert any(d["taxa"] == "fish" and d["dose_unit"] == "mg/l" for d in drug["doses"])
    assert any(
        d["taxa"] == "fish" and d["dose_min"] == 2.5 and d["dose_max"] == 5.0
        for d in drug["doses"]
    )


def test_clean_bsava_strips_letter_column():
    raw = "   F       (Diamox*) POM\n  G        Formulations: Oral tablets.\n"
    cleaned = clean_bsava_text(raw)
    assert "Formulations" in cleaned
    assert "\nF\n" not in cleaned


def test_manual_ru_diakarb_block():
    lines = [
        "Диакарб",
        "Активное вещество: Ацетазоламид (Acetazolamide)",
        "Аналоги: Диамокс (Diamox®), Дазамид",
        "Описание:Диуретик, ингибитор карбоангидразы.",
        "Дозы:",
        "При глаукоме: 5–10 мг/кг перорально, каждые 8–12 часов.(млекопитающие)",
        "Противопоказания:",
        "Аллергическая реакция на ацетазоламид.",
    ]
    drug = parse_block(lines)
    assert drug is not None
    assert drug["canonical_name_ru"] == "Диакарб"
    assert drug["canonical_name_en"] == "Acetazolamide"
    assert any("Диамокс" in t or "Diamox" in t for t in drug["trade_names"])
    assert drug["doses"]
    assert drug["contraindications"]


def test_schema_and_search_smoke(tmp_path, monkeypatch):
    db_path = tmp_path / "formulary.db"
    conn = sqlite3.connect(str(db_path))
    init_schema(conn)
    cur = conn.execute(
        """
        INSERT INTO drugs (
            canonical_name_en, canonical_name_ru, trade_names, sources
        ) VALUES (?, ?, ?, ?)
        """,
        ("Acetazolamide", "Диакарб", json.dumps(["Диамокс"]), json.dumps(["bsava", "manual"])),
    )
    drug_id = cur.lastrowid
    for alias, lang in [
        ("Acetazolamide", "en"),
        ("Диакарб", "ru"),
        ("diakarb", "tr"),
        ("Diamox", "en"),
    ]:
        from scripts.formulary.common import normalize_name

        norm = normalize_name(alias)
        cur = conn.execute(
            "INSERT INTO drug_aliases(drug_id, alias, alias_norm, lang) VALUES (?,?,?,?)",
            (drug_id, alias, norm, lang),
        )
        conn.execute(
            "INSERT INTO drugs_fts(rowid, alias_norm, display_name) VALUES (?,?,?)",
            (cur.lastrowid, norm, "Диакарб"),
        )
    conn.execute(
        """
        INSERT INTO doses (drug_id, taxa, raw_text, dose_min, dose_max, dose_unit, source)
        VALUES (?, 'fish', '6 mg/l immersion', 6, 6, 'mg/l', 'bsava')
        """,
        (drug_id,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("config.FORMULARY_DB", str(db_path))
    monkeypatch.setattr("config.FORMULARY_SEARCH_MIN_SCORE", 60.0)

    from services.formulary_search import get_drug, search_drugs

    hits = search_drugs("Диакарб", limit=3)
    assert hits and hits[0].drug_id == drug_id
    hits2 = search_drugs("Acetazolamide", limit=3)
    assert hits2 and hits2[0].canonical_name_en == "Acetazolamide"
    rec = get_drug(drug_id)
    assert rec is not None
    assert rec.doses[0]["dose_unit"] == "mg/l"


def test_llm_parse_json_object():
    from services.llm_client import parse_json_object

    assert parse_json_object('{"a": 1}')["a"] == 1
    assert parse_json_object("```json\n{\"drug\": \"x\"}\n```")["drug"] == "x"
