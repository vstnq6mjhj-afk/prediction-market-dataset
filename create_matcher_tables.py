from __future__ import annotations

import os
from pathlib import Path

import duckdb


DB_PATH = Path(os.getenv("DB_PATH", "/var/data/warehouse.duckdb"))


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"DuckDB warehouse not found at {DB_PATH}. "
            "Run this on the Render API service that owns the persistent disk."
        )

    print(f"[matcher-schema] Opening {DB_PATH}")
    connection = duckdb.connect(str(DB_PATH))

    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_semantics (
                platform VARCHAR NOT NULL,
                market_id VARCHAR NOT NULL,
                parent_event_id VARCHAR,
                series_id VARCHAR,

                category VARCHAR,
                event_type VARCHAR,
                outcome_type VARCHAR,

                primary_entity VARCHAR,
                secondary_entity VARCHAR,
                competition VARCHAR,

                target VARCHAR,
                comparison_operator VARCHAR,
                threshold DOUBLE,
                unit VARCHAR,

                event_start TIMESTAMP,
                event_end TIMESTAMP,

                resolution_source VARCHAR,
                resolution_rule_hash VARCHAR,

                canonical_key VARCHAR,
                parser_version VARCHAR,
                extraction_confidence DOUBLE,
                needs_review BOOLEAN DEFAULT TRUE,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (platform, market_id)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_matches (
                match_id VARCHAR PRIMARY KEY,

                platform_a VARCHAR NOT NULL,
                market_id_a VARCHAR NOT NULL,
                platform_b VARCHAR NOT NULL,
                market_id_b VARCHAR NOT NULL,

                match_status VARCHAR NOT NULL,
                match_confidence DOUBLE,
                canonical_key VARCHAR,

                reviewed BOOLEAN DEFAULT FALSE,
                review_note VARCHAR,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_market_semantics_canonical_key
            ON market_semantics (canonical_key)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_market_semantics_event_type
            ON market_semantics (event_type)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_market_matches_canonical_key
            ON market_matches (canonical_key)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_market_matches_status
            ON market_matches (match_status)
            """
        )

        print("\n[matcher-schema] market_semantics")
        print(connection.execute("DESCRIBE market_semantics").fetchdf().to_string(index=False))

        print("\n[matcher-schema] market_matches")
        print(connection.execute("DESCRIBE market_matches").fetchdf().to_string(index=False))

        semantics_count = connection.execute(
            "SELECT COUNT(*) FROM market_semantics"
        ).fetchone()[0]
        matches_count = connection.execute(
            "SELECT COUNT(*) FROM market_matches"
        ).fetchone()[0]

        print(f"\n[matcher-schema] market_semantics rows: {semantics_count:,}")
        print(f"[matcher-schema] market_matches rows: {matches_count:,}")
        print("[matcher-schema] Complete")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
