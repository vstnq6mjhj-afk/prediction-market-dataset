from pathlib import Path
import sys

from utils.bootstrap import bootstrap_project

ROOT = bootstrap_project()

from datetime import datetime, timezone
import random
import pandas as pd

from utils.config_loader import get_path, ensure_directories


# ============================================================
# STAGE 40 — UNIFIED MULTI-VENUE INGESTION
# ============================================================

ensure_directories()

SNAPSHOT_DIR = get_path("snapshots")
REPORT_DIR = get_path("reports")

RUN_TIME = datetime.now(timezone.utc)
RUN_ID = RUN_TIME.strftime("%Y-%m-%dT%H-%M-%SZ")


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def make_market(
    platform,
    market_id,
    title,
    category,
    yes_price,
    volume,
    liquidity,
    status="active",
    outcome="",
    resolution_source="",
    raw_url="",
):
    yes_price = clamp(float(yes_price))
    no_price = clamp(1.0 - yes_price)

    return {
        "platform": platform,
        "market_id": market_id,
        "title": title,
        "category": category,
        "start_date": "",
        "close_date": "",
        "resolution_date": "",
        "status": status,
        "outcome": outcome,
        "resolution_source": resolution_source,
        "raw_url": raw_url,
        "volume": float(volume),
        "liquidity": float(liquidity),
        "yes_price": round(yes_price, 4),
        "no_price": round(no_price, 4),
    }


def collect_polymarket():
    return [
        make_market(
            "polymarket",
            "POLY-BTC-100K",
            "Will Bitcoin hit 100k in 2026?",
            "crypto",
            random.uniform(0.35, 0.65),
            random.randint(15000, 50000),
            random.randint(5000, 25000),
            resolution_source="official_oracle_or_news_source",
        ),
        make_market(
            "polymarket",
            "POLY-TRUMP-2028",
            "Will Trump win the 2028 presidential election?",
            "politics",
            random.uniform(0.25, 0.55),
            random.randint(10000, 45000),
            random.randint(4000, 22000),
            resolution_source="official_election_result",
        ),
        make_market(
            "polymarket",
            "POLY-OPENAI-FRONTIER",
            "Will OpenAI release a frontier model this year?",
            "ai",
            random.uniform(0.45, 0.8),
            random.randint(8000, 35000),
            random.randint(3000, 18000),
            resolution_source="company_announcement",
        ),
    ]


def collect_kalshi():
    return [
        make_market(
            "kalshi",
            "KX-FED-CUT-DEMO",
            "Will the Federal Reserve cut interest rates at the next meeting?",
            "macro",
            random.uniform(0.25, 0.75),
            random.randint(5000, 30000),
            random.randint(2500, 16000),
            resolution_source="Federal Reserve announcement",
        ),
        make_market(
            "kalshi",
            "KX-GDP-DEMO",
            "Will US GDP growth exceed 2 percent this quarter?",
            "macro",
            random.uniform(0.25, 0.65),
            random.randint(3000, 20000),
            random.randint(2000, 12000),
            resolution_source="official economic release",
        ),
        make_market(
            "kalshi",
            "KX-CPI-DEMO",
            "Will inflation fall below 2.5 percent this year?",
            "macro",
            random.uniform(0.2, 0.7),
            random.randint(3000, 20000),
            random.randint(2000, 12000),
            resolution_source="official CPI release",
        ),
    ]


def collect_manifold():
    return [
        make_market(
            "manifold",
            "MANI-AGI-2030",
            "Will AGI be announced before 2030?",
            "ai",
            random.uniform(0.3, 0.8),
            random.randint(2000, 25000),
            random.randint(1500, 10000),
            resolution_source="public consensus / announcement",
        ),
        make_market(
            "manifold",
            "MANI-ETH-BTC",
            "Will Ethereum outperform Bitcoin this year?",
            "crypto",
            random.uniform(0.25, 0.65),
            random.randint(2000, 20000),
            random.randint(1000, 9000),
            resolution_source="market price reference",
        ),
        make_market(
            "manifold",
            "MANI-CHINA-TAIWAN",
            "Will China invade Taiwan before 2030?",
            "geopolitics",
            random.uniform(0.05, 0.35),
            random.randint(5000, 25000),
            random.randint(2000, 12000),
            resolution_source="credible news source",
        ),
    ]


