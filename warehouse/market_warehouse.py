from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(
    os.getenv(
        "DB_PATH",
        str(ROOT / "data" / "warehouse.duckdb"),
    )
)
REFRESH_STATUS_DB_PATH = Path(
    os.getenv(
        "REFRESH_STATUS_DB_PATH",
        "/var/data/refresh_status.duckdb",
    )
)

COLUMNS = [
    "platform",
    "market_id",
    "title",
    "category",
    "start_date",
    "close_date",
    "resolution_date",
    "status",
    "outcome",
    "resolution_source",
    "raw_url",
    "volume",
    "liquidity",
    "yes_price",
    "no_price",
    "source",
    "ingested_at",
    "snapshot_time",
    "close_time",
]


def connect(
    *,
    read_only: bool = False,
) -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(
        str(DB_PATH),
        read_only=read_only,
    )
    connection.execute("SET threads = 1")
    return connection


def status_connect() -> duckdb.DuckDBPyConnection:
    REFRESH_STATUS_DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    connection = duckdb.connect(
        str(REFRESH_STATUS_DB_PATH),
    )
    connection.execute("SET threads = 1")
    return connection


def initialize() -> None:
    connection = connect()
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_snapshots (
                platform VARCHAR,
                market_id VARCHAR,
                title VARCHAR,
                category VARCHAR,
                start_date TIMESTAMP WITH TIME ZONE,
                close_date VARCHAR,
                resolution_date TIMESTAMP WITH TIME ZONE,
                status VARCHAR,
                outcome VARCHAR,
                resolution_source VARCHAR,
                raw_url VARCHAR,
                volume DOUBLE,
                liquidity DOUBLE,
                yes_price DOUBLE,
                no_price DOUBLE,
                source VARCHAR,
                ingested_at TIMESTAMP WITH TIME ZONE,
                snapshot_time TIMESTAMP WITH TIME ZONE,
                close_time VARCHAR
            )
            """
        )
    finally:
        connection.close()

    initialize_status_database()


def initialize_status_database() -> None:
    connection = status_connect()
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dataset_refresh_runs (
                run_id VARCHAR PRIMARY KEY,
                refresh_type VARCHAR,
                started_at TIMESTAMP WITH TIME ZONE,
                completed_at TIMESTAMP WITH TIME ZONE,
                status VARCHAR,
                snapshot_file VARCHAR,
                snapshot_rows BIGINT,
                total_rows BIGINT,
                latest_snapshot TIMESTAMP WITH TIME ZONE,
                error_message VARCHAR
            )
            """
        )
    finally:
        connection.close()


def _prepare_snapshot(
    csv_path: str | Path,
) -> pd.DataFrame:
    frame = pd.read_csv(csv_path)

    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None

    frame = frame[COLUMNS].copy()

    text_columns = [
        "platform",
        "market_id",
        "title",
        "category",
        "close_date",
        "status",
        "outcome",
        "resolution_source",
        "raw_url",
        "source",
        "close_time",
    ]
    for column in text_columns:
        frame[column] = (
            frame[column]
            .astype("string")
            .replace(
                {
                    "nan": None,
                    "None": None,
                    "unknown": None,
                    "": None,
                }
            )
        )

    for column in [
        "start_date",
        "resolution_date",
        "ingested_at",
        "snapshot_time",
    ]:
        frame[column] = pd.to_datetime(
            frame[column],
            errors="coerce",
            utc=True,
        )

    for column in [
        "volume",
        "liquidity",
        "yes_price",
        "no_price",
    ]:
        frame[column] = pd.to_numeric(
            frame[column],
            errors="coerce",
        )

    frame = frame.dropna(
        subset=["platform", "market_id", "snapshot_time"],
    )
    frame = frame.drop_duplicates(
        subset=["platform", "market_id", "snapshot_time"],
        keep="last",
    )

    return frame


