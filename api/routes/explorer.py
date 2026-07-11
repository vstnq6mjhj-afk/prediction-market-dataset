import csv
import io
import math
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

import duckdb
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

router = APIRouter(
    prefix="/explorer",
    tags=["Dataset Explorer"],
    include_in_schema=False,
)

templates = Jinja2Templates(directory="api/templates")
DB_PATH = os.getenv("DB_PATH", "/var/data/warehouse.duckdb")

TOOLS = [
    {
        "name": "Dataset Overview",
        "slug": "overview",
        "description": "Warehouse totals, snapshot freshness, collection growth, and platform coverage.",
        "status": "Live",
    },
    {
        "name": "Markets",
        "slug": "markets",
        "description": "Search, filter, sort, paginate, and export prediction market records.",
        "status": "Live",
    },
    {
        "name": "Platforms",
        "slug": "platforms",
        "description": "Compare platform coverage, market counts, volume, liquidity, and freshness.",
        "status": "Live",
    },
    {
        "name": "Movers",
        "slug": "movers",
        "description": "Inspect recent price, volume, and liquidity changes.",
        "status": "Planned",
    },
    {
        "name": "Market Matcher",
        "slug": "matcher",
        "description": "Compare likely equivalent markets across supported platforms.",
        "status": "Planned",
    },
    {
        "name": "Market Detail",
        "slug": "market-detail",
        "description": "Inspect historical observations for a selected market.",
        "status": "Planned",
    },
    {
        "name": "Dataset Health",
        "slug": "health",
        "description": "Review freshness, coverage, and data-quality summaries.",
        "status": "Planned",
    },
]

SORT_COLUMNS = {
    "volume": "volume",
    "liquidity": "liquidity",
    "yes_price": "yes_price",
    "no_price": "no_price",
    "title": "title",
    "platform": "platform",
    "snapshot_time": "snapshot_time",
}


def _connect() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(DB_PATH, read_only=True)
    try:
        connection.execute("PRAGMA threads=1")
    except Exception:
        pass
    return connection


def _rows_as_dicts(cursor: duckdb.DuckDBPyConnection) -> List[Dict[str, Any]]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _latest_snapshot(connection: duckdb.DuckDBPyConnection) -> Any:
    row = connection.execute(
        "SELECT MAX(snapshot_time) FROM market_snapshots"
    ).fetchone()
    return row[0] if row else None


