"""Merge duplicate INN cards (no PDF, no LLM)."""
from __future__ import annotations

import sqlite3

from scripts.formulary.build_db import inn_match_key, merge_sources, write_sqlite
from scripts.formulary.common import empty_drug


def _card(
    en: str = "",
    ru: str = "",
    *,
    source: str = "carpenter",
    trade_names: list[str] | None = None,
    aliases: list[str] | None = None,
    doses: list[dict] | None = None,
) -> dict:
    drug = empty_drug(canonical_name_en=en, canonical_name_ru=ru, source=source)
    if trade_names:
        drug["trade_names"] = list(trade_names)
    if aliases:
        drug["aliases"] = list(aliases)
    if doses:
        drug["doses"] = list(doses)
    return drug


def _dose(raw: str, source: str = "carpenter") -> dict:
    return {"raw_text": raw, "source": source, "taxa": "other"}


def _names(merged: list[dict]) -> set[str]:
    return {(d.get("canonical_name_en") or "").strip() for d in merged}


def test_inn_match_key_strips_suffixes_and_trades():
    assert inn_match_key("Enrofloxacin") == "enrofloxacin"
    assert inn_match_key("Enrofloxacin (cont'd)") == "enrofloxacin"
    assert inn_match_key("Enrofloxacin (cont’d") == "enrofloxacin"
    assert inn_match_key("Enrofloxacin (Baytril)") == "enrofloxacin"
    assert inn_match_key("Enrofloxacin (Baytril") == "enrofloxacin"
    assert inn_match_key("Энрофлоксацин (продолжение)") == inn_match_key("Энрофлоксацин")
    assert inn_match_key("Энрофлоксацин (Байтрил)") == inn_match_key("Энрофлоксацин")
    assert inn_match_key("acepromazine (A") == inn_match_key("Acepromazine")
    assert inn_match_key("Aciclovir") == inn_match_key("Acyclovir")
    assert inn_match_key("Acriflavin") == inn_match_key("Acriflavine")
    assert inn_match_key("Famcyclovir") == inn_match_key("Famciclovir")
    assert inn_match_key("Aciclovir") != inn_match_key("Famciclovir")
    assert inn_match_key("Meloxicam") != inn_match_key("Meloxicam prolonged-release")
    assert inn_match_key("Meloxicam") != inn_match_key("Meloxicam SR")
    assert inn_match_key("Fluoroquinolones (enrofloxacin") != inn_match_key("Enrofloxacin")
    assert inn_match_key("Sulfamethoxazole + Trimethoprim") == inn_match_key(
        "Trimethoprim/sulfamethoxazole"
    )
    assert inn_match_key("Бисептол") == inn_match_key("Co-trimoxazole")
    assert inn_match_key("Bactrim") == inn_match_key("sulfamethoxazole trimethoprim")
    assert inn_match_key("Trimethoprim/Sulphonamide") == inn_match_key("Trimethoprim/sulfa")


def test_merge_enrofloxacin_continuation_and_baytril(tmp_path):
    carpenter = [
        _card("Enrofloxacin", doses=[_dose("5 mg/kg i.m.")]),
        _card("Enrofloxacin (cont'd)", doses=[_dose("10 mg/kg p.o.")]),
        _card("Enrofloxacin (Baytril", doses=[_dose("15 mg/kg")]),
    ]
    manual = [
        _card("Enrofloxacin", "Энрофлоксацин", source="manual"),
        _card("", "Энрофлоксацин (продолжение)", source="manual"),
        _card("Enrofloxacin (Baytril)", "Энрофлоксацин (Байтрил)", source="manual"),
    ]
    merged = merge_sources([], carpenter, manual)
    assert len(merged) == 1
    card = merged[0]
    assert card["canonical_name_en"] == "Enrofloxacin"
    assert card["canonical_name_ru"] == "Энрофлоксацин"
    assert len(card["doses"]) == 3
    trades_and_aliases = {
        t.lower() for t in (card.get("trade_names") or []) + (card.get("aliases") or [])
    }
    assert "baytril" in trades_and_aliases
    assert any("байтрил" in t.lower() for t in trades_and_aliases)

    stats = write_sqlite(merged, tmp_path / "formulary.db")
    assert stats["drugs"] == 1
    assert stats["doses"] == 3
    conn = sqlite3.connect(str(tmp_path / "formulary.db"))
    row = conn.execute("SELECT canonical_name_en, canonical_name_ru FROM drugs").fetchone()
    conn.close()
    assert row == ("Enrofloxacin", "Энрофлоксацин")


