"""VK handlers for the dosage (formulary) module."""
from __future__ import annotations

import logging
import re

from bot import keyboards, states
from bot.keyboards import back_menu_keyboard
from integrations import vk
from services import dosage_access, dosage_service, session, user_service

logger = logging.getLogger(__name__)

MENU_LABEL = "Дозировки препаратов (для врачей)"

_QUESTION_RE = re.compile(
    r"(\?|(?:^|\s)(?:"
    r"как|какая|какой|какие|какое|сколько|можно|почему|когда|зачем|"
    r"что|чем|побоч\w*|противопоказ\w*|дозировк\w*|доза|дозу|дозы"
    r")\b)",
    re.IGNORECASE,
)


def _looks_like_question(text: str) -> bool:
    folded = (text or "").lower().replace("ё", "е")
    return bool(_QUESTION_RE.search(folded))


def start_dosage(peer_id: int, vk_user_id: int):
    user_service.get_or_create_user(vk_user_id)
    session.set_state(vk_user_id, states.DOSAGE_WAIT_QUERY, {})
    vk.send_message(
        peer_id,
        "💊 Дозировки препаратов (для врачей)\n\n"
        "Модуль бесплатный и без лимита запросов.\n"
        "Введите название препарата (латиницей или по-русски).",
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
        if not _looks_like_question(text):
            _handle_query(peer_id, user, text)
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
    # If the exact/name match is missing, we still try brand→INN analogs.
    # In that case we should not pretend that the user-entered brand exists
    # in the formulary, so we will show a short "analogue" note.
    direct_hits = dosage_service.pick_search_hits(dosage_service.search(query, limit=5))
    hits = direct_hits or dosage_service.search_with_analogs(query)
    if not hits:
        session.set_state(
            user.vk_id,
            states.DOSAGE_WAIT_QUERY,
            {"last_query": query},
        )
        msg = (
            f"Препарат «{query}» не найден в справочнике ExoCare.\n\n"
            "Можете нажать «Помощь ИИ» — ответ будет по фрагментам "
            "справочника, без выдуманных доз — или ввести другой запрос."
        )
        vk.send_message(peer_id, msg, keyboards.dosage_miss_keyboard(can_ask_ai=True))
        return

    if len(hits) == 1:
        chosen = hits[0]
        display_title = (chosen.display_name or "").strip()

        # If user input is not a true exact match, still go through the "pick" step
        # (even when there is only one analog option).
        needs_pick = (not direct_hits) or (display_title.lower() != (query or "").strip().lower())
        if needs_pick:
            session.set_state(
                user.vk_id,
                states.DOSAGE_PICK,
                {
                    "last_query": query,
                    "candidates": [chosen.drug_id],
                    "candidate_labels": {chosen.drug_id: display_title},
                },
            )
            vk.send_message(
                peer_id,
                (
                    f"Препарат «{query}» не найден как точное совпадение. "
                    "Выберите вариант из справочника:"
                ),
                keyboards.dosage_candidates_keyboard(hits),
            )
            return

        _deliver_hit(
            peer_id,
            user,
            chosen.drug_id,
            query,
            False,
            display_title=display_title,
        )
        return

    session.set_state(
        user.vk_id,
        states.DOSAGE_PICK,
        {
            "last_query": query,
            "candidates": [h.drug_id for h in hits],
            "candidate_labels": {h.drug_id: h.display_name for h in hits},
        },
    )
    labels = "\n".join(f"• {h.display_name}" for h in hits)
    vk.send_message(
        peer_id,
        f"Найдено несколько вариантов:\n{labels}\n\nВыберите препарат:",
        keyboards.dosage_candidates_keyboard(hits),
    )


def _deliver_hit(
    peer_id: int,
    user,
    drug_id: int,
    query: str,
    apply_delay: bool,
    *,
    display_title: str = "",
):
    vk.send_message(peer_id, "Ищу информацию по препарату…")
    outcome = dosage_service.deliver_brief(
        user,
        drug_id,
        user_query=query,
        display_title=display_title,
        apply_delay=apply_delay,
    )
    session.set_state(
        user.vk_id,
        states.DOSAGE_QA,
        {"drug_id": drug_id, "last_query": query, "display_title": display_title or query},
    )
    vk.send_message(
        peer_id,
        outcome.text,
        keyboards.dosage_after_brief_keyboard(
            can_ask_ai=dosage_access.can_ask_ai(user)
        ),
    )
    if outcome.kind == "brief":
        vk.send_message(
            peer_id,
            "Можете ввести следующий препарат или задать вопрос по текущему.",
            back_menu_keyboard(),
        )


def pick_drug(peer_id: int, vk_user_id: int, drug_id: int) -> str | None:
    user = user_service.get_or_create_user(vk_user_id)
    data = session.get_payload(vk_user_id)
    query = data.get("last_query", "")
    labels = data.get("candidate_labels") or {}
    display_title = labels.get(drug_id) or query
    _deliver_hit(
        peer_id,
        user,
        drug_id,
        query,
        False,
        display_title=display_title,
    )
    return "Готово"


def start_ask_ai(peer_id: int, vk_user_id: int) -> str | None:
    user_service.get_or_create_user(vk_user_id)
    data = session.get_payload(vk_user_id)
    last_query = (data.get("last_query") or "").strip()
    display_title = (data.get("display_title") or "").strip()
    session.set_state(
        vk_user_id,
        states.DOSAGE_ASK_AI,
        {
            "last_query": last_query,
            "drug_id": data.get("drug_id"),
            "display_title": display_title,
        },
    )
    vk.send_message(
        peer_id,
        "Сформулируйте вопрос для ИИ. Модуль бесплатный — без лимита запросов.",
        back_menu_keyboard(),
    )
    return "Ожидаю вопрос"


def _run_ask_ai(peer_id: int, user, question: str):
    vk.send_message(peer_id, "Готовлю ответ ИИ…")
    data = session.get_payload(user.vk_id) or {}
    drug_id = data.get("drug_id")
    selected_drug = (data.get("display_title") or data.get("last_query") or "").strip()
    outcome = dosage_service.ask_ai(
        user,
        question,
        drug_id=int(drug_id) if drug_id else None,
        selected_drug=selected_drug,
    )
    session.set_state(user.vk_id, states.DOSAGE_WAIT_QUERY, {})
    vk.send_message(peer_id, outcome.text, back_menu_keyboard())
