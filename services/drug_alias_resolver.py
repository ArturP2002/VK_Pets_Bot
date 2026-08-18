"""Map veterinary brand names to INN for formulary search."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from scripts.formulary.common import normalize_name, transliterate_ru

logger = logging.getLogger(__name__)

# Seed of RU/EN trade names seen in clinic use (query → INN search terms).
BRAND_TO_INN: dict[str, list[str]] = {
    "доксифин": ["doxycycline", "доксициклин"],
    "doxyfin": ["doxycycline", "доксициклин"],
    "энроксил": ["enrofloxacin", "энрофлоксацин"],
    "enroxil": ["enrofloxacin", "энрофлоксацин"],
    "марфлоксин": ["marbofloxacin", "марбофлоксацин"],
    "marfloxin": ["marbofloxacin", "марбофлоксацин"],
    "мелоксивет": ["meloxicam", "мелоксикам"],
    "meloxivet": ["meloxicam", "мелоксикам"],
    "эпокрин": ["epoetin", "эпоэтин", "erythropoietin"],
    "epokrin": ["epoetin", "эпоэтин"],
    "бисептол": ["trimethoprim/sulfa", "co-trimoxazole", "бисептол"],
    "biseptol": ["trimethoprim/sulfa", "co-trimoxazole", "бисептол"],
    "бактрим": ["trimethoprim/sulfa", "co-trimoxazole", "bactrim"],
    "bactrim": ["trimethoprim/sulfa", "co-trimoxazole"],
    "ганатон": ["mosapride", "мозаприд", "ганатон"],
    "ganaton": ["mosapride", "мозаприд"],
}


@dataclass
class BrandResolution:
    inn_terms: list[str]
    source: str  # dict | llm
    confidence: float


def _lookup_dict(query: str) -> BrandResolution | None:
    q = (query or "").strip()
    if not q:
        return None
    keys = {
        q.lower(),
        normalize_name(q),
        transliterate_ru(q),
    }
    for key in keys:
        if key in BRAND_TO_INN:
            return BrandResolution(inn_terms=list(BRAND_TO_INN[key]), source="dict", confidence=1.0)
    return None


def resolve_brand(query: str, *, use_llm: bool = True) -> BrandResolution | None:
    """Resolve a trade name to INN search terms. Never invents doses."""
    found = _lookup_dict(query)
    if found:
        return found
    if not use_llm:
        return None
    try:
        from services import llm_client
    except ImportError:
        return None
    if not llm_client.is_configured():
        return None
    try:
        data = llm_client.resolve_brand_inn(query)
    except Exception as exc:
        logger.warning("brand INN resolve failed: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    if data.get("unknown") or (data.get("confidence") or 0) < 0.6:
        return None
    terms: list[str] = []
    for key in ("inn_en", "inn_ru"):
        val = (data.get(key) or "").strip()
        if val:
            terms.append(val)
    for extra in data.get("aliases") or []:
        cleaned = str(extra).strip()
        if cleaned:
            terms.append(cleaned)
    if not terms:
        return None
    return BrandResolution(
        inn_terms=terms,
        source="llm",
        confidence=float(data.get("confidence") or 0.6),
    )
