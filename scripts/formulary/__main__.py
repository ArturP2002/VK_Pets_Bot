"""python -m scripts.formulary → build_db by default."""
from __future__ import annotations

import sys

from scripts.formulary.build_db import main as build_main


def main() -> int:
    # Allow: python -m scripts.formulary.build_db  OR  python -m scripts.formulary
    if len(sys.argv) > 1 and sys.argv[1] in {"validate", "validate_kb"}:
        from scripts.formulary.validate_kb import main as validate_main

        return validate_main(sys.argv[2:])
    return build_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
