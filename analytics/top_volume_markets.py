import duckdb

conn = duckdb.connect("data/warehouse.duckdb")

df = conn.execute("""
SELECT
    platform,
    title,
    volume,
    yes_price
FROM market_snapshots
WHERE volume IS NOT NULL
ORDER BY volume DESC
LIMIT 25
""").df()

print(df)

conn.close()