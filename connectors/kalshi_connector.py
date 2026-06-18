import requests
from datetime import datetime, timezone

KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


def _num(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _price(*values):
    for value in values:
        n = _num(value, None)
        if n is None:
            continue
        if n > 1:
            n = n / 100.0
        return round(n, 4)
    return 0.0


def fetch_kalshi_markets(limit=100):
    url = f"{KALSHI_BASE_URL}/markets"
    params = {
        "limit": limit,
        "status": "open",
    }

    rows = []
    now = datetime.now(timezone.utc).isoformat()

    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        print(f"Kalshi fetch failed: {e}")
        return rows

    markets = payload.get("markets", [])

    for market in markets:
        yes_price = _price(
            market.get("yes_ask_dollars"),
            market.get("yes_bid_dollars"),
            market.get("yes_price"),
            market.get("yes_ask"),
            market.get("yes_bid"),
            market.get("last_price"),
            market.get("previous_yes_ask"),
            market.get("previous_yes_bid"),
        )

        no_price = _price(
            market.get("no_ask_dollars"),
            market.get("no_bid_dollars"),
            market.get("no_price"),
            market.get("no_ask"),
            market.get("no_bid"),
            market.get("previous_no_ask"),
            market.get("previous_no_bid"),
        )

        if no_price == 0.0 and yes_price:
            no_price = round(1 - yes_price, 4)

        volume = _num(
            market.get("volume_24h_fp")
            or market.get("volume_24h")
            or market.get("volume")
            or market.get("dollar_volume"),
            default=None,
        )

        liquidity = _num(
            market.get("liquidity_dollars")
            or market.get("liquidity")
            or market.get("open_interest"),
            default=None,
        )

        rows.append({
            "platform": "kalshi",
            "market_id": market.get("ticker") or market.get("event_ticker"),
            "title": market.get("title") or market.get("subtitle") or market.get("ticker"),
            "category": market.get("category") or "unknown",
            "start_date": market.get("open_time"),
            "close_date": market.get("close_time"),
            "resolution_date": market.get("expected_expiration_time") or market.get("expiration_time"),
            "status": market.get("status") or "active",
            "outcome": None,
            "resolution_source": market.get("rules_primary") or market.get("rules_secondary"),
            "raw_url": f"https://kalshi.com/markets/{market.get('ticker')}",
            "volume": volume,
            "liquidity": liquidity,
            "yes_price": yes_price,
            "no_price": no_price,
            "source": "kalshi_api",
            "ingested_at": now,
            "snapshot_time": now,
        })

    return rows


if __name__ == "__main__":
    rows = fetch_kalshi_markets(limit=10)
    print(f"Fetched Kalshi markets: {len(rows)}")
    for row in rows[:3]:
        print(row)