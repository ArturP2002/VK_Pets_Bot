from peewee import CharField, DateTimeField, IntegerField, TextField

from models.base import BaseModel


class Notification(BaseModel):
    vk_user_id = IntegerField(index=True)
    template = CharField()
    payload = TextField(default="{}")
    status = CharField(default="pending")  # pending, sent, failed
    attempts = IntegerField(default=0)
    created_at = DateTimeField()
    sent_at = DateTimeField(null=True)

    class Meta:
        table_name = "notifications"
