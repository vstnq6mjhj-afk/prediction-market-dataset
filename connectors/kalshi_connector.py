import requests
from datetime import datetime, timezone

KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


def _safe_price(*values):
    for value in values:
        try:
            if value is None:
                continue

            value = float(value)

            # cents -> dollars
            if value > 1:
                value = value / 100.0

            return round(value, 4)

        except Exception:
            continue

    return None


def _to_float(*values):
    for value in values:
        try:
            if value is None:
                continue
            return float(value)
        except Exception:
            continue

    return 0.0


def _first(*values):
    for v in values:
        if v is not None:
            return v
    return None


def fetch_kalshi_markets(limit=100):
    url = f"{KALSHI_BASE_URL}/markets"

    params = {
        "limit": limit,
        "status": "open",
    }

    try:
        response = requests.get(url, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()

    except Exception as e:
        print(f"Kalshi fetch failed: {e}")
        return []

    markets = payload.get("markets", [])
    rows = []

    now = datetime.now(timezone.utc).isoformat()

    for market in markets:

        yes_price = _safe_price(
            market.get("yes_ask_dollars"),
            market.get("yes_bid_dollars"),
            market.get("yes_ask"),
            market.get("yes_bid"),
            market.get("last_price"),
            market.get("price"),
        )

        no_price = _safe_price(
            market.get("no_ask_dollars"),
            market.get("no_bid_dollars"),
            market.get("no_ask"),
            market.get("no_bid"),
        )

        if no_price is None and yes_price is not None:
            no_price = round(1 - yes_price, 4)

        title = (
            market.get("title")
            or market.get("subtitle")
            or market.get("event_title")
            or market.get("ticker")
            or ""
        )

        rows.append({
            "platform": "kalshi",
            "market_id": market.get("event_ticker") or market.get("ticker"),
            "title": title,
            "category": market.get("category", "unknown"),
            "start_date": market.get("open_time"),
            "close_date": market.get("close_time"),
            "resolution_date": market.get("expiration_time"),
            "status": market.get("status", "active"),
            "outcome": None,
            "resolution_source": "",
            "raw_url": f"https://kalshi.com/markets/{market.get('ticker')}",
            "volume": _to_float(
                market.get("volume"),
                market.get("volume_24h")
            ),
            "liquidity": _to_float(
                market.get("liquidity"),
                market.get("open_interest"),
                market.get("volume")
            ),
            "yes_price": yes_price,
            "no_price": no_price,
            "source": "kalshi_api",
            "ingested_at": now,
        })

    return rows


if __name__ == "__main__":
    rows = fetch_kalshi_markets(limit=10)

    print(f"Fetched Kalshi markets: {len(rows)}")

    for row in rows[:3]:
        print(row)