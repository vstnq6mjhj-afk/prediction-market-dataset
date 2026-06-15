import duckdb
import sys

query = sys.argv[1] if len(sys.argv) > 1 else ""

conn = duckdb.connect("data/warehouse.duckdb")

df = conn.execute("""
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
WHERE lower(title) LIKE ?
ORDER BY snapshot_time DESC
LIMIT 100
""", [f"%{query.lower()}%"]).df()

print(df.to_string())

conn.close()