from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable, Optional

import duckdb
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.source_policy import PolicyContext, allowed_platforms

router = APIRouter()

DB_PATH = os.getenv("DB_PATH", "/var/data/warehouse.duckdb")
SEMANTICS_DB_PATH = os.getenv(
    "SEMANTICS_DB_PATH",
    "/var/data/market_semantics.duckdb",
)
REFRESH_STATUS_DB_PATH = Path(
    os.getenv(
        "REFRESH_STATUS_DB_PATH",
        "/var/data/refresh_status.sqlite3",
    )
)
CONNECTOR_DIAGNOSTICS_DIR = Path(
    os.getenv("CONNECTOR_DIAGNOSTICS_DIR", "/var/data")
)
DUCKDB_CONNECT_ATTEMPTS = max(
    1, int(os.getenv("DUCKDB_CONNECT_ATTEMPTS", "30"))
)
DUCKDB_CONNECT_RETRY_SECONDS = max(
    0.0, float(os.getenv("DUCKDB_CONNECT_RETRY_SECONDS", "0.25"))
)

APP_SECRET_KEY = (
    os.getenv("APP_SECRET_KEY")
    or os.getenv("STRIPE_SECRET_KEY")
    or "dev-change-me"
)
SESSION_COOKIE = "pmd_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14

CUSTOMER_POLICY_ENV = {
    "Customer API": "CUSTOMER_API_PLATFORMS",
    "Explorer": "EXPLORER_DATA_PLATFORMS",
    "Exports": "CUSTOMER_EXPORT_PLATFORMS",
    "Matcher": "CUSTOMER_MATCHER_PLATFORMS",
    "Public summary": "PUBLIC_SUMMARY_PLATFORMS",
}

SEMANTIC_TABLES = (
    "market_semantics_live",
    "canonical_events",
    "platform_events",
    "event_contracts",
    "matcher_diagnostics",
)


def _escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _format_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


def _format_value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:,.3f}"
    return str(value)


def _truncate(value: Any, limit: int = 280) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _admin_emails() -> set[str]:
    raw_values = [
        os.getenv("ADMIN_EMAILS", ""),
        os.getenv("ADMIN_EMAIL", ""),
    ]
    emails: set[str] = set()
    for raw in raw_values:
        for item in str(raw or "").split(","):
            email = item.strip().lower()
            if email:
                emails.add(email)
    return emails


