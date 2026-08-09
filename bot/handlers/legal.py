import config
from bot import keyboards, states
from integrations import vk
from services import bot_docs, legal_service, session, user_service
from services.gatekeeper import check_access


CLINIC_WARNING = (
    "Услуги партнёрской клиники, диагностика, стационар, манипуляции, "
    "очный приём и лечение оплачиваются владельцем отдельно по прайсу клиники."
)

ONBOARDING_CONSENT_PROMPT = (
    "Перед использованием сервиса ознакомьтесь с документами во вложении.\n"
    "После ознакомления нажмите зелёную кнопку «принять условия exo care»."
)


def start_legal_flow(peer_id: int, user):
    doc_keys = legal_service.onboarding_version_keys()
    session.set_state(
        user.vk_id,
        states.LEGAL_CHECKBOXES,
        {"doc_keys": doc_keys},
    )
    attachments = bot_docs.upload_legal_attachments(peer_id)
    if attachments:
        vk.send_message_attachments(
            peer_id,
            "Документы во вложении:",
            attachments,
        )
    else:
        vk.send_message(
            peer_id,
            "Не удалось прикрепить документы. Напишите в поддержку или попробуйте позже.",
        )
    vk.send_message(
        peer_id,
        ONBOARDING_CONSENT_PROMPT,
        keyboards.onboarding_consent_keyboard(config.LEGAL_ONBOARDING_PACKAGE_URL),
    )


def handle_legal_accept(vk_user_id: int, peer_id: int) -> bool:
    user = user_service.get_or_create_user(vk_user_id)
    payload = session.get_payload(vk_user_id)
    doc_keys = payload.get("doc_keys", legal_service.onboarding_version_keys())
    if not doc_keys:
        vk.send_message(peer_id, "Документы не настроены. Обратитесь в поддержку.")
        return False
    legal_service.save_consents(user, list(doc_keys), ip_address=None)
    session.clear_state(vk_user_id)
    gate = check_access(user)
    if gate.reason == "registration":
        from bot.handlers import registration
        registration.start_registration(peer_id, vk_user_id)
    elif gate.reason == "clinic_ack":
        start_clinic_ack(peer_id, vk_user_id)
    else:
        from bot.handlers.common import send_main_menu
        send_main_menu(peer_id, user, "Документы приняты.")
    return True


def start_clinic_ack(peer_id: int, vk_user_id: int | None = None):
    vk.send_message(peer_id, CLINIC_WARNING, keyboards.clinic_ack_keyboard())
    if vk_user_id:
        session.set_state(vk_user_id, states.CLINIC_ACK, {})


def handle_clinic_ack(vk_user_id: int, peer_id: int):
    user = user_service.get_or_create_user(vk_user_id)
    legal_service.set_clinic_ack(user)
    session.clear_state(vk_user_id)
    gate = check_access(user)
    if gate.reason == "registration":
        from bot.handlers import registration
        registration.start_registration(peer_id, vk_user_id)
    else:
        from bot.handlers.common import send_main_menu
        send_main_menu(peer_id, user)
