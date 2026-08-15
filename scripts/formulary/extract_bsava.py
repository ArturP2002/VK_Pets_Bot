"""Extract BSAVA Exotic Pets formulary monographs → JSONL."""
from __future__ import annotations

import argparse
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from scripts.formulary.common import (
    dose_dict,
    empty_drug,
    knowledge_base_dir,
    merge_unique,
    raw_dir,
    write_jsonl,
)

logger = logging.getLogger(__name__)

DEFAULT_PDF = (
    knowledge_base_dir()
    / "novaya_BSAVA_Small_Animal_Formulary_Part_B_Exotic_Pets_11th_Edition.pdf"
)

SECTION_HEADERS = {
    "formulations": "formulations",
    "action": "action",
    "use": "use",
    "safety and handling": "safety_handling",
    "contraindications": "contraindications",
    "adverse reactions": "adverse_reactions",
    "drug interactions": "drug_interactions",
    "doses": "doses",
}

TAXA_LINE_RE = re.compile(
    r"^(Fish|Mammals|Birds|Reptiles|Amphibians|Invertebrates)\s*:\s*(.*)$",
    re.I | re.M,
)
TRADE_POM_RE = re.compile(
    r"^\((?P<trades>[^)]*)\)\s*(?P<pom>POM(?:-V)?(?:\s*/\s*POM)?|P|GSL)?\s*$",
    re.I,
)
# Drug titles are Title Case / chemical names, not all-caps headers.
DRUG_TITLE_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z0-9][A-Za-z0-9\s\-/']{1,60})$"
)


def _pdf_to_text(pdf_path: Path) -> str:
    cached = raw_dir() / "bsava_raw.txt"
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
        import fitz  # pymupdf

        doc = fitz.open(pdf_path)
        text = "\n".join(page.get_text("text") for page in doc)
    cached.write_text(text, encoding="utf-8")
    return text


