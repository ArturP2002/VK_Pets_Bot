import config


def is_admin(vk_id: int) -> bool:
    return vk_id in config.VK_ADMIN_IDS


def _doctor_vk_ids_from_db() -> set[int]:
    from models import Doctor

    return {
        d.vk_id
        for d in Doctor.select().where(
            (Doctor.is_active == True) & (Doctor.vk_id.is_null(False)) & (Doctor.vk_id > 0)
        )
    }


def is_doctor_chat(peer_id: int | None) -> bool:
    try:
        from integrations import vk

        return vk.is_staff_chat(peer_id)
    except Exception:
        return False


def is_doctor(vk_id: int) -> bool:
    return vk_id in config.VK_DOCTOR_IDS or vk_id in _doctor_vk_ids_from_db() or is_admin(vk_id)


def can_manage_tickets(vk_id: int, peer_id: int | None = None) -> bool:
    if is_admin(vk_id) or vk_id in config.VK_DOCTOR_IDS:
        return True
    if vk_id in _doctor_vk_ids_from_db():
        return True
    if peer_id and is_doctor_chat(peer_id):
        return True
    return False
