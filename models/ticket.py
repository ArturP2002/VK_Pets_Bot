from peewee import CharField, DateTimeField, ForeignKeyField, IntegerField, TextField

from models.base import BaseModel
from models.pet import Pet
from models.user import User


class Ticket(BaseModel):
    number = IntegerField(unique=True, index=True)
    user = ForeignKeyField(User, backref="tickets", on_delete="CASCADE")
    pet = ForeignKeyField(Pet, backref="tickets", on_delete="CASCADE")
    urgency = CharField(default="green")  # red, yellow, green
    symptoms_json = TextField(default="{}")
    status = CharField(default="created")
    assigned_doctor_vk_id = IntegerField(null=True)
    ticket_type = CharField(default="consultation")  # emergency, consultation, primary
    created_at = DateTimeField()
    updated_at = DateTimeField()

    class Meta:
        table_name = "tickets"


class TicketAttachment(BaseModel):
    ticket = ForeignKeyField(Ticket, backref="attachments", on_delete="CASCADE")
    vk_attachment = CharField(null=True)
    local_path = CharField(null=True)
    created_at = DateTimeField()

    class Meta:
        table_name = "ticket_attachments"


class TicketNote(BaseModel):
    ticket = ForeignKeyField(Ticket, backref="notes", on_delete="CASCADE")
    author_vk_id = IntegerField()
    text = TextField()
    created_at = DateTimeField()

    class Meta:
        table_name = "ticket_notes"


class TicketStatusHistory(BaseModel):
    ticket = ForeignKeyField(Ticket, backref="status_history", on_delete="CASCADE")
    old_status = CharField(null=True)
    new_status = CharField()
    changed_by_vk_id = IntegerField(null=True)
    created_at = DateTimeField()

    class Meta:
        table_name = "ticket_status_history"
