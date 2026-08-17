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


_client_singleton = None


def _client():
    """Anthropic client; optional ANTHROPIC_PROXY / HTTPS_PROXY for geo egress."""
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton
    if not config.ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    try:
        import anthropic
        import httpx
    except ImportError as exc:
        raise LLMError("anthropic package is not installed") from exc

    kwargs: dict[str, Any] = {"api_key": config.ANTHROPIC_API_KEY}
    proxy = (getattr(config, "ANTHROPIC_PROXY", None) or "").strip()
    if proxy:
        # httpx 0.28+: proxy= ; older: proxies=
        try:
            http_client = httpx.Client(proxy=proxy, timeout=60.0)
        except TypeError:
            http_client = httpx.Client(proxies=proxy, timeout=60.0)
        kwargs["http_client"] = http_client
        logger.info("Anthropic client uses proxy egress")
    _client_singleton = anthropic.Anthropic(**kwargs)
    return _client_singleton


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


def translate_drug_names(drugs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Batch medical RU translation for foreign formulary drug names.

    Each input dict should include canonical_name_en; optional action, formulations,
    trade_names, sources. Returns list aligned with input:
    {"canonical_name_en", "canonical_name_ru", "search_aliases": [...]}.
    """
    if not drugs:
        return []
    system = _load_prompt("drug_name_translate") or (
        "Переведи МНН препаратов на устоявшееся русское медицинское написание. "
        "Ответ — JSON {\"translations\": [{\"canonical_name_en\", "
        "\"canonical_name_ru\", \"search_aliases\"}]}, порядок как во входе."
    )
    lines: list[str] = []
    for i, drug in enumerate(drugs, start=1):
        en = (drug.get("canonical_name_en") or "").strip()
        trade = drug.get("trade_names") or []
        if isinstance(trade, str):
            trade = [trade]
        ctx_parts = [f"{i}. canonical_name_en: {en}"]
        if trade:
            ctx_parts.append(f"   trade_names: {', '.join(str(t) for t in trade[:8])}")
        if drug.get("action"):
            ctx_parts.append(f"   action: {(drug['action'] or '')[:300]}")
        if drug.get("formulations"):
            ctx_parts.append(f"   formulations: {(drug['formulations'] or '')[:200]}")
        sources = drug.get("sources") or []
        if sources:
            ctx_parts.append(f"   sources: {', '.join(str(s) for s in sources)}")
        lines.append("\n".join(ctx_parts))
    user = "Препараты для перевода:\n\n" + "\n\n".join(lines)
    data = chat_json(system=system, user=user, max_tokens=4096)
    raw_items = data.get("translations")
    if not isinstance(raw_items, list):
        raise LLMError("translate_drug_names: expected translations array")

    out: list[dict[str, Any]] = []
    for i, drug in enumerate(drugs):
        en = (drug.get("canonical_name_en") or "").strip()
        item = raw_items[i] if i < len(raw_items) else {}
        if not isinstance(item, dict):
            item = {}
        name_ru = (item.get("canonical_name_ru") or "").strip()
        aliases_raw = item.get("search_aliases") or []
        if isinstance(aliases_raw, str):
            aliases_raw = [aliases_raw]
        aliases = [str(a).strip() for a in aliases_raw if str(a).strip()]
        out.append(
            {
                "canonical_name_en": en,
                "canonical_name_ru": name_ru,
                "search_aliases": aliases,
            }
        )
    return out
