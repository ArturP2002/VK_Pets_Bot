"""VK handlers for the dose calculator module."""
from __future__ import annotations

import logging
import re
from typing import Any

from bot import keyboards, states
from bot.keyboards import back_menu_keyboard
from integrations import vk
from services import (
    dosage_service,
    dose_calculator,
    llm_client,
    session,
    user_service,
)

logger = logging.getLogger(__name__)

MENU_LABEL = "Калькулятор дозы (для владельцев)"

FIELD_LABELS = {
    "weight_kg": "вес животного в килограммах",
    "dose_mg_per_kg": "дозу в мг/кг (как назначил врач)",
    "mg_per_unit": "сколько мг действующего вещества в одной таблетке",
    "concentration_mg_ml": "концентрацию раствора в мг/мл",
    "form": "форму выпуска: таблетки или раствор",
    "drug": "название препарата",
    "species": "вид животного",
}

FIELD_PROMPTS = {
    "weight_kg": "Напишите вес животного в кг — например: 4 или 1.2 кг.",
    "dose_mg_per_kg": (
        "Напишите дозу в мг/кг, которую назначил врач — например: 12 мг/кг.\n"
        "Если дозы нет, укажите препарат и вид — попробуем взять из справочника."
    ),
    "mg_per_unit": "Сколько мг в одной таблетке? Например: 50 мг.",
    "concentration_mg_ml": "Какая концентрация раствора (мг/мл)? Например: 5 мг/мл.",
    "form": "Это таблетки или раствор?",
    "drug": "Как называется препарат?",
    "species": "Какой вид животного? (кот, собака, кролик…)",
}


def start_calculator(peer_id: int, vk_user_id: int):
    user_service.get_or_create_user(vk_user_id)
    session.set_state(vk_user_id, states.CALC_WAIT_TEXT, {"extract": {}})
    vk.send_message(
        peer_id,
        "🧮 Калькулятор дозы (для владельцев)\n\n"
        "Модуль бесплатный — подписка не нужна.\n\n"
        "Напишите как удобно, своими словами — без шаблонов.\n"
        "Например: «У кота 4 кг, амоксициллин 12 мг/кг, таблетки по 50 мг — сколько давать?»\n\n"
        "Если чего-то не хватит для расчёта, спросим отдельно.\n"
        "Считает код: доли таблеток (¼/½/¾) или растворение для микродоз, "
        "проверка min–max по справочнику.",
        back_menu_keyboard(),
    )


def handle_calculator_message(peer_id: int, vk_user_id: int, text: str) -> bool:
    state = session.get_state(vk_user_id)
    if not state.startswith("calc_"):
        return False

    text = (text or "").strip()
    if not text:
        return True

    low = text.lower().strip()
    if low in ("отмена", "cancel"):
        from bot.handlers import common

        common.go_main_menu(peer_id, vk_user_id)
        return True

    user = user_service.get_or_create_user(vk_user_id)
    data = session.get_payload(vk_user_id)

    if state == states.CALC_WAIT_TEXT:
        _extract_and_continue(peer_id, user, text)
        return True

    if state == states.CALC_MISSING:
        _fill_missing(peer_id, user, text, data)
        return True

    if state == states.CALC_CONFIRM:
        if normalized in ("да", "yes", "ок", "ok", "посчитать", "рассчитать"):
            confirm_calc(peer_id, vk_user_id, "yes")
        elif normalized in ("нет", "no", "отмена"):
            confirm_calc(peer_id, vk_user_id, "no")
        else:
            vk.send_message(
                peer_id,
                "Подтвердите расчёт: да / нет.",
                keyboards.calc_confirm_keyboard(),
            )
        return True

    return True


def _extract_and_continue(peer_id: int, user, text: str):
    vk.send_message(peer_id, "Разбираю параметры…")
    extract = _extract_params(text)
    extract = _enrich_from_formulary(extract)
    missing = _required_missing(extract)
    session.set_state(
        user.vk_id,
        states.CALC_MISSING if missing else states.CALC_CONFIRM,
        {"extract": extract, "missing": missing, "missing_idx": 0, "raw_text": text},
    )

    if missing:
        vk.send_message(
            peer_id,
            _missing_message(extract, missing),
            back_menu_keyboard(),
        )
        return

    _show_confirm(peer_id, user.vk_id, extract)


