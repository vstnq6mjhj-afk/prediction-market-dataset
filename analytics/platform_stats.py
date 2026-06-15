import duckdb
import pandas as pd
import numpy as np

DB_PATH = "data/warehouse.duckdb"

def get_platform_stats():
    conn = duckdb.connect(DB_PATH)

    df = conn.execute("""
        SELECT
            platform,
            COUNT(*) AS row_count,
            COUNT(DISTINCT market_id) AS unique_markets,
            ROUND(COALESCE(AVG(volume), 0), 2) AS avg_volume,
            ROUND(COALESCE(AVG(liquidity), 0), 2) AS avg_liquidity
        FROM market_snapshots
        GROUP BY platform
        ORDER BY row_count DESC
    """).df()

    conn.close()

    df = df.replace([np.inf, -np.inf], 0)
    df = df.fillna(0)

    return df.to_dict(orient="records")


if __name__ == "__main__":
    print(pd.DataFrame(get_platform_stats()))