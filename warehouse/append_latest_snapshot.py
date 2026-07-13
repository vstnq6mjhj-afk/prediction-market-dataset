from __future__ import annotations

from pathlib import Path

from warehouse.market_warehouse import (
    DB_PATH,
    append_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = ROOT / "data" / "snapshots"


def main() -> None:
    files = sorted(
        SNAPSHOT_DIR.glob("markets_*.csv"),
        key=lambda path: path.stat().st_mtime,
    )
    if not files:
        raise FileNotFoundError(
            f"No markets_*.csv found in {SNAPSHOT_DIR}"
        )

    latest = files[-1]
    print(f"Appending latest snapshot: {latest}", flush=True)
    print(f"Warehouse database: {DB_PATH}", flush=True)

    result = append_snapshot(latest)

    print(
        "Append complete: "
        f"snapshot_rows={result['snapshot_rows']:,}, "
        f"total_rows={result['total_rows']:,}, "
        f"snapshot_time={result['snapshot_time']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
