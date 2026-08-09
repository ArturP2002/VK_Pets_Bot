import json
from datetime import datetime

from models import Pet, Ticket, TicketAttachment, TicketNote, TicketStatusHistory, User
from services import subscription_service, urgency_service

TICKET_STATUSES = (
    "created",
    "waiting_doctor",
    "doctor_joined",
    "in_progress",
    "completed",
)

TICKET_STATUS_LABELS = {
    "created": "Создана",
    "waiting_doctor": "Ожидает врача",
    "doctor_joined": "Врач подключился",
    "in_progress": "В работе",
    "completed": "Завершена",
}

TICKET_TYPE_LABELS = {
    "emergency": "Экстренная помощь",
    "consultation": "Консультация",
    "primary": "Первичная консультация",
}


def status_label(status: str) -> str:
    return TICKET_STATUS_LABELS.get(status, status)


def ticket_type_label(ticket_type: str) -> str:
    return TICKET_TYPE_LABELS.get(ticket_type, ticket_type)

ALLOWED_TRANSITIONS = {
    "created": {"waiting_doctor"},
    "waiting_doctor": {"doctor_joined", "in_progress", "completed"},
    "doctor_joined": {"in_progress", "completed"},
    "in_progress": {"completed", "waiting_doctor"},
    "completed": set(),
}


def next_ticket_number() -> int:
    last = Ticket.select().order_by(Ticket.number.desc()).first()
    return (last.number + 1) if last else 1


def can_transition(old_status: str, new_status: str) -> bool:
    if new_status not in TICKET_STATUSES:
        return False
    allowed = ALLOWED_TRANSITIONS.get(old_status, set())
    return new_status in allowed or old_status == new_status


def create_ticket(
    user: User,
    pet: Pet,
    symptoms: dict,
    ticket_type: str = "consultation",
    worsening: bool = False,
) -> Ticket:
    urgency = (
        "red"
        if ticket_type == "emergency"
        else urgency_service.classify_urgency(symptoms, worsening=worsening)
    )
    now = datetime.utcnow()
    ticket = Ticket.create(
        number=next_ticket_number(),
        user=user,
        pet=pet,
        urgency=urgency,
        symptoms_json=json.dumps(symptoms, ensure_ascii=False),
        status="waiting_doctor",
        ticket_type=ticket_type,
        created_at=now,
        updated_at=now,
    )
    TicketStatusHistory.create(
        ticket=ticket,
        old_status="created",
        new_status="waiting_doctor",
        created_at=now,
    )
    return ticket


def change_status(ticket: Ticket, new_status: str, actor_vk_id: int | None = None) -> bool:
    if not can_transition(ticket.status, new_status):
        return False
    old = ticket.status
    ticket.status = new_status
    ticket.updated_at = datetime.utcnow()
    ticket.save()
    TicketStatusHistory.create(
        ticket=ticket,
        old_status=old,
        new_status=new_status,
        changed_by_vk_id=actor_vk_id,
        created_at=datetime.utcnow(),
    )
    return True


def assign_doctor(ticket: Ticket, doctor_vk_id: int) -> bool:
    ticket.assigned_doctor_vk_id = doctor_vk_id
    if ticket.status == "waiting_doctor":
        return change_status(ticket, "doctor_joined", doctor_vk_id)
    if ticket.status in ("doctor_joined", "in_progress"):
        ticket.updated_at = datetime.utcnow()
        ticket.save()
        return True
    ticket.updated_at = datetime.utcnow()
    ticket.save()
    return False


def add_note(ticket: Ticket, author_vk_id: int, text: str) -> TicketNote:
    return TicketNote.create(
        ticket=ticket,
        author_vk_id=author_vk_id,
        text=text,
        created_at=datetime.utcnow(),
    )


def add_attachment(ticket: Ticket | None, vk_attachment: str, local_path: str | None = None):
    if not ticket:
        return None
    return TicketAttachment.create(
        ticket=ticket,
        vk_attachment=vk_attachment,
        local_path=local_path,
        created_at=datetime.utcnow(),
    )


def list_attachments(ticket: Ticket) -> list[TicketAttachment]:
    return list(TicketAttachment.select().where(TicketAttachment.ticket == ticket))


def vk_attachments_param(ticket: Ticket, limit: int = 10, target_peer_id: int | None = None) -> list[str]:
    """VK attachment strings for messages.send (max 10 per message)."""
    from integrations import vk

    parts = []
    for att in list_attachments(ticket):
        if not att.vk_attachment:
            continue
        normalized = vk.reupload_attachment_for_community(att.vk_attachment, peer_id=target_peer_id)
        if normalized:
            parts.append(normalized)
    return parts[:limit]


def save_pending_attachments(ticket: Ticket, attachment_strings: list[str]):
    for att in attachment_strings:
        add_attachment(ticket, att)


def list_user_tickets(user: User) -> list[Ticket]:
    return list(Ticket.select().where(Ticket.user == user).order_by(Ticket.created_at.desc()))


def list_open_user_tickets(user: User, limit: int = 10) -> list[Ticket]:
    return list(
        Ticket.select()
        .where((Ticket.user == user) & (Ticket.status != "completed"))
        .order_by(Ticket.created_at.desc())
        .limit(limit)
    )


def get_ticket_by_number(number: int) -> Ticket | None:
    return Ticket.get_or_none(Ticket.number == number)


def parse_ticket_number(raw: str) -> int | None:
    cleaned = (raw or "").strip().lstrip("#")
    if cleaned.isdigit():
        return int(cleaned)
    return None


def get_user_ticket(user: User, number: int) -> Ticket | None:
    return Ticket.get_or_none((Ticket.number == number) & (Ticket.user == user))


def can_create_emergency(user: User) -> bool:
    return subscription_service.can_use_feature(user, "emergency")


def can_start_ticket(user: User, ticket_type: str) -> tuple[bool, str | None]:
    from services import payment_service

    has_sub = bool(subscription_service.get_active_subscription(user))
    has_one_time = payment_service.get_available_one_time_payment(user) is not None

    if not has_sub and not has_one_time:
        return False, (
            "Нужна активная подписка, пробный период или оплаченная разовая консультация."
        )

    if ticket_type == "emergency":
        if can_create_emergency(user):
            return True, None
        if has_one_time and not has_sub:
            return False, "emergency_upsell_one_time"
        return False, "emergency_upsell"

    if has_sub or has_one_time:
        return True, None
    return False, "Нужна активная подписка или разовая консультация."
