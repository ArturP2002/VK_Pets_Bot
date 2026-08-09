from unittest.mock import patch

from integrations import vk


def test_resolve_ticket_chat_prefers_urgency():
    with patch("integrations.vk.resolve_urgency_chat_peer_id", return_value=2000000100):
        with patch("integrations.vk.resolve_doctor_chat_peer_id", return_value=2000000058):
            assert vk.resolve_ticket_chat_peer_id("red") == 2000000100


def test_resolve_ticket_chat_fallback_to_doctor():
    with patch("integrations.vk.resolve_urgency_chat_peer_id", return_value=None):
        with patch("integrations.vk.resolve_doctor_chat_peer_id", return_value=2000000058):
            assert vk.resolve_ticket_chat_peer_id("green") == 2000000058
