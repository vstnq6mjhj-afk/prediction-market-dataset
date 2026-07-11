from __future__ import annotations

import os
from pathlib import Path

import duckdb


DB_PATH = Path(os.getenv("DB_PATH", "/var/data/warehouse.duckdb"))

COLUMNS = [
    ("raw_title", "VARCHAR"),
    ("normalized_title", "VARCHAR"),
    ("event_key", "VARCHAR"),
    ("lower_threshold", "DOUBLE"),
    ("upper_threshold", "DOUBLE"),
    ("source_url", "VARCHAR"),
    ("parse_notes", "VARCHAR"),
    ("is_matchable", "BOOLEAN DEFAULT FALSE"),
]


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DuckDB warehouse not found: {DB_PATH}")

    print(f"[schema-v2] Opening {DB_PATH}")
    connection = duckdb.connect(str(DB_PATH))

    try:
        existing_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info('market_semantics')"
            ).fetchall()
        }

        for name, column_type in COLUMNS:
            if name in existing_columns:
                print(f"[schema-v2] column already exists: {name}")
                continue

            connection.execute(
                f"ALTER TABLE market_semantics ADD COLUMN {name} {column_type}"
            )
            print(f"[schema-v2] added column: {name}")

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_market_semantics_event_key
            ON market_semantics (event_key)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_market_semantics_matchable
            ON market_semantics (is_matchable)
            """
        )

        print("\n[schema-v2] Updated market_semantics schema")
        print(connection.execute("DESCRIBE market_semantics").fetchdf().to_string(index=False))
        print("\n[schema-v2] Complete")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