def clean_bsava_text(text: str) -> str:
    """Drop page chrome and single-letter index column noise."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if re.fullmatch(r"[A-Z]", stripped):
            continue
        if re.match(r"^Booksvets\.blogspot", stripped, re.I):
            continue
        if re.match(r"^BSAVA Small Animal", stripped, re.I):
            continue
        if re.match(r"^Formulary$", stripped, re.I):
            continue
        # Left-margin letter + content: "  G        Formulations: ..."
        m = re.match(r"^\s*[A-Z]\s{2,}(?P<body>\S.*)$", line)
        if m:
            lines.append(m.group("body").strip())
            continue
        # Indent-only body
        m = re.match(r"^\s{6,}(?P<body>.+)$", line)
        if m:
            lines.append(m.group("body").strip())
            continue
        lines.append(stripped)
    # Collapse excessive blank lines
    out: list[str] = []
    blank = 0
    for line in lines:
        if not line:
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        out.append(line)
    return "\n".join(out)


def _is_drug_title(line: str, next_line: str | None) -> bool:
    if not DRUG_TITLE_RE.match(line):
        return False
    lowered = line.lower()
    if lowered in SECTION_HEADERS or lowered in {
        "fish",
        "mammals",
        "birds",
        "reptiles",
        "amphibians",
        "references",
        "appendix",
        "index",
        "contents",
        "preface",
        "introduction",
    }:
        return False
    if len(line.split()) > 6:
        return False
    # Next line usually starts with (trade) / Formulations / Action
    if next_line:
        nl = next_line.strip()
        if (
            nl.startswith("(")
            or nl.lower().startswith("formulations")
            or nl.lower().startswith("action")
            or nl.lower().startswith("use:")
            or "pom" in nl.lower()
        ):
            return True
    return bool(next_line and next_line[:1].isupper())


def split_monographs(text: str) -> list[tuple[str, str]]:
    lines = text.splitlines()
    titles: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if _is_drug_title(line, nxt):
            titles.append((i, line.strip()))
    monographs: list[tuple[str, str]] = []
    for idx, (start, name) in enumerate(titles):
        end = titles[idx + 1][0] if idx + 1 < len(titles) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        if body:
            monographs.append((name, body))
    return monographs


def _parse_trade_pom(body: str) -> tuple[list[str], str, str]:
    first = body.splitlines()[0].strip() if body else ""
    m = TRADE_POM_RE.match(first)
    if not m:
        return [], "", body
    trades_raw = m.group("trades") or ""
    trades = []
    for part in re.split(r"[,;]", trades_raw):
        part = re.sub(r"\*+$", "", part).strip()
        if part:
            trades.append(part)
    pom = (m.group("pom") or "").strip()
    rest = "\n".join(body.splitlines()[1:]).strip()
    return trades, pom, rest


def _split_sections(body: str) -> dict[str, str]:
    pattern = re.compile(
        r"(?im)^(Formulations|Action|Use|Safety and handling|"
        r"Contraindications|Adverse reactions|Drug interactions|DOSES)\s*:?\s*"
    )
    matches = list(pattern.finditer(body))
    sections: dict[str, str] = {}
    if not matches:
        sections["full"] = body
        return sections
    # preamble before first section
    if matches[0].start() > 0:
        sections["preamble"] = body[: matches[0].start()].strip()
    for i, match in enumerate(matches):
        key = SECTION_HEADERS[match.group(1).lower()]
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[key] = body[start:end].strip()
    return sections


def _parse_doses_block(block: str) -> list[dict[str, Any]]:
    doses: list[dict[str, Any]] = []
    if not block:
        return doses
    # Attach continuation lines to taxa entries
    current_taxa = "other"
    current_chunks: list[str] = []

    def flush() -> None:
        nonlocal current_chunks
        text = " ".join(current_chunks).strip()
        current_chunks = []
        if not text or re.match(r"^no information available\.?$", text, re.I):
            return
        # Split multi-dose sentences on semicolons when they look like separate regimens
        parts = [p.strip() for p in re.split(r";(?=\s*\d)", text) if p.strip()]
        if not parts:
            parts = [text]
        for part in parts:
            species_note = ""
            indication = ""
            # "Seahorses: 6 mg/l ..."
            sm = re.match(r"^([^:]{2,40}):\s*(.+)$", part)
            if sm and not re.search(r"\d", sm.group(1)):
                species_note = sm.group(1).strip()
                part = sm.group(2).strip()
            doses.append(
                dose_dict(
                    taxa=current_taxa,
                    raw_text=part,
                    source="bsava",
                    species_note=species_note,
                    indication=indication,
                )
            )

    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        # "Mammals, Birds, Reptiles, Amphibians: No information available."
        multi = re.match(
            r"^((?:Fish|Mammals|Birds|Reptiles|Amphibians|Invertebrates)"
            r"(?:\s*,\s*(?:Fish|Mammals|Birds|Reptiles|Amphibians|Invertebrates))+)\s*:\s*(.*)$",
            line,
            re.I,
        )
        if multi:
            flush()
            rest = multi.group(2).strip()
            if rest and not re.match(r"^no information available\.?$", rest, re.I):
                for taxa_name in re.split(r"\s*,\s*", multi.group(1)):
                    current_taxa = taxa_name.lower()
                    current_chunks = [rest]
                    flush()
            current_chunks = []
            continue
        tm = re.match(
            r"^(Fish|Mammals|Birds|Reptiles|Amphibians|Invertebrates)\s*:\s*(.*)$",
            line,
            re.I,
        )
        if tm:
            flush()
            current_taxa = tm.group(1).lower()
            rest = tm.group(2).strip()
            current_chunks = [rest] if rest else []
            continue
        current_chunks.append(line)
    flush()
    return doses


def parse_monograph(name: str, body: str) -> dict[str, Any]:
    trades, pom, rest = _parse_trade_pom(body)
    sections = _split_sections(rest)
    drug = empty_drug(canonical_name_en=name, source="bsava")
    drug["trade_names"] = trades
    drug["pom_note"] = pom
    drug["formulations"] = sections.get("formulations", "")
    drug["action"] = sections.get("action", "")
    drug["use"] = sections.get("use", "")
    drug["safety_handling"] = sections.get("safety_handling", "")
    drug["contraindications"] = sections.get("contraindications", "")
    drug["adverse_reactions"] = sections.get("adverse_reactions", "")
    drug["drug_interactions"] = sections.get("drug_interactions", "")
    drug["doses"] = _parse_doses_block(sections.get("doses", ""))
    drug["aliases"] = merge_unique([name, *trades])
    drug["full_text_en"] = f"{name}\n{body}".strip()
    return drug


def extract(pdf_path: Path | None = None) -> list[dict[str, Any]]:
    pdf_path = pdf_path or DEFAULT_PDF
    if not pdf_path.exists():
        raise FileNotFoundError(f"BSAVA PDF not found: {pdf_path}")
    logger.info("Extracting BSAVA from %s", pdf_path)
    raw = _pdf_to_text(pdf_path)
    cleaned = clean_bsava_text(raw)
    monographs = split_monographs(cleaned)
    drugs = [parse_monograph(name, body) for name, body in monographs]
    filtered = []
    for d in drugs:
        name = d["canonical_name_en"]
        if re.search(r"\bsee\b", name, re.I):
            continue
        if name.lower() in {
            "health and safety in dispensing",
            "introduction",
            "appendix",
            "references",
            "index",
        }:
            continue
        if not (d["formulations"] or d["action"] or d["use"] or d["doses"]):
            continue
        filtered.append(d)
    logger.info("BSAVA: %s monographs (%s kept)", len(drugs), len(filtered))
    return filtered


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Extract BSAVA formulary PDF")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument(
        "--out",
        type=Path,
        default=raw_dir() / "bsava.jsonl",
    )
    args = parser.parse_args(argv)
    rows = extract(args.pdf)
    n = write_jsonl(args.out, rows)
    print(f"Wrote {n} drugs → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
