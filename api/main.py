from fastapi import FastAPI, Query
import duckdb

DB_PATH = "data/warehouse.duckdb"

app = FastAPI(title="Prediction Market Dataset API")


def query(sql: str, params=None):
    conn = duckdb.connect(DB_PATH, read_only=True)
    df = conn.execute(sql, params or []).df()
    conn.close()
    return df.to_dict(orient="records")


@app.get("/")
def home():
    return {
        "status": "ok",
        "name": "Prediction Market Dataset API",
    }


@app.get("/stats")
def stats():
    conn = duckdb.connect(DB_PATH, read_only=True)

    total_rows = conn.execute("""
    SELECT COUNT(*)
    FROM market_snapshots
    """).fetchone()[0]

    unique_markets = conn.execute("""
    SELECT COUNT(DISTINCT market_id)
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

    conn.close()

    return {
        "total_rows": total_rows,
        "unique_markets": unique_markets,
        "snapshots": snapshots,
        "latest_snapshot": str(latest_snapshot),
    }


@app.get("/platforms")
def platforms():
    return query("""
    SELECT
        platform,
        COUNT(*) AS rows,
        COUNT(DISTINCT market_id) AS unique_markets,
        AVG(volume) AS avg_volume,
        AVG(liquidity) AS avg_liquidity
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
        filters.append("lower(title) LIKE ?")
        params.append(f"%{search.lower()}%")

    where_clause = ""
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    params.append(limit)

    return query(f"""
    SELECT
        platform,
        market_id,
        title,
        yes_price,
        no_price,
        volume,
        liquidity,
        snapshot_time
    FROM market_snapshots
    {where_clause}
    ORDER BY snapshot_time DESC
    LIMIT ?
    """, params)


@app.get("/market/{market_id}")
def market_detail(market_id: str):
    return query("""
    SELECT *
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
        WHERE snapshot_time = (
            SELECT MAX(snapshot_time)
            FROM market_snapshots
        )
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
    ORDER BY volume DESC NULLS LAST
    LIMIT ?
    """, [limit])


@app.get("/movers")
def movers(limit: int = Query(50, ge=1, le=500)):
    return query("""
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
    HAVING COUNT(*) >= 3
    ORDER BY price_move DESC
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
def latest_snapshot(limit: int = Query(300, ge=1, le=1000)):
    return query("""
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
    ORDER BY platform, volume DESC NULLS LAST
    LIMIT ?
    """, [limit])