import os
from typing import Optional

import duckdb
import math
import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query

from api.auth import verify_api_key
from api.usage import log_api_request
from api.supabase_client import supabase
from api.keygen import generate_api_key
from api.supabase_client import supabase

DB_PATH = os.getenv("DB_PATH", "/var/data/warehouse.duckdb")

app = FastAPI(
    title="Prediction Market Dataset API",
    version="1.0.0",
)


def query_db(sql: str, params=None):
    conn = duckdb.connect(DB_PATH, read_only=True)
    try:
        if params is None:
            df = conn.execute(sql).fetchdf()
        else:
            df = conn.execute(sql, params).fetchdf()

        records = df.to_dict(orient="records")

        for row in records:
            for key, value in row.items():
                if pd.isna(value):
                    row[key] = None
                elif isinstance(value, float) and math.isinf(value):
                    row[key] = None

        return records
    finally:
        conn.close()


@app.get("/v1/health")
def health(account=Depends(verify_api_key)):
    rows = query_db("""
        SELECT
            COUNT(*) AS total_rows,
            COUNT(DISTINCT market_id) AS unique_markets,
            COUNT(DISTINCT snapshot_time) AS snapshots,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
    """)

    log_api_request(account["api_key"], "/v1/health", 200, 1)
    return rows[0]


@app.get("/v1/latest")
def latest(
    account=Depends(verify_api_key),
    platform: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
):
    where = ""
    params = [limit]

    if platform:
        where = "AND platform = ?"
        params = [platform, limit]

    rows = query_db(f"""
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

    log_api_request(account["api_key"], "/v1/latest", 200, len(rows))
    return rows


@app.get("/v1/platforms")
def platforms(account=Depends(verify_api_key)):
    rows = query_db("""
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

    log_api_request(account["api_key"], "/v1/platforms", 200, len(rows))
    return rows


@app.get("/v1/markets")
def markets(
    account=Depends(verify_api_key),
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

    rows = query_db(f"""
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

    log_api_request(account["api_key"], "/v1/markets", 200, len(rows))
    return rows


@app.get("/v1/market/{market_id}")
def market_detail(
    market_id: str,
    account=Depends(verify_api_key),
):
    rows = query_db("""
        SELECT *
        FROM market_snapshots
        WHERE market_id = ?
        ORDER BY snapshot_time
    """, [market_id])

    if not rows:
        raise HTTPException(status_code=404, detail="Market not found")

    log_api_request(account["api_key"], f"/v1/market/{market_id}", 200, len(rows))
    return rows


@app.get("/v1/movers")
def movers(
    account=Depends(verify_api_key),
    limit: int = Query(100, ge=1, le=500),
):
    rows = query_db("""
        WITH latest AS (
            SELECT MAX(snapshot_time) AS max_time
            FROM market_snapshots
        ),
        recent AS (
            SELECT *
            FROM market_snapshots
            WHERE snapshot_time >= (SELECT max_time FROM latest) - INTERVAL '2 days'
              AND yes_price IS NOT NULL
        ),
        market_changes AS (
            SELECT
                platform,
                market_id,
                title,
                COUNT(*) AS snapshots,
                MIN(yes_price) AS min_price,
                MAX(yes_price) AS max_price,
                MAX(yes_price) - MIN(yes_price) AS price_change,
                MAX(volume) - MIN(volume) AS volume_change,
                MAX(liquidity) - MIN(liquidity) AS liquidity_change
            FROM recent
            GROUP BY platform, market_id, title
            HAVING COUNT(*) >= 2
        )
        SELECT *
        FROM market_changes
        ORDER BY ABS(price_change) DESC NULLS LAST
        LIMIT ?
    """, [limit])

    log_api_request(account["api_key"], "/v1/movers", 200, len(rows))
    return rows

    log_api_request(account["api_key"], "/v1/movers", 200, len(rows))
    return rows

@app.get("/v1/account")
def account(account=Depends(verify_api_key)):
    return {
        "email": account["email"],
        "plan": account["tier"],
        "requests_today": account["requests_today"],
        "daily_limit": account["daily_limit"],
        "remaining": account["remaining"],
        "api_key": account["api_key"][:8] + "..."
    }

@app.post("/v1/api-key/regenerate")
def regenerate_api_key(account=Depends(verify_api_key)):
    new_key = generate_api_key()

    supabase.table("api_keys").update(
        {"api_key": new_key}
    ).eq(
        "api_key", account["api_key"]
    ).execute()

    return {
        "api_key": new_key,
        "message": "API key regenerated successfully"
    }

@app.get("/v1/market/{market_id}")
def market_detail(
    market_id: str,
    account=Depends(verify_api_key),
):
    rows = query_db("""
        SELECT *
        FROM market_snapshots
        WHERE market_id = ?
        ORDER BY snapshot_time DESC
    """, [market_id])

    log_api_request(account["api_key"], "/v1/market", 200, len(rows))
    return rows

@app.get("/v1/search")
def search(
    q: str,
    account=Depends(verify_api_key),
    limit: int = Query(50, ge=1, le=200),
):
    rows = query_db("""
        SELECT *
        FROM market_snapshots
        WHERE LOWER(title) LIKE LOWER(?)
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY platform, market_id
            ORDER BY snapshot_time DESC
        ) = 1
        ORDER BY snapshot_time DESC
        LIMIT ?
    """, [f"%{q}%", limit])

    log_api_request(account["api_key"], "/v1/search", 200, len(rows))
    return rows

@app.get("/v1/categories")
def categories(account=Depends(verify_api_key)):
    rows = query_db("""
        SELECT
            LOWER(COALESCE(category, 'unknown')) AS category,
            COUNT(*) AS markets
        FROM (
            SELECT DISTINCT platform, market_id, category
            FROM market_snapshots
        )
        GROUP BY category
        ORDER BY markets DESC
    """)

    log_api_request(account["api_key"], "/v1/categories", 200, len(rows))
    return rows