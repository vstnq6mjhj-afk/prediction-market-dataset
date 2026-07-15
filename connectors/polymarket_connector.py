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
                "100",
            )
        ),
        1,
    ),
    100,
)
DISCOVERY_MAX_PAGES = max(
    int(os.getenv("POLYMARKET_DISCOVERY_MAX_PAGES", "250")),
    1,
)
REQUEST_SLEEP_SECONDS = max(
    float(os.getenv("POLYMARKET_REQUEST_SLEEP_SECONDS", "0.05")),
    0.0,
)

_LAST_FETCH_DIAGNOSTICS: dict[str, Any] = {}


def get_last_fetch_diagnostics() -> dict[str, Any]:
    return dict(_LAST_FETCH_DIAGNOSTICS)


def _set_diagnostics(**values: Any) -> None:
    global _LAST_FETCH_DIAGNOSTICS
    _LAST_FETCH_DIAGNOSTICS = dict(values)


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


def _bool_value(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    return None


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


def _event_category(event: dict[str, Any]) -> str:
    direct = event.get("category")
    if direct:
        return str(direct)

    tags = event.get("tags") or []
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict):
                label = tag.get("label") or tag.get("name")
                if label:
                    return str(label)
            elif tag:
                return str(tag)

    return "unknown"


def _is_active_market(market: dict[str, Any]) -> bool:
    active = _bool_value(market.get("active"))
    closed = _bool_value(market.get("closed"))

    if active is False:
        return False
    if closed is True:
        return False
    return True


def _market_row(
    market: dict[str, Any],
    *,
    event: Optional[dict[str, Any]],
    now: str,
    mode: str,
) -> Optional[dict[str, Any]]:
    if not _is_active_market(market):
        return None

    market_id = (
        market.get("conditionId")
        or market.get("condition_id")
        or market.get("id")
    )
    if market_id in (None, ""):
        return None

    event = event or {}
    title = (
        market.get("question")
        or market.get("title")
        or event.get("title")
        or ""
    )
    yes_price, no_price = _extract_yes_no_prices(market)
    event_slug = event.get("slug") or ""
    market_slug = market.get("slug") or ""

    return {
        "platform": "polymarket",
        "market_id": str(market_id),
        "title": str(title),
        "canonical_title": normalize_title(title),
        "category": (
            market.get("category")
            or _event_category(event)
        ),
        "start_date": (
            market.get("startDate")
            or market.get("start_date")
            or event.get("startDate")
            or event.get("start_date")
        ),
        "close_date": (
            market.get("endDate")
            or market.get("end_date")
            or event.get("endDate")
            or event.get("end_date")
        ),
        "resolution_date": (
            market.get("resolutionDate")
            or market.get("resolution_date")
            or event.get("resolutionDate")
            or event.get("resolution_date")
        ),
        "status": "active",
        "outcome": "",
        "resolution_source": (
            market.get("resolutionSource")
            or market.get("resolution_source")
            or event.get("resolutionSource")
            or event.get("resolution_source")
        ),
        "raw_url": (
            f"https://polymarket.com/event/{event_slug}"
            if event_slug
            else (
                f"https://polymarket.com/event/{market_slug}"
                if market_slug
                else ""
            )
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
            or event.get("endDate")
            or event.get("end_date")
        ),
    }


def _fetch_fast(
    *,
    maximum_rows: int,
    now: str,
) -> list[dict[str, Any]]:
    session = build_session()
    payload = get_json(
        session,
        f"{POLYMARKET_BASE_URL}/markets",
        params={
            "active": "true",
            "closed": "false",
            "limit": min(maximum_rows, 100),
            "offset": 0,
        },
    )
    markets = payload if isinstance(payload, list) else []

    rows_by_id: dict[str, dict[str, Any]] = {}
    for market in markets:
        if not isinstance(market, dict):
            continue
        row = _market_row(
            market,
            event=None,
            now=now,
            mode="fast",
        )
        if row is not None:
            rows_by_id[row["market_id"]] = row

    rows = list(rows_by_id.values())[:maximum_rows]
    _set_diagnostics(
        endpoint="/markets",
        mode="fast",
        pages_fetched=1,
        page_size=min(maximum_rows, 100),
        maximum_pages=1,
        raw_events_received=0,
        raw_markets_received=len(markets),
        unique_markets=len(rows),
        duplicate_markets=0,
        last_page_size=len(markets),
        termination_reason="fast_limit",
        complete=False,
        page_limit_reached=False,
    )
    return rows


