from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

import duckdb

DB_PATH = Path(os.getenv("DB_PATH", "/var/data/warehouse.duckdb"))
PARSER_VERSION = "semantic-latest-v1"

STOPWORDS = {
    "a", "an", "and", "are", "at", "be", "before", "by", "for", "from", "how",
    "in", "is", "of", "on", "or", "the", "to", "what", "when", "where", "which",
    "who", "will", "with", "would", "yes", "no",
}


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9%$.\s|-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def slug(value: Any) -> str:
    words = [
        word for word in re.findall(r"[a-z0-9]+", normalize_text(value))
        if word not in STOPWORDS
    ]
    return "_".join(words[:18])


def parse_range_contract(outcome_text: str):
    text = normalize_text(outcome_text)

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s+or\s+fewer", text)
    if match:
        return "lte", None, float(match.group(1))

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s+or\s+(?:more|greater)", text)
    if match:
        return "gte", float(match.group(1)), None

    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s+(?:to|-)\s+(\d+(?:\.\d+)?)", text)
    if match:
        return "between", float(match.group(1)), float(match.group(2))

    return None, None, None


def infer_semantics(row: dict[str, Any]) -> tuple[Any, ...]:
    platform = normalize_text(row["platform"])
    market_id = str(row["market_id"])
    raw_title = str(row.get("title") or "").strip()
    title = normalize_text(raw_title)

    event_type: Optional[str] = None
    outcome_type = "binary"
    primary: Optional[str] = None
    secondary: Optional[str] = None
    competition: Optional[str] = None
    target: Optional[str] = None
    operator: Optional[str] = None
    lower: Optional[float] = None
    upper: Optional[float] = None
    confidence = 0.25
    needs_review = True
    is_matchable = False
    parent_event_id: Optional[str] = None
    notes = "No reliable semantic pattern found."

    if platform == "kalshi" and (
        "multigame" in market_id.lower()
        or title.startswith("yes ")
        or title.count(",yes ") >= 1
    ):
        event_type = "multi_leg_parlay"
        outcome_type = "multi_leg"
        target = "all_legs_resolve_yes"
        confidence = 0.99
        notes = "Kalshi multi-leg market excluded from automatic matching."

    elif platform == "predictit" and "|" in raw_title:
        parent_raw, outcome_raw = [part.strip() for part in raw_title.split("|", 1)]
        operator, lower, upper = parse_range_contract(outcome_raw)
        parent_event_id_match = re.search(r"/detail/(\d+)/", str(row.get("raw_url") or ""))
        parent_event_id = parent_event_id_match.group(1) if parent_event_id_match else slug(parent_raw)

        if operator:
            event_type = "range_contract"
            outcome_type = "range"
            primary = slug(parent_raw)
            target = slug(outcome_raw)
            confidence = 0.96
            needs_review = False
            is_matchable = True
            notes = "PredictIt range contract parsed."
        else:
            event_type = "contract_outcome"
            primary = slug(parent_raw)
            target = slug(outcome_raw)
            confidence = 0.80
            notes = "PredictIt contract parsed; outcome needs review."

    else:
        match = re.search(
            r"will\s+(.+?)\s+(?:beat|defeat)\s+(.+?)(?:\s+in\b|\s+during\b|\?|$)",
            title,
        )
        if match:
            event_type = "head_to_head"
            primary = match.group(1).strip()
            secondary = match.group(2).strip()
            target = "match_winner"
            confidence = 0.91
            needs_review = False
            is_matchable = True
            notes = "Head-to-head market parsed."

        match_tie = re.search(r"will\s+(.+?)\s+and\s+(.+?)\s+be\s+tied", title)
        if not event_type and match_tie:
            event_type = "head_to_head"
            primary = match_tie.group(1).strip()
            secondary = match_tie.group(2).strip()
            target = "tie"
            confidence = 0.92
            needs_review = False
            is_matchable = True
            notes = "Head-to-head tie market parsed."

        match_winner = re.search(
            r"will\s+(.+?)\s+win\s+(?:the\s+)?(?:20\d{2}\s+)?(.+?)(?:\?|$)",
            title,
        )
        if not event_type and match_winner:
            primary = match_winner.group(1).strip()
            competition = match_winner.group(2).strip()
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
            needs_review = False
            is_matchable = True
            notes = "Winner market parsed."

        match_before = re.search(r"(.+?)\s+before\s+(.+?)(?:\?|$)", title)
        if not event_type and match_before:
            event_type = "relative_deadline"
            primary = match_before.group(1).strip()
            secondary = match_before.group(2).strip()
            target = "occurs_before"
            confidence = 0.86
            needs_review = False
            is_matchable = True
            notes = "Relative deadline market parsed."

    canonical_key = None
    event_key = None
    if is_matchable:
        base_parts = [
            normalize_text(row.get("category")) or "unknown",
            event_type or "unknown",
            slug(competition),
            slug(primary),
            slug(secondary),
            slug(target),
        ]
        event_key = "|".join(base_parts)
        canonical_key = "|".join(
            base_parts
            + [
                "" if lower is None else str(lower),
                "" if upper is None else str(upper),
            ]
        )

    return (
        platform,
        market_id,
        parent_event_id,
        None,
        normalize_text(row.get("category")) or None,
        event_type or "unknown",
        outcome_type,
        primary,
        secondary,
        competition,
        target,
        operator,
        None,
        None,
        row.get("start_date"),
        row.get("resolution_date"),
        row.get("resolution_source"),
        None,
        canonical_key,
        PARSER_VERSION,
        confidence,
        needs_review,
        raw_title,
        title,
        event_key,
        lower,
        upper,
        row.get("raw_url"),
        notes,
        is_matchable,
    )


