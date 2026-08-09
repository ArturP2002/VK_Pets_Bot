from datetime import datetime, timedelta

from models import RecurrentAgreement, User
from services import payment_service, recurrent_billing_service, subscription_service


def test_create_and_cancel_agreement(memory_db):
    user = User.create(
        vk_id=2001,
        full_name="Test User",
        email="test@example.com",
        phone="+79001112233",
        created_at=datetime.utcnow(),
    )
    payment = payment_service.start_payment(user, "starter", 1, recurrent=True)
    payment_service.complete_payment(order_id=payment.order_id)
    agreement = recurrent_billing_service.create_mock_agreement(payment)
    assert agreement is not None
    assert agreement.status == "active"
    sub = subscription_service.get_active_subscription(user)
    assert sub.auto_renew is True

    cancelled = recurrent_billing_service.cancel_agreement(user)
    assert cancelled.status == "cancelled"
    assert recurrent_billing_service.get_active_agreement(user) is None


def test_recurrent_charge_extends_subscription(memory_db):
    user = User.create(vk_id=2002, created_at=datetime.utcnow())
    payment = payment_service.start_payment(user, "starter", 1, recurrent=True)
    payment_service.complete_payment(order_id=payment.order_id)
    agreement = recurrent_billing_service.create_mock_agreement(payment)
    sub = subscription_service.get_active_subscription(user)
    original_end = sub.ends_at
    agreement.next_charge_at = datetime.utcnow() - timedelta(hours=1)
    agreement.save()

    recurrent_billing_service.process_recurrent_charges()

    sub = subscription_service.get_active_subscription(user)
    agreement = RecurrentAgreement.get_by_id(agreement.id)
    assert sub.ends_at > original_end
    assert agreement.next_charge_at > original_end
