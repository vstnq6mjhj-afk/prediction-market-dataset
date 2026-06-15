import duckdb

conn = duckdb.connect("data/warehouse.duckdb")

df = conn.execute("""
SELECT
    snapshot_time,
    COUNT(*) AS rows
FROM market_snapshots
GROUP BY snapshot_time
ORDER BY snapshot_time
""").df()

print(df.to_string())

conn.close()