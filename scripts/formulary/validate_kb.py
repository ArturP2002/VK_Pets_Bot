"""Validate formulary.db: counts, holes, smoke search."""
from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

from scripts.formulary.common import normalize_name, project_root, transliterate_ru

logger = logging.getLogger(__name__)

SMOKE_QUERIES = ("Acetazolamide", "Диакарб", "Серения", "Enrofloxacin", "Meloxicam")


def _similar_pairs(names: list[str], threshold: float = 0.88) -> list[tuple[str, str, float]]:
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return []
    pairs: list[tuple[str, str, float]] = []
    norms = [(n, normalize_name(n)) for n in names if n]
    for i, (a, an) in enumerate(norms):
        for b, bn in norms[i + 1 :]:
            if not an or not bn or an == bn:
                continue
            score = fuzz.ratio(an, bn) / 100.0
            if score >= threshold:
                pairs.append((a, b, score))
    return pairs[:50]


def validate(db_path: Path) -> dict:
    if not db_path.exists():
        raise FileNotFoundError(f"Formulary DB not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    drugs = conn.execute("SELECT COUNT(*) AS c FROM drugs").fetchone()["c"]
    aliases = conn.execute("SELECT COUNT(*) AS c FROM drug_aliases").fetchone()["c"]
    doses = conn.execute("SELECT COUNT(*) AS c FROM doses").fetchone()["c"]
    missing_minmax = conn.execute(
        """
        SELECT COUNT(*) AS c FROM doses
        WHERE raw_text != '' AND (dose_min IS NULL OR dose_max IS NULL)
        """
    ).fetchone()["c"]
    by_source = dict(
        conn.execute(
            """
            SELECT source, COUNT(*) FROM doses GROUP BY source ORDER BY COUNT(*) DESC
            """
        ).fetchall()
    )
    drugs_no_doses = conn.execute(
        """
        SELECT COUNT(*) AS c FROM drugs d
        WHERE NOT EXISTS (SELECT 1 FROM doses x WHERE x.drug_id = d.id)
        """
    ).fetchone()["c"]

    # Duplicate canonical names (case-insensitive)
    dup_names = conn.execute(
        """
        SELECT lower(canonical_name_en) AS n, COUNT(*) AS c
        FROM drugs
        WHERE canonical_name_en != ''
        GROUP BY lower(canonical_name_en)
        HAVING c > 1
        """
    ).fetchall()

    names = [
        r[0]
        for r in conn.execute(
            "SELECT canonical_name_en FROM drugs WHERE canonical_name_en != ''"
        ).fetchall()
    ]
    similar = _similar_pairs(names)

    smoke: dict[str, list[str]] = {}
    for query in SMOKE_QUERIES:
        qn = normalize_name(query)
        qt = transliterate_ru(query)
        rows = conn.execute(
            """
            SELECT DISTINCT d.canonical_name_en, d.canonical_name_ru
            FROM drug_aliases a
            JOIN drugs d ON d.id = a.drug_id
            WHERE a.alias_norm = ? OR a.alias_norm = ?
               OR a.alias_norm LIKE ?
            LIMIT 5
            """,
            (qn, qt, f"%{qn}%"),
        ).fetchall()
        # FTS fallback
        if not rows:
            try:
                rows = conn.execute(
                    """
                    SELECT DISTINCT d.canonical_name_en, d.canonical_name_ru
                    FROM drugs_fts f
                    JOIN drug_aliases a ON a.id = f.rowid
                    JOIN drugs d ON d.id = a.drug_id
                    WHERE drugs_fts MATCH ?
                    LIMIT 5
                    """,
                    (qn.replace(" ", " OR "),),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        smoke[query] = [
            (r["canonical_name_ru"] or r["canonical_name_en"]).strip()
            if isinstance(r, sqlite3.Row)
            else (r[1] or r[0])
            for r in rows
        ]

    report = {
        "drugs": drugs,
        "aliases": aliases,
        "doses": doses,
        "missing_minmax": missing_minmax,
        "doses_by_source": by_source,
        "drugs_without_doses": drugs_no_doses,
        "duplicate_en_names": [dict(r) for r in dup_names],
        "similar_name_pairs": similar,
        "smoke": smoke,
    }
    conn.close()
    return report


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = project_root()
    parser = argparse.ArgumentParser(description="Validate formulary.db")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(
            __import__("os").getenv("FORMULARY_DB", str(root / "data" / "formulary.db"))
        ),
    )
    args = parser.parse_args(argv)
    report = validate(args.db)
    print(f"drugs={report['drugs']} aliases={report['aliases']} doses={report['doses']}")
    print(f"doses_by_source={report['doses_by_source']}")
    print(f"missing_minmax={report['missing_minmax']} drugs_without_doses={report['drugs_without_doses']}")
    if report["duplicate_en_names"]:
        print(f"duplicate_en_names={report['duplicate_en_names'][:10]}")
    if report["similar_name_pairs"]:
        print("similar_name_pairs (sample):")
        for a, b, score in report["similar_name_pairs"][:15]:
            print(f"  {score:.2f}  {a}  ≈  {b}")
    print("smoke search:")
    for q, hits in report["smoke"].items():
        status = ", ".join(hits) if hits else "MISS"
        print(f"  {q!r} → {status}")
    # Fail if critical smoke queries miss entirely on a built DB with drugs
    critical_miss = [q for q in ("Acetazolamide", "Диакарб") if not report["smoke"].get(q)]
    if report["drugs"] == 0:
        print("FAIL: empty formulary")
        return 1
    if critical_miss:
        print(f"WARN: critical smoke misses: {critical_miss}")
        return 2
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
