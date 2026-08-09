from peewee import BooleanField, CharField, DateTimeField, ForeignKeyField, TextField

from models.base import BaseModel
from models.user import User


class LegalDocument(BaseModel):
    doc_type = CharField(index=True)
    title = CharField()
    version = CharField()
    google_doc_url = CharField()
    is_active = BooleanField(default=True)

    class Meta:
        table_name = "legal_documents"


class ConsentRecord(BaseModel):
    user = ForeignKeyField(User, backref="consents", on_delete="CASCADE")
    accepted_at = DateTimeField()
    document_versions = TextField()  # JSON list of "type:version"
    ip_address = CharField(null=True)

    class Meta:
        table_name = "consent_records"
