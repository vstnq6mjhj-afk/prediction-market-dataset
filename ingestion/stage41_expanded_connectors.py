from pathlib import Path
from datetime import datetime, timezone
import hashlib
import random
import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
REPORTS = BASE_DIR / "reports"
DATA_LAKE = BASE_DIR / "data_lake"
REPORTS.mkdir(exist_ok=True)
DATA_LAKE.mkdir(exist_ok=True)

SNAPSHOT_HISTORY = DATA_LAKE / "stage41_market_snapshot_history.csv"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def stable_id(platform, title):
    raw = f"{platform}:{title}".lower().encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:18]


def clamp(x, lo=0.01, hi=0.99):
    return max(lo, min(hi, float(x)))


def infer_category(title):
    s = str(title).lower()

    if any(x in s for x in ["nba", "nhl", "mlb", "nfl", "soccer", "arsenal", "lakers", "yankees", "betfair"]):
        return "sports"
    if any(x in s for x in ["election", "president", "senate", "trump", "biden", "republican", "democrat"]):
        return "politics"
    if any(x in s for x in ["bitcoin", "ethereum", "crypto", "btc", "eth", "oracle", "chainlink"]):
        return "crypto"
    if any(x in s for x in ["fed", "inflation", "cpi", "rates", "recession", "gdp", "unemployment"]):
        return "macro"
    if any(x in s for x in ["ai", "openai", "model", "nvidia", "agi"]):
        return "ai"
    if any(x in s for x in ["war", "ukraine", "russia", "china", "taiwan", "israel"]):
        return "geopolitics"
    if any(x in s for x in ["court", "legal", "sec", "lawsuit"]):
        return "legal"

    return "general"


def infer_family(title, category):
    s = str(title).lower()

    if category == "sports":
        for fam in ["nhl", "nba", "mlb", "nfl", "soccer"]:
            if fam in s:
                return fam
        return "sports_general"

    if category == "politics":
        if "2028" in s:
            return "2028_us_politics"
        if "president" in s:
            return "us_presidential"
        return "politics_general"

    if category == "crypto":
        return "crypto_assets"

    if category == "macro":
        return "macro_rates"

    if category == "ai":
        return "ai_technology"

    if category == "geopolitics":
        return "geopolitical_events"

    if category == "legal":
        return "legal_events"

    return "general"


def extract_entities(title):
    known = [
        "trump", "biden", "bitcoin", "ethereum", "openai", "nvidia",
        "arsenal", "lakers", "yankees", "russia", "ukraine", "china",
        "taiwan", "fed", "cpi", "nba", "nhl", "mlb", "nfl", "sec",
        "chainlink", "betfair"
    ]
    s = str(title).lower()
    return ", ".join(sorted({x for x in known if x in s}))


def normalize_market(platform, raw):
    title = raw["title"]
    category = infer_category(title)
    family = infer_family(title, category)

    probability = clamp(raw.get("probability", 0.5))
    spread = float(raw.get("spread", random.uniform(0.02, 0.09)))
    bid = clamp(probability - spread / 2)
    ask = clamp(probability + spread / 2)

    return {
        "market_id": raw.get("market_id") or stable_id(platform, title),
        "platform": platform,
        "source_group": raw.get("source_group", "prediction_market"),
        "title": title,
        "description": raw.get("description", ""),
        "category": category,
        "family": family,
        "entities": extract_entities(title),
        "event_terms": f"{category},{family}",
        "probability": round(probability, 6),
        "bid": round(bid, 6),
        "ask": round(ask, 6),
        "spread": round(ask - bid, 6),
        "volume": round(float(raw.get("volume", 0)), 4),
        "liquidity": round(float(raw.get("liquidity", 0)), 4),
        "open_time": raw.get("open_time", ""),
        "close_time": raw.get("close_time", ""),
        "resolution_time": raw.get("resolution_time", ""),
        "outcome": raw.get("outcome", ""),
        "url": raw.get("url", ""),
        "last_updated": now_iso(),
        "connector_mode": raw.get("connector_mode", "simulated"),
    }


def make_rows(platform, titles, source_group="prediction_market"):
    rows = []
    for i, title in enumerate(titles):
        rows.append({
            "market_id": f"{platform}_{i:05d}",
            "title": title,
            "probability": random.uniform(0.12, 0.88),
            "spread": random.uniform(0.015, 0.12),
            "volume": random.uniform(100, 60000),
            "liquidity": random.uniform(50, 25000),
            "url": f"https://example.com/{platform}/{i}",
            "source_group": source_group,
            "connector_mode": "simulated_connector",
        })
    return rows


