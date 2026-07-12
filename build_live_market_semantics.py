from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

import duckdb

DB_PATH = Path(os.getenv("DB_PATH", "/var/data/warehouse.duckdb"))
PARSER_VERSION = "semantic-live-v1"


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9%$.\s|:-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def slug(value: Any) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", normalize_text(value))[:20])


def parse_range_contract(text: str):
    value = normalize_text(text)

    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s+or\s+fewer", value)
    if m:
        return "lte", None, float(m.group(1))

    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s+or\s+(?:more|greater)", value)
    if m:
        return "gte", float(m.group(1)), None

    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s+(?:to|-)\s+(\d+(?:\.\d+)?)", value)
    if m:
        return "between", float(m.group(1)), float(m.group(2))

    return None, None, None


def infer(row: dict[str, Any]) -> tuple[Any, ...]:
    platform = normalize_text(row["platform"])
    market_id = str(row["market_id"])
    raw_title = str(row.get("title") or "").strip()
    title = normalize_text(raw_title)

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
    notes = "No reliable semantic pattern found."

    if platform == "kalshi" and (
        "multigame" in market_id.lower()
        or title.startswith("yes ")
        or ",yes " in title
    ):
        event_type = "multi_leg_parlay"
        outcome_type = "multi_leg"
        target = "all_legs_yes"
        confidence = 0.99
        notes = "Multi-leg market excluded from automatic matching."

    elif platform == "predictit" and "|" in raw_title:
        parent, contract = [part.strip() for part in raw_title.split("|", 1)]
        operator, lower, upper = parse_range_contract(contract)
        primary = normalize_text(parent)
        target = normalize_text(contract)

        if operator:
            event_type = "range_contract"
            outcome_type = "range"
            confidence = 0.96
            matchable = True
            notes = "PredictIt range contract parsed."
        else:
            event_type = "contract_outcome"
            confidence = 0.75
            notes = "PredictIt contract parsed but requires review."

    else:
        m = re.search(
            r"will\s+(.+?)\s+(?:beat|defeat)\s+(.+?)(?:\s+in\b|\s+during\b|\?|$)",
            title,
        )
        if m:
            event_type = "head_to_head"
            primary = m.group(1).strip()
            secondary = m.group(2).strip()
            target = "match_winner"
            confidence = 0.91
            matchable = True
            notes = "Head-to-head market parsed."

        if event_type == "unknown":
            m = re.search(r"will\s+(.+?)\s+and\s+(.+?)\s+be\s+tied", title)
            if m:
                event_type = "head_to_head"
                primary = m.group(1).strip()
                secondary = m.group(2).strip()
                target = "tie"
                confidence = 0.92
                matchable = True
                notes = "Head-to-head tie market parsed."

        if event_type == "unknown":
            m = re.search(
                r"will\s+(.+?)\s+win\s+(?:the\s+)?(?:20\d{2}\s+)?(.+?)(?:\?|$)",
                title,
            )
            if m:
                primary = m.group(1).strip()
                competition = m.group(2).strip()
                if "nomination" in competition:
                    event_type = "nomination_winner"
                elif "election" in competition:
                    event_type = "election_winner"
                elif any(x in competition for x in ("world cup", "championship", "tournament", "league")):
                    event_type = "tournament_winner"
                else:
                    event_type = "event_winner"
                target = "winner"
                confidence = 0.88
                matchable = True
                notes = "Winner market parsed."

        if event_type == "unknown":
            m = re.search(r"(.+?)\s+before\s+(.+?)(?:\?|$)", title)
            if m:
                event_type = "relative_deadline"
                primary = m.group(1).strip()
                secondary = m.group(2).strip()
                target = "occurs_before"
                confidence = 0.86
                matchable = True
                notes = "Relative deadline market parsed."

    canonical_key = None
    if matchable:
        canonical_key = "|".join(
            [
                event_type,
                slug(primary),
                slug(secondary),
                slug(competition),
                slug(target),
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
        row.get("snapshot_time"),
        PARSER_VERSION,
    )


CREATE_SQL = """
CREATE TABLE IF NOT EXISTS market_semantics_live (
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
    snapshot_time TIMESTAMP WITH TIME ZONE,
    parser_version VARCHAR,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (platform, market_id)
)
"""

INSERT_SQL = """
INSERT OR REPLACE INTO market_semantics_live (
    platform, market_id, raw_title, normalized_title, event_type, outcome_type,
    primary_entity, secondary_entity, competition, target, comparison_operator,
    lower_threshold, upper_threshold, canonical_key, extraction_confidence,
    is_matchable, parse_notes, source_url, snapshot_time, parser_version, updated_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
"""


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Warehouse not found: {DB_PATH}")

    connection = duckdb.connect(str(DB_PATH))
    connection.execute("SET threads = 1")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET memory_limit = '64MB'")

    try:
        latest_snapshot = connection.execute(
            "SELECT MAX(snapshot_time) FROM market_snapshots"
        ).fetchone()[0]

        cursor = connection.execute(
            """
            SELECT
                platform,
                market_id,
                title,
                raw_url,
                snapshot_time
            FROM market_snapshots
            WHERE snapshot_time = ?
              AND market_id IS NOT NULL
            """,
            [latest_snapshot],
        )

        columns = [item[0] for item in cursor.description]
        records = [dict(zip(columns, row)) for row in cursor.fetchall()]
        parsed = [infer(record) for record in records]

        connection.execute(CREATE_SQL)
        connection.execute("BEGIN TRANSACTION")
        connection.execute("DELETE FROM market_semantics_live")

        for start in range(0, len(parsed), 25):
            connection.executemany(INSERT_SQL, parsed[start:start + 25])

        connection.execute("COMMIT")
        connection.execute("CHECKPOINT")

        counts = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE is_matchable = TRUE) AS matchable
            FROM market_semantics_live
            """
        ).fetchone()

        print(f"[live-semantics] Latest snapshot: {latest_snapshot}")
        print(f"[live-semantics] Parsed markets: {counts[0]:,}")
        print(f"[live-semantics] Matchable markets: {counts[1]:,}")
        print("[live-semantics] Complete")
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
