"""VK handlers for the dose calculator module."""
from __future__ import annotations

import logging
from typing import Any

from bot import keyboards, states
from bot.keyboards import back_menu_keyboard
from integrations import vk
from services import (
    calculator_access,
    dose_calculator,
    formulary_search,
    llm_client,
    session,
    user_service,
)

logger = logging.getLogger(__name__)

MENU_LABEL = "Калькулятор дозы (для владельцев)"

FIELD_PROMPTS = {
    "weight_kg": "Укажите вес животного в кг (число).",
    "dose_mg_per_kg": "Укажите дозу в мг/кг (число), либо название препарата и вид — подставим из справочника.",
    "mg_per_unit": "Укажите содержание действующего вещества в 1 таблетке (мг).",
    "concentration_mg_ml": "Укажите концентрацию раствора (мг/мл).",
    "form": "Укажите форму: таблетка или раствор.",
    "drug": "Укажите название препарата.",
    "species": "Укажите вид животного.",
}


def start_calculator(peer_id: int, vk_user_id: int):
    user = user_service.get_or_create_user(vk_user_id)
    allowed, reason = calculator_access.check_calculator_access(user)
    if not allowed:
        vk.send_message(
            peer_id,
            (
                "Калькулятор дозы доступен на пробном периоде "
                "или по подписке «Калькулятор» — 300 ₽/мес.\n\n"
                "Тарифы консультаций (Стартовый/Базовый/Премиум) этот модуль не открывают."
            ),
            keyboards.calculator_upsell_keyboard(),
        )
        return

    session.set_state(vk_user_id, states.CALC_WAIT_TEXT, {"extract": {}})
    vk.send_message(
        peer_id,
        "🧮 Калькулятор дозы (для владельцев)\n\n"
        "Опишите свободным текстом, например:\n"
        "«Мелоксикам, кролик 1.2 кг, 0.2 мг/кг, таблетки по 1 мг»\n"
        "или «раствор 5 мг/мл, птица 0.08 кг, 10 мг/кг».\n\n"
        "ИИ извлечёт параметры, арифметику посчитает код "
        "(с округлением таблеток до ¼ и проверкой min–max).",
        back_menu_keyboard(),
    )


def handle_calculator_message(peer_id: int, vk_user_id: int, text: str) -> bool:
    state = session.get_state(vk_user_id)
    if not state.startswith("calc_"):
        return False

    text = (text or "").strip()
    if not text:
        return True

    normalized = text.lower()
    if normalized in ("меню", "главное меню", "отмена", "cancel", "начать", "start", "/start"):
        session.clear_state(vk_user_id)
        from bot.handlers import common

        common.handle_start(peer_id, vk_user_id)
        return True

    user = user_service.get_or_create_user(vk_user_id)
    allowed, _ = calculator_access.check_calculator_access(user)
    if not allowed:
        session.clear_state(vk_user_id)
        vk.send_message(
            peer_id,
            "Доступ к калькулятору закрыт. Оформите подписку «Калькулятор» — 300 ₽/мес.",
            keyboards.calculator_upsell_keyboard(),
        )
        return True

    data = session.get_payload(vk_user_id)

    if state == states.CALC_WAIT_TEXT:
        _extract_and_continue(peer_id, user, text)
        return True

    if state == states.CALC_MISSING:
        _fill_missing(peer_id, user, text, data)
        return True

    if state == states.CALC_CONFIRM:
        # Typed yes/no
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
    try:
        if not llm_client.is_configured():
            extract = _naive_extract(text)
        else:
            extract = llm_client.calc_extract(text)
    except llm_client.LLMError as exc:
        logger.warning("calc_extract failed: %s", exc)
        vk.send_message(
            peer_id,
            "Не удалось разобрать текст. Попробуйте ещё раз проще "
            "(препарат, вид, вес кг, мг/кг, форма).",
            back_menu_keyboard(),
        )
        return

    extract = _enrich_from_formulary(extract)
    missing = _required_missing(extract)
    session.set_state(
        user.vk_id,
        states.CALC_MISSING if missing else states.CALC_CONFIRM,
        {"extract": extract, "missing": missing, "missing_idx": 0},
    )

    if missing:
        field = missing[0]
        vk.send_message(
            peer_id,
            f"Не хватает данных.\n\n{FIELD_PROMPTS.get(field, field)}",
            back_menu_keyboard(),
        )
        return

    _show_confirm(peer_id, user.vk_id, extract)


def _fill_missing(peer_id: int, user, text: str, data: dict):
    extract = dict(data.get("extract") or {})
    missing = list(data.get("missing") or [])
    idx = int(data.get("missing_idx") or 0)
    if not missing or idx >= len(missing):
        extract = _enrich_from_formulary(extract)
        missing = _required_missing(extract)
        if missing:
            session.update_payload(user.vk_id, extract=extract, missing=missing, missing_idx=0)
            vk.send_message(
                peer_id,
                FIELD_PROMPTS.get(missing[0], missing[0]),
                back_menu_keyboard(),
            )
            return
        session.set_state(user.vk_id, states.CALC_CONFIRM, {"extract": extract})
        _show_confirm(peer_id, user.vk_id, extract)
        return

    field = missing[idx]
    value = _parse_field(field, text)
    if value is None and field in ("weight_kg", "dose_mg_per_kg", "mg_per_unit", "concentration_mg_ml"):
        vk.send_message(peer_id, "Нужно число. " + FIELD_PROMPTS.get(field, field))
        return
    if value is not None:
        extract[field] = value
    elif field in ("drug", "species", "form", "route", "notes"):
        extract[field] = text.strip()

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
            FIELD_PROMPTS.get(remaining[0], remaining[0]),
            back_menu_keyboard(),
        )
        return

    session.set_state(user.vk_id, states.CALC_CONFIRM, {"extract": extract})
    _show_confirm(peer_id, user.vk_id, extract)


