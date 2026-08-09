from datetime import datetime

from models import Payment, User
from services import payment_service, subscription_service


def test_one_time_credit(memory_db):
    user = User.create(vk_id=999010, created_at=datetime.utcnow())
    payment = Payment.create(
        user=user,
        order_id="exo_test_one_time",
        amount=2500,
        tariff="one_time",
        period_months=0,
        provider="mock",
        status="paid",
        created_at=datetime.utcnow(),
        paid_at=datetime.utcnow(),
    )
    assert payment_service.get_available_one_time_payment(user) is not None
    assert payment_service.has_ticket_access(user)
    assert not subscription_service.get_active_subscription(user)

    payment_service.consume_one_time(payment, ticket_id=42)
    assert payment_service.get_available_one_time_payment(user) is None


def test_one_time_does_not_grant_emergency(memory_db):
    user = User.create(vk_id=999011, created_at=datetime.utcnow())
    Payment.create(
        user=user,
        order_id="exo_test_emergency",
        amount=2500,
        tariff="one_time",
        period_months=0,
        provider="mock",
        status="paid",
        created_at=datetime.utcnow(),
        paid_at=datetime.utcnow(),
    )
    from services import ticket_service

    allowed, error = ticket_service.can_start_ticket(user, "emergency")
    assert not allowed
    assert error == "emergency_upsell_one_time"

    allowed, _ = ticket_service.can_start_ticket(user, "consultation")
    assert allowed
