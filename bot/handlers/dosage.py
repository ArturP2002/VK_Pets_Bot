"""VK handlers for the dosage (formulary) module."""
from __future__ import annotations

import logging
import time

import config
from bot import keyboards, states
from bot.keyboards import back_menu_keyboard
from integrations import vk
from services import dosage_access, dosage_service, session, user_service

logger = logging.getLogger(__name__)

MENU_LABEL = "Дозировки препаратов (для врачей)"


def start_dosage(peer_id: int, vk_user_id: int):
    user = user_service.get_or_create_user(vk_user_id)
    access = dosage_access.check_dosage_access(user)
    if not access.allowed:
        vk.send_message(
            peer_id,
            (
                "Лимит бесплатных запросов дозировок исчерпан "
                f"({dosage_access.count_usage_last_24h(user.vk_id)} за 24 ч).\n\n"
                "Оформите подписку «Дозировки» — 200 ₽/мес "
                "(без лимита и без задержки) или дождитесь обновления окна."
            ),
            keyboards.dosage_upsell_keyboard(),
        )
        return

    hint = ""
    if not access.has_subscription:
        hint = (
            f"\n\nБесплатно: осталось {access.remaining_free} запроса(ов) за 24 ч "
            f"(задержка ~{config.FORMULARY_DOSAGE_DELAY_SEC} с)."
        )

    session.set_state(vk_user_id, states.DOSAGE_WAIT_QUERY, {})
    vk.send_message(
        peer_id,
        "💊 Дозировки препаратов (для врачей)\n\n"
        "Введите название препарата (латиницей или по-русски)."
        f"{hint}",
        back_menu_keyboard(),
    )


def handle_dosage_message(peer_id: int, vk_user_id: int, text: str) -> bool:
    state = session.get_state(vk_user_id)
    if not state.startswith("dosage_"):
        return False

    text = (text or "").strip()
    if not text:
        return True

    # «Главное меню» / «Начать» обрабатываются в router; здесь — отмена сценария
    low = text.lower().strip()
    if low in ("отмена", "cancel"):
        from bot.handlers import common

        common.go_main_menu(peer_id, vk_user_id)
        return True

    user = user_service.get_or_create_user(vk_user_id)
    data = session.get_payload(vk_user_id)

    if state == states.DOSAGE_WAIT_QUERY:
        _handle_query(peer_id, user, text)
        return True

    if state == states.DOSAGE_PICK:
        # User typed instead of tapping — treat as new search
        _handle_query(peer_id, user, text)
        return True

    if state == states.DOSAGE_QA:
        drug_id = data.get("drug_id")
        if not drug_id:
            session.set_state(vk_user_id, states.DOSAGE_WAIT_QUERY, {})
            vk.send_message(peer_id, "Введите название препарата:", back_menu_keyboard())
            return True
        outcome = dosage_service.answer_qa(user, int(drug_id), text)
        vk.send_message(
            peer_id,
            outcome.text,
            keyboards.dosage_after_brief_keyboard(
                can_ask_ai=dosage_access.can_ask_ai(user)
            ),
        )
        return True

    if state == states.DOSAGE_ASK_AI:
        _run_ask_ai(peer_id, user, text)
        return True

    return True


