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

    connection = sqlite3.connect(STATUS_DB)
    try:
        columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(refresh_runs)"
            ).fetchall()
        ]
        wanted = [
            "refresh_type",
            "status",
            "started_at",
            "completed_at",
            "snapshot_rows",
            "total_rows",
            "latest_snapshot",
            "error_message",
        ]
        selected = [name for name in wanted if name in columns]
        query = (
            f"SELECT {', '.join(selected)} "
            "FROM refresh_runs "
            "WHERE refresh_type = 'discovery' "
            "ORDER BY started_at DESC "
            "LIMIT 10"
        )

        print("LATEST DISCOVERY RUNS")
        print(tuple(selected))
        rows = connection.execute(query).fetchall()
        if not rows:
            print("No discovery runs found.")
        for row in rows:
            print(row)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
