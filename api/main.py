from pathlib import Path
import math
import duckdb
import pandas as pd
import numpy as np
from fastapi import FastAPI, Query

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "warehouse.duckdb"

app = FastAPI(title="Prediction Market Dataset API")


def clean_value(v):
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        v = float(v)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(v, pd.Timestamp):
        return str(v)
    return v


def clean_json(obj):
    if isinstance(obj, pd.DataFrame):
        return clean_json(obj.to_dict(orient="records"))
    if isinstance(obj, list):
        return [clean_json(x) for x in obj]
    if isinstance(obj, dict):
        return {k: clean_json(v) for k, v in obj.items()}
    return clean_value(obj)


def query(sql: str, params=None):
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        df = conn.execute(sql, params or []).df()
    finally:
        conn.close()

    df = df.replace([np.inf, -np.inf], np.nan)
    return clean_json(df)


@app.get("/")
def home():
    return {
        "status": "ok",
        "name": "Prediction Market Dataset API",
    }


@app.get("/stats")
def stats():
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        total_rows = conn.execute("""
            SELECT COUNT(*) FROM market_snapshots
        """).fetchone()[0]

        unique_markets = conn.execute("""
            SELECT COUNT(DISTINCT platform || ':' || market_id)
            FROM market_snapshots
        """).fetchone()[0]

        snapshots = conn.execute("""
            SELECT COUNT(DISTINCT snapshot_time)
            FROM market_snapshots
        """).fetchone()[0]

        latest_snapshot = conn.execute("""
            SELECT MAX(snapshot_time)
            FROM market_snapshots
        """).fetchone()[0]
    finally:
        conn.close()

    return clean_json({
        "total_rows": total_rows,
        "unique_markets": unique_markets,
        "snapshots": snapshots,
        "latest_snapshot": latest_snapshot,
    })


@app.get("/platforms")
def platforms():
    return query("""
        SELECT
            platform,
            COUNT(*) AS rows,
            COUNT(DISTINCT platform || ':' || market_id) AS unique_markets,

            CASE
                WHEN SUM(CASE WHEN volume IS NOT NULL AND volume > 0 THEN 1 ELSE 0 END) = 0
                THEN NULL
                ELSE ROUND(AVG(CASE WHEN volume > 0 THEN volume ELSE NULL END), 4)
            END AS avg_volume,

            CASE
                WHEN SUM(CASE WHEN liquidity IS NOT NULL AND liquidity > 0 THEN 1 ELSE 0 END) = 0
                THEN NULL
                ELSE ROUND(AVG(CASE WHEN liquidity > 0 THEN liquidity ELSE NULL END), 4)
            END AS avg_liquidity,

            MIN(snapshot_time) AS first_snapshot,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
        GROUP BY platform
        ORDER BY rows DESC
    """)


@app.get("/markets")
def markets(
    platform: str | None = None,
    search: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
):
    filters = []
    params = []

    if platform:
        filters.append("platform = ?")
        params.append(platform)

    if search:
        filters.append("LOWER(title) LIKE ?")
        params.append(f"%{search.lower()}%")

    where_clause = ""
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    params.append(limit)

    return query(f"""
        WITH latest AS (
            SELECT *
            FROM market_snapshots
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY platform, market_id
                ORDER BY snapshot_time DESC
            ) = 1
        )
        SELECT
            platform,
            market_id,
            title,
            category,
            status,
            yes_price,
            no_price,
            volume,
            liquidity,
            outcome,
            resolution_source,
            close_time,
            snapshot_time,
            raw_url
        FROM latest
        {where_clause}
        ORDER BY snapshot_time DESC
        LIMIT ?
    """, params)


@app.get("/market/{market_id}")
def market_detail(market_id: str):
    return query("""
        SELECT
            snapshot_time,
            platform,
            market_id,
            title,
            yes_price,
            no_price,
            volume,
            liquidity,
            status,
            outcome,
            resolution_source,
            raw_url,
            ingested_at,
            close_time
        FROM market_snapshots
        WHERE market_id = ?
        ORDER BY snapshot_time
    """, [market_id])


@app.get("/top-volume")
def top_volume(limit: int = Query(50, ge=1, le=500)):
    return query("""
        WITH latest AS (
            SELECT *
            FROM market_snapshots
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY platform, market_id
                ORDER BY snapshot_time DESC
            ) = 1
        )
        SELECT
            platform,
            market_id,
            title,
            yes_price,
            no_price,
            volume,
            liquidity,
            snapshot_time
        FROM latest
        WHERE volume IS NOT NULL
          AND volume > 0
        ORDER BY volume DESC
        LIMIT ?
    """, [limit])


@app.get("/movers")
def movers(limit: int = Query(50, ge=1, le=500)):
    return query("""
        WITH per_market AS (
            SELECT
                platform,
                market_id,
                ANY_VALUE(title) AS title,
                MIN(yes_price) AS low_price,
                MAX(yes_price) AS high_price,
                MAX(yes_price) - MIN(yes_price) AS price_change,
                MIN(volume) AS low_volume,
                MAX(volume) AS high_volume,
                CASE
                    WHEN MIN(volume) IS NULL OR MAX(volume) IS NULL
                    THEN NULL
                    ELSE MAX(volume) - MIN(volume)
                END AS volume_change,
                COUNT(*) AS snapshots
            FROM market_snapshots
            WHERE yes_price IS NOT NULL
            GROUP BY platform, market_id
        )
        SELECT
            platform,
            market_id,
            title,
            low_price,
            high_price,
            ROUND(price_change, 4) AS price_change,
            volume_change,
            snapshots
        FROM per_market
        ORDER BY price_change DESC NULLS LAST
        LIMIT ?
    """, [limit])


@app.get("/snapshot-growth")
def snapshot_growth():
    return query("""
        SELECT
            snapshot_time,
            COUNT(*) AS rows
        FROM market_snapshots
        GROUP BY snapshot_time
        ORDER BY snapshot_time
    """)


@app.get("/latest-snapshot")
def latest_snapshot(limit: int = Query(300, ge=1, le=3000)):
    return query("""
        WITH latest_time AS (
            SELECT MAX(snapshot_time) AS latest_snapshot
            FROM market_snapshots
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
            snapshot_time
        FROM market_snapshots
        WHERE snapshot_time = (
            SELECT latest_snapshot FROM latest_time
        )
        ORDER BY platform, volume DESC NULLS LAST
        LIMIT ?
    """, [limit])