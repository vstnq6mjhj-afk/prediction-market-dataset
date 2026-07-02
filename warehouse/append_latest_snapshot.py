from pathlib import Path
from warehouse.market_warehouse import append_snapshot

ROOT = Path(__file__).resolve().parents[1]

files = sorted((ROOT / "exports").glob("markets_*.csv"))
if not files:
    files = sorted(ROOT.glob("markets_*.csv"))

if not files:
    raise FileNotFoundError("No markets_*.csv found to append")

latest = files[-1]
print("Appending latest snapshot:", latest)
append_snapshot(latest)
print("Append complete")