import duckdb
import pandas as pd

conn = duckdb.connect("data/warehouse.duckdb")

df = conn.execute("""
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
    title,
    yes_price,
    volume,
    liquidity
FROM latest
ORDER BY volume DESC
LIMIT 50
""").df()

print(df)

conn.close()