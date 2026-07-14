from __future__ import annotations

import json
import os
from pathlib import Path


DIAGNOSTICS_DIR = Path(
    os.getenv(
        "CONNECTOR_DIAGNOSTICS_DIR",
        "/var/data",
    )
)


def _display_connector(
    name: str,
    values: dict,
) -> None:
    pagination = values.get("pagination") or {}

    print(f"\n{name}")
    print("  returned:", values.get("returned_rows"))
    print("  accepted:", values.get("accepted_rows"))
    print("  elapsed_seconds:", values.get("elapsed_seconds"))
    print("  error:", values.get("error"))

    if pagination:
        print("  endpoint:", pagination.get("endpoint"))
        print("  complete:", pagination.get("complete"))
        print(
            "  termination_reason:",
            pagination.get("termination_reason"),
        )
        print(
            "  pages_fetched:",
            pagination.get("pages_fetched"),
        )
        print(
            "  page_limit_reached:",
            pagination.get("page_limit_reached"),
        )
        print(
            "  raw_events_received:",
            pagination.get("raw_events_received"),
        )
        print(
            "  raw_markets_received:",
            pagination.get("raw_markets_received"),
        )
        print(
            "  duplicate_markets:",
            pagination.get("duplicate_markets"),
        )
        print(
            "  mve_filter:",
            pagination.get("mve_filter"),
        )
        print(
            "  excluded_multivariate:",
            pagination.get("excluded_multivariate"),
        )


def main() -> None:
    for mode in ("fast", "discovery"):
        path = (
            DIAGNOSTICS_DIR
            / f"connector_diagnostics_{mode}.json"
        )
        print(f"\n{mode.upper()} CONNECTOR DIAGNOSTICS")

        if not path.exists():
            print(f"No diagnostics file yet: {path}")
            continue

        payload = json.loads(
            path.read_text(encoding="utf-8")
        )
        print(
            "completed_at:",
            payload.get("completed_at"),
        )
        print(
            "total_unique_rows:",
            payload.get("total_unique_rows"),
        )
        print(
            "elapsed_seconds:",
            payload.get("elapsed_seconds"),
        )

        for name, values in (
            payload.get("connectors") or {}
        ).items():
            _display_connector(name, values)


if __name__ == "__main__":
    main()
