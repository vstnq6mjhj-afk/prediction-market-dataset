import requests
from datetime import datetime, timezone

MANIFOLD_URL = "https://api.manifold.markets/v0/markets"


def _to_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def fetch_manifold_markets(limit=100):
    try:
        data = requests.get(MANIFOLD_URL, timeout=20).json()
    except Exception as e:
        print(f"Manifold fetch failed: {e}")
        return []

    rows = []
    now = datetime.now(timezone.utc).isoformat()

    for m in data[:limit]:
        prob = _to_float(m.get("probability"))

        # Keep only binary/probability markets for now
        if prob is None:
            continue

        yes_price = round(prob, 4)
        no_price = round(1 - prob, 4)

        rows.append({
            "platform": "manifold",
            "market_id": str(m.get("id") or ""),
            "title": m.get("question") or m.get("title") or "",
            "category": (
                m.get("groupSlugs", ["unknown"])[0]
                if m.get("groupSlugs")
                else "unknown"
            ),
            "start_date": None,
            "close_date": m.get("closeTime"),
            "resolution_date": None,
            "status": "closed" if m.get("isResolved") else "open",
            "outcome": m.get("resolution"),
            "resolution_source": "",
            "raw_url": m.get("url") or "",
            "volume": _to_float(m.get("volume")) or 0,
            "liquidity": _to_float(m.get("totalLiquidity")) or 0,
            "yes_price": yes_price,
            "no_price": no_price,
            "source": "manifold_api",
            "ingested_at": now,
        })

    return rows


if __name__ == "__main__":
    rows = fetch_manifold_markets(limit=10)
    print(f"Fetched Manifold markets: {len(rows)}")
    for row in rows[:3]:
        print(row)