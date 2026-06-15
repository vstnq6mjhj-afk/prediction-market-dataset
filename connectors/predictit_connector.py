import requests
from datetime import datetime, timezone

def fetch_predictit_markets():
    url = "https://www.predictit.org/api/marketdata/all/"
    rows = []

    try:
        data = requests.get(url, timeout=20).json()
    except Exception as e:
        print(f"PredictIt fetch failed: {e}")
        return rows

    now = datetime.now(timezone.utc).isoformat()

    for market in data.get("markets", []):
        for contract in market.get("contracts", []):
            yes_price = contract.get("lastTradePrice")

            rows.append({
                "snapshot_time": now,
                "platform": "predictit",
                "market_id": str(contract.get("id")),
                "title": f"{market.get('name')} | {contract.get('name')}",
                "category": "politics",
                "yes_price": yes_price,
                "no_price": 1 - yes_price if yes_price is not None else None,
                "volume": None,
                "liquidity": None,
                "status": "open" if market.get("status") == "Open" else "closed",
                "close_time": market.get("timeStamp"),
                "source": "predictit_api",
            })

    return rows