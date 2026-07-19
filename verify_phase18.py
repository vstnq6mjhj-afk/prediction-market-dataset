from __future__ import annotations

import ast
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CUSTOMER_VARS = (
    "CUSTOMER_API_PLATFORMS",
    "EXPLORER_DATA_PLATFORMS",
    "CUSTOMER_EXPORT_PLATFORMS",
    "CUSTOMER_MATCHER_PLATFORMS",
    "PUBLIC_SUMMARY_PLATFORMS",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_static() -> None:
    source_policy = (ROOT / "api/source_policy.py").read_text(encoding="utf-8")
    aggregator = (ROOT / "connectors/market_aggregator.py").read_text(encoding="utf-8")
    admin = (ROOT / "api/routes/admin_data_health.py").read_text(encoding="utf-8")
    main = (ROOT / "api/main.py").read_text(encoding="utf-8")

    for path in (
        ROOT / "api/source_policy.py",
        ROOT / "connectors/market_aggregator.py",
        ROOT / "api/routes/admin_data_health.py",
        ROOT / "api/main.py",
    ):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    require('"kalshi,polymarket,predictit"' in source_policy, "Safe internal default missing")
    require('"kalshi,polymarket,predictit,manifold"' not in source_policy, "Manifold remains in internal default")
    require("disabled_by_internal_source_policy" in aggregator, "Aggregator policy skip missing")
    require("Manifold\", \"Declined" in admin, "Admin licensing status missing")
    require("COMPANY_LEGAL_NAME" in main, "Company configuration missing from main.py")
    require("PMD Data Systems Ltd" in main, "Legal company fallback missing")
    print("PASS static company identity, Manifold suspension, and licensing-status checks.")


def verify_environment() -> None:
    for name in CUSTOMER_VARS:
        require(not os.getenv(name, "").strip(), f"{name} must remain empty")

    internal = [x.strip().lower() for x in os.getenv(
        "INTERNAL_DATA_PLATFORMS", "kalshi,polymarket,predictit"
    ).split(",") if x.strip()]
    require("manifold" not in internal, "Manifold must not be internally collected")
    require(set(internal) <= {"kalshi", "polymarket", "predictit"}, "Unexpected internal platform")
    require(os.getenv("BILLING_CHECKOUT_ENABLED", "false").lower() not in {"1","true","yes","on"}, "Checkout must be disabled")
    require(os.getenv("BILLING_TEST_MODE_ONLY", "true").lower() in {"1","true","yes","on"}, "Test mode lock must be true")
    require(os.getenv("BILLING_REQUIRE_COMMERCIAL_SOURCES", "true").lower() in {"1","true","yes","on"}, "Commercial-source lock must be true")
    print("PASS environment: customer allowlists empty, Manifold suspended, checkout locked.")


if __name__ == "__main__":
    verify_static()
    verify_environment()
    print("PHASE 18 VERIFICATION PASSED")
