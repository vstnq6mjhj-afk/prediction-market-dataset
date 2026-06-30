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
    CREATE TABLE IF NOT EXISTS market_snapshots (
        platform VARCHAR,
        market_id VARCHAR,
        title VARCHAR,
        canonical_title VARCHAR,
        category VARCHAR,
        start_date VARCHAR,
        close_date VARCHAR,
        resolution_date VARCHAR,
        status VARCHAR,
        outcome VARCHAR,
        resolution_source VARCHAR,
        raw_url VARCHAR,
        volume DOUBLE,
        liquidity DOUBLE,
        yes_price DOUBLE,
        no_price DOUBLE,
        source VARCHAR,
        ingested_at VARCHAR,
        snapshot_time VARCHAR
    )
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