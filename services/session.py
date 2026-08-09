import json
from datetime import datetime

from models import UserSession


def get_session(vk_user_id: int) -> UserSession:
    session, _ = UserSession.get_or_create(
        vk_user_id=vk_user_id,
        defaults={"state": "", "payload": "{}", "updated_at": datetime.utcnow()},
    )
    return session


def get_state(vk_user_id: int) -> str:
    return get_session(vk_user_id).state


def set_state(vk_user_id: int, state: str, payload: dict | None = None):
    session = get_session(vk_user_id)
    session.state = state
    if payload is not None:
        session.payload = json.dumps(payload, ensure_ascii=False)
    session.updated_at = datetime.utcnow()
    session.save()


def get_payload(vk_user_id: int) -> dict:
    session = get_session(vk_user_id)
    try:
        return json.loads(session.payload or "{}")
    except json.JSONDecodeError:
        return {}


def update_payload(vk_user_id: int, **kwargs):
    data = get_payload(vk_user_id)
    data.update(kwargs)
    set_state(vk_user_id, get_session(vk_user_id).state, data)


def clear_state(vk_user_id: int):
    set_state(vk_user_id, "", {})


def set_active_pet(vk_user_id: int, pet_id: int | None):
    session = get_session(vk_user_id)
    session.active_pet_id = pet_id
    session.updated_at = datetime.utcnow()
    session.save()


def get_active_pet_id(vk_user_id: int) -> int | None:
    return get_session(vk_user_id).active_pet_id
