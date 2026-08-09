from models.base import BaseModel
from models.user import User, UserSession, AuditLog
from models.legal import LegalDocument, ConsentRecord
from models.pet import Pet
from models.subscription import CheckoutConsent, Payment, RecurrentAgreement, Subscription
from models.subscription_reminder import SubscriptionReminderSent
from models.ticket import Ticket, TicketAttachment, TicketNote, TicketStatusHistory
from models.catalog import Doctor, Clinic
from models.chat import SpeciesChat, ChatMembership
from models.notification import Notification

ALL_MODELS = [
    User,
    UserSession,
    AuditLog,
    LegalDocument,
    ConsentRecord,
    Pet,
    Subscription,
    Payment,
    RecurrentAgreement,
    CheckoutConsent,
    SubscriptionReminderSent,
    Ticket,
    TicketAttachment,
    TicketNote,
    TicketStatusHistory,
    Doctor,
    Clinic,
    SpeciesChat,
    ChatMembership,
    Notification,
]

__all__ = [
    "BaseModel",
    "User",
    "UserSession",
    "AuditLog",
    "LegalDocument",
    "ConsentRecord",
    "Pet",
    "Subscription",
    "Payment",
    "RecurrentAgreement",
    "CheckoutConsent",
    "SubscriptionReminderSent",
    "Ticket",
    "TicketAttachment",
    "TicketNote",
    "TicketStatusHistory",
    "Doctor",
    "Clinic",
    "SpeciesChat",
    "ChatMembership",
    "Notification",
    "ALL_MODELS",
]
