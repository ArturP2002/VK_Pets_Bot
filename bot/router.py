import json
import logging

from bot.handlers import (
    calculator,
    common,
    doctor,
    dosage,
    legal,
    menu,
    pets,
    registration,
    subscription,
    tickets,
)
from services import session

logger = logging.getLogger(__name__)


def _parse_payload(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
    return {}


def route_message(peer_id: int, vk_user_id: int, text: str, attachments: list[str] | None = None, payload_raw=None):
    text = (text or "").strip()
    payload = _parse_payload(payload_raw)
    # Кнопка «Начать» в VK иногда приходит с пустым текстом и payload command=start
    if not text and payload.get("command") in ("start", "Начать"):
        text = "Начать"

    # Global navigation — before any FSM (tickets/pets/dosage/…), so «Главное меню» always works
    if common.is_start_command(text):
        common.handle_start(peer_id, vk_user_id)
        return
    if common.is_main_menu_command(text):
        common.go_main_menu(peer_id, vk_user_id)
        return

    if attachments and tickets.handle_ticket_attachments(peer_id, vk_user_id, attachments):
        return
    if doctor.handle_doctor_command(peer_id, vk_user_id, text):
        return
    if registration.handle_registration_message(peer_id, vk_user_id, text):
        return
    if pets.handle_pet_message(peer_id, vk_user_id, text):
        return
    if tickets.handle_ticket_message(peer_id, vk_user_id, text):
        return
    if dosage.handle_dosage_message(peer_id, vk_user_id, text):
        return
    if calculator.handle_calculator_message(peer_id, vk_user_id, text):
        return
    menu.handle_menu_text(peer_id, vk_user_id, text)


def route_event(peer_id: int, vk_user_id: int, payload_raw, event: dict | None = None) -> str | None:
    payload = _parse_payload(payload_raw)
    cmd = payload.get("cmd")
    conversation_message_id = event.get("conversation_message_id") if event else None
    if cmd == "legal_accept":
        if legal.handle_legal_accept(vk_user_id, peer_id):
            return "Документы приняты"
        return "Ошибка принятия документов"
    if cmd == "main_menu":
        common.go_main_menu(peer_id, vk_user_id)
        return "Главное меню"
    if cmd == "clinic_ack":
        legal.handle_clinic_ack(vk_user_id, peer_id)
        return "Подтверждено"
    if cmd == "pay_ok":
        subscription.handle_pay_ok(peer_id, vk_user_id, payload.get("order", ""))
    elif cmd == "pay_fail":
        subscription.handle_pay_fail(peer_id, vk_user_id, payload.get("order", ""))
    elif cmd == "sub_plan":
        subscription.handle_sub_plan(peer_id, vk_user_id, payload.get("plan", ""))
    elif cmd == "sub_period":
        subscription.handle_sub_period(
            peer_id, vk_user_id, payload.get("plan", ""), payload.get("months", 1)
        )
    elif cmd == "sub_consent_pay":
        subscription.handle_sub_consent_pay(peer_id, vk_user_id)
    elif cmd == "sub_cancel_auto_renew":
        subscription.handle_sub_cancel_auto_renew(peer_id, vk_user_id)
    elif cmd == "cancel_auto_renew":
        subscription.handle_cancel_auto_renew(peer_id, vk_user_id, payload.get("answer", "no"))
    elif cmd == "ticket_worsening":
        tickets.handle_ticket_worsening(peer_id, vk_user_id, payload.get("answer", "no"))
    elif cmd == "ticket_confirm":
        tickets.confirm_ticket(peer_id, vk_user_id, payload.get("answer", "no"))
    elif cmd == "pet_add":
        pets.start_add_pet(peer_id, vk_user_id)
    elif cmd == "pet_select":
        pets.show_pet_picker(peer_id, vk_user_id)
    elif cmd == "pet_pick":
        pets.set_active_pet(peer_id, vk_user_id, int(payload.get("pet_id", 0)))
    elif cmd == "ticket_pet":
        data = session.get_payload(vk_user_id)
        tickets.begin_ticket_for_pet(
            peer_id, vk_user_id, data.get("ticket_type", "consultation"), int(payload.get("pet_id", 0))
        )
    elif cmd == "ticket_view":
        from services import user_service
        user = user_service.get_or_create_user(vk_user_id)
        tickets.show_ticket_detail(peer_id, user, int(payload.get("number", 0)))
    elif cmd == "doc_assign":
        return doctor.handle_assign_callback(peer_id, vk_user_id, int(payload.get("number", 0)))
    elif cmd == "doc_complete":
        return doctor.handle_complete_callback(peer_id, vk_user_id, int(payload.get("number", 0)))
    elif cmd == "dosage_pick":
        return dosage.pick_drug(peer_id, vk_user_id, int(payload.get("drug_id", 0)))
    elif cmd == "dosage_ask_ai":
        return dosage.start_ask_ai(peer_id, vk_user_id)
    elif cmd == "dosage_to_calc":
        calculator.start_calculator(peer_id, vk_user_id)
        return "Калькулятор"
    elif cmd == "calc_confirm":
        return calculator.confirm_calc(peer_id, vk_user_id, payload.get("answer", "no"))
    return None
