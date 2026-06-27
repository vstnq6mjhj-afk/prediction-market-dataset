import requests
from datetime import datetime, timezone
from connectors.title_utils import normalize_title

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


def fetch_predictit_markets():
    url = "https://www.predictit.org/api/marketdata/all/"
    rows = []
    now = datetime.now(timezone.utc).isoformat()

    try:
        data = requests.get(url, timeout=20).json()
    except Exception as e:
        print(f"PredictIt fetch failed: {e}")
        return rows

    for market in data.get("markets", []):
        contracts = market.get("contracts", [])

        for contract in contracts:
            yes_price = _price(
                contract.get("lastTradePrice"),
                contract.get("bestBuyYesCost"),
                contract.get("bestSellYesCost"),
            )

            no_price = _price(
                contract.get("bestBuyNoCost"),
                contract.get("bestSellNoCost"),
            )

            if no_price == 0.0 and yes_price:
                no_price = round(1 - yes_price, 4)

            previous_price = _price(contract.get("lastClosePrice"))
            price_change = round(abs(yes_price - previous_price), 4) if previous_price else 0.0
            title = f"{market.get('name','')} | {contract.get('name','')}"

            rows.append({
                "platform": "predictit",
                "market_id": str(contract.get("id")),
                "title": title,
                "canonical_title": normalize_title(title),
                "category": "politics",
                "start_date": None,
                "close_date": market.get("timeStamp"),
                "resolution_date": None,
                "status": "open" if contract.get("status") == "Open" else "closed",
                "outcome": None,
                "resolution_source": market.get("url") or "",
                "raw_url": market.get("url") or f"https://www.predictit.org/markets/detail/{market.get('id')}",
                "volume": 0.0,
                "liquidity": 0.0,
                "yes_price": yes_price,
                "no_price": no_price,
                "price_change": price_change,
                "volume_change": 0.0,
                "source": "predictit_api",
                "ingested_at": now,
                "snapshot_time": now,
            })

    return rows


if __name__ == "__main__":
    rows = fetch_predictit_markets()
    print(f"Fetched PredictIt contracts: {len(rows)}")
    for row in rows[:3]:
        print(row)