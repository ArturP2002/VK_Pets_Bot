"""python -m scripts.formulary → build_db by default."""
from __future__ import annotations

import sys

from scripts.formulary.build_db import main as build_main


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"validate", "validate_kb"}:
        from scripts.formulary.validate_kb import main as validate_main

        return validate_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "export":
        from scripts.formulary.export_xlsx import main as export_main

        return export_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "import":
        from scripts.formulary.import_xlsx import main as import_main

        return import_main(sys.argv[2:])
    return build_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
