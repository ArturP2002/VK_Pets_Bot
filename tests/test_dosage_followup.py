"""Follow-up text after a drug card: a new name searches, a question stays Q&A."""
from types import SimpleNamespace

from bot.handlers.dosage import _looks_like_question, handle_dosage_message


def test_question_detector():
    assert _looks_like_question("какая доза кролику?")
    assert _looks_like_question("побочные эффекты")
    assert not _looks_like_question("мелоксикам")
    assert not _looks_like_question("Эссенциале")


def test_dosage_qa_drug_name_starts_new_search(monkeypatch):
    calls: dict[str, str] = {}

    monkeypatch.setattr("bot.handlers.dosage.session.get_state", lambda uid: "dosage_qa")
    monkeypatch.setattr(
        "bot.handlers.dosage.session.get_payload",
        lambda uid: {"drug_id": 7, "last_query": "ганатон"},
    )
    monkeypatch.setattr(
        "bot.handlers.dosage.user_service.get_or_create_user",
        lambda uid: SimpleNamespace(vk_id=uid),
    )
    monkeypatch.setattr(
        "bot.handlers.dosage._handle_query",
        lambda peer, user, text: calls.setdefault("query", text),
    )

    def _fail_qa(*args, **kwargs):
        raise AssertionError("drug name must not go to Q&A")

    monkeypatch.setattr("bot.handlers.dosage.dosage_service.answer_qa", _fail_qa)
    assert handle_dosage_message(1, 2, "мелоксикам") is True
    assert calls["query"] == "мелоксикам"


def test_dosage_qa_question_stays_on_current_drug(monkeypatch):
    calls: dict[str, str] = {}

    monkeypatch.setattr("bot.handlers.dosage.session.get_state", lambda uid: "dosage_qa")
    monkeypatch.setattr(
        "bot.handlers.dosage.session.get_payload",
        lambda uid: {"drug_id": 7},
    )
    monkeypatch.setattr(
        "bot.handlers.dosage.user_service.get_or_create_user",
        lambda uid: SimpleNamespace(vk_id=uid),
    )
    monkeypatch.setattr(
        "bot.handlers.dosage.vk.send_message",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "bot.handlers.dosage.dosage_access.can_ask_ai",
        lambda user: False,
    )

    def _qa(user, drug_id, text):
        calls["qa"] = text
        calls["drug_id"] = str(drug_id)
        return SimpleNamespace(text="ответ")

    monkeypatch.setattr("bot.handlers.dosage.dosage_service.answer_qa", _qa)
    assert handle_dosage_message(1, 2, "какая доза кролику?") is True
    assert calls["qa"] == "какая доза кролику?"
    assert calls["drug_id"] == "7"
