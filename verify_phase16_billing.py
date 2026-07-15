from __future__ import annotations

import json
import os

from api.routes.billing_v2 import (
    PLAN_CONFIG,
    TERMS,
    amount_pence,
    checkout_readiness,
    format_gbp,
    price_id,
)
from api.supabase_client import supabase


def main() -> int:
    print("PHASE 16 BILLING CONFIGURATION")
    print(
        json.dumps(
            {
                "checkout_enabled": os.getenv(
                    "BILLING_CHECKOUT_ENABLED",
                    "false",
                ),
                "test_mode_only": os.getenv(
                    "BILLING_TEST_MODE_ONLY",
                    "true",
                ),
                "require_commercial_sources": os.getenv(
                    "BILLING_REQUIRE_COMMERCIAL_SOURCES",
                    "true",
                ),
                "stripe_key_mode": (
                    "test"
                    if os.getenv(
                        "STRIPE_SECRET_KEY",
                        "",
                    ).startswith("sk_test_")
                    else "live"
                    if os.getenv(
                        "STRIPE_SECRET_KEY",
                        "",
                    ).startswith("sk_live_")
                    else "missing"
                ),
            },
            indent=2,
        )
    )

    for term, term_config in TERMS.items():
        print(f"\n{term_config.label}")
        for plan, plan_config in PLAN_CONFIG.items():
            configured_price = price_id(plan, term)
            print(
                f"  {plan_config['display_name']}: "
                f"{format_gbp(amount_pence(plan, term))} "
                f"| price_id="
                f"{configured_price or 'inline price_data'}"
            )

    ready, reason = checkout_readiness()
    print(
        f"\ncheckout_ready={ready}"
        + (f" reason={reason}" if reason else "")
    )

    api_keys = (
        supabase.table("api_keys")
        .select(
            "email,plan,subscription_status,"
            "stripe_customer_id,stripe_subscription_id,"
            "billing_term,current_period_start,"
            "current_period_end,cancel_at_period_end"
        )
        .limit(1)
        .execute()
    )
    print(
        "PASS: api_keys billing columns are available "
        f"(sample_rows={len(api_keys.data or [])})."
    )

    events = (
        supabase.table("stripe_webhook_events")
        .select("event_id,event_type,status,attempt_count")
        .limit(1)
        .execute()
    )
    print(
        "PASS: stripe_webhook_events is available "
        f"(sample_rows={len(events.data or [])})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