INSERT_SQL = """
INSERT OR REPLACE INTO market_semantics (
    platform, market_id, parent_event_id, series_id, category, event_type,
    outcome_type, primary_entity, secondary_entity, competition, target,
    comparison_operator, threshold, unit, event_start, event_end,
    resolution_source, resolution_rule_hash, canonical_key, parser_version,
    extraction_confidence, needs_review, raw_title, normalized_title, event_key,
    lower_threshold, upper_threshold, source_url, parse_notes, is_matchable,
    created_at, updated_at
)
VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
)
"""


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DuckDB warehouse not found: {DB_PATH}")

    connection = duckdb.connect(str(DB_PATH))
    connection.execute("SET threads = 1")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET memory_limit = '128MB'")

    try:
        latest_snapshot = connection.execute(
            "SELECT MAX(snapshot_time) FROM market_snapshots"
        ).fetchone()[0]

        rows = connection.execute(
            """
            SELECT
                platform, market_id, title, category, start_date, resolution_date,
                resolution_source, raw_url
            FROM market_snapshots
            WHERE snapshot_time = ?
              AND market_id IS NOT NULL
            """,
            [latest_snapshot],
        ).fetchall()

        columns = [
            "platform", "market_id", "title", "category", "start_date",
            "resolution_date", "resolution_source", "raw_url"
        ]
        records = [dict(zip(columns, row)) for row in rows]
        parsed = [infer_semantics(row) for row in records]

        connection.execute("BEGIN TRANSACTION")
        connection.executemany(INSERT_SQL, parsed)
        connection.execute("COMMIT")

        matchable = connection.execute(
            """
            SELECT COUNT(*)
            FROM market_semantics
            WHERE parser_version = ?
              AND is_matchable = TRUE
            """,
            [PARSER_VERSION],
        ).fetchone()[0]

        print(f"[latest-semantics] Latest snapshot: {latest_snapshot}")
        print(f"[latest-semantics] Parsed markets: {len(parsed):,}")
        print(f"[latest-semantics] Matchable markets: {matchable:,}")
        print("[latest-semantics] Complete")
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