def _show_confirm(peer_id: int, vk_user_id: int, extract: dict):
    lines = [
        "Проверьте параметры перед расчётом:",
        "",
        f"• Препарат: {extract.get('drug') or '—'}",
        f"• Вид: {extract.get('species') or '—'}",
        f"• Вес: {extract.get('weight_kg')} кг",
        f"• Доза: {extract.get('dose_mg_per_kg')} мг/кг",
        f"• Форма: {extract.get('form') or '—'}",
    ]
    if extract.get("mg_per_unit"):
        lines.append(f"• мг в таблетке: {extract.get('mg_per_unit')}")
    if extract.get("concentration_mg_ml"):
        lines.append(f"• концентрация: {extract.get('concentration_mg_ml')} мг/мл")
    if extract.get("_dose_source"):
        lines.append(f"• мг/кг из справочника: {extract['_dose_source']}")
    if extract.get("notes"):
        lines.append(f"• Заметки: {extract['notes']}")
    lines.extend(["", "Рассчитать?"])
    session.set_state(vk_user_id, states.CALC_CONFIRM, {"extract": extract})
    vk.send_message(peer_id, "\n".join(lines), keyboards.calc_confirm_keyboard())


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
        dose_min=_as_float(extract.get("_dose_min")),
        dose_max=_as_float(extract.get("_dose_max")),
    )
    session.set_state(vk_user_id, states.CALC_WAIT_TEXT, {"extract": {}})
    vk.send_message(peer_id, result.format_message(), back_menu_keyboard())
    return "Готово"


def _enrich_from_formulary(extract: dict[str, Any]) -> dict[str, Any]:
    """If dose_mg_per_kg missing — try formulary by drug + species (code, not LLM guess)."""
    out = dict(extract)
    dose = _as_float(out.get("dose_mg_per_kg"))
    if dose is not None and dose > 0:
        out["dose_mg_per_kg"] = dose
        # still attach min/max for warnings if we can find the drug
    drug_name = (out.get("drug") or "").strip()
    if not drug_name:
        return out

    hits = formulary_search.search_drugs(drug_name, limit=1)
    if not hits:
        return out
    drug = formulary_search.get_drug(hits[0].drug_id)
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
        src = (row or {}).get("source") or "formulary"
        out["_dose_source"] = (
            f"{mid:g} мг/кг (середина {dmin}–{dmax}, {src})"
            if dmin is not None and dmax is not None
            else f"{mid:g} мг/кг ({src})"
        )
    return out


def _required_missing(extract: dict) -> list[str]:
    missing: list[str] = []
    if _as_float(extract.get("weight_kg")) is None:
        missing.append("weight_kg")
    if _as_float(extract.get("dose_mg_per_kg")) is None:
        missing.append("dose_mg_per_kg")

    form = (extract.get("form") or "").strip().lower()
    if form in ("tablet", "таблетка", "таблетки", "tab"):
        if _as_float(extract.get("mg_per_unit")) is None:
            missing.append("mg_per_unit")
    elif form in ("solution", "раствор", "injection", "инъекция", "ml", "liquid"):
        if _as_float(extract.get("concentration_mg_ml")) is None:
            missing.append("concentration_mg_ml")
    return missing


def _parse_field(field: str, text: str) -> Any:
    text = text.strip().replace(",", ".")
    if field in ("weight_kg", "dose_mg_per_kg", "mg_per_unit", "concentration_mg_ml"):
        try:
            return float(text.split()[0])
        except (ValueError, IndexError):
            return None
    if field == "form":
        low = text.lower()
        if any(x in low for x in ("таблет", "tab", "табл")):
            return "tablet"
        if any(x in low for x in ("раствор", "solution", "мл", "инъек", "liquid")):
            return "solution"
        return text
    return text


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _naive_extract(text: str) -> dict[str, Any]:
    """Minimal fallback when Anthropic is not configured — parse obvious numbers."""
    import re

    weight = None
    dose = None
    mg_unit = None
    conc = None
    form = None

    m = re.search(r"(\d+[.,]?\d*)\s*кг", text, re.I)
    if m:
        weight = float(m.group(1).replace(",", "."))
    m = re.search(r"(\d+[.,]?\d*)\s*мг\s*/\s*кг", text, re.I)
    if m:
        dose = float(m.group(1).replace(",", "."))
    m = re.search(r"таблет\w*\s*по\s*(\d+[.,]?\d*)\s*мг", text, re.I)
    if m:
        mg_unit = float(m.group(1).replace(",", "."))
        form = "tablet"
    m = re.search(r"(\d+[.,]?\d*)\s*мг\s*/\s*мл", text, re.I)
    if m:
        conc = float(m.group(1).replace(",", "."))
        form = form or "solution"
    if re.search(r"таблет", text, re.I):
        form = form or "tablet"
    if re.search(r"раствор", text, re.I):
        form = form or "solution"

    missing = []
    if weight is None:
        missing.append("weight_kg")
    if dose is None:
        missing.append("dose_mg_per_kg")
    return {
        "drug": None,
        "species": None,
        "weight_kg": weight,
        "dose_mg_per_kg": dose,
        "form": form,
        "mg_per_unit": mg_unit,
        "concentration_mg_ml": conc,
        "route": None,
        "notes": text,
        "missing_fields": missing,
    }
