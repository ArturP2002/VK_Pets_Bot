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
