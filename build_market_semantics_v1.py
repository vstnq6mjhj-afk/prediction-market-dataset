from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import duckdb


DB_PATH = Path(os.getenv("DB_PATH", "/var/data/warehouse.duckdb"))
PARSER_VERSION = "semantic-v1.1-low-memory"
BATCH_SIZE = 250

STOPWORDS = {
    "a", "an", "and", "are", "at", "be", "before", "by", "for", "from", "how",
    "in", "is", "of", "on", "or", "the", "to", "what", "when", "where", "which",
    "who", "will", "with", "would", "yes", "no",
}

SPORT_TERMS = {
    "world cup", "fifa", "uefa", "nba", "nfl", "nhl", "mlb", "ufc",
    "championship", "semifinal", "semifinals", "final", "finals",
    "match", "game", "tournament", "league", "medal",
}

POLITICS_TERMS = {
    "election", "president", "presidential", "governor", "senate", "house",
    "congress", "nomination", "nominee", "republican", "democratic",
    "democrat", "midterm", "prime minister",
}

CRYPTO_TERMS = {
    "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "xrp",
}

ENTERTAINMENT_TERMS = {
    "album", "movie", "film", "gta", "game release", "oscar", "grammy",
    "emmy", "box office",
}


@dataclass
class SemanticRecord:
    platform: str
    market_id: str
    parent_event_id: Optional[str]
    series_id: Optional[str]
    category: Optional[str]
    event_type: Optional[str]
    outcome_type: Optional[str]
    primary_entity: Optional[str]
    secondary_entity: Optional[str]
    competition: Optional[str]
    target: Optional[str]
    comparison_operator: Optional[str]
    threshold: Optional[float]
    unit: Optional[str]
    event_start: Optional[datetime]
    event_end: Optional[datetime]
    resolution_source: Optional[str]
    resolution_rule_hash: Optional[str]
    canonical_key: Optional[str]
    parser_version: str
    extraction_confidence: float
    needs_review: bool
    raw_title: str
    normalized_title: str
    event_key: Optional[str]
    lower_threshold: Optional[float]
    upper_threshold: Optional[float]
    source_url: Optional[str]
    parse_notes: str
    is_matchable: bool

    def as_tuple(self) -> tuple[Any, ...]:
        return (
            self.platform,
            self.market_id,
            self.parent_event_id,
            self.series_id,
            self.category,
            self.event_type,
            self.outcome_type,
            self.primary_entity,
            self.secondary_entity,
            self.competition,
            self.target,
            self.comparison_operator,
            self.threshold,
            self.unit,
            self.event_start,
            self.event_end,
            self.resolution_source,
            self.resolution_rule_hash,
            self.canonical_key,
            self.parser_version,
            self.extraction_confidence,
            self.needs_review,
            self.raw_title,
            self.normalized_title,
            self.event_key,
            self.lower_threshold,
            self.upper_threshold,
            self.source_url,
            self.parse_notes,
            self.is_matchable,
        )


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9%$.\s|-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def slug(value: Any) -> str:
    text = normalize_text(value)
    words = [
        word for word in re.findall(r"[a-z0-9]+", text)
        if word not in STOPWORDS
    ]
    return "_".join(words[:18])


def first_year(text: str, *dates: Any) -> Optional[str]:
    match = re.search(r"\b(20\d{2})\b", text)
    if match:
        return match.group(1)

    for value in dates:
        if isinstance(value, datetime):
            return str(value.year)
        year = getattr(value, "year", None)
        if year:
            return str(year)
    return None


def category_from(title: str, supplied: Any) -> Optional[str]:
    supplied_text = normalize_text(supplied)
    if supplied_text:
        return supplied_text

    if any(term in title for term in POLITICS_TERMS):
        return "politics"
    if any(term in title for term in SPORT_TERMS):
        return "sports"
    if any(term in title for term in CRYPTO_TERMS):
        return "crypto"
    if any(term in title for term in ENTERTAINMENT_TERMS):
        return "entertainment"
    return None


