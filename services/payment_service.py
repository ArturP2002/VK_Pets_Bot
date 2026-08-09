import json
import uuid
from datetime import datetime

import config
from integrations.payment.factory import get_payment_provider
from integrations.payment.tbank import tariff_description
from models import Payment, User
from services import subscription_service
from services.audit import log_action


def _order_id() -> str:
    return f"exo_{uuid.uuid4().hex[:12]}"


def payment_meta(payment: Payment) -> dict:
    try:
        return json.loads(payment.raw_json or "{}")
    except json.JSONDecodeError:
        return {}


def get_payment_url(payment: Payment) -> str | None:
    return payment_meta(payment).get("payment_url")


def get_available_one_time_payment(user: User) -> Payment | None:
    for payment in (
        Payment.select()
        .where(
            (Payment.user == user)
            & (Payment.tariff == "one_time")
            & (Payment.status == "paid")
        )
        .order_by(Payment.paid_at.desc())
    ):
        if not payment_meta(payment).get("consumed"):
            return payment
    return None


def consume_one_time(payment: Payment, ticket_id: int):
    meta = payment_meta(payment)
    meta["consumed"] = True
    meta["ticket_id"] = ticket_id
    payment.raw_json = json.dumps(meta, ensure_ascii=False)
    payment.save()
    log_action(payment.user.vk_id, "one_time_consumed", "payment", payment.id, str(ticket_id))


def has_ticket_access(user: User) -> bool:
    if subscription_service.get_active_subscription(user):
        return True
    return get_available_one_time_payment(user) is not None


def customer_key(user: User) -> str:
    return f"vk_{user.vk_id}"


def start_payment(
    user: User,
    tariff: str,
    period_months: int = 1,
    *,
    recurrent: bool = False,
    is_recurrent_charge: bool = False,
) -> Payment:
    if tariff == "one_time":
        amount = config.ONE_TIME_CONSULTATION_PRICE
        period = 0
        description = tariff_description(tariff, 0)
        recurrent = False
    else:
        prices = config.TARIFF_PRICES.get(tariff, {})
        amount = prices.get(period_months, 0)
        period = period_months
        description = tariff_description(tariff, period)
    order_id = _order_id()
    provider = get_payment_provider()
    result = provider.create_payment(
        order_id=order_id,
        amount=amount,
        description=description,
        user_id=user.vk_id,
        recurrent=recurrent and not is_recurrent_charge,
        customer_key=customer_key(user),
        email=user.email,
        phone=user.phone,
    )
    meta = {
        "payment_url": result.payment_url,
        "provider_response": result.raw if hasattr(result, "raw") else {},
        "recurrent": recurrent,
        "customer_key": customer_key(user),
    }
    payment = Payment.create(
        user=user,
        order_id=order_id,
        amount=amount,
        tariff=tariff,
        period_months=period,
        provider=provider.name,
        external_id=result.external_id,
        status="pending",
        raw_json=json.dumps({k: v for k, v in meta.items() if v is not None}, ensure_ascii=False),
        created_at=datetime.utcnow(),
        is_recurrent_charge=is_recurrent_charge,
    )
    log_action(user.vk_id, "payment_started", "payment", payment.id, order_id)
    return payment


def complete_payment(
    order_id: str | None = None,
    external_id: str | None = None,
    *,
    rebill_id: str | None = None,
    card_mask: str | None = None,
) -> Payment | None:
    if order_id:
        payment = Payment.get_or_none(Payment.order_id == order_id)
    elif external_id:
        payment = Payment.get_or_none(Payment.external_id == external_id)
    else:
        return None
    if not payment:
        return None
    if payment.status == "paid":
        return payment
    payment.status = "paid"
    payment.paid_at = datetime.utcnow()
    meta = payment_meta(payment)
    if rebill_id:
        meta["rebill_id"] = rebill_id
    if card_mask:
        meta["card_mask"] = card_mask
    payment.raw_json = json.dumps(meta, ensure_ascii=False)
    payment.save()
    log_action(payment.user.vk_id, "payment_completed", "payment", payment.id)
    if payment.tariff != "one_time" and not payment.is_recurrent_charge:
        subscription_service.activate_after_payment(payment, rebill_id=rebill_id, card_mask=card_mask)
    return payment


def fail_payment(payment: Payment):
    payment.status = "failed"
    payment.save()
    log_action(payment.user.vk_id, "payment_failed", "payment", payment.id)


def refund_payment(payment: Payment):
    payment.status = "failed"
    meta = payment_meta(payment)
    meta["refunded"] = True
    payment.raw_json = json.dumps(meta, ensure_ascii=False)
    payment.save()
    log_action(payment.user.vk_id, "payment_refunded", "payment", payment.id)


def get_payment_by_order(order_id: str) -> Payment | None:
    return Payment.get_or_none(Payment.order_id == order_id)


def list_user_payments(user: User, limit: int = 10):
    return list(
        Payment.select()
        .where(Payment.user == user)
        .order_by(Payment.created_at.desc())
        .limit(limit)
    )