def _extract_params(text: str) -> dict[str, Any]:
    """LLM extract with heuristic merge; never hard-fail on free-form text."""
    naive = _normalize_extract(_naive_extract(text))
    if not llm_client.is_configured():
        return naive

    try:
        llm_data = _normalize_extract(llm_client.calc_extract(text))
    except llm_client.LLMError as exc:
        logger.warning("calc_extract failed, using heuristic fallback: %s", exc)
        return naive

    return _merge_extracts(llm_data, naive)


def _merge_extracts(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    """Prefer LLM values; fill gaps from heuristics."""
    out = dict(fallback)
    for key, value in primary.items():
        if key.startswith("_"):
            out[key] = value
            continue
        if value is None or value == "" or value == []:
            continue
        out[key] = value
    # Prefer explicit numeric from either side
    for key in ("weight_kg", "dose_mg_per_kg", "mg_per_unit", "concentration_mg_ml"):
        if _as_float(out.get(key)) is None and _as_float(fallback.get(key)) is not None:
            out[key] = fallback[key]
        elif _as_float(primary.get(key)) is not None:
            # Heuristic: when user is converting tablet strength (e.g. "пересчитай на 50 мг"),
            # LLM may incorrectly read previous mg_per_unit as dose_mg_per_kg.
            # If primary dose_mg_per_kg equals fallback mg_per_unit, and mg_per_unit is changing,
            # keep fallback dose_mg_per_kg.
            if key == "dose_mg_per_kg":
                p_dose = _as_float(primary.get("dose_mg_per_kg"))
                p_mg_unit = _as_float(primary.get("mg_per_unit"))
                f_dose = _as_float(fallback.get("dose_mg_per_kg"))
                f_mg_unit = _as_float(fallback.get("mg_per_unit"))
                if (
                    p_dose is not None
                    and p_mg_unit is not None
                    and f_dose is not None
                    and f_mg_unit is not None
                    and abs(p_dose - f_mg_unit) <= 1e-9
                    and abs(p_mg_unit - f_mg_unit) > 1e-9
                ):
                    out[key] = fallback[key]
                    continue
            out[key] = primary[key]
    return _normalize_extract(out)


def _normalize_extract(data: dict[str, Any]) -> dict[str, Any]:
    out = {
        "drug": (data.get("drug") or None),
        "species": (data.get("species") or None),
        "weight_kg": _as_float(data.get("weight_kg")),
        "dose_mg_per_kg": _as_float(data.get("dose_mg_per_kg")),
        "form": data.get("form"),
        "mg_per_unit": _as_float(data.get("mg_per_unit")),
        "concentration_mg_ml": _as_float(data.get("concentration_mg_ml")),
        "route": data.get("route"),
        "notes": data.get("notes"),
        "missing_fields": list(data.get("missing_fields") or []),
    }
    if isinstance(out["drug"], str):
        out["drug"] = out["drug"].strip() or None
    if isinstance(out["species"], str):
        out["species"] = out["species"].strip() or None

    form = _normalize_form(out.get("form"))
    if form is None:
        if out["mg_per_unit"] is not None:
            form = "tablet"
        elif out["concentration_mg_ml"] is not None:
            form = "solution"
    out["form"] = form

    # Keep helpful extras from formulary enrichment
    for key, value in data.items():
        if key.startswith("_"):
            out[key] = value
    return out


def _normalize_form(form: Any) -> str | None:
    if form is None:
        return None
    low = str(form).strip().lower()
    if not low:
        return None
    if any(x in low for x in ("tablet", "таблет", "табл", "tab")):
        return "tablet"
    if any(x in low for x in ("solution", "раствор", "инъек", "injection", "liquid", "сироп")):
        return "solution"
    if low in ("other", "другое"):
        return "other"
    return low


def _missing_message(extract: dict, missing: list[str]) -> str:
    found: list[str] = []
    if extract.get("drug"):
        found.append(f"препарат — {extract['drug']}")
    if extract.get("species"):
        found.append(f"вид — {extract['species']}")
    if extract.get("weight_kg") is not None:
        found.append(f"вес — {extract['weight_kg']:g} кг")
    if extract.get("dose_mg_per_kg") is not None:
        found.append(f"доза — {extract['dose_mg_per_kg']:g} мг/кг")
    if extract.get("form") == "tablet" and extract.get("mg_per_unit") is not None:
        found.append(f"таблетки по {extract['mg_per_unit']:g} мг")
    if extract.get("form") == "solution" and extract.get("concentration_mg_ml") is not None:
        found.append(f"раствор {extract['concentration_mg_ml']:g} мг/мл")

    lines = ["Понял часть данных." if found else "Нужно чуть уточнить."]
    if found:
        lines.append("Уже есть: " + "; ".join(found) + ".")
    lines.append("")
    if len(missing) == 1:
        lines.append(FIELD_PROMPTS.get(missing[0], f"Укажите: {FIELD_LABELS.get(missing[0], missing[0])}."))
    else:
        lines.append("Не хватает для расчёта:")
        for field in missing:
            label = FIELD_LABELS.get(field, field)
            lines.append(f"• {label}")
        lines.append("")
        lines.append(FIELD_PROMPTS.get(missing[0], f"Сначала укажите: {FIELD_LABELS.get(missing[0], missing[0])}."))
    return "\n".join(lines)


def _fill_missing(peer_id: int, user, text: str, data: dict):
    extract = dict(data.get("extract") or {})
    missing = list(data.get("missing") or [])
    idx = int(data.get("missing_idx") or 0)

    # If user sent another full free-form sentence — re-extract and merge
    if _looks_like_full_query(text):
        fresh = _extract_params(text)
        extract = _merge_extracts(fresh, extract)
        extract = _enrich_from_formulary(extract)
        remaining = _required_missing(extract)
        if remaining:
            session.set_state(
                user.vk_id,
                states.CALC_MISSING,
                {"extract": extract, "missing": remaining, "missing_idx": 0},
            )
            vk.send_message(peer_id, _missing_message(extract, remaining), back_menu_keyboard())
            return
        session.set_state(user.vk_id, states.CALC_CONFIRM, {"extract": extract})
        _show_confirm(peer_id, user.vk_id, extract)
        return

    if not missing or idx >= len(missing):
        extract = _enrich_from_formulary(extract)
        missing = _required_missing(extract)
        if missing:
            session.update_payload(user.vk_id, extract=extract, missing=missing, missing_idx=0)
            vk.send_message(peer_id, _missing_message(extract, missing), back_menu_keyboard())
            return
        session.set_state(user.vk_id, states.CALC_CONFIRM, {"extract": extract})
        _show_confirm(peer_id, user.vk_id, extract)
        return

    field = missing[idx]
    value = _parse_field(field, text)
    if value is None and field in ("weight_kg", "dose_mg_per_kg", "mg_per_unit", "concentration_mg_ml"):
        # Try pulling the number from a short free phrase
        patched = _normalize_extract(_naive_extract(text))
        if field == "weight_kg" and patched.get("weight_kg") is not None:
            value = patched["weight_kg"]
        elif field == "dose_mg_per_kg" and patched.get("dose_mg_per_kg") is not None:
            value = patched["dose_mg_per_kg"]
        elif field == "mg_per_unit" and patched.get("mg_per_unit") is not None:
            value = patched["mg_per_unit"]
        elif field == "concentration_mg_ml" and patched.get("concentration_mg_ml") is not None:
            value = patched["concentration_mg_ml"]
        else:
            vk.send_message(peer_id, "Нужно число. " + FIELD_PROMPTS.get(field, field))
            return

    if value is not None:
        extract[field] = value
    elif field in ("drug", "species", "form", "route", "notes"):
        extract[field] = text.strip()

    extract = _normalize_extract(extract)
    extract = _enrich_from_formulary(extract)
    remaining = _required_missing(extract)
    if remaining:
        session.set_state(
            user.vk_id,
            states.CALC_MISSING,
            {"extract": extract, "missing": remaining, "missing_idx": 0},
        )
        vk.send_message(
            peer_id,
            _missing_message(extract, remaining),
            back_menu_keyboard(),
        )
        return

    session.set_state(user.vk_id, states.CALC_CONFIRM, {"extract": extract})
    _show_confirm(peer_id, user.vk_id, extract)


def _looks_like_full_query(text: str) -> bool:
    low = text.lower()
    has_weight = bool(re.search(r"\d+[.,]?\d*\s*(кг|kg|г\b|g\b)", low))
    has_dose = bool(re.search(r"мг\s*/\s*кг|mg\s*/\s*kg", low))
    has_form = bool(re.search(r"таблет|раствор|мг\s*/\s*мл|mg\s*/\s*ml", low))
    return sum([has_weight, has_dose, has_form]) >= 2 or (has_weight and has_dose)


def _show_confirm(peer_id: int, vk_user_id: int, extract: dict):
    lines = [
        "Проверьте параметры перед расчётом:",
        "",
        f"• Препарат: {extract.get('drug') or '—'}",
        f"• Вид: {extract.get('species') or '—'}",
        f"• Вес: {extract.get('weight_kg')} кг",
        f"• Доза: {extract.get('dose_mg_per_kg')} мг/кг",
        f"• Форма: {_form_label(extract.get('form'))}",
    ]
    if extract.get("mg_per_unit"):
        lines.append(f"• мг в таблетке: {extract.get('mg_per_unit')}")
    if extract.get("concentration_mg_ml"):
        lines.append(f"• концентрация: {extract.get('concentration_mg_ml')} мг/мл")
    if extract.get("notes"):
        lines.append(f"• Заметки: {extract['notes']}")
    lines.extend(["", "Рассчитать?"])
    session.set_state(vk_user_id, states.CALC_CONFIRM, {"extract": extract})
    vk.send_message(peer_id, "\n".join(lines), keyboards.calc_confirm_keyboard())


def _form_label(form: Any) -> str:
    f = _normalize_form(form)
    if f == "tablet":
        return "таблетки"
    if f == "solution":
        return "раствор"
    return form or "—"


def confirm_calc(peer_id: int, vk_user_id: int, answer: str) -> str | None:
    if answer != "yes":
        session.set_state(vk_user_id, states.CALC_WAIT_TEXT, {"extract": {}})
        vk.send_message(
            peer_id,
            "Расчёт отменён. Введите новый запрос или «Главное меню».",
            back_menu_keyboard(),
        )
        return "Отменено"

    data = session.get_payload(vk_user_id)
    extract = data.get("extract") or {}
    result = dose_calculator.calculate(
        weight_kg=float(extract["weight_kg"]),
        dose_mg_per_kg=float(extract["dose_mg_per_kg"]),
        form=extract.get("form"),
        mg_per_unit=_as_float(extract.get("mg_per_unit")),
        concentration_mg_ml=_as_float(extract.get("concentration_mg_ml")),
    )
    session.set_state(vk_user_id, states.CALC_WAIT_TEXT, {"extract": {}})
    vk.send_message(peer_id, result.format_message(), back_menu_keyboard())
    return "Готово"


def _enrich_from_formulary(extract: dict[str, Any]) -> dict[str, Any]:
    """If dose_mg_per_kg missing — try formulary by drug + species (code, not LLM guess)."""
    out = _normalize_extract(extract)
    dose = _as_float(out.get("dose_mg_per_kg"))
    if dose is not None and dose > 0:
        out["dose_mg_per_kg"] = dose

    drug_name = (out.get("drug") or "").strip()
    if not drug_name:
        return out

    try:
        hits = dosage_service.search_with_analogs(drug_name, limit=5)
    except Exception as exc:
        logger.warning("formulary lookup failed: %s", exc)
        return out
    if not hits:
        return out
    drug = None
    from services import formulary_search as _fs

    for hit in hits:
        drug = _fs.get_drug(hit.drug_id)
        if drug:
            break
    if not drug:
        return out

    mid, dmin, dmax, row = dose_calculator.resolve_dose_from_rows(
        drug.doses, species=out.get("species")
    )
    if dmin is not None:
        out["_dose_min"] = dmin
    if dmax is not None:
        out["_dose_max"] = dmax
    if (dose is None or dose <= 0) and mid is not None:
        out["dose_mg_per_kg"] = mid
        if dmin is not None and dmax is not None and dmin != dmax:
            out["_dose_source"] = f"{dmin:g}–{dmax:g} мг/кг"
        else:
            out["_dose_source"] = f"{mid:g} мг/кг"
        notes = (out.get("notes") or "")
        if re.search(r"отсутств|нет данных|missing", notes, re.I):
            out["notes"] = None
    return out


def _required_missing(extract: dict) -> list[str]:
    missing: list[str] = []
    if _as_float(extract.get("weight_kg")) is None:
        missing.append("weight_kg")
    if _as_float(extract.get("dose_mg_per_kg")) is None:
        missing.append("dose_mg_per_kg")

    form = _normalize_form(extract.get("form"))
    mg_unit = _as_float(extract.get("mg_per_unit"))
    conc = _as_float(extract.get("concentration_mg_ml"))

    if form == "tablet" or (form is None and mg_unit is not None):
        if mg_unit is None:
            missing.append("mg_per_unit")
    elif form == "solution" or (form is None and conc is not None):
        if conc is None:
            missing.append("concentration_mg_ml")
    elif form is None and mg_unit is None and conc is None:
        # Can still compute total mg, but ask form for a useful owner answer
        missing.append("form")
    return missing


def _parse_field(field: str, text: str) -> Any:
    text = text.strip().replace(",", ".")
    if field in ("weight_kg", "dose_mg_per_kg", "mg_per_unit", "concentration_mg_ml"):
        m = re.search(r"(\d+(?:\.\d+)?)", text)
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None
    if field == "form":
        return _normalize_form(text)
    return text


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _naive_extract(text: str) -> dict[str, Any]:
    """Heuristic parse of free-form RU text (works even without LLM)."""
    raw = text
    low = text.lower().replace("ё", "е")

    weight = None
    m = re.search(r"(\d+[.,]?\d*)\s*(кг|kg)\b", low, re.I)
    if m:
        weight = float(m.group(1).replace(",", "."))
    else:
        m = re.search(r"(\d+[.,]?\d*)\s*(г|g)\b", low, re.I)
        if m:
            weight = float(m.group(1).replace(",", ".")) / 1000.0

    dose = None
    m = re.search(r"(\d+[.,]?\d*)\s*мг\s*/\s*кг", low, re.I)
    if m:
        dose = float(m.group(1).replace(",", "."))
    else:
        m = re.search(r"(\d+[.,]?\d*)\s*mg\s*/\s*kg", low, re.I)
        if m:
            dose = float(m.group(1).replace(",", "."))

    mg_unit = None
    conc = None
    form = None

    m = re.search(
        r"(?:таблет\w*|табл\.?|tab\w*)\s*(?:по\s*)?(\d+[.,]?\d*)\s*мг",
        low,
        re.I,
    )
    if m:
        mg_unit = float(m.group(1).replace(",", "."))
        form = "tablet"
    if mg_unit is None:
        m = re.search(r"(\d+[.,]?\d*)\s*мг\s*(?:в\s*)?(?:1\s*)?таблет", low, re.I)
        if m:
            mg_unit = float(m.group(1).replace(",", "."))
            form = "tablet"

    m = re.search(r"(\d+[.,]?\d*)\s*мг\s*/\s*мл", low, re.I)
    if m:
        conc = float(m.group(1).replace(",", "."))
        form = form or "solution"
    if re.search(r"таблет|табл\.?", low, re.I):
        form = form or "tablet"
    if re.search(r"раствор|сироп|суспенз", low, re.I):
        form = form or "solution"

    species = None
    for word in (
        "котёнок",
        "котенок",
        "кошка",
        "кот",
        "собака",
        "щенок",
        "кролик",
        "хорек",
        "хорь",
        "попугай",
        "птица",
        "крыса",
        "мышь",
        "шиншилла",
        "черепаха",
        "ящерица",
        "змея",
    ):
        if re.search(rf"\b{re.escape(word)}\w*\b", low):
            species = word
            break

    drug = None
    # Common drugs + latin-looking tokens after «назначил»
    m = re.search(
        r"(?:назначил[аи]?|препарат|дай(?:те)?|дать)\s+([A-Za-zА-Яа-яЁё-]{4,})",
        raw,
        re.I,
    )
    if m:
        candidate = m.group(1).strip(".,;:!")
        if candidate.lower() not in {"врач", "животн", "таблет", "раствор"}:
            drug = candidate
    if drug is None:
        known = (
            "амоксициллин",
            "амоксиклав",
            "мелоксикам",
            "метронидазол",
            "энрофлоксацин",
            "байтрил",
            "синулокс",
            "бускопан",
            "фенбендазол",
            "фебтал",
            "преднизолон",
            "дексаметазон",
            "трамадол",
            "габапентин",
            "маропитант",
            "серения",
            "омепразол",
            "ранитидин",
            "ивермектин",
        )
        for name in known:
            if name in low:
                drug = name
                break

    return {
        "drug": drug,
        "species": species,
        "weight_kg": weight,
        "dose_mg_per_kg": dose,
        "form": form,
        "mg_per_unit": mg_unit,
        "concentration_mg_ml": conc,
        "route": None,
        "notes": None,
        "missing_fields": [],
    }
