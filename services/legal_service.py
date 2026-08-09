import json
from datetime import datetime

import config
from models import CheckoutConsent, ConsentRecord, LegalDocument, Payment, User
from services.audit import log_action


def get_active_documents() -> list[LegalDocument]:
    return list(
        LegalDocument.select()
        .where(LegalDocument.is_active == True)
        .order_by(LegalDocument.doc_type)
    )


def get_onboarding_documents() -> list[LegalDocument]:
    allowed = set(config.ONBOARDING_LEGAL_DOC_TYPES)
    return [d for d in get_active_documents() if d.doc_type in allowed]


def onboarding_version_keys() -> list[str]:
    return [f"{d.doc_type}:{d.version}" for d in get_onboarding_documents()]


def onboarding_document_labels() -> dict[str, str]:
    return {f"{d.doc_type}:{d.version}": d.title for d in get_onboarding_documents()}


def current_version_keys() -> list[str]:
    return onboarding_version_keys()


def document_labels() -> dict[str, str]:
    return onboarding_document_labels()


def has_all_consents(user: User) -> bool:
    required = set(current_version_keys())
    if not required:
        return False
    latest = (
        ConsentRecord.select()
        .where(ConsentRecord.user == user)
        .order_by(ConsentRecord.accepted_at.desc())
        .first()
    )
    if not latest:
        return False
    try:
        accepted = set(json.loads(latest.document_versions))
    except json.JSONDecodeError:
        accepted = set()
    return required.issubset(accepted)


def save_consents(user: User, accepted_keys: list[str], ip_address: str | None = None):
    ConsentRecord.create(
        user=user,
        accepted_at=datetime.utcnow(),
        document_versions=json.dumps(accepted_keys, ensure_ascii=False),
        ip_address=ip_address,
    )
    log_action(user.vk_id, "consent_accepted", "user", user.id)


def needs_clinic_ack(user: User) -> bool:
    return not user.clinic_cost_ack


def set_clinic_ack(user: User):
    user.clinic_cost_ack = True
    user.save()


def get_document_by_type(doc_type: str) -> LegalDocument | None:
    return (
        LegalDocument.select()
        .where((LegalDocument.doc_type == doc_type) & (LegalDocument.is_active == True))
        .order_by(LegalDocument.version.desc())
        .first()
    )


def checkout_consent_keys(tariff: str) -> list[str]:
    if tariff == "one_time":
        doc_types = config.CHECKOUT_CONSENT_KEYS["one_time"]
    else:
        doc_types = config.CHECKOUT_CONSENT_KEYS["subscription"]
    keys = []
    for doc_type in doc_types:
        doc = get_document_by_type(doc_type)
        if doc:
            keys.append(f"{doc.doc_type}:{doc.version}")
    return keys


def checkout_consent_labels(keys: list[str]) -> dict[str, str]:
    labels = {}
    for key in keys:
        doc_type = key.split(":")[0]
        doc = get_document_by_type(doc_type)
        if doc:
            labels[key] = doc.title
        else:
            labels[key] = doc_type
    return labels


def save_checkout_consent(
    user: User,
    tariff: str,
    period_months: int,
    amount: int,
    accepted_keys: list[str],
    payment: Payment | None = None,
) -> CheckoutConsent:
    record = CheckoutConsent.create(
        user=user,
        payment=payment,
        tariff=tariff,
        period_months=period_months,
        amount=amount,
        document_versions=json.dumps(accepted_keys, ensure_ascii=False),
        accepted_at=datetime.utcnow(),
    )
    log_action(user.vk_id, "checkout_consent", "checkout_consent", record.id, tariff)
    return record
