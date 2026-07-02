import os
from typing import Optional

import duckdb
from fastapi import FastAPI, HTTPException, Query

DB_PATH = os.getenv("DB_PATH", "/var/data/warehouse.duckdb")

app = FastAPI(
    title="Prediction Market Dataset API",
    version="1.0.0",
)


def query_db(sql: str, params=None):
    conn = duckdb.connect(DB_PATH, read_only=True)
    try:
        if params is None:
            return conn.execute(sql).fetchdf().to_dict(orient="records")
        return conn.execute(sql, params).fetchdf().to_dict(orient="records")
    finally:
        conn.close()


@app.get("/v1/health")
def health():
    rows = query_db("""
        SELECT
            COUNT(*) AS total_rows,
            COUNT(DISTINCT market_id) AS unique_markets,
            COUNT(DISTINCT snapshot_time) AS snapshots,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
    """)
    return rows[0]


@app.get("/v1/latest")
def latest(
    platform: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
):
    where = ""
    params = [limit]

    if platform:
        where = "AND platform = ?"
        params = [platform, limit]

    return query_db(f"""
        WITH latest AS (
            SELECT *
            FROM market_snapshots
            WHERE snapshot_time = (
                SELECT MAX(snapshot_time)
                FROM market_snapshots
            )
        )
        SELECT *
        FROM latest
        WHERE 1=1
        {where}
        ORDER BY volume DESC NULLS LAST
        LIMIT ?
    """, params)


@app.get("/v1/platforms")
def platforms():
    return query_db("""
        SELECT
            platform,
            COUNT(*) AS rows,
            COUNT(DISTINCT market_id) AS unique_markets,
            AVG(volume) AS avg_volume,
            AVG(liquidity) AS avg_liquidity,
            MIN(snapshot_time) AS first_snapshot,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
        GROUP BY platform
        ORDER BY rows DESC
    """)


@app.get("/v1/markets")
def markets(
    q: Optional[str] = None,
    platform: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
):
    filters = []
    params = []

    if q:
        filters.append("LOWER(title) LIKE ?")
        params.append(f"%{q.lower()}%")

    if platform:
        filters.append("platform = ?")
        params.append(platform)

    where = "WHERE " + " AND ".join(filters) if filters else ""

    params.append(limit)

    return query_db(f"""
        SELECT
            platform,
            market_id,
            title,
            yes_price,
            no_price,
            volume,
            liquidity,
            status,
            snapshot_time,
            raw_url
        FROM market_snapshots
        {where}
        ORDER BY snapshot_time DESC
        LIMIT ?
    """, params)


@app.get("/v1/market/{market_id}")
def market_detail(market_id: str):
    rows = query_db("""
        SELECT *
        FROM market_snapshots
        WHERE market_id = ?
        ORDER BY snapshot_time
    """, [market_id])

    if not rows:
        raise HTTPException(status_code=404, detail="Market not found")

    return rows


@app.get("/v1/movers")
def movers(limit: int = Query(100, ge=1, le=500)):
    return query_db("""
        WITH market_changes AS (
            SELECT
                platform,
                market_id,
                title,
                COUNT(*) AS snapshots,
                FIRST(yes_price ORDER BY snapshot_time) AS first_price,
                LAST(yes_price ORDER BY snapshot_time) AS last_price,
                LAST(yes_price ORDER BY snapshot_time)
                    - FIRST(yes_price ORDER BY snapshot_time) AS price_change,
                LAST(volume ORDER BY snapshot_time)
                    - FIRST(volume ORDER BY snapshot_time) AS volume_change,
                LAST(liquidity ORDER BY snapshot_time)
                    - FIRST(liquidity ORDER BY snapshot_time) AS liquidity_change
            FROM market_snapshots
            WHERE yes_price IS NOT NULL
            GROUP BY platform, market_id, title
            HAVING COUNT(*) >= 3
        )
        SELECT *
        FROM market_changes
        ORDER BY ABS(price_change) DESC NULLS LAST
        LIMIT ?
    """, [limit])