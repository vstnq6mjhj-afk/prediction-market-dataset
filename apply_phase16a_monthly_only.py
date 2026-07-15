from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BILLING_PATH = ROOT / "api" / "routes" / "billing_v2.py"
MARKER = "PHASE16A_MONTHLY_ONLY"


def replace_once(source: str, old: str, new: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(
            f"Expected exactly one patch anchor, found {count}: {old[:80]!r}"
        )
    return source.replace(old, new, 1)


def main() -> int:
    if not BILLING_PATH.exists():
        raise RuntimeError(
            f"Run this from the project root. Missing: {BILLING_PATH}"
        )

    source = BILLING_PATH.read_text(encoding="utf-8")
    if MARKER in source:
        print("Phase 16A monthly-only launch mode is already installed.")
        return 0

    source = replace_once(
        source,
        "PLAN_CONFIG: dict[str, dict[str, Any]] = {\n",
        """# PHASE16A_MONTHLY_ONLY
def visible_terms() -> tuple[str, ...]:
    raw = os.getenv("BILLING_VISIBLE_TERMS", "monthly")
    selected: list[str] = []
    unknown: list[str] = []

    for item in str(raw).split(","):
        slug = item.strip().lower()
        if not slug:
            continue
        if slug not in TERMS:
            unknown.append(slug)
            continue
        if slug not in selected:
            selected.append(slug)

    if unknown:
        raise ValueError(
            "BILLING_VISIBLE_TERMS contains unknown term(s): "
            + ", ".join(sorted(set(unknown)))
        )

    if not selected:
        return ("monthly",)

    return tuple(selected)


PLAN_CONFIG: dict[str, dict[str, Any]] = {
""",
    )

    source = replace_once(
        source,
        """    selected_term = (
        term if term in TERMS else "monthly"
    )
""",
        """    enabled_terms = visible_terms()
    selected_term = (
        term if term in enabled_terms else enabled_terms[0]
    )
""",
    )

    source = replace_once(
        source,
        "    for slug, term_config in TERMS.items():\n",
        """    for slug in enabled_terms:
        term_config = TERMS[slug]
""",
    )

    source = replace_once(
        source,
        """      Select monthly, 3-month, 6-month, or annual billing.
      Each term renews automatically until cancelled.
      The launch defaults use the same monthly rate across
      all terms; term discounts can be configured through
      Render environment variables without another code change.
""",
        """      Monthly subscriptions are available during the initial
      launch. Longer commitment terms remain disabled until the
      platform has established stable paid usage and the terms
      have completed legal and operational review.
""",
    )

    source = replace_once(
        source,
        """    if plan not in PLAN_CONFIG or term not in TERMS:
        raise HTTPException(
            status_code=404,
            detail="Unknown billing plan or term.",
        )
""",
        """    if plan not in PLAN_CONFIG or term not in TERMS:
        raise HTTPException(
            status_code=404,
            detail="Unknown billing plan or term.",
        )

    if term not in visible_terms():
        raise HTTPException(
            status_code=404,
            detail="This billing term is not currently available.",
        )
""",
    )

    compile(source, str(BILLING_PATH), "exec")

    backup = BILLING_PATH.with_suffix(
        BILLING_PATH.suffix + ".phase16a.bak"
    )
    if not backup.exists():
        shutil.copy2(BILLING_PATH, backup)

    BILLING_PATH.write_text(source, encoding="utf-8")
    print("Phase 16A monthly-only launch mode installed.")
    print(f"Updated: {BILLING_PATH}")
    print(f"Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
