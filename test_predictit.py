from connectors.predictit_connector import fetch_markets

markets = fetch_markets()

print("Total markets:", len(markets))

print(markets[0])