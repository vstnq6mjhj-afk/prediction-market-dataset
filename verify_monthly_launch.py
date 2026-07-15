from __future__ import annotations

from api.routes.billing_v2 import (
    PLAN_CONFIG,
    amount_pence,
    checkout_readiness,
    format_gbp,
    visible_terms,
)


def main() -> int:
    terms = visible_terms()
    print("VISIBLE BILLING TERMS")
    print(terms)

    if terms != ("monthly",):
        raise RuntimeError("Launch policy requires monthly-only billing.")

    print("\nMONTHLY PLANS")
    for slug, config in PLAN_CONFIG.items():
        print(
            f"{config['display_name']}: "
            f"{format_gbp(amount_pence(slug, 'monthly'))}/month"
        )

    ready, reason = checkout_readiness()
    print(f"\ncheckout_ready={ready}")
    if reason:
        print(f"reason={reason}")

    print("PASS: billing is monthly-only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
