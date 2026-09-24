"""Extract Carpenter Exotic Animal Formulary dose tables → JSONL."""
from __future__ import annotations

import argparse
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from scripts.formulary.common import (
    detect_taxa,
    dose_dict,
    empty_drug,
    knowledge_base_dir,
    merge_unique,
    normalize_name,
    raw_dir,
    write_jsonl,
)

logger = logging.getLogger(__name__)

DEFAULT_PDF = (
    knowledge_base_dir() / "Carpenter_39_s_Exotic_Animal_Formulary_6th_Edition.pdf"
)

CHAPTER_TAXA = {
    "invertebrates": "invertebrates",
    "fish": "fish",
    "amphibians": "amphibians",
    "reptiles": "reptiles",
    "birds": "birds",
    "mammals": "mammals",
}

DOSE_START_RE = re.compile(
    r"^(?P<dose>\d[\d.,/\-\s]*(?:mg|µg|ug|mcg|g|ml|IU|U|%|ppm|ppt|g/L|mg/L|"
    r"mg/kg|µg/kg|ug/kg|mcg/kg|ml/kg|IU/kg|U/kg|g/kg).*)$",
    re.I,
)

_NON_DRUG_TABLE = (
    "hematologic",
    "hematology",
    "serum biochemical",
    "biochemical values",
    "physiologic",
    "blood collection",
    "differential diagnos",
    "disinfectant",
    "scientific names",
    "common captive",
    "common names",
    "guidelines for treatment",
    "blood gas",
    "urinalysis",
    "electrocardiog",
    "arrhythmia",
    "electrophoresis",
    "lipoprotein",
    "plasma protein",
    "venipuncture",
    "injection sites",
    "reference values",
    "arterial and venous",
    "environmental, dietary",
    "t4 values",
    "bone marrow",
)

# OCR sometimes splits TABLE → "TA BL E 5-31"
_TABLE_HEAD_RE = re.compile(r"^TA\s*BL\s*E\s+\d", re.I)

_JUNK_TABLE_LINE = (
    "Hematologic",
    "Serum Biochemical",
    "Electrocardiog",
    "Protein Electrophoresis",
    "Blood Gas",
    "Urinalysis Values",
)

# Diagnoses, lab values, and physiology crumbs that are not drug names.
_DIAGNOSIS_LAB_RE = re.compile(
    r"(?i)\b("
    r"block[a-d]?|ratio[a-d]?|bacteria|aerobic|anaerobic|"
    r"hematologic|biochemical|"
    r"\d+(?:st|nd|rd|th)[-\s]?degree|"
    r"protein\s*\(?\s*%|"
    r"белок\s*\(?\s*%"
    r")\b"
)

_ROUTE_ONLY_RE = re.compile(
    r"(?i)^("
    r"(?:i\.?\s*/?\s*[vmcpso]|s\.?\s*c\.?|p\.?\s*o\.?|i\.?\s*ce|"
    r"iv|im|sc|sq|po|ice|ip|io|"
    r"в\s*/\s*[вм]|п\s*/\s*к|"
    r"интрацеломическ\w*|интраперитонеальн\w*|"
    r"внутривенн\w*|внутримышечн\w*|подкожн\w*|пероральн\w*"
    r")"
    r"(?:\s*[,;/]\s*"
    r"(?:i\.?\s*/?\s*[vmcpso]|s\.?\s*c\.?|p\.?\s*o\.?|i\.?\s*ce|"
    r"iv|im|sc|sq|po|ice|ip|io|"
    r"в\s*/\s*[вм]|п\s*/\s*к|"
    r"интрацеломическ\w*|интраперитонеальн\w*"
    r")*)*"
    r")$"
)

_BARE_NON_DRUG = frozenset(
    {
        "oil",
        "oils",
        "масло",
        "масла",
        "skin",
        "кожа",
        "protein",
        "белок",
        "finch",
        "финч",
        "fire ants",
        "fire ant",
        "огненные муравьи",
        "муравьи",
        "ants",
        "feather",
        "перо",
        "acid",
        "acids",
        "кислота",
        "кислоты",
        "essential",
        "male",
        "female",
        "самец",
        "самка",
        "самцы",
        "самки",
        "ici",
    }
)

