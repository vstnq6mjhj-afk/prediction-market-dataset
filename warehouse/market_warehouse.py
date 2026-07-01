from pathlib import Path
import duckdb
import pandas as pd
import os

ROOT = Path(__file__).resolve().parents[1]

DB_PATH = Path(os.getenv(
    "DB_PATH",
    str(ROOT / "data" / "warehouse.duckdb")
))


def initialize():

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(DB_PATH))

    conn.execute("""
INSERT INTO market_snapshots (
    platform,
    market_id,
    title,
    canonical_title,
    category,
    start_date,
    close_date,
    resolution_date,
    status,
    outcome,
    resolution_source,
    raw_url,
    volume,
    liquidity,
    yes_price,
    no_price,
    source,
    ingested_at,
    snapshot_time
)
SELECT
    platform,
    market_id,
    title,
    canonical_title,
    category,
    start_date,
    close_date,
    resolution_date,
    status,
    outcome,
    resolution_source,
    raw_url,
    volume,
    liquidity,
    yes_price,
    no_price,
    source,
    ingested_at,
    snapshot_time
FROM snapshot_df
""")

    conn.close()


def append_snapshot(csv_path):

    conn = duckdb.connect(str(DB_PATH))

    df = pd.read_csv(csv_path)

    conn.register("snapshot_df", df)

    conn.execute("""
    INSERT INTO market_snapshots
    SELECT * FROM snapshot_df
    """)

    conn.close()