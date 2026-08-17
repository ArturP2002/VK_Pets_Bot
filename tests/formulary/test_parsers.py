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


def test_strip_markdown_for_chat():
    from services.llm_client import strip_markdown_for_chat

    raw = (
        "## Фентанил\n\n"
        "**Источник:** Carpenter\n\n"
        "### Дозы\n"
        "- Мыши: 0.025–0.6 мг/кг\n"
        "- Общие: **5–10** мкг/кг\n"
    )
    clean = strip_markdown_for_chat(raw)
    assert "##" not in clean
    assert "**" not in clean
    assert "###" not in clean
    assert "Фентанил" in clean
    assert "• Мыши:" in clean
    assert "5–10" in clean


def test_format_brief_ru_labels_and_doses():
    from services.formulary_search import DrugRecord, format_brief_ru

    drug = DrugRecord(
        id=1,
        canonical_name_en="Fentanyl",
        canonical_name_ru="",
        trade_names=[],
        pom_note="",
        formulations="",
        action="",
        use="",
        safety_handling="",
        contraindications="",
        adverse_reactions="",
        drug_interactions="",
        full_text_en="",
        full_text_ru="",
        sources=["carpenter"],
        doses=[
            {
                "taxa": "mammals",
                "species_note": "Mice",
                "raw_text": "0.025-0.6 mg/kg SC",
                "dose_min": 0.025,
                "dose_max": 0.6,
                "dose_unit": "mg/kg",
                "source": "carpenter",
            }
        ],
        aliases=["Fentanyl"],
    )
    text = format_brief_ru(drug)
    assert "Action:" not in text
    assert "DOSES:" not in text
    assert "Млекопитающие" in text
    assert "Carpenter" in text or "carpenter" not in text.lower() or "Carpenter" in text
    assert "Дозы:" in text
    assert "0.025-0.6 mg/kg" in text
    assert "Калькулятор дозы" in text


def test_carpenter_junk_agent_names_rejected():
    from scripts.formulary.extract_carpenter import _clean_agent_name, is_junk_agent_name

    junk = [
        "2nd-degree AV block",
        "3rd-degree AV block",
        "1st-degree AV block",
        "Bundle branch block",
        "Aerobic bacteria",
        "Anaerobic bacteria",
        "A/G ratio",
        "A/G ratiob",
        "A) + midazolam (Mi",
        "F/f) + midazolam (Mi",
        "K) + midazolam (Mi",
        "K) + fentanyl (F",
        "alfaxalone (A",
        "alfaxalone (Al",
        "acepromazine (A",
        "African Green",
        "African Grey",
        "Amazon parrot",
        "Amazon parrots",
        "Eclectus parrot",
        "Amazona spp.b",
        "Agent",
        "Agent(s",
        "Measurements",
        "TABLE9-2",
        "TABL E 1-1",
        "C ontents",
    ]
    for name in junk:
        assert is_junk_agent_name(name), f"expected junk: {name!r}"
        assert _clean_agent_name(name) == "", f"expected reject: {name!r}"


def test_carpenter_real_drugs_kept():
    from scripts.formulary.extract_carpenter import _clean_agent_name

    keep = [
        "Acepromazine",
        "Alfaxalone",
        "Acyclovir",
        "Enrofloxacin",
        "Meloxicam",
        "Phenoxyethanol",
        "2-phenoxyethanol",
        "Amoxicillin",
        "Midazolam",
        "Ketamine",
        "Malachite green",
        "Hydrogen peroxide",
        "Cefovecin (Convenia",
        "Afoxolaner (A) + milbemycin",
        "Imidacloprid 10% + moxidectin",
        "Eugenol) (cont’d",
    ]
    for name in keep:
        cleaned = _clean_agent_name(name)
        expected = "Eugenol" if name.startswith("Eugenol)") else name
        assert cleaned == expected, f"expected keep: {name!r} → {cleaned!r}"


def test_carpenter_parse_tables_filters_junk():
    from scripts.formulary.extract_carpenter import parse_tables

    text = """
CHAPTER 4 Reptiles
TABLE 4-1 Antimicrobial Agents Used in Reptiles
Agent                    Dosage                    Comments
Acepromazine             0.1 mg/kg IM              African Grey / sedation
Alfaxalone               5 mg/kg IV                anesthesia
2nd-degree AV block      Long PR intervals         Anesthetics
3rd-degree AV block      Escape ventricular rhythm Severe cardiomegaly
Aerobic bacteria         Aminoglycoside with a penicillin
A/G ratio                0.6–1.6                   lab value
African Grey             45-53                     parrot values
African Green            Common                    primate values
A) + midazolam (Mi       1 mg/kg SC, IM            sedation
alfaxalone (A            5 mg/kg SC                truncated
acepromazine (A          0.5 mg/kg IM              truncated
Acyclovir                80 mg/kg PO               herpes
Enrofloxacin             10 mg/kg IM               gram-negative
Meloxicam                0.2 mg/kg IM              NSAID
Phenoxyethanol           0.1-0.5 mL/L              anesthesia
"""
    drugs = parse_tables(text)
    names = {d["canonical_name_en"] for d in drugs}
    for kept in (
        "Acepromazine",
        "Alfaxalone",
        "Acyclovir",
        "Enrofloxacin",
        "Meloxicam",
        "Phenoxyethanol",
    ):
        assert kept in names, names
    ace = next(d for d in drugs if d["canonical_name_en"] == "Acepromazine")
    assert any(x.get("species_note") == "African Grey" for x in ace["doses"])
    for rejected in (
        "2nd-degree AV block",
        "3rd-degree AV block",
        "Aerobic bacteria",
        "A/G ratio",
        "African Grey",
        "African Green",
        "A) + midazolam (Mi",
        "alfaxalone (A",
        "acepromazine (A",
    ):
        assert rejected not in names, names


def test_carpenter_skips_non_drug_tables():
    from scripts.formulary.extract_carpenter import parse_tables

    text = """
CHAPTER 5 Birds
TABLE 5-19 Hematologic and Biochemical Values of Select Psittaciformes
African Grey             45-53                     Measurement
A/G ratio                0.6-1.6                   lab
TABLE 5-51 Select Arrhythmias and Some Documented Causes in Birds
2nd-degree AV block      Long PR intervals         Anesthetics
TABLE 5-1 Antimicrobial Agents Used in Birds
Enrofloxacin             10 mg/kg IM               infection
Acepromazine             0.1 mg/kg IM              sedation
"""
    drugs = parse_tables(text)
    names = {d["canonical_name_en"] for d in drugs}
    assert names == {"Enrofloxacin", "Acepromazine"}