def append_snapshot(
    csv_path: str | Path,
) -> dict[str, Any]:
    initialize()
    frame = _prepare_snapshot(csv_path)

    if frame.empty:
        raise ValueError(
            "The prepared snapshot contains no appendable rows."
        )

    snapshot_times = list(
        frame["snapshot_time"].dropna().unique()
    )
    if len(snapshot_times) != 1:
        raise ValueError(
            "A snapshot append must contain exactly one "
            f"snapshot_time; found {len(snapshot_times)}."
        )

    snapshot_time = pd.Timestamp(
        snapshot_times[0]
    ).to_pydatetime()

    # Exclusive lock is held only for the transaction and checkpoint.
    connection = connect()
    try:
        connection.register("snapshot_df", frame)
        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            """
            DELETE FROM market_snapshots
            WHERE snapshot_time = ?
            """,
            [snapshot_time],
        )
        connection.execute(
            """
            INSERT INTO market_snapshots (
                platform,
                market_id,
                title,
                category,
                start_date,
                close_date,
                resolution_date,
                status,
                outcome,
                resolution_source,
                raw_url,
                volume,
                liquidity,
                yes_price,
                no_price,
                source,
                ingested_at,
                snapshot_time,
                close_time
            )
            SELECT
                platform,
                market_id,
                title,
                category,
                start_date,
                close_date,
                resolution_date,
                status,
                outcome,
                resolution_source,
                raw_url,
                volume,
                liquidity,
                yes_price,
                no_price,
                source,
                ingested_at,
                snapshot_time,
                close_time
            FROM snapshot_df
            """
        )
        connection.execute("COMMIT")
        connection.execute("CHECKPOINT")
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        connection.close()

    # The expensive total count is read-only, so dashboard readers can
    # operate concurrently after the short append transaction is complete.
    read_connection = connect(read_only=True)
    try:
        total_rows = int(
            read_connection.execute(
                "SELECT COUNT(*) FROM market_snapshots"
            ).fetchone()[0]
        )
    finally:
        read_connection.close()

    return {
        "snapshot_rows": len(frame),
        "total_rows": total_rows,
        "snapshot_time": snapshot_time,
    }


def start_refresh_run(
    *,
    run_id: str,
    refresh_type: str,
    started_at: datetime,
) -> None:
    initialize_status_database()
    connection = status_connect()
    try:
        connection.execute(
            """
            INSERT OR REPLACE INTO dataset_refresh_runs (
                run_id,
                refresh_type,
                started_at,
                completed_at,
                status,
                snapshot_file,
                snapshot_rows,
                total_rows,
                latest_snapshot,
                error_message
            )
            VALUES (?, ?, ?, NULL, 'running', NULL, NULL, NULL, NULL, NULL)
            """,
            [run_id, refresh_type, started_at],
        )
    finally:
        connection.close()


def finish_refresh_run(
    *,
    run_id: str,
    status: str,
    completed_at: datetime,
    snapshot_file: Optional[str],
    snapshot_rows: Optional[int],
    total_rows: Optional[int],
    latest_snapshot: Any,
    error_message: Optional[str],
) -> None:
    initialize_status_database()
    connection = status_connect()
    try:
        connection.execute(
            """
            UPDATE dataset_refresh_runs
            SET
                completed_at = ?,
                status = ?,
                snapshot_file = ?,
                snapshot_rows = ?,
                total_rows = ?,
                latest_snapshot = ?,
                error_message = ?
            WHERE run_id = ?
            """,
            [
                completed_at,
                status,
                snapshot_file,
                snapshot_rows,
                total_rows,
                latest_snapshot,
                error_message,
                run_id,
            ],
        )
    finally:
        connection.close()


def mark_abandoned_refresh_runs() -> int:
    initialize_status_database()
    connection = status_connect()
    try:
        count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM dataset_refresh_runs
                WHERE status = 'running'
                """
            ).fetchone()[0]
        )

        if count:
            connection.execute(
                """
                UPDATE dataset_refresh_runs
                SET
                    completed_at = CURRENT_TIMESTAMP,
                    status = 'interrupted',
                    error_message = COALESCE(
                        error_message,
                        'Refresh process ended before completion.'
                    )
                WHERE status = 'running'
                """
            )

        return count
    finally:
        connection.close()


def warehouse_stats() -> dict[str, Any]:
    initialize()
    connection = connect(read_only=True)
    try:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                COUNT(DISTINCT snapshot_time) AS snapshots,
                COUNT(DISTINCT platform || ':' || market_id)
                    AS unique_markets,
                MAX(snapshot_time) AS latest_snapshot
            FROM market_snapshots
            """
        ).fetchone()
        return {
            "total_rows": int(row[0] or 0),
            "snapshots": int(row[1] or 0),
            "unique_markets": int(row[2] or 0),
            "latest_snapshot": row[3],
        }
    finally:
        connection.close()
