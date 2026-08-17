"""Dosage deliver: synonyms, empty cards, display title."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from models import User
from scripts.formulary.common import normalize_name
from scripts.formulary.schema import init_schema
from services import dosage_service
from services.formulary_search import (
    apply_display_title,
    dose_row_has_usable_content,
    format_brief_ru,
    get_drug,
    has_usable_dose_data,
    resolve_display_title,
    search_drugs,
)


def _insert_drug(
    conn: sqlite3.Connection,
    *,
    name_en: str,
    name_ru: str,
    sources: list[str],
    aliases: list[tuple[str, str, str]],
    trade_names: list[str] | None = None,
    doses: list[tuple[str, str, str | None, str | None]] | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO drugs (canonical_name_en, canonical_name_ru, trade_names, sources)
        VALUES (?, ?, ?, ?)
        """,
        (name_en, name_ru, json.dumps(trade_names or []), json.dumps(sources)),
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
    for taxa, raw_text, dmin, dmax in doses or []:
        conn.execute(
            """
            INSERT INTO doses (drug_id, taxa, raw_text, dose_min, dose_max, dose_unit, source)
            VALUES (?, ?, ?, ?, ?, 'mg/kg', 'carpenter')
            """,
            (drug_id, taxa, raw_text, dmin, dmax),
        )
    return drug_id


@pytest.fixture
def formulary_db(tmp_path, monkeypatch):
    db_path = tmp_path / "formulary.db"
    conn = sqlite3.connect(str(db_path))
    init_schema(conn)

    ids = {
        "nembutal_manual": _insert_drug(
            conn,
            name_en="Nembutal",
            name_ru="Нембутал",
            sources=["manual"],
            trade_names=["Nembutal"],
            aliases=[
                ("Nembutal", "nembutal", "en"),
                ("Нембутал", "нембутал", "ru"),
            ],
            doses=[("birds", "100 mg/kg IV", 100.0, 100.0)],
        ),
        "pentobarbital": _insert_drug(
            conn,
            name_en="Pentobarbital sodium",
            name_ru="Пентобарбитал натрия",
            sources=["carpenter"],
            trade_names=["Nembutal"],
            aliases=[
                ("Pentobarbital sodium", normalize_name("Pentobarbital sodium"), "en"),
                ("Нембутал", "нембутал", "ru"),
            ],
            doses=[
                (
                    "birds",
                    "IV and ICE; dose range not specified",
                    None,
                    None,
                )
            ],
        ),
        "empty_only": _insert_drug(
            conn,
            name_en="EmptyDrug",
            name_ru="ПустойПреп",
            sources=["carpenter"],
            aliases=[("ПустойПреп", normalize_name("ПустойПреп"), "ru")],
            doses=[("birds", "данных по диапазону доз не указано", None, None)],
        ),
    }
    conn.commit()
    conn.close()

    monkeypatch.setattr("config.FORMULARY_DB", str(db_path))
    monkeypatch.setattr("config.FORMULARY_SEARCH_MIN_SCORE", 60.0)
    return db_path, ids


def test_nembutal_synonyms_collapse_to_one(formulary_db):
    _db, ids = formulary_db
    hits = search_drugs("Нембутал", limit=5)
    assert len(hits) == 1
    assert hits[0].drug_id == ids["nembutal_manual"]
    picked = dosage_service.pick_search_hits(hits)
    assert len(picked) == 1


def test_dose_row_vague_is_not_usable():
    assert not dose_row_has_usable_content(
        {"raw_text": "IV; dose range not specified", "dose_min": None, "dose_max": None}
    )
    assert dose_row_has_usable_content(
        {"raw_text": "2–4 mg/kg PO", "dose_min": 2.0, "dose_max": 4.0}
    )


def test_has_usable_dose_data(formulary_db):
    _db, ids = formulary_db
    good = get_drug(ids["nembutal_manual"])
    empty = get_drug(ids["empty_only"])
    assert good is not None and has_usable_dose_data(good)
    assert empty is not None and not has_usable_dose_data(empty)


def test_resolve_display_title_prefers_user_query(formulary_db):
    _db, ids = formulary_db
    drug = get_drug(ids["pentobarbital"])
    assert drug is not None
    assert resolve_display_title("Нембутал", drug=drug) == "Нембутал"


def test_apply_display_title_replaces_header():
    text = "💊 Пентобарбитал\n\nДозы:\n• 1 мг/кг"
    out = apply_display_title(text, "Нембутал")
    assert out.startswith("💊 Нембутал")
    assert "Пентобарбитал" not in out.split("\n")[0]


def test_format_brief_ru_uses_display_title(formulary_db):
    _db, ids = formulary_db
    drug = get_drug(ids["nembutal_manual"])
    assert drug is not None
    text = format_brief_ru(drug, display_title="Нембутал")
    assert text.startswith("💊 Нембутал")


def test_deliver_brief_empty_card_not_counted(formulary_db, memory_db, monkeypatch):
    _db, ids = formulary_db
    recorded: list[tuple] = []

    def _record(user_id, **kwargs):
        recorded.append((user_id, kwargs))

    monkeypatch.setattr("services.dosage_access.record_usage", _record)
    user = User.create(vk_id=900001, created_at=datetime.utcnow())
    outcome = dosage_service.deliver_brief(
        user,
        ids["empty_only"],
        user_query="ПустойПреп",
    )
    assert outcome.kind == "miss"
    assert outcome.counted is False
    assert not recorded
    assert "числовых доз" in outcome.text
    assert outcome.text.startswith("💊 ПустойПреп")
