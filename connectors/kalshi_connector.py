from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from connectors.http_client import build_session, get_json
from connectors.title_utils import normalize_title

KALSHI_BASE_URL = os.getenv(
    "KALSHI_BASE_URL",
    "https://external-api.kalshi.com/trade-api/v2",
).rstrip("/")

FAST_LIMIT = max(
    int(os.getenv("KALSHI_FAST_LIMIT", "100")),
    1,
)
DISCOVERY_PAGE_SIZE = min(
    max(int(os.getenv("KALSHI_DISCOVERY_PAGE_SIZE", "1000")), 1),
    1000,
)
DISCOVERY_MAX_PAGES = max(
    int(os.getenv("KALSHI_DISCOVERY_MAX_PAGES", "100")),
    1,
)
REQUEST_SLEEP_SECONDS = max(
    float(os.getenv("KALSHI_REQUEST_SLEEP_SECONDS", "0.10")),
    0.0,
)


def _num(
    value: Any,
    default: Optional[float] = 0.0,
) -> Optional[float]:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _probability(value: Any) -> Optional[float]:
    numeric = _num(value, None)
    if numeric is None:
        return None
    if numeric > 1:
        numeric /= 100.0
    if numeric < 0 or numeric > 1:
        return None
    return round(numeric, 6)


def _yes_price(market: dict[str, Any]) -> Optional[float]:
    last_price = _probability(
        market.get("last_price_dollars")
        or market.get("last_price")
    )
    if last_price is not None:
        return last_price

    bid = _probability(
        market.get("yes_bid_dollars")
        or market.get("yes_bid")
    )
    ask = _probability(
        market.get("yes_ask_dollars")
        or market.get("yes_ask")
    )

    if bid is not None and ask is not None:
        return round((bid + ask) / 2.0, 6)

    return bid if bid is not None else ask


def _no_price(
    market: dict[str, Any],
    yes_price: Optional[float],
) -> Optional[float]:
    bid = _probability(
        market.get("no_bid_dollars")
        or market.get("no_bid")
    )
    ask = _probability(
        market.get("no_ask_dollars")
        or market.get("no_ask")
    )

    if bid is not None and ask is not None:
        return round((bid + ask) / 2.0, 6)
    if bid is not None:
        return bid
    if ask is not None:
        return ask
    if yes_price is not None:
        return round(1.0 - yes_price, 6)
    return None


def _market_row(
    market: dict[str, Any],
    *,
    now: str,
    mode: str,
) -> Optional[dict[str, Any]]:
    ticker = market.get("ticker") or market.get("market_ticker")
    if not ticker:
        return None

    title = (
        market.get("title")
        or market.get("subtitle")
        or market.get("yes_sub_title")
        or ticker
    )
    yes_price = _yes_price(market)
    no_price = _no_price(market, yes_price)
    event_ticker = market.get("event_ticker")

    return {
        "platform": "kalshi",
        "market_id": str(ticker),
        "title": str(title),
        "canonical_title": normalize_title(title),
        "category": market.get("category") or "unknown",
        "start_date": market.get("open_time"),
        "close_date": market.get("close_time"),
        "resolution_date": (
            market.get("expected_expiration_time")
            or market.get("latest_expiration_time")
            or market.get("expiration_time")
        ),
        "status": market.get("status") or "open",
        "outcome": None,
        "resolution_source": (
            market.get("rules_primary")
            or market.get("rules_secondary")
        ),
        "raw_url": (
            "https://kalshi.com/markets/"
            f"{event_ticker or ticker}"
        ),
        "volume": _num(
            market.get("volume_fp")
            or market.get("volume")
            or market.get("volume_24h_fp")
            or market.get("volume_24h"),
            None,
        ),
        "liquidity": _num(
            market.get("liquidity_dollars")
            or market.get("liquidity")
            or market.get("open_interest_fp")
            or market.get("open_interest"),
            None,
        ),
        "yes_price": yes_price,
        "no_price": no_price,
        "source": f"kalshi_api:{mode}",
        "ingested_at": now,
        "snapshot_time": now,
        "close_time": market.get("close_time"),
    }


def fetch_kalshi_markets(
    limit: Optional[int] = None,
    *,
    mode: Optional[str] = None,
) -> list[dict[str, Any]]:
    refresh_mode = (
        mode
        or os.getenv("MARKET_REFRESH_MODE", "fast")
    ).strip().lower()
    discovery = refresh_mode == "discovery"

    if discovery:
        page_size = DISCOVERY_PAGE_SIZE
        maximum_pages = DISCOVERY_MAX_PAGES
        maximum_rows = max(
            int(
                os.getenv(
                    "KALSHI_DISCOVERY_MAX_MARKETS",
                    "0",
                )
            ),
            0,
        )
    else:
        maximum_rows = max(int(limit or FAST_LIMIT), 1)
        page_size = min(maximum_rows, 1000)
        maximum_pages = 1

    session = build_session()
    url = f"{KALSHI_BASE_URL}/markets"
    cursor = ""
    now = datetime.now(timezone.utc).isoformat()
    rows_by_id: dict[str, dict[str, Any]] = {}

    for page_number in range(1, maximum_pages + 1):
        params: dict[str, Any] = {
            "limit": page_size,
            "status": "open",
        }
        if cursor:
            params["cursor"] = cursor

        payload = get_json(session, url, params=params)
        markets = (
            payload.get("markets", [])
            if isinstance(payload, dict)
            else []
        )
        if not isinstance(markets, list):
            raise RuntimeError(
                "Kalshi response field 'markets' was not a list."
            )

        for market in markets:
            if not isinstance(market, dict):
                continue
            row = _market_row(
                market,
                now=now,
                mode=refresh_mode,
            )
            if row is not None:
                rows_by_id[row["market_id"]] = row

        print(
            f"[kalshi:{refresh_mode}] page={page_number} "
            f"received={len(markets):,} "
            f"unique={len(rows_by_id):,}",
            flush=True,
        )

        if maximum_rows and len(rows_by_id) >= maximum_rows:
            break

        cursor = (
            str(payload.get("cursor") or "")
            if isinstance(payload, dict)
            else ""
        )
        if not cursor or not markets:
            break

        if REQUEST_SLEEP_SECONDS:
            time.sleep(REQUEST_SLEEP_SECONDS)

    rows = list(rows_by_id.values())
    if maximum_rows:
        rows = rows[:maximum_rows]
    return rows


if __name__ == "__main__":
    output = fetch_kalshi_markets(mode="fast")
    print(f"Fetched Kalshi markets: {len(output):,}")
