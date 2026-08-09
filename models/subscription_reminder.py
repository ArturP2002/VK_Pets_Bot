from peewee import DateTimeField, ForeignKeyField, IntegerField

from models.base import BaseModel
from models.subscription import Subscription


class SubscriptionReminderSent(BaseModel):
    subscription = ForeignKeyField(Subscription, backref="reminders_sent", on_delete="CASCADE")
    days_before = IntegerField()
    sent_at = DateTimeField()

    class Meta:
        table_name = "subscription_reminder_sent"
        indexes = ((("subscription", "days_before"), True),)
