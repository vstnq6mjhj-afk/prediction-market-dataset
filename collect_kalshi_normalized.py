from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import duckdb
import requests

API_BASE = os.getenv(
    "KALSHI_API_BASE",
    "https://external-api.kalshi.com/trade-api/v2",
).rstrip("/")
OUTPUT_PATH = Path(
    os.getenv(
        "KALSHI_NORMALIZED_DB_PATH",
        "/var/data/kalshi_normalized.duckdb",
    )
)
STATUS = os.getenv("KALSHI_EVENT_STATUS", "open")
PAGE_LIMIT = min(max(int(os.getenv("KALSHI_PAGE_LIMIT", "200")), 1), 200)
MAX_PAGES = max(int(os.getenv("KALSHI_MAX_PAGES", "50")), 1)
REQUEST_TIMEOUT = max(int(os.getenv("KALSHI_REQUEST_TIMEOUT", "30")), 5)
SLEEP_SECONDS = max(float(os.getenv("KALSHI_REQUEST_SLEEP", "0.15")), 0.0)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "Accept": "application/json",
        "User-Agent": "PredictionMarketDataset/1.0",
    }
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def first_value(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return value
    return None


def text_value(record: dict[str, Any], *names: str) -> Optional[str]:
    value = first_value(record, *names)
    return None if value is None else str(value)


def float_value(record: dict[str, Any], *names: str) -> Optional[float]:
    value = first_value(record, *names)
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def bool_value(record: dict[str, Any], *names: str) -> Optional[bool]:
    value = first_value(record, *names)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if str(value).lower() in {"true", "1", "yes"}:
        return True
    if str(value).lower() in {"false", "0", "no"}:
        return False
    return None


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def dollar_probability(
    record: dict[str, Any],
    dollar_names: Iterable[str],
    legacy_cent_names: Iterable[str],
) -> Optional[float]:
    value = first_value(record, *tuple(dollar_names))
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            pass

    legacy = first_value(record, *tuple(legacy_cent_names))
    if legacy is None:
        return None

    try:
        numeric = float(legacy)
        return numeric / 100.0 if numeric > 1 else numeric
    except (TypeError, ValueError):
        return None


def get_events_page(cursor: str = "") -> dict[str, Any]:
    params: dict[str, Any] = {
        "limit": PAGE_LIMIT,
        "with_nested_markets": "true",
        "status": STATUS,
    }
    if cursor:
        params["cursor"] = cursor

    response = SESSION.get(
        f"{API_BASE}/events",
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError("Kalshi returned a non-object response.")

    return payload


def create_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS kalshi_events (
            event_ticker VARCHAR PRIMARY KEY,
            series_ticker VARCHAR,
            event_title VARCHAR,
            event_subtitle VARCHAR,
            category VARCHAR,
            sub_category VARCHAR,
            status VARCHAR,
            mutually_exclusive BOOLEAN,
            competition VARCHAR,
            competition_scope VARCHAR,
            market_count INTEGER,
            event_updated_time VARCHAR,
            collected_at TIMESTAMP WITH TIME ZONE,
            raw_event_json VARCHAR
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS kalshi_contracts (
            market_ticker VARCHAR PRIMARY KEY,
            event_ticker VARCHAR,
            series_ticker VARCHAR,
            event_title VARCHAR,
            event_subtitle VARCHAR,
            market_title VARCHAR,
            market_subtitle VARCHAR,
            yes_sub_title VARCHAR,
            no_sub_title VARCHAR,
            status VARCHAR,
            market_type VARCHAR,
            strike_type VARCHAR,
            floor_strike DOUBLE,
            cap_strike DOUBLE,
            functional_strike VARCHAR,
            custom_strike_json VARCHAR,
            primary_participant_key VARCHAR,
            rules_primary VARCHAR,
            rules_secondary VARCHAR,
            open_time VARCHAR,
            close_time VARCHAR,
            expected_expiration_time VARCHAR,
            expiration_time VARCHAR,
            occurrence_datetime VARCHAR,
            yes_bid DOUBLE,
            yes_ask DOUBLE,
            no_bid DOUBLE,
            no_ask DOUBLE,
            last_price DOUBLE,
            volume DOUBLE,
            volume_24h DOUBLE,
            open_interest DOUBLE,
            liquidity DOUBLE,
            is_provisional BOOLEAN,
            is_multivariate BOOLEAN,
            multivariate_collection_ticker VARCHAR,
            selected_legs_json VARCHAR,
            collected_at TIMESTAMP WITH TIME ZONE,
            raw_market_json VARCHAR
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS kalshi_ingestion_runs (
            run_id VARCHAR PRIMARY KEY,
            started_at TIMESTAMP WITH TIME ZONE,
            completed_at TIMESTAMP WITH TIME ZONE,
            status VARCHAR,
            pages_fetched INTEGER,
            events_seen INTEGER,
            contracts_seen INTEGER,
            error_message VARCHAR
        )
        """
    )


EVENT_UPSERT = """
INSERT OR REPLACE INTO kalshi_events (
    event_ticker,
    series_ticker,
    event_title,
    event_subtitle,
    category,
    sub_category,
    status,
    mutually_exclusive,
    competition,
    competition_scope,
    market_count,
    event_updated_time,
    collected_at,
    raw_event_json
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


CONTRACT_UPSERT = """
INSERT OR REPLACE INTO kalshi_contracts (
    market_ticker,
    event_ticker,
    series_ticker,
    event_title,
    event_subtitle,
    market_title,
    market_subtitle,
    yes_sub_title,
    no_sub_title,
    status,
    market_type,
    strike_type,
    floor_strike,
    cap_strike,
    functional_strike,
    custom_strike_json,
    primary_participant_key,
    rules_primary,
    rules_secondary,
    open_time,
    close_time,
    expected_expiration_time,
    expiration_time,
    occurrence_datetime,
    yes_bid,
    yes_ask,
    no_bid,
    no_ask,
    last_price,
    volume,
    volume_24h,
    open_interest,
    liquidity,
    is_provisional,
    is_multivariate,
    multivariate_collection_ticker,
    selected_legs_json,
    collected_at,
    raw_market_json
)
VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?
)
"""


def event_row(event: dict[str, Any], collected_at: datetime) -> tuple[Any, ...]:
    markets = event.get("markets")
    if not isinstance(markets, list):
        markets = []

    return (
        text_value(event, "event_ticker", "ticker"),
        text_value(event, "series_ticker"),
        text_value(event, "title"),
        text_value(event, "subtitle", "sub_title"),
        text_value(event, "category"),
        text_value(event, "sub_category", "subcategory"),
        text_value(event, "status"),
        bool_value(event, "mutually_exclusive"),
        text_value(event, "competition"),
        text_value(event, "competition_scope"),
        len(markets),
        text_value(event, "updated_time", "last_updated_ts"),
        collected_at,
        json_text(event),
    )


def contract_row(
    event: dict[str, Any],
    market: dict[str, Any],
    collected_at: datetime,
) -> tuple[Any, ...]:
    event_ticker = text_value(
        market,
        "event_ticker",
    ) or text_value(event, "event_ticker", "ticker")

    series_ticker = text_value(
        market,
        "series_ticker",
    ) or text_value(event, "series_ticker")

    selected_legs = market.get("mve_selected_legs") or []
    collection_ticker = text_value(
        market,
        "mve_collection_ticker",
        "multivariate_event_collection_ticker",
    )

    return (
        text_value(market, "ticker", "market_ticker"),
        event_ticker,
        series_ticker,
        text_value(event, "title"),
        text_value(event, "subtitle", "sub_title"),
        text_value(market, "title"),
        text_value(market, "subtitle", "sub_title"),
        text_value(market, "yes_sub_title", "yes_subtitle"),
        text_value(market, "no_sub_title", "no_subtitle"),
        text_value(market, "status"),
        text_value(market, "market_type", "type"),
        text_value(market, "strike_type"),
        float_value(market, "floor_strike"),
        float_value(market, "cap_strike"),
        text_value(market, "functional_strike"),
        json_text(market.get("custom_strike") or {}),
        text_value(market, "primary_participant_key"),
        text_value(market, "rules_primary"),
        text_value(market, "rules_secondary"),
        text_value(market, "open_time"),
        text_value(market, "close_time"),
        text_value(market, "expected_expiration_time"),
        text_value(market, "expiration_time"),
        text_value(market, "occurrence_datetime"),
        dollar_probability(
            market,
            ("yes_bid_dollars",),
            ("yes_bid",),
        ),
        dollar_probability(
            market,
            ("yes_ask_dollars",),
            ("yes_ask",),
        ),
        dollar_probability(
            market,
            ("no_bid_dollars",),
            ("no_bid",),
        ),
        dollar_probability(
            market,
            ("no_ask_dollars",),
            ("no_ask",),
        ),
        dollar_probability(
            market,
            ("last_price_dollars",),
            ("last_price",),
        ),
        float_value(market, "volume", "volume_fp"),
        float_value(market, "volume_24h", "volume_24h_fp"),
        float_value(market, "open_interest", "open_interest_fp"),
        float_value(market, "liquidity_dollars", "liquidity"),
        bool_value(market, "is_provisional"),
        bool(collection_ticker or selected_legs),
        collection_ticker,
        json_text(selected_legs),
        collected_at,
        json_text(market),
    )


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    run_id = f"kalshi_{utc_now().strftime('%Y%m%dT%H%M%S%fZ')}"
    started_at = utc_now()

    connection = duckdb.connect(str(OUTPUT_PATH))
    connection.execute("SET threads = 1")
    create_schema(connection)

    pages_fetched = 0
    events_seen = 0
    contracts_seen = 0
    cursor = ""

    connection.execute(
        """
        INSERT INTO kalshi_ingestion_runs (
            run_id,
            started_at,
            completed_at,
            status,
            pages_fetched,
            events_seen,
            contracts_seen,
            error_message
        )
        VALUES (?, ?, NULL, 'running', 0, 0, 0, NULL)
        """,
        [run_id, started_at],
    )

    try:
        for page_number in range(1, MAX_PAGES + 1):
            payload = get_events_page(cursor)
            collected_at = utc_now()
            events = payload.get("events") or []

            if not isinstance(events, list):
                raise RuntimeError("Kalshi response field 'events' was not a list.")

            event_rows: list[tuple[Any, ...]] = []
            contract_rows: list[tuple[Any, ...]] = []

            for event in events:
                if not isinstance(event, dict):
                    continue

                ticker = text_value(event, "event_ticker", "ticker")
                if not ticker:
                    continue

                event_rows.append(event_row(event, collected_at))
                events_seen += 1

                markets = event.get("markets") or []
                if not isinstance(markets, list):
                    markets = []

                for market in markets:
                    if not isinstance(market, dict):
                        continue

                    market_ticker = text_value(market, "ticker", "market_ticker")
                    if not market_ticker:
                        continue

                    contract_rows.append(
                        contract_row(event, market, collected_at)
                    )
                    contracts_seen += 1

            if event_rows:
                connection.executemany(EVENT_UPSERT, event_rows)
            if contract_rows:
                connection.executemany(CONTRACT_UPSERT, contract_rows)

            pages_fetched += 1
            connection.execute("CHECKPOINT")

            print(
                f"[kalshi-normalized] page={page_number} "
                f"events={len(event_rows):,} "
                f"contracts={len(contract_rows):,} "
                f"total_events={events_seen:,} "
                f"total_contracts={contracts_seen:,}"
            )

            cursor = str(payload.get("cursor") or "")
            if not cursor:
                break

            if SLEEP_SECONDS:
                time.sleep(SLEEP_SECONDS)

        completed_at = utc_now()
        connection.execute(
            """
            UPDATE kalshi_ingestion_runs
            SET
                completed_at = ?,
                status = 'complete',
                pages_fetched = ?,
                events_seen = ?,
                contracts_seen = ?
            WHERE run_id = ?
            """,
            [
                completed_at,
                pages_fetched,
                events_seen,
                contracts_seen,
                run_id,
            ],
        )
        connection.execute("CHECKPOINT")

        event_count = connection.execute(
            "SELECT COUNT(*) FROM kalshi_events"
        ).fetchone()[0]
        contract_count = connection.execute(
            "SELECT COUNT(*) FROM kalshi_contracts"
        ).fetchone()[0]
        multivariate_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM kalshi_contracts
            WHERE is_multivariate = TRUE
            """
        ).fetchone()[0]

        print(f"[kalshi-normalized] Database: {OUTPUT_PATH}")
        print(f"[kalshi-normalized] Events stored: {event_count:,}")
        print(f"[kalshi-normalized] Contracts stored: {contract_count:,}")
        print(
            "[kalshi-normalized] Multivariate contracts marked: "
            f"{multivariate_count:,}"
        )
        print("[kalshi-normalized] Complete")
    except Exception as exc:
        connection.execute(
            """
            UPDATE kalshi_ingestion_runs
            SET
                completed_at = ?,
                status = 'failed',
                pages_fetched = ?,
                events_seen = ?,
                contracts_seen = ?,
                error_message = ?
            WHERE run_id = ?
            """,
            [
                utc_now(),
                pages_fetched,
                events_seen,
                contracts_seen,
                str(exc),
                run_id,
            ],
        )
        connection.execute("CHECKPOINT")
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