def infer_unit(parent_title: str, outcome_text: str) -> Optional[str]:
    combined = f"{parent_title} {outcome_text}"
    if "seat" in combined:
        return "seats"
    if "%" in combined or "percent" in combined:
        return "percent"
    if "$" in combined or "dollar" in combined:
        return "usd"
    if "goal" in combined:
        return "goals"
    if "run" in combined:
        return "runs"
    if "vote" in combined:
        return "votes"
    if "pageview" in combined or "page view" in combined:
        return "pageviews"
    return None


def parse_range_contract(outcome_text: str) -> tuple[Optional[str], Optional[float], Optional[float]]:
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


def event_hash(*parts: Any) -> Optional[str]:
    usable = [normalize_text(part) for part in parts if normalize_text(part)]
    if not usable:
        return None
    return hashlib.sha256("|".join(usable).encode("utf-8")).hexdigest()


def build_key(
    category: Optional[str],
    event_type: Optional[str],
    primary: Optional[str],
    secondary: Optional[str],
    competition: Optional[str],
    target: Optional[str],
    year: Optional[str],
    lower: Optional[float] = None,
    upper: Optional[float] = None,
) -> Optional[str]:
    required = [event_type, primary or competition]
    if any(not item for item in required):
        return None

    parts = [
        category or "unknown",
        event_type or "unknown",
        slug(competition),
        slug(primary),
        slug(secondary),
        slug(target),
        year or "",
        "" if lower is None else str(lower),
        "" if upper is None else str(upper),
    ]
    return "|".join(parts)


