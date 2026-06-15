from pathlib import Path
import re
import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

MATCH_THRESHOLD = 0.34


SPORT_TEAMS = {
    "nba": [
        "lakers", "warriors", "celtics", "knicks", "nets", "bucks", "heat", "bulls",
        "pistons", "cavaliers", "spurs", "mavericks", "suns", "nuggets", "timberwolves",
        "clippers", "76ers", "sixers", "raptors", "pacers", "magic", "hawks", "hornets",
        "wizards", "rockets", "grizzlies", "pelicans", "jazz", "kings", "trail blazers",
        "thunder"
    ],
    "nhl": [
        "sabres", "bruins", "rangers", "islanders", "devils", "flyers", "penguins",
        "capitals", "hurricanes", "panthers", "lightning", "maple leafs", "senators",
        "canadiens", "red wings", "blackhawks", "blues", "predators", "stars", "wild",
        "avalanche", "kraken", "canucks", "oilers", "flames", "ducks", "kings", "sharks",
        "golden knights", "jets", "coyotes"
    ],
    "soccer": [
        "arsenal", "tottenham", "aston villa", "chelsea", "manchester", "liverpool",
        "barcelona", "real madrid", "milan", "roma", "inter", "bayern", "psg",
        "france", "brazil", "argentina", "germany", "england", "spain", "portugal",
        "japan", "morocco", "south korea", "uruguay", "ecuador", "cape verde",
        "bosnia", "congo", "uzbekistan", "haiti", "netherlands"
    ],
    "mlb": [
        "yankees", "mets", "dodgers", "giants", "red sox", "cubs", "white sox",
        "braves", "phillies", "padres", "mariners", "astros", "rangers", "blue jays",
        "orioles", "rays", "cardinals", "brewers", "twins", "guardians", "tigers",
        "royals", "athletics", "angels", "rockies", "diamondbacks", "pirates",
        "reds", "nationals", "marlins"
    ],
}


KNOWN_ENTITIES = [
    "trump", "kamala harris", "mark kelly", "josh shapiro", "rahm emanuel",
    "stephen a smith", "xi jinping", "gavin newsom", "cory booker",
    "john fetterman", "ruben gallego", "andy beshear", "jon ossoff",
    "jb pritzker", "raphael warnock", "jesus christ", "harvey weinstein",
    "gta vi", "gta 6", "playboi carti", "rihanna"
]


