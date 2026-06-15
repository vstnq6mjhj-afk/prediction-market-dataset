from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import duckdb
import pandas as pd
import math
from pathlib import Path

app = FastAPI(title="Prediction Market Dataset API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = Path("data/warehouse.duckdb")


def clean_value(v):
    if pd.isna(v):
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
    return v


def clean_df(df: pd.DataFrame):
    df = df.astype(object)
    return [
        {k: clean_value(v) for k, v in row.items()}
        for row in df.to_dict(orient="records")
    ]


def query_df(sql: str, params=None):
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return conn.execute(sql, params or []).df()
    finally:
        conn.close()


@app.get("/")
def home():
    return {"status": "ok", "name": "Prediction Market Dataset API"}


@app.get("/stats")
def stats():
    df = query_df("""
        SELECT
            COUNT(*) AS total_rows,
            COUNT(DISTINCT market_id) AS unique_markets,
            COUNT(DISTINCT snapshot_time) AS snapshots,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
    """)
    return clean_df(df)[0]


@app.get("/platforms")
def platforms():
    df = query_df("""
        SELECT
            platform,
            COUNT(*) AS rows,
            COUNT(DISTINCT market_id) AS unique_markets,
            AVG(volume) AS avg_volume,
            AVG(liquidity) AS avg_liquidity,
            MIN(snapshot_time) AS first_snapshot,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
        WHERE platform IS NOT NULL
        GROUP BY platform
        ORDER BY rows DESC
    """)
    return clean_df(df)


@app.get("/markets")
def markets(
    platform: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
):
    where = ["1=1"]
    params = []

    if platform:
        where.append("platform = ?")
        params.append(platform)

    if search:
        where.append("LOWER(title) LIKE ?")
        params.append(f"%{search.lower()}%")

    sql = f"""
        SELECT *
        FROM market_snapshots
        WHERE {' AND '.join(where)}
        ORDER BY snapshot_time DESC
        LIMIT ?
    """

    params.append(limit)

    df = query_df(sql, params)
    return clean_df(df)


@app.get("/market/{market_id}")
def market_detail(market_id: str):
    df = query_df("""
        SELECT *
        FROM market_snapshots
        WHERE market_id = ?
        ORDER BY snapshot_time ASC
    """, [market_id])

    return clean_df(df)


@app.get("/top-volume")
def top_volume(limit: int = Query(default=50, ge=1, le=500)):
    df = query_df("""
        SELECT *
        FROM market_snapshots
        WHERE volume IS NOT NULL
        ORDER BY volume DESC
        LIMIT ?
    """, [limit])

    return clean_df(df)


@app.get("/movers")
def movers(limit: int = Query(default=50, ge=1, le=500)):
    df = query_df("""
        SELECT
            platform,
            market_id,
            title,
            MIN(yes_price) AS low_price,
            MAX(yes_price) AS high_price,
            MAX(yes_price) - MIN(yes_price) AS price_move,
            COUNT(*) AS observations
        FROM market_snapshots
        WHERE yes_price IS NOT NULL
        GROUP BY platform, market_id, title
        HAVING COUNT(*) > 1
        ORDER BY price_move DESC
        LIMIT ?
    """, [limit])

    return clean_df(df)


@app.get("/snapshot-growth")
def snapshot_growth():
    df = query_df("""
        SELECT
            snapshot_time,
            COUNT(*) AS rows,
            COUNT(DISTINCT market_id) AS unique_markets
        FROM market_snapshots
        GROUP BY snapshot_time
        ORDER BY snapshot_time ASC
    """)

    return clean_df(df)


@app.get("/latest-snapshot")
def latest_snapshot(limit: int = Query(default=300, ge=1, le=1000)):
    df = query_df("""
        SELECT *
        FROM market_snapshots
        WHERE snapshot_time = (
            SELECT MAX(snapshot_time)
            FROM market_snapshots
        )
        LIMIT ?
    """, [limit])

    return clean_df(df)