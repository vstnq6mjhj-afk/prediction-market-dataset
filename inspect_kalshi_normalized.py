from __future__ import annotations

import os

import duckdb


DB_PATH = os.getenv(
    "KALSHI_NORMALIZED_DB_PATH",
    "/var/data/kalshi_normalized.duckdb",
)


def main() -> None:
    connection = duckdb.connect(DB_PATH, read_only=True)

    print("\nTABLES")
    print(connection.execute("SHOW TABLES").fetchall())

    print("\nCOUNTS")
    print(
        connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM kalshi_events) AS events,
                (SELECT COUNT(*) FROM kalshi_contracts) AS contracts,
                (
                    SELECT COUNT(*)
                    FROM kalshi_contracts
                    WHERE is_multivariate = TRUE
                ) AS multivariate
            """
        ).fetchall()
    )

    print("\nEVENT EXAMPLES")
    for row in connection.execute(
        """
        SELECT
            event_ticker,
            series_ticker,
            event_title,
            status,
            market_count
        FROM kalshi_events
        ORDER BY market_count DESC, event_ticker
        LIMIT 15
        """
    ).fetchall():
        print(row)

    print("\nSINGLE-CONTRACT EXAMPLES")
    for row in connection.execute(
        """
        SELECT
            event_title,
            market_title,
            market_subtitle,
            yes_sub_title,
            no_sub_title,
            market_ticker,
            event_ticker,
            strike_type,
            floor_strike,
            cap_strike
        FROM kalshi_contracts
        WHERE is_multivariate = FALSE
        ORDER BY COALESCE(volume, 0) DESC
        LIMIT 25
        """
    ).fetchall():
        print(row)

    print("\nFIELD COVERAGE")
    print(
        connection.execute(
            """
            SELECT
                COUNT(*) AS contracts,
                COUNT(event_title) AS event_title,
                COUNT(market_title) AS market_title,
                COUNT(market_subtitle) AS market_subtitle,
                COUNT(yes_sub_title) AS yes_sub_title,
                COUNT(no_sub_title) AS no_sub_title,
                COUNT(strike_type) AS strike_type,
                COUNT(floor_strike) AS floor_strike,
                COUNT(cap_strike) AS cap_strike
            FROM kalshi_contracts
            WHERE is_multivariate = FALSE
            """
        ).fetchall()
    )

    connection.close()


if __name__ == "__main__":
    main()
