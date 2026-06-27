import requests
from datetime import datetime, timezone
from connectors.title_utils import normalize_title

POLYMARKET_BASE_URL = "https://gamma-api.polymarket.com"


def _safe_float(value, default=0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _extract_yes_no_prices(market):
    yes_price = None
    no_price = None

    outcomes = market.get("outcomes")
    prices = market.get("outcomePrices")

    if isinstance(outcomes, str):
        try:
            import json
            outcomes = json.loads(outcomes)
        except Exception:
            outcomes = []

    if isinstance(prices, str):
        try:
            import json
            prices = json.loads(prices)
        except Exception:
            prices = []

    if isinstance(outcomes, list) and isinstance(prices, list):
        for outcome, price in zip(outcomes, prices):
            outcome_name = str(outcome).lower()

            if outcome_name == "yes":
                yes_price = _safe_float(price, None)

            if outcome_name == "no":
                no_price = _safe_float(price, None)

    return yes_price, no_price


def fetch_polymarket_markets(limit=100):
    url = f"{POLYMARKET_BASE_URL}/markets"

    params = {
        "active": "true",
        "closed": "false",
        "limit": limit,
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        markets = response.json()
    except Exception as e:
        print(f"Polymarket fetch failed: {e}")
        return []

    rows = []

    for market in markets:
        yes_price, no_price = _extract_yes_no_prices(market)
        title = market.get("question") or market.get("title")

        rows.append(
            {
                "platform": "polymarket",
                "market_id": market.get("conditionId") or market.get("id"),
                "title": title,
"canonical_title": normalize_title(title),
                "category": market.get("category", "unknown"),
                "start_date": market.get("startDate"),
                "close_date": market.get("endDate"),
                "resolution_date": market.get("resolutionDate"),
                "status": "active",
                "outcome": "",
                "resolution_source": market.get("resolutionSource"),
                "raw_url": market.get("slug"),
                "volume": _safe_float(market.get("volume"), 0),
                "liquidity": _safe_float(market.get("liquidity"), 0),
                "yes_price": yes_price,
                "no_price": no_price,
                "source": "polymarket_gamma_api",
                "ingested_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return rows


if __name__ == "__main__":
    rows = fetch_polymarket_markets(limit=10)
    print(f"Fetched Polymarket markets: {len(rows)}")
    for row in rows[:3]:
        print(row)