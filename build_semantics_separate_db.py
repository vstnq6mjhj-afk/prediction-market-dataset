from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

import duckdb


WAREHOUSE_PATH = Path(os.getenv("DB_PATH", "/var/data/warehouse.duckdb"))
SEMANTICS_PATH = Path(
    os.getenv("SEMANTICS_DB_PATH", "/var/data/market_semantics.duckdb")
)
PARSER_VERSION = "semantic-live-separate-db-v3"


COUNTRY_ALIASES = {
    "usa": "united_states",
    "u s": "united_states",
    "us": "united_states",
    "united states of america": "united_states",
    "united states": "united_states",
    "uk": "united_kingdom",
    "u k": "united_kingdom",
    "great britain": "united_kingdom",
    "britain": "united_kingdom",
    "south korea": "south_korea",
    "republic of korea": "south_korea",
    "north korea": "north_korea",
    "czech republic": "czechia",
    "uae": "united_arab_emirates",
}

ENTITY_PREFIXES = (
    "team ",
    "the team ",
    "national team ",
    "the national team ",
)


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9%$.\s|:'-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def slug(value: Any) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", normalize_text(value))[:24])


def extract_year(*values: Any) -> Optional[str]:
    for value in values:
        text = normalize_text(value)
        match = re.search(r"\b(20\d{2})\b", text)
        if match:
            return match.group(1)

        year = getattr(value, "year", None)
        if year:
            return str(year)

    return None


def normalize_entity(value: Any) -> Optional[str]:
    text = normalize_text(value)
    if not text:
        return None

    for prefix in ENTITY_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break

    text = re.sub(r"\b(?:mens|women s|womens)\s+national\s+team\b", "", text)
    text = re.sub(r"\bnational\s+team\b", "", text)
    text = re.sub(r"\bteam\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -")

    return COUNTRY_ALIASES.get(text, slug(text)) or None


def normalize_competition(value: Any, year: Optional[str]) -> Optional[str]:
    text = normalize_text(value)
    if not text:
        return None

    if "world cup" in text:
        base = "fifa_world_cup"
    elif "champions league" in text:
        base = "uefa_champions_league"
    elif "europa league" in text:
        base = "uefa_europa_league"
    elif "premier league" in text:
        base = "english_premier_league"
    elif "presidential election" in text:
        base = "presidential_election"
    elif "midterm election" in text:
        base = "midterm_election"
    elif "democratic nomination" in text:
        base = "democratic_nomination"
    elif "republican nomination" in text:
        base = "republican_nomination"
    else:
        base = slug(text)

    if year and year not in base:
        return f"{base}_{year}"
    return base or None


def parse_range_contract(text: str):
    value = normalize_text(text)

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s+or\s+fewer", value)
    if match:
        return "lte", None, float(match.group(1))

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s+or\s+(?:more|greater)", value)
    if match:
        return "gte", float(match.group(1)), None

    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s+(?:to|-)\s+(\d+(?:\.\d+)?)",
        value,
    )
    if match:
        return "between", float(match.group(1)), float(match.group(2))

    return None, None, None


