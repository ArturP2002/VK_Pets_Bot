from datetime import datetime

from models import Pet, User
from services import subscription_service


def list_pets(user: User) -> list[Pet]:
    return list(Pet.select().where(Pet.user == user).order_by(Pet.id))


def can_add_pet(user: User) -> bool:
    pets = list_pets(user)
    if subscription_service.can_use_feature(user, "multiple_pets"):
        return True
    return len(pets) < 1


def create_pet(user: User, **fields) -> Pet | None:
    if not can_add_pet(user):
        return None
    return Pet.create(user=user, created_at=datetime.utcnow(), **fields)


def get_pet(user: User, pet_id: int) -> Pet | None:
    return Pet.get_or_none((Pet.id == pet_id) & (Pet.user == user))
