import json

from vk_api.keyboard import VkKeyboard, VkKeyboardColor

import config
from integrations.vk import keyboard_to_json
from models import LegalDocument
from services.subscription_catalog import (
    PLAN_ORDER,
    ADDON_PLAN_ORDER,
    one_time_button_label,
    plan_button_label,
)

VK_BUTTON_LABEL_MAX = 40

CHECKOUT_CONSENT_BUTTON_LABEL = "принять правила exo care и оплатить"


def _vk_button_label(text: str, max_len: int = VK_BUTTON_LABEL_MAX) -> str:
    return text[:max_len]


def main_menu_keyboard() -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("Экстренная помощь", color=VkKeyboardColor.NEGATIVE)
    kb.add_button("Консультация", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button(one_time_button_label(), color=VkKeyboardColor.PRIMARY)
    kb.add_button("Мои животные", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("Мои заявки", color=VkKeyboardColor.SECONDARY)
    kb.add_button("Моя подписка", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("О проекте", color=VkKeyboardColor.SECONDARY)
    kb.add_button("Контакты", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("Рекомендуемые врачи", color=VkKeyboardColor.SECONDARY)
    kb.add_button("Партнёрские клиники", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("Дозировки препаратов", color=VkKeyboardColor.PRIMARY)
    kb.add_button("Калькулятор дозы", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("Пробный период 5 дней", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    # VK API не поддерживает оранжевый; negative (#E64646) — ближайший акцентный цвет
    kb.add_button("Отменить автопродление", color=VkKeyboardColor.NEGATIVE)
    return keyboard_to_json(kb)


def back_menu_keyboard() -> str:
    kb = VkKeyboard(one_time=False)
    kb.add_button("Главное меню", color=VkKeyboardColor.SECONDARY)
    return keyboard_to_json(kb)


def build_legal_docs_keyboard(docs: list[LegalDocument], use_links: bool = True) -> str:
    kb = VkKeyboard(inline=True)
    for i, doc in enumerate(docs):
        if use_links:
            kb.add_openlink_button(doc.title[:40], link=doc.google_doc_url)
        if (i + 1) % 2 == 0 and i + 1 < len(docs):
            kb.add_line()
    return keyboard_to_json(kb)


def onboarding_consent_keyboard(package_url: str) -> str:
    kb = VkKeyboard(inline=True)
    if package_url:
        kb.add_openlink_button("Ознакомиться с документами", link=package_url)
        kb.add_line()
    kb.add_callback_button(
        "принять условия exo care",
        payload={"cmd": "legal_accept"},
        color=VkKeyboardColor.POSITIVE,
    )
    return keyboard_to_json(kb)


def clinic_ack_keyboard() -> str:
    kb = VkKeyboard(inline=True)
    kb.add_callback_button(
        "Подтверждаю",
        payload={"cmd": "clinic_ack"},
        color=VkKeyboardColor.POSITIVE,
    )
    return keyboard_to_json(kb)


def mock_payment_keyboard(order_id: str) -> str:
    kb = VkKeyboard(inline=True)
    kb.add_callback_button(
        "Оплатить успешно (тест)",
        payload={"cmd": "pay_ok", "order": order_id},
        color=VkKeyboardColor.POSITIVE,
    )
    kb.add_callback_button(
        "Оплата не прошла (тест)",
        payload={"cmd": "pay_fail", "order": order_id},
        color=VkKeyboardColor.NEGATIVE,
    )
    return keyboard_to_json(kb)


def subscription_plans_keyboard() -> str:
    kb = VkKeyboard(inline=True)
    for i, plan in enumerate(PLAN_ORDER):
        kb.add_callback_button(
            plan_button_label(plan),
            payload={"cmd": "sub_plan", "plan": plan},
        )
        if i + 1 < len(PLAN_ORDER):
            kb.add_line()
    kb.add_line()
    kb.add_callback_button(
        one_time_button_label(),
        payload={"cmd": "sub_plan", "plan": "one_time"},
    )
    for plan in ADDON_PLAN_ORDER:
        kb.add_line()
        kb.add_callback_button(
            plan_button_label(plan),
            payload={"cmd": "sub_plan", "plan": plan},
            color=VkKeyboardColor.POSITIVE,
        )
    return keyboard_to_json(kb)


def subscription_period_keyboard(plan: str) -> str:
    kb = VkKeyboard(inline=True)
    months_list = sorted(config.TARIFF_PRICES.get(plan, {}).keys())
    prices = config.TARIFF_PRICES.get(plan, {})
    for i, months in enumerate(months_list):
        kb.add_callback_button(
            f"{months} мес — {prices[months]}₽",
            payload={"cmd": "sub_period", "plan": plan, "months": months},
        )
        if i + 1 < len(months_list):
            kb.add_line()
    return keyboard_to_json(kb)


def partners_catalog_keyboard() -> str:
    kb = VkKeyboard(inline=True)
    if config.PARTNERS_CATALOG_URL:
        kb.add_openlink_button("Врачи и партнёрские клиники", link=config.PARTNERS_CATALOG_URL)
    return keyboard_to_json(kb)


def doctor_link_keyboard(url: str, label: str = "Написать врачу ВКонтакте") -> str:
    kb = VkKeyboard(inline=True)
    kb.add_openlink_button(label, link=url)
    return keyboard_to_json(kb)


def patient_link_keyboard(url: str) -> str:
    return doctor_link_keyboard(url, label="Написать пациенту ВКонтакте")


def yes_no_keyboard(cmd: str) -> str:
    kb = VkKeyboard(inline=True)
    kb.add_callback_button("Да", payload={"cmd": cmd, "answer": "yes"})
    kb.add_callback_button("Нет", payload={"cmd": cmd, "answer": "no"})
    return keyboard_to_json(kb)


def pet_actions_keyboard() -> str:
    kb = VkKeyboard(inline=True)
    kb.add_callback_button("Добавить животное", payload={"cmd": "pet_add"})
    kb.add_callback_button("Выбрать активного", payload={"cmd": "pet_select"})
    return keyboard_to_json(kb)


def pet_picker_keyboard(pets, cmd: str = "pet_pick") -> str:
    buttons = [
        (
            f"{p.species}: {p.name}"[:40],
            {"cmd": cmd, "pet_id": p.id},
        )
        for p in pets[:12]
    ]
    return inline_grid_keyboard(buttons)


def ticket_list_keyboard(tickets) -> str:
    from services import ticket_service

    buttons = []
    for t in tickets[:10]:
        status = ticket_service.status_label(t.status)
        buttons.append(
            (
                f"#{t.number} · {status[:14]}",
                {"cmd": "ticket_view", "number": t.number},
            )
        )
    return inline_grid_keyboard(buttons)


def emergency_upsell_keyboard() -> str:
    kb = VkKeyboard(inline=True)
    kb.add_callback_button("Тариф Базовый", payload={"cmd": "sub_plan", "plan": "basic"})
    kb.add_callback_button("Тариф Премиум", payload={"cmd": "sub_plan", "plan": "premium"})
    kb.add_line()
    kb.add_callback_button(
        one_time_button_label(),
        payload={"cmd": "sub_plan", "plan": "one_time"},
    )
    return keyboard_to_json(kb)


def payment_link_keyboard(url: str) -> str:
    kb = VkKeyboard(inline=True)
    kb.add_openlink_button("Оплатить", link=url)
    return keyboard_to_json(kb)


def checkout_consent_keyboard(package_url: str) -> str:
    kb = VkKeyboard(inline=True)
    if package_url:
        kb.add_openlink_button("Ознакомиться с документами", link=package_url)
        kb.add_line()
    kb.add_callback_button(
        _vk_button_label(CHECKOUT_CONSENT_BUTTON_LABEL),
        payload={"cmd": "sub_consent_pay"},
        color=VkKeyboardColor.POSITIVE,
    )
    return keyboard_to_json(kb)


def subscription_manage_keyboard(show_cancel: bool = False) -> str:
    kb = VkKeyboard(inline=True)
    if show_cancel:
        kb.add_callback_button(
            "Отменить автопродление",
            payload={"cmd": "sub_cancel_auto_renew"},
            color=VkKeyboardColor.NEGATIVE,
        )
        kb.add_line()
    for i, plan in enumerate(PLAN_ORDER):
        kb.add_callback_button(
            plan_button_label(plan),
            payload={"cmd": "sub_plan", "plan": plan},
        )
        if i + 1 < len(PLAN_ORDER):
            kb.add_line()
    kb.add_line()
    kb.add_callback_button(
        one_time_button_label(),
        payload={"cmd": "sub_plan", "plan": "one_time"},
    )
    return keyboard_to_json(kb)


def inline_grid_keyboard(buttons: list[tuple[str, dict]], columns: int = 2) -> str:
    """Inline keyboard with up to 6 rows (VK API limit)."""
    kb = VkKeyboard(inline=True)
    for i, (label, payload) in enumerate(buttons):
        kb.add_callback_button(label, payload=payload, color=VkKeyboardColor.PRIMARY)
        if i % columns == columns - 1 and i + 1 < len(buttons):
            kb.add_line()
    return keyboard_to_json(kb)


def doctor_ticket_keyboard(ticket_number: int) -> str:
    kb = VkKeyboard(inline=True)
    kb.add_callback_button(
        f"Взять #{ticket_number}",
        payload={"cmd": "doc_assign", "number": ticket_number},
    )
    kb.add_callback_button(
        "Завершить",
        payload={"cmd": "doc_complete", "number": ticket_number},
    )
    return keyboard_to_json(kb)


def dosage_candidates_keyboard(hits: list) -> str:
    buttons = [
        (
            f"{h.display_name}"[:40],
            {"cmd": "dosage_pick", "drug_id": h.drug_id},
        )
        for h in hits[:8]
    ]
    return inline_grid_keyboard(buttons, columns=1)


def dosage_after_brief_keyboard(*, can_ask_ai: bool = False, query: str = "") -> str:
    kb = VkKeyboard(inline=True)
    kb.add_callback_button(
        "Калькулятор дозы",
        payload={"cmd": "dosage_to_calc"},
        color=VkKeyboardColor.PRIMARY,
    )
    if can_ask_ai:
        kb.add_line()
        kb.add_callback_button(
            "Спросить ИИ",
            payload={"cmd": "dosage_ask_ai"},
            color=VkKeyboardColor.SECONDARY,
        )
    return keyboard_to_json(kb)


def dosage_miss_keyboard(*, can_ask_ai: bool) -> str:
    kb = VkKeyboard(inline=True)
    if can_ask_ai:
        kb.add_callback_button(
            "Спросить ИИ",
            payload={"cmd": "dosage_ask_ai"},
            color=VkKeyboardColor.PRIMARY,
        )
    else:
        kb.add_callback_button(
            "Подписка Дозировки 200₽",
            payload={"cmd": "sub_plan", "plan": "dosage"},
            color=VkKeyboardColor.POSITIVE,
        )
    return keyboard_to_json(kb)


def dosage_upsell_keyboard() -> str:
    kb = VkKeyboard(inline=True)
    kb.add_callback_button(
        "Дозировки — 200 ₽/мес",
        payload={"cmd": "sub_plan", "plan": "dosage"},
        color=VkKeyboardColor.POSITIVE,
    )
    return keyboard_to_json(kb)


def calculator_upsell_keyboard() -> str:
    kb = VkKeyboard(inline=True)
    kb.add_callback_button(
        "Калькулятор — 300 ₽/мес",
        payload={"cmd": "sub_plan", "plan": "calculator"},
        color=VkKeyboardColor.POSITIVE,
    )
    return keyboard_to_json(kb)


def calc_confirm_keyboard() -> str:
    return yes_no_keyboard("calc_confirm")

