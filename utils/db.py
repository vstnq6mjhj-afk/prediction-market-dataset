from pathlib import Path
import sqlite3
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "prediction_market.db"


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def save_dataframe(table_name, df):
    with get_connection() as conn:
        df.to_sql(
            table_name,
            conn,
            if_exists="append",
            index=False,
        )


def read_table(table_name):
    with get_connection() as conn:
        return pd.read_sql(
            f"SELECT * FROM {table_name}",
            conn,
        )


def initialize_database():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stage TEXT,
                status TEXT,
                message TEXT,
                created_at TEXT
            )
            """
        )

    print(f"Database initialized: {DB_PATH}")


if __name__ == "__main__":
    initialize_database()