"""Extract Russian manual препараты.docx → JSONL."""
from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Any

from scripts.formulary.common import (
    detect_taxa,
    dose_dict,
    empty_drug,
    knowledge_base_dir,
    merge_unique,
    raw_dir,
    write_jsonl,
)

logger = logging.getLogger(__name__)

DEFAULT_DOCX = knowledge_base_dir() / "препараты.docx"

SECTION_MARKERS = (
    ("аналоги", "analogs"),
    ("активные вещества", "inn"),
    ("активное вещество", "inn"),
    ("описание", "description"),
    ("дозы", "doses"),
    ("дозировка", "doses"),
    ("альтернативные дозы", "doses"),
    ("альтернативная доза", "doses"),
    ("противопоказания", "contraindications"),
)


def _load_paragraphs(docx_path: Path) -> list[str]:
    from docx import Document

    doc = Document(str(docx_path))
    return [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]


def _marker(line: str) -> tuple[str | None, str]:
    lowered = line.lower().strip()
    for prefix, key in SECTION_MARKERS:
        if lowered.startswith(prefix):
            rest = line[len(prefix) :].lstrip(" :.—–-")
            return key, rest.strip()
    return None, line


_FALSE_TITLE_RE = re.compile(
    r"(?i)заболеван|гепатит|холестаз|холецистит|нефрит|противопоказ|"
    r"применять|дозировк|описание|аналог|липидоз|бронхоспазм|анафилакс"
)


def _looks_like_title(line: str, next_line: str | None) -> bool:
    """True only when this line starts a new drug card."""
    line = line.rstrip(":").strip()
    if len(line) > 60:
        return False
    if ":" in line or "：" in line:
        return False
    if re.match(r"^[\d\W]", line):
        return False
    if re.search(r"\d\s*(мг|mg|мкг|µg)", line, re.I):
        return False
    if _FALSE_TITLE_RE.search(line):
        return False
    key, _ = _marker(line)
    if key:
        return False
    if not re.match(
        r"^[A-ZА-ЯЁD][\w\-]*(?:\s+[A-ZА-ЯЁA-Za-zА-Яа-яё0-9][\w\-]*){0,4}$",
        line,
    ):
        return False
    if not next_line:
        return False
    nk, _ = _marker(next_line)
    if nk in {"analogs", "inn", "description", "doses", "contraindications"}:
        return True
    return False