def _read_session_email(request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or "." not in token:
        return None

    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(
        APP_SECRET_KEY.encode(),
        encoded.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(
            base64.urlsafe_b64decode(encoded.encode()).decode()
        )
    except Exception:
        return None

    issued_at = int(payload.get("iat", 0) or 0)
    if issued_at <= 0 or time.time() - issued_at > SESSION_MAX_AGE_SECONDS:
        return None

    email = str(payload.get("email") or "").strip().lower()
    return email or None


def _require_admin(request: Request) -> str | RedirectResponse:
    email = _read_session_email(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    configured_admins = _admin_emails()
    if not configured_admins:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_EMAILS is not configured.",
        )
    if email not in configured_admins:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return email


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _connect_duckdb_read_only(path: str) -> duckdb.DuckDBPyConnection:
    last_error: Exception | None = None
    for attempt in range(1, DUCKDB_CONNECT_ATTEMPTS + 1):
        try:
            return duckdb.connect(path, read_only=True)
        except Exception as exc:
            last_error = exc
            if attempt < DUCKDB_CONNECT_ATTEMPTS:
                time.sleep(DUCKDB_CONNECT_RETRY_SECONDS)
    raise RuntimeError(
        f"Could not open DuckDB read-only after "
        f"{DUCKDB_CONNECT_ATTEMPTS} attempts: {last_error}"
    )


def _warehouse_data() -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False,
        "summary": {},
        "platforms": [],
        "error": None,
    }
    try:
        connection = _connect_duckdb_read_only(DB_PATH)
        try:
            summary_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_rows,
                    COUNT(DISTINCT snapshot_time) AS snapshot_groups,
                    COUNT(DISTINCT platform || ':' || market_id) AS unique_markets,
                    MIN(snapshot_time) AS first_snapshot,
                    MAX(snapshot_time) AS latest_snapshot
                FROM market_snapshots
                """
            ).fetchone()

            result["summary"] = {
                "total_rows": summary_row[0],
                "snapshot_groups": summary_row[1],
                "unique_markets": summary_row[2],
                "first_snapshot": summary_row[3],
                "latest_snapshot": summary_row[4],
            }

            rows = connection.execute(
                """
                WITH latest_per_platform AS (
                    SELECT
                        platform,
                        MAX(snapshot_time) AS latest_snapshot
                    FROM market_snapshots
                    GROUP BY platform
                ),
                latest_counts AS (
                    SELECT
                        snapshots.platform,
                        COUNT(*) AS latest_rows,
                        COUNT(DISTINCT snapshots.market_id) AS latest_markets
                    FROM market_snapshots AS snapshots
                    JOIN latest_per_platform AS latest
                      ON latest.platform = snapshots.platform
                     AND latest.latest_snapshot = snapshots.snapshot_time
                    GROUP BY snapshots.platform
                )
                SELECT
                    all_rows.platform,
                    COUNT(*) AS total_rows,
                    COUNT(DISTINCT all_rows.market_id) AS unique_markets,
                    COUNT(DISTINCT all_rows.snapshot_time) AS snapshot_groups,
                    MIN(all_rows.snapshot_time) AS first_snapshot,
                    MAX(all_rows.snapshot_time) AS latest_snapshot,
                    COALESCE(MAX(latest_counts.latest_rows), 0) AS latest_rows,
                    COALESCE(MAX(latest_counts.latest_markets), 0) AS latest_markets
                FROM market_snapshots AS all_rows
                LEFT JOIN latest_counts
                  ON latest_counts.platform = all_rows.platform
                GROUP BY all_rows.platform
                ORDER BY total_rows DESC, all_rows.platform
                """
            ).fetchall()

            result["platforms"] = [
                {
                    "platform": row[0],
                    "total_rows": row[1],
                    "unique_markets": row[2],
                    "snapshot_groups": row[3],
                    "first_snapshot": row[4],
                    "latest_snapshot": row[5],
                    "latest_rows": row[6],
                    "latest_markets": row[7],
                }
                for row in rows
            ]
            result["available"] = True
        finally:
            connection.close()
    except Exception as exc:
        result["error"] = _truncate(exc)
    return result


def _refresh_runs(limit: int = 20) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False,
        "rows": [],
        "error": None,
    }
    if not REFRESH_STATUS_DB_PATH.exists():
        result["error"] = f"Not found: {REFRESH_STATUS_DB_PATH}"
        return result

    try:
        connection = sqlite3.connect(
            str(REFRESH_STATUS_DB_PATH),
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=5000")
            rows = connection.execute(
                """
                SELECT
                    refresh_type,
                    status,
                    started_at,
                    completed_at,
                    snapshot_rows,
                    total_rows,
                    latest_snapshot,
                    error_message
                FROM dataset_refresh_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            result["rows"] = [dict(row) for row in rows]
            result["available"] = True
        finally:
            connection.close()
    except Exception as exc:
        result["error"] = _truncate(exc)
    return result


def _connector_diagnostics(mode: str) -> dict[str, Any]:
    path = CONNECTOR_DIAGNOSTICS_DIR / f"connector_diagnostics_{mode}.json"
    result: dict[str, Any] = {
        "available": False,
        "mode": mode,
        "path": str(path),
        "completed_at": None,
        "total_unique_rows": None,
        "elapsed_seconds": None,
        "connectors": [],
        "error": None,
    }
    if not path.exists():
        result["error"] = f"Not found: {path}"
        return result

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        result["completed_at"] = payload.get("completed_at")
        result["total_unique_rows"] = payload.get("total_unique_rows")
        result["elapsed_seconds"] = payload.get("elapsed_seconds")
        connector_rows: list[dict[str, Any]] = []
        for name, values in sorted((payload.get("connectors") or {}).items()):
            values = values or {}
            pagination = values.get("pagination") or {}
            connector_rows.append(
                {
                    "name": name,
                    "returned_rows": values.get("returned_rows"),
                    "accepted_rows": values.get("accepted_rows"),
                    "elapsed_seconds": values.get("elapsed_seconds"),
                    "error": _truncate(values.get("error")),
                    "complete": pagination.get("complete"),
                    "termination_reason": pagination.get("termination_reason"),
                    "pages_fetched": pagination.get("pages_fetched"),
                    "page_limit_reached": pagination.get("page_limit_reached"),
                    "strategy": pagination.get("strategy"),
                }
            )
        result["connectors"] = connector_rows
        result["available"] = True
    except Exception as exc:
        result["error"] = _truncate(exc)
    return result


def _table_exists(connection: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_name = ?
        LIMIT 1
        """,
        [table_name],
    ).fetchone()
    return bool(row)


def _table_columns(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
) -> set[str]:
    rows = connection.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = ?
        """,
        [table_name],
    ).fetchall()
    return {str(row[0]) for row in rows}


def _semantic_data() -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False,
        "tables": [],
        "latest_refresh": None,
        "error": None,
    }
    path = Path(SEMANTICS_DB_PATH)
    if not path.exists():
        result["error"] = f"Not found: {path}"
        return result

    try:
        connection = _connect_duckdb_read_only(str(path))
        try:
            latest_candidates: list[Any] = []
            table_rows: list[dict[str, Any]] = []
            for table_name in SEMANTIC_TABLES:
                if not _table_exists(connection, table_name):
                    table_rows.append(
                        {
                            "table": table_name,
                            "rows": None,
                            "latest": None,
                            "status": "missing",
                        }
                    )
                    continue

                count = connection.execute(
                    f"SELECT COUNT(*) FROM {_quote_identifier(table_name)}"
                ).fetchone()[0]
                columns = _table_columns(connection, table_name)
                latest = None
                for candidate in (
                    "updated_at",
                    "processed_at",
                    "parsed_at",
                    "created_at",
                    "snapshot_time",
                    "completed_at",
                ):
                    if candidate in columns:
                        latest = connection.execute(
                            "SELECT MAX(" + _quote_identifier(candidate) + ") "
                            "FROM " + _quote_identifier(table_name)
                        ).fetchone()[0]
                        if latest is not None:
                            latest_candidates.append(latest)
                        break

                table_rows.append(
                    {
                        "table": table_name,
                        "rows": count,
                        "latest": latest,
                        "status": "ok",
                    }
                )

            result["tables"] = table_rows
            if latest_candidates:
                result["latest_refresh"] = max(
                    latest_candidates,
                    key=lambda value: str(value),
                )
            else:
                result["latest_refresh"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(path.stat().st_mtime),
                )
            result["available"] = True
        finally:
            connection.close()
    except Exception as exc:
        result["error"] = _truncate(exc)
    return result


