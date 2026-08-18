"""Shared helpers for formulary extractors and DB build."""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

TAXA = ("fish", "mammals", "birds", "reptiles", "amphibians", "invertebrates", "other")

TAXA_ALIASES = {
    "fish": "fish",
    "fishes": "fish",
    "mammal": "mammals",
    "mammals": "mammals",
    "bird": "birds",
    "birds": "birds",
    "avian": "birds",
    "reptile": "reptiles",
    "reptiles": "reptiles",
    "amphibian": "amphibians",
    "amphibians": "amphibians",
    "invertebrate": "invertebrates",
    "invertebrates": "invertebrates",
    "рыб": "fish",
    "рыбы": "fish",
    "млекопитающ": "mammals",
    "птиц": "birds",
    "рептил": "reptiles",
    "амфиб": "amphibians",
    "guinea pig": "mammals",
    "guinea pigs": "mammals",
    "морская свинка": "mammals",
    "свинк": "mammals",
    "rabbit": "mammals",
    "кролик": "mammals",
    "hamster": "mammals",
    "хомяк": "mammals",
    "shrimp": "invertebrates",
    "crab": "invertebrates",
    "креветк": "invertebrates",
    "краб": "invertebrates",
}

SECTION_KEYS = (
    "formulations",
    "action",
    "use",
    "safety_handling",
    "contraindications",
    "adverse_reactions",
    "drug_interactions",
)

# Common Cyrillic → Latin transliteration (GOST-ish, search-oriented).
_RU_TO_LAT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

DOSE_RANGE_RE = re.compile(
    r"(?P<min>\d+(?:[.,]\d+)?)\s*(?:[-–—]|to)\s*(?P<max>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>mg/kg|µg/kg|ug/kg|mcg/kg|g/kg|mg/l|mg/L|µg/l|ug/l|mcg/l|"
    r"mg/ml|IU/kg|iu/kg|U/kg|ml/kg|%|ppm|ppt|"
    r"мг/кг|мкг/кг|г/кг|мг/л|мкг/л|мг/мл|ме/кг|ед/кг|мл/кг)",
    re.I,
)
DOSE_SINGLE_RE = re.compile(
    r"(?P<val>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>mg/kg|µg/kg|ug/kg|mcg/kg|g/kg|mg/l|mg/L|µg/l|ug/l|mcg/l|"
    r"mg/ml|IU/kg|iu/kg|U/kg|ml/kg|%|ppm|ppt|"
    r"мг/кг|мкг/кг|г/кг|мг/л|мкг/л|мг/мл|ме/кг|ед/кг|мл/кг)",
    re.I,
)
ROUTE_RE = re.compile(
    r"\b(i\.?m\.?|i\.?v\.?|s\.?c\.?|p\.?o\.?|oral|orally|topical|immersion|"
    r"bath|intramuscular|intravenous|subcutaneous|intracoelomic|i\.?ce\.?|"
    r"peribulbar|вв|в/в|вм|в/м|пк|п/к|внутрь|перорально)\b",
    re.I,
)
FREQ_RE = re.compile(
    r"\b(q\d+-?\d*(?:h|d|days?)|qid|tid|bid|sid|once|twice|"
    r"каждые?\s+\d+(?:[-–]\d+)?\s*(?:час(?:а|ов)?|ч|сут(?:ок)?|дн(?:я|ей)?))\b",
    re.I,
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def knowledge_base_dir() -> Path:
    return project_root() / "knowledge_base"


def raw_dir() -> Path:
    path = project_root() / "data" / "formulary_raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_name(value: str) -> str:
    """Lowercase, strip punctuation/diacritics, collapse whitespace."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[®™©]", "", text)
    text = re.sub(r"[^\w\s/+-]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def transliterate_ru(value: str) -> str:
    out: list[str] = []
    for ch in value.lower():
        if ch in _RU_TO_LAT:
            out.append(_RU_TO_LAT[ch])
        else:
            out.append(ch)
    return normalize_name("".join(out))


def fold_match_key(value: str) -> str:
    """Collapse RU/EN spelling so метоклопрамид matches Metoclopramide."""
    text = transliterate_ru(value or "")
    text = text.replace("ph", "f").replace("c", "k")
    if text.endswith("e") and len(text) > 4:
        text = text[:-1]
    return text


def detect_taxa(text: str) -> str:
    lowered = (text or "").lower()
    for alias, taxa in TAXA_ALIASES.items():
        if alias in lowered:
            return taxa
    return "other"


def parse_dose_numbers(raw: str) -> tuple[float | None, float | None, str | None]:
    """Extract min/max/unit from free-text dose string."""
    if not raw:
        return None, None, None
    cleaned = raw.replace(",", ".")
    m = DOSE_RANGE_RE.search(cleaned)
    if m:
        return float(m.group("min")), float(m.group("max")), _canon_unit(m.group("unit"))
    m = DOSE_SINGLE_RE.search(cleaned)
    if m:
        val = float(m.group("val"))
        return val, val, _canon_unit(m.group("unit"))
    return None, None, None


def _canon_unit(unit: str) -> str:
    u = unit.lower().replace("µg", "ug").replace("μg", "ug")
    mapping = {
        "ug/kg": "µg/kg",
        "mcg/kg": "µg/kg",
        "ug/l": "µg/l",
        "mcg/l": "µg/l",
        "mg/l": "mg/l",
        "iu/kg": "IU/kg",
        "u/kg": "U/kg",
        "мг/кг": "mg/kg",
        "мкг/кг": "µg/kg",
        "г/кг": "g/kg",
        "мг/л": "mg/l",
        "мкг/л": "µg/l",
        "мг/мл": "mg/ml",
        "ме/кг": "IU/kg",
        "ед/кг": "U/kg",
        "мл/кг": "ml/kg",
    }
    return mapping.get(u, unit if unit in ("%", "ppm", "ppt") else u)


def extract_route(raw: str) -> str | None:
    m = ROUTE_RE.search(raw or "")
    return m.group(0) if m else None


def extract_frequency(raw: str) -> str | None:
    m = FREQ_RE.search(raw or "")
    return m.group(0) if m else None


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def empty_drug(
    *,
    canonical_name_en: str = "",
    canonical_name_ru: str = "",
    source: str,
) -> dict[str, Any]:
    return {
        "canonical_name_en": canonical_name_en.strip(),
        "canonical_name_ru": canonical_name_ru.strip(),
        "trade_names": [],
        "aliases": [],
        "pom_note": "",
        "formulations": "",
        "action": "",
        "use": "",
        "safety_handling": "",
        "contraindications": "",
        "adverse_reactions": "",
        "drug_interactions": "",
        "full_text_en": "",
        "full_text_ru": "",
        "doses": [],
        "sources": [source],
        "source": source,
    }


def merge_unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned:
            continue
        key = normalize_name(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def dose_dict(
    *,
    taxa: str,
    raw_text: str,
    source: str,
    species_note: str = "",
    indication: str = "",
) -> dict[str, Any]:
    dmin, dmax, unit = parse_dose_numbers(raw_text)
    return {
        "taxa": taxa if taxa in TAXA else detect_taxa(taxa or raw_text),
        "species_note": species_note.strip(),
        "indication": indication.strip(),
        "route": extract_route(raw_text) or "",
        "dose_min": dmin,
        "dose_max": dmax,
        "dose_unit": unit or "",
        "frequency": extract_frequency(raw_text) or "",
        "duration": "",
        "raw_text": raw_text.strip(),
        "source": source,
    }
