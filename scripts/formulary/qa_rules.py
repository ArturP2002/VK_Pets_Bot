"""Automatic QA flags for formulary drug cards (export / review)."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from scripts.formulary.common import fold_match_key, normalize_name
from scripts.formulary.junk_names import is_junk_drug_name

# High-confidence merge contamination: wrong monograph body attached in BSAVA PDF parse.
_CRITICAL_CONTAMINATION_RULES: tuple[dict[str, Any], ...] = (
    {
        "flag": "antiparasitic_hormone_mix",
        "name_re": re.compile(
            r"(?i)\b("
            r"selamectin|ivermectin|moxidectin|doramectin|eprinomectin|"
            r"abamectin|emamectin|milbemycin|milbemycin\s*oxime|"
            r"селамектин|ivermektin|моксидектин"
            r")\b"
        ),
        "body_re": re.compile(
            r"(?i)("
            r"\bh\s*cg\b|\bpmsg\b|\bfsh\b|gonadotropin|gonadotrophin|"
            r"follicle.stimulating|ovulation|oestrus|estrus|"
            r"iu/animal|iu\s*freeze|freeze.dried\s*plug|"
            r"хорионическ|гонадотропин|овуляц|эструс"
            r")"
        ),
    },
    {
        "flag": "inodilator_antibiotic_mix",
        "name_re": re.compile(r"(?i)\b(pimobendan|vetmedin|pimocard|cardisure|пимобендан)\b"),
        "body_re": re.compile(
            r"(?i)("
            r"piperacillin|tazobactam|beta.lactam|ureidopenicillin|"
            r"penicillin.binding|tazocin"
            r")"
        ),
    },
    {
        "flag": "topical_spot_on_iu_formulation",
        "name_re": re.compile(
            r"(?i)\b(selamectin|ivermectin|moxidectin|stronghold|revolution|"
            r"селамектин|stronghold\s*plus)\b"
        ),
        "body_re": re.compile(
            r"(?i)(spot\.on|topical|\b6\s*%\s*selamec).{0,120}(iu|freeze\.dried)|"
            r"injectable:\s*\d+\s*iu"
        ),
    },
)

# Softer suspected contamination (yellow): review recommended.
_WARNING_CONTAMINATION_RULES: tuple[dict[str, Any], ...] = (
    {
        "flag": "suspected_hormone_contamination",
        "name_re": re.compile(
            r"(?i)\b(chorionic|gonadotropin|pmsg|fsh|cabergoline|prolactin|"
            r"oxytocin|prostaglandin|хорион|гонадотропин)\b"
        ),
        "body_re": re.compile(
            r"(?i)\b(hcg|pmsg|fsh|gonadotropin|iu/animal|iu\s*freeze|ovulation|oestrus)\b"
        ),
        "invert_name": True,
    },
    {
        "flag": "suspected_antibiotic_contamination",
        "name_re": re.compile(
            r"(?i)\b(penicillin|amoxicillin|cephalosporin|fluoroquinolone|"
            r"enrofloxacin|marbofloxacin|piperacillin|metronidazole|"
            r"амоксициллин|цеф|энрофлоксацин|марбофлоксацин|пенициллин)\b"
        ),
        "body_re": re.compile(
            r"(?i)(beta.lactam|penicillin.binding|ureidopenicillin|"
            r"aminoglycoside|fluoroquinolone|bactericidal|"
            r"cell wall synthesis)"
        ),
        "invert_name": True,
    },
    {
        "flag": "suspected_cardiac_contamination",
        "name_re": re.compile(
            r"(?i)\b(pimobendan|vetmedin|benazepril|enalapril|furosemide|digoxin|пимобендан)\b"
        ),
        "body_re": re.compile(
            r"(?i)(piperacillin|tazobactam|cephalosporin|fluoroquinolone|"
            r"endocarditis.*septicaemia|antipseudomonal)"
        ),
    },
)

_HORMONE_NAME_RE = re.compile(
    r"(?i)\b(chorionic|gonadotropin|gonadotrophin|pmsg|fsh|hcg|oxytocin|"
    r"prostaglandin|cabergoline|prolactin|хорион|гонадотропин)\b"
)
_HORMONE_BODY_RE = re.compile(
    r"(?i)\b(hcg|pmsg|fsh|gonadotropin|iu/animal|iu\s*freeze|ovulation|oestrus|"
    r"follicle.stimulating)\b"
)
_ANTIBIOTIC_BODY_RE = re.compile(
    r"(?i)(beta.lactam|penicillin.binding|ureidopenicillin|aminoglycoside|"
    r"bactericidal.*cell wall|antipseudomonal penicillin)"
)
_ANTIBIOTIC_NAME_RE = re.compile(
    r"(?i)\b(penicillin|amoxicillin|ampicillin|cephalosporin|cef|enrofloxacin|"
    r"marbofloxacin|piperacillin|metronidazole|azithromycin|doxycycline|"
    r"fluoroquinolone|tazobactam|амоксициллин|пенициллин|цеф|энрофлоксацин)\b"
)
_IU_DOSE_RE = re.compile(r"(?i)\b\d+\s*(?:–|-|to)\s*\d+\s*iu\b|\biu/animal\b|\b\d+\s*iu\b")
_PDF_ARTIFACT_RE = re.compile(r"(?i)\.indd\s+\d+|bsava small animal formulary\s+\d+")
_REFERENCE_BLOB_RE = re.compile(
    r"(?i)references\s+a\s+[a-z]|journal of zoo and wildlife medicine"
)
_GARBAGE_DOSE_RE = re.compile(r"^(h|m|k|d|e|f|g|inc|inj|sex|pd|pk|po|iv|im|sc)$", re.I)


@dataclass
class QAResult:
    flags: list[str] = field(default_factory=list)
    critical: bool = False
    warning: bool = False

    @property
    def flags_text(self) -> str:
        return "; ".join(self.flags)


def _has_cyrillic(value: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", value or ""))


def _card_text_blob(drug: dict[str, Any], doses: list[dict[str, Any]] | None = None) -> str:
    parts = [
        drug.get("canonical_name_en") or "",
        drug.get("canonical_name_ru") or "",
        drug.get("formulations") or "",
        drug.get("action") or "",
        drug.get("use") or drug.get("use_text") or "",
        drug.get("contraindications") or "",
    ]
    for dose in doses or drug.get("doses") or []:
        parts.append(dose.get("raw_text") or "")
    return "\n".join(parts)


def _apply_contamination_rules(
    result: QAResult,
    *,
    name_en: str,
    name_ru: str,
    blob: str,
    rules: tuple[dict[str, Any], ...],
    critical: bool,
) -> None:
    names = f"{name_en} {name_ru}"
    for rule in rules:
        name_match = rule["name_re"].search(names)
        body_match = rule["body_re"].search(blob)
        invert = rule.get("invert_name", False)
        if invert:
            if body_match and not name_match:
                result.flags.append(rule["flag"])
                if critical:
                    result.critical = True
                else:
                    result.warning = True
        elif name_match and body_match:
            result.flags.append(rule["flag"])
            if critical:
                result.critical = True
            else:
                result.warning = True


def duplicate_en_keys(drugs: list[dict[str, Any]]) -> set[str]:
    counts: dict[str, int] = {}
    for drug in drugs:
        key = normalize_name(drug.get("canonical_name_en") or "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count > 1}


def fold_key_duplicate_ids(drugs: list[dict[str, Any]]) -> set[int]:
    """Different cards that likely describe the same INN (Teofilin vs Theophylline)."""
    by_fold: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for drug in drugs:
        for candidate in (drug.get("canonical_name_en"), drug.get("canonical_name_ru")):
            fk = fold_match_key(candidate or "")
            if fk:
                by_fold[fk].append(drug)
                break
    dup_ids: set[int] = set()
    for group in by_fold.values():
        if len(group) < 2:
            continue
        en_norms = {normalize_name(d.get("canonical_name_en") or "") for d in group}
        if len(en_norms) > 1:
            for drug in group:
                dup_ids.add(int(drug["id"]))
    return dup_ids


def near_duplicate_pairs(
    drugs: list[dict[str, Any]],
    *,
    threshold: float = 0.92,
) -> set[int]:
    """Fuzzy near-duplicate canonical names (likely split cards)."""
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return set()

    entries: list[tuple[int, str]] = []
    for drug in drugs:
        drug_id = int(drug["id"])
        seen: set[str] = set()
        for name in (drug.get("canonical_name_en"), drug.get("canonical_name_ru")):
            norm = normalize_name(name or "")
            if len(norm) < 5 or norm in seen:
                continue
            seen.add(norm)
            entries.append((drug_id, norm))

    buckets: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for drug_id, norm in entries:
        buckets[norm[:4]].append((drug_id, norm))

    dup_ids: set[int] = set()
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        for i, (id_a, name_a) in enumerate(bucket):
            for id_b, name_b in bucket[i + 1 :]:
                if id_a == id_b or name_a == name_b:
                    continue
                if fuzz.ratio(name_a, name_b) / 100.0 >= threshold:
                    dup_ids.add(id_a)
                    dup_ids.add(id_b)
    return dup_ids


def analyze_drug(
    drug: dict[str, Any],
    *,
    doses: list[dict[str, Any]] | None = None,
    duplicate_en_keys: set[str] | None = None,
    fold_dup_ids: set[int] | None = None,
    near_dup_ids: set[int] | None = None,
) -> QAResult:
    """Return QA flags; ``critical`` = broken for sure, ``warning`` = needs review."""
    result = QAResult()
    dose_rows = doses if doses is not None else list(drug.get("doses") or [])
    drug_id = int(drug.get("id") or 0)
    name_en = (drug.get("canonical_name_en") or "").strip()
    name_ru = (drug.get("canonical_name_ru") or "").strip()
    blob = _card_text_blob(drug, dose_rows)
    names = f"{name_en} {name_ru}"

    if is_junk_drug_name(name_en) or is_junk_drug_name(name_ru):
        result.flags.append("junk_name")
        result.critical = True

    en_key = normalize_name(name_en)
    if duplicate_en_keys and en_key and en_key in duplicate_en_keys:
        result.flags.append("duplicate_canonical_en")
        result.critical = True

    _apply_contamination_rules(
        result,
        name_en=name_en,
        name_ru=name_ru,
        blob=blob,
        rules=_CRITICAL_CONTAMINATION_RULES,
        critical=True,
    )
    _apply_contamination_rules(
        result,
        name_en=name_en,
        name_ru=name_ru,
        blob=blob,
        rules=_WARNING_CONTAMINATION_RULES,
        critical=False,
    )

    if _HORMONE_BODY_RE.search(blob) and not _HORMONE_NAME_RE.search(names):
        if "suspected_hormone_contamination" not in result.flags:
            result.flags.append("hormone_content_without_hormone_name")
            result.warning = True

    if _ANTIBIOTIC_BODY_RE.search(blob) and not _ANTIBIOTIC_NAME_RE.search(names):
        if not any("antibiotic" in f for f in result.flags):
            result.flags.append("antibiotic_monograph_non_antibiotic_name")
            result.warning = True

    if _IU_DOSE_RE.search(blob) and not _HORMONE_NAME_RE.search(names):
        result.flags.append("iu_doses_non_hormone_drug")
        result.warning = True

    if fold_dup_ids and drug_id in fold_dup_ids:
        result.flags.append("fold_key_duplicate")
        result.warning = True

    if near_dup_ids and drug_id in near_dup_ids:
        result.flags.append("near_duplicate_name")
        result.warning = True

    if _has_cyrillic(name_en) and (not name_ru or not _has_cyrillic(name_ru)):
        result.flags.append("cyrillic_en_canonical")
        result.warning = True

    if not name_ru or not _has_cyrillic(name_ru):
        result.flags.append("no_ru_name")

    if not dose_rows:
        result.flags.append("no_doses")
        result.warning = True

    monograph_len = sum(
        len((drug.get(field) or "").strip())
        for field in (
            "formulations",
            "action",
            "use",
            "use_text",
            "contraindications",
            "adverse_reactions",
        )
    )
    if not dose_rows and monograph_len < 40:
        result.flags.append("empty_card")
        result.warning = True
        if is_junk_drug_name(name_en) or is_junk_drug_name(name_ru):
            result.critical = True

    if _PDF_ARTIFACT_RE.search(blob):
        result.flags.append("pdf_page_artifact")
        result.warning = True

    ref_doses = sum(1 for d in dose_rows if _REFERENCE_BLOB_RE.search(d.get("raw_text") or ""))
    if ref_doses:
        result.flags.append(f"reference_blob_in_doses:{ref_doses}")
        result.warning = True

    garbage_doses = sum(
        1
        for d in dose_rows
        if _GARBAGE_DOSE_RE.match((d.get("raw_text") or "").strip())
        or len((d.get("raw_text") or "").strip()) <= 2
    )
    if garbage_doses:
        result.flags.append(f"garbage_dose_lines:{garbage_doses}")
        result.warning = True

    sources = {str(s).lower() for s in (drug.get("sources") or [])}
    if sources == {"bsava"} and monograph_len > 120 and dose_rows:
        bsava_only = sum(1 for d in dose_rows if (d.get("source") or "").lower() == "bsava")
        if bsava_only == len(dose_rows):
            result.flags.append("bsava_only_unverified")
            result.warning = True

    if result.critical:
        result.warning = False
    return result


def compute_stable_key(drug: dict[str, Any]) -> str:
    """Stable merge key for Excel round-trip (matches build_db merge key)."""
    preset = (drug.get("stable_key") or drug.get("_stable_key") or "").strip()
    if preset:
        return preset
    from scripts.formulary.build_db import inn_match_key

    for candidate in (drug.get("canonical_name_en"), drug.get("canonical_name_ru")):
        key = inn_match_key(candidate or "")
        if key:
            return key
    return ""


def analyze_all(drugs: list[dict[str, Any]]) -> dict[int, QAResult]:
    dup_keys = duplicate_en_keys(drugs)
    fold_dups = fold_key_duplicate_ids(drugs)
    near_dups = near_duplicate_pairs(drugs)
    return {
        int(d["id"]): analyze_drug(
            d,
            duplicate_en_keys=dup_keys,
            fold_dup_ids=fold_dups,
            near_dup_ids=near_dups,
        )
        for d in drugs
    }
