from peewee import BooleanField, CharField, DateTimeField, ForeignKeyField, IntegerField, TextField

from models.base import BaseModel


class User(BaseModel):
    vk_id = IntegerField(unique=True, index=True)
    vk_screen_name = CharField(null=True)
    full_name = CharField(null=True)
    phone = CharField(null=True)
    email = CharField(null=True)
    city = CharField(null=True)
    registered_at = DateTimeField(null=True)
    clinic_cost_ack = BooleanField(default=False)
    trial_used = BooleanField(default=False)
    assigned_doctor_vk_id = IntegerField(null=True)
    created_at = DateTimeField()

    class Meta:
        table_name = "users"


class UserSession(BaseModel):
    vk_user_id = IntegerField(unique=True, index=True)
    state = CharField(default="")
    payload = TextField(default="{}")
    active_pet_id = IntegerField(null=True)
    updated_at = DateTimeField()

    class Meta:
        table_name = "user_sessions"


class AuditLog(BaseModel):
    actor_vk_id = IntegerField(null=True)
    action = CharField()
    entity_type = CharField(null=True)
    entity_id = IntegerField(null=True)
    details = TextField(null=True)
    created_at = DateTimeField()

    class Meta:
        table_name = "audit_log"