_SPECIES_COMMON_RE = re.compile(
    r"(?i)\b("
    r"finch(?:es)?|canary|canaries|sparrow|pigeon|dove|macaw|"
    r"cockatoo|conure|budgerigar|shrimp|crab|lobster|ant(?:s)?|"
    r"fire\s+ants?|parrot(?:s)?|finches|"
    r"финч|канарейк|воробей|голуб|креветк|краб|мурав"
    r")\b"
)

# Truncated protocol abbreviations: "alfaxalone (A", "midazolam (Mi", "acepromazine (A"
_INCOMPLETE_ABBREV_PAREN_RE = re.compile(r"\([A-Za-z]{1,2}(?:/[A-Za-z]{1,2})?$")

# Species/common-name rows from hematology / taxa tables (agent column only).
_SPECIES_ONLY_NAMES = {
    "african green",
    "african grey",
    "african gray",
    "african clawed frog",
    "guinea pig",
    "guinea pigs",
    "cavia",
    "cavia porcellus",
    "морская свинка",
    "свинка морская",
    "rabbit",
    "rabbits",
    "кролик",
    "hamster",
    "hamsters",
    "хомяк",
    "rat",
    "rats",
    "крыса",
    "mouse",
    "mice",
    "мышь",
    "ferret",
    "хорек",
    "хорёк",
    "chinchilla",
    "шиншилла",
    "parrot",
    "попугай",
    "dog",
    "dogs",
    "cat",
    "cats",
    "собака",
    "кошка",
    "кот",
}
_SPECIES_ONLY_RE = re.compile(
    r"(?i)(^african\s+(?:green|grey|gray)\b|\bparrots?\b|\bspp\.?\b)"
)
# Keep names that look like chemicals even if a species token is present
# (e.g. "malachite green" must not be banned just because of "green").
# Bare "oil" is junk; "mineral oil" / "cod liver oil" stay via these tokens.
_DRUG_LIKE_RE = re.compile(
    r"(?i)("
    r"cillin|mycin|cycline|floxacin|nazole|caine|olol|pril|sartan|"
    r"statin|\bmab\b|\bnib\b|vir\b|faxalone|promazine|cyclovir|"
    r"phenoxy|ethanol|oxicam|chloride|sulfate|oxide|peroxide|"
    r"\bacid\b|mineral\s+oil|cod\s+liver|olive\s+oil|castor\s+oil|"
    r"vaccine|extract|hormone|steroid|"
    r"antibiotic|antifungal|antiviral"
    r")"
)


def _pdf_to_text(pdf_path: Path) -> str:
    cached = raw_dir() / "carpenter_raw.txt"
    if cached.exists() and cached.stat().st_mtime >= pdf_path.stat().st_mtime:
        return cached.read_text(encoding="utf-8", errors="replace")
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
        text = result.stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        import fitz

        doc = fitz.open(pdf_path)
        text = "\n".join(page.get_text("text") for page in doc)
    cached.write_text(text, encoding="utf-8")
    return text


def _chapter_taxa(line: str) -> str | None:
    m = re.match(r"^CHAPTER\s+\d+\s+(.+)$", line.strip(), re.I)
    if not m:
        return None
    title = m.group(1).lower()
    for key, taxa in CHAPTER_TAXA.items():
        if key in title:
            return taxa
    if any(
        x in title
        for x in (
            "ferret",
            "rabbit",
            "rodent",
            "hedgehog",
            "sugar glider",
            "miniature pig",
            "primate",
            "carnivore",
            "mammal",
        )
    ):
        return "mammals"
    return "other"


def _is_protocol_fragment(name: str) -> bool:
    """True for 'A) + midazolam' / 'K) + fentanyl' crumbs (closing paren before any open)."""
    close = name.find(")")
    if close == -1:
        return False
    open_ = name.find("(")
    return open_ == -1 or close < open_


def _letter_count(name: str) -> int:
    return sum(1 for ch in name if ch.isalpha())


def _is_species_only_name(name: str) -> bool:
    """Reject animal common/scientific names used as the agent, not as species_note."""
    if _DRUG_LIKE_RE.search(name):
        return False
    lowered = name.lower().strip()
    if lowered in _SPECIES_ONLY_NAMES or lowered in _BARE_NON_DRUG:
        return True
    if _SPECIES_COMMON_RE.search(lowered):
        return True
    return bool(_SPECIES_ONLY_RE.search(lowered))


