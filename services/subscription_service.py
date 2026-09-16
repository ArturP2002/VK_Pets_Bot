from datetime import datetime, timedelta

import config
from models import Payment, Subscription, User


PLAN_RANK = {"trial": 0, "starter": 1, "basic": 2, "premium": 3}

# Consultation SKUs (tickets / pets). Separate from formulary addons.
CONSULTATION_PLANS = frozenset({"starter", "basic", "premium"})
# Formulary modules — can run in parallel with consultation plans and each other.
ADDON_PLANS = frozenset({"dosage", "calculator"})


def _active_query(user: User):
    now = datetime.utcnow()
    return Subscription.select().where(
        (Subscription.user == user)
        & (Subscription.status == "active")
        & (Subscription.ends_at >= now)
    )


def list_active_subscriptions(user: User) -> list[Subscription]:
    return list(_active_query(user).order_by(Subscription.ends_at.desc()))


def get_active_subscription(user: User) -> Subscription | None:
    """Primary consultation/trial subscription (excludes formulary addons)."""
    subs = list_active_subscriptions(user)
    for sub in subs:
        if sub.is_trial:
            return sub
    for sub in subs:
        if sub.plan in CONSULTATION_PLANS:
            return sub
    return None


def get_active_plan_subscription(user: User, plan: str) -> Subscription | None:
    """Active non-trial subscription for an exact plan id (e.g. dosage)."""
    for sub in list_active_subscriptions(user):
        if sub.is_trial:
            continue
        if sub.plan == plan:
            return sub
    return None


def has_active_trial(user: User) -> bool:
    return any(sub.is_trial for sub in list_active_subscriptions(user))


def effective_plan(user: User) -> str | None:
    sub = get_active_subscription(user)
    if sub:
        return "starter" if sub.is_trial else sub.plan
    return None


def start_trial(user: User) -> Subscription | None:
    if user.trial_used:
        return None
    now = datetime.utcnow()
    sub = Subscription.create(
        user=user,
        plan="starter",
        period_months=0,
        started_at=now,
        ends_at=now + timedelta(days=config.TRIAL_DAYS),
        status="active",
        is_trial=True,
    )
    user.trial_used = True
    user.save()
    return sub


def activate_after_payment(
    payment: Payment,
    rebill_id: str | None = None,
    card_mask: str | None = None,
) -> Subscription:
    user = payment.user
    now = datetime.utcnow()
    months = payment.period_months or 1
    plan = payment.tariff
    if plan == "one_time":
        return None  # type: ignore

    ends = now + timedelta(days=30 * months)
    parent = None

    # Stack only within the same SKU: consultation plans stack among themselves;
    # each addon stacks only with the same addon plan. Never expire cross-family.
    if plan in ADDON_PLANS:
        existing = get_active_plan_subscription(user, plan)
    else:
        existing = get_active_subscription(user)
        if existing and existing.is_trial:
            existing = None

    if existing and existing.ends_at > now:
        ends = existing.ends_at + timedelta(days=30 * months)
        parent = existing
        existing.status = "expired"
        existing.save()

    auto_renew = not payment.is_recurrent_charge
    sub = Subscription.create(
        user=user,
        plan=plan,
        period_months=months,
        started_at=now,
        ends_at=ends,
        status="active",
        payment=payment,
        is_trial=False,
        parent_subscription=parent,
        auto_renew=auto_renew,
    )
    if auto_renew and rebill_id:
        from services import recurrent_billing_service

        agreement = recurrent_billing_service.create_agreement_from_payment(
            payment, rebill_id, card_mask
        )
        if agreement:
            sub.recurrent_agreement = agreement
            sub.save()
    elif payment.is_recurrent_charge:
        from models import RecurrentAgreement

        agreement = (
            RecurrentAgreement.select()
            .where(
                (RecurrentAgreement.user == user)
                & (RecurrentAgreement.plan == plan)
                & (RecurrentAgreement.status == "active")
            )
            .order_by(RecurrentAgreement.agreed_at.desc())
            .first()
        )
        if agreement:
            sub.auto_renew = True
            sub.recurrent_agreement = agreement
            sub.save()
    return sub


def expire_subscription(sub: Subscription):
    sub.status = "expired"
    sub.save()


def can_use_feature(user: User, feature: str) -> bool:
    if feature == "drug_dosage":
        return True
    if feature == "dose_calculator":
        return True

    plan = effective_plan(user)
    if not plan:
        return feature == "view_history"
    if feature in ("view_history", "renew_subscription"):
        return True
    if feature == "planned_consultation":
        return plan in ("starter", "basic", "premium", "trial")
    if feature in ("emergency", "treatment_ticket"):
        return plan in ("basic", "premium")
    if feature == "multiple_pets":
        return plan in ("basic", "premium")
    if feature == "premium_features":
        return plan == "premium"
    return False


def on_subscription_expired(user: User):
    now = datetime.utcnow()
    for sub in Subscription.select().where(
        (Subscription.user == user)
        & (Subscription.status == "active")
        & (Subscription.ends_at < now)
    ):
        expire_subscription(sub)
