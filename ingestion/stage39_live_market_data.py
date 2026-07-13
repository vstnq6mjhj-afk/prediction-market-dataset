from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from connectors.market_aggregator import aggregate_markets

SNAPSHOT_DIR = Path(
    os.getenv(
        "SNAPSHOT_DIR",
        str(ROOT / "data" / "snapshots"),
    )
)

EXPECTED_COLUMNS = [
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


def write_csv_atomically(
    frame: pd.DataFrame,
    destination: Path,
) -> None:
    temporary = destination.with_suffix(
        destination.suffix + ".tmp"
    )
    frame.to_csv(temporary, index=False)
    temporary.replace(destination)


def main() -> None:
    print("Stage 39 live market data started.", flush=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    rows = aggregate_markets()
    if not rows:
        raise ValueError(
            "No live markets returned from the aggregator."
        )

    frame = pd.DataFrame(rows)
    snapshot_time = datetime.now(timezone.utc).isoformat()

    frame["snapshot_time"] = snapshot_time
    if "ingested_at" not in frame.columns:
        frame["ingested_at"] = snapshot_time

    for column in EXPECTED_COLUMNS:
        if column not in frame.columns:
            frame[column] = None

    frame = frame[EXPECTED_COLUMNS].copy()
    frame = frame.dropna(
        subset=["platform", "market_id"],
    )
    frame = frame.drop_duplicates(
        subset=["platform", "market_id"],
        keep="last",
    )

    if frame.empty:
        raise ValueError(
            "No valid market rows remained after normalization."
        )

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H-%M-%SZ"
    )
    output_path = SNAPSHOT_DIR / f"markets_{timestamp}.csv"
    latest_path = SNAPSHOT_DIR / "latest.csv"

    write_csv_atomically(frame, output_path)
    write_csv_atomically(frame, latest_path)

    print(
        f"{datetime.now(timezone.utc).isoformat()} | "
        "SUCCESS: Saved live market snapshot -> "
        f"{output_path.name}",
        flush=True,
    )
    print(
        f"[{len(frame):,} rows x {len(frame.columns)} columns]",
        flush=True,
    )
    print(
        "Stage 39 live market data complete. "
        "The warehouse append runs after validation.",
        flush=True,
    )


if __name__ == "__main__":
    main()
