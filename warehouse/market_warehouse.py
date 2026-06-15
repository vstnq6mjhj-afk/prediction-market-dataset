from pathlib import Path
import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "warehouse.duckdb"

def initialize():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))

    conn.execute("""
    CREATE TABLE IF NOT EXISTS market_snapshots AS
    SELECT * FROM read_csv_auto('data/snapshots/latest.csv')
    WHERE 1=0
    """)

    conn.close()

def append_snapshot(csv_path):
    conn = duckdb.connect(str(DB_PATH))
    df = pd.read_csv(csv_path)

    conn.register("snapshot_df", df)

    conn.execute("""
    INSERT INTO market_snapshots
    SELECT * FROM snapshot_df
    """)

    conn.close()