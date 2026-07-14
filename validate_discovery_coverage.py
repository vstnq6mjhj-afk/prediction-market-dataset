from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


DIAGNOSTICS_PATH = Path(
    os.getenv(
        "DISCOVERY_DIAGNOSTICS_PATH",
        "/var/data/connector_diagnostics_discovery.json",
    )
)


def connector(
    payload: dict[str, Any],
    name: str,
) -> dict[str, Any]:
    return (
        payload.get("connectors", {}).get(name, {})
        or {}
    )


def main() -> int:
    if not DIAGNOSTICS_PATH.exists():
        print(
            f"FAIL: discovery diagnostics not found: "
            f"{DIAGNOSTICS_PATH}"
        )
        return 1

    payload = json.loads(
        DIAGNOSTICS_PATH.read_text(encoding="utf-8")
    )
    failures: list[str] = []
    warnings: list[str] = []

    kalshi = connector(payload, "kalshi")
    kalshi_page = kalshi.get("pagination") or {}
    if kalshi.get("error"):
        failures.append(
            f"Kalshi connector error: {kalshi['error']}"
        )
    if kalshi_page.get("mve_filter") != "exclude":
        failures.append(
            "Kalshi discovery did not use mve_filter=exclude."
        )
    if not kalshi_page.get("complete"):
        failures.append(
            "Kalshi pagination did not reach cursor exhaustion; "
            f"termination={kalshi_page.get('termination_reason')}."
        )

    polymarket = connector(payload, "polymarket")
    polymarket_page = polymarket.get("pagination") or {}
    if polymarket.get("error"):
        failures.append(
            f"Polymarket connector error: "
            f"{polymarket['error']}"
        )
    if polymarket_page.get("endpoint") != "/events/keyset":
        failures.append(
            "Polymarket discovery did not use the "
            "/events/keyset endpoint."
        )
    if polymarket_page.get("partial_error"):
        failures.append(
            "Polymarket keyset pagination stopped after a "
            f"request error: {polymarket_page.get('partial_error')}."
        )
    if int(polymarket.get("returned_rows") or 0) <= 100:
        failures.append(
            "Polymarket discovery still returned 100 or fewer "
            "markets."
        )
    if not polymarket_page.get("complete"):
        failures.append(
            "Polymarket event pagination did not reach a final "
            f"page; termination="
            f"{polymarket_page.get('termination_reason')}."
        )

    manifold = connector(payload, "manifold")
    manifold_page = manifold.get("pagination") or {}
    if manifold.get("error"):
        warnings.append(
            f"Manifold connector error: {manifold['error']}"
        )
    elif not manifold_page.get("complete"):
        warnings.append(
            "Manifold pagination remains incomplete. Manifold "
            "must remain excluded from customer-facing products "
            "while commercial licensing is pending."
        )

    predictit = connector(payload, "predictit")
    if predictit.get("error"):
        failures.append(
            f"PredictIt connector error: {predictit['error']}"
        )

    print("DISCOVERY COVERAGE VALIDATION")
    print(
        "total_unique_rows:",
        payload.get("total_unique_rows"),
    )

    for warning in warnings:
        print("WARNING:", warning)

    if failures:
        for failure in failures:
            print("FAIL:", failure)
        return 1

    print(
        "PASS: Kalshi and Polymarket keyset discovery completed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