def _platforms(
    connection: duckdb.DuckDBPyConnection,
    latest_snapshot: Any,
) -> List[str]:
    if latest_snapshot is None:
        return []
    rows = connection.execute(
        """
        SELECT DISTINCT platform
        FROM market_snapshots
        WHERE snapshot_time = ?
          AND platform IS NOT NULL
        ORDER BY platform
        """,
        [latest_snapshot],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _market_conditions(
    latest_snapshot: Any,
    search: str,
    platform: str,
) -> Tuple[str, List[Any]]:
    conditions = ["snapshot_time = ?"]
    params: List[Any] = [latest_snapshot]

    clean_search = search.strip()
    clean_platform = platform.strip()

    if clean_search:
        conditions.append("LOWER(COALESCE(title, '')) LIKE ?")
        params.append(f"%{clean_search.lower()}%")

    if clean_platform:
        conditions.append("LOWER(platform) = ?")
        params.append(clean_platform.lower())

    return " AND ".join(conditions), params


def _fetch_markets(
    connection: duckdb.DuckDBPyConnection,
    latest_snapshot: Any,
    search: str,
    platform: str,
    sort: str,
    direction: str,
    limit: int,
    offset: int,
) -> Tuple[List[Dict[str, Any]], int]:
    where_sql, params = _market_conditions(latest_snapshot, search, platform)
    sort_sql = SORT_COLUMNS.get(sort, "volume")
    direction_sql = "ASC" if direction.lower() == "asc" else "DESC"

    total = connection.execute(
        f"SELECT COUNT(*) FROM market_snapshots WHERE {where_sql}",
        params,
    ).fetchone()[0]

    query = f"""
        SELECT
            platform,
            market_id,
            title,
            yes_price,
            no_price,
            volume,
            liquidity,
            status,
            snapshot_time,
            raw_url
        FROM market_snapshots
        WHERE {where_sql}
        ORDER BY {sort_sql} {direction_sql} NULLS LAST, title ASC
        LIMIT ? OFFSET ?
    """
    cursor = connection.execute(query, [*params, limit, offset])
    return _rows_as_dicts(cursor), int(total)



def _overview_stats(connection: duckdb.DuckDBPyConnection) -> Dict[str, Any]:
    row = connection.execute(
        """
        SELECT
            COUNT(*) AS total_rows,
            COUNT(DISTINCT market_id) AS unique_markets,
            COUNT(DISTINCT snapshot_time) AS snapshots,
            COUNT(DISTINCT platform) AS platforms,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
        """
    ).fetchone()

    return {
        "total_rows": int(row[0] or 0),
        "unique_markets": int(row[1] or 0),
        "snapshots": int(row[2] or 0),
        "platforms": int(row[3] or 0),
        "latest_snapshot": row[4],
    }


def _overview_growth(
    connection: duckdb.DuckDBPyConnection,
    limit: int = 40,
) -> List[Dict[str, Any]]:
    cursor = connection.execute(
        """
        SELECT
            snapshot_time,
            COUNT(*) AS rows
        FROM market_snapshots
        GROUP BY snapshot_time
        ORDER BY snapshot_time DESC
        LIMIT ?
        """,
        [limit],
    )
    rows = _rows_as_dicts(cursor)
    rows.reverse()

    max_rows = max((int(item.get("rows") or 0) for item in rows), default=1)
    decorated: List[Dict[str, Any]] = []
    for item in rows:
        row_count = int(item.get("rows") or 0)
        decorated.append(
            {
                "snapshot_time": _display_datetime(item.get("snapshot_time")),
                "rows": row_count,
                "rows_display": f"{row_count:,}",
                "bar_width": max(3, round((row_count / max_rows) * 100, 2)),
            }
        )
    return decorated


def _overview_platforms(
    connection: duckdb.DuckDBPyConnection,
) -> List[Dict[str, Any]]:
    cursor = connection.execute(
        """
        SELECT
            platform,
            COUNT(*) AS rows,
            COUNT(DISTINCT market_id) AS unique_markets,
            AVG(volume) AS avg_volume,
            AVG(liquidity) AS avg_liquidity,
            MIN(snapshot_time) AS first_snapshot,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
        GROUP BY platform
        ORDER BY rows DESC
        """
    )
    rows = _rows_as_dicts(cursor)
    max_rows = max((int(item.get("rows") or 0) for item in rows), default=1)

    decorated: List[Dict[str, Any]] = []
    for item in rows:
        row_count = int(item.get("rows") or 0)
        decorated.append(
            {
                **item,
                "rows_display": f"{row_count:,}",
                "unique_markets_display": f"{int(item.get('unique_markets') or 0):,}",
                "avg_volume_display": _display_number(item.get("avg_volume")),
                "avg_liquidity_display": _display_number(item.get("avg_liquidity")),
                "first_snapshot_display": _display_datetime(item.get("first_snapshot")),
                "latest_snapshot_display": _display_datetime(item.get("latest_snapshot")),
                "bar_width": max(3, round((row_count / max_rows) * 100, 2)),
            }
        )
    return decorated


def _platform_comparison(
    connection: duckdb.DuckDBPyConnection,
) -> List[Dict[str, Any]]:
    latest_snapshot = _latest_snapshot(connection)

    cursor = connection.execute(
        """
        WITH historical AS (
            SELECT
                platform,
                COUNT(*) AS total_rows,
                COUNT(DISTINCT market_id) AS historical_markets,
                MIN(snapshot_time) AS first_snapshot,
                MAX(snapshot_time) AS latest_snapshot,
                AVG(volume) AS historical_avg_volume,
                AVG(liquidity) AS historical_avg_liquidity
            FROM market_snapshots
            GROUP BY platform
        ),
        latest AS (
            SELECT
                platform,
                COUNT(*) AS latest_rows,
                COUNT(DISTINCT market_id) AS latest_markets,
                AVG(volume) AS latest_avg_volume,
                AVG(liquidity) AS latest_avg_liquidity,
                SUM(CASE WHEN yes_price IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_price,
                SUM(CASE WHEN volume IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_volume,
                SUM(CASE WHEN liquidity IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_liquidity
            FROM market_snapshots
            WHERE snapshot_time = ?
            GROUP BY platform
        )
        SELECT
            h.platform,
            h.total_rows,
            h.historical_markets,
            h.first_snapshot,
            h.latest_snapshot,
            h.historical_avg_volume,
            h.historical_avg_liquidity,
            COALESCE(l.latest_rows, 0) AS latest_rows,
            COALESCE(l.latest_markets, 0) AS latest_markets,
            l.latest_avg_volume,
            l.latest_avg_liquidity,
            COALESCE(l.rows_with_price, 0) AS rows_with_price,
            COALESCE(l.rows_with_volume, 0) AS rows_with_volume,
            COALESCE(l.rows_with_liquidity, 0) AS rows_with_liquidity
        FROM historical h
        LEFT JOIN latest l USING (platform)
        ORDER BY h.total_rows DESC
        """,
        [latest_snapshot],
    )

    rows = _rows_as_dicts(cursor)
    max_total_rows = max((int(item.get("total_rows") or 0) for item in rows), default=1)

    output: List[Dict[str, Any]] = []
    for item in rows:
        latest_rows = int(item.get("latest_rows") or 0)
        rows_with_price = int(item.get("rows_with_price") or 0)
        rows_with_volume = int(item.get("rows_with_volume") or 0)
        rows_with_liquidity = int(item.get("rows_with_liquidity") or 0)

        def pct(numerator: int, denominator: int) -> float:
            if denominator <= 0:
                return 0.0
            return round((numerator / denominator) * 100, 1)

        total_rows = int(item.get("total_rows") or 0)

        output.append(
            {
                **item,
                "platform_display": str(item.get("platform") or "Unknown").title(),
                "total_rows_display": f"{total_rows:,}",
                "historical_markets_display": f"{int(item.get('historical_markets') or 0):,}",
                "latest_rows_display": f"{latest_rows:,}",
                "latest_markets_display": f"{int(item.get('latest_markets') or 0):,}",
                "historical_avg_volume_display": _display_number(item.get("historical_avg_volume")),
                "historical_avg_liquidity_display": _display_number(item.get("historical_avg_liquidity")),
                "latest_avg_volume_display": _display_number(item.get("latest_avg_volume")),
                "latest_avg_liquidity_display": _display_number(item.get("latest_avg_liquidity")),
                "first_snapshot_display": _display_datetime(item.get("first_snapshot")),
                "latest_snapshot_display": _display_datetime(item.get("latest_snapshot")),
                "coverage_width": max(3, round((total_rows / max_total_rows) * 100, 2)),
                "price_coverage": pct(rows_with_price, latest_rows),
                "volume_coverage": pct(rows_with_volume, latest_rows),
                "liquidity_coverage": pct(rows_with_liquidity, latest_rows),
            }
        )
    return output

def _display_number(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)

    if abs(number) >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if abs(number) >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if abs(number) >= 1_000:
        return f"{number / 1_000:.1f}K"
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _display_price(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if 0 <= number <= 1:
        return f"{number:.1%}"
    return f"{number:.4f}"


def _display_datetime(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


def _decorate_market_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["yes_display"] = _display_price(row.get("yes_price"))
        item["no_display"] = _display_price(row.get("no_price"))
        item["volume_display"] = _display_number(row.get("volume"))
        item["liquidity_display"] = _display_number(row.get("liquidity"))
        item["snapshot_display"] = _display_datetime(row.get("snapshot_time"))
        output.append(item)
    return output


def _safe_csv_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _markets_url(
    *,
    page: int,
    page_size: int,
    search: str,
    platform: str,
    sort: str,
    direction: str,
    export: bool = False,
) -> str:
    params = {
        "page": page,
        "page_size": page_size,
        "q": search,
        "platform": platform,
        "sort": sort,
        "direction": direction,
    }
    base = "/explorer/markets/export" if export else "/explorer/markets"
    return f"{base}?{urlencode(params)}"


@router.get("")
@router.get("/")
def explorer_menu(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="explorer/menu.html",
        context={
            "page_title": "Dataset Explorer",
            "tools": TOOLS,
        },
    )


@router.get("/markets")
def explorer_markets(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    q: str = Query(default="", max_length=200),
    platform: str = Query(default="", max_length=50),
    sort: str = Query(default="volume"),
    direction: str = Query(default="desc"),
):
    selected_sort = sort if sort in SORT_COLUMNS else "volume"
    selected_direction = "asc" if direction.lower() == "asc" else "desc"
    rows: List[Dict[str, Any]] = []
    platforms: List[str] = []
    total = 0
    total_pages = 1
    latest_snapshot: Optional[Any] = None
    error: Optional[str] = None

    try:
        with _connect() as connection:
            latest_snapshot = _latest_snapshot(connection)
            if latest_snapshot is not None:
                platforms = _platforms(connection, latest_snapshot)
                _, total = _fetch_markets(
                    connection=connection,
                    latest_snapshot=latest_snapshot,
                    search=q,
                    platform=platform,
                    sort=selected_sort,
                    direction=selected_direction,
                    limit=1,
                    offset=0,
                )
                total_pages = max(1, math.ceil(total / page_size))
                page = min(page, total_pages)
                offset = (page - 1) * page_size
                raw_rows, _ = _fetch_markets(
                    connection=connection,
                    latest_snapshot=latest_snapshot,
                    search=q,
                    platform=platform,
                    sort=selected_sort,
                    direction=selected_direction,
                    limit=page_size,
                    offset=offset,
                )
                rows = _decorate_market_rows(raw_rows)
    except Exception as exc:
        error = str(exc)

    previous_url = None
    next_url = None
    if page > 1:
        previous_url = _markets_url(
            page=page - 1,
            page_size=page_size,
            search=q,
            platform=platform,
            sort=selected_sort,
            direction=selected_direction,
        )
    if page < total_pages:
        next_url = _markets_url(
            page=page + 1,
            page_size=page_size,
            search=q,
            platform=platform,
            sort=selected_sort,
            direction=selected_direction,
        )

    return templates.TemplateResponse(
        request=request,
        name="explorer/markets.html",
        context={
            "page_title": "Markets",
            "rows": rows,
            "platforms": platforms,
            "selected_platform": platform,
            "search": q,
            "sort": selected_sort,
            "direction": selected_direction,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "previous_url": previous_url,
            "next_url": next_url,
            "latest_snapshot": _display_datetime(latest_snapshot),
            "export_url": _markets_url(
                page=page,
                page_size=page_size,
                search=q,
                platform=platform,
                sort=selected_sort,
                direction=selected_direction,
                export=True,
            ),
            "error": error,
        },
    )


@router.get("/markets/export")
def export_markets_page(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10, le=100),
    q: str = Query(default="", max_length=200),
    platform: str = Query(default="", max_length=50),
    sort: str = Query(default="volume"),
    direction: str = Query(default="desc"),
):
    selected_sort = sort if sort in SORT_COLUMNS else "volume"
    selected_direction = "asc" if direction.lower() == "asc" else "desc"

    with _connect() as connection:
        latest_snapshot = _latest_snapshot(connection)
        if latest_snapshot is None:
            rows: List[Dict[str, Any]] = []
        else:
            offset = (page - 1) * page_size
            rows, _ = _fetch_markets(
                connection=connection,
                latest_snapshot=latest_snapshot,
                search=q,
                platform=platform,
                sort=selected_sort,
                direction=selected_direction,
                limit=page_size,
                offset=offset,
            )

    output = io.StringIO()
    fieldnames = [
        "platform",
        "market_id",
        "title",
        "yes_price",
        "no_price",
        "volume",
        "liquidity",
        "status",
        "snapshot_time",
        "raw_url",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _safe_csv_value(row.get(key)) for key in fieldnames})

    filename = f"prediction_markets_page_{page}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )



@router.get("/overview")
def explorer_overview(request: Request):
    error: Optional[str] = None
    stats: Dict[str, Any] = {
        "total_rows": 0,
        "unique_markets": 0,
        "snapshots": 0,
        "platforms": 0,
        "latest_snapshot": None,
    }
    growth: List[Dict[str, Any]] = []
    platform_rows: List[Dict[str, Any]] = []

    try:
        with _connect() as connection:
            stats = _overview_stats(connection)
            growth = _overview_growth(connection)
            platform_rows = _overview_platforms(connection)
    except Exception as exc:
        error = str(exc)

    return templates.TemplateResponse(
        request=request,
        name="explorer/overview.html",
        context={
            "page_title": "Dataset Overview",
            "stats": {
                **stats,
                "total_rows_display": f"{int(stats.get('total_rows') or 0):,}",
                "unique_markets_display": f"{int(stats.get('unique_markets') or 0):,}",
                "snapshots_display": f"{int(stats.get('snapshots') or 0):,}",
                "platforms_display": f"{int(stats.get('platforms') or 0):,}",
                "latest_snapshot_display": _display_datetime(stats.get("latest_snapshot")),
            },
            "growth": growth,
            "platform_rows": platform_rows,
            "error": error,
        },
    )


@router.get("/platforms")
def explorer_platforms(request: Request):
    error: Optional[str] = None
    rows: List[Dict[str, Any]] = []
    latest_snapshot: Optional[Any] = None

    try:
        with _connect() as connection:
            latest_snapshot = _latest_snapshot(connection)
            rows = _platform_comparison(connection)
    except Exception as exc:
        error = str(exc)

    return templates.TemplateResponse(
        request=request,
        name="explorer/platforms.html",
        context={
            "page_title": "Platforms",
            "rows": rows,
            "latest_snapshot": _display_datetime(latest_snapshot),
            "platform_count": len(rows),
            "error": error,
        },
    )

@router.get("/{tool_slug}")
def explorer_placeholder(request: Request, tool_slug: str):
    tool = next((item for item in TOOLS if item["slug"] == tool_slug), None)

    if tool is None:
        return templates.TemplateResponse(
            request=request,
            name="explorer/not_found.html",
            context={
                "page_title": "Tool not found",
                "tool_slug": tool_slug,
            },
            status_code=404,
        )

    return templates.TemplateResponse(
        request=request,
        name="explorer/tool_placeholder.html",
        context={
            "page_title": tool["name"],
            "tool": tool,
        },
    )
