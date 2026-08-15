"""Anthropic Claude client for dosage / calculator flows."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(config.BASE_DIR) / "prompts"


class LLMError(RuntimeError):
    """Raised when Claude call fails or is not configured."""


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def is_configured() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


def _client():
    if not config.ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    try:
        import anthropic
    except ImportError as exc:
        raise LLMError("anthropic package is not installed") from exc
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def chat(
    *,
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.2,
) -> str:
    """Plain text completion."""
    client = _client()
    message = client.messages.create(
        model=model or config.CLAUDE_MODEL,
        max_tokens=max_tokens or config.CLAUDE_MAX_TOKENS,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts = []
    for block in message.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def chat_json(
    *,
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Ask Claude for a JSON object; parse robustly."""
    text = chat(
        system=system + "\n\nRespond with a single JSON object only, no markdown.",
        user=user,
        model=model,
        max_tokens=max_tokens,
        temperature=0,
    )
    return parse_json_object(text)


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        raise LLMError(f"Claude did not return JSON: {text[:200]!r}")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise LLMError("Claude JSON root is not an object")
    return data


def dosage_brief(drug_context: str, user_query: str = "") -> str:
    system = _load_prompt("dosage_brief") or (
        "Ты ветеринарный помощник ExoCare. По карточке препарата из справочника "
        "дай краткую сводку врачу на русском: мг/кг по таксонам/видам, путь введения, "
        "отличия по показаниям, источники. Не выдумывай дозы вне переданного контекста. "
        "В конце предложи кнопку «Калькулятор дозы»."
    )
    user = f"Запрос врача: {user_query or 'краткая сводка'}\n\nКарточка:\n{drug_context}"
    return chat(system=system, user=user)


def dosage_qa(drug_context: str, chunks: str, question: str) -> str:
    system = _load_prompt("dosage_qa") or (
        "Отвечай на вопрос врача строго по переданным секциям и RAG-чанкам. "
        "Если данных нет — скажи «в справочнике нет данных». Язык ответа: русский."
    )
    user = (
        f"Вопрос: {question}\n\nКарточка:\n{drug_context}\n\nЧанки:\n{chunks or '(нет)'}"
    )
    return chat(system=system, user=user)


def ask_ai_fallback(question: str) -> str:
    system = _load_prompt("ask_ai_fallback") or (
        "Ты ветеринарный ИИ-помощник. Препарата нет в локальной базе. "
        "Ответь осторожно и обязательно добавь жёсткий дисклеймер: "
        "это не замена справочнику и клиническому решению врача; проверь первоисточник."
    )
    return chat(system=system, user=question)


def calc_extract(free_text: str) -> dict[str, Any]:
    system = _load_prompt("calc_extract") or (
        "Извлеки параметры расчёта дозы в JSON со полями: "
        "drug, species, weight_kg, dose_mg_per_kg, form, mg_per_unit, "
        "concentration_mg_ml, route, notes, missing_fields (массив). "
        "Не выдумывай число мг/кг — если его нет во входе, поставь null."
    )
    return chat_json(system=system, user=free_text)