def parse_row(row: dict[str, Any]) -> SemanticRecord:
    platform = normalize_text(row.get("platform"))
    market_id = str(row.get("market_id") or "")
    raw_title = str(row.get("title") or "").strip()
    title = normalize_text(raw_title)
    category = category_from(title, row.get("category"))
    event_start = row.get("start_date")
    event_end = row.get("resolution_date")
    resolution_source = row.get("resolution_source")
    source_url = row.get("raw_url")
    year = first_year(title, event_start, event_end)

    parent_event_id: Optional[str] = None
    series_id: Optional[str] = None
    event_type: Optional[str] = None
    outcome_type: Optional[str] = "binary"
    primary: Optional[str] = None
    secondary: Optional[str] = None
    competition: Optional[str] = None
    target: Optional[str] = None
    operator: Optional[str] = None
    threshold: Optional[float] = None
    lower: Optional[float] = None
    upper: Optional[float] = None
    unit: Optional[str] = None
    confidence = 0.25
    needs_review = True
    is_matchable = False
    notes: list[str] = []

    # Kalshi multi-leg products are deliberately excluded from automatic matching.
    if platform == "kalshi" and (
        "multigame" in market_id.lower()
        or title.startswith("yes ")
        or title.count(",yes ") >= 1
    ):
        event_type = "multi_leg_parlay"
        outcome_type = "multi_leg"
        target = "all_legs_resolve_yes"
        confidence = 0.99
        notes.append("Kalshi multi-leg market; excluded from automatic cross-platform matching.")

    # PredictIt contracts: parent market title and contract outcome are separated by "|".
    elif platform == "predictit" and "|" in raw_title:
        parent_title_raw, outcome_raw = [part.strip() for part in raw_title.split("|", 1)]
        parent_title = normalize_text(parent_title_raw)
        outcome_text = normalize_text(outcome_raw)
        parent_event_id_match = re.search(r"/detail/(\d+)/", str(source_url or ""))
        parent_event_id = (
            parent_event_id_match.group(1)
            if parent_event_id_match
            else slug(parent_title_raw)
        )
        operator, lower, upper = parse_range_contract(outcome_text)
        unit = infer_unit(parent_title, outcome_text)

        if operator:
            event_type = "range_contract"
            outcome_type = "range"
            primary = slug(parent_title_raw)
            target = slug(outcome_raw)
            confidence = 0.96
            needs_review = False
            is_matchable = True
            notes.append("PredictIt parent market and range contract parsed separately.")
        else:
            event_type = "contract_outcome"
            primary = slug(parent_title_raw)
            target = slug(outcome_raw)
            confidence = 0.80
            notes.append("PredictIt contract parsed, but outcome was not a numeric range.")

        title = normalize_text(parent_title_raw)
        raw_title = f"{parent_title_raw} | {outcome_raw}"

    else:
        # Head-to-head or tie markets.
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
            notes.append("Head-to-head market parsed.")

        match_tie = re.search(r"will\s+(.+?)\s+and\s+(.+?)\s+be\s+tied", title)
        if not event_type and match_tie:
            event_type = "head_to_head"
            primary = match_tie.group(1).strip()
            secondary = match_tie.group(2).strip()
            target = "tie"
            confidence = 0.92
            needs_review = False
            is_matchable = True
            notes.append("Head-to-head tie market parsed.")

        # Tournament or competition winner.
        match_winner = re.search(
            r"will\s+(.+?)\s+win\s+(?:the\s+)?(?:20\d{2}\s+)?(.+?)(?:\?|$)",
            title,
        )
        if not event_type and match_winner:
            subject = match_winner.group(1).strip()
            event_name = match_winner.group(2).strip()

            if "nomination" in event_name:
                event_type = "nomination_winner"
                category = category or "politics"
            elif "election" in event_name:
                event_type = "election_winner"
                category = category or "politics"
            elif any(term in event_name for term in SPORT_TERMS):
                event_type = "tournament_winner"
                category = category or "sports"
            else:
                event_type = "event_winner"

            primary = subject
            competition = event_name
            target = "winner"
            confidence = 0.88
            needs_review = False
            is_matchable = True
            notes.append("Winner market parsed.")

        # Relative deadline: X before Y.
        match_before = re.search(r"(.+?)\s+before\s+(.+?)(?:\?|$)", title)
        if not event_type and match_before:
            event_type = "relative_deadline"
            primary = match_before.group(1).strip()
            secondary = match_before.group(2).strip()
            target = "occurs_before"
            confidence = 0.86
            needs_review = False
            is_matchable = True
            notes.append("Relative deadline market parsed.")

        # Threshold wording.
        threshold_patterns = [
            ("gte", r"(.+?)\s+(?:exceed|exceeds|reach|reaches|be above|be over|be at least)\s+\$?([\d,.]+)"),
            ("lte", r"(.+?)\s+(?:be below|be under|be at most|fall below)\s+\$?([\d,.]+)"),
        ]
        if not event_type:
            for op, pattern in threshold_patterns:
                match_threshold = re.search(pattern, title)
                if match_threshold:
                    event_type = "threshold"
                    primary = match_threshold.group(1).strip()
                    operator = op
                    threshold = float(match_threshold.group(2).replace(",", ""))
                    lower = threshold if op == "gte" else None
                    upper = threshold if op == "lte" else None
                    unit = infer_unit(title, "")
                    target = "numeric_threshold"
                    confidence = 0.84
                    needs_review = False
                    is_matchable = True
                    notes.append("Numeric threshold market parsed.")
                    break

    # Competition hints, used only when the parser has not already extracted one.
    if not competition:
        for phrase in (
            "fifa world cup",
            "world cup",
            "midterm election",
            "presidential election",
            "democratic nomination",
            "republican nomination",
        ):
            if phrase in title:
                competition = phrase
                break

    event_key = None
    canonical_key = None
    if is_matchable:
        event_key = build_key(
            category,
            event_type,
            primary,
            secondary,
            competition,
            target,
            year,
        )
        canonical_key = build_key(
            category,
            event_type,
            primary,
            secondary,
            competition,
            target,
            year,
            lower,
            upper,
        )

    resolution_rule_hash = event_hash(
        resolution_source,
        row.get("outcome"),
        event_type,
        target,
        operator,
        lower,
        upper,
    )

    return SemanticRecord(
        platform=platform,
        market_id=market_id,
        parent_event_id=parent_event_id,
        series_id=series_id,
        category=category,
        event_type=event_type or "unknown",
        outcome_type=outcome_type,
        primary_entity=primary,
        secondary_entity=secondary,
        competition=competition,
        target=target,
        comparison_operator=operator,
        threshold=threshold,
        unit=unit,
        event_start=event_start,
        event_end=event_end,
        resolution_source=resolution_source,
        resolution_rule_hash=resolution_rule_hash,
        canonical_key=canonical_key,
        parser_version=PARSER_VERSION,
        extraction_confidence=confidence,
        needs_review=needs_review,
        raw_title=raw_title,
        normalized_title=title,
        event_key=event_key,
        lower_threshold=lower,
        upper_threshold=upper,
        source_url=source_url,
        parse_notes=" ".join(notes) or "No reliable semantic pattern found.",
        is_matchable=is_matchable,
    )


