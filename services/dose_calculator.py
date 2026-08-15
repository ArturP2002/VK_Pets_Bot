"""Deterministic dose arithmetic — LLM never computes the dose."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


OWNER_DISCLAIMER = (
    "⚠️ Расчёт справочный и предназначен для врача. "
    "Не заменяет клиническое решение. Перед применением проверьте дозу, "
    "форму выпуска и показания по первоисточнику. "
    "Владельцу: не назначайте препарат самостоятельно."
)


@dataclass
class CalcResult:
    ok: bool
    total_mg: float | None = None
    tablets: float | None = None
    ml: float | None = None
    form: str = ""
    dose_mg_per_kg: float | None = None
    weight_kg: float | None = None
    warning: str = ""
    error: str = ""
    details: str = ""

    def format_message(self) -> str:
        if not self.ok:
            return self.error or "Не удалось выполнить расчёт."
        lines = ["📐 Результат расчёта дозы", ""]
        if self.dose_mg_per_kg is not None and self.weight_kg is not None:
            lines.append(f"Доза: {self.dose_mg_per_kg:g} мг/кг × {self.weight_kg:g} кг")
        if self.total_mg is not None:
            lines.append(f"Всего: {self.total_mg:g} мг")
        if self.tablets is not None:
            lines.append(f"Таблетки: {self._fmt_tablets(self.tablets)}")
        if self.ml is not None:
            lines.append(f"Раствор: {self.ml:g} мл")
        if self.details:
            lines.extend(["", self.details])
        if self.warning:
            lines.extend(["", f"⚠️ {self.warning}"])
        lines.extend(["", OWNER_DISCLAIMER])
        return "\n".join(lines)

    @staticmethod
    def _fmt_tablets(value: float) -> str:
        if value == int(value):
            return f"{int(value)} шт."
        # quarters
        quarters = round(value * 4)
        whole = quarters // 4
        frac = quarters % 4
        frac_map = {1: "¼", 2: "½", 3: "¾"}
        if whole and frac:
            return f"{whole} {frac_map[frac]} табл."
        if frac:
            return f"{frac_map[frac]} табл."
        return f"{value:g} табл."


def round_to_quarter(units: float) -> float:
    """Round tablet count to nearest ¼ / ½ / ¾ / whole."""
    return round(units * 4) / 4.0


def check_minmax(
    dose_mg_per_kg: float,
    dose_min: float | None,
    dose_max: float | None,
) -> str:
    if dose_min is None and dose_max is None:
        return ""
    if dose_min is not None and dose_mg_per_kg < dose_min:
        return (
            f"Доза {dose_mg_per_kg:g} мг/кг ниже минимума справочника "
            f"({dose_min:g} мг/кг)."
        )
    if dose_max is not None and dose_mg_per_kg > dose_max:
        return (
            f"Доза {dose_mg_per_kg:g} мг/кг выше максимума справочника "
            f"({dose_max:g} мг/кг)."
        )
    if dose_min is not None and dose_max is not None:
        return f"Доза в пределах справочника ({dose_min:g}–{dose_max:g} мг/кг)."
    return ""


def calculate(
    *,
    weight_kg: float,
    dose_mg_per_kg: float,
    form: str | None = None,
    mg_per_unit: float | None = None,
    concentration_mg_ml: float | None = None,
    dose_min: float | None = None,
    dose_max: float | None = None,
) -> CalcResult:
    if weight_kg is None or weight_kg <= 0:
        return CalcResult(ok=False, error="Укажите вес животного (кг > 0).")
    if dose_mg_per_kg is None or dose_mg_per_kg <= 0:
        return CalcResult(ok=False, error="Укажите дозу в мг/кг (> 0).")

    total_mg = float(dose_mg_per_kg) * float(weight_kg)
    form_norm = (form or "").strip().lower()
    warning = check_minmax(float(dose_mg_per_kg), dose_min, dose_max)
    # check_minmax returns an info string when in range — treat only out-of-range as warning
    if warning.startswith("Доза в пределах"):
        details = warning
        warning = ""
    else:
        details = ""

    if form_norm in ("tablet", "таблетка", "таблетки", "tab"):
        if not mg_per_unit or mg_per_unit <= 0:
            return CalcResult(
                ok=False,
                error="Для таблеток укажите содержание мг в 1 таблетке (mg_per_unit).",
            )
        raw = total_mg / float(mg_per_unit)
        tablets = round_to_quarter(raw)
        return CalcResult(
            ok=True,
            total_mg=round(total_mg, 4),
            tablets=tablets,
            form="tablet",
            dose_mg_per_kg=float(dose_mg_per_kg),
            weight_kg=float(weight_kg),
            warning=warning,
            details=details or f"До округления: {raw:g} табл. (по {mg_per_unit:g} мг).",
        )

    if form_norm in ("solution", "раствор", "injection", "инъекция", "ml", "liquid"):
        if not concentration_mg_ml or concentration_mg_ml <= 0:
            return CalcResult(
                ok=False,
                error="Для раствора укажите концентрацию мг/мл (concentration_mg_ml).",
            )
        ml = total_mg / float(concentration_mg_ml)
        return CalcResult(
            ok=True,
            total_mg=round(total_mg, 4),
            ml=round(ml, 4),
            form="solution",
            dose_mg_per_kg=float(dose_mg_per_kg),
            weight_kg=float(weight_kg),
            warning=warning,
            details=details or f"Концентрация: {concentration_mg_ml:g} мг/мл.",
        )

    # form unknown / other — return total mg only
    return CalcResult(
        ok=True,
        total_mg=round(total_mg, 4),
        form=form_norm or "other",
        dose_mg_per_kg=float(dose_mg_per_kg),
        weight_kg=float(weight_kg),
        warning=warning,
        details=details
        or "Форма не указана — посчитана только суммарная доза в мг. "
        "Уточните таблетки (мг/табл.) или раствор (мг/мл) для полного расчёта.",
    )


def resolve_dose_from_rows(
    doses: list[dict[str, Any]],
    species: str | None = None,
) -> tuple[float | None, float | None, float | None, dict[str, Any] | None]:
    """
    Pick dose_min/max from formulary rows; return (mid, min, max, matched_row).
    Mid is used only when the user did not provide мг/кг.
    """
    if not doses:
        return None, None, None, None

    species_l = (species or "").strip().lower()
    taxa_hints = _species_to_taxa(species_l)

    scored: list[tuple[int, dict[str, Any]]] = []
    for row in doses:
        if row.get("dose_min") is None and row.get("dose_max") is None:
            continue
        unit = (row.get("dose_unit") or "").lower()
        if unit and "mg/kg" not in unit and "мг/кг" not in unit:
            # Prefer mg/kg rows; still allow empty unit
            if unit not in ("", "mg/kg", "мг/кг"):
                continue
        score = 0
        taxa = (row.get("taxa") or "").lower()
        note = (row.get("species_note") or "").lower()
        if species_l and species_l in note:
            score += 3
        if taxa and taxa in taxa_hints:
            score += 2
        if taxa_hints and taxa in taxa_hints:
            score += 1
        scored.append((score, row))

    if not scored:
        # fallback: any row with numeric min/max
        for row in doses:
            if row.get("dose_min") is not None or row.get("dose_max") is not None:
                scored.append((0, row))
                break
    if not scored:
        return None, None, None, None

    scored.sort(key=lambda x: -x[0])
    best = scored[0][1]
    dmin = best.get("dose_min")
    dmax = best.get("dose_max")
    dmin_f = float(dmin) if dmin is not None else None
    dmax_f = float(dmax) if dmax is not None else None
    if dmin_f is not None and dmax_f is not None:
        mid = (dmin_f + dmax_f) / 2.0
    else:
        mid = dmin_f if dmin_f is not None else dmax_f
    return mid, dmin_f, dmax_f, best


def _species_to_taxa(species: str) -> set[str]:
    s = species.lower()
    mapping = {
        "fish": {"fish", "рыба", "рыб", "рыбка"},
        "mammals": {
            "mammal",
            "mammals",
            "млекопитающ",
            "кролик",
            "хорь",
            "крыс",
            "мыш",
            "шиншилл",
            "дегу",
            "морск",
            "хомяк",
            "ёж",
            "еж",
            "белк",
            "енот",
            "собак",
            "кошк",
            "кот",
            "щенок",
            "котён",
            "котен",
        },
        "birds": {"bird", "birds", "птиц", "попуга", "канарей", "куропат"},
        "reptiles": {
            "reptile",
            "reptiles",
            "рептил",
            "ящер",
            "змей",
            "черепа",
            "игуан",
            "геккон",
            "хамелеон",
        },
        "amphibians": {"amphibian", "amphibians", "амфиб", "лягуш", "жаб", "тритон"},
    }
    found: set[str] = set()
    for taxa, keys in mapping.items():
        if any(k in s for k in keys):
            found.add(taxa)
    if not found and s:
        found.add("other")
    return found
