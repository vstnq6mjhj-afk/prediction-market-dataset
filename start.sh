#!/usr/bin/env bash

python - <<'PY'
from pathlib import Path
import glob
from warehouse.market_warehouse import initialize, append_snapshot, DB_PATH

initialize()

if not Path(DB_PATH).exists() or Path(DB_PATH).stat().st_size < 10_000_000:
    for csv_path in sorted(glob.glob("warehouse/legacy_mvp_snapshots/*.csv")):
        append_snapshot(csv_path)

print("Warehouse ready:", DB_PATH)
PY

streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port $PORT