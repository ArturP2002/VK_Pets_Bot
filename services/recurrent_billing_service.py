import json
import logging
from datetime import datetime, timedelta

import config
from integrations.payment.factory import get_payment_provider
from models import CheckoutConsent, Payment, RecurrentAgreement, Subscription, User
from services import payment_service, subscription_service
from services.audit import log_action
from services.subscription_catalog import recurrent_charge_label

logger = logging.getLogger(__name__)

MAX_CHARGE_RETRIES = 2


def get_active_agreement(user: User) -> RecurrentAgreement | None:
    return (
        RecurrentAgreement.select()
        .where(
            (RecurrentAgreement.user == user)
            & (RecurrentAgreement.status == "active")
        )
        .order_by(RecurrentAgreement.agreed_at.desc())
        .first()
    )


def _consent_snapshot(payment: Payment) -> str:
    consent = (
        CheckoutConsent.select()
        .where(CheckoutConsent.payment == payment)
        .order_by(CheckoutConsent.accepted_at.desc())
        .first()
    )
    snapshot = {
        "amount": payment.amount,
        "period_months": payment.period_months,
        "tariff": payment.tariff,
        "document_versions": json.loads(consent.document_versions) if consent else [],
    }
    return json.dumps(snapshot, ensure_ascii=False)


def create_agreement_from_payment(
    payment: Payment,
    rebill_id: str | None,
    card_mask: str | None = None,
) -> RecurrentAgreement | None:
    if payment.tariff == "one_time":
        return None
    user = payment.user
    existing = get_active_agreement(user)
    if existing:
        existing.status = "expired"
        existing.save()
    sub = (
        Subscription.select()
        .where(Subscription.payment == payment)
        .order_by(Subscription.started_at.desc())
        .first()
    )
    next_charge = sub.ends_at if sub else datetime.utcnow() + timedelta(days=30 * payment.period_months)
    agreement = RecurrentAgreement.create(
        user=user,
        payment=payment,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        plan=payment.tariff,
        period_months=payment.period_months,
        amount=payment.amount,
        currency="RUB",
        rebill_id=rebill_id or payment_service.payment_meta(payment).get("rebill_id"),
        customer_key=payment_service.customer_key(user),
        card_mask=card_mask,
        agreed_at=datetime.utcnow(),
        next_charge_at=next_charge,
        status="active",
        consent_snapshot=_consent_snapshot(payment),
    )
    if sub:
        sub.auto_renew = True
        sub.recurrent_agreement = agreement
        sub.save()
    log_action(user.vk_id, "recurrent_agreement_created", "recurrent_agreement", agreement.id)
    return agreement


def create_mock_agreement(payment: Payment) -> RecurrentAgreement | None:
    return create_agreement_from_payment(
        payment,
        rebill_id=f"mock_rebill_{payment.order_id}",
        card_mask="430000******0777",
    )


def cancel_agreement(user: User) -> RecurrentAgreement | None:
    agreement = get_active_agreement(user)
    if not agreement:
        return None
    agreement.status = "cancelled"
    agreement.cancelled_at = datetime.utcnow()
    agreement.save()
    for sub in Subscription.select().where(
        (Subscription.user == user)
        & (Subscription.status == "active")
        & (Subscription.recurrent_agreement == agreement)
    ):
        sub.auto_renew = False
        sub.save()
    log_action(user.vk_id, "recurrent_agreement_cancelled", "recurrent_agreement", agreement.id)
    return agreement


def _extend_subscription(agreement: RecurrentAgreement, payment: Payment):
    user = agreement.user
    months = agreement.period_months
    sub = subscription_service.get_active_subscription(user)
    now = datetime.utcnow()
    if sub and sub.ends_at > now and not sub.is_trial:
        ends = sub.ends_at + timedelta(days=30 * months)
        sub.status = "expired"
        sub.save()
    else:
        ends = now + timedelta(days=30 * months)
    new_sub = Subscription.create(
        user=user,
        plan=agreement.plan,
        period_months=months,
        started_at=now,
        ends_at=ends,
        status="active",
        payment=payment,
        is_trial=False,
        auto_renew=True,
        recurrent_agreement=agreement,
        parent_subscription=sub,
    )
    agreement.next_charge_at = ends
    agreement.charge_retry_count = 0
    agreement.reminder_sent_for = None
    agreement.save()
    return new_sub


def process_charge_reminders():
    from services import notification_service

    tomorrow = (datetime.utcnow() + timedelta(days=1)).date()
    agreements = RecurrentAgreement.select().where(RecurrentAgreement.status == "active")
    for agreement in agreements:
        if not agreement.next_charge_at:
            continue
        if agreement.next_charge_at.date() != tomorrow:
            continue
        if agreement.reminder_sent_for and agreement.reminder_sent_for.date() == tomorrow:
            continue
        user = agreement.user
        notification_service.queue_notification(
            user.vk_id,
            "charge_reminder",
            {
                "amount": str(agreement.amount),
                "periodicity": recurrent_charge_label(agreement.period_months, agreement.amount),
                "charge_date": agreement.next_charge_at.strftime("%d.%m.%Y"),
            },
        )
        notification_service.queue_charge_reminder_email(user, agreement)
        agreement.reminder_sent_for = agreement.next_charge_at
        agreement.save()
    notification_service.process_pending()


def process_recurrent_charges():
    from services import chat_service, notification_service

    now = datetime.utcnow()
    today = now.date()
    provider = get_payment_provider()
    agreements = RecurrentAgreement.select().where(
        (RecurrentAgreement.status == "active") & (RecurrentAgreement.rebill_id.is_null(False))
    )
    for agreement in agreements:
        if not agreement.next_charge_at or agreement.next_charge_at.date() > today:
            continue
        user = agreement.user
        payment = payment_service.start_payment(
            user,
            agreement.plan,
            agreement.period_months,
            recurrent=False,
            is_recurrent_charge=True,
        )
        charge = provider.charge_recurrent(payment.external_id, agreement.rebill_id)
        if not charge.success:
            agreement.charge_retry_count += 1
            agreement.save()
            payment_service.fail_payment(payment)
            if agreement.charge_retry_count >= MAX_CHARGE_RETRIES:
                notification_service.queue_notification(
                    user.vk_id,
                    "charge_failed",
                    {
                        "amount": str(agreement.amount),
                        "ends_at": (
                            subscription_service.get_active_subscription(user).ends_at.strftime("%d.%m.%Y")
                            if subscription_service.get_active_subscription(user)
                            else "—"
                        ),
                    },
                )
            continue
        state = provider.get_state(charge.payment_id or payment.external_id)
        if state.get("Status") not in ("CONFIRMED", "AUTHORIZED") and not state.get("Success"):
            payment_service.fail_payment(payment)
            agreement.charge_retry_count += 1
            agreement.save()
            continue
        completed = payment_service.complete_payment(order_id=payment.order_id)
        if completed:
            _extend_subscription(agreement, completed)
            chat_service.sync_chats_for_user(user)
            notification_service.queue_notification(
                user.vk_id,
                "charge_success",
                {
                    "amount": str(agreement.amount),
                    "periodicity": recurrent_charge_label(agreement.period_months, agreement.amount),
                },
            )
            notification_service.queue_charge_notification(user, completed, is_first=False)
    notification_service.process_pending()
