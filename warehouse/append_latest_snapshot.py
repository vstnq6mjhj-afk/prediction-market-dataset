from pathlib import Path
from warehouse.market_warehouse import append_snapshot

ROOT = Path(__file__).resolve().parents[1]

SNAPSHOT_DIR = ROOT / "data" / "snapshots"

files = sorted(
    SNAPSHOT_DIR.glob("markets_*.csv"),
    key=lambda p: p.stat().st_mtime
)

if not files:
    raise FileNotFoundError(f"No markets_*.csv found in {SNAPSHOT_DIR}")

latest = files[-1]

print(f"Appending latest snapshot: {latest}")

append_snapshot(latest)

print("Append complete")