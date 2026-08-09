from datetime import datetime

from models import AuditLog


def log_action(actor_vk_id: int | None, action: str, entity_type: str | None = None,
               entity_id: int | None = None, details: str | None = None):
    AuditLog.create(
        actor_vk_id=actor_vk_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        created_at=datetime.utcnow(),
    )
