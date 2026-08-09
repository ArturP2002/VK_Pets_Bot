from datetime import datetime

from models import Pet, Ticket, User
from services import staff_messages, ticket_service


def test_ticket_not_found_message():
    assert "не найдена" in staff_messages.ticket_not_found()


def test_format_tickets_list_empty():
    assert "нет" in staff_messages.format_tickets_list([])


def test_format_tickets_list_with_items(memory_db):
    user = User.create(vk_id=999201, created_at=datetime.utcnow())
    pet = Pet.create(user=user, species="рептилия", name="Тест", created_at=datetime.utcnow())
    ticket = Ticket.create(
        number=9901,
        user=user,
        pet=pet,
        urgency="green",
        status="waiting_doctor",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    text = staff_messages.format_tickets_list([ticket])
    assert "#9901" in text
    assert ticket_service.status_label("waiting_doctor") in text
    assert "Ticket" not in text
    assert "user=" not in text


def test_status_change_ok_uses_russian_label():
    msg = staff_messages.status_change_ok(5, "in_progress")
    assert "В работе" in msg
    assert "in_progress" not in msg
