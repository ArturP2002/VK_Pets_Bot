"""Tests for AI / cache drug name translation (build-time + runtime)."""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import pytest

from scripts.formulary.build_db import (
    apply_translation_to_drug,
    drug_needs_ai_translation,
    load_name_translation_cache,
    save_name_translation_cache,
    translate_drug_names_batch,
)
from scripts.formulary.common import normalize_name
from scripts.formulary.schema import init_schema
from services.formulary_search import (
    DrugHit,
    DrugRecord,
    resolve_display_name_ru,
)


def test_drug_needs_ai_translation():
    assert drug_needs_ai_translation(
        {"canonical_name_en": "Acetazolamide", "canonical_name_ru": "", "sources": ["bsava"]}
    )
    assert not drug_needs_ai_translation(
        {"canonical_name_en": "Acetazolamide", "canonical_name_ru": "Диакарб", "sources": ["bsava"]}
    )
    assert not drug_needs_ai_translation(
        {"canonical_name_en": "Acetazolamide", "canonical_name_ru": "", "sources": ["manual"]}
    )


def test_apply_translation_does_not_overwrite_manual_ru():
    drug = {
        "canonical_name_en": "Acetazolamide",
        "canonical_name_ru": "Диакарб",
        "aliases": ["Диакарб"],
        "sources": ["manual", "bsava"],
    }
    changed = apply_translation_to_drug(
        drug,
        {"canonical_name_ru": "Ацетазоламид", "search_aliases": ["Диамокс"]},
    )
    assert not changed
    assert drug["canonical_name_ru"] == "Диакарб"


def test_apply_translation_sets_ru_and_aliases():
    drug = {
        "canonical_name_en": "Acetazolamide",
        "canonical_name_ru": "",
        "aliases": ["Diamox"],
        "sources": ["bsava"],
    }
    assert apply_translation_to_drug(
        drug,
        {"canonical_name_ru": "Ацетазоламид", "search_aliases": ["Диамокс"]},
    )
    assert drug["canonical_name_ru"] == "Ацетазоламид"
    assert "Ацетазоламид" in drug["aliases"]
    assert "Диамокс" in drug["aliases"]


def test_translation_jsonl_cache_roundtrip(tmp_path, monkeypatch):
    cache_path = tmp_path / "name_translations.jsonl"
    monkeypatch.setattr(
        "scripts.formulary.build_db._translation_cache_path",
        lambda: cache_path,
    )
    entries = {
        normalize_name("Acetazolamide"): {
            "canonical_name_en": "Acetazolamide",
            "canonical_name_ru": "Ацетазоламид",
            "search_aliases": [],
            "source": "ai",
        }
    }
    save_name_translation_cache(entries)
    loaded = load_name_translation_cache()
    assert loaded[normalize_name("Acetazolamide")]["canonical_name_ru"] == "Ацетазоламид"


def test_translate_batch_uses_cache_without_llm(tmp_path, monkeypatch):
    cache_path = tmp_path / "name_translations.jsonl"
    monkeypatch.setattr(
        "scripts.formulary.build_db._translation_cache_path",
        lambda: cache_path,
    )
    cache = {
        normalize_name("Acetazolamide"): {
            "canonical_name_en": "Acetazolamide",
            "canonical_name_ru": "Ацетазоламид",
            "search_aliases": [],
            "source": "ai",
        }
    }
    save_name_translation_cache(cache)

    llm_mock = MagicMock()
    monkeypatch.setattr("services.llm_client.translate_drug_names", llm_mock)

    drugs = [
        {
            "canonical_name_en": "Acetazolamide",
            "canonical_name_ru": "",
            "aliases": [],
            "sources": ["bsava"],
        }
    ]
    translate_drug_names_batch(drugs, cache=load_name_translation_cache())
    assert drugs[0]["canonical_name_ru"] == "Ацетазоламид"
    llm_mock.assert_not_called()


def test_translate_batch_calls_llm_and_saves_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "name_translations.jsonl"
    monkeypatch.setattr(
        "scripts.formulary.build_db._translation_cache_path",
        lambda: cache_path,
    )

    def fake_translate(batch):
        return [
            {
                "canonical_name_en": d["canonical_name_en"],
                "canonical_name_ru": "Ацетазоламид",
                "search_aliases": ["Диамокс"],
            }
            for d in batch
        ]

    monkeypatch.setattr("services.llm_client.is_configured", lambda: True)
    monkeypatch.setattr("services.llm_client.translate_drug_names", fake_translate)

    drugs = [
        {
            "canonical_name_en": "Acetazolamide",
            "canonical_name_ru": "",
            "aliases": [],
            "action": "carbonic anhydrase inhibitor",
            "sources": ["carpenter"],
        }
    ]
    translate_drug_names_batch(drugs, cache={})
    assert drugs[0]["canonical_name_ru"] == "Ацетазоламид"
    assert "Диамокс" in drugs[0]["aliases"]

    reloaded = load_name_translation_cache()
    assert reloaded[normalize_name("Acetazolamide")]["canonical_name_ru"] == "Ацетазоламид"

    llm_calls = {"count": 0}

    def counting_translate(batch):
        llm_calls["count"] += 1
        return fake_translate(batch)

    monkeypatch.setattr("services.llm_client.translate_drug_names", counting_translate)
    drugs2 = [
        {
            "canonical_name_en": "Acetazolamide",
            "canonical_name_ru": "",
            "aliases": [],
            "sources": ["bsava"],
        }
    ]
    translate_drug_names_batch(drugs2)
    assert drugs2[0]["canonical_name_ru"] == "Ацетазоламид"
    assert llm_calls["count"] == 0


