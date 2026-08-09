import json
import logging

from models import Doctor, Ticket, TicketNote, TicketStatusHistory
from services import ticket_service, urgency_service, vk_links

logger = logging.getLogger(__name__)


SYMPTOM_LABELS = {
    "complaints": "Жалобы",
    "started_at": "Когда началось",
    "activity": "Активность",
    "appetite": "Аппетит",
    "stool": "Стул",
    "urine": "Мочеиспускание",
    "temperature": "Температура",
}


def format_pet_block(pet) -> str:
    lines = [
        f"Вид: {pet.species}",
        f"Кличка: {pet.name}",
        f"Возраст: {pet.age or '—'}",
        f"Пол: {pet.sex or '—'}",
        f"Вес: {pet.weight or '—'}",
        f"Кастрация: {pet.castration or '—'}",
    ]
    if pet.chronic_diseases:
        lines.append(f"Хронические: {pet.chronic_diseases}")
    if pet.past_diseases:
        lines.append(f"Перенесённые: {pet.past_diseases}")
    if pet.medications:
        lines.append(f"Препараты: {pet.medications}")
    if pet.allergies:
        lines.append(f"Аллергии: {pet.allergies}")
    return "\n".join(lines)


def format_symptoms_block(symptoms: dict) -> str:
    lines = []
    for key, label in SYMPTOM_LABELS.items():
        val = symptoms.get(key)
        if val:
            lines.append(f"{label}: {val}")
    return "\n".join(lines) if lines else "—"


def format_attachments_block(ticket: Ticket) -> str:
    attachments = ticket_service.list_attachments(ticket)
    if not attachments:
        return "Файлы: нет"
    counts = {"photo": 0, "video": 0, "doc": 0, "other": 0}
    for att in attachments:
        raw = att.vk_attachment or att.local_path or ""
        if raw.startswith("photo"):
            counts["photo"] += 1
        elif raw.startswith("video"):
            counts["video"] += 1
        elif raw.startswith("doc"):
            counts["doc"] += 1
        elif raw:
            counts["other"] += 1
    parts = []
    if counts["photo"]:
        n = counts["photo"]
        parts.append(f"{n} фото")
    if counts["video"]:
        n = counts["video"]
        parts.append(f"{n} видео")
    if counts["doc"]:
        n = counts["doc"]
        parts.append(f"{n} документ")
    if counts["other"]:
        parts.append(f"{counts['other']} файл")
    summary = ", ".join(parts) if parts else "нет"
    if counts["photo"] or counts["video"] or counts["doc"]:
        return f"Файлы: {summary} (прикреплены к сообщению)"
    return f"Файлы: {summary}"


def format_status_history(ticket: Ticket) -> str:
    history = list(
        TicketStatusHistory.select()
        .where(TicketStatusHistory.ticket == ticket)
        .order_by(TicketStatusHistory.created_at)
    )
    if not history:
        return ""
    lines = ["История статусов:"]
    for h in history:
        ts = h.created_at.strftime("%d.%m %H:%M") if h.created_at else ""
        old = ticket_service.status_label(h.old_status) if h.old_status else "—"
        new = ticket_service.status_label(h.new_status)
        lines.append(f"• {ts} {old} → {new}")
    return "\n".join(lines)


def format_notes_block(ticket: Ticket) -> str:
    notes = list(
        TicketNote.select().where(TicketNote.ticket == ticket).order_by(TicketNote.created_at)
    )
    if not notes:
        return ""
    lines = ["Заключения врача:"]
    for n in notes:
        lines.append(f"— {n.text}")
    return "\n".join(lines)


def format_ticket_card(ticket: Ticket, for_user: bool = False) -> str:
    user = ticket.user
    pet = ticket.pet
    symptoms = json.loads(ticket.symptoms_json or "{}")
    label = urgency_service.urgency_label(ticket.urgency)
    lines = [
        f"Заявка #{ticket.number} [{label}]",
        f"Тип: {ticket_service.ticket_type_label(ticket.ticket_type)}",
    ]
    if not for_user:
        lines.extend([
            f"Владелец: {user.full_name or '—'}",
            f"Телефон: {user.phone or '—'}",
            f"Эл. почта: {user.email or '—'}",
            f"Город: {user.city or '—'}",
            f"Профиль ВКонтакте: {vk_links.user_profile_url(user.vk_id, user.vk_screen_name)}",
        ])
    lines.extend([
        "",
        "Питомец:",
        format_pet_block(pet),
        "",
        "Анкета:",
        format_symptoms_block(symptoms),
        "",
        format_attachments_block(ticket),
        f"Статус: {ticket_service.status_label(ticket.status)}",
    ])
    notes = format_notes_block(ticket)
    if notes:
        lines.extend(["", notes])
    if for_user:
        hist = format_status_history(ticket)
        if hist:
            lines.extend(["", hist])
    elif not for_user:
        hist = format_status_history(ticket)
        if hist:
            lines.extend(["", hist])
    return "\n".join(lines)


def get_doctor_for_assign(doctor_vk_id: int) -> Doctor | None:
    return Doctor.get_or_none((Doctor.vk_id == doctor_vk_id) & (Doctor.is_active == True))


def _send_ticket_message(peer_id: int, text: str, keyboard: str | None, attachments: list[str]):
    from integrations import vk

    if attachments:
        vk.send_message_attachments(peer_id, text, attachments, keyboard=keyboard)
    else:
        vk.send_message(peer_id, text, keyboard=keyboard)


