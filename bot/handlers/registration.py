from bot import keyboards, states
from integrations import vk
from services import session, user_service
from services.validation import validate_email_address, validate_phone


def start_registration(peer_id: int, vk_user_id: int):
    session.set_state(vk_user_id, states.REG_NAME, {})
    vk.send_message(peer_id, "Регистрация. Введите ФИО:")


def handle_registration_message(peer_id: int, vk_user_id: int, text: str) -> bool:
    session_obj = session.get_session(vk_user_id)
    state = session_obj.state
    user = user_service.get_or_create_user(vk_user_id)
    if state == states.REG_NAME:
        user_service.update_profile(user, full_name=text.strip())
        session.set_state(vk_user_id, states.REG_PHONE, {})
        vk.send_message(peer_id, "Введите номер телефона (+7...):")
        return True
    if state == states.REG_PHONE:
        ok, phone = validate_phone(text)
        if not ok:
            vk.send_message(peer_id, "Неверный формат телефона. Пример: +79001234567")
            return True
        user_service.update_profile(user, phone=phone)
        session.set_state(vk_user_id, states.REG_EMAIL, {})
        vk.send_message(peer_id, "Введите email:")
        return True
    if state == states.REG_EMAIL:
        ok, email = validate_email_address(text)
        if not ok:
            vk.send_message(peer_id, "Неверный email.")
            return True
        user_service.update_profile(user, email=email)
        session.set_state(vk_user_id, states.REG_CITY, {})
        vk.send_message(peer_id, "Введите город:")
        return True
    if state == states.REG_CITY:
        user_service.update_profile(user, city=text.strip())
        session.clear_state(vk_user_id)
        from bot.handlers.legal import start_clinic_ack
        from services.gatekeeper import check_access
        gate = check_access(user)
        if gate.reason == "clinic_ack":
            start_clinic_ack(peer_id, vk_user_id)
        else:
            from bot.handlers.common import send_main_menu
            send_main_menu(peer_id, user, "Регистрация завершена!")
        return True
    return False
