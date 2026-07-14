from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from connectors.http_client import build_session, get_json
from connectors.title_utils import normalize_title

POLYMARKET_BASE_URL = os.getenv(
    "POLYMARKET_BASE_URL",
    "https://gamma-api.polymarket.com",
).rstrip("/")

FAST_LIMIT = max(
    int(os.getenv("POLYMARKET_FAST_LIMIT", "100")),
    1,
)
DISCOVERY_PAGE_SIZE = min(
    max(
        int(
            os.getenv(
                "POLYMARKET_DISCOVERY_PAGE_SIZE",
                "500",
            )
        ),
        1,
    ),
    500,
)
DISCOVERY_MAX_PAGES = max(
    int(os.getenv("POLYMARKET_DISCOVERY_MAX_PAGES", "100")),
    1,
)
REQUEST_SLEEP_SECONDS = max(
    float(os.getenv("POLYMARKET_REQUEST_SLEEP_SECONDS", "0.05")),
    0.0,
)


def _safe_float(
    value: Any,
    default: Optional[float] = 0.0,
) -> Optional[float]:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
    return []


def _extract_yes_no_prices(
    market: dict[str, Any],
) -> tuple[Optional[float], Optional[float]]:
    outcomes = _json_list(market.get("outcomes"))
    prices = _json_list(market.get("outcomePrices"))

    yes_price: Optional[float] = None
    no_price: Optional[float] = None

    for outcome, price in zip(outcomes, prices):
        outcome_name = str(outcome).strip().lower()
        numeric_price = _safe_float(price, None)

        if outcome_name == "yes":
            yes_price = numeric_price
        elif outcome_name == "no":
            no_price = numeric_price

    if yes_price is not None and no_price is None:
        no_price = round(1.0 - yes_price, 6)
    elif no_price is not None and yes_price is None:
        yes_price = round(1.0 - no_price, 6)

    return yes_price, no_price


def _market_row(
    market: dict[str, Any],
    *,
    now: str,
    mode: str,
) -> Optional[dict[str, Any]]:
    market_id = (
        market.get("conditionId")
        or market.get("condition_id")
        or market.get("id")
    )
    if market_id in (None, ""):
        return None

    title = market.get("question") or market.get("title") or ""
    yes_price, no_price = _extract_yes_no_prices(market)
    slug = market.get("slug") or ""

    return {
        "platform": "polymarket",
        "market_id": str(market_id),
        "title": str(title),
        "canonical_title": normalize_title(title),
        "category": market.get("category") or "unknown",
        "start_date": (
            market.get("startDate")
            or market.get("start_date")
        ),
        "close_date": (
            market.get("endDate")
            or market.get("end_date")
        ),
        "resolution_date": (
            market.get("resolutionDate")
            or market.get("resolution_date")
        ),
        "status": "active",
        "outcome": "",
        "resolution_source": (
            market.get("resolutionSource")
            or market.get("resolution_source")
        ),
        "raw_url": (
            f"https://polymarket.com/event/{slug}"
            if slug
            else ""
        ),
        "volume": _safe_float(
            market.get("volumeNum")
            or market.get("volume"),
            None,
        ),
        "liquidity": _safe_float(
            market.get("liquidityNum")
            or market.get("liquidity"),
            None,
        ),
        "yes_price": yes_price,
        "no_price": no_price,
        "source": f"polymarket_gamma_api:{mode}",
        "ingested_at": now,
        "snapshot_time": now,
        "close_time": (
            market.get("endDate")
            or market.get("end_date")
        ),
    }


def fetch_polymarket_markets(
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
                    "POLYMARKET_DISCOVERY_MAX_MARKETS",
                    "0",
                )
            ),
            0,
        )
    else:
        maximum_rows = max(int(limit or FAST_LIMIT), 1)
        page_size = min(maximum_rows, 500)
        maximum_pages = 1

    session = build_session()
    url = f"{POLYMARKET_BASE_URL}/markets"
    offset = 0
    now = datetime.now(timezone.utc).isoformat()
    rows_by_id: dict[str, dict[str, Any]] = {}

    for page_number in range(1, maximum_pages + 1):
        params = {
            "active": "true",
            "closed": "false",
            "limit": page_size,
            "offset": offset,
        }
        payload = get_json(session, url, params=params)

        markets = payload if isinstance(payload, list) else []
        if not isinstance(markets, list):
            raise RuntimeError(
                "Polymarket markets response was not a list."
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
            f"[polymarket:{refresh_mode}] page={page_number} "
            f"offset={offset:,} received={len(markets):,} "
            f"unique={len(rows_by_id):,}",
            flush=True,
        )

        if maximum_rows and len(rows_by_id) >= maximum_rows:
            break
        if len(markets) < page_size:
            break

        offset += page_size
        if REQUEST_SLEEP_SECONDS:
            time.sleep(REQUEST_SLEEP_SECONDS)

    rows = list(rows_by_id.values())
    if maximum_rows:
        rows = rows[:maximum_rows]
    return rows


if __name__ == "__main__":
    output = fetch_polymarket_markets(mode="fast")
    print(f"Fetched Polymarket markets: {len(output):,}")
