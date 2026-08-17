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

DISSOLUTION_DISCLAIMERS = (
    "Взбалтывать раствор перед каждым приёмом.",
    "Готовить свежий раствор не реже чем раз в 48 часов.",
)

# Calculated tablet fraction below this → dissolution instead of physical split.
PHYSICAL_TABLET_MIN = 0.25

# Max water volume for tablet dissolution (ml).
DISSOLUTION_V_MAX_ML = 10.0

# Safety margin applied to dissolution volume (10–15 % → use 12.5 %).
DISSOLUTION_VOLUME_MARGIN = 0.875


@dataclass
class CalcResult:
    ok: bool
    total_mg: float | None = None
    tablets: float | None = None
    ml: float | None = None
    form: str = ""
    method: str = ""
    dose_mg_per_kg: float | None = None
    weight_kg: float | None = None
    dissolve_volume_ml: float | None = None
    draw_volume_ml: float | None = None
    tablet_fraction: float | None = None
    mg_per_unit: float | None = None
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

        if self.method == "dissolution":
            lines.extend(["", "Способ: растворение таблетки"])
            if self.tablet_fraction is not None and self.mg_per_unit is not None:
                lines.append(
                    f"• Измельчить {_fmt_tablet_fraction(self.tablet_fraction)} таблетку "
                    f"({self.mg_per_unit:g} мг)"
                )
            if self.dissolve_volume_ml is not None:
                lines.append(
                    f"• Растворить в {self._fmt_ml(self.dissolve_volume_ml)} мл воды, взболтать"
                )
            if self.draw_volume_ml is not None:
                lines.append(
                    f"• Набрать шприцем {self._fmt_ml(self.draw_volume_ml)} мл"
                )
        elif self.tablets is not None:
            lines.append(f"Таблетки: {self._fmt_tablets(self.tablets)}")
        if self.ml is not None and self.method != "dissolution":
            lines.append(f"Раствор: {self._fmt_ml(self.ml)} мл")

        if self.details:
            lines.extend(["", self.details])

        warnings: list[str] = []
        if self.method == "dissolution":
            warnings.extend(DISSOLUTION_DISCLAIMERS)
        if self.warning:
            warnings.append(self.warning)
        if warnings:
            lines.append("")
            for item in warnings:
                lines.append(f"⚠️ {item}")

        lines.extend(["", OWNER_DISCLAIMER])
        return "\n".join(lines)

    @staticmethod
    def _fmt_tablets(value: float) -> str:
        if value == int(value):
            return f"{int(value)} шт."
        quarters = round(value * 4)
        whole = quarters // 4
        frac = quarters % 4
        frac_map = {1: "¼", 2: "½", 3: "¾"}
        if whole and frac:
            return f"{whole} {frac_map[frac]} табл."
        if frac:
            return f"{frac_map[frac]} табл."
        return f"{value:g} табл."

    @staticmethod
    def _fmt_ml(value: float) -> str:
        if value < 1:
            rounded = round(value, 2)
            text = f"{rounded:g}"
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return text
        if value == int(value):
            return f"{int(value)}"
        return f"{value:g}"


def round_to_quarter(units: float) -> float:
    """Round tablet count to nearest ¼ / ½ / ¾ / whole."""
    return round(units * 4) / 4.0


def v_max_for_weight(weight_kg: float) -> tuple[float | None, str]:
    """
    Syringe draw volume limit (ml) by animal weight.
    Returns (v_max, warning) — v_max is None when weight > 10 kg.
    """
    weight_g = float(weight_kg) * 1000.0
    if weight_g < 100:
        return 0.1, ""
    if weight_g < 500:
        return 0.5, ""
    if weight_g < 1000:
        return 1.0, ""
    if float(weight_kg) <= 10.0:
        return 2.0, ""
    return None, (
        "Вес более 10 кг — лимит объёма набора шприцем не определён; "
        "растворение недоступно, используйте физическое деление таблетки."
    )


