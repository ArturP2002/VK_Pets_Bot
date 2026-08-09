from peewee import BooleanField, CharField, IntegerField

from models.base import BaseModel


class Doctor(BaseModel):
    full_name = CharField()
    specialization = CharField(null=True)
    phone = CharField(null=True)
    vk_id = IntegerField(null=True)
    vk_profile_url = CharField(null=True)
    is_active = BooleanField(default=True)
    sort_order = IntegerField(default=0)

    class Meta:
        table_name = "doctors"


class Clinic(BaseModel):
    region = CharField(index=True)
    name = CharField()
    address = CharField(null=True)
    phone = CharField(null=True)
    is_active = BooleanField(default=True)

    class Meta:
        table_name = "clinics"
