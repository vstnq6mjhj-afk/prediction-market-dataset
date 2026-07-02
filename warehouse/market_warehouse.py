from pathlib import Path
import os

import duckdb
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]

DB_PATH = Path(
    os.getenv(
        "DB_PATH",
        str(ROOT / "data" / "warehouse.duckdb"),
    )
)


COLUMNS = [
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


def initialize() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(DB_PATH))

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_snapshots (
            platform VARCHAR,
            market_id VARCHAR,
            title VARCHAR,
            category VARCHAR,
            start_date TIMESTAMP WITH TIME ZONE,
            close_date VARCHAR,
            resolution_date TIMESTAMP WITH TIME ZONE,
            status VARCHAR,
            outcome VARCHAR,
            resolution_source VARCHAR,
            raw_url VARCHAR,
            volume DOUBLE,
            liquidity DOUBLE,
            yes_price DOUBLE,
            no_price DOUBLE,
            source VARCHAR,
            ingested_at TIMESTAMP WITH TIME ZONE,
            snapshot_time TIMESTAMP WITH TIME ZONE,
            close_time VARCHAR
        )
        """
    )

    conn.close()


def _prepare_snapshot(csv_path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)

    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None

    df = df[COLUMNS].copy()

    for col in [
        "platform",
        "market_id",
        "title",
        "category",
        "close_date",
        "status",
        "outcome",
        "resolution_source",
        "raw_url",
        "source",
        "close_time",
    ]:
        df[col] = df[col].astype("string").replace(
            {
                "nan": None,
                "None": None,
                "unknown": None,
                "": None,
            }
        )

    for col in ["start_date", "resolution_date", "ingested_at", "snapshot_time"]:
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

    for col in ["volume", "liquidity", "yes_price", "no_price"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def append_snapshot(csv_path: str | Path) -> None:
    initialize()

    df = _prepare_snapshot(csv_path)

    conn = duckdb.connect(str(DB_PATH))
    conn.register("snapshot_df", df)

    conn.execute(
        """
        INSERT INTO market_snapshots (
            platform,
            market_id,
            title,
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
            snapshot_time,
            close_time
        )
        SELECT
            platform,
            market_id,
            title,
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
            snapshot_time,
            close_time
        FROM snapshot_df
        """
    )

    conn.close()