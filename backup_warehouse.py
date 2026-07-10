import os
import shutil
import time
from datetime import datetime
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "/var/data/warehouse.duckdb")
BACKUP_DIR = os.getenv("BACKUP_DIR", "/var/data/backups")
BACKUP_INTERVAL_SECONDS = int(os.getenv("BACKUP_INTERVAL_SECONDS", "86400"))  # 24 hours


def backup_once():
    db_path = Path(DB_PATH)
    backup_dir = Path(BACKUP_DIR)

    if not db_path.exists():
        print(f"[backup] Database not found: {db_path}", flush=True)
        return

    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    timestamped_backup = backup_dir / f"warehouse_backup_{timestamp}.duckdb"
    latest_backup = backup_dir / "warehouse_backup_latest.duckdb"

    tmp_backup = backup_dir / f".warehouse_backup_{timestamp}.tmp"

    print(f"[backup] Starting backup: {db_path} -> {timestamped_backup}", flush=True)

    shutil.copy2(db_path, tmp_backup)
    tmp_backup.rename(timestamped_backup)

    shutil.copy2(timestamped_backup, latest_backup)

    print(f"[backup] Backup complete: {timestamped_backup}", flush=True)
    print(f"[backup] Latest backup updated: {latest_backup}", flush=True)


def main():
    print("[backup] Backup worker started", flush=True)

    while True:
        try:
            backup_once()
        except Exception as e:
            print(f"[backup] Backup failed: {e}", flush=True)

        time.sleep(BACKUP_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()