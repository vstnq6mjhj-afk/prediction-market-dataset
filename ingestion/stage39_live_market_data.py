from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

import sys
sys.path.insert(0, str(ROOT))

from connectors.market_aggregator import aggregate_markets

SNAPSHOT_DIR = ROOT / "data" / "snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    print("Stage 39 live market data started.")

    rows = aggregate_markets()

    if not rows:
        raise ValueError("No live markets returned from aggregator.")

    df = pd.DataFrame(rows)

    snapshot_time = datetime.now(timezone.utc).isoformat()
    df["snapshot_time"] = snapshot_time

    if "ingested_at" not in df.columns:
        df["ingested_at"] = snapshot_time

    expected_columns = [
        "platform",
        "market_id",
        "title",
        "category",
        "start_date",
        "close_date",
        "resolution_date",
        "status",
        "outcome",
        "resolution_source",
        "raw_url",
        "volume",
        "liquidity",
        "yes_price",
        "no_price",
        "source",
        "ingested_at",
        "snapshot_time",
        "close_time",
    ]

    for col in expected_columns:
        if col not in df.columns:
            df[col] = None

    df = df[expected_columns]

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    output_path = SNAPSHOT_DIR / f"markets_{timestamp}.csv"
    latest_path = SNAPSHOT_DIR / "latest.csv"

    df.to_csv(output_path, index=False)
    df.to_csv(latest_path, index=False)
    from storage.market_warehouse import initialize
    from storage.market_warehouse import append_snapshot

    initialize()
    append_snapshot(output_path)

    print(f"{datetime.now(timezone.utc).isoformat()} | SUCCESS: Saved live market snapshot -> {output_path.name}")
    print(df.head())
    print(f"[{len(df)} rows x {len(df.columns)} columns]")
    print("Stage 39 live market data complete.")


if __name__ == "__main__":
    main()
