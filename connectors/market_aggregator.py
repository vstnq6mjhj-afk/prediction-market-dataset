from connectors.kalshi_connector import fetch_kalshi_markets
from connectors.polymarket_connector import fetch_polymarket_markets
from connectors.manifold_connector import fetch_manifold_markets
from connectors.metaculus_connector import fetch_metaculus_questions
from connectors.predictit_connector import fetch_predictit_markets

def aggregate_markets():
    rows = []

    for name, fn in [
        ("kalshi", fetch_kalshi_markets),
        ("polymarket", fetch_polymarket_markets),
        ("manifold", fetch_manifold_markets),
        ("metaculus", fetch_metaculus_questions),
        ("predictit", fetch_predictit_markets),
    ]:
        try:
            data = fn()
            print(f"{name}: {len(data)} rows")
            rows.extend(data)
        except Exception as e:
            print(f"{name} failed: {e}")

    return rows