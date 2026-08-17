"""LLM client for dosage / calculator flows (OpenAI or Anthropic)."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(config.BASE_DIR) / "prompts"

_anthropic_client = None
_openai_client = None


class LLMError(RuntimeError):
    """Raised when an LLM call fails or the selected provider is not configured."""


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def provider_name() -> str:
    """Normalized provider: openai | anthropic."""
    raw = (getattr(config, "LLM_PROVIDER", "") or "").strip().lower()
    if raw in ("openai", "gpt"):
        return "openai"
    if raw in ("claude", "anthropic"):
        return "anthropic"
    return "anthropic"


def is_configured() -> bool:
    if provider_name() == "anthropic":
        return bool(config.ANTHROPIC_API_KEY)
    return bool(getattr(config, "OPENAI_API_KEY", "") or "")


def reset_clients() -> None:
    """Drop cached SDK clients (tests / after .env change)."""
    global _anthropic_client, _openai_client
    _anthropic_client = None
    _openai_client = None


def _httpx_client(proxy: str):
    import httpx

    try:
        return httpx.Client(proxy=proxy, timeout=60.0)
    except TypeError:
        return httpx.Client(proxies=proxy, timeout=60.0)


def _anthropic():
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    if not config.ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    try:
        import anthropic
    except ImportError as exc:
        raise LLMError("anthropic package is not installed") from exc

    kwargs: dict[str, Any] = {"api_key": config.ANTHROPIC_API_KEY}
    proxy = (getattr(config, "ANTHROPIC_PROXY", None) or "").strip()
    if proxy:
        kwargs["http_client"] = _httpx_client(proxy)
        logger.info("Anthropic client uses proxy egress")
    _anthropic_client = anthropic.Anthropic(**kwargs)
    return _anthropic_client


def _openai():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    key = (getattr(config, "OPENAI_API_KEY", "") or "").strip()
    if not key:
        raise LLMError("OPENAI_API_KEY is not set")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMError("openai package is not installed") from exc

    kwargs: dict[str, Any] = {"api_key": key}
    proxy = (getattr(config, "OPENAI_PROXY", None) or "").strip()
    if proxy:
        kwargs["http_client"] = _httpx_client(proxy)
        logger.info("OpenAI client uses proxy egress")
    _openai_client = OpenAI(**kwargs)
    return _openai_client


def _default_model() -> str:
    if provider_name() == "anthropic":
        return config.CLAUDE_MODEL
    return getattr(config, "OPENAI_MODEL", "gpt-4.1")


def _max_tokens(explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    return int(getattr(config, "LLM_MAX_TOKENS", None) or config.CLAUDE_MAX_TOKENS)


def _openai_token_kwargs(model: str, max_tokens: int) -> dict[str, int]:
    low = (model or "").lower()
    if low.startswith("gpt-5") or low.startswith("o1") or low.startswith("o3"):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def chat(
    *,
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    json_mode: bool = False,
) -> str:
    """Plain text completion via the provider selected in LLM_PROVIDER."""
    if provider_name() == "anthropic":
        return _chat_anthropic(
            system=system,
            user=user,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    return _chat_openai(
        system=system,
        user=user,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
    )


def _chat_anthropic(
    *,
    system: str,
    user: str,
    model: str | None,
    max_tokens: int | None,
    temperature: float | None,
) -> str:
    client = _anthropic()
    params: dict[str, Any] = {
        "model": model or config.CLAUDE_MODEL,
        "max_tokens": _max_tokens(max_tokens),
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if temperature is not None:
        params["temperature"] = temperature
    try:
        message = client.messages.create(**params)
    except Exception as exc:
        raise LLMError(str(exc)) from exc
    parts = []
    for block in message.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _chat_openai(
    *,
    system: str,
    user: str,
    model: str | None,
    max_tokens: int | None,
    temperature: float | None,
    json_mode: bool,
) -> str:
    client = _openai()
    model_name = model or _default_model()
    params: dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        **_openai_token_kwargs(model_name, _max_tokens(max_tokens)),
    }
    if temperature is not None:
        params["temperature"] = temperature
    if json_mode:
        params["response_format"] = {"type": "json_object"}
    try:
        message = client.chat.completions.create(**params)
    except Exception as exc:
        raise LLMError(str(exc)) from exc
    choice = (message.choices or [None])[0]
    if choice is None:
        return ""
    content = getattr(choice.message, "content", None) or ""
    return str(content).strip()


def chat_json(
    *,
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Ask the LLM for a JSON object; parse robustly."""
    text = chat(
        system=system + "\n\nRespond with a single JSON object only, no markdown.",
        user=user,
        model=model,
        max_tokens=max_tokens,
        json_mode=True,
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
        raise LLMError(f"LLM did not return JSON: {text[:200]!r}")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise LLMError("LLM JSON root is not an object")
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


def dosage_brief(drug_context: str, user_query: str = "", display_title: str = "") -> str:
    system = _load_prompt("dosage_brief") or (
        "Ты ветеринарный помощник ExoCare. Отвечай ТОЛЬКО на русском, без Markdown. "
        "По карточке препарата дай краткую сводку врачу: мг/кг по таксонам/видам, "
        "путь введения, отличия по показаниям, источники. Переведи весь EN-текст. "
        "Не выдумывай дозы вне переданного контекста. "
        "В конце предложи кнопку «Калькулятор дозы»."
    )
    title_line = ""
    if (display_title or user_query or "").strip():
        title_line = (
            f"Показывай препарат в заголовке под названием: "
            f"{(display_title or user_query).strip()}\n"
        )
    user = (
        f"Запрос врача: {user_query or 'краткая сводка'}\n"
        f"{title_line}\n"
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
