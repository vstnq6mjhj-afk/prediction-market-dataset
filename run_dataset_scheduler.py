from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from warehouse.market_warehouse import (
    finish_refresh_run,
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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def log(message: str) -> None:
    print(
        f"{utc_now().isoformat()} | {message}",
        flush=True,
    )


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def acquire_scheduler_lock() -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            descriptor = os.open(
                str(LOCK_PATH),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            log(f"Scheduler lock acquired: {LOCK_PATH}")
            return
        except FileExistsError:
            try:
                existing_pid = int(
                    LOCK_PATH.read_text(encoding="utf-8").strip()
                )
            except Exception:
                existing_pid = 0

            if process_is_alive(existing_pid):
                raise RuntimeError(
                    "Another dataset scheduler is already running "
                    f"with PID {existing_pid}."
                )

            log(
                "Removing stale scheduler lock "
                f"for PID {existing_pid or 'unknown'}."
            )
            try:
                LOCK_PATH.unlink()
            except FileNotFoundError:
                pass


def release_scheduler_lock() -> None:
    try:
        stored_pid = int(
            LOCK_PATH.read_text(encoding="utf-8").strip()
        )
    except Exception:
        stored_pid = None

    if stored_pid in (None, os.getpid()):
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass


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


def main(
    *,
    once: bool = False,
    include_semantics: bool = False,
) -> int:
    acquire_scheduler_lock()

    try:
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

            semantic_due = (
                include_semantics
                if once
                else time.monotonic() >= next_semantic_at
            )
            if snapshot_succeeded and semantic_due:
                run_semantic_cycle()
                next_semantic_at = (
                    time.monotonic()
                    + SEMANTIC_INTERVAL_SECONDS
                )

            if once:
                return 0 if snapshot_succeeded else 1

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
