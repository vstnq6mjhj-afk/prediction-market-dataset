import duckdb

conn = duckdb.connect("data/warehouse.duckdb")

df = conn.execute("""
SELECT *
FROM market_snapshots
WHERE snapshot_time = (
    SELECT MAX(snapshot_time)
    FROM market_snapshots
)
ORDER BY platform, volume DESC
LIMIT 100
""").df()

print(df)

conn.close()