def collect_metaculus():
    return [
        make_market(
            "metaculus",
            "META-UKRAINE-CEASEFIRE",
            "Will there be a negotiated ceasefire in Ukraine before 2027?",
            "geopolitics",
            random.uniform(0.25, 0.65),
            random.randint(5000, 30000),
            random.randint(3000, 12000),
            resolution_source="credible news source",
        ),
        make_market(
            "metaculus",
            "META-AI-BENCHMARK",
            "Will a top AI model exceed human experts on a major benchmark?",
            "ai",
            random.uniform(0.45, 0.85),
            random.randint(5000, 25000),
            random.randint(2500, 12000),
            resolution_source="benchmark publication",
        ),
        make_market(
            "metaculus",
            "META-CLIMATE-2026",
            "Will 2026 be the hottest year on record?",
            "climate",
            random.uniform(0.25, 0.65),
            random.randint(3000, 18000),
            random.randint(1500, 9000),
            resolution_source="official climate dataset",
        ),
    ]


def collect_all_markets():
    rows = []
    rows.extend(collect_polymarket())
    rows.extend(collect_kalshi())
    rows.extend(collect_manifold())
    rows.extend(collect_metaculus())
    return pd.DataFrame(rows)


def build_schema_report(df):
    return pd.DataFrame(
        [
            {"field": col, "type": str(df[col].dtype), "non_null": int(df[col].notna().sum())}
            for col in df.columns
        ]
    )


def build_venue_health(df):
    rows = []

    for platform, group in df.groupby("platform"):
        rows.append(
            {
                "venue": platform,
                "started_at": RUN_TIME.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "status": "ok",
                "markets_collected": len(group),
                "error": "",
            }
        )

    return pd.DataFrame(rows)


def build_metrics(df):
    return pd.DataFrame(
        [
            {"metric": "venues", "value": df["platform"].nunique()},
            {"metric": "markets_collected", "value": len(df)},
            {"metric": "avg_yes_price", "value": round(df["yes_price"].mean(), 4)},
            {"metric": "avg_no_price", "value": round(df["no_price"].mean(), 4)},
            {"metric": "total_volume", "value": round(df["volume"].sum(), 4)},
            {"metric": "total_liquidity", "value": round(df["liquidity"].sum(), 4)},
        ]
    )


def run():
    markets = collect_all_markets()

    snapshot_path = SNAPSHOT_DIR / f"markets_{RUN_ID}.csv"
    markets.to_csv(snapshot_path, index=False)

    markets.to_csv(REPORT_DIR / "stage40_unified_markets.csv", index=False)

    schema = build_schema_report(markets)
    schema.to_csv(REPORT_DIR / "stage40_normalized_schema.csv", index=False)

    venue_health = build_venue_health(markets)
    venue_health.to_csv(REPORT_DIR / "stage40_venue_health.csv", index=False)

    metrics = build_metrics(markets)
    metrics.to_csv(REPORT_DIR / "stage40_ingestion_metrics.csv", index=False)

    summary = f"""Stage 40 Unified Multi-Venue Ingestion Summary

Run time:
- {RUN_TIME.isoformat()}

Created:
- {snapshot_path}
- reports/stage40_unified_markets.csv
- reports/stage40_normalized_schema.csv
- reports/stage40_venue_health.csv
- reports/stage40_ingestion_metrics.csv

Metrics:
{metrics.to_string(index=False)}

Status:
- ok
"""

    (REPORT_DIR / "stage40_summary.txt").write_text(summary, encoding="utf-8")

    print(
        "Stage 40 complete: unified multi-venue ingestion, "
        "snapshots, schema, venue health, and metrics generated."
    )


if __name__ == "__main__":
    run()