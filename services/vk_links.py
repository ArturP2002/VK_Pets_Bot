"""VK profile URL helpers."""


def user_profile_url(vk_id: int, screen_name: str | None = None) -> str:
    if screen_name:
        return f"https://vk.com/{screen_name}"
    return f"https://vk.com/id{vk_id}"


def default_doctor_profile_url() -> str:
    import config

    if config.VK_DOCTOR_PROFILE_URL:
        return config.VK_DOCTOR_PROFILE_URL
    if config.VK_DOCTOR_PROFILE_ID:
        return f"https://vk.com/id{config.VK_DOCTOR_PROFILE_ID}"
    return "https://vk.com/"


def doctor_profile_url(vk_id: int | None, profile_url: str | None = None) -> str:
    if profile_url:
        return profile_url
    if vk_id:
        return f"https://vk.com/id{vk_id}"
    return default_doctor_profile_url()
