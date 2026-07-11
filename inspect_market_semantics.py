from __future__ import annotations

import os
from pathlib import Path

import duckdb


DB_PATH = Path(os.getenv("DB_PATH", "/var/data/warehouse.duckdb"))


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DuckDB warehouse not found: {DB_PATH}")

    connection = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        print("[inspect-semantics] Counts by platform and event type\n")
        print(
            connection.execute(
                """
                SELECT
                    platform,
                    event_type,
                    is_matchable,
                    needs_review,
                    COUNT(*) AS markets
                FROM market_semantics
                GROUP BY platform, event_type, is_matchable, needs_review
                ORDER BY platform, markets DESC
                """
            ).fetchdf().to_string(index=False)
        )

        print("\n[inspect-semantics] Matchable samples\n")
        print(
            connection.execute(
                """
                SELECT
                    platform,
                    market_id,
                    raw_title,
                    event_type,
                    primary_entity,
                    secondary_entity,
                    competition,
                    target,
                    comparison_operator,
                    lower_threshold,
                    upper_threshold,
                    canonical_key,
                    extraction_confidence
                FROM market_semantics
                WHERE is_matchable = TRUE
                ORDER BY extraction_confidence DESC, platform
                LIMIT 40
                """
            ).fetchdf().to_string(index=False)
        )

        print("\n[inspect-semantics] Review samples\n")
        print(
            connection.execute(
                """
                SELECT
                    platform,
                    market_id,
                    raw_title,
                    event_type,
                    parse_notes,
                    extraction_confidence
                FROM market_semantics
                WHERE needs_review = TRUE
                ORDER BY platform, extraction_confidence DESC
                LIMIT 40
                """
            ).fetchdf().to_string(index=False)
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
