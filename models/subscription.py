from peewee import BooleanField, CharField, DateTimeField, ForeignKeyField, IntegerField, TextField

from models.base import BaseModel
from models.user import User


class Payment(BaseModel):
    user = ForeignKeyField(User, backref="payments", on_delete="CASCADE")
    order_id = CharField(unique=True, index=True)
    amount = IntegerField()
    tariff = CharField()
    period_months = IntegerField(default=1)
    provider = CharField(default="mock")
    external_id = CharField(null=True)
    status = CharField(default="pending")  # pending, paid, failed
    raw_json = TextField(null=True)
    created_at = DateTimeField()
    paid_at = DateTimeField(null=True)
    is_recurrent_charge = BooleanField(default=False)

    class Meta:
        table_name = "payments"


class RecurrentAgreement(BaseModel):
    user = ForeignKeyField(User, backref="recurrent_agreements", on_delete="CASCADE")
    payment = ForeignKeyField(Payment, null=True, backref="recurrent_agreement_link", on_delete="SET NULL")
    full_name = CharField(null=True)
    email = CharField(null=True)
    phone = CharField(null=True)
    plan = CharField()
    period_months = IntegerField()
    amount = IntegerField()
    currency = CharField(default="RUB")
    rebill_id = CharField(null=True)
    customer_key = CharField()
    card_mask = CharField(null=True)
    agreed_at = DateTimeField()
    next_charge_at = DateTimeField(null=True)
    status = CharField(default="active")  # active, cancelled, expired
    consent_snapshot = TextField(null=True)
    cancelled_at = DateTimeField(null=True)
    charge_retry_count = IntegerField(default=0)
    reminder_sent_for = DateTimeField(null=True)

    class Meta:
        table_name = "recurrent_agreements"


class CheckoutConsent(BaseModel):
    user = ForeignKeyField(User, backref="checkout_consents", on_delete="CASCADE")
    payment = ForeignKeyField(Payment, null=True, backref="checkout_consent", on_delete="SET NULL")
    tariff = CharField()
    period_months = IntegerField()
    amount = IntegerField()
    document_versions = TextField()
    accepted_at = DateTimeField()

    class Meta:
        table_name = "checkout_consents"


class Subscription(BaseModel):
    user = ForeignKeyField(User, backref="subscriptions", on_delete="CASCADE")
    plan = CharField()
    period_months = IntegerField(default=1)
    started_at = DateTimeField()
    ends_at = DateTimeField()
    status = CharField(default="active")
    payment = ForeignKeyField(Payment, null=True, backref="subscription_link", on_delete="SET NULL")
    is_trial = BooleanField(default=False)
    parent_subscription = ForeignKeyField("self", null=True, backref="renewals", on_delete="SET NULL")
    auto_renew = BooleanField(default=False)
    recurrent_agreement = ForeignKeyField(
        RecurrentAgreement, null=True, backref="subscriptions", on_delete="SET NULL"
    )

    class Meta:
        table_name = "subscriptions"
