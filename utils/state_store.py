from pathlib import Path
import sys
import sqlite3
import json
from typing import Optional, Dict, Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from utils.config_loader import get_path, ensure_directories
from utils.time_utils import utc_now_iso


ensure_directories()

DB_PATH = get_path("data") / "prediction_market_mvp.db"


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_state_store():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT UNIQUE,
                start_time TEXT,
                end_time TEXT,
                status TEXT,
                stages_run TEXT,
                errors TEXT,
                latest_snapshot TEXT,
                latest_signal TEXT,
                latest_portfolio TEXT,
                latest_risk_report TEXT,
                metadata TEXT
            )
            """
        )
        conn.commit()


def create_run(run_id: str, metadata: Optional[Dict[str, Any]] = None):
    init_state_store()

    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO pipeline_runs (
                run_id,
                start_time,
                status,
                stages_run,
                errors,
                metadata
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                utc_now_iso(),
                "running",
                json.dumps([]),
                json.dumps([]),
                json.dumps(metadata or {}),
            ),
        )
        conn.commit()


def update_run(
    run_id: str,
    status: Optional[str] = None,
    stages_run: Optional[list] = None,
    errors: Optional[list] = None,
    latest_snapshot: Optional[str] = None,
    latest_signal: Optional[str] = None,
    latest_portfolio: Optional[str] = None,
    latest_risk_report: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    init_state_store()

    fields = []
    values = []

    if status is not None:
        fields.append("status = ?")
        values.append(status)

    if stages_run is not None:
        fields.append("stages_run = ?")
        values.append(json.dumps(stages_run))

    if errors is not None:
        fields.append("errors = ?")
        values.append(json.dumps(errors))

    if latest_snapshot is not None:
        fields.append("latest_snapshot = ?")
        values.append(latest_snapshot)

    if latest_signal is not None:
        fields.append("latest_signal = ?")
        values.append(latest_signal)

    if latest_portfolio is not None:
        fields.append("latest_portfolio = ?")
        values.append(latest_portfolio)

    if latest_risk_report is not None:
        fields.append("latest_risk_report = ?")
        values.append(latest_risk_report)

    if metadata is not None:
        fields.append("metadata = ?")
        values.append(json.dumps(metadata))

    if status in {"success", "failed"}:
        fields.append("end_time = ?")
        values.append(utc_now_iso())

    if not fields:
        return

    values.append(run_id)

    with get_connection() as conn:
        conn.execute(
            f"""
            UPDATE pipeline_runs
            SET {", ".join(fields)}
            WHERE run_id = ?
            """,
            values,
        )
        conn.commit()


def get_latest_run():
    init_state_store()

    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM pipeline_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        return None

    return dict(row)


def get_recent_runs(limit: int = 20):
    init_state_store()

    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM pipeline_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]


if __name__ == "__main__":
    init_state_store()
    print(f"State store initialized: {DB_PATH}")