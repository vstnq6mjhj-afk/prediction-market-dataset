# analytics/market_count.py

import duckdb

conn = duckdb.connect("data/warehouse.duckdb")

print(
    conn.execute("""
    SELECT
        COUNT(*) total_rows,
        COUNT(DISTINCT market_id) unique_markets
    FROM market_snapshots
    """).fetchdf()
)

conn.close()