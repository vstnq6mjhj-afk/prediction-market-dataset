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
PARSER_VERSION = "semantic-adapters-v5"


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



def classify_parent_question(parent: str, contract: str, year: Optional[str]):
    """Parse PredictIt parent-question + contract structures conservatively."""
    parent_text = normalize_text(parent)
    contract_entity = normalize_entity(contract)

    if not contract_entity:
        return None

    if "who will win" in parent_text or "who wins" in parent_text:
        competition_text = re.sub(r"^.*?who\s+(?:will\s+)?win\s+", "", parent_text)
        competition = normalize_competition(competition_text, year)
        if "nomination" in parent_text:
            event_type = "nomination_winner"
        elif "election" in parent_text:
            event_type = "election_winner"
        elif any(term in parent_text for term in ("world cup", "championship", "tournament", "league")):
            event_type = "tournament_winner"
        else:
            event_type = "event_winner"
        return event_type, contract_entity, competition, "winner", 0.93

    if "presidential nomination" in parent_text:
        return "nomination_winner", contract_entity, normalize_competition(parent_text, year), "winner", 0.92

    if "presidential election" in parent_text or "elected president" in parent_text:
        return "election_winner", contract_entity, normalize_competition(parent_text, year), "winner", 0.92

    return None


def parse_kalshi_ticker_context(market_id: str) -> tuple[Optional[str], Optional[str]]:
    """Extract only safe family hints from Kalshi ticker prefixes."""
    ticker = str(market_id or "").upper()
    if "PRES" in ticker or "ELECT" in ticker:
        return "election", None
    if "NOM" in ticker:
        return "nomination", None
    if "WORLD" in ticker or "WCUP" in ticker:
        return "world_cup", None
    if "NBA" in ticker:
        return "nba", None
    if "NFL" in ticker:
        return "nfl", None
    if "MLB" in ticker:
        return "mlb", None
    return None, None

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

    # Kalshi single-market titles are parsed with ticker family hints only as support.
    elif platform == "kalshi":
        family_hint, _ = parse_kalshi_ticker_context(market_id)

        kalshi_winner = re.search(
            r"(?:will\s+)?(.+?)\s+(?:win|wins|to win)\s+(?:the\s+)?(?:20\d{2}\s+)?(.+?)(?:\?|$)",
            title,
        )
        kalshi_yes_no = re.search(r"will\s+(.+?)(?:\?|$)", title)

        if kalshi_winner:
            primary = normalize_entity(kalshi_winner.group(1))
            competition_text = kalshi_winner.group(2).strip()
            competition = normalize_competition(competition_text, year)
            if "nomination" in competition_text or family_hint == "nomination":
                event_type = "nomination_winner"
            elif "election" in competition_text or family_hint == "election":
                event_type = "election_winner"
            elif any(term in competition_text for term in ("world cup", "championship", "tournament", "league")):
                event_type = "tournament_winner"
            else:
                event_type = "event_winner"
            target = "winner"
            confidence = 0.90
            matchable = bool(primary and competition)
            notes = "Kalshi single winner market parsed."
        elif kalshi_yes_no and family_hint in {"election", "nomination", "world_cup"}:
            # Keep as unsupported unless the title itself reveals a compatible outcome.
            event_type = "kalshi_binary"
            primary = normalize_entity(kalshi_yes_no.group(1))
            confidence = 0.55
            notes = "Kalshi ticker family identified, but outcome structure is not yet safe to match."

    # PredictIt parent question and contract are separated by a pipe.
    elif platform == "predictit" and "|" in raw_title:
        parent, contract = [part.strip() for part in raw_title.split("|", 1)]
        operator, lower, upper = parse_range_contract(contract)

        if operator:
            event_type = "range_contract"
            outcome_type = "range"
            primary = slug(parent)
            target = slug(contract)
            confidence = 0.96
            matchable = True
            notes = "PredictIt numeric range contract parsed."
        else:
            parsed_parent = classify_parent_question(parent, contract, year)
            if parsed_parent:
                event_type, primary, competition, target, confidence = parsed_parent
                matchable = True
                notes = "PredictIt parent event and contract outcome parsed."
            else:
                event_type = "contract_outcome"
                primary = slug(parent)
                target = slug(contract)
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

    exclusion_reason = None
    if not matchable:
        if event_type == "multi_leg_parlay":
            exclusion_reason = "multi_leg"
        elif event_type in {"unknown", "kalshi_binary", "contract_outcome"}:
            exclusion_reason = "unsupported_structure"
        else:
            exclusion_reason = "insufficient_fields"

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
        year,
        exclusion_reason,
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
    event_year VARCHAR,
    exclusion_reason VARCHAR,
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
    event_year,
    exclusion_reason,
    parser_version,
    updated_at
)
VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
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
            WITH latest_unique AS (
                SELECT platform,market_id,title,category,start_date,close_date,resolution_date,close_time,raw_url,yes_price,no_price,volume,liquidity,status,snapshot_time,
                       ROW_NUMBER() OVER(PARTITION BY platform,market_id ORDER BY snapshot_time DESC) market_rank
                FROM warehouse.market_snapshots
                WHERE market_id IS NOT NULL
                AND NOT (
                    LOWER(platform) = 'kalshi'
                    AND (
                        LOWER(market_id) LIKE '%multigame%'
                        OR LOWER(COALESCE(title, '')) LIKE 'yes %,%'
                        OR LOWER(COALESCE(title, '')) LIKE '%,yes %'
                    )
                )
            ), capped AS (
                SELECT *, ROW_NUMBER() OVER(PARTITION BY platform ORDER BY COALESCE(volume,0) DESC,snapshot_time DESC) platform_rank
                FROM latest_unique WHERE market_rank=1
            )
            SELECT
                platform,
                market_id,
                title,
                category,
                start_date,
                close_date,
                resolution_date,
                close_time,
                raw_url,
                yes_price,
                no_price,
                volume,
                liquidity,
                status,
                snapshot_time
            FROM capped
            WHERE platform_rank <=
                CASE
                    WHEN LOWER(platform) = 'kalshi' THEN 5000
                    ELSE 2000
                END
            ORDER BY platform, platform_rank
            """
        )

        columns = [item[0] for item in cursor.description]
        records = [dict(zip(columns, row)) for row in cursor.fetchall()]
        parsed = [infer(record) for record in records]

        connection.execute(CREATE_SQL)
        for row in parsed:
            connection.execute(INSERT_SQL, row)

        connection.execute(
            """
            CREATE TABLE matcher_diagnostics AS
            SELECT
                platform,
                COUNT(*) AS parsed,
                COUNT(*) FILTER (WHERE is_matchable = TRUE) AS matchable,
                COUNT(*) FILTER (WHERE exclusion_reason = 'multi_leg') AS excluded_multi_leg,
                COUNT(*) FILTER (WHERE exclusion_reason = 'unsupported_structure') AS unsupported_structure,
                COUNT(*) FILTER (WHERE exclusion_reason = 'insufficient_fields') AS insufficient_fields,
                MAX(snapshot_time) AS latest_snapshot
            FROM market_semantics_live
            GROUP BY platform
            """
        )
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
