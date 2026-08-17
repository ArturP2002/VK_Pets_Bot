"""LLM provider switch: OpenAI vs Anthropic."""
from __future__ import annotations

import pytest

from services import llm_client


@pytest.fixture(autouse=True)
def _reset_llm_clients():
    llm_client.reset_clients()
    yield
    llm_client.reset_clients()


def test_provider_name_aliases(monkeypatch):
    monkeypatch.setattr("config.LLM_PROVIDER", "claude")
    assert llm_client.provider_name() == "anthropic"
    monkeypatch.setattr("config.LLM_PROVIDER", "GPT")
    assert llm_client.provider_name() == "openai"
    monkeypatch.setattr("config.LLM_PROVIDER", "openai")
    assert llm_client.provider_name() == "openai"


def test_is_configured_follows_selected_provider(monkeypatch):
    monkeypatch.setattr("config.LLM_PROVIDER", "openai")
    monkeypatch.setattr("config.OPENAI_API_KEY", "")
    monkeypatch.setattr("config.ANTHROPIC_API_KEY", "sk-ant-x")
    assert llm_client.is_configured() is False

    monkeypatch.setattr("config.OPENAI_API_KEY", "sk-openai-x")
    assert llm_client.is_configured() is True

    monkeypatch.setattr("config.LLM_PROVIDER", "anthropic")
    monkeypatch.setattr("config.ANTHROPIC_API_KEY", "")
    assert llm_client.is_configured() is False
    monkeypatch.setattr("config.ANTHROPIC_API_KEY", "sk-ant-x")
    assert llm_client.is_configured() is True


def test_chat_openai_uses_gpt_model(monkeypatch):
    monkeypatch.setattr("config.LLM_PROVIDER", "openai")
    monkeypatch.setattr("config.OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr("config.OPENAI_MODEL", "gpt-4.1")
    monkeypatch.setattr("config.OPENAI_PROXY", "")
    monkeypatch.setattr("config.LLM_MAX_TOKENS", 2048)

    captured: dict = {}

    class FakeMessage:
        content = "ok-ru"

    class FakeChoice:
        message = FakeMessage()

    class FakeResp:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return FakeResp()

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = FakeChat()

    monkeypatch.setattr(llm_client, "_openai", lambda: FakeOpenAI(api_key="sk-test"))
    text = llm_client.chat(system="sys", user="hello", json_mode=True)
    assert text == "ok-ru"
    assert captured["model"] == "gpt-4.1"
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["messages"][0]["role"] == "system"


def test_chat_anthropic_still_works(monkeypatch):
    monkeypatch.setattr("config.LLM_PROVIDER", "anthropic")
    monkeypatch.setattr("config.ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setattr("config.CLAUDE_MODEL", "claude-sonnet-5")
    monkeypatch.setattr("config.LLM_MAX_TOKENS", 2048)

    captured: dict = {}

    class FakeBlock:
        text = "claude-ok"

    class FakeMessage:
        content = [FakeBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return FakeMessage()

    class FakeAnthropic:
        messages = FakeMessages()

    monkeypatch.setattr(llm_client, "_anthropic", lambda: FakeAnthropic())
    text = llm_client.chat(system="sys", user="hello")
    assert text == "claude-ok"
    assert captured["model"] == "claude-sonnet-5"


def test_openai_token_kwargs_gpt5():
    assert llm_client._openai_token_kwargs("gpt-5", 100) == {
        "max_completion_tokens": 100
    }
    assert llm_client._openai_token_kwargs("gpt-4.1", 100) == {"max_tokens": 100}


def test_parse_json_error_is_provider_neutral():
    with pytest.raises(llm_client.LLMError, match="LLM did not return JSON"):
        llm_client.parse_json_object("not-json")
