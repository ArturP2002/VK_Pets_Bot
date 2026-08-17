"""Runtime filter: names that are not drugs (lab rows, months, diagnoses)."""
from __future__ import annotations

import re

from scripts.formulary.common import normalize_name
from scripts.formulary.extract_carpenter import is_junk_agent_name

_RU_MONTHS = frozenset(
    {
        "январь",
        "февраль",
        "март",
        "апрель",
        "май",
        "июнь",
        "июль",
        "август",
        "сентябрь",
        "октябрь",
        "ноябрь",
        "декабрь",
    }
)

# Lab / reference-value rows (EN + RU) that sometimes land in the formulary as "agents".
_LAB_REFERENCE_RE = re.compile(
    r"(?i)("
    r"phosphatase|hemoglobin|hematocrit|hematolog|biochemical|"
    r"electrophoresis|urinalysis|reference\s+values?|normal\s+values?|"
    r"blood\s+gas|lipoprotein|bone\s+marrow|"
    r"фосфатаз|гемоглобин|гематокрит|лейкоцит|эритроцит|"
    r"биохимич|гематолог|референс|нормальн\s*знач|"
    r"электрофорез|анализ\s+кров|клиническ\s+анализ"
    r")"
)

_DIAGNOSIS_RU_RE = re.compile(
    r"(?i)("
    r"блокада|аритми|степен[ьи]\s+ав|"
    r"degree\s+av\s+block|bundle\s+branch"
    r")"
)


def is_junk_drug_name(name: str) -> bool:
    """True if the label is not a searchable drug name."""
    cleaned = (name or "").strip()
    if not cleaned:
        return True
    if is_junk_agent_name(cleaned):
        return True
    norm = normalize_name(cleaned)
    if norm in _RU_MONTHS:
        return True
    if _LAB_REFERENCE_RE.search(cleaned):
        return True
    if _DIAGNOSIS_RU_RE.search(cleaned):
        return True
    return False