def _process_state() -> dict[str, Any]:
    result = {
        "available": False,
        "api_running": None,
        "scheduler_running": None,
        "error": None,
    }
    try:
        completed = subprocess.run(
            ["ps", "-eo", "args="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "ps returned an error")
        commands = completed.stdout.splitlines()
        result["api_running"] = any(
            "uvicorn" in command and "api.main:app" in command
            for command in commands
        )
        result["scheduler_running"] = any(
            "run_dataset_scheduler.py" in command
            for command in commands
        )
        result["available"] = True
    except Exception as exc:
        result["error"] = _truncate(exc)
    return result


def _source_policy_data() -> dict[str, Any]:
    contexts = {
        "Internal": PolicyContext.INTERNAL,
        "Customer API": PolicyContext.CUSTOMER_API,
        "Explorer": PolicyContext.EXPLORER,
        "Exports": PolicyContext.EXPORT,
        "Matcher": PolicyContext.MATCHER,
        "Public summary": PolicyContext.PUBLIC_SUMMARY,
    }
    effective = {
        label: list(allowed_platforms(context))
        for label, context in contexts.items()
    }
    raw = {
        label: {
            "variable": env_name,
            "configured": env_name in os.environ,
            "raw": os.getenv(env_name, ""),
        }
        for label, env_name in CUSTOMER_POLICY_ENV.items()
    }
    customer_empty = all(not effective[label] for label in CUSTOMER_POLICY_ENV)
    raw_customer_empty = all(
        not str(item["raw"] or "").strip() for item in raw.values()
    )
    return {
        "effective": effective,
        "raw": raw,
        "customer_empty": customer_empty,
        "raw_customer_empty": raw_customer_empty,
    }


def _billing_lock_data() -> dict[str, Any]:
    checkout_enabled = _env_bool("BILLING_CHECKOUT_ENABLED", False)
    test_mode_only = _env_bool("BILLING_TEST_MODE_ONLY", True)
    require_sources = _env_bool("BILLING_REQUIRE_COMMERCIAL_SOURCES", True)
    return {
        "checkout_enabled": checkout_enabled,
        "test_mode_only": test_mode_only,
        "require_commercial_sources": require_sources,
        "visible_terms": os.getenv("BILLING_VISIBLE_TERMS", "monthly"),
        "safe": (
            not checkout_enabled
            and test_mode_only
            and require_sources
        ),
    }


def _status_badge(ok: Optional[bool], true_text: str, false_text: str) -> str:
    if ok is None:
        return '<span class="badge neutral">Unknown</span>'
    css_class = "good" if ok else "bad"
    text = true_text if ok else false_text
    return f'<span class="badge {css_class}">{_escape(text)}</span>'


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    header_html = "".join(f"<th>{_escape(item)}</th>" for item in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{_escape(_format_value(item))}</td>" for item in row)
        body_rows.append(f"<tr>{cells}</tr>")
    if not body_rows:
        body_rows.append(
            f'<tr><td colspan="{len(tuple(headers))}" class="muted">No rows.</td></tr>'
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + header_html
        + "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table></div>"
    )




PLATFORM_LICENSING_STATUS = (
    ("Kalshi", "Application in progress", "Disabled", "Written commercial agreement required"),
    ("Polymarket", "Permission request pending", "Disabled", "Written commercial agreement required"),
    ("PredictIt", "Permission request pending", "Disabled", "Written commercial agreement required"),
    ("Metaculus", "Not integrated; permission required", "Disabled", "Do not collect or expose without approval"),
    ("Manifold", "Declined", "Prohibited", "New collection suspended; existing records restricted"),
)

def _render_page(admin_email: str) -> str:
    warehouse = _warehouse_data()
    refresh = _refresh_runs()
    fast = _connector_diagnostics("fast")
    discovery = _connector_diagnostics("discovery")
    semantics = _semantic_data()
    processes = _process_state()
    policy = _source_policy_data()
    billing = _billing_lock_data()

    all_customer_controls_safe = (
        policy["customer_empty"]
        and policy["raw_customer_empty"]
        and billing["safe"]
    )

    summary = warehouse.get("summary") or {}
    platform_rows = [
        (
            item.get("platform"),
            _format_int(item.get("total_rows")),
            _format_int(item.get("unique_markets")),
            _format_int(item.get("snapshot_groups")),
            _format_int(item.get("latest_rows")),
            item.get("latest_snapshot"),
        )
        for item in warehouse.get("platforms", [])
    ]

    refresh_rows = [
        (
            item.get("refresh_type"),
            item.get("status"),
            item.get("started_at"),
            item.get("completed_at"),
            _format_int(item.get("snapshot_rows")),
            _format_int(item.get("total_rows")),
            item.get("latest_snapshot"),
            _truncate(item.get("error_message")),
        )
        for item in refresh.get("rows", [])
    ]

    def connector_table(payload: dict[str, Any]) -> str:
        rows = [
            (
                item.get("name"),
                _format_int(item.get("returned_rows")),
                _format_int(item.get("accepted_rows")),
                item.get("elapsed_seconds"),
                item.get("complete"),
                item.get("termination_reason"),
                item.get("pages_fetched"),
                _truncate(item.get("error")),
            )
            for item in payload.get("connectors", [])
        ]
        return _table(
            (
                "Connector",
                "Returned",
                "Accepted",
                "Seconds",
                "Complete",
                "Termination",
                "Pages",
                "Error",
            ),
            rows,
        )

    semantic_rows = [
        (
            item.get("table"),
            _format_int(item.get("rows")),
            item.get("latest"),
            item.get("status"),
        )
        for item in semantics.get("tables", [])
    ]

    policy_rows = []
    for label, values in policy["effective"].items():
        raw_info = policy["raw"].get(label)
        if raw_info:
            raw_state = (
                "empty"
                if raw_info["configured"] and not str(raw_info["raw"]).strip()
                else "unset"
                if not raw_info["configured"]
                else str(raw_info["raw"])
            )
            variable = raw_info["variable"]
        else:
            raw_state = os.getenv("INTERNAL_DATA_PLATFORMS", "default")
            variable = "INTERNAL_DATA_PLATFORMS"
        policy_rows.append(
            (
                label,
                variable,
                raw_state,
                ", ".join(values) if values else "empty",
            )
        )

    generated_at = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    safety_class = "good-panel" if all_customer_controls_safe else "bad-panel"
    safety_title = (
        "Commercial exposure controls are locked"
        if all_customer_controls_safe
        else "Commercial exposure controls need attention"
    )
    safety_text = (
        "All customer-facing effective allowlists are empty, their environment values contain no platforms, and live checkout is disabled."
        if all_customer_controls_safe
        else "At least one customer-facing allowlist or billing lock is not in the required safe state. Review the tables below before continuing."
    )

    errors = []
    for label, payload in (
        ("Warehouse", warehouse),
        ("Refresh status", refresh),
        ("Fast diagnostics", fast),
        ("Discovery diagnostics", discovery),
        ("Semantics", semantics),
        ("Process state", processes),
    ):
        if payload.get("error"):
            errors.append((label, payload["error"]))

    error_html = ""
    if errors:
        error_html = (
            '<section class="panel"><h2>Section warnings</h2><ul>'
            + "".join(
                f"<li><strong>{_escape(label)}:</strong> {_escape(error)}</li>"
                for label, error in errors
            )
            + "</ul></section>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex,nofollow,noarchive">
  <title>Admin Data Health | Prediction Market Dataset</title>
  <style>
    :root {{ color-scheme: dark; --bg:#071017; --panel:#0d1a23; --line:#203642; --text:#f4f7f8; --muted:#91a5b1; --green:#4ee1a0; --red:#ff7d87; --cyan:#5ed6f1; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif; }}
    main {{ width:min(1440px, calc(100% - 32px)); margin:28px auto 60px; }}
    a {{ color:var(--cyan); }}
    .topbar {{ display:flex; gap:16px; align-items:flex-start; justify-content:space-between; margin-bottom:18px; }}
    h1,h2,h3 {{ margin-top:0; }}
    h1 {{ margin-bottom:4px; font-size:30px; }}
    h2 {{ font-size:20px; margin-bottom:14px; }}
    .muted {{ color:var(--muted); }}
    .button {{ display:inline-block; padding:9px 13px; border:1px solid var(--line); border-radius:8px; background:#12232e; color:var(--text); text-decoration:none; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:14px 0; }}
    .card,.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; }}
    .card {{ padding:16px; }}
    .card .value {{ font-size:25px; font-weight:700; overflow-wrap:anywhere; }}
    .card .label {{ color:var(--muted); font-size:13px; }}
    .panel {{ padding:18px; margin-top:14px; }}
    .good-panel {{ border-color:#276849; background:#0d251d; }}
    .bad-panel {{ border-color:#7d343a; background:#291317; }}
    .badge {{ display:inline-block; border-radius:999px; padding:3px 8px; font-size:12px; font-weight:700; }}
    .badge.good {{ color:var(--green); background:#123526; }}
    .badge.bad {{ color:var(--red); background:#3b171b; }}
    .badge.neutral {{ color:var(--muted); background:#17252d; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:9px; }}
    table {{ width:100%; border-collapse:collapse; min-width:760px; }}
    th,td {{ text-align:left; vertical-align:top; padding:9px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }}
    th {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
    td:last-child {{ white-space:normal; max-width:420px; }}
    tr:last-child td {{ border-bottom:0; }}
    .split {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
    code {{ color:var(--cyan); }}
    @media (max-width:1000px) {{ .grid {{ grid-template-columns:repeat(2,1fr); }} .split {{ grid-template-columns:1fr; }} }}
    @media (max-width:620px) {{ main {{ width:min(100% - 18px, 1440px); }} .grid {{ grid-template-columns:1fr; }} .topbar {{ display:block; }} .topbar .actions {{ margin-top:12px; }} }}
  </style>
</head>
<body>
<main>
  <div class="topbar">
    <div>
      <h1>Admin data health</h1>
      <div class="muted">Aggregate operational metrics only. No market titles, IDs, prices, URLs, or raw restricted rows are rendered.</div>
      <div class="muted">Signed in as {_escape(admin_email)} · Generated {_escape(generated_at)}</div>
    </div>
    <div class="actions">
      <a class="button" href="/admin/data-health">Refresh</a>
      <a class="button" href="/dashboard">Dashboard</a>
    </div>
  </div>

  <section class="panel {safety_class}">
    <h2>{_escape(safety_title)}</h2>
    <div>{_escape(safety_text)}</div>
  </section>

  <div class="grid">
    <div class="card"><div class="label">Warehouse rows</div><div class="value">{_format_int(summary.get('total_rows'))}</div></div>
    <div class="card"><div class="label">Unique markets</div><div class="value">{_format_int(summary.get('unique_markets'))}</div></div>
    <div class="card"><div class="label">Snapshot groups</div><div class="value">{_format_int(summary.get('snapshot_groups'))}</div></div>
    <div class="card"><div class="label">Latest snapshot</div><div class="value" style="font-size:17px">{_escape(_format_value(summary.get('latest_snapshot')))}</div></div>
  </div>

  <section class="panel">
    <h2>Runtime</h2>
    <p>API process: {_status_badge(processes.get('api_running'), 'Running', 'Not detected')} &nbsp; Scheduler: {_status_badge(processes.get('scheduler_running'), 'Running', 'Not detected')}</p>
    <p class="muted">Warehouse: <code>{_escape(DB_PATH)}</code> · Refresh status: <code>{_escape(REFRESH_STATUS_DB_PATH)}</code> · Semantics: <code>{_escape(SEMANTICS_DB_PATH)}</code></p>
  </section>

  <section class="panel">
    <h2>Warehouse coverage by platform</h2>
    {_table(('Platform','Total rows','Unique markets','Snapshot groups','Rows in latest platform snapshot','Latest snapshot'), platform_rows)}
  </section>

  <section class="panel">
    <h2>Recent refresh runs</h2>
    {_table(('Type','Status','Started','Completed','Added rows','Warehouse rows','Latest snapshot','Error'), refresh_rows)}
  </section>

  <div class="split">
    <section class="panel">
      <h2>Fast connector diagnostics</h2>
      <p class="muted">Completed: {_escape(_format_value(fast.get('completed_at')))} · Unique rows: {_escape(_format_int(fast.get('total_unique_rows')))} · Seconds: {_escape(_format_value(fast.get('elapsed_seconds')))}</p>
      {connector_table(fast)}
    </section>
    <section class="panel">
      <h2>Discovery connector diagnostics</h2>
      <p class="muted">Completed: {_escape(_format_value(discovery.get('completed_at')))} · Unique rows: {_escape(_format_int(discovery.get('total_unique_rows')))} · Seconds: {_escape(_format_value(discovery.get('elapsed_seconds')))}</p>
      {connector_table(discovery)}
    </section>
  </div>

  <section class="panel">
    <h2>Semantic and matcher tables</h2>
    <p class="muted">Latest detected semantic timestamp or database modification: {_escape(_format_value(semantics.get('latest_refresh')))}</p>
    {_table(('Table','Rows','Latest timestamp','State'), semantic_rows)}
  </section>

  <section class="panel">
    <h2>Commercial licensing status</h2>
    <p class="muted">Operational status only. This table does not grant rights or expose market data.</p>
    {_table(('Platform','Licensing status','Customer exposure','Required action'), PLATFORM_LICENSING_STATUS)}
  </section>

  <section class="panel">
    <h2>Source-policy allowlists</h2>
    <p>Effective customer allowlists empty: {_status_badge(policy.get('customer_empty'), 'Yes', 'No')} &nbsp; Raw customer environment values contain no platforms: {_status_badge(policy.get('raw_customer_empty'), 'Yes', 'No')}</p>
    {_table(('Surface','Environment variable','Raw state/value','Effective platforms'), policy_rows)}
  </section>

  <section class="panel">
    <h2>Billing launch lock</h2>
    <p>Safe production lock: {_status_badge(billing.get('safe'), 'Enabled', 'Not fully enabled')}</p>
    {_table(('Setting','Value'), (
        ('BILLING_CHECKOUT_ENABLED', billing.get('checkout_enabled')),
        ('BILLING_TEST_MODE_ONLY', billing.get('test_mode_only')),
        ('BILLING_REQUIRE_COMMERCIAL_SOURCES', billing.get('require_commercial_sources')),
        ('BILLING_VISIBLE_TERMS', billing.get('visible_terms')),
    ))}
  </section>

  {error_html}
</main>
</body>
</html>"""


def _secure_html(content: str, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(content, status_code=status_code)
    response.headers["Cache-Control"] = "no-store, private, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "img-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@router.get(
    "/admin/data-health",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def admin_data_health(request: Request):
    admin = _require_admin(request)
    if isinstance(admin, RedirectResponse):
        return admin
    return _secure_html(_render_page(admin))
