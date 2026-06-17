import duckdb
import pandas as pd

con = duckdb.connect("data/warehouse.duckdb")

print("\n=== PLATFORM SUMMARY ===")
print(
    con.sql("""
    SELECT
        platform,
        COUNT(*) AS rows,
        COUNT(volume) AS volume_filled,
        COUNT(liquidity) AS liquidity_filled,
        COUNT(yes_price) AS yes_price_filled,
        COUNT(no_price) AS no_price_filled
    FROM market_snapshots
    GROUP BY platform
    ORDER BY rows DESC
    """).df()
)

print("\n=== SAMPLE ROWS ===")
print(
    con.sql("""
    SELECT
        platform,
        market_id,
        title,
        yes_price,
        no_price,
        volume,
        liquidity
    FROM market_snapshots
    ORDER BY snapshot_time DESC
    LIMIT 20
    """).df()
)

con.close()