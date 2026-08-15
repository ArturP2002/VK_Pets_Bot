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
    "serum biochemical",
    "physiologic",
    "blood collection",
    "differential diagnos",
    "disinfectant",
    "scientific names",
    "common captive",
    "guidelines for treatment",
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


def _clean_agent_name(name: str) -> str:
    name = re.sub(r"\s*\(cont'?d\.?\)?\s*$", "", name, flags=re.I)
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
    if not re.search(r"[A-Za-z]", name):
        return ""
    # Require at least one letter sequence of length >= 3 (drug-like token)
    if not re.search(r"[A-Za-z]{3,}", name):
        return ""
    if re.match(r"^\d", name) and re.search(r"mg|ml|µg|ug|day|wk", name, re.I):
        return ""
    if re.match(r"^\d", name) and not re.match(r"^\d+[-/]?[A-Za-z]", name):
        return ""
    if re.search(r"\btreatments?\d*\b", lowered):
        return ""
    return name


def _split_agent_dose_comment(line: str) -> tuple[str, str, str] | None:
    if re.match(r"^Agent\s+Dosage", line, re.I):
        return None
    if re.match(r"^TABLE\s+\d", line, re.I):
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
            continue
        if re.match(r"^REFERENCES\b", stripped, re.I):
            current_agent = ""
            in_table = False
            continue
        if re.match(r"^TABLE\s+\d", stripped, re.I):
            low = stripped.lower()
            if any(x in low for x in _NON_DRUG_TABLE):
                in_table = False
                current_agent = ""
                continue
            in_table = True
            continue
        if not in_table and re.match(r"^Agent\s+Dosage", stripped, re.I):
            in_table = True
        if not in_table:
            continue
        if any(x in stripped for x in ("Hematologic", "Serum Biochemical")):
            in_table = False
            continue

        parsed = _split_agent_dose_comment(stripped)
        if not parsed:
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

        drug = ensure(current_agent)
        species_note = ""
        if comment:
            sm = re.match(r"^([^/]{2,40})/\s*(.*)$", comment)
            if sm:
                species_note = sm.group(1).strip()
                comment = sm.group(2).strip()
        raw_text = dosage if not comment else f"{dosage}; {comment}"
        drug["doses"].append(
            dose_dict(
                taxa=detect_taxa(species_note) if species_note else taxa,
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
