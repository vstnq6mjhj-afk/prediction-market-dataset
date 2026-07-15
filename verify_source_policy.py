from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from api.source_policy import (
    PolicyContext,
    allowed_platforms,
    install_market_policy_view,
    install_semantic_policy_views,
    policy_snapshot,
)


DB_PATH = Path(os.getenv("DB_PATH", "/var/data/warehouse.duckdb"))
SEMANTICS_DB_PATH = Path(
    os.getenv(
        "SEMANTICS_DB_PATH",
        "/var/data/market_semantics.duckdb",
    )
)


def _verify_market_db(context: PolicyContext) -> None:
    import duckdb

    if not DB_PATH.exists():
        print(f"Warehouse not found; skipped: {DB_PATH}")
        return

    connection = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        install_market_policy_view(connection, context)
        visible = {
            str(row[0]).lower()
            for row in connection.execute(
                """
                SELECT DISTINCT platform
                FROM market_snapshots
                WHERE platform IS NOT NULL
                """
            ).fetchall()
        }
    finally:
        connection.close()

    allowed = set(allowed_platforms(context))
    leaked = visible - allowed
    print(
        f"{context.value}: visible={sorted(visible)} "
        f"allowed={sorted(allowed)}"
    )
    if leaked:
        raise RuntimeError(
            f"Blocked platform leak detected: {sorted(leaked)}"
        )


def _verify_semantics() -> None:
    import duckdb

    if not SEMANTICS_DB_PATH.exists():
        print(
            "Semantic database not found; skipped: "
            f"{SEMANTICS_DB_PATH}"
        )
        return

    connection = duckdb.connect(
        str(SEMANTICS_DB_PATH),
        read_only=True,
    )
    try:
        install_semantic_policy_views(
            connection,
            PolicyContext.MATCHER,
        )
        visible = {
            str(row[0]).lower()
            for row in connection.execute(
                """
                SELECT DISTINCT platform
                FROM event_contracts
                WHERE platform IS NOT NULL
                """
            ).fetchall()
        }
    finally:
        connection.close()

    allowed = set(allowed_platforms(PolicyContext.MATCHER))
    leaked = visible - allowed
    print(
        f"matcher: visible={sorted(visible)} "
        f"allowed={sorted(allowed)}"
    )
    if leaked:
        raise RuntimeError(
            f"Blocked matcher platform leak detected: {sorted(leaked)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-db", action="store_true")
    args = parser.parse_args()

    print("SOURCE POLICY")
    print(json.dumps(policy_snapshot(), indent=2, sort_keys=True))

    if args.skip_db:
        return 0

    for context in (
        PolicyContext.CUSTOMER_API,
        PolicyContext.EXPLORER,
        PolicyContext.EXPORT,
        PolicyContext.PUBLIC_SUMMARY,
    ):
        _verify_market_db(context)

    _verify_semantics()
    print("PASS: no blocked platform was visible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
