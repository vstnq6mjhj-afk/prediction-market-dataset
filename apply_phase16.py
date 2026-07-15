from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MAIN_PATH = ROOT / "api" / "main.py"
MARKER = "PHASE16_BILLING_V2"


def main() -> int:
    if not MAIN_PATH.exists():
        raise RuntimeError(
            "Run this script from the project root. "
            f"Missing: {MAIN_PATH}"
        )

    source = MAIN_PATH.read_text(encoding="utf-8")
    if MARKER in source:
        print("Phase 16 billing router is already installed.")
        return 0

    import_anchor = (
        "from api.routes.explorer import "
        "router as explorer_router"
    )
    include_anchor = "app.include_router(explorer_router)"

    if import_anchor not in source:
        raise RuntimeError(
            "Could not find the explorer router import."
        )
    if include_anchor not in source:
        raise RuntimeError(
            "Could not find the explorer router registration."
        )

    source = source.replace(
        import_anchor,
        import_anchor
        + "\n"
        + f"# {MARKER}\n"
        + "from api.routes.billing_v2 import "
        + "router as billing_v2_router",
        1,
    )
    source = source.replace(
        include_anchor,
        include_anchor
        + "\n"
        + "app.include_router(billing_v2_router)",
        1,
    )

    compile(source, str(MAIN_PATH), "exec")

    backup = MAIN_PATH.with_suffix(
        MAIN_PATH.suffix + ".phase16.bak"
    )
    if not backup.exists():
        shutil.copy2(MAIN_PATH, backup)

    MAIN_PATH.write_text(source, encoding="utf-8")
    print("Phase 16 billing router installed.")
    print(f"Updated: {MAIN_PATH}")
    print(f"Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
