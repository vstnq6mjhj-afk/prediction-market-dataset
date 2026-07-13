from __future__ import annotations

import os

import duckdb

DB_PATH = os.getenv(
    "DB_PATH",
    "/var/data/warehouse.duckdb",
)


def main() -> None:
    connection = duckdb.connect(DB_PATH, read_only=True)

    print("\nWAREHOUSE SUMMARY")
    print(
        connection.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                COUNT(DISTINCT snapshot_time) AS snapshots,
                COUNT(DISTINCT platform || ':' || market_id)
                    AS unique_markets,
                MIN(snapshot_time) AS first_snapshot,
                MAX(snapshot_time) AS latest_snapshot
            FROM market_snapshots
            """
        ).fetchall()
    )

    print("\nLATEST SNAPSHOT BY PLATFORM")
    for row in connection.execute(
        """
        WITH latest AS (
            SELECT MAX(snapshot_time) AS snapshot_time
            FROM market_snapshots
        )
        SELECT
            platform,
            COUNT(*) AS rows,
            COUNT(DISTINCT market_id) AS markets,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
        WHERE snapshot_time = (
            SELECT snapshot_time FROM latest
        )
        GROUP BY platform
        ORDER BY platform
        """
    ).fetchall():
        print(row)

    tables = {
        row[0]
        for row in connection.execute("SHOW TABLES").fetchall()
    }

    if "dataset_refresh_runs" in tables:
        print("\nLATEST REFRESH RUNS")
        for row in connection.execute(
            """
            SELECT
                refresh_type,
                status,
                started_at,
                completed_at,
                snapshot_rows,
                total_rows,
                latest_snapshot,
                error_message
            FROM dataset_refresh_runs
            ORDER BY started_at DESC
            LIMIT 15
            """
        ).fetchall():
            print(row)

    connection.close()


if __name__ == "__main__":
    main()