def _fetch_discovery(
    *,
    page_size: int,
    maximum_pages: int,
    maximum_rows: int,
    now: str,
) -> list[dict[str, Any]]:
    session = build_session()
    url = f"{POLYMARKET_BASE_URL}/events/keyset"
    after_cursor = ""
    seen_cursors: set[str] = set()
    rows_by_id: dict[str, dict[str, Any]] = {}

    pages_fetched = 0
    raw_events_received = 0
    raw_markets_received = 0
    duplicate_markets = 0
    last_page_size = 0
    termination_reason = "not_started"
    complete = False
    partial_error: Optional[str] = None

    for page_number in range(1, maximum_pages + 1):
        params: dict[str, Any] = {
            "active": "true",
            "closed": "false",
            "limit": page_size,
        }
        if after_cursor:
            params["after_cursor"] = after_cursor

        try:
            payload = get_json(
                session,
                url,
                params=params,
            )
        except Exception as exc:
            partial_error = str(exc)
            termination_reason = "request_failed"
            break

        if not isinstance(payload, dict):
            partial_error = (
                "Polymarket keyset response was not an object."
            )
            termination_reason = "invalid_response"
            break

        events = payload.get("events") or []
        if not isinstance(events, list):
            partial_error = (
                "Polymarket keyset response field 'events' "
                "was not a list."
            )
            termination_reason = "invalid_response"
            break

        next_cursor = str(payload.get("next_cursor") or "")

        pages_fetched += 1
        last_page_size = len(events)
        raw_events_received += len(events)

        page_market_count = 0
        for event in events:
            if not isinstance(event, dict):
                continue

            markets = event.get("markets") or []
            if not isinstance(markets, list):
                continue

            raw_markets_received += len(markets)
            page_market_count += len(markets)

            for market in markets:
                if not isinstance(market, dict):
                    continue

                row = _market_row(
                    market,
                    event=event,
                    now=now,
                    mode="discovery",
                )
                if row is None:
                    continue

                key = row["market_id"]
                if key in rows_by_id:
                    duplicate_markets += 1
                rows_by_id[key] = row

        print(
            f"[polymarket:discovery] page={page_number} "
            f"events={len(events):,} "
            f"nested_markets={page_market_count:,} "
            f"unique={len(rows_by_id):,} "
            f"next_cursor={'yes' if next_cursor else 'no'}",
            flush=True,
        )

        if maximum_rows and len(rows_by_id) >= maximum_rows:
            termination_reason = "row_cap_reached"
            break

        if not events:
            termination_reason = "empty_page"
            complete = True
            break

        if not next_cursor:
            termination_reason = "cursor_exhausted"
            complete = True
            break

        if next_cursor in seen_cursors:
            termination_reason = "repeated_cursor"
            break

        seen_cursors.add(next_cursor)
        after_cursor = next_cursor

        if REQUEST_SLEEP_SECONDS:
            time.sleep(REQUEST_SLEEP_SECONDS)
    else:
        termination_reason = "page_limit_reached"

    rows = list(rows_by_id.values())
    if maximum_rows:
        rows = rows[:maximum_rows]

    _set_diagnostics(
        endpoint="/events/keyset",
        strategy="keyset_event_first_nested_markets",
        mode="discovery",
        pages_fetched=pages_fetched,
        page_size=page_size,
        maximum_pages=maximum_pages,
        maximum_rows=maximum_rows,
        raw_events_received=raw_events_received,
        raw_markets_received=raw_markets_received,
        unique_markets=len(rows),
        duplicate_markets=duplicate_markets,
        last_page_size=last_page_size,
        termination_reason=termination_reason,
        complete=complete,
        page_limit_reached=(
            termination_reason == "page_limit_reached"
        ),
        partial_error=partial_error,
    )
    return rows


def fetch_polymarket_markets(
    limit: Optional[int] = None,
    *,
    mode: Optional[str] = None,
) -> list[dict[str, Any]]:
    refresh_mode = (
        mode
        or os.getenv("MARKET_REFRESH_MODE", "fast")
    ).strip().lower()
    now = datetime.now(timezone.utc).isoformat()

    if refresh_mode != "discovery":
        return _fetch_fast(
            maximum_rows=max(int(limit or FAST_LIMIT), 1),
            now=now,
        )

    return _fetch_discovery(
        page_size=DISCOVERY_PAGE_SIZE,
        maximum_pages=DISCOVERY_MAX_PAGES,
        maximum_rows=max(
            int(
                os.getenv(
                    "POLYMARKET_DISCOVERY_MAX_MARKETS",
                    "0",
                )
            ),
            0,
        ),
        now=now,
    )


if __name__ == "__main__":
    output = fetch_polymarket_markets(mode="fast")
    print(f"Fetched Polymarket markets: {len(output):,}")
    print(get_last_fetch_diagnostics())
