"""Tests for dose calculator arithmetic, dissolution, and min/max checks."""
import pytest

from services.dose_calculator import (
    CalcResult,
    apply_dissolution_volume_margin,
    calculate,
    check_minmax,
    compute_dissolution,
    resolve_dose_from_rows,
    round_convenient_ml,
    round_to_quarter,
    v_max_for_weight,
)


def test_round_to_quarter():
    assert round_to_quarter(0.12) == 0.0
    assert round_to_quarter(0.13) == 0.25
    assert round_to_quarter(0.4) == 0.5
    assert round_to_quarter(0.9) == 1.0
    assert round_to_quarter(1.7) == 1.75


def test_v_max_for_weight():
    assert v_max_for_weight(0.057)[0] == pytest.approx(0.1)
    assert v_max_for_weight(0.2)[0] == pytest.approx(0.5)
    assert v_max_for_weight(0.8)[0] == pytest.approx(1.0)
    assert v_max_for_weight(3.0)[0] == pytest.approx(2.0)
    v_max, warning = v_max_for_weight(12.0)
    assert v_max is None
    assert "10 кг" in warning


def test_round_convenient_ml():
    assert round_convenient_ml(1.53125) == pytest.approx(1.5)
    assert round_convenient_ml(0.0855) == pytest.approx(0.09)


def test_apply_dissolution_volume_margin():
    # 1.75 ml from spec → ~1.5 ml after 12.5 % margin and rounding
    assert apply_dissolution_volume_margin(1.75) == pytest.approx(1.5)


def test_compute_dissolution_hamster_example():
    result = compute_dissolution(total_mg=0.285, mg_per_unit=5.0, v_max=0.1)
    assert result is not None
    fraction, dissolve_volume, draw_volume = result
    assert fraction == 1.0
    assert dissolve_volume == pytest.approx(1.5)
    assert draw_volume == pytest.approx(0.0855, rel=1e-3)


def test_hamster_dissolution_via_calculate():
    """Spec example: hamster 57 g, 5 mg/kg, tablet 5 mg."""
    result = calculate(
        weight_kg=0.057,
        dose_mg_per_kg=5.0,
        form="tablet",
        mg_per_unit=5.0,
    )
    assert result.ok
    assert result.method == "dissolution"
    assert result.total_mg == pytest.approx(0.285)
    assert result.tablet_fraction == 1.0
    assert result.dissolve_volume_ml == pytest.approx(1.5)
    assert result.draw_volume_ml == pytest.approx(0.0855, rel=1e-3)

    message = result.format_message()
    assert "растворение таблетки" in message.lower()
    assert "1.5 мл" in message
    assert "0.09 мл" in message
    assert "взбалтывать" in message.lower()
    assert "48 часов" in message.lower()
    assert "справочный" in message.lower()
    assert "лимит шприца" not in message.lower()
    assert "расчётная доля таблетки" not in message.lower()


def test_physical_tablet_when_fraction_at_least_quarter():
    result = calculate(
        weight_kg=2.0,
        dose_mg_per_kg=0.5,
        form="tablet",
        mg_per_unit=1.0,
    )
    assert result.ok
    assert result.method == "physical_tablet"
    assert result.total_mg == 1.0
    assert result.tablets == 1.0
    assert result.dissolve_volume_ml is None


def test_dissolution_preferred_for_half_tablet_dose():
    result = calculate(
        weight_kg=1.0,
        dose_mg_per_kg=0.5,
        form="tablet",
        mg_per_unit=1.0,
    )
    assert result.ok
    assert result.method == "dissolution"
    assert result.dissolve_volume_ml is not None
    assert result.draw_volume_ml is not None
    assert "растворение таблетки" in result.format_message().lower()


def test_dissolution_preferred_for_three_quarters_tablet_dose():
    result = calculate(
        weight_kg=1.0,
        dose_mg_per_kg=0.75,
        form="tablet",
        mg_per_unit=1.0,
    )
    assert result.ok
    assert result.method == "dissolution"
    assert result.dissolve_volume_ml is not None
    assert result.draw_volume_ml is not None
    assert "растворение таблетки" in result.format_message().lower()


def test_dissolution_when_fraction_below_quarter():
    result = calculate(
        weight_kg=0.05,
        dose_mg_per_kg=2.0,
        form="tablet",
        mg_per_unit=10.0,
    )
    assert result.ok
    assert result.method == "dissolution"
    assert result.total_mg == pytest.approx(0.1)
    assert (result.total_mg / 10.0) < 0.25


def test_off_quarter_036_uses_dissolution_not_one_quarter():
    """1 mg/kg × 1.8 kg, 5 mg tablet → 0.36 tab, not ¼."""
    result = calculate(
        weight_kg=1.8,
        dose_mg_per_kg=1.0,
        form="tablet",
        mg_per_unit=5.0,
    )
    assert result.ok
    assert result.method == "dissolution"
    message = result.format_message()
    assert "¼" not in message
    assert "0.36" in message
    assert "точная доля" in message.lower()
    assert "растворение таблетки" in message.lower()
    assert "лимит шприца" not in message.lower()


