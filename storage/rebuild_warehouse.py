from pathlib import Path
import glob
from storage.market_warehouse import initialize, append_snapshot

initialize()

files = sorted(glob.glob("data/snapshots/*.csv"))
files += sorted(glob.glob("warehouse/legacy_mvp_snapshots/*.csv"))

print(f"Rebuilding from {len(files)} snapshot files...")

for f in files:
    try:
        append_snapshot(f)
        print("loaded", f)
    except Exception as e:
        print("skipped", f, e)

print("Done.")