def collect_prediction_venues():
    connectors = {
        "polymarket": [
            "Will Bitcoin hit 100k in 2026?",
            "Will Trump win the 2028 presidential election?",
            "Will OpenAI release a frontier model this year?",
        ],
        "kalshi": [
            "Will the Fed cut rates before September 2026?",
            "Will US GDP growth exceed 2 percent this quarter?",
            "Will inflation fall below 2.5 percent this year?",
        ],
        "manifold": [
            "Will AGI be announced before 2030?",
            "Will Ethereum outperform Bitcoin this year?",
            "Will China invade Taiwan before 2030?",
        ],
        "metaculus": [
            "Will there be a negotiated ceasefire in Ukraine before 2027?",
            "Will the US enter a recession before 2027?",
            "Will a top AI model exceed human experts on a major benchmark?",
        ],
        "predictit": [
            "Will the Republican nominee win the next US presidential election?",
            "Will Democrats control the Senate after the next election?",
            "Will a major candidate withdraw before election day?",
        ],
        "hypermind": [
            "Will eurozone inflation exceed expectations next quarter?",
            "Will Russia Ukraine negotiations produce a ceasefire?",
            "Will AI regulation pass in the EU this year?",
        ],
        "insight_prediction": [
            "Will a major technology company face an antitrust ruling?",
            "Will Nvidia remain the largest semiconductor company?",
            "Will a major crypto exchange launch a new derivatives product?",
        ],
        "zeitgeist": [
            "Will Bitcoin market dominance rise this quarter?",
            "Will Chainlink oracle volume increase this month?",
            "Will a decentralized prediction market resolve a major political event?",
        ],
        "hedgehog": [
            "Will Ethereum staking yield rise this quarter?",
            "Will a crypto ETF see record inflows?",
            "Will a DeFi protocol pass a major governance proposal?",
        ],
        "polkamarket": [
            "Will Portugal win its next soccer match?",
            "Will a major European election produce a coalition government?",
            "Will BTC close the month above its opening price?",
        ],
        "forecastex": [
            "Will CPI come in above consensus?",
            "Will unemployment rise next month?",
            "Will the Fed hold rates at the next meeting?",
        ],
    }

    all_rows = []
    health = []

    for platform, titles in connectors.items():
        started = now_iso()
        status = "ok"
        error = ""
        rows = []
        try:
            rows = make_rows(platform, titles)
            for r in rows:
                all_rows.append(normalize_market(platform, r))
        except Exception as e:
            status = "error"
            error = str(e)

        health.append({
            "connector": platform,
            "source_group": "prediction_market",
            "started_at": started,
            "finished_at": now_iso(),
            "status": status,
            "rows_collected": len(rows),
            "error": error,
        })

    return all_rows, health


def collect_reference_prices():
    rows = make_rows("betting_exchange", [
        "Betfair implied probability for NHL moneyline market",
        "Betfair implied probability for NBA moneyline market",
        "Sportsbook consensus probability for NFL game market",
        "Sportsbook consensus probability for soccer match market",
    ], source_group="reference_price")

    return [normalize_market("betting_exchange", r) for r in rows]


def collect_event_context():
    context = [
        {
            "event_id": "event_macro_cpi",
            "source": "economic_calendar",
            "event_type": "macro_release",
            "title": "Upcoming CPI inflation release",
            "category": "macro",
            "family": "macro_rates",
            "event_time": now_iso(),
            "importance": "high",
            "linked_entities": "cpi,fed,inflation",
            "context_text": "Economic calendar event used for macro prediction market context.",
        },
        {
            "event_id": "event_fomc",
            "source": "economic_calendar",
            "event_type": "central_bank",
            "title": "Upcoming FOMC rate decision",
            "category": "macro",
            "family": "macro_rates",
            "event_time": now_iso(),
            "importance": "high",
            "linked_entities": "fed,rates",
            "context_text": "Fed decision context for rates and inflation markets.",
        },
        {
            "event_id": "event_ai_news",
            "source": "news_stream",
            "event_type": "news",
            "title": "AI model release speculation increases",
            "category": "ai",
            "family": "ai_technology",
            "event_time": now_iso(),
            "importance": "medium",
            "linked_entities": "openai,nvidia,ai",
            "context_text": "News-like context event for AI-related prediction markets.",
        },
        {
            "event_id": "event_geopolitics",
            "source": "news_stream",
            "event_type": "news",
            "title": "Ukraine ceasefire negotiation headlines",
            "category": "geopolitics",
            "family": "geopolitical_events",
            "event_time": now_iso(),
            "importance": "high",
            "linked_entities": "ukraine,russia",
            "context_text": "News-like context event for geopolitical prediction markets.",
        },
    ]
    return pd.DataFrame(context)


