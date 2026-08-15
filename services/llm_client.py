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
    temperature: float | None = None,
) -> str:
    """Plain text completion. Omits temperature by default (some Claude models reject it)."""
    client = _client()
    params: dict[str, Any] = {
        "model": model or config.CLAUDE_MODEL,
        "max_tokens": max_tokens or config.CLAUDE_MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if temperature is not None:
        params["temperature"] = temperature
    try:
        message = client.messages.create(**params)
    except Exception as exc:
        # Surface as LLMError so handlers can fall back instead of crashing Long Poll.
        raise LLMError(str(exc)) from exc
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


def strip_markdown_for_chat(text: str) -> str:
    """Remove Markdown chrome so VK messages look clean / premium."""
    if not text:
        return ""
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    out = re.sub(r"```[\w+-]*\n?", "", out)
    out = out.replace("```", "")
    # Headings: ### Title → Title
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", out)
    # Bold / italic
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", out)
    out = re.sub(r"__(.+?)__", r"\1", out)
    out = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", out)
    out = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"\1", out)
    # Links [label](url) → label
    out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out)
    # Blockquotes
    out = re.sub(r"(?m)^\s*>\s?", "", out)
    # Horizontal rules
    out = re.sub(r"(?m)^\s*([-*_]\s*){3,}\s*$", "", out)
    # Markdown list markers → bullet
    out = re.sub(r"(?m)^\s*[-*+]\s+", "• ", out)
    out = re.sub(r"(?m)^\s*\d+\.\s+", "• ", out)
    # Collapse excess blank lines
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def dosage_brief(drug_context: str, user_query: str = "") -> str:
    system = _load_prompt("dosage_brief") or (
        "Ты ветеринарный помощник ExoCare. Отвечай ТОЛЬКО на русском, без Markdown. "
        "По карточке препарата дай краткую сводку врачу: мг/кг по таксонам/видам, "
        "путь введения, отличия по показаниям, источники. Переведи весь EN-текст. "
        "Не выдумывай дозы вне переданного контекста. "
        "В конце предложи кнопку «Калькулятор дозы»."
    )
    user = (
        f"Запрос врача: {user_query or 'краткая сводка'}\n\n"
        "Сделай ответ полностью на русском, без Markdown-разметки.\n\n"
        f"Карточка:\n{drug_context}"
    )
    return strip_markdown_for_chat(chat(system=system, user=user))


def dosage_qa(drug_context: str, chunks: str, question: str) -> str:
    system = _load_prompt("dosage_qa") or (
        "Отвечай на вопрос врача строго по переданным секциям и RAG-чанкам. "
        "Если данных нет — скажи «в справочнике нет данных». Язык ответа: русский. Без Markdown."
    )
    user = (
        f"Вопрос: {question}\n\nКарточка:\n{drug_context}\n\nЧанки:\n{chunks or '(нет)'}"
    )
    return strip_markdown_for_chat(chat(system=system, user=user))


def ask_ai_fallback(question: str) -> str:
    system = _load_prompt("ask_ai_fallback") or (
        "Ты ветеринарный ИИ-помощник. Препарата нет в локальной базе. "
        "Ответь осторожно и обязательно добавь жёсткий дисклеймер: "
        "это не замена справочнику и клиническому решению врача; проверь первоисточник. Без Markdown."
    )
    return strip_markdown_for_chat(chat(system=system, user=question))


def calc_extract(free_text: str) -> dict[str, Any]:
    system = _load_prompt("calc_extract") or (
        "Извлеки параметры расчёта дозы из свободного русского текста в JSON: "
        "drug, species, weight_kg, dose_mg_per_kg, form (tablet|solution|other), "
        "mg_per_unit, concentration_mg_ml, route, notes, missing_fields. "
        "Не выдумывай числа. Понимай естественную речь без шаблонов."
    )
    user = (
        "Разбери свободный текст пользователя (без шаблона) и верни JSON.\n\n"
        f"Текст:\n{free_text}"
    )
    data = chat_json(system=system, user=user)
    if not isinstance(data, dict):
        raise LLMError("calc_extract: expected object")
    return data