def is_junk_agent_name(name: str) -> bool:
    """True if the agent column is not a drug (diagnosis, lab, species, truncated protocol)."""
    if not name:
        return True
    lowered = name.lower().strip()
    collapsed = re.sub(r"\s+", "", lowered)
    if _letter_count(name) < 4:
        return True
    if collapsed in {"contents", "table"} or re.match(r"^table\d", collapsed):
        return True
    if lowered in {
        "agent",
        "agent(s",
        "agents",
        "dosage",
        "comments",
        "species",
        "measurement",
        "measurements",
        "injectable agents",
        "inhaled agents",
        "acceptable methods",
        "normal values",
    }:
        return True
    if lowered in _BARE_NON_DRUG:
        return True
    if re.search(r"(?i)protein\s*\(?\s*%|белок\s*\(?\s*%", name):
        return True
    if _ROUTE_ONLY_RE.match(lowered):
        return True
    if _DIAGNOSIS_LAB_RE.search(name):
        return True
    if _INCOMPLETE_ABBREV_PAREN_RE.search(name):
        return True
    if _is_protocol_fragment(name):
        return True
    if _is_species_only_name(name):
        return True
    return False


def _clean_agent_name(name: str) -> str:
    name = re.sub(r"\s*\(cont[’']?d\.?\)?\s*$", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" -–—)(,")
    if len(name) < 3 or len(name) > 60:
        return ""
    lowered = name.lower()
    if lowered in {
        "agent",
        "dosage",
        "comments",
        "species",
        "measurement",
        "measurements",
        "cont’d",
        "cont'd",
    }:
        return ""
    if re.fullmatch(r"[\divx]+", lowered):
        return ""
    if re.fullmatch(r"\d+", name):
        return ""
    # Duration / powder crumbs mis-parsed as agents
    if re.match(
        r"^\d+(\.\d+)?\s*(wk|day|days|hr|hrs|h|min|mo|months?|yr|years?)\b",
        lowered,
    ):
        return ""
    if re.match(r"^\d+(\.\d+)?\s*%", lowered):
        return ""
    if re.match(r"^\d+\s*(drops?|drop of)\b", lowered):
        return ""
    if name.startswith(("•", "and ", "see ", "the ", "with ")):
        return ""
    if _letter_count(name) < 3:
        return ""
    if not re.search(r"[A-Za-zА-Яа-яЁё]{3,}", name):
        return ""
    if re.match(r"^\d", name) and re.search(r"mg|ml|µg|ug|day|wk", name, re.I):
        return ""
    if re.match(r"^\d", name) and not re.match(r"^\d+[-/]?[A-Za-z]", name):
        return ""
    if re.search(r"\btreatments?\d*\b", lowered):
        return ""
    if is_junk_agent_name(name):
        return ""
    return name


def _split_agent_dose_comment(line: str) -> tuple[str, str, str] | None:
    if re.match(r"^Agent\s+Dosage", line, re.I):
        return None
    if _TABLE_HEAD_RE.match(line):
        return None

    parts = re.split(r"\s{2,}", line.strip())
    if len(parts) >= 3:
        agent = _clean_agent_name(parts[0])
        dosage = parts[1].strip()
        comment = " ".join(parts[2:]).strip()
        if agent and dosage:
            return agent, dosage, comment
    if len(parts) == 2:
        left, right = parts
        if DOSE_START_RE.match(left) or left.startswith(("—", "-")):
            return "", left.strip(), right.strip()
        agent = _clean_agent_name(left)
        if agent:
            dm = re.match(
                r"^(?P<dose>.+?)(?:\s{2,}|\s+)(?P<comment>[A-Z].+)?$",
                right,
            )
            if dm and re.search(r"\d", dm.group("dose") or ""):
                return agent, dm.group("dose").strip(), (dm.group("comment") or "").strip()
            return agent, right.strip(), ""
    return None


