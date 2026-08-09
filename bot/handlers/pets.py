from bot import keyboards, states
from bot.keyboards import back_menu_keyboard, pet_actions_keyboard
from integrations import vk
from services import pet_service, session, user_service


def show_pets(peer_id: int, user):
    pets = pet_service.list_pets(user)
    active_id = session.get_active_pet_id(user.vk_id)
    if not pets:
        vk.send_message(
            peer_id,
            "У вас пока нет животных. Добавьте первого.",
            pet_actions_keyboard(),
        )
        return
    lines = ["Ваши питомцы:"]
    for p in pets:
        mark = " ★" if p.id == active_id else ""
        lines.append(f"#{p.id} {p.species} — {p.name}{mark}")
    vk.send_message(peer_id, "\n".join(lines), pet_actions_keyboard())


def show_pet_picker(peer_id: int, vk_user_id: int):
    user = user_service.get_or_create_user(vk_user_id)
    pets = pet_service.list_pets(user)
    if not pets:
        vk.send_message(peer_id, "Нет питомцев.", back_menu_keyboard())
        return
    vk.send_message(peer_id, "Выберите активного питомца:", keyboards.pet_picker_keyboard(pets, "pet_pick"))


def set_active_pet(peer_id: int, vk_user_id: int, pet_id: int):
    user = user_service.get_or_create_user(vk_user_id)
    pet = pet_service.get_pet(user, pet_id)
    if pet:
        session.set_active_pet(vk_user_id, pet_id)
        vk.send_message(peer_id, f"Активный питомец: {pet.name} (#{pet_id})", back_menu_keyboard())
    else:
        vk.send_message(peer_id, "Питомец не найден.", back_menu_keyboard())


def start_add_pet(peer_id: int, vk_user_id: int):
    user = user_service.get_or_create_user(vk_user_id)
    if not pet_service.can_add_pet(user):
        vk.send_message(peer_id, "Лимит животных для вашего тарифа. Повысьте подписку.", back_menu_keyboard())
        return
    session.set_state(vk_user_id, states.PET_SPECIES, {"pet": {}})
    vk.send_message(peer_id, "Вид животного (рептилия, птица, грызун, другое):")


def handle_pet_message(peer_id: int, vk_user_id: int, text: str) -> bool:
    s = session.get_session(vk_user_id)
    if not s.state.startswith("pet_"):
        return False
    data = session.get_payload(vk_user_id)
    pet = data.get("pet", {})
    state = s.state
    flow = [
        (states.PET_SPECIES, "species", states.PET_NAME, "Кличка:"),
        (states.PET_NAME, "name", states.PET_AGE, "Возраст:"),
        (states.PET_AGE, "age", states.PET_SEX, "Пол:"),
        (states.PET_SEX, "sex", states.PET_WEIGHT, "Вес:"),
        (states.PET_WEIGHT, "weight", states.PET_CAST, "Кастрация/стерилизация:"),
        (states.PET_CAST, "castration", states.PET_CHRONIC, "Хронические заболевания (или —):"),
        (states.PET_CHRONIC, "chronic_diseases", states.PET_PAST, "Перенесённые (или —):"),
        (states.PET_PAST, "past_diseases", states.PET_MEDS, "Препараты (или —):"),
        (states.PET_MEDS, "medications", states.PET_ALLERGY, "Аллергии (или —):"),
    ]
    for st, field, next_st, prompt in flow:
        if state == st:
            pet[field] = text.strip()
            session.set_state(vk_user_id, next_st, {"pet": pet})
            if next_st == states.PET_ALLERGY:
                vk.send_message(peer_id, prompt)
            else:
                vk.send_message(peer_id, prompt)
            return True
    if state == states.PET_ALLERGY:
        pet["allergies"] = text.strip()
        user = user_service.get_or_create_user(vk_user_id)
        created = pet_service.create_pet(user, **pet)
        session.clear_state(vk_user_id)
        if created:
            session.set_active_pet(vk_user_id, created.id)
            from services import chat_service, subscription_service
            if subscription_service.get_active_subscription(user):
                chat_service.add_user_to_species_chat(user, created.species)
            vk.send_message(peer_id, f"Питомец {created.name} добавлен!", back_menu_keyboard())
        else:
            vk.send_message(peer_id, "Не удалось добавить.", back_menu_keyboard())
        return True
    return False
