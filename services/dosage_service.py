"""Orchestration for dosage lookups: search → brief / QA / ask-AI."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import config
from models import User
from scripts.formulary.common import fold_match_key
from scripts.formulary.junk_names import is_junk_drug_name
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


def pick_search_hits(hits: list[formulary_search.DrugHit]) -> list[formulary_search.DrugHit]:
    """
    Collapse search results for UI: drop junk, dedupe labels, one exact hit → card;
    several exact hits → show only those; weak fuzzy → miss (no garbage buttons).
    """
    if not hits:
        return hits
    hits = formulary_search.filter_search_hits(hits)
    if not hits:
        return hits
    exact = [h for h in hits if h.is_exact]
    if exact:
        inn_exact = [
            h
            for h in exact
            if fold_match_key(h.canonical_name_en) == fold_match_key(h.matched_alias)
            or fold_match_key(h.canonical_name_ru) == fold_match_key(h.matched_alias)
        ]
        return inn_exact or exact
    confident = [h for h in hits if h.score >= 90]
    return confident


def search_with_analogs(query: str, *, limit: int = 5) -> list[formulary_search.DrugHit]:
    """Local search, then brand→INN resolver if there is no confident hit."""
    hits = pick_search_hits(search(query, limit=limit))
    if hits:
        return hits
    from services.drug_alias_resolver import resolve_brand

    resolved = resolve_brand(query)
    if not resolved:
        return []
    seen: set[int] = set()
    analog_hits: list[formulary_search.DrugHit] = []
    for term in resolved.inn_terms:
        for hit in pick_search_hits(search(term, limit=limit)):
            if hit.drug_id in seen:
                continue
            seen.add(hit.drug_id)
            analog_hits.append(hit)
        if analog_hits:
            break
    return analog_hits


def deliver_brief(
    user: User,
    drug_id: int,
    *,
    user_query: str = "",
    display_title: str = "",
    apply_delay: bool = False,
) -> DosageOutcome:
    drug = formulary_search.get_drug(drug_id)
    if not drug:
        return DosageOutcome(kind="error", text="Препарат не найден в справочнике.")

    title = formulary_search.resolve_display_title(
        user_query or display_title,
        drug=drug,
    )

    if is_junk_drug_name(drug.canonical_name_en) or is_junk_drug_name(
        drug.canonical_name_ru
    ):
        return DosageOutcome(
            kind="miss",
            text=f"Препарат «{title}» не найден в справочнике.",
        )

    if not formulary_search.has_usable_dose_data(drug):
        return DosageOutcome(
            kind="miss",
            text=formulary_search.format_empty_drug_message(drug, display_title=title),
            drug_id=drug_id,
            counted=False,
        )

    if apply_delay and config.FORMULARY_DOSAGE_DELAY_SEC > 0:
        time.sleep(config.FORMULARY_DOSAGE_DELAY_SEC)

    context = formulary_search.format_drug_context(drug)
    try:
        if not llm_client.is_configured():
            text = _fallback_brief(drug, display_title=title)
        else:
            text = llm_client.dosage_brief(context, user_query, display_title=title)
            if not (text or "").strip():
                text = _fallback_brief(drug, display_title=title)
    except llm_client.LLMError as exc:
        logger.warning("dosage_brief failed: %s", exc)
        text = _fallback_brief(drug, display_title=title)

    text = formulary_search.apply_display_title(text, title)
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
                "Полный ИИ-ответ недоступен (в .env не задан ключ выбранного провайдера).\n\n"
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


def ask_ai(
    user: User,
    question: str,
    *,
    drug_id: int | None = None,
    selected_drug: str = "",
) -> DosageOutcome:
    """Grounded fallback: RAG over the formulary, no invented mg/kg."""
    access = dosage_access.check_dosage_access(user)
    if not access.allowed:
        return DosageOutcome(
            kind="error",
            text="Модуль дозировок временно недоступен. Попробуйте позже.",
        )
    chunks = formulary_rag.retrieve(question, drug_id=drug_id)
    relevant = [c for c in chunks if c.score >= 0.25]
    chunks_text = formulary_rag.format_chunks_for_prompt(relevant)
    try:
        if not llm_client.is_configured():
            if relevant:
                text = (
                    "ИИ-перевод недоступен (нет ключа провайдера). "
                    "Ниже — фрагменты справочника без доработки:\n\n"
                    + "\n\n".join(c.text for c in relevant[:4])
                )
            else:
                text = (
                    "В локальных справочниках (BSAVA / Carpenter / ручной список) "
                    "данных по запросу нет. Дозу указать не могу.\n\n"
                    "Дисклеймер: ответ не заменяет formulary и клиническое решение врача."
                )
        else:
            drug_hint = (selected_drug or "").strip()
            if not drug_hint and drug_id:
                drug = formulary_search.get_drug(int(drug_id))
                if drug:
                    drug_hint = formulary_search.resolve_display_title(drug=drug)
            text = llm_client.ask_ai_fallback(
                question,
                chunks_text,
                selected_drug=drug_hint,
            )
    except llm_client.LLMError as exc:
        logger.warning("ask_ai_fallback failed: %s", exc)
        return DosageOutcome(
            kind="error",
            text="Не удалось получить ответ ИИ. Попробуйте позже.",
        )
    dosage_access.record_usage(user.vk_id, kind="ask_ai", drug_id=drug_id)
    return DosageOutcome(kind="ask_ai", text=text, counted=True)


def _fallback_brief(drug: formulary_search.DrugRecord, *, display_title: str = "") -> str:
    """Always Russian UI labels; no English field dump."""
    body = formulary_search.format_brief_ru(drug, display_title=display_title or None)
    note = ""
    if not llm_client.is_configured():
        note = (
            "\n\n⚠️ Перевод полного описания через ИИ недоступен "
            "(нет ключа выбранного провайдера). Ниже — структурированная сводка "
            "по справочнику на русском; фрагменты доз из EN-источников "
            "могут остаться на языке оригинала."
        )
    return body + note