def round_convenient_ml(volume_ml: float) -> float:
    """Round dissolution volume to a practical syringe-friendly value."""
    if volume_ml <= 0:
        return 0.0
    if volume_ml < 0.5:
        return round(volume_ml, 2)
    if volume_ml < 5.0:
        half_step = round(volume_ml * 2) / 2.0
        if abs(half_step - volume_ml) <= 0.15:
            return half_step
        return round(volume_ml, 1)
    return min(round(volume_ml * 10) / 10.0, DISSOLUTION_V_MAX_ML)


def apply_dissolution_volume_margin(volume_ml: float) -> float:
    """Reduce calculated dissolution volume by ~10–15 % for a safety margin."""
    return round_convenient_ml(volume_ml * DISSOLUTION_VOLUME_MARGIN)


def compute_dissolution(
    *,
    total_mg: float,
    mg_per_unit: float,
    v_max: float,
) -> tuple[float, float, float] | None:
    """
    Pick tablet fraction f, dissolution volume V and draw volume v.

    V = (v_max × f × S) / D  (≤ 10 ml); v = D × V / (f × S).
    Returns (f, V, v) or None when dissolution cannot meet v_max.
    """
    d = float(total_mg)
    s = float(mg_per_unit)
    if d <= 0 or s <= 0 or v_max <= 0:
        return None

    for f in (1.0, 0.5, 0.25):
        v_raw = (v_max * f * s) / d
        if v_raw > DISSOLUTION_V_MAX_ML:
            continue
        v_dissolve = apply_dissolution_volume_margin(v_raw)
        if v_dissolve <= 0:
            v_dissolve = round_convenient_ml(v_raw * 0.9)
        draw = d * v_dissolve / (f * s)
        if draw <= v_max + 1e-9:
            return f, v_dissolve, draw

    f = 1.0
    draw_at_cap = d * DISSOLUTION_V_MAX_ML / (f * s)
    if draw_at_cap <= v_max + 1e-9:
        v_dissolve = apply_dissolution_volume_margin(DISSOLUTION_V_MAX_ML)
        draw = d * v_dissolve / (f * s)
        if draw <= v_max + 1e-9:
            return f, v_dissolve, draw

    return None


def _fmt_tablet_fraction(fraction: float) -> str:
    mapping = {1.0: "1 целую", 0.5: "½", 0.25: "¼"}
    return mapping.get(fraction, f"{fraction:g}")


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


def _split_minmax_message(warning: str) -> tuple[str, str]:
    if warning.startswith("Доза в пределах"):
        return "", warning
    return warning, ""


