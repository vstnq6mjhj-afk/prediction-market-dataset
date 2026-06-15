import duckdb
import sys

DB_PATH = "data/warehouse.duckdb"

market_id = sys.argv[1] if len(sys.argv) > 1 else None

if not market_id:
    print("Usage: python analytics/market_detail.py MARKET_ID")
    raise SystemExit(1)

conn = duckdb.connect(DB_PATH)

df = conn.execute("""
SELECT
    platform,
    market_id,
    title,
    snapshot_time,
    yes_price,
    no_price,
    volume,
    liquidity
FROM market_snapshots
WHERE market_id = ?
ORDER BY snapshot_time
""", [market_id]).df()

print(df.to_string())

conn.close()