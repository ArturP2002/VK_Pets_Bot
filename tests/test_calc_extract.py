"""Calculator free-text extraction heuristics."""
from bot.handlers.calculator import (
    _merge_extracts,
    _naive_extract,
    _normalize_extract,
    _required_missing,
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
