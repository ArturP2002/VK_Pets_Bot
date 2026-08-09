"""Seed initial data: legal docs, sample doctors/clinics."""
import json
from datetime import datetime

import config
from db import database, init_db
from models import Clinic, Doctor, LegalDocument, SpeciesChat
import os


def seed_legal_documents():
    """Activate only the real BotDocs package; deactivate legacy placeholder types."""
    active_types = {doc_type for doc_type, _ in config.LEGAL_DOC_TYPES}
    for doc in LegalDocument.select():
        if doc.doc_type not in active_types and doc.is_active:
            doc.is_active = False
            doc.save()

    for doc_type, title in config.LEGAL_DOC_TYPES:
        doc, created = LegalDocument.get_or_create(
            doc_type=doc_type,
            version="1.0",
            defaults={
                "title": title,
                "google_doc_url": "",
                "is_active": True,
            },
        )
        if not created:
            doc.title = title
            doc.is_active = True
            doc.save()


def seed_doctors():
    if Doctor.select().count() > 0:
        return
    Doctor.create(
        full_name="Иванов И.И.",
        specialization="Экзотические рептилии",
        phone="+79001234567",
        vk_id=0,
        vk_profile_url="https://vk.com/",
        sort_order=1,
    )


def seed_clinics():
    if Clinic.select().count() > 0:
        return
    clinics = [
        ("Москва", "Клиника ЭкзоВет", "ул. Примерная, 1", "+74950000001"),
        ("Москва", "Альфа Экзотик", "пр. Тестовый, 5", "+74950000002"),
        ("Санкт-Петербург", "Бета Вет", "Невский пр., 10", "+78120000001"),
    ]
    for region, name, address, phone in clinics:
        Clinic.create(region=region, name=name, address=address, phone=phone)


def seed_species_chats():
    mapping = {
        "рептилия": os.getenv("VK_CHAT_REPTILE", ""),
        "птица": os.getenv("VK_CHAT_BIRD", ""),
        "грызун": os.getenv("VK_CHAT_RODENT", ""),
        "другое": os.getenv("VK_CHAT_OTHER", ""),
    }
    for species_key, chat_id in mapping.items():
        if chat_id and str(chat_id).isdigit():
            SpeciesChat.get_or_create(
                species_key=species_key,
                defaults={"vk_chat_id": int(chat_id)},
            )


def run_seed():
    init_db()
    seed_legal_documents()
    seed_doctors()
    seed_clinics()
    seed_species_chats()
    print("Seed completed.")


if __name__ == "__main__":
    run_seed()
