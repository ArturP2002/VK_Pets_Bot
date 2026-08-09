import json

from bot.keyboards import (
    CHECKOUT_CONSENT_BUTTON_LABEL,
    VK_BUTTON_LABEL_MAX,
    checkout_consent_keyboard,
    onboarding_consent_keyboard,
)


def _button_labels(keyboard_json: str) -> list[str]:
    data = json.loads(keyboard_json)
    labels = []
    for row in data.get("buttons", []):
        for btn in row:
            labels.append(btn.get("action", {}).get("label", ""))
    return labels


def test_onboarding_consent_keyboard():
    kb = onboarding_consent_keyboard("https://storage.yandexcloud.net/example/docs")
    assert kb
    assert '"action":' in kb
    assert "принять условия exo care" in kb
    assert "Ознакомиться с документами" in kb


def test_onboarding_consent_keyboard_without_url():
    kb = onboarding_consent_keyboard("")
    assert "принять условия exo care" in kb
    assert kb.count('"action":') == 1


def test_checkout_consent_keyboard():
    kb = checkout_consent_keyboard("https://storage.yandexcloud.net/example/checkout")
    assert kb
    assert CHECKOUT_CONSENT_BUTTON_LABEL in kb
    assert "Ознакомиться с документами" in kb
    for label in _button_labels(kb):
        assert len(label) <= VK_BUTTON_LABEL_MAX


def test_all_consent_keyboard_labels_within_vk_limit():
    for kb in (
        onboarding_consent_keyboard("https://example.com/docs"),
        checkout_consent_keyboard("https://example.com/checkout"),
    ):
        for label in _button_labels(kb):
            assert len(label) <= VK_BUTTON_LABEL_MAX
