from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys
import time
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from warehouse.market_warehouse import (
    finish_refresh_run,
    mark_abandoned_refresh_runs,
    start_refresh_run,
    warehouse_stats,
)

ROOT = Path(__file__).resolve().parent

SNAPSHOT_PIPELINE = [
    "ingestion.stage39_live_market_data",
    "ingestion.stage41_snapshot_validator",
    "ingestion.stage47_schema_enforcer",
    "warehouse.append_latest_snapshot",
]

SNAPSHOT_INTERVAL_SECONDS = max(
    int(os.getenv("DATASET_REFRESH_SECONDS", "300")),
    60,
)
SEMANTIC_INTERVAL_SECONDS = max(
    int(os.getenv("SEMANTIC_REFRESH_SECONDS", "3600")),
    300,
)
STARTUP_DELAY_SECONDS = max(
    int(os.getenv("DATASET_REFRESH_STARTUP_DELAY_SECONDS", "5")),
    0,
)
RUN_SEMANTICS_ON_START = (
    os.getenv("RUN_SEMANTICS_ON_START", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)

LOCK_PATH = Path(
    os.getenv(
        "DATASET_SCHEDULER_LOCK_PATH",
        "/var/data/dataset_scheduler.lock",
    )
)

REQUIRED_SNAPSHOT_COLUMNS = {
    "platform",
    "market_id",
    "title",
    "yes_price",
    "no_price",
    "volume",
    "liquidity",
    "snapshot_time",
}


WAREHOUSE_ACCESS_LOCK = threading.Lock()
_SEMANTIC_THREAD: Optional[threading.Thread] = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def log(message: str) -> None:
    print(
        f"{utc_now().isoformat()} | {message}",
        flush=True,
    )


_LOCK_HANDLE = None


def acquire_scheduler_lock() -> None:
    """
    Hold an operating-system lock for the scheduler lifetime.

    The file itself may persist on the Render disk. The lock is attached to
    the open file descriptor and is automatically released when the process
    exits, so a PID left by an earlier container cannot block a deployment.
    """
    global _LOCK_HANDLE

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+", encoding="utf-8")

    try:
        fcntl.flock(
            handle.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
    except BlockingIOError as exc:
        handle.seek(0)
        owner = handle.read().strip() or "unknown"
        handle.close()
        raise RuntimeError(
            "Another dataset scheduler currently owns the lock "
            f"(recorded owner: {owner})."
        ) from exc

    handle.seek(0)
    handle.truncate()
    handle.write(
        f"pid={os.getpid()} started={utc_now().isoformat()}\n"
    )
    handle.flush()

    _LOCK_HANDLE = handle
    log(f"Scheduler lock acquired: {LOCK_PATH}")


def release_scheduler_lock() -> None:
    global _LOCK_HANDLE

    if _LOCK_HANDLE is None:
        return

    try:
        fcntl.flock(
            _LOCK_HANDLE.fileno(),
            fcntl.LOCK_UN,
        )
    finally:
        _LOCK_HANDLE.close()
        _LOCK_HANDLE = None


def run_command(
    command: list[str],
    *,
    label: str,
) -> None:
    log(f"Running {label}: {' '.join(command)}")
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT),
        },
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed with exit code {result.returncode}."
        )


def latest_snapshot_file() -> Path:
    snapshot_dir = ROOT / "data" / "snapshots"
    files = sorted(
        snapshot_dir.glob("markets_*.csv"),
        key=lambda path: path.stat().st_mtime,
    )
    if not files:
        raise FileNotFoundError(
            f"No markets_*.csv snapshot found in {snapshot_dir}."
        )
    return files[-1]


def validate_snapshot(snapshot_path: Path) -> int:
    frame = pd.read_csv(snapshot_path)

    if frame.empty:
        raise ValueError("The generated market snapshot is empty.")

    missing = sorted(
        REQUIRED_SNAPSHOT_COLUMNS.difference(frame.columns)
    )
    if missing:
        raise ValueError(
            "Snapshot is missing required columns: "
            + ", ".join(missing)
        )

    null_identity_rows = int(
        frame[["platform", "market_id"]].isna().any(axis=1).sum()
    )
    if null_identity_rows:
        raise ValueError(
            f"Snapshot contains {null_identity_rows:,} rows "
            "without a platform or market_id."
        )

    duplicates = int(
        frame.duplicated(
            subset=["platform", "market_id"],
            keep=False,
        ).sum()
    )
    if duplicates:
        raise ValueError(
            f"Snapshot contains {duplicates:,} duplicate "
            "platform/market_id rows."
        )

    for price_column in ("yes_price", "no_price"):
        numeric = pd.to_numeric(
            frame[price_column],
            errors="coerce",
        )
        invalid = int(((numeric < 0) | (numeric > 1)).sum())
        if invalid:
            raise ValueError(
                f"Snapshot contains {invalid:,} invalid "
                f"{price_column} values."
            )

    snapshot_count = frame["snapshot_time"].nunique(dropna=True)
    if snapshot_count != 1:
        raise ValueError(
            "Every row in one snapshot must have the same "
            f"snapshot_time; found {snapshot_count} values."
        )

    return len(frame)


