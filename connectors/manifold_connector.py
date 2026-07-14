from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from connectors.http_client import build_session, get_json
from connectors.title_utils import normalize_title

MANIFOLD_URL = os.getenv(
    "MANIFOLD_MARKETS_URL",
    "https://api.manifold.markets/v0/markets",
)

FAST_LIMIT = max(
    int(os.getenv("MANIFOLD_FAST_LIMIT", "100")),
    1,
)
DISCOVERY_PAGE_SIZE = min(
    max(
        int(
            os.getenv(
                "MANIFOLD_DISCOVERY_PAGE_SIZE",
                "1000",
            )
        ),
        1,
    ),
    1000,
)
DISCOVERY_MAX_PAGES = max(
    int(os.getenv("MANIFOLD_DISCOVERY_MAX_PAGES", "50")),
    1,
)
_LAST_FETCH_DIAGNOSTICS: dict[str, Any] = {}


def get_last_fetch_diagnostics() -> dict[str, Any]:
    return dict(_LAST_FETCH_DIAGNOSTICS)


def _set_diagnostics(**values: Any) -> None:
    global _LAST_FETCH_DIAGNOSTICS
    _LAST_FETCH_DIAGNOSTICS = dict(values)


REQUEST_SLEEP_SECONDS = max(
    float(os.getenv("MANIFOLD_REQUEST_SLEEP_SECONDS", "0.20")),
    0.0,
)


def _to_float(
    value: Any,
    default: Optional[float] = None,
) -> Optional[float]:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_active_binary(
    market: dict[str, Any],
    now_ms: int,
) -> bool:
    if market.get("isResolved"):
        return False

    probability = _to_float(market.get("probability"))
    if probability is None:
        return False

    outcome_type = str(
        market.get("outcomeType") or ""
    ).upper()
    if outcome_type and outcome_type != "BINARY":
        return False

    close_time = _to_float(market.get("closeTime"))
    if close_time is not None and close_time < now_ms:
        return False

    return True


def _market_row(
    market: dict[str, Any],
    *,
    now: str,
    mode: str,
) -> Optional[dict[str, Any]]:
    market_id = market.get("id")
    probability = _to_float(market.get("probability"))

    if market_id in (None, "") or probability is None:
        return None
    if probability < 0 or probability > 1:
        return None

    title = market.get("question") or market.get("title") or ""
    group_slugs = market.get("groupSlugs") or []
    category = (
        group_slugs[0]
        if isinstance(group_slugs, list) and group_slugs
        else "unknown"
    )

    return {
        "platform": "manifold",
        "market_id": str(market_id),
        "title": str(title),
        "canonical_title": normalize_title(title),
        "category": category,
        "start_date": market.get("createdTime"),
        "close_date": market.get("closeTime"),
        "resolution_date": market.get("resolutionTime"),
        "status": "open",
        "outcome": market.get("resolution"),
        "resolution_source": "",
        "raw_url": market.get("url") or "",
        "volume": _to_float(market.get("volume"), 0.0),
        "liquidity": _to_float(
            market.get("totalLiquidity"),
            0.0,
        ),
        "yes_price": round(probability, 6),
        "no_price": round(1.0 - probability, 6),
        "source": f"manifold_api:{mode}",
        "ingested_at": now,
        "snapshot_time": now,
        "close_time": market.get("closeTime"),
    }


def fetch_manifold_markets(
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
                    "MANIFOLD_DISCOVERY_MAX_MARKETS",
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
    before = ""
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    now_ms = int(now_dt.timestamp() * 1000)
    rows_by_id: dict[str, dict[str, Any]] = {}
    pages_fetched = 0
    raw_markets_received = 0
    active_binary_received = 0
    duplicate_markets = 0
    last_page_size = 0
    termination_reason = "not_started"
    complete = False

    for page_number in range(1, maximum_pages + 1):
        params: dict[str, Any] = {
            "limit": page_size,
            "sort": "updated-time",
            "order": "desc",
        }
        if before:
            params["before"] = before

        payload = get_json(
            session,
            MANIFOLD_URL,
            params=params,
        )
        markets = payload if isinstance(payload, list) else []
        if not isinstance(markets, list):
            raise RuntimeError(
                "Manifold markets response was not a list."
            )

        pages_fetched += 1
        last_page_size = len(markets)
        raw_markets_received += len(markets)
        active_received = 0

        for market in markets:
            if not isinstance(market, dict):
                continue
            if not _is_active_binary(market, now_ms):
                continue

            row = _market_row(
                market,
                now=now_iso,
                mode=refresh_mode,
            )
            if row is not None:
                if row["market_id"] in rows_by_id:
                    duplicate_markets += 1
                rows_by_id[row["market_id"]] = row
                active_received += 1
                active_binary_received += 1

        print(
            f"[manifold:{refresh_mode}] page={page_number} "
            f"received={len(markets):,} "
            f"active_binary={active_received:,} "
            f"unique={len(rows_by_id):,}",
            flush=True,
        )

        if maximum_rows and len(rows_by_id) >= maximum_rows:
            termination_reason = "row_cap_reached"
            break
        if not markets:
            termination_reason = "empty_page"
            complete = True
            break
        if len(markets) < page_size:
            termination_reason = "final_short_page"
            complete = True
            break

        last_market = markets[-1] if markets else {}
        before = str(
            last_market.get("id") or ""
            if isinstance(last_market, dict)
            else ""
        )
        if not before:
            termination_reason = "missing_before_cursor"
            break

        if REQUEST_SLEEP_SECONDS:
            time.sleep(REQUEST_SLEEP_SECONDS)
    else:
        termination_reason = "page_limit_reached"

    rows = list(rows_by_id.values())
    if maximum_rows:
        rows = rows[:maximum_rows]

    if not discovery:
        complete = False
        termination_reason = "fast_limit"

    _set_diagnostics(
        endpoint="/v0/markets",
        mode=refresh_mode,
        pages_fetched=pages_fetched,
        page_size=page_size,
        maximum_pages=maximum_pages,
        maximum_rows=maximum_rows,
        raw_markets_received=raw_markets_received,
        active_binary_received=active_binary_received,
        unique_markets=len(rows),
        duplicate_markets=duplicate_markets,
        last_page_size=last_page_size,
        termination_reason=termination_reason,
        complete=complete,
        page_limit_reached=(
            termination_reason == "page_limit_reached"
        ),
    )
    return rows


if __name__ == "__main__":
    output = fetch_manifold_markets(mode="fast")
    print(f"Fetched Manifold markets: {len(output):,}")