def test_off_quarter_035_uses_dissolution_not_one_quarter():
    """5 mg/kg × 0.35 kg, 5 mg tablet → 0.35 tab, with dilution steps."""
    result = calculate(
        weight_kg=0.35,
        dose_mg_per_kg=5.0,
        form="tablet",
        mg_per_unit=5.0,
    )
    assert result.ok
    assert result.method == "dissolution"
    message = result.format_message()
    assert "¼" not in message
    assert "0.35" in message
    assert "растворение таблетки" in message.lower()
    assert "растворить" in message.lower()


def test_near_quarter_stays_physical():
    result = calculate(
        weight_kg=1.0,
        dose_mg_per_kg=2.6,
        form="tablet",
        mg_per_unit=10.0,
    )
    assert result.ok
    assert result.method == "physical_tablet"
    assert result.tablets == 0.25


def test_physical_tablet_at_exact_quarter():
    result = calculate(
        weight_kg=1.0,
        dose_mg_per_kg=2.5,
        form="tablet",
        mg_per_unit=10.0,
    )
    assert result.ok
    assert result.method == "physical_tablet"
    assert result.tablets == 0.25


def test_dissolution_impossible_warning():
    """Very small animal + high dose may exceed syringe limit even at V=10 ml."""
    result = calculate(
        weight_kg=0.05,
        dose_mg_per_kg=50.0,
        form="tablet",
        mg_per_unit=1.0,
    )
    assert result.ok
    assert result.method in ("dissolution_failed", "physical_tablet")
    message = result.format_message().lower()
    if result.method == "dissolution_failed":
        assert "растворением" in message or "¼" in message


def test_solution_calculation():
    result = calculate(
        weight_kg=1.0,
        dose_mg_per_kg=10.0,
        form="solution",
        concentration_mg_ml=5.0,
    )
    assert result.ok
    assert result.method == "solution"
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
    assert msg == ""


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


def test_guinea_pig_ganaton_round_dissolution():
    result = calculate(
        weight_kg=0.5,
        dose_mg_per_kg=10.0,
        form="tablet",
        mg_per_unit=50.0,
    )
    assert result.ok
    assert result.method == "dissolution"
    assert result.total_mg == pytest.approx(5.0)
    assert result.dissolve_volume_ml == pytest.approx(5.0)
    assert result.draw_volume_ml == pytest.approx(0.5)
    message = result.format_message()
    assert "пределах справочника" not in message
    assert "5 мл" in message
    assert "0.5 мл" in message


def test_fmt_ml_rounds_to_hundredths():
    assert CalcResult._fmt_ml(1.5156) == "1.52"
    assert CalcResult._fmt_ml(0.7578) == "0.76"
    assert CalcResult._fmt_ml(2.0) == "2"
    assert CalcResult._fmt_amount(0.7578) == "0.76"


def test_resolve_dose_prefers_guinea_pig_not_crab():
    rows = [
        {
            "taxa": "invertebrates",
            "species_note": "Pacific white shrimp",
            "dose_min": 100.0,
            "dose_max": 100.0,
            "dose_unit": "mg/kg",
            "source": "carpenter",
        },
        {
            "taxa": "mammals",
            "species_note": "Most species",
            "dose_min": 15.0,
            "dose_max": 30.0,
            "dose_unit": "mg/kg",
            "source": "carpenter",
        },
    ]
    mid, dmin, dmax, row = resolve_dose_from_rows(rows, species="морская свинка")
    assert row["taxa"] == "mammals"
    assert dmin == 15.0
    assert 100.0 not in {dmin, dmax, mid}


def test_resolve_dose_prefers_guinea_pig_metoclopramide_over_manual():
    rows = [
        {
            "taxa": "mammals",
            "species_note": "Most species",
            "dose_min": 0.2,
            "dose_max": 1.0,
            "dose_unit": "mg/kg",
            "source": "carpenter",
        },
        {
            "taxa": "mammals",
            "species_note": "Guinea pigs / antiemetic",
            "dose_min": 0.5,
            "dose_max": 1.0,
            "dose_unit": "mg/kg",
            "source": "carpenter",
        },
        {
            "taxa": "mammals",
            "species_note": "",
            "dose_min": 6.0,
            "dose_max": 20.0,
            "dose_unit": "mg/kg",
            "source": "manual",
        },
    ]
    mid, dmin, dmax, row = resolve_dose_from_rows(rows, species="морская свинка")
    assert dmin == pytest.approx(0.5)
    assert dmax == pytest.approx(1.0)
    assert "Guinea" in (row.get("species_note") or "")


def test_compute_dissolution_reduces_f_when_v_exceeds_cap():
    # Large dose relative to v_max → need f=0.5 or 0.25
    result = compute_dissolution(total_mg=50.0, mg_per_unit=100.0, v_max=2.0)
    assert result is not None
    fraction, dissolve_volume, draw_volume = result
    assert fraction in (1.0, 0.5, 0.25)
    assert dissolve_volume <= 10.0
    assert draw_volume <= 2.0 + 1e-9