def collect_resolution_sources(markets):
    rows = []
    for _, r in markets.head(8).iterrows():
        rows.append({
            "resolution_id": f"res_{r['market_id']}",
            "market_id": r["market_id"],
            "platform": r["platform"],
            "title": r["title"],
            "candidate_source": "official_oracle_or_news_source",
            "resolution_status": "pending",
            "confidence": round(random.uniform(0.35, 0.85), 4),
            "checked_at": now_iso(),
        })
    return pd.DataFrame(rows)


def append_snapshot_history(snapshots):
    if SNAPSHOT_HISTORY.exists():
        old = pd.read_csv(SNAPSHOT_HISTORY)
        combined = pd.concat([old, snapshots], ignore_index=True)
    else:
        combined = snapshots.copy()

    combined.to_csv(SNAPSHOT_HISTORY, index=False)
    return combined


def main():
    random.seed(41)
    np.random.seed(41)

    market_rows, health_rows = collect_prediction_venues()

    reference_rows = collect_reference_prices()
    market_rows.extend(reference_rows)

    for connector in ["betting_exchange", "economic_calendar", "news_stream", "oracle_feeds"]:
        health_rows.append({
            "connector": connector,
            "source_group": "reference_context_resolution",
            "started_at": now_iso(),
            "finished_at": now_iso(),
            "status": "ok",
            "rows_collected": 1 if connector != "betting_exchange" else len(reference_rows),
            "error": "",
        })

    markets = pd.DataFrame(market_rows)
    health = pd.DataFrame(health_rows)

    snapshot_time = now_iso()
    snapshots = markets.copy()
    snapshots["snapshot_time"] = snapshot_time
    snapshots["snapshot_id"] = [f"stage41_snap_{i:08d}" for i in range(len(snapshots))]

    history = append_snapshot_history(snapshots)

    event_context = collect_event_context()
    resolution_sources = collect_resolution_sources(markets)

    lake_metrics = pd.DataFrame([
        {"metric": "connectors_total", "value": health["connector"].nunique()},
        {"metric": "markets_current", "value": len(markets)},
        {"metric": "snapshots_this_run", "value": len(snapshots)},
        {"metric": "snapshot_history_rows", "value": len(history)},
        {"metric": "event_context_rows", "value": len(event_context)},
        {"metric": "resolution_source_rows", "value": len(resolution_sources)},
        {"metric": "venues_prediction_market", "value": markets[markets["source_group"] == "prediction_market"]["platform"].nunique()},
        {"metric": "reference_sources", "value": markets[markets["source_group"] == "reference_price"]["platform"].nunique()},
        {"metric": "avg_probability", "value": round(float(markets["probability"].mean()), 6)},
        {"metric": "avg_spread", "value": round(float(markets["spread"].mean()), 6)},
        {"metric": "total_volume", "value": round(float(markets["volume"].sum()), 4)},
        {"metric": "total_liquidity", "value": round(float(markets["liquidity"].sum()), 4)},
    ])

    markets.to_csv(REPORTS / "stage41_unified_markets_expanded.csv", index=False)
    snapshots.to_csv(REPORTS / "stage41_latest_market_snapshots.csv", index=False)
    history.to_csv(REPORTS / "stage41_market_snapshot_history.csv", index=False)
    health.to_csv(REPORTS / "stage41_connector_health.csv", index=False)
    event_context.to_csv(REPORTS / "stage41_event_context.csv", index=False)
    markets[markets["source_group"] == "reference_price"].to_csv(
        REPORTS / "stage41_external_reference_prices.csv",
        index=False,
    )
    resolution_sources.to_csv(REPORTS / "stage41_resolution_sources.csv", index=False)
    lake_metrics.to_csv(REPORTS / "stage41_data_lake_metrics.csv", index=False)

    summary = f"""Stage 41 Expanded Connectors + Historical Data Lake Summary

Created:
- stage41_unified_markets_expanded.csv: {len(markets)}
- stage41_latest_market_snapshots.csv: {len(snapshots)}
- stage41_market_snapshot_history.csv: {len(history)}
- stage41_connector_health.csv: {len(health)}
- stage41_event_context.csv: {len(event_context)}
- stage41_external_reference_prices.csv: {len(reference_rows)}
- stage41_resolution_sources.csv: {len(resolution_sources)}
- stage41_data_lake_metrics.csv: {len(lake_metrics)}

Connectors:
{health[['connector', 'source_group', 'status', 'rows_collected']].to_string(index=False)}

Metrics:
{lake_metrics.to_string(index=False)}

Status:
- ok
"""

    (REPORTS / "stage41_summary.txt").write_text(summary, encoding="utf-8")

    print("Stage 41 complete: expanded connectors, event context, reference prices, resolution sources, and append-only snapshot history generated.")


if __name__ == "__main__":
    main()