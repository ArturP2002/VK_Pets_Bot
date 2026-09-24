"""Calculator free-text extraction heuristics."""
from bot.handlers.calculator import (
    _merge_extracts,
    _missing_message,
    _naive_extract,
    _normalize_extract,
    _required_missing,
    _show_confirm,
)


def test_naive_extract_owner_amoxicillin():
    text = (
        "У меня кот 4 кг. Врач назначил амоксициллин 12 мг/кг. "
        "Таблетки по 50 мг. Сколько давать?"
    )
    data = _normalize_extract(_naive_extract(text))
    assert data["weight_kg"] == 4.0
    assert data["dose_mg_per_kg"] == 12.0
    assert data["form"] == "tablet"
    assert data["mg_per_unit"] == 50.0
    assert data["species"] == "кот"
    assert data["drug"] and "амоксициллин" in data["drug"].lower()
    assert _required_missing(data) == []


def test_missing_asks_only_weight():
    data = _normalize_extract(
        {
            "dose_mg_per_kg": 10,
            "form": "tablet",
            "mg_per_unit": 50,
            "weight_kg": None,
        }
    )
    assert _required_missing(data) == ["weight_kg"]


def test_merge_fills_gaps_from_naive():
    llm = {"drug": "Amoxicillin", "weight_kg": None, "dose_mg_per_kg": 12, "form": "tablet"}
    naive = {
        "drug": "амоксициллин",
        "weight_kg": 4,
        "dose_mg_per_kg": 12,
        "form": "tablet",
        "mg_per_unit": 50,
    }
    merged = _merge_extracts(llm, naive)
    assert merged["weight_kg"] == 4.0
    assert merged["mg_per_unit"] == 50.0
    assert merged["drug"] == "Amoxicillin"


def test_merge_dose_not_overwritten_when_converting_tablet_strength():
    """
    Scenario:
    - current state: dose_mg_per_kg=10, mg_per_unit=15
    - user says: "пересчитать на 50 мг"
    LLM may wrongly set dose_mg_per_kg=15 (taking previous mg_per_unit as dose).
    We should keep dose_mg_per_kg=10 and update only mg_per_unit=50.
    """
    primary = {
        "drug": "Amoxicillin",
        "weight_kg": None,
        "dose_mg_per_kg": 15,
        "form": "tablet",
        "mg_per_unit": 50,
    }
    fallback = {
        "drug": "amоксициллин",
        "weight_kg": 1,
        "dose_mg_per_kg": 10,
        "form": "tablet",
        "mg_per_unit": 15,
    }
    merged = _merge_extracts(primary, fallback)
    assert merged["dose_mg_per_kg"] == 10
    assert merged["mg_per_unit"] == 50


def test_ampule_without_volume_is_not_tablet_strength():
    text = "нефопам 1,5мг/кг кролику 2,34 кг ампула 10мг"
    data = _normalize_extract(_naive_extract(text))
    assert data["form"] == "solution"
    assert data["mg_per_unit"] is None
    assert data["_ampule_mg"] == 10.0
    assert data["concentration_mg_ml"] is None
    assert data["dose_mg_per_kg"] == 1.5
    assert data["weight_kg"] == 2.34
    assert data["species"] == "кролик"
    missing = _required_missing(data)
    assert missing == ["concentration_mg_ml"]
    message = _missing_message(data, missing)
    assert "объём ампулы" in message
    assert "мг в таблетке" not in message


def test_ampule_with_volume_sets_concentration():
    data = _normalize_extract(_naive_extract("ампула 10 мг/2 мл"))
    assert data["form"] == "solution"
    assert data["mg_per_unit"] is None
    assert data["concentration_mg_ml"] == 5.0
    assert _required_missing(data) == ["weight_kg", "dose_mg_per_kg"]


def test_merge_ignores_llm_tablet_mg_for_ampule(monkeypatch):
    naive = _normalize_extract(_naive_extract("нефопам 1,5мг/кг кролику 2,34 кг ампула 10мг"))
    llm = {
        "drug": "нефопам",
        "species": "кролик",
        "weight_kg": 2.34,
        "dose_mg_per_kg": 1.5,
        "form": "other",
        "mg_per_unit": 10,
        "notes": "ампула 10 мг (объем ампулы не указан)",
    }
    merged = _merge_extracts(llm, naive)
    assert merged["form"] == "solution"
    assert merged["mg_per_unit"] is None
    assert not merged.get("notes")

    captured: dict[str, str] = {}
    monkeypatch.setattr(
        "bot.handlers.calculator.vk.send_message",
        lambda peer, text, keyboard=None: captured.setdefault("text", text),
    )
    monkeypatch.setattr("bot.handlers.calculator.session.set_state", lambda *args, **kwargs: None)
    merged["_ampule_ml"] = 2.0
    merged["concentration_mg_ml"] = 5.0
    _show_confirm(1, 2, merged)
    text = captured["text"]
    assert "мг в таблетке" not in text
    assert "мг в ампуле: 10" in text
    assert "ампула" in text
    assert "концентрация: 5" in text
