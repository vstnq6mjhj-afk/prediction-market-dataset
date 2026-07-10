"""
Manual DuckDB warehouse backup script.

Use this only when you intentionally want to create a backup.
Do NOT run this as a background worker inside the live Streamlit service.

Default paths:
- Source DB: /var/data/warehouse.duckdb
- Backup dir: /var/data/backups
"""

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(os.getenv("DB_PATH", "/var/data/warehouse.duckdb"))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/var/data/backups"))


def human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024


def backup_once() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    timestamped_backup = BACKUP_DIR / f"warehouse_backup_{timestamp}.duckdb"
    latest_backup = BACKUP_DIR / "warehouse_backup_latest.duckdb"
    temp_backup = BACKUP_DIR / f".warehouse_backup_{timestamp}.tmp"

    source_size = DB_PATH.stat().st_size

    print(f"[backup] Source: {DB_PATH}", flush=True)
    print(f"[backup] Size: {human_size(source_size)}", flush=True)
    print(f"[backup] Writing temporary backup: {temp_backup}", flush=True)

    shutil.copy2(DB_PATH, temp_backup)

    print(f"[backup] Finalizing timestamped backup: {timestamped_backup}", flush=True)
    temp_backup.rename(timestamped_backup)

    print(f"[backup] Updating latest backup: {latest_backup}", flush=True)
    shutil.copy2(timestamped_backup, latest_backup)

    print("[backup] Complete", flush=True)
    print(f"[backup] Timestamped: {timestamped_backup}", flush=True)
    print(f"[backup] Latest: {latest_backup}", flush=True)


if __name__ == "__main__":
    backup_once()
