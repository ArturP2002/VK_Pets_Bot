from datetime import datetime

from models import User


def get_or_create_user(vk_id: int) -> User:
    user, created = User.get_or_create(
        vk_id=vk_id,
        defaults={"created_at": datetime.utcnow()},
    )
    return user


def profile_complete(user: User) -> bool:
    return bool(user.full_name and user.phone and user.email and user.city)


def update_profile(user: User, **kwargs):
    for key, value in kwargs.items():
        if hasattr(user, key) and value is not None:
            setattr(user, key, value)
    if profile_complete(user) and not user.registered_at:
        user.registered_at = datetime.utcnow()
    user.save()


def update_screen_name(user: User, screen_name: str | None):
    if screen_name:
        user.vk_screen_name = screen_name
        user.save()
