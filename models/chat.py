from peewee import CharField, DateTimeField, ForeignKeyField, IntegerField

from models.base import BaseModel
from models.user import User


class SpeciesChat(BaseModel):
    species_key = CharField(unique=True)
    vk_chat_id = IntegerField()
    invite_link = CharField(null=True)

    class Meta:
        table_name = "species_chats"


class ChatMembership(BaseModel):
    user = ForeignKeyField(User, backref="chat_memberships", on_delete="CASCADE")
    species_chat = ForeignKeyField(SpeciesChat, backref="members", on_delete="CASCADE")
    joined_at = DateTimeField(null=True)
    removed_at = DateTimeField(null=True)

    class Meta:
        table_name = "chat_memberships"
