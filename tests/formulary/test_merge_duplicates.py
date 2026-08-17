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


def test_merge_aciclovir_spelling_variants():
    merged = merge_sources(
        [_card("Aciclovir", source="bsava")],
        [_card("Acyclovir")],
        [],
    )
    assert len(merged) == 1
    assert merged[0]["canonical_name_en"] in {"Aciclovir", "Acyclovir"}


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