def test_merge_ru_en_amlodipine_fold(tmp_path):
    """Cyrillic manual card must merge with Latin BSAVA/Carpenter INN."""
    bsava = [_card("Amlodipine", source="bsava", doses=[_dose("0.1 mg/kg", "bsava")])]
    carpenter = [_card("Amlodipine", doses=[_dose("0.2 mg/kg")])]
    manual = [_card("Амлодипин", "Амлодипин", source="manual", doses=[_dose("0.3 mg/kg", "manual")])]
    curated = [
        _card(
            "Амлодипин",
            "Амлодипин",
            source="curated",
            doses=[_dose("0.25 mg/kg", "curated")],
        )
    ]
    for row in curated:
        row["status"] = "verified"
        row["stable_key"] = "amlodipine"
        row["_stable_key"] = "amlodipine"

    merged = merge_sources(bsava, carpenter, manual, curated)
    amlo = [d for d in merged if "амло" in (d.get("canonical_name_en") or "").casefold()
            or "amlo" in (d.get("canonical_name_en") or "").casefold()
            or "амло" in (d.get("canonical_name_ru") or "").casefold()]
    assert len(amlo) == 1
    card = amlo[0]
    assert card["canonical_name_en"] == "Amlodipine"
    assert card["canonical_name_ru"] == "Амлодипин"

    stats = write_sqlite(merged, tmp_path / "formulary.db")
    assert stats["drugs"] == len(merged)
    conn = sqlite3.connect(str(tmp_path / "formulary.db"))
    names = [r[0] for r in conn.execute("SELECT canonical_name_en FROM drugs").fetchall()]
    conn.close()
    assert len(names) == len({n.casefold() for n in names})


def test_collapse_duplicate_en_names_casefold():
    from scripts.formulary.build_db import collapse_duplicate_en_names

    a = _card("Амлодипин", "Амлодипин", source="manual", doses=[_dose("1")])
    b = _card("амлодипин", "Амлодипин", source="bsava", doses=[_dose("2", "bsava")])
    out = collapse_duplicate_en_names([a, b])
    assert len(out) == 1
    assert len(out[0]["doses"]) == 2


def test_do_not_merge_aciclovir_with_famciclovir():
    merged = merge_sources(
        [_card("Aciclovir", source="bsava")],
        [_card("Famciclovir")],
        [],
    )
    assert len(merged) == 2
    assert _names(merged) == {"Aciclovir", "Famciclovir"}


def test_merge_famciclovir_famcyclovir():
    merged = merge_sources(
        [_card("Famciclovir", source="bsava")],
        [_card("Famcyclovir")],
        [],
    )
    assert len(merged) == 1


def test_do_not_merge_meloxicam_formulation_variants():
    merged = merge_sources(
        [_card("Meloxicam", source="bsava")],
        [
            _card("Meloxicam prolonged-release"),
            _card("Meloxicam SR"),
            _card("Meloxicam, sustained"),
        ],
        [],
    )
    names = _names(merged)
    assert "Meloxicam" in names
    assert "Meloxicam prolonged-release" in names
    assert len(merged) == 4


def test_merge_meloxicam_continuation_only():
    merged = merge_sources(
        [_card("Meloxicam", source="bsava")],
        [_card("Meloxicam (cont’d")],
        [],
    )
    assert len(merged) == 1
    assert merged[0]["canonical_name_en"] == "Meloxicam"


def test_merge_acepromazine_truncated_crumb():
    merged = merge_sources(
        [],
        [_card("Acepromazine"), _card("acepromazine (A")],
        [],
    )
    assert len(merged) == 1
    assert merged[0]["canonical_name_en"] == "Acepromazine"


def test_merge_acriflavin_variants():
    merged = merge_sources(
        [],
        [_card("Acriflavine"), _card("Acriflavin")],
        [],
    )
    assert len(merged) == 1


def test_do_not_merge_class_heading_with_inn():
    merged = merge_sources(
        [],
        [_card("Enrofloxacin"), _card("Fluoroquinolones (enrofloxacin")],
        [],
    )
    assert len(merged) == 2


def test_merge_tmp_smx_aliases_into_one_card():
    merged = merge_sources(
        [],
        [
            _card("Sulfamethoxazole + Trimethoprim", doses=[_dose("100 mg/kg shrimp")]),
            _card("Trimethoprim/sulfa", doses=[_dose("15 mg/kg mammals")]),
            _card("Biseptol", doses=[_dose("30 mg/kg")]),
        ],
        [_card("Co-trimoxazole", "Бисептол", source="manual", doses=[_dose("20 мг/кг")])],
    )
    assert len(merged) == 1
    card = merged[0]
    assert len(card["doses"]) == 4
    blob = " ".join(
        [card.get("canonical_name_en") or "", card.get("canonical_name_ru") or ""]
        + list(card.get("aliases") or [])
        + list(card.get("trade_names") or [])
    ).lower()
    assert "biseptol" in blob or "бисептол" in blob
