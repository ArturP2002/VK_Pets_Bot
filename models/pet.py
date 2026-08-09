from peewee import CharField, DateTimeField, ForeignKeyField, TextField

from models.base import BaseModel
from models.user import User


class Pet(BaseModel):
    user = ForeignKeyField(User, backref="pets", on_delete="CASCADE")
    species = CharField()
    name = CharField()
    age = CharField(null=True)
    sex = CharField(null=True)
    weight = CharField(null=True)
    castration = CharField(null=True)
    chronic_diseases = TextField(null=True)
    past_diseases = TextField(null=True)
    medications = TextField(null=True)
    allergies = TextField(null=True)
    created_at = DateTimeField()

    class Meta:
        table_name = "pets"