def parse_tables(text: str) -> list[dict[str, Any]]:
    """Return stub drug records with carpenter doses."""
    taxa = "other"
    current_agent = ""
    by_name: dict[str, dict[str, Any]] = {}
    in_table = False
    pending_wrap = ""

    def ensure(agent: str) -> dict[str, Any]:
        key = normalize_name(agent)
        if key not in by_name:
            drug = empty_drug(canonical_name_en=agent, source="carpenter")
            drug["aliases"] = [agent]
            by_name[key] = drug
        return by_name[key]

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("Carpenter") or stripped.startswith("Contents"):
            continue
        ch = _chapter_taxa(stripped)
        if ch:
            taxa = ch
            current_agent = ""
            in_table = False
            pending_wrap = ""
            continue
        if re.match(r"^REFERENCES\b", stripped, re.I):
            current_agent = ""
            in_table = False
            pending_wrap = ""
            continue
        if _TABLE_HEAD_RE.match(stripped):
            low = stripped.lower()
            if any(x in low for x in _NON_DRUG_TABLE):
                in_table = False
                current_agent = ""
                pending_wrap = ""
                continue
            in_table = True
            continue
        if not in_table and re.match(r"^Agent\s+Dosage", stripped, re.I):
            in_table = True
        if not in_table:
            continue
        if any(x in stripped for x in _JUNK_TABLE_LINE):
            in_table = False
            pending_wrap = ""
            continue

        parsed = _split_agent_dose_comment(stripped)
        if not parsed and pending_wrap:
            parsed = _split_agent_dose_comment(f"{pending_wrap} {stripped}")
            if parsed:
                pending_wrap = ""
        if not parsed:
            looks_trunc = (
                len(stripped) <= 24
                and not re.search(r"\d", stripped)
                and bool(re.match(r"^[A-ZА-ЯЁ]", stripped))
                and not re.match(r"^TABLE\b", stripped, re.I)
            )
            allow_wrap = looks_trunc and (
                _letter_count(stripped) < 3 or not is_junk_agent_name(stripped)
            )
            if in_table and allow_wrap:
                pending_wrap = f"{pending_wrap} {stripped}".strip() if pending_wrap else stripped
                continue
            pending_wrap = ""
            if current_agent and (
                DOSE_START_RE.match(stripped) or re.search(r"\d\s*mg", stripped, re.I)
            ):
                drug = ensure(current_agent)
                drug["doses"].append(
                    dose_dict(
                        taxa=taxa,
                        raw_text=stripped,
                        source="carpenter",
                        species_note="",
                    )
                )
            continue

        agent, dosage, comment = parsed
        if agent:
            current_agent = agent
        if not current_agent:
            continue
        if not dosage or dosage in {"—", "-", "–"}:
            continue

        pending_wrap = ""
        drug = ensure(current_agent)
        species_note = ""
        if comment:
            sm = re.match(r"^([^/]{2,40})/\s*(.*)$", comment)
            if sm:
                species_note = sm.group(1).strip()
                comment = sm.group(2).strip()
        raw_text = dosage if not comment else f"{dosage}; {comment}"
        detected = detect_taxa(species_note) if species_note else "other"
        row_taxa = detected if detected != "other" else taxa
        drug["doses"].append(
            dose_dict(
                taxa=row_taxa,
                raw_text=raw_text,
                source="carpenter",
                species_note=species_note,
                indication=comment if not species_note else comment,
            )
        )

    cleaned: list[dict[str, Any]] = []
    for drug in by_name.values():
        if not drug["doses"]:
            continue
        name = drug["canonical_name_en"]
        if not _clean_agent_name(name):
            continue
        if name.endswith("/") or name.startswith("("):
            continue
        drug["full_text_en"] = (
            f"{name}\n" + "\n".join(f"- {x['raw_text']}" for x in drug["doses"][:50])
        )
        drug["aliases"] = merge_unique(drug["aliases"])
        cleaned.append(drug)
    logger.info("Carpenter: %s agents with doses", len(cleaned))
    return cleaned


def extract(pdf_path: Path | None = None) -> list[dict[str, Any]]:
    pdf_path = pdf_path or DEFAULT_PDF
    if not pdf_path.exists():
        raise FileNotFoundError(f"Carpenter PDF not found: {pdf_path}")
    logger.info("Extracting Carpenter from %s", pdf_path)
    text = _pdf_to_text(pdf_path)
    return parse_tables(text)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Extract Carpenter formulary PDF")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--out", type=Path, default=raw_dir() / "carpenter.jsonl")
    args = parser.parse_args(argv)
    rows = extract(args.pdf)
    n = write_jsonl(args.out, rows)
    print(f"Wrote {n} agents → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
