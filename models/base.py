from peewee import Model

from db import database


class BaseModel(Model):
    class Meta:
        database = database