def _handle_query(peer_id: int, user, query: str):
    access = dosage_access.check_dosage_access(user)
    if not access.allowed:
        vk.send_message(
            peer_id,
            "Лимит запросов исчерпан. Оформите подписку «Дозировки» — 200 ₽/мес.",
            keyboards.dosage_upsell_keyboard(),
        )
        session.clear_state(user.vk_id)
        return

    hits = dosage_service.pick_search_hits(dosage_service.search(query))
    if not hits:
        can_ai = dosage_access.can_ask_ai(user)
        session.set_state(
            user.vk_id,
            states.DOSAGE_ASK_AI if can_ai else states.DOSAGE_WAIT_QUERY,
            {"last_query": query},
        )
        msg = f"Препарат «{query}» не найден в справочнике."
        if can_ai:
            msg += "\n\nМожете нажать «Спросить ИИ» или ввести другой запрос."
        else:
            msg += (
                "\n\nЛимит исчерпан — «Спросить ИИ» недоступен. "
                "Оформите подписку «Дозировки»."
            )
        vk.send_message(peer_id, msg, keyboards.dosage_miss_keyboard(can_ask_ai=can_ai))
        return

    if len(hits) == 1:
        _deliver_hit(peer_id, user, hits[0].drug_id, query, access.apply_delay)
        return

    session.set_state(
        user.vk_id,
        states.DOSAGE_PICK,
        {"last_query": query, "candidates": [h.drug_id for h in hits]},
    )
    labels = "\n".join(f"• {h.display_name}" for h in hits)
    vk.send_message(
        peer_id,
        f"Найдено несколько вариантов:\n{labels}\n\nВыберите препарат:",
        keyboards.dosage_candidates_keyboard(hits),
    )


def _deliver_hit(peer_id: int, user, drug_id: int, query: str, apply_delay: bool):
    vk.send_message(peer_id, "Ищу информацию по препарату…")
    outcome = dosage_service.deliver_brief(
        user, drug_id, user_query=query, apply_delay=apply_delay
    )
    session.set_state(
        user.vk_id,
        states.DOSAGE_QA,
        {"drug_id": drug_id, "last_query": query},
    )
    vk.send_message(
        peer_id,
        outcome.text,
        keyboards.dosage_after_brief_keyboard(
            can_ask_ai=dosage_access.can_ask_ai(user)
        ),
    )
    vk.send_message(
        peer_id,
        "Можете задать уточняющий вопрос по этому препарату "
        "или открыть калькулятор дозы.",
        back_menu_keyboard(),
    )


def pick_drug(peer_id: int, vk_user_id: int, drug_id: int) -> str | None:
    user = user_service.get_or_create_user(vk_user_id)
    access = dosage_access.check_dosage_access(user)
    if not access.allowed:
        vk.send_message(
            peer_id,
            "Лимит запросов исчерпан. Оформите подписку «Дозировки» — 200 ₽/мес.",
            keyboards.dosage_upsell_keyboard(),
        )
        return "Лимит исчерпан"

    data = session.get_payload(vk_user_id)
    query = data.get("last_query", "")
    _deliver_hit(peer_id, user, drug_id, query, access.apply_delay)
    return "Готово"


def start_ask_ai(peer_id: int, vk_user_id: int) -> str | None:
    user = user_service.get_or_create_user(vk_user_id)
    if not dosage_access.can_ask_ai(user):
        vk.send_message(
            peer_id,
            "«Спросить ИИ» недоступно: лимит исчерпан или нужна подписка «Дозировки».",
            keyboards.dosage_upsell_keyboard(),
        )
        return "Нет доступа"

    data = session.get_payload(vk_user_id)
    session.set_state(
        vk_user_id,
        states.DOSAGE_ASK_AI,
        {"last_query": data.get("last_query", ""), "drug_id": data.get("drug_id")},
    )
    vk.send_message(
        peer_id,
        "Сформулируйте вопрос для ИИ (препарата нет в локальной базе "
        "или нужен общий ответ). Ответ засчитывается в лимит 2/24 ч.",
        back_menu_keyboard(),
    )
    return "Ожидаю вопрос"


def _run_ask_ai(peer_id: int, user, question: str):
    vk.send_message(peer_id, "Готовлю ответ ИИ…")
    access = dosage_access.check_dosage_access(user)
    if access.apply_delay and config.FORMULARY_DOSAGE_DELAY_SEC > 0:
        time.sleep(config.FORMULARY_DOSAGE_DELAY_SEC)

    outcome = dosage_service.ask_ai(user, question)
    session.set_state(user.vk_id, states.DOSAGE_WAIT_QUERY, {})
    vk.send_message(peer_id, outcome.text, back_menu_keyboard())
