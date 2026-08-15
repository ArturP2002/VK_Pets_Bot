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


def test_addon_and_starter_agreements_coexist(memory_db):
    user = User.create(
        vk_id=2003,
        full_name="Addon User",
        email="addon@example.com",
        phone="+79001112234",
        created_at=datetime.utcnow(),
    )
    starter_pay = payment_service.start_payment(user, "starter", 1, recurrent=True)
    payment_service.complete_payment(order_id=starter_pay.order_id)
    starter_ag = recurrent_billing_service.create_mock_agreement(starter_pay)

    dosage_pay = payment_service.start_payment(user, "dosage", 1, recurrent=True)
    payment_service.complete_payment(order_id=dosage_pay.order_id)
    dosage_ag = recurrent_billing_service.create_mock_agreement(dosage_pay)

    assert starter_ag.status == "active"
    assert dosage_ag.status == "active"
    assert RecurrentAgreement.get_by_id(starter_ag.id).status == "active"
    assert len(recurrent_billing_service.list_active_agreements(user)) == 2

    dosage_ag.next_charge_at = datetime.utcnow() - timedelta(hours=1)
    dosage_ag.save()
    starter_end = subscription_service.get_active_subscription(user).ends_at
    dosage_end = subscription_service.get_active_plan_subscription(user, "dosage").ends_at

    recurrent_billing_service.process_recurrent_charges()

    assert subscription_service.get_active_subscription(user).ends_at == starter_end
    assert (
        subscription_service.get_active_plan_subscription(user, "dosage").ends_at
        > dosage_end
    )