def _calculate_tablet(
    *,
    total_mg: float,
    weight_kg: float,
    dose_mg_per_kg: float,
    mg_per_unit: float,
    warning: str,
    details: str,
) -> CalcResult:
    raw = total_mg / float(mg_per_unit)

    if raw >= PHYSICAL_TABLET_MIN:
        tablets = round_to_quarter(raw)
        return CalcResult(
            ok=True,
            total_mg=round(total_mg, 4),
            tablets=tablets,
            form="tablet",
            method="physical_tablet",
            dose_mg_per_kg=float(dose_mg_per_kg),
            weight_kg=float(weight_kg),
            mg_per_unit=float(mg_per_unit),
            warning=warning,
            details=details or f"До округления: {raw:g} табл. (по {mg_per_unit:g} мг).",
        )

    v_max, v_max_warning = v_max_for_weight(weight_kg)
    extra_warnings: list[str] = []
    if v_max_warning:
        extra_warnings.append(v_max_warning)

    if v_max is None:
        tablets = round_to_quarter(raw)
        if tablets >= PHYSICAL_TABLET_MIN:
            return CalcResult(
                ok=True,
                total_mg=round(total_mg, 4),
                tablets=tablets,
                form="tablet",
                method="physical_tablet",
                dose_mg_per_kg=float(dose_mg_per_kg),
                weight_kg=float(weight_kg),
                mg_per_unit=float(mg_per_unit),
                warning=_join_warnings(warning, extra_warnings),
                details=details or f"До округления: {raw:g} табл. (по {mg_per_unit:g} мг).",
            )
        return CalcResult(
            ok=True,
            total_mg=round(total_mg, 4),
            form="tablet",
            method="dissolution_failed",
            dose_mg_per_kg=float(dose_mg_per_kg),
            weight_kg=float(weight_kg),
            mg_per_unit=float(mg_per_unit),
            warning=_join_warnings(
                warning,
                extra_warnings
                + [
                    f"Доля таблетки {raw:g} меньше ¼ — растворение недоступно при данном весе. "
                    "Уточните дозу или форму выпуска у врача."
                ],
            ),
            details=details or f"Расчётная доля: {raw:g} табл. (по {mg_per_unit:g} мг).",
        )

    dissolution = compute_dissolution(
        total_mg=total_mg,
        mg_per_unit=mg_per_unit,
        v_max=v_max,
    )
    if dissolution is None:
        fallback = round_to_quarter(raw)
        fallback_warning = (
            "Не удалось уложить дозу растворением в лимит шприца — "
            "рассмотрите физическую долю таблетки (¼/½/¾) или другую форму выпуска."
        )
        if fallback >= PHYSICAL_TABLET_MIN:
            return CalcResult(
                ok=True,
                total_mg=round(total_mg, 4),
                tablets=fallback,
                form="tablet",
                method="physical_tablet",
                dose_mg_per_kg=float(dose_mg_per_kg),
                weight_kg=float(weight_kg),
                mg_per_unit=float(mg_per_unit),
                warning=_join_warnings(warning, extra_warnings + [fallback_warning]),
                details=details or f"До округления: {raw:g} табл. (по {mg_per_unit:g} мг).",
            )
        return CalcResult(
            ok=True,
            total_mg=round(total_mg, 4),
            form="tablet",
            method="dissolution_failed",
            dose_mg_per_kg=float(dose_mg_per_kg),
            weight_kg=float(weight_kg),
            mg_per_unit=float(mg_per_unit),
            warning=_join_warnings(warning, extra_warnings + [fallback_warning]),
            details=details or f"Расчётная доля: {raw:g} табл. (по {mg_per_unit:g} мг).",
        )

    fraction, dissolve_volume, draw_volume = dissolution
    return CalcResult(
        ok=True,
        total_mg=round(total_mg, 4),
        form="tablet",
        method="dissolution",
        dose_mg_per_kg=float(dose_mg_per_kg),
        weight_kg=float(weight_kg),
        mg_per_unit=float(mg_per_unit),
        tablet_fraction=fraction,
        dissolve_volume_ml=round(dissolve_volume, 4),
        draw_volume_ml=round(draw_volume, 4),
        warning=_join_warnings(warning, extra_warnings),
        details=details
        or (
            f"Расчётная доля таблетки {raw:g} (< ¼) — через растворение "
            f"(лимит шприца {v_max:g} мл)."
        ),
    )


def _join_warnings(*parts: str | list[str]) -> str:
    items: list[str] = []
    for part in parts:
        if not part:
            continue
        if isinstance(part, list):
            items.extend(p for p in part if p)
        else:
            items.append(part)
    return " ".join(items)


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
    warning, details = _split_minmax_message(
        check_minmax(float(dose_mg_per_kg), dose_min, dose_max)
    )

    if form_norm in ("tablet", "таблетка", "таблетки", "tab"):
        if not mg_per_unit or mg_per_unit <= 0:
            return CalcResult(
                ok=False,
                error="Для таблеток укажите содержание мг в 1 таблетке (mg_per_unit).",
            )
        return _calculate_tablet(
            total_mg=total_mg,
            weight_kg=float(weight_kg),
            dose_mg_per_kg=float(dose_mg_per_kg),
            mg_per_unit=float(mg_per_unit),
            warning=warning,
            details=details,
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
            method="solution",
            dose_mg_per_kg=float(dose_mg_per_kg),
            weight_kg=float(weight_kg),
            warning=warning,
            details=details or f"Концентрация: {concentration_mg_ml:g} мг/мл.",
        )

    return CalcResult(
        ok=True,
        total_mg=round(total_mg, 4),
        form=form_norm or "other",
        method="mg_only",
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
