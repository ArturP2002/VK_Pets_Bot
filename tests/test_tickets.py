from datetime import datetime

from models import Pet, Ticket, User
from services import ticket_service


def test_parse_ticket_number():
    assert ticket_service.parse_ticket_number("7") == 7
    assert ticket_service.parse_ticket_number("#7") == 7
    assert ticket_service.parse_ticket_number(" #7 ") == 7
    assert ticket_service.parse_ticket_number("abc") is None


def test_list_open_user_tickets_excludes_completed(memory_db):
    user = User.create(vk_id=999100, created_at=datetime.utcnow())
    pet = Pet.create(
        user=user,
        species="рептилия",
        name="Test",
        created_at=datetime.utcnow(),
    )
    now = datetime.utcnow()
    Ticket.create(
        number=1,
        user=user,
        pet=pet,
        urgency="green",
        symptoms_json="{}",
        status="waiting_doctor",
        ticket_type="consultation",
        created_at=now,
        updated_at=now,
    )
    Ticket.create(
        number=2,
        user=user,
        pet=pet,
        urgency="green",
        symptoms_json="{}",
        status="completed",
        ticket_type="consultation",
        created_at=now,
        updated_at=now,
    )

    open_tickets = ticket_service.list_open_user_tickets(user)
    assert len(open_tickets) == 1
    assert open_tickets[0].number == 1
