import os
from typing import Optional

import duckdb
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

        df = df.astype(object).where(df.notna(), None)
        return df.to_dict(orient="records")
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