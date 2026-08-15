"""Tests for dose calculator arithmetic and min/max checks."""
import pytest

from services.dose_calculator import (
    calculate,
    check_minmax,
    resolve_dose_from_rows,
    round_to_quarter,
)


def test_round_to_quarter():
    assert round_to_quarter(0.12) == 0.0
    assert round_to_quarter(0.13) == 0.25
    assert round_to_quarter(0.4) == 0.5
    assert round_to_quarter(0.9) == 1.0
    assert round_to_quarter(1.7) == 1.75


def test_tablet_calculation():
    result = calculate(
        weight_kg=2.0,
        dose_mg_per_kg=0.5,
        form="tablet",
        mg_per_unit=1.0,
    )
    assert result.ok
    assert result.total_mg == 1.0
    assert result.tablets == 1.0
    assert "дисклеймер" in result.format_message().lower() or "справочный" in result.format_message().lower()


def test_solution_calculation():
    result = calculate(
        weight_kg=1.0,
        dose_mg_per_kg=10.0,
        form="solution",
        concentration_mg_ml=5.0,
    )
    assert result.ok
    assert result.total_mg == 10.0
    assert result.ml == 2.0


def test_minmax_warning():
    result = calculate(
        weight_kg=1.0,
        dose_mg_per_kg=20.0,
        form="tablet",
        mg_per_unit=10.0,
        dose_min=1.0,
        dose_max=10.0,
    )
    assert result.ok
    assert "выше максимума" in result.warning


def test_check_minmax_in_range():
    msg = check_minmax(5.0, 1.0, 10.0)
    assert "пределах" in msg


def test_resolve_dose_from_rows_prefers_species():
    rows = [
        {
            "taxa": "birds",
            "species_note": "parrot",
            "dose_min": 1.0,
            "dose_max": 3.0,
            "dose_unit": "mg/kg",
            "source": "bsava",
        },
        {
            "taxa": "mammals",
            "species_note": "rabbit",
            "dose_min": 0.2,
            "dose_max": 0.4,
            "dose_unit": "mg/kg",
            "source": "carpenter",
        },
    ]
    mid, dmin, dmax, row = resolve_dose_from_rows(rows, species="кролик")
    assert mid == pytest.approx(0.3)
    assert dmin == 0.2
    assert dmax == 0.4
    assert row["taxa"] == "mammals"
