from __future__ import annotations

import os
import sqlite3
from pathlib import Path

STATUS_DB = Path(
    os.getenv(
        "REFRESH_STATUS_DB_PATH",
        "/var/data/refresh_status.sqlite3",
    )
)


def main() -> None:
    if not STATUS_DB.exists():
        raise SystemExit(f"Status database not found: {STATUS_DB}")

    connection = sqlite3.connect(str(STATUS_DB), timeout=30.0)
    try:
        connection.execute("PRAGMA busy_timeout=30000")

        print("LATEST DISCOVERY RUNS")
        rows = connection.execute(
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
            WHERE refresh_type = 'discovery'
            ORDER BY started_at DESC
            LIMIT 10
            """
        ).fetchall()

        if not rows:
            print("No discovery runs found.")
            return

        for row in rows:
            print(row)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
