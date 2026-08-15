"""Orchestration for dosage lookups: search → brief / QA / ask-AI."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import config
from models import User
from services import dosage_access, formulary_rag, formulary_search, llm_client

logger = logging.getLogger(__name__)


@dataclass
class DosageOutcome:
    kind: str  # brief | qa | ask_ai | miss | error | candidates
    text: str = ""
    drug_id: int | None = None
    candidates: list[Any] | None = None
    counted: bool = False


def search(query: str, *, limit: int = 5) -> list[formulary_search.DrugHit]:
    return formulary_search.search_drugs(query, limit=limit)


def deliver_brief(
    user: User,
    drug_id: int,
    *,
    user_query: str = "",
    apply_delay: bool = False,
) -> DosageOutcome:
    drug = formulary_search.get_drug(drug_id)
    if not drug:
        return DosageOutcome(kind="error", text="Препарат не найден в справочнике.")

    if apply_delay and config.FORMULARY_DOSAGE_DELAY_SEC > 0:
        time.sleep(config.FORMULARY_DOSAGE_DELAY_SEC)

    context = formulary_search.format_drug_context(drug)
    try:
        if not llm_client.is_configured():
            text = _fallback_brief(drug)
        else:
            text = llm_client.dosage_brief(context, user_query)
            if not (text or "").strip():
                text = _fallback_brief(drug)
    except llm_client.LLMError as exc:
        logger.warning("dosage_brief failed: %s", exc)
        text = _fallback_brief(drug)

    dosage_access.record_usage(user.vk_id, kind="dosage_hit", drug_id=drug_id)
    return DosageOutcome(kind="brief", text=text, drug_id=drug_id, counted=True)


def answer_qa(user: User, drug_id: int, question: str) -> DosageOutcome:
    """Follow-up Q&A on an already selected drug (does not consume free quota)."""
    drug = formulary_search.get_drug(drug_id)
    if not drug:
        return DosageOutcome(kind="error", text="Препарат не найден в справочнике.")

    context = formulary_search.format_drug_context(drug)
    chunks = formulary_rag.retrieve(question, drug_id=drug_id)
    chunks_text = formulary_rag.format_chunks_for_prompt(chunks)
    try:
        if not llm_client.is_configured():
            text = (
                "Полный ИИ-ответ недоступен (нет ANTHROPIC_API_KEY).\n\n"
                "Краткая карточка на русском:\n\n"
                + formulary_search.format_brief_ru(drug)
            )
        else:
            text = llm_client.dosage_qa(context, chunks_text, question)
    except llm_client.LLMError as exc:
        logger.warning("dosage_qa failed: %s", exc)
        return DosageOutcome(
            kind="error",
            text="Не удалось получить ответ ИИ. Попробуйте позже.",
            drug_id=drug_id,
        )
    return DosageOutcome(kind="qa", text=text, drug_id=drug_id, counted=False)


def ask_ai(user: User, question: str) -> DosageOutcome:
    """Free-form AI answer without KB; counts toward free dosage limit."""
    access = dosage_access.check_dosage_access(user)
    if not access.allowed:
        return DosageOutcome(
            kind="error",
            text="Лимит запросов исчерпан. Оформите подписку «Дозировки» (200 ₽/мес).",
        )
    try:
        if not llm_client.is_configured():
            text = (
                "ИИ недоступен: в .env не задан ANTHROPIC_API_KEY.\n\n"
                "Без ключа Claude нельзя перевести ответ и ответить вне справочника.\n"
                "⚠️ Это не замена справочнику и клиническому решению врача."
            )
        else:
            text = llm_client.ask_ai_fallback(question)
    except llm_client.LLMError as exc:
        logger.warning("ask_ai_fallback failed: %s", exc)
        return DosageOutcome(
            kind="error",
            text="Не удалось получить ответ ИИ. Попробуйте позже.",
        )
    dosage_access.record_usage(user.vk_id, kind="ask_ai", drug_id=None)
    return DosageOutcome(kind="ask_ai", text=text, counted=True)


def _fallback_brief(drug: formulary_search.DrugRecord) -> str:
    """Always Russian UI labels; no English field dump."""
    body = formulary_search.format_brief_ru(drug)
    note = ""
    if not llm_client.is_configured():
        note = (
            "\n\n⚠️ Перевод полного описания через Claude недоступен "
            "(нет ANTHROPIC_API_KEY). Ниже — структурированная сводка "
            "по справочнику на русском; фрагменты доз из EN-источников "
            "могут остаться на языке оригинала."
        )
    return body + note
