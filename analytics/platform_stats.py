import duckdb

conn = duckdb.connect("data/warehouse.duckdb")

df = conn.execute("""
SELECT
    platform,
    COUNT(*) AS row_count,
    COUNT(DISTINCT market_id) AS unique_markets,
    ROUND(AVG(volume),2) AS avg_volume,
    ROUND(AVG(liquidity),2) AS avg_liquidity
FROM market_snapshots
GROUP BY platform
ORDER BY row_count DESC
""").df()

print(df)

conn.close()