INSERT_SQL = """
INSERT OR REPLACE INTO market_semantics (
    platform,
    market_id,
    parent_event_id,
    series_id,
    category,
    event_type,
    outcome_type,
    primary_entity,
    secondary_entity,
    competition,
    target,
    comparison_operator,
    threshold,
    unit,
    event_start,
    event_end,
    resolution_source,
    resolution_rule_hash,
    canonical_key,
    parser_version,
    extraction_confidence,
    needs_review,
    raw_title,
    normalized_title,
    event_key,
    lower_threshold,
    upper_threshold,
    source_url,
    parse_notes,
    is_matchable,
    created_at,
    updated_at
)
VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
)
"""


def main() -> None:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DuckDB warehouse not found: {DB_PATH}")

    print(f"[semantics] Opening {DB_PATH}")
    connection = duckdb.connect(str(DB_PATH))

    temp_dir = DB_PATH.parent / "duckdb_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Keep the parser stable on small Render instances.
    connection.execute("SET threads = 1")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET memory_limit = '300MB'")
    connection.execute(f"SET temp_directory = '{temp_dir.as_posix()}'")

    try:
        print("[semantics] Building temporary latest-market table...")
        connection.execute(
            """
            CREATE OR REPLACE TEMP TABLE latest_unique_markets AS
            SELECT
                platform,
                market_id,
                title,
                category,
                start_date,
                close_date,
                resolution_date,
                status,
                outcome,
                resolution_source,
                raw_url,
                snapshot_time
            FROM market_snapshots
            WHERE market_id IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY platform, market_id
                ORDER BY snapshot_time DESC
            ) = 1
            """
        )

        total_unique = connection.execute(
            "SELECT COUNT(*) FROM latest_unique_markets"
        ).fetchone()[0]
        print(f"[semantics] Latest unique markets: {total_unique:,}")

        processed = 0
        offset = 0

        while offset < total_unique:
            batch_cursor = connection.execute(
                """
                SELECT
                    platform,
                    market_id,
                    title,
                    category,
                    start_date,
                    close_date,
                    resolution_date,
                    status,
                    outcome,
                    resolution_source,
                    raw_url,
                    snapshot_time
                FROM latest_unique_markets
                ORDER BY platform, market_id
                LIMIT ? OFFSET ?
                """,
                [BATCH_SIZE, offset],
            )

            column_names = [item[0] for item in batch_cursor.description]
            raw_batch = batch_cursor.fetchall()
            if not raw_batch:
                break

            records = [
                dict(zip(column_names, raw_row))
                for raw_row in raw_batch
            ]
            parsed_batch = [
                parse_row(record).as_tuple()
                for record in records
            ]

            connection.execute("BEGIN TRANSACTION")
            try:
                connection.executemany(INSERT_SQL, parsed_batch)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

            processed += len(parsed_batch)
            offset += len(parsed_batch)

            if processed % 5_000 == 0 or processed == total_unique:
                print(
                    f"[semantics] processed {processed:,} / "
                    f"{total_unique:,} unique markets"
                )

        print(f"[semantics] Latest unique markets processed: {processed:,}")

        summary = connection.execute(
            """
            SELECT
                platform,
                event_type,
                is_matchable,
                needs_review,
                COUNT(*) AS markets
            FROM market_semantics
            WHERE parser_version = ?
            GROUP BY platform, event_type, is_matchable, needs_review
            ORDER BY platform, markets DESC
            """,
            [PARSER_VERSION],
        ).fetchdf()

        print("\n[semantics] Summary")
        print(summary.to_string(index=False))

        total = connection.execute(
            "SELECT COUNT(*) FROM market_semantics WHERE parser_version = ?",
            [PARSER_VERSION],
        ).fetchone()[0]
        matchable = connection.execute(
            """
            SELECT COUNT(*)
            FROM market_semantics
            WHERE parser_version = ?
              AND is_matchable = TRUE
            """,
            [PARSER_VERSION],
        ).fetchone()[0]

        print(f"\n[semantics] Parsed rows: {total:,}")
        print(f"[semantics] Conservatively matchable: {matchable:,}")
        print("[semantics] Complete")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