def run_snapshot_cycle() -> bool:
    run_id = (
        "snapshot_"
        + utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    )
    started = utc_now()
    start_refresh_run(
        run_id=run_id,
        refresh_type="snapshot",
        started_at=started,
    )

    try:
        for module_name in SNAPSHOT_PIPELINE[:-1]:
            run_command(
                [sys.executable, "-m", module_name],
                label=module_name,
            )

        snapshot_path = latest_snapshot_file()
        snapshot_rows = validate_snapshot(snapshot_path)

        with WAREHOUSE_ACCESS_LOCK:
            run_command(
                [sys.executable, "-m", SNAPSHOT_PIPELINE[-1]],
                label=SNAPSHOT_PIPELINE[-1],
            )
            stats = warehouse_stats()
        finish_refresh_run(
            run_id=run_id,
            status="complete",
            completed_at=utc_now(),
            snapshot_file=str(snapshot_path),
            snapshot_rows=snapshot_rows,
            total_rows=stats["total_rows"],
            latest_snapshot=stats["latest_snapshot"],
            error_message=None,
        )
        elapsed = (utc_now() - started).total_seconds()
        log(
            "Snapshot refresh complete: "
            f"{snapshot_rows:,} current rows; "
            f"{stats['total_rows']:,} warehouse rows; "
            f"latest={stats['latest_snapshot']}; "
            f"elapsed={elapsed:.1f}s"
        )
        return True
    except Exception as exc:
        finish_refresh_run(
            run_id=run_id,
            status="failed",
            completed_at=utc_now(),
            snapshot_file=None,
            snapshot_rows=None,
            total_rows=None,
            latest_snapshot=None,
            error_message=str(exc),
        )
        log(f"Snapshot refresh failed: {exc}")
        traceback.print_exc()
        return False


def run_semantic_cycle() -> bool:
    run_id = (
        "semantics_"
        + utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    )
    started = utc_now()
    start_refresh_run(
        run_id=run_id,
        refresh_type="semantics",
        started_at=started,
    )

    try:
        run_command(
            [sys.executable, "collect_kalshi_normalized.py"],
            label="normalized Kalshi refresh",
        )

        with WAREHOUSE_ACCESS_LOCK:
            run_command(
                [sys.executable, "build_semantics_separate_db.py"],
                label="semantic database rebuild",
            )
            stats = warehouse_stats()
        finish_refresh_run(
            run_id=run_id,
            status="complete",
            completed_at=utc_now(),
            snapshot_file=None,
            snapshot_rows=None,
            total_rows=stats["total_rows"],
            latest_snapshot=stats["latest_snapshot"],
            error_message=None,
        )
        elapsed = (utc_now() - started).total_seconds()
        log(
            "Semantic refresh complete: "
            f"elapsed={elapsed:.1f}s"
        )
        return True
    except Exception as exc:
        finish_refresh_run(
            run_id=run_id,
            status="failed",
            completed_at=utc_now(),
            snapshot_file=None,
            snapshot_rows=None,
            total_rows=None,
            latest_snapshot=None,
            error_message=str(exc),
        )
        log(f"Semantic refresh failed: {exc}")
        traceback.print_exc()
        return False



def semantic_worker_running() -> bool:
    return (
        _SEMANTIC_THREAD is not None
        and _SEMANTIC_THREAD.is_alive()
    )


def start_semantic_worker() -> bool:
    global _SEMANTIC_THREAD

    if semantic_worker_running():
        log(
            "Semantic refresh is already running; "
            "the new request was skipped."
        )
        return False

    _SEMANTIC_THREAD = threading.Thread(
        target=run_semantic_cycle,
        name="semantic-refresh",
        daemon=True,
    )
    _SEMANTIC_THREAD.start()
    log("Semantic refresh started in the background.")
    return True



def main(
    *,
    once: bool = False,
    include_semantics: bool = False,
) -> int:
    acquire_scheduler_lock()

    try:
        recovered = mark_abandoned_refresh_runs()
        if recovered:
            log(
                f"Marked {recovered} abandoned refresh run(s) "
                "as interrupted."
            )

        if STARTUP_DELAY_SECONDS and not once:
            log(
                "Waiting "
                f"{STARTUP_DELAY_SECONDS}s before first refresh."
            )
            time.sleep(STARTUP_DELAY_SECONDS)

        next_semantic_at = (
            0.0
            if RUN_SEMANTICS_ON_START
            else time.monotonic() + SEMANTIC_INTERVAL_SECONDS
        )

        while True:
            cycle_started = time.monotonic()
            snapshot_succeeded = run_snapshot_cycle()

            if once:
                if snapshot_succeeded and include_semantics:
                    semantic_succeeded = run_semantic_cycle()
                    return 0 if semantic_succeeded else 1
                return 0 if snapshot_succeeded else 1

            semantic_due = time.monotonic() >= next_semantic_at
            if snapshot_succeeded and semantic_due:
                if start_semantic_worker():
                    next_semantic_at = (
                        time.monotonic()
                        + SEMANTIC_INTERVAL_SECONDS
                    )

            elapsed = time.monotonic() - cycle_started
            sleep_seconds = max(
                SNAPSHOT_INTERVAL_SECONDS - elapsed,
                5.0,
            )
            log(
                "Next live dataset refresh in "
                f"{sleep_seconds:.0f}s."
            )
            time.sleep(sleep_seconds)
    finally:
        release_scheduler_lock()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Continuously refresh the prediction-market warehouse "
            "and semantic matcher."
        )
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one live snapshot refresh and exit.",
    )
    parser.add_argument(
        "--include-semantics",
        action="store_true",
        help=(
            "With --once, also refresh normalized Kalshi data "
            "and rebuild the semantic database."
        ),
    )
    arguments = parser.parse_args()
    raise SystemExit(
        main(
            once=arguments.once,
            include_semantics=arguments.include_semantics,
        )
    )
