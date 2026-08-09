import config
from bot.handlers import common, pets, subscription, tickets
from bot.keyboards import back_menu_keyboard
from integrations import vk
from services import payment_service, subscription_catalog, user_service
from services.gatekeeper import check_access


def require_gate(peer_id: int, user) -> bool:
    gate = check_access(user)
    if not gate.allowed:
        if gate.reason == "legal":
            from bot.handlers import legal
            legal.start_legal_flow(peer_id, user)
        elif gate.reason == "registration":
            from bot.handlers import registration
            registration.start_registration(peer_id, user.vk_id)
        elif gate.reason == "clinic_ack":
            from bot.handlers import legal
            legal.start_clinic_ack(peer_id, user.vk_id)
        return False
    return True


def _can_create_tickets(user) -> bool:
    return payment_service.has_ticket_access(user)


def handle_menu_text(peer_id: int, vk_user_id: int, text: str):
    text = (text or "").strip()
    normalized = text.lower()
    user = user_service.get_or_create_user(vk_user_id)
    if normalized in ("начать", "start", "/start"):
        common.handle_start(peer_id, vk_user_id)
        return
    if normalized in ("меню", "главное меню"):
        if require_gate(peer_id, user):
            common.send_main_menu(peer_id, user)
        return
    if not require_gate(peer_id, user):
        return

    ticket_actions = ("Экстренная помощь", "Консультация", "Первичная консультация")
    if text in ticket_actions and not _can_create_tickets(user):
        vk.send_message(
            peer_id,
            "Подписка закончилась. Оформите продление в «Моя подписка» или просмотрите историю в «Мои заявки».",
            back_menu_keyboard(),
        )
        return

    if text == "Экстренная помощь":
        tickets.start_ticket_flow(peer_id, vk_user_id, "emergency")
    elif text == "Консультация":
        tickets.start_ticket_flow(peer_id, vk_user_id, "consultation")
    elif text == "Первичная консультация":
        tickets.start_ticket_flow(peer_id, vk_user_id, "primary")
    elif text == "Мои животные":
        pets.show_pets(peer_id, user)
    elif text == "Мои заявки":
        tickets.show_tickets(peer_id, user)
    elif text == "Моя подписка":
        subscription.show_subscription(peer_id, user)
    elif text in ("О проекте", "Информация о сервисе"):
        show_service_info(peer_id)
    elif text == "Контакты":
        vk.send_message(
            peer_id,
            f"{config.CONTACTS_TEXT}\n\n{config.REFUND_POLICY_TEXT}\n\n{config.CANCEL_INSTRUCTIONS_TEXT}",
            back_menu_keyboard(),
        )
    elif text == "Отменить автопродление":
        subscription.start_cancel_auto_renew(peer_id, user)
    elif text == "Рекомендуемые врачи":
        show_doctors(peer_id)
    elif text == "Партнёрские клиники":
        show_clinics(peer_id)
    elif text == "Пробный период 5 дней":
        subscription.start_trial_flow(peer_id, user)
    elif text == subscription_catalog.one_time_button_label():
        subscription.begin_one_time_consultation_flow(peer_id, vk_user_id)
    else:
        vk.send_message(peer_id, "Выберите пункт меню или «Главное меню».", back_menu_keyboard())


def show_service_info(peer_id: int):
    from bot.keyboards import partners_catalog_keyboard

    msg = config.SERVICE_INFO_TEXT
    kb = partners_catalog_keyboard() if config.PARTNERS_CATALOG_URL else back_menu_keyboard()
    vk.send_message(peer_id, msg, kb)


def show_doctors(peer_id: int):
    msg = (
        "Раздел «Рекомендуемые врачи» в подготовке.\n"
        "Актуальная информация появится позже."
    )
    if config.PARTNERS_CATALOG_URL:
        msg += f"\n\nПодробнее: {config.PARTNERS_CATALOG_URL}"
    vk.send_message(peer_id, msg, back_menu_keyboard())


def show_clinics(peer_id: int):
    msg = (
        "Раздел «Партнёрские клиники» в подготовке.\n"
        "Актуальная информация появится позже."
    )
    if config.PARTNERS_CATALOG_URL:
        msg += f"\n\nПодробнее: {config.PARTNERS_CATALOG_URL}"
    vk.send_message(peer_id, msg, back_menu_keyboard())
