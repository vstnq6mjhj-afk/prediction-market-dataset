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

    connection = duckdb.connect(str(DB_PATH), read_only=True)

    try:
        print(f"[inspect] Warehouse: {DB_PATH}\n")

        schema = connection.execute(
            "DESCRIBE market_snapshots"
        ).fetchdf()
        print("[inspect] market_snapshots schema")
        print(schema.to_string(index=False))

        platforms = [
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT platform
                FROM market_snapshots
                WHERE platform IS NOT NULL
                ORDER BY platform
                """
            ).fetchall()
        ]

        print(f"\n[inspect] Platforms: {', '.join(platforms)}")

        columns = [row[0] for row in connection.execute(
            "DESCRIBE market_snapshots"
        ).fetchall()]

        preferred = [
            "platform",
            "market_id",
            "title",
            "category",
            "status",
            "snapshot_time",
            "close_time",
            "yes_price",
            "no_price",
            "volume",
            "liquidity",
            "raw_url",
        ]
        selected = [column for column in preferred if column in columns]

        for platform in platforms:
            print(f"\n[inspect] Latest sample for {platform}")
            query = f"""
                SELECT {", ".join(selected)}
                FROM market_snapshots
                WHERE platform = ?
                ORDER BY snapshot_time DESC
                LIMIT 3
            """
            frame = connection.execute(query, [platform]).fetchdf()
            print(frame.to_string(index=False))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