def split_blocks(paragraphs: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    # Normalize trailing colons on bare titles early
    paragraphs = [p.rstrip(":").strip() if p.count(":") == 1 and p.endswith(":") else p for p in paragraphs]

    def has_body(lines: list[str]) -> bool:
        joined = "\n".join(lines).lower()
        return any(x in joined for x in ("описан", "доз", "противопоказ", "аналог", "активн"))

    i = 0
    while i < len(paragraphs):
        line = paragraphs[i]
        nxt = paragraphs[i + 1] if i + 1 < len(paragraphs) else None
        if current and has_body(current) and (
            _looks_like_title(line, nxt) or _is_soft_title(line, paragraphs, i)
        ):
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
        i += 1
    if current:
        blocks.append(current)
    return blocks


def _is_soft_title(line: str, paragraphs: list[str], idx: int) -> bool:
    """Stacked brand/INN titles before a section marker (Серения / Маропитант)."""
    if _FALSE_TITLE_RE.search(line):
        return False
    if ":" in line or len(line) > 40 or re.match(r"^[\d\W]", line):
        return False
    if _marker(line)[0]:
        return False
    if len(line.split()) > 3:
        return False
    if not re.match(r"^[A-ZА-ЯЁD][\w\-]*(?:\s+[\w\-]+){0,2}$", line):
        return False
    # Require a section marker within 3 lines, with only short name-like lines between.
    for j in range(idx + 1, min(idx + 4, len(paragraphs))):
        candidate = paragraphs[j]
        key, _ = _marker(candidate)
        if key in {"analogs", "inn", "description", "doses", "contraindications"}:
            return True
        if key or ":" in candidate or _FALSE_TITLE_RE.search(candidate):
            return False
        if len(candidate) > 40 or len(candidate.split()) > 3:
            return False
    return False


def _parse_analogs(text: str) -> list[str]:
    parts = re.split(r"[,;]", text)
    out = []
    for part in parts:
        part = re.sub(r"\([^)]*\)", "", part)
        part = part.strip(" .")
        if part and "не найдено" not in part.lower():
            out.append(part)
    return out


def _extract_en_inn(text: str) -> str:
    # Acetazolamide / (Acetazolamide) / Latin INN
    m = re.search(r"\(([A-Za-z][A-Za-z0-9\-]{2,40})\)", text)
    if m:
        return m.group(1)
    m = re.search(r"\b([A-Z][a-z]+(?:ol|ide|ine|ate|am|ib|in|one|ium)?)\b", text)
    if m:
        return m.group(1)
    return ""


def parse_block(lines: list[str]) -> dict[str, Any] | None:
    if not lines:
        return None
    # Collect leading title lines until a section marker
    titles: list[str] = []
    i = 0
    while i < len(lines):
        key, _ = _marker(lines[i])
        if key:
            break
        titles.append(lines[i])
        i += 1
    if not titles:
        return None

    name_ru = titles[0].strip().rstrip(":")
    name_en = ""
    extras = titles[1:]
    for t in extras:
        en = _extract_en_inn(t)
        if en:
            name_en = en
        elif re.match(r"^[A-Za-z]", t) and not name_en:
            name_en = t.strip()

    sections: dict[str, list[str]] = {
        "analogs": [],
        "inn": [],
        "description": [],
        "doses": [],
        "contraindications": [],
    }
    current = "description"
    for line in lines[i:]:
        key, rest = _marker(line)
        if key:
            current = key
            if rest:
                sections.setdefault(current, []).append(rest)
            continue
        sections.setdefault(current, []).append(line)

    inn_text = " ".join(sections.get("inn", []))
    if not name_en:
        name_en = _extract_en_inn(inn_text) or _extract_en_inn(" ".join(titles))

    analogs = []
    for chunk in sections.get("analogs", []):
        analogs.extend(_parse_analogs(chunk))

    description = " ".join(sections.get("description", [])).strip()
    contraindications = " ".join(sections.get("contraindications", [])).strip()

    doses: list[dict[str, Any]] = []
    for dose_line in sections.get("doses", []):
        for part in re.split(r"(?<=\.)\s+(?=\d)|;\s*", dose_line):
            part = part.strip()
            if not part:
                continue
            doses.append(
                dose_dict(
                    taxa=detect_taxa(part),
                    raw_text=part,
                    source="manual",
                )
            )

    drug = empty_drug(
        canonical_name_en=name_en or name_ru,
        canonical_name_ru=name_ru,
        source="manual",
    )
    drug["trade_names"] = merge_unique([name_ru, *analogs])
    drug["aliases"] = merge_unique(
        [name_ru, name_en, *analogs, *extras, *([inn_text] if inn_text else [])]
    )
    drug["use"] = description
    drug["action"] = description
    drug["contraindications"] = contraindications
    drug["doses"] = doses
    drug["full_text_ru"] = "\n".join(lines)
    if name_en:
        drug["full_text_en"] = name_en
    return drug


def extract(docx_path: Path | None = None) -> list[dict[str, Any]]:
    docx_path = docx_path or DEFAULT_DOCX
    if not docx_path.exists():
        raise FileNotFoundError(f"RU manual not found: {docx_path}")
    logger.info("Extracting RU manual from %s", docx_path)
    paragraphs = _load_paragraphs(docx_path)
    blocks = split_blocks(paragraphs)
    drugs: list[dict[str, Any]] = []
    for block in blocks:
        parsed = parse_block(block)
        if not parsed:
            continue
        name = parsed["canonical_name_ru"] or parsed["canonical_name_en"]
        if len(name) > 60 or ":" in name:
            continue
        if not (parsed.get("use") or parsed.get("doses") or parsed.get("contraindications")):
            continue
        drugs.append(parsed)
    logger.info("Manual RU: %s drugs from %s blocks", len(drugs), len(blocks))
    return drugs


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Extract препараты.docx")
    parser.add_argument("--docx", type=Path, default=DEFAULT_DOCX)
    parser.add_argument("--out", type=Path, default=raw_dir() / "manual_ru.jsonl")
    args = parser.parse_args(argv)
    rows = extract(args.docx)
    n = write_jsonl(args.out, rows)
    print(f"Wrote {n} drugs → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
