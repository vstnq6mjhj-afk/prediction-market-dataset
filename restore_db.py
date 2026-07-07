import duckdb
from pathlib import Path

db = Path("/var/data/warehouse.duckdb")

print("Exists:", db.exists())
print("Size:", db.stat().st_size)

conn = duckdb.connect(str(db))

print("Rows:", conn.execute(
    "SELECT COUNT(*) FROM market_snapshots"
).fetchone()[0])

print("Markets:", conn.execute(
    "SELECT COUNT(DISTINCT market_id) FROM market_snapshots"
).fetchone()[0])

print("Latest:", conn.execute(
    "SELECT MAX(snapshot_time) FROM market_snapshots"
).fetchone()[0])