def test_translate_batch_no_api_key_uses_translit(monkeypatch):
    monkeypatch.setattr("services.llm_client.is_configured", lambda: False)
    drugs = [
        {
            "canonical_name_en": "Fentanyl",
            "canonical_name_ru": "",
            "aliases": [],
            "sources": ["carpenter"],
        }
    ]
    translate_drug_names_batch(drugs, cache={})
    assert drugs[0]["canonical_name_ru"]
    assert drugs[0]["_name_translation"]["source"] == "translit"


def _seed_foreign_drug_db(db_path, *, name_ru: str = "") -> int:
    conn = sqlite3.connect(str(db_path))
    init_schema(conn)
    cur = conn.execute(
        """
        INSERT INTO drugs (canonical_name_en, canonical_name_ru, trade_names, sources)
        VALUES (?, ?, ?, ?)
        """,
        ("Acetazolamide", name_ru, json.dumps([]), json.dumps(["bsava"])),
    )
    drug_id = cur.lastrowid
    conn.execute(
        "INSERT INTO drug_aliases(drug_id, alias, alias_norm, lang) VALUES (?,?,?,?)",
        (drug_id, "Acetazolamide", normalize_name("Acetazolamide"), "en"),
    )
    conn.commit()
    conn.close()
    return drug_id


def test_resolve_display_name_from_cache(tmp_path, monkeypatch):
    db_path = tmp_path / "formulary.db"
    drug_id = _seed_foreign_drug_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO drug_name_cache (drug_id, name_ru, aliases_json, source, created_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        """,
        (drug_id, "Ацетазоламид", json.dumps([]), "ai"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("config.FORMULARY_DB", str(db_path))
    hit = DrugHit(
        drug_id=drug_id,
        canonical_name_en="Acetazolamide",
        canonical_name_ru="",
        matched_alias="Acetazolamide",
        score=100.0,
        sources=["bsava"],
    )
    assert resolve_display_name_ru(hit, use_llm=False) == "Ацетазоламид"


def test_resolve_display_name_runtime_llm(tmp_path, monkeypatch):
    db_path = tmp_path / "formulary.db"
    drug_id = _seed_foreign_drug_db(db_path)
    monkeypatch.setattr("config.FORMULARY_DB", str(db_path))

    monkeypatch.setattr(
        "services.llm_client.is_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "services.llm_client.translate_drug_names",
        lambda batch: [
            {
                "canonical_name_en": batch[0]["canonical_name_en"],
                "canonical_name_ru": "Ацетазоламид",
                "search_aliases": ["Диамокс"],
            }
        ],
    )

    record = DrugRecord(
        id=drug_id,
        canonical_name_en="Acetazolamide",
        canonical_name_ru="",
        trade_names=[],
        pom_note="",
        formulations="",
        action="inhibitor",
        use="",
        safety_handling="",
        contraindications="",
        adverse_reactions="",
        drug_interactions="",
        full_text_en="",
        full_text_ru="",
        sources=["bsava"],
        doses=[],
        aliases=["Acetazolamide"],
    )
    name = resolve_display_name_ru(record)
    assert name == "Ацетазоламид"

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT name_ru FROM drug_name_cache WHERE drug_id = ?", (drug_id,)
    ).fetchone()
    assert row[0] == "Ацетазоламид"
    alias_rows = conn.execute(
        "SELECT alias FROM drug_aliases WHERE drug_id = ?", (drug_id,)
    ).fetchall()
    conn.close()
    assert any(r[0] == "Диамокс" for r in alias_rows)


def test_llm_translate_drug_names_parsing(monkeypatch):
    from services import llm_client

    monkeypatch.setattr(
        llm_client,
        "chat_json",
        lambda **kwargs: {
            "translations": [
                {
                    "canonical_name_en": "Trimethoprim",
                    "canonical_name_ru": "Триметоприм",
                    "search_aliases": ["Тримедат"],
                }
            ]
        },
    )
    out = llm_client.translate_drug_names(
        [{"canonical_name_en": "Trimethoprim", "sources": ["bsava"]}]
    )
    assert out[0]["canonical_name_ru"] == "Триметоприм"
    assert "Тримедат" in out[0]["search_aliases"]
