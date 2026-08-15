from bot.keyboards import back_menu_keyboard, main_menu_keyboard
from integrations import vk
from services import session, user_service
from services.gatekeeper import check_access


def normalize_nav_text(text: str) -> str:
    """Normalize user/VK button text for menu navigation matching."""
    if not text:
        return ""
    cleaned = (
        text.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
        .strip()
        .lower()
    )
    return " ".join(cleaned.split())


def is_main_menu_command(text: str) -> bool:
    return normalize_nav_text(text) in {"меню", "главное меню"}


def is_start_command(text: str) -> bool:
    return normalize_nav_text(text) in {"начать", "start", "/start"}


def send_main_menu(peer_id: int, user, text: str = "Главное меню ExoCare:"):
    """Show main keyboard; if access incomplete — start the right onboarding step."""
    gate = check_access(user, require_clinic_ack=False)
    if not gate.allowed:
        if gate.reason == "legal":
            from bot.handlers import legal

            legal.start_legal_flow(peer_id, user)
            return
        if gate.reason == "registration":
            from bot.handlers import registration

            registration.start_registration(peer_id, user.vk_id)
            return
        vk.send_message(
            peer_id,
            "Не удалось открыть меню. Напишите «Начать».",
            back_menu_keyboard(),
        )
        return
    vk.send_message(peer_id, text, main_menu_keyboard())


def go_main_menu(peer_id: int, vk_user_id: int, text: str = "Главное меню ExoCare:"):
    """Clear FSM and open main menu (or onboarding)."""
    session.clear_state(vk_user_id)
    user = user_service.get_or_create_user(vk_user_id)
    gate = check_access(user)
    if not gate.allowed:
        if gate.reason == "legal":
            from bot.handlers import legal

            legal.start_legal_flow(peer_id, user)
            return
        if gate.reason == "registration":
            from bot.handlers import registration

            registration.start_registration(peer_id, vk_user_id)
            return
        if gate.reason == "clinic_ack":
            from bot.handlers import legal

            legal.start_clinic_ack(peer_id, vk_user_id)
            return
    send_main_menu(peer_id, user, text)


def handle_start(peer_id: int, vk_user_id: int):
    session.clear_state(vk_user_id)
    user = user_service.get_or_create_user(vk_user_id)
    screen = vk.fetch_screen_name(vk_user_id)
    if screen:
        user_service.update_screen_name(user, screen)

    gate = check_access(user, require_clinic_ack=False)
    if gate.reason == "legal":
        from bot.handlers import legal

        legal.start_legal_flow(peer_id, user)
        return
    if gate.reason == "registration":
        from bot.handlers import registration

        registration.start_registration(peer_id, vk_user_id)
        return

    gate_full = check_access(user)
    if gate_full.reason == "clinic_ack":
        from bot.handlers import legal

        legal.start_clinic_ack(peer_id, vk_user_id)
        return
    send_main_menu(peer_id, user, "Добро пожаловать в ExoCare!")
