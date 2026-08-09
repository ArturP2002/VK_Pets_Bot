from datetime import datetime

from integrations import vk
from models import ChatMembership, SpeciesChat, User


SPECIES_MAP = {
    "рептилия": "рептилия",
    "птица": "птица",
    "птицы": "птица",
    "грызун": "грызун",
    "грызуны": "грызун",
}


def normalize_species(species: str) -> str:
    s = (species or "").lower().strip()
    return SPECIES_MAP.get(s, "другое")


def get_species_chat(species: str) -> SpeciesChat | None:
    key = normalize_species(species)
    return SpeciesChat.get_or_none(SpeciesChat.species_key == key)


def add_user_to_species_chat(user: User, species: str, notify: bool = True) -> bool:
    chat = get_species_chat(species)
    if not chat:
        return False
    ok = vk.add_user_to_chat(chat.vk_chat_id, user.vk_id)
    if ok:
        membership, _ = ChatMembership.get_or_create(
            user=user,
            species_chat=chat,
            defaults={"joined_at": datetime.utcnow()},
        )
        membership.removed_at = None
        membership.joined_at = datetime.utcnow()
        membership.save()
        return True
    if chat.invite_link and notify:
        vk.send_message(
            user.vk_id,
            f"Чат по виду «{species}». Перейдите по ссылке для вступления:\n{chat.invite_link}",
        )
        return True
    return False


def remove_from_all_chats(user: User):
    for m in ChatMembership.select().where(
        (ChatMembership.user == user) & (ChatMembership.removed_at.is_null())
    ):
        chat = m.species_chat
        vk.remove_user_from_chat(chat.vk_chat_id, user.vk_id)
        m.removed_at = datetime.utcnow()
        m.save()


def sync_chats_for_user(user: User):
    from services import pet_service
    pets = pet_service.list_pets(user)
    for p in pets:
        add_user_to_species_chat(user, p.species, notify=True)
