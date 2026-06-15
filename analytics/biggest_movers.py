import duckdb

conn = duckdb.connect("data/warehouse.duckdb")

df = conn.execute("""
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
LIMIT 50
""").df()

print(df.to_string())

conn.close()