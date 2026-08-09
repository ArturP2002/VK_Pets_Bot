from integrations.vk import (
    _attachment_object_key,
    _attachment_string,
    _collect_message_attachment_objects,
    _is_user_owned_attachment,
    chat_peer_id,
)


def test_chat_peer_id_short():
    assert chat_peer_id(58) == 2000000058


def test_chat_peer_id_full():
    assert chat_peer_id(2000000058) == 2000000058


def test_attachment_string_with_access_key():
    assert _attachment_string("photo", {"owner_id": 1, "id": 2, "access_key": "abc"}) == "photo1_2_abc"


def test_is_user_owned_attachment():
    assert _is_user_owned_attachment("photo123_456") is True
    assert _is_user_owned_attachment("photo-239246256_789") is False


def test_collect_message_attachment_objects_dedupes():
    message = {
        "peer_id": 123,
        "conversation_message_id": 10,
        "is_cropped": True,
        "attachments": [
            {"type": "photo", "photo": {"owner_id": 1, "id": 1}},
            {"type": "photo", "photo": {"owner_id": 1, "id": 1}},
        ],
    }
    collected = _collect_message_attachment_objects(message)
    assert len(collected) == 1
    assert _attachment_object_key(collected[0]) == "photo1_1"