def read_csv_safe(name):
    path = REPORTS_DIR / name
    if path.exists():
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def clean_text(x):
    if pd.isna(x):
        return ""
    s = str(x)
    s = s.replace("\n", " ").replace("\t", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def normalize_piece(x):
    s = clean_text(x).lower()
    s = re.sub(r"^(yes|no)\s+", "", s)
    s = re.sub(r"\b(yes|no)\b", " ", s)
    s = re.sub(r"[\|;]+", ",", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" ,.-_")


def split_combo_title(title):
    raw = clean_text(title)

    parts = re.split(r",\s*(?=(yes|no)\b)", raw, flags=re.I)

    if len(parts) <= 1:
        parts = re.split(r"\s+(?=(yes|no)\b)", raw, flags=re.I)

    cleaned = []
    buffer = ""

    for p in parts:
        p = clean_text(p)
        if not p:
            continue

        if p.lower() in ["yes", "no"]:
            buffer = p
            continue

        if buffer:
            p = buffer + " " + p
            buffer = ""

        c = normalize_piece(p)
        if len(c) >= 4:
            cleaned.append(c)

    if not cleaned:
        cleaned = [normalize_piece(raw)]

    seen = set()
    out = []
    for c in cleaned:
        if c not in seen:
            seen.add(c)
            out.append(c)

    return out


def infer_league(text):
    s = text.lower()

    if "nba" in s:
        return "nba"
    if "nhl" in s or "stanley" in s:
        return "nhl"
    if "fifa" in s or "world cup" in s:
        return "soccer"
    if "mlb" in s or "runs" in s or "innings" in s:
        return "mlb"

    for league, names in SPORT_TEAMS.items():
        if any(name in s for name in names):
            return league

    return ""


def extract_entities(text):
    s = text.lower()
    found = []

    for entity in KNOWN_ENTITIES:
        if entity in s:
            found.append(entity)

    for league, names in SPORT_TEAMS.items():
        for name in names:
            if name in s:
                found.append(name)

    found = sorted(set(found))
    return found


def infer_category(text, league, entities):
    s = text.lower()

    if league:
        return "sports"

    if any(x in s for x in ["president", "election", "democratic", "republican", "senate", "congress", "nomination"]):
        return "politics"

    if any(x in s for x in ["china", "taiwan", "russia", "ukraine", "war", "nato", "xi jinping"]):
        return "geopolitics"

    if any(x in s for x in ["gta", "game", "playstation", "xbox", "nintendo"]):
        return "gaming"

    if any(x in s for x in ["bitcoin", "ethereum", "crypto", "solana"]):
        return "crypto"

    if any(x in s for x in ["fed", "rate", "inflation", "cpi", "gdp", "recession"]):
        return "macro"

    if any(x in s for x in ["court", "prison", "sentenced", "trial", "lawsuit"]):
        return "legal"

    if any(x in s for x in ["album", "movie", "oscars", "grammy"]):
        return "entertainment"

    if any(x in s for x in ["ai", "openai", "chatgpt", "nvidia"]):
        return "ai"

    if entities:
        return "general_entity"

    return "unknown"


def semantic_family(category, league, text):
    s = text.lower()

    if category == "sports":
        if league == "soccer":
            if "world cup" in s or "fifa" in s:
                return "soccer_world_cup"
            return "soccer_general"
        if league:
            return league
        return "sports_general"

    if category == "politics":
        if "2028" in s:
            return "2028_us_politics"
        return "politics_general"

    if category == "geopolitics":
        return "geopolitical_events"

    if category == "gaming":
        return "gaming_release"

    if category == "legal":
        return "legal_courts"

    if category == "entertainment":
        return "entertainment_media"

    if category == "macro":
        return "macro_rates"

    if category == "crypto":
        return "crypto_assets"

    if category == "ai":
        return "ai_technology"

    return "general"


def synthetic_title(parts, entities, league, category):
    if entities:
        ent = ", ".join(entities[:4])
        if league:
            return f"{league.upper()} market involving {ent}"
        return f"{category.replace('_', ' ').title()} market involving {ent}"

    if parts:
        p = parts[0]
        p = p[:120]
        return p[:1].upper() + p[1:]

    return "Unknown market"


def token_key(text):
    s = normalize_piece(text).lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\b(will|the|a|an|in|on|before|after|by|to|of|and|or|with|over|under|yes|no)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def jaccard(a, b):
    aa = set(token_key(a).split())
    bb = set(token_key(b).split())

    if not aa or not bb:
        return 0

    return len(aa & bb) / len(aa | bb)


def main():
    repaired = read_csv_safe("stage23_repaired_markets.csv")
    markets = read_csv_safe("markets.csv")

    if repaired.empty:
        if markets.empty:
            print("No repaired markets or markets.csv found.")
            return
        repaired = markets.copy()

    df = repaired.copy()

    if "platform" not in df.columns:
        df["platform"] = "unknown"

    if "market_id" not in df.columns:
        df["market_id"] = df.index.astype(str)

    if "raw_title" in df.columns:
        title_col = "raw_title"
    elif "title" in df.columns:
        title_col = "title"
    else:
        df["title"] = ""
        title_col = "title"

    parsed_rows = []

    for _, row in df.iterrows():
        platform = str(row.get("platform", "unknown")).lower()
        market_id = str(row.get("market_id", ""))
        raw_title = clean_text(row.get(title_col, ""))

        parts = split_combo_title(raw_title)
        combined = " ".join(parts)

        league = infer_league(combined)
        entities = extract_entities(combined)
        category = infer_category(combined, league, entities)
        family = semantic_family(category, league, combined)

        parsed_rows.append({
            "platform": platform,
            "market_id": market_id,
            "raw_title": raw_title,
            "combo_component_count": len(parts),
            "combo_components": " | ".join(parts[:12]),
            "primary_component": parts[0] if parts else "",
            "extracted_entities": ", ".join(entities),
            "entity_count": len(entities),
            "detected_league": league if league else "none",
            "stage24_category": category,
            "stage24_family": family,
            "synthetic_title": synthetic_title(parts, entities, league, category),
        })

    parsed = pd.DataFrame(parsed_rows)

    base_cols = [
        c for c in df.columns
        if c not in parsed.columns or c in ["platform", "market_id"]
    ]

    enriched = df.merge(parsed, on=["platform", "market_id"], how="left", suffixes=("", "_stage24"))

    enriched["stage24_quality_score"] = (
        np.where(enriched["stage24_category"].ne("unknown"), 40, 0)
        + np.where(enriched["entity_count"] > 0, 25, 0)
        + np.where(enriched["detected_league"].ne("none"), 20, 0)
        + np.where(enriched["combo_component_count"] > 0, 15, 0)
    )

    enriched["stage24_quality_label"] = np.select(
        [
            enriched["stage24_quality_score"] >= 85,
            enriched["stage24_quality_score"] >= 65,
            enriched["stage24_quality_score"] >= 40,
        ],
        ["strong_parse", "usable_parse", "weak_parse"],
        default="unparsed",
    )

    kalshi = enriched[enriched["platform"].eq("kalshi")].copy()
    poly = enriched[enriched["platform"].eq("polymarket")].copy()

    link_rows = []

    if not kalshi.empty and not poly.empty:
        poly_records = poly[
            [
                "platform",
                "market_id",
                "synthetic_title",
                "stage24_category",
                "stage24_family",
                "extracted_entities",
            ]
        ].to_dict("records")

        for _, k in kalshi.iterrows():
            best = None
            best_score = 0

            for p in poly_records:
                score = jaccard(k["synthetic_title"], p["synthetic_title"])

                k_entities = set(str(k.get("extracted_entities", "")).split(", "))
                p_entities = set(str(p.get("extracted_entities", "")).split(", "))
                k_entities.discard("")
                p_entities.discard("")

                if k_entities and p_entities:
                    overlap = len(k_entities & p_entities) / max(len(k_entities | p_entities), 1)
                    score += overlap * 0.35

                if k["stage24_category"] == p["stage24_category"]:
                    score += 0.10

                if k["stage24_family"] == p["stage24_family"]:
                    score += 0.10

                if score > best_score:
                    best_score = score
                    best = p

            if best and best_score >= MATCH_THRESHOLD:
                link_rows.append({
                    "platform_1": "kalshi",
                    "market_id_1": k["market_id"],
                    "title_1": k["synthetic_title"],
                    "platform_2": best["platform"],
                    "market_id_2": best["market_id"],
                    "title_2": best["synthetic_title"],
                    "stage24_category": k["stage24_category"],
                    "stage24_family": k["stage24_family"],
                    "match_score": round(best_score, 4),
                    "match_method": "stage24_entity_semantic_combo_parser",
                })

    links = pd.DataFrame(link_rows)

    category_summary = (
        enriched.groupby(["stage24_category", "stage24_family"], dropna=False)
        .agg(
            markets=("market_id", "count"),
            avg_components=("combo_component_count", "mean"),
            avg_entities=("entity_count", "mean"),
            avg_parse_quality=("stage24_quality_score", "mean"),
        )
        .reset_index()
        .sort_values("markets", ascending=False)
    )

    platform_summary = (
        enriched.groupby("platform", dropna=False)
        .agg(
            markets=("market_id", "count"),
            unknown_markets=("stage24_category", lambda x: (x == "unknown").sum()),
            avg_components=("combo_component_count", "mean"),
            avg_entities=("entity_count", "mean"),
            avg_parse_quality=("stage24_quality_score", "mean"),
        )
        .reset_index()
    )

    entity_summary = (
        enriched[enriched["extracted_entities"].astype(str).str.len() > 0]
        .assign(entity=enriched["extracted_entities"].astype(str).str.split(", "))
        .explode("entity")
    )

    if not entity_summary.empty:
        entity_summary = (
            entity_summary.groupby(["entity", "stage24_category"], dropna=False)
            .agg(
                markets=("market_id", "count"),
                avg_quality=("stage24_quality_score", "mean"),
            )
            .reset_index()
            .sort_values("markets", ascending=False)
        )
    else:
        entity_summary = pd.DataFrame(columns=["entity", "stage24_category", "markets", "avg_quality"])

    diagnostics = pd.DataFrame([
        {
            "metric": "total_markets",
            "value": len(enriched),
            "note": "All parsed market rows.",
        },
        {
            "metric": "unknown_after_stage24",
            "value": int((enriched["stage24_category"] == "unknown").sum()),
            "note": "Markets still uncategorized after combo/entity parsing.",
        },
        {
            "metric": "combo_markets_detected",
            "value": int((enriched["combo_component_count"] > 1).sum()),
            "note": "Markets with multiple parsed components.",
        },
        {
            "metric": "entity_markets_detected",
            "value": int((enriched["entity_count"] > 0).sum()),
            "note": "Markets with extracted teams/entities.",
        },
        {
            "metric": "stage24_cross_market_links",
            "value": len(links),
            "note": "Candidate Kalshi/Polymarket links from entity + semantic matching.",
        },
    ])

    enriched.to_csv(REPORTS_DIR / "stage24_parsed_markets.csv", index=False)
    links.to_csv(REPORTS_DIR / "stage24_cross_market_candidates.csv", index=False)
    category_summary.to_csv(REPORTS_DIR / "stage24_category_summary.csv", index=False)
    platform_summary.to_csv(REPORTS_DIR / "stage24_platform_summary.csv", index=False)
    entity_summary.to_csv(REPORTS_DIR / "stage24_entity_summary.csv", index=False)
    diagnostics.to_csv(REPORTS_DIR / "stage24_diagnostics.csv", index=False)

    summary = f"""Stage 24 Kalshi Parser + Entity Extraction Summary

Loaded:
- repaired markets: {len(repaired)}
- total parsed markets: {len(enriched)}

Created:
- stage24_parsed_markets.csv: {len(enriched)}
- stage24_cross_market_candidates.csv: {len(links)}
- stage24_category_summary.csv: {len(category_summary)}
- stage24_platform_summary.csv: {len(platform_summary)}
- stage24_entity_summary.csv: {len(entity_summary)}
- stage24_diagnostics.csv: {len(diagnostics)}

Results:
- unknown after stage24: {int((enriched["stage24_category"] == "unknown").sum())}
- combo markets detected: {int((enriched["combo_component_count"] > 1).sum())}
- entity markets detected: {int((enriched["entity_count"] > 0).sum())}
- cross-market candidates: {len(links)}

Status:
- ok
"""

    (REPORTS_DIR / "stage24_summary.txt").write_text(summary, encoding="utf-8")

    print("Stage 24 complete: Kalshi combo parsing, entity extraction, and cross-market candidates generated.")


if __name__ == "__main__":
    main()