def infer(row: dict[str, Any]) -> tuple[Any, ...]:
    platform = normalize_text(row["platform"])
    market_id = str(row["market_id"])
    raw_title = str(row.get("title") or "").strip()
    title = normalize_text(raw_title)
    year = extract_year(
        raw_title,
        row.get("start_date"),
        row.get("resolution_date"),
        row.get("close_date"),
        row.get("close_time"),
    )

    event_type = "unknown"
    outcome_type = "binary"
    primary: Optional[str] = None
    secondary: Optional[str] = None
    competition: Optional[str] = None
    target: Optional[str] = None
    operator: Optional[str] = None
    lower: Optional[float] = None
    upper: Optional[float] = None
    confidence = 0.20
    matchable = False
    notes = "No reliable deterministic semantic pattern found."

    # Kalshi multi-leg products must never be treated as a single-market equivalent.
    if platform == "kalshi" and (
        "multigame" in market_id.lower()
        or title.startswith("yes ")
        or ",yes " in title
    ):
        event_type = "multi_leg_parlay"
        outcome_type = "multi_leg"
        target = "all_legs_yes"
        confidence = 0.99
        notes = "Kalshi multi-leg market excluded from automatic matching."

    # PredictIt parent question and contract are separated by a pipe.
    elif platform == "predictit" and "|" in raw_title:
        parent, contract = [part.strip() for part in raw_title.split("|", 1)]
        operator, lower, upper = parse_range_contract(contract)
        primary = slug(parent)
        target = slug(contract)

        if operator:
            event_type = "range_contract"
            outcome_type = "range"
            confidence = 0.96
            matchable = True
            notes = "PredictIt numeric range contract parsed."
        else:
            event_type = "contract_outcome"
            confidence = 0.75
            notes = "PredictIt contract parsed but requires review."

    else:
        # Head-to-head winner:
        # "Will Spain beat France in the World Cup semifinal?"
        # "Spain to beat France in the World Cup semifinal"
        head_to_head_patterns = [
            r"will\s+(.+?)\s+(?:beat|defeat)\s+(.+?)(?:\s+in\s+(.+?))?(?:\?|$)",
            r"(.+?)\s+to\s+(?:beat|defeat)\s+(.+?)(?:\s+in\s+(.+?))?(?:\?|$)",
        ]

        for pattern in head_to_head_patterns:
            match = re.search(pattern, title)
            if match:
                event_type = "head_to_head"
                primary = normalize_entity(match.group(1))
                secondary = normalize_entity(match.group(2))
                competition = normalize_competition(match.group(3), year)
                target = "match_winner"
                confidence = 0.93
                matchable = bool(primary and secondary)
                notes = "Directional head-to-head market parsed."
                break

        # Tie/draw market.
        if event_type == "unknown":
            match = re.search(
                r"will\s+(.+?)\s+and\s+(.+?)\s+be\s+(?:tied|a draw)",
                title,
            )
            if match:
                event_type = "head_to_head"
                pair = sorted(
                    filter(
                        None,
                        [
                            normalize_entity(match.group(1)),
                            normalize_entity(match.group(2)),
                        ],
                    )
                )
                if len(pair) == 2:
                    primary, secondary = pair
                    target = "tie"
                    confidence = 0.94
                    matchable = True
                    notes = "Symmetric tie market parsed."

        # Event, tournament, election, or nomination winner.
        if event_type == "unknown":
            winner_patterns = [
                r"will\s+(.+?)\s+win\s+(?:the\s+)?(?:20\d{2}\s+)?(.+?)(?:\?|$)",
                r"(.+?)\s+to\s+win\s+(?:the\s+)?(?:20\d{2}\s+)?(.+?)(?:\?|$)",
                r"(.+?)\s+wins\s+(?:the\s+)?(?:20\d{2}\s+)?(.+?)(?:\?|$)",
            ]

            for pattern in winner_patterns:
                match = re.search(pattern, title)
                if not match:
                    continue

                primary = normalize_entity(match.group(1))
                competition_text = match.group(2).strip()
                competition = normalize_competition(competition_text, year)

                if "nomination" in competition_text:
                    event_type = "nomination_winner"
                elif "election" in competition_text:
                    event_type = "election_winner"
                elif any(
                    term in competition_text
                    for term in (
                        "world cup",
                        "championship",
                        "tournament",
                        "league",
                    )
                ):
                    event_type = "tournament_winner"
                else:
                    event_type = "event_winner"

                target = "winner"
                confidence = 0.91
                matchable = bool(primary and competition)
                notes = "Winner market parsed and aliases normalized."
                break

        # Relative deadline:
        # "New Rihanna album before GTA VI?"
        if event_type == "unknown":
            match = re.search(r"(.+?)\s+before\s+(.+?)(?:\?|$)", title)
            if match:
                event_type = "relative_deadline"
                primary = slug(match.group(1))
                secondary = slug(match.group(2))
                target = "occurs_before"
                confidence = 0.88
                matchable = bool(primary and secondary)
                notes = "Relative deadline market parsed."

    canonical_key = None
    if matchable:
        canonical_key = "|".join(
            [
                event_type,
                primary or "",
                secondary or "",
                competition or "",
                target or "",
                year or "",
                "" if lower is None else str(lower),
                "" if upper is None else str(upper),
            ]
        )

    return (
        platform,
        market_id,
        raw_title,
        title,
        event_type,
        outcome_type,
        primary,
        secondary,
        competition,
        target,
        operator,
        lower,
        upper,
        canonical_key,
        confidence,
        matchable,
        notes,
        row.get("raw_url"),
        row.get("yes_price"),
        row.get("no_price"),
        row.get("volume"),
        row.get("liquidity"),
        row.get("status"),
        row.get("snapshot_time"),
        PARSER_VERSION,
    )


CREATE_SQL = """
CREATE TABLE market_semantics_live (
    platform VARCHAR NOT NULL,
    market_id VARCHAR NOT NULL,
    raw_title VARCHAR,
    normalized_title VARCHAR,
    event_type VARCHAR,
    outcome_type VARCHAR,
    primary_entity VARCHAR,
    secondary_entity VARCHAR,
    competition VARCHAR,
    target VARCHAR,
    comparison_operator VARCHAR,
    lower_threshold DOUBLE,
    upper_threshold DOUBLE,
    canonical_key VARCHAR,
    extraction_confidence DOUBLE,
    is_matchable BOOLEAN,
    parse_notes VARCHAR,
    source_url VARCHAR,
    yes_price DOUBLE,
    no_price DOUBLE,
    volume DOUBLE,
    liquidity DOUBLE,
    status VARCHAR,
    snapshot_time TIMESTAMP WITH TIME ZONE,
    parser_version VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (platform, market_id)
)
"""


INSERT_SQL = """
INSERT INTO market_semantics_live (
    platform,
    market_id,
    raw_title,
    normalized_title,
    event_type,
    outcome_type,
    primary_entity,
    secondary_entity,
    competition,
    target,
    comparison_operator,
    lower_threshold,
    upper_threshold,
    canonical_key,
    extraction_confidence,
    is_matchable,
    parse_notes,
    source_url,
    yes_price,
    no_price,
    volume,
    liquidity,
    status,
    snapshot_time,
    parser_version,
    updated_at
)
VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    CURRENT_TIMESTAMP
)
"""


def main() -> None:
    if not WAREHOUSE_PATH.exists():
        raise FileNotFoundError(f"Warehouse not found: {WAREHOUSE_PATH}")

    temporary_path = SEMANTICS_PATH.with_suffix(".tmp.duckdb")
    if temporary_path.exists():
        temporary_path.unlink()

    connection = duckdb.connect(str(temporary_path))
    connection.execute("SET threads = 1")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET memory_limit = '192MB'")
    connection.execute(
        f"ATTACH '{WAREHOUSE_PATH.as_posix()}' AS warehouse (READ_ONLY)"
    )

    try:
        cursor = connection.execute(
            """
            WITH latest_per_platform AS (
                SELECT
                    platform,
                    MAX(snapshot_time) AS latest_snapshot
                FROM warehouse.market_snapshots
                WHERE platform IS NOT NULL
                GROUP BY platform
            )
            SELECT
                market.platform,
                market.market_id,
                market.title,
                market.category,
                market.start_date,
                market.close_date,
                market.resolution_date,
                market.close_time,
                market.raw_url,
                market.yes_price,
                market.no_price,
                market.volume,
                market.liquidity,
                market.status,
                market.snapshot_time
            FROM warehouse.market_snapshots AS market
            INNER JOIN latest_per_platform AS latest
              ON market.platform = latest.platform
             AND market.snapshot_time = latest.latest_snapshot
            WHERE market.market_id IS NOT NULL
            ORDER BY market.platform, market.market_id
            """
        )

        columns = [item[0] for item in cursor.description]
        records = [dict(zip(columns, row)) for row in cursor.fetchall()]
        parsed = [infer(record) for record in records]

        connection.execute(CREATE_SQL)
        for row in parsed:
            connection.execute(INSERT_SQL, row)

        connection.execute("CHECKPOINT")

        summary = connection.execute(
            """
            SELECT
                platform,
                COUNT(*) AS parsed,
                COUNT(*) FILTER (WHERE is_matchable = TRUE) AS matchable,
                MAX(snapshot_time) AS latest_snapshot
            FROM market_semantics_live
            GROUP BY platform
            ORDER BY platform
            """
        ).fetchall()

        total, matchable = connection.execute(
            """
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE is_matchable = TRUE)
            FROM market_semantics_live
            """
        ).fetchone()

        print(f"[semantics-db] Warehouse: {WAREHOUSE_PATH}")
        print(f"[semantics-db] Temporary output: {temporary_path}")
        print(f"[semantics-db] Parsed markets: {total:,}")
        print(f"[semantics-db] Matchable markets: {matchable:,}")

        for platform, parsed_count, matchable_count, latest_snapshot in summary:
            print(
                f"[semantics-db] {platform}: "
                f"parsed={parsed_count:,}, "
                f"matchable={matchable_count:,}, "
                f"latest={latest_snapshot}"
            )
    finally:
        connection.close()

    if SEMANTICS_PATH.exists():
        SEMANTICS_PATH.unlink()
    temporary_path.replace(SEMANTICS_PATH)

    print(f"[semantics-db] Published: {SEMANTICS_PATH}")
    print("[semantics-db] Complete")


if __name__ == "__main__":
    main()
