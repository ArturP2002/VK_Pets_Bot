from services.session import get_state, set_state


def test_get_state(memory_db):
    set_state(42, "test_state", {"a": 1})
    assert get_state(42) == "test_state"
