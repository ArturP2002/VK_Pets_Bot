from unittest.mock import patch

from services import rbac


def test_can_manage_tickets_in_doctor_chat_without_env_ids():
    with patch("integrations.vk.resolve_doctor_chat_peer_id", return_value=2000000001):
        assert rbac.can_manage_tickets(12345, peer_id=2000000001)
        assert not rbac.can_manage_tickets(12345, peer_id=12345)


def test_can_manage_tickets_from_env():
    with patch("services.rbac.config") as cfg:
        cfg.VK_ADMIN_IDS = []
        cfg.VK_DOCTOR_IDS = [999]
        with patch("services.rbac._doctor_vk_ids_from_db", return_value=set()):
            assert rbac.can_manage_tickets(999)
            assert not rbac.can_manage_tickets(1)