def notify_new_ticket(ticket: Ticket) -> int:
    """Send ticket card to configured doctor destinations. Returns delivery count."""
    import config
    import vk_api
    from bot.keyboards import doctor_ticket_keyboard
    from integrations import vk

    card = format_ticket_card(ticket)
    kb = doctor_ticket_keyboard(ticket.number)
    ticket_peer_id = vk.resolve_ticket_chat_peer_id(ticket.urgency)
    attachments = ticket_service.vk_attachments_param(ticket, target_peer_id=ticket_peer_id)
    sent = 0
    is_premium = False
    try:
        from services import subscription_service
        sub = subscription_service.get_active_subscription(ticket.user)
        if sub and not sub.is_trial and sub.plan == "premium":
            is_premium = True
    except Exception:
        pass
    prefix = "⭐ Премиум\n" if is_premium else ""
    if ticket.urgency == "red":
        prefix = "🚨 " + prefix

    if ticket_peer_id:
        try:
            _send_ticket_message(ticket_peer_id, prefix + card, kb, attachments)
            sent += 1
            logger.info(
                "Ticket #%s sent to %s chat peer_id=%s",
                ticket.number,
                ticket.urgency,
                ticket_peer_id,
            )
        except vk_api.exceptions.ApiError as e:
            logger.error(
                "Urgency chat send failed for ticket #%s peer_id=%s [%s]: %s",
                ticket.number,
                ticket_peer_id,
                e.code,
                e,
            )
        except Exception:
            logger.exception("Failed urgency chat notify for ticket #%s", ticket.number)
    elif config.VK_DOCTOR_CHAT_ID or config.VK_DOCTOR_CHAT_TITLE or any(
        t
        for t in (
            config.VK_CHAT_URGENCY_RED_TITLE,
            config.VK_CHAT_URGENCY_YELLOW_TITLE,
            config.VK_CHAT_URGENCY_GREEN_TITLE,
        )
        if t
    ):
        logger.error(
            "Ticket #%s: chat not resolved for urgency=%s (doctor title=%r)",
            ticket.number,
            ticket.urgency,
            config.VK_DOCTOR_CHAT_TITLE,
        )

    notified_ids: set[int] = set()
    for doctor_id in config.VK_DOCTOR_IDS:
        if doctor_id in notified_ids:
            continue
        if vk.is_chat_peer_id(doctor_id):
            logger.warning(
                "VK_DOCTOR_IDS: %s — это peer_id беседы, а не vk_id пользователя. "
                "Укажите числовой id врача (vk.com/id123). Пропуск.",
                doctor_id,
            )
            continue
        if ticket_peer_id and doctor_id == ticket_peer_id:
            continue
        try:
            _send_ticket_message(doctor_id, prefix + card, kb, attachments)
            notified_ids.add(doctor_id)
            sent += 1
        except vk_api.exceptions.ApiError as e:
            logger.warning("Cannot notify doctor vk_id=%s [%s]: %s", doctor_id, e.code, e)
        except Exception:
            logger.exception("Failed doctor vk_id=%s", doctor_id)

    for admin_id in config.VK_ADMIN_IDS:
        if admin_id in notified_ids:
            continue
        if vk.is_chat_peer_id(admin_id):
            logger.warning(
                "VK_ADMIN_IDS: %s — это peer_id беседы, а не vk_id пользователя. Пропуск.",
                admin_id,
            )
            continue
        try:
            _send_ticket_message(admin_id, prefix + card, kb, attachments)
            notified_ids.add(admin_id)
            sent += 1
        except vk_api.exceptions.ApiError as e:
            logger.warning("Cannot notify admin vk_id=%s [%s]: %s", admin_id, e.code, e)
        except Exception:
            logger.exception("Failed admin vk_id=%s", admin_id)

    if sent == 0:
        logger.warning("Ticket #%s: no doctor notifications sent", ticket.number)
    return sent


def log_doctor_destinations_status():
    import config
    from integrations import vk

    if not (
        config.VK_DOCTOR_CHAT_ID
        or config.VK_DOCTOR_CHAT_TITLE
        or config.VK_DOCTOR_IDS
        or config.VK_CHAT_URGENCY_RED_TITLE
        or config.VK_CHAT_URGENCY_YELLOW_TITLE
        or config.VK_CHAT_URGENCY_GREEN_TITLE
    ):
        logger.warning("Doctor notifications not configured")
        return
    for level, title in (
        ("red", config.VK_CHAT_URGENCY_RED_TITLE),
        ("yellow", config.VK_CHAT_URGENCY_YELLOW_TITLE),
        ("green", config.VK_CHAT_URGENCY_GREEN_TITLE),
    ):
        if title:
            peer_id = vk.resolve_urgency_chat_peer_id(level)
            if peer_id:
                logger.info("Urgency chat %s OK: peer_id=%s (title=%r)", level, peer_id, title)
            else:
                logger.error("Urgency chat %s: not found (title=%r)", level, title)
    if config.VK_DOCTOR_CHAT_ID or config.VK_DOCTOR_CHAT_TITLE:
        peer_id = vk.resolve_doctor_chat_peer_id()
        if peer_id:
            logger.info("Doctor chat OK: peer_id=%s (title=%r)", peer_id, config.VK_DOCTOR_CHAT_TITLE)
        else:
            _, hint = vk.check_chat_access()
            logger.error("Doctor chat: %s", hint)
    for doctor_id in config.VK_DOCTOR_IDS:
        if vk.is_chat_peer_id(doctor_id):
            logger.warning(
                "VK_DOCTOR_IDS=%s — peer_id беседы, не vk_id. "
                "Заявки уже идут в чат врачей; для личных сообщений укажите id пользователя.",
                doctor_id,
            )
