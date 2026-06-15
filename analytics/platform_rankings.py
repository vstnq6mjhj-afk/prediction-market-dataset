import duckdb

conn = duckdb.connect("data/warehouse.duckdb")

df = conn.execute("""
SELECT
    platform,
    COUNT(*) markets,
    AVG(volume) avg_volume,
    AVG(liquidity) avg_liquidity
FROM market_snapshots
GROUP BY platform
ORDER BY avg_volume DESC
""").df()

print(df)