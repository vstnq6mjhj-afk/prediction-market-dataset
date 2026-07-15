import base64
import hashlib
import hmac
import html
import json
import math
import os
import secrets
import time
from urllib.parse import urlencode
from typing import Optional

import duckdb
import pandas as pd
import stripe
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.auth import verify_api_key
from api.keygen import generate_api_key
from api.supabase_client import supabase
from api.usage import log_api_request
from api.routes.explorer import router as explorer_router
# PHASE16_BILLING_V2
from api.routes.billing_v2 import router as billing_v2_router

# PHASE15B_SOURCE_POLICY
from api.source_policy import (
    PolicyContext,
    allowed_platforms,
    install_market_policy_view,
)

# =========================
# Configuration
# =========================

DB_PATH = os.getenv("DB_PATH", "/var/data/warehouse.duckdb")
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://prediction-market-dataset-api.onrender.com")
DATASET_EXPLORER_URL = os.getenv("DATASET_EXPLORER_URL", "https://prediction-market-dataset.onrender.com")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
stripe.api_key = STRIPE_SECRET_KEY

# The checkout code below uses inline Stripe price_data so a bad/stale price_... ID cannot break checkout.
# You can switch back to catalog Price IDs later after test/live mode is fully verified.
PLAN_CONFIG = {
    "developer": {
        "name": "Prediction Market Dataset Developer",
        "display_name": "Developer",
        "price_label": "£19/mo",
        "amount_pence": 1900,
        "daily_limit": 1_000,
        "description": "For individual developers, testing, prototypes, and small research projects.",
    },
    "professional": {
        "name": "Prediction Market Dataset Professional",
        "display_name": "Professional",
        "price_label": "£49/mo",
        "amount_pence": 4900,
        "daily_limit": 10_000,
        "description": "For serious users, researchers, data teams, and production applications.",
    },
}

APP_SECRET_KEY = os.getenv("APP_SECRET_KEY") or STRIPE_SECRET_KEY or "dev-change-me"
SESSION_COOKIE = "pmd_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14

app = FastAPI(
    title="Prediction Market Dataset API",
    version="1.0.0",
    description=(
        "Cross-platform prediction market data API with commercial source availability "
        "controlled by the source-policy allowlist. Includes market search, latest snapshots, "
        "historical market data, movers, categories, platforms, and dataset stats."
    ),
)

app.mount(
    "/static",
    StaticFiles(directory="api/static"),
    name="static",
)
app.include_router(explorer_router)
app.include_router(billing_v2_router)

# =========================
# Shared helpers
# =========================


def make_api_key() -> str:
    return "pmd_live_" + secrets.token_urlsafe(32)


def query_db(sql: str, params=None):
    conn = duckdb.connect(DB_PATH, read_only=True)
    install_market_policy_view(
        conn,
        PolicyContext.CUSTOMER_API,
    )
    try:
        df = conn.execute(sql, params or []).fetchdf()
        records = df.to_dict(orient="records")
        for row in records:
            for key, value in row.items():
                if pd.isna(value):
                    row[key] = None
                elif isinstance(value, float) and math.isinf(value):
                    row[key] = None
        return records
    finally:
        conn.close()


def normalize_email(email: Optional[str]) -> str:
    return str(email or "").strip().lower()


def escape(value) -> str:
    return html.escape(str(value or ""), quote=True)


def create_session_token(email: str) -> str:
    payload = {
        "email": normalize_email(email),
        "iat": int(time.time()),
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    signature = hmac.new(APP_SECRET_KEY.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def read_session_email(request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or "." not in token:
        return None

    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(APP_SECRET_KEY.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded.encode()).decode())
    except Exception:
        return None

    iat = int(payload.get("iat", 0))
    if time.time() - iat > SESSION_MAX_AGE_SECONDS:
        return None

    return normalize_email(payload.get("email"))


def login_redirect(email: str, url: str = "/dashboard") -> RedirectResponse:
    response = RedirectResponse(url=url, status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(email),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


def require_portal_user(request: Request) -> Optional[str]:
    email = read_session_email(request)
    if not email:
        return None
    return email


def ensure_api_key_for_user(email: str, user_id: Optional[str] = None):
    """Return an api_keys row for this user, creating it if missing."""
    email = normalize_email(email)

    existing = (
        supabase.table("api_keys")
        .select("*")
        .eq("email", email)
        .limit(1)
        .execute()
    )

    if existing.data:
        row = existing.data[0]
        updates = {}
        if user_id and not row.get("user_id"):
            updates["user_id"] = user_id
        if not row.get("api_key"):
            updates["api_key"] = make_api_key()
        if not row.get("plan"):
            updates["plan"] = "free"
        if row.get("active") is None:
            updates["active"] = True
        if row.get("daily_limit") is None:
            updates["daily_limit"] = 100
        if row.get("requests_today") is None:
            updates["requests_today"] = 0
        if not row.get("subscription_status"):
            updates["subscription_status"] = "free"

        if updates:
            (
                supabase.table("api_keys")
                .update(updates)
                .eq("email", email)
                .execute()
            )
            refreshed = (
                supabase.table("api_keys")
                .select("*")
                .eq("email", email)
                .limit(1)
                .execute()
            )
            return refreshed.data[0]

        return row

    api_key = make_api_key()
    inserted = supabase.table("api_keys").insert({
        "user_id": user_id,
        "email": email,
        "api_key": api_key,
        "plan": "free",
        "active": True,
        "daily_limit": 100,
        "requests_today": 0,
        "subscription_status": "free",
    }).execute()

    return inserted.data[0] if inserted.data else {
        "user_id": user_id,
        "email": email,
        "api_key": api_key,
        "plan": "free",
        "active": True,
        "daily_limit": 100,
        "requests_today": 0,
        "subscription_status": "free",
    }


def get_api_key_row_by_email(email: str):
    result = (
        supabase.table("api_keys")
        .select("*")
        .eq("email", normalize_email(email))
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None





def plan_daily_limit(plan: str) -> int:
    plan = str(plan or "").lower()
    if plan in PLAN_CONFIG:
        return int(PLAN_CONFIG[plan]["daily_limit"])
    return 100


def stripe_timestamp_to_iso(value) -> Optional[str]:
    if not value:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(value)))
    except Exception:
        return None


def stripe_object_to_dict(value):
    """Convert Stripe objects to plain dicts safely.

    Newer Stripe Python objects can raise AttributeError when code treats them
    exactly like dictionaries. This helper keeps webhook/dashboard sync stable.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return value.to_dict_recursive()
    except Exception:
        try:
            return dict(value)
        except Exception:
            return {}


def dict_get(data, key, default=None):
    if not isinstance(data, dict):
        data = stripe_object_to_dict(data)
    return data.get(key, default)


def safe_update_api_keys(updates: dict, column: str, value: str):
    """Update api_keys without ever crashing user-facing pages.

    Optional billing columns may be missing or temporarily unavailable during deploys.
    If a full update fails, retry without those optional fields. If that also fails,
    return None so the dashboard can still load.
    """
    try:
        return supabase.table("api_keys").update(updates).eq(column, value).execute()
    except Exception:
        try:
            fallback = {
                key: val
                for key, val in updates.items()
                if key not in {"cancel_at_period_end", "current_period_end"}
            }
            if fallback:
                return supabase.table("api_keys").update(fallback).eq(column, value).execute()
        except Exception:
            return None
    return None


def update_subscription_by_email(
    email: str,
    plan: str,
    subscription_status: str,
    stripe_customer_id: Optional[str] = None,
    cancel_at_period_end: bool = False,
    current_period_end: Optional[str] = None,
):
    email = normalize_email(email)
    plan = str(plan or "free").lower()
    subscription_status = str(subscription_status or "free").lower()

    if not email:
        return

    if subscription_status in {"active", "trialing"}:
        daily_limit = plan_daily_limit(plan)
        stored_status = "active"
    else:
        daily_limit = 100
        stored_status = subscription_status
        if plan not in PLAN_CONFIG:
            plan = "free"

    updates = {
        "plan": plan,
        "subscription_status": stored_status,
        "daily_limit": daily_limit,
        "cancel_at_period_end": bool(cancel_at_period_end),
        "current_period_end": current_period_end,
    }

    if stripe_customer_id:
        updates["stripe_customer_id"] = stripe_customer_id

    safe_update_api_keys(updates, "email", email)


def update_subscription_by_customer(stripe_customer_id: str, subscription_status: str):
    stripe_customer_id = str(stripe_customer_id or "").strip()
    if not stripe_customer_id:
        return

    status = str(subscription_status or "free").lower()
    updates = {
        "subscription_status": status,
        "cancel_at_period_end": False if status != "active" else None,
    }

    if status != "active":
        updates["daily_limit"] = 100
        updates["current_period_end"] = None

    # Remove None values so active updates do not accidentally clear fields.
    updates = {key: value for key, value in updates.items() if value is not None}
    safe_update_api_keys(updates, "stripe_customer_id", stripe_customer_id)


def infer_plan_from_stripe_subscription(subscription) -> str:
    """Best-effort plan detection for inline Stripe prices."""
    subscription = stripe_object_to_dict(subscription)

    metadata = stripe_object_to_dict(subscription.get("metadata") or {})
    plan = str(metadata.get("plan") or "").lower()
    if plan in PLAN_CONFIG:
        return plan

    try:
        items = stripe_object_to_dict(subscription.get("items") or {})
        item_data = items.get("data") or []
        first_item = stripe_object_to_dict(item_data[0]) if item_data else {}
        price = stripe_object_to_dict(first_item.get("price") or {})
        amount = int(price.get("unit_amount") or 0)
        if amount >= 4900:
            return "professional"
        if amount >= 1900:
            return "developer"
    except Exception:
        pass

    return "developer"


def sync_subscription_from_stripe(email: str, row: dict):
    """Refresh Supabase billing fields from Stripe when the dashboard loads.

    This catches cases where a webhook was missed or the customer canceled from
    Stripe Customer Portal before the latest webhook code was deployed.
    """
    email = normalize_email(email)
    stripe_customer_id = str((row or {}).get("stripe_customer_id") or "").strip()

    if not email or not stripe_customer_id or not STRIPE_SECRET_KEY:
        return

    try:
        subscriptions = stripe.Subscription.list(
            customer=stripe_customer_id,
            status="all",
            limit=10,
        )
    except Exception:
        return

    subscription_data = getattr(subscriptions, "data", None) or []
    if not subscription_data:
        return

    active_statuses = {"active", "trialing", "past_due", "unpaid"}

    # A customer may have multiple subscriptions from testing/upgrades.
    # Prefer the active/trialing subscription that is scheduled to cancel,
    # because that is the status the customer portal is currently showing.
    selected = None
    normalized_subscriptions = [stripe_object_to_dict(item) for item in subscription_data]

    for sub_dict in normalized_subscriptions:
        status_value = str(sub_dict.get("status") or "").lower()
        if status_value in active_statuses and bool(sub_dict.get("cancel_at_period_end") or sub_dict.get("cancel_at")):
            selected = sub_dict
            break

    # Otherwise use the newest active/trialing/past_due/unpaid subscription.
    if selected is None:
        for sub_dict in normalized_subscriptions:
            status_value = str(sub_dict.get("status") or "").lower()
            if status_value in active_statuses:
                selected = sub_dict
                break

    # Final fallback: newest subscription Stripe returned.
    if selected is None and normalized_subscriptions:
        selected = normalized_subscriptions[0]

    if not selected:
        return

    status = str(selected.get("status") or "free").lower()
    plan = infer_plan_from_stripe_subscription(selected)
    cancel_at_period_end = bool(selected.get("cancel_at_period_end") or selected.get("cancel_at"))

    # Stripe usually provides current_period_end; for cancel-at-period-end,
    # cancel_at is another reliable end-date field, so use it as fallback.
    current_period_end = stripe_timestamp_to_iso(
        selected.get("current_period_end") or selected.get("cancel_at")
    )

    if status in {"active", "trialing"}:
        update_subscription_by_email(
            email=email,
            plan=plan,
            subscription_status="active",
            stripe_customer_id=stripe_customer_id,
            cancel_at_period_end=cancel_at_period_end,
            current_period_end=current_period_end,
        )
    elif status in {"past_due", "unpaid"}:
        update_subscription_by_customer(stripe_customer_id, "past_due")
        safe_update_api_keys(
            {
                "cancel_at_period_end": cancel_at_period_end,
                "current_period_end": current_period_end,
            },
            "stripe_customer_id",
            stripe_customer_id,
        )
    elif status in {"canceled", "incomplete_expired"}:
        update_subscription_by_customer(stripe_customer_id, "canceled")
        safe_update_api_keys(
            {
                "cancel_at_period_end": False,
                "current_period_end": None,
            },
            "stripe_customer_id",
            stripe_customer_id,
        )

def require_active_subscription(account=Depends(verify_api_key)):
    """Allow /v1/account for all valid keys, but require paid subscription for dataset endpoints."""
    result = (
        supabase.table("api_keys")
        .select("plan,subscription_status,daily_limit,requests_today")
        .eq("api_key", account["api_key"])
        .limit(1)
        .execute()
    )
    row = result.data[0] if result.data else {}
    account.update(row)

    if str(row.get("subscription_status", "free")).lower() != "active":
        raise HTTPException(
            status_code=402,
            detail="A paid subscription is required to access the Prediction Market Dataset API.",
        )

    return account

def page_shell(title: str, body: str) -> str:
    return f"""
<!DOCTYPE html>
<html>
<head>
<title>{escape(title)} | Prediction Market Dataset</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body {{ margin:0; background:#0b1020; font-family:Arial,sans-serif; color:white; }}
.container {{ max-width:1150px; margin:auto; padding:52px 24px; }}
a {{ color:#38bdf8; text-decoration:none; }}
h1 {{ font-size:44px; margin:0 0 12px; }}
h2 {{ margin-top:34px; }}
p {{ color:#cbd5e1; line-height:1.6; }}
.grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:20px; }}
.card {{ background:#111827; border:1px solid #1f2937; border-radius:18px; padding:26px; box-shadow:0 20px 60px rgba(0,0,0,.35); }}
.label {{ color:#94a3b8; font-size:14px; }}
.value {{ font-size:30px; font-weight:bold; margin-top:10px; }}
.api-key {{ background:#020617; border:1px solid #1f2937; padding:18px; border-radius:12px; word-break:break-all; color:#22c55e; margin-top:15px; }}
.actions {{ margin-top:35px; display:flex; flex-wrap:wrap; gap:15px; align-items:center; }}
.button, button {{ display:inline-block; padding:14px 20px; border-radius:10px; border:0; background:#22c55e; color:white; font-weight:bold; cursor:pointer; text-decoration:none; font-size:16px; }}
.secondary {{ background:#1e293b; }}
.danger {{ background:#dc2626; }}
.progress {{ height:14px; background:#1e293b; border-radius:999px; overflow:hidden; margin-top:18px; }}
.bar {{ height:100%; background:#22c55e; }}
.header {{ display:flex; justify-content:space-between; align-items:center; gap:20px; margin-bottom:35px; }}
.price {{ font-size:42px; color:#38bdf8; font-weight:bold; margin:18px 0; }}
ul {{ padding-left:20px; color:#cbd5e1; line-height:1.9; }}
form {{ margin:0; }}
code {{ display:block; background:#020617; border:1px solid #1e293b; padding:20px; border-radius:12px; overflow-x:auto; color:#a7f3d0; }}
footer {{ margin-top:70px; text-align:center; color:#64748b; }}
@media(max-width:900px) {{ .grid {{ grid-template-columns:1fr; }} .header {{ flex-direction:column; align-items:flex-start; }} h1 {{ font-size:34px; }} }}
</style>
</head>
<body>
<div class="container">
{body}
</div>
</body>
</html>
"""

# =========================
# Protected REST API
# =========================


@app.get("/v1/health")
def health(account=Depends(require_active_subscription)):
    rows = query_db("""
        SELECT
            COUNT(*) AS total_rows,
            COUNT(DISTINCT market_id) AS unique_markets,
            COUNT(DISTINCT snapshot_time) AS snapshots,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
    """)
    log_api_request(account["api_key"], "/v1/health", 200, 1)
    return rows[0]


@app.get("/v1/latest")
def latest(account=Depends(require_active_subscription), platform: Optional[str] = None, limit: int = Query(100, ge=1, le=1000)):
    where = ""
    params = [limit]
    if platform:
        where = "AND platform = ?"
        params = [platform, limit]

    rows = query_db(f"""
        WITH latest AS (
            SELECT *
            FROM market_snapshots
            WHERE snapshot_time = (SELECT MAX(snapshot_time) FROM market_snapshots)
        )
        SELECT *
        FROM latest
        WHERE 1=1
        {where}
        ORDER BY volume DESC NULLS LAST
        LIMIT ?
    """, params)
    log_api_request(account["api_key"], "/v1/latest", 200, len(rows))
    return rows


@app.get("/v1/platforms")
def platforms(account=Depends(require_active_subscription)):
    rows = query_db("""
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
    """)
    log_api_request(account["api_key"], "/v1/platforms", 200, len(rows))
    return rows


@app.get("/v1/markets")
def markets(account=Depends(require_active_subscription), q: Optional[str] = None, platform: Optional[str] = None, limit: int = Query(100, ge=1, le=1000)):
    filters = []
    params = []
    if q:
        filters.append("LOWER(title) LIKE ?")
        params.append(f"%{q.lower()}%")
    if platform:
        filters.append("platform = ?")
        params.append(platform)
    where = "WHERE " + " AND ".join(filters) if filters else ""
    params.append(limit)

    rows = query_db(f"""
        SELECT platform, market_id, title, yes_price, no_price, volume, liquidity, status, snapshot_time, raw_url
        FROM market_snapshots
        {where}
        ORDER BY snapshot_time DESC
        LIMIT ?
    """, params)
    log_api_request(account["api_key"], "/v1/markets", 200, len(rows))
    return rows


@app.get("/v1/market/{market_id}")
def market_detail(market_id: str, account=Depends(require_active_subscription)):
    rows = query_db("""
        SELECT *
        FROM market_snapshots
        WHERE market_id = ?
        ORDER BY snapshot_time DESC
        LIMIT 5000
    """, [market_id])
    if not rows:
        raise HTTPException(status_code=404, detail="Market not found")
    log_api_request(account["api_key"], f"/v1/market/{market_id}", 200, len(rows))
    return rows


@app.get("/v1/movers")
def movers(account=Depends(require_active_subscription), limit: int = Query(100, ge=1, le=500)):
    rows = query_db("""
        WITH latest AS (SELECT MAX(snapshot_time) AS max_time FROM market_snapshots),
        recent AS (
            SELECT *
            FROM market_snapshots
            WHERE snapshot_time >= (SELECT max_time FROM latest) - INTERVAL '2 days'
              AND yes_price IS NOT NULL
        ),
        market_changes AS (
            SELECT
                platform,
                market_id,
                title,
                COUNT(*) AS snapshots,
                MIN(yes_price) AS min_price,
                MAX(yes_price) AS max_price,
                MAX(yes_price) - MIN(yes_price) AS price_change,
                MAX(volume) - MIN(volume) AS volume_change,
                MAX(liquidity) - MIN(liquidity) AS liquidity_change
            FROM recent
            GROUP BY platform, market_id, title
            HAVING COUNT(*) >= 2
        )
        SELECT *
        FROM market_changes
        ORDER BY ABS(price_change) DESC NULLS LAST
        LIMIT ?
    """, [limit])
    log_api_request(account["api_key"], "/v1/movers", 200, len(rows))
    return rows


@app.get("/v1/account")
def account(account=Depends(verify_api_key)):
    return {
        "email": account["email"],
        "plan": account.get("plan", account.get("tier", "free")),
        "subscription_status": account.get("subscription_status", "free"),
        "requests_today": account["requests_today"],
        "daily_limit": account["daily_limit"],
        "remaining": account["remaining"],
        "api_key": account["api_key"][:8] + "...",
    }


@app.post("/v1/api-key/regenerate")
def regenerate_api_key(account=Depends(verify_api_key)):
    new_key = generate_api_key()
    supabase.table("api_keys").update({"api_key": new_key}).eq("api_key", account["api_key"]).execute()
    return {"api_key": new_key, "message": "API key regenerated successfully"}


@app.get("/v1/search")
def search(q: str, account=Depends(require_active_subscription), limit: int = Query(50, ge=1, le=200)):
    rows = query_db("""
        SELECT *
        FROM market_snapshots
        WHERE LOWER(title) LIKE LOWER(?)
        QUALIFY ROW_NUMBER() OVER (PARTITION BY platform, market_id ORDER BY snapshot_time DESC) = 1
        ORDER BY snapshot_time DESC
        LIMIT ?
    """, [f"%{q}%", limit])
    log_api_request(account["api_key"], "/v1/search", 200, len(rows))
    return rows


@app.get("/v1/categories")
def categories(account=Depends(require_active_subscription)):
    rows = query_db("""
        SELECT LOWER(COALESCE(NULLIF(TRIM(category), ''), 'unknown')) AS category, COUNT(*) AS markets
        FROM (
            SELECT DISTINCT platform, market_id, LOWER(COALESCE(NULLIF(TRIM(category), ''), 'unknown')) AS category
            FROM market_snapshots
        )
        GROUP BY category
        ORDER BY markets DESC
    """)
    log_api_request(account["api_key"], "/v1/categories", 200, len(rows))
    return rows


@app.get("/v1/stats")
def stats(account=Depends(require_active_subscription)):
    row = query_db("""
        SELECT
            COUNT(*) AS snapshots,
            COUNT(DISTINCT platform || ':' || market_id) AS markets,
            COUNT(DISTINCT platform) AS platforms,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
    """)[0]
    log_api_request(account["api_key"], "/v1/stats", 200, 1)
    return row

# =========================
# Portal pages
# =========================



def compact_count(value) -> str:
    try:
        n = int(value or 0)
    except Exception:
        return "—"

    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M+"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K+"
    return str(n)


def homepage_dataset_stats():
    """Small public homepage stats. Never allow homepage to fail if DuckDB is busy."""
    fallback = {
        "snapshots": "5M+",
        "markets": "500K+",
        "platforms": str(len(allowed_platforms(PolicyContext.PUBLIC_SUMMARY))),
        "latest_snapshot": "Live dataset",
    }

    try:
        row = query_db("""
            SELECT
                COUNT(*) AS snapshots,
                COUNT(DISTINCT platform || ':' || market_id) AS markets,
                COUNT(DISTINCT platform) AS platforms,
                MAX(snapshot_time) AS latest_snapshot
            FROM market_snapshots
        """)[0]

        latest = row.get("latest_snapshot")
        latest_label = "Live dataset"
        if latest:
            latest_label = str(latest)[:19].replace("T", " ") + " UTC"

        return {
            "snapshots": compact_count(row.get("snapshots")),
            "markets": compact_count(row.get("markets")),
            "platforms": str(row.get("platforms") or "0"),
            "latest_snapshot": latest_label,
        }
    except Exception:
        return fallback


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root():
    stats = homepage_dataset_stats()

    body = f"""
<section style="text-align:center; padding:70px 0 55px;">
    <p class="label" style="font-size:16px;">Prediction market data infrastructure</p>
    <h1>Prediction Market Dataset</h1>
    <p style="font-size:20px; max-width:850px; margin:0 auto;">
        Unified historical and live prediction market data from commercially enabled sources —
        delivered through a REST API, customer dashboard,
        and interactive dataset explorer.
    </p>
    <div class="actions" style="justify-content:center;">
        <a class="button" href="/signup">Create Account</a>
        <a class="button secondary" href="/pricing">View Pricing</a>
        <a class="button secondary" href="/api-examples">API Examples</a>
        <a class="button secondary" href="/docs">API Docs</a>
        <a class="button secondary" href="/contact">Contact</a>
    </div>
</section>

<section class="grid">
    <div class="card">
        <h2>{escape(stats["snapshots"])}</h2>
        <p>Market snapshots collected across supported prediction market platforms.</p>
    </div>
    <div class="card">
        <h2>{escape(stats["markets"])}</h2>
        <p>Unique prediction markets normalized into one queryable dataset.</p>
    </div>
    <div class="card">
        <h2>{escape(stats["platforms"])}</h2>
        <p>Commercial source availability varies by licensing status and plan.</p>
    </div>
</section>

<div class="card" style="margin-top:24px; text-align:center; border-color:#38bdf8;">
    <h2>Dataset freshness</h2>
    <p style="font-size:18px; margin:0;">
        Latest warehouse snapshot: <strong>{escape(stats["latest_snapshot"])}</strong>
    </p>
    <p class="label" style="margin-top:10px;">
        The dataset is updated continuously by the collection scheduler.
    </p>
</div>

<h2>Built for developers, researchers, and data teams</h2>
<section class="grid">
    <div class="card">
        <h3>Unified REST API</h3>
        <p>
            Query market search, latest snapshots, platform coverage, market history,
            categories, movers, and dataset stats through one API.
        </p>
    </div>
    <div class="card">
        <h3>Historical Market Data</h3>
        <p>
            Access normalized market snapshots including prices, volume, liquidity,
            platform, status, category, timestamps, and market identifiers.
        </p>
    </div>
    <div class="card">
        <h3>Dataset Explorer</h3>
        <p>
            Browse markets, inspect market history, compare platforms, review movers,
            and export data from the interactive explorer.
        </p>
    </div>
</section>

<h2>What you can build</h2>
<section class="grid">
    <div class="card">
        <h3>Research workflows</h3>
        <p>
            Study market behavior, information aggregation, probability movement,
            liquidity, and cross-platform market coverage.
        </p>
    </div>
    <div class="card">
        <h3>Data applications</h3>
        <p>
            Build dashboards, internal tools, models, alerts, data pipelines,
            and market intelligence products on top of the API.
        </p>
    </div>
    <div class="card">
        <h3>Cross-platform analysis</h3>
        <p>
            Compare similar markets across platforms and inspect differences in
            prices, volume, liquidity, and market availability.
        </p>
    </div>
</section>

<h2>Example API request</h2>
<code>GET /v1/search?q=bitcoin&amp;limit=5<br>Authorization: Bearer YOUR_API_KEY</code>

<h2>Plans</h2>
<section class="grid">
    <div class="card">
        <h3>Developer</h3>
        <div class="price">£19/mo</div>
        <p>For individual developers, testing, prototypes, and small research projects.</p>
        <a class="button" href="/pricing">View Developer Plan</a>
    </div>
    <div class="card" style="border-color:#38bdf8;">
        <h3>Professional</h3>
        <div class="price">£49/mo</div>
        <p>For serious users, researchers, data teams, and production applications.</p>
        <a class="button" href="/pricing">View Professional Plan</a>
    </div>
    <div class="card">
        <h3>Enterprise</h3>
        <div class="price">Custom</div>
        <p>For companies, funds, universities, and teams needing custom access or exports.</p>
        <a class="button secondary" href="/contact">Contact Sales</a>
    </div>
</section>

<h2>FAQ</h2>
<section class="grid">
    <div class="card">
        <h3>What is Prediction Market Dataset?</h3>
        <p>
            It is a unified data platform for historical and live prediction market data,
            combining supported platforms into one API and explorer.
        </p>
    </div>
    <div class="card">
        <h3>Who is it for?</h3>
        <p>
            Researchers, developers, quantitative analysts, universities, funds,
            data teams, and anyone building with prediction market data.
        </p>
    </div>
    <div class="card">
        <h3>What platforms are covered?</h3>
        <p>
            The internal warehouse may contain additional sources. Customer availability is
            controlled separately by licensing status and plan.
        </p>
    </div>
    <div class="card">
        <h3>Do I get API access?</h3>
        <p>
            Yes. Paid plans include an API key, customer dashboard, dataset explorer,
            API examples, usage tracking, and protected REST endpoints.
        </p>
    </div>
    <div class="card">
        <h3>Can I export data?</h3>
        <p>
            The Dataset Explorer includes export workflows. Enterprise users can request
            custom exports, bulk delivery, or commercial licensing.
        </p>
    </div>
    <div class="card">
        <h3>Is this trading or betting advice?</h3>
        <p>
            No. This is a data access product. It does not provide financial,
            investment, betting, or trading advice.
        </p>
    </div>
</section>

<div class="card" style="margin-top:35px;">
    <h2>Important note</h2>
    <p>
        Prediction Market Dataset is a data access product. It does not provide financial,
        investment, betting, or trading advice. Customers are responsible for their own
        analysis and use of the data.
    </p>
</div>

<footer>
    Prediction Market Dataset · Unified prediction market data infrastructure
    <br><br>
    <a href="/api-examples">API Examples</a> ·
    <a href="/docs">API Docs</a> ·
    <a href="/terms">Terms</a> ·
    <a href="/privacy">Privacy</a> ·
    <a href="/contact">Contact</a>
</footer>
"""
    return page_shell("Home", body)


@app.get("/signup", response_class=HTMLResponse, include_in_schema=False)
def signup_page():
    body = """
<div class="card" style="max-width:420px; margin:80px auto;">
    <h1>Create your account</h1>
    <p>Start using the Prediction Market Dataset platform.</p>
    <form method="post" action="/signup">
        <input name="email" type="email" placeholder="Email" required style="width:100%; padding:14px; margin-top:16px; box-sizing:border-box; border:0; border-radius:10px; background:#1e293b; color:white;">
        <input name="password" type="password" placeholder="Password" required style="width:100%; padding:14px; margin-top:16px; box-sizing:border-box; border:0; border-radius:10px; background:#1e293b; color:white;">
        <button type="submit" style="width:100%; margin-top:22px;">Create Account</button>
    </form>
    <p>Already have an account? <a href="/login">Login</a></p>
</div>
"""
    return page_shell("Sign Up", body)


@app.post("/signup", include_in_schema=False)
def signup(email: str = Form(...), password: str = Form(...)):
    try:
        email = normalize_email(email)
        auth_result = supabase.auth.sign_up({"email": email, "password": password})
        if not auth_result.user:
            raise Exception("Supabase did not return a user for this signup.")
        ensure_api_key_for_user(email=email, user_id=auth_result.user.id)
        return login_redirect(email)
    except Exception as e:
        return HTMLResponse(page_shell("Signup failed", f"<h1>Signup failed</h1><pre>{escape(e)}</pre><p><a href='/signup'>Try again</a></p>"), status_code=500)


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page():
    body = """
<div class="card" style="max-width:420px; margin:80px auto;">
    <h1>Login</h1>
    <p>Access your customer dashboard, API key, billing, and dataset explorer.</p>
    <form method="post" action="/login">
        <input name="email" type="email" placeholder="Email" required style="width:100%; padding:14px; margin-top:16px; box-sizing:border-box; border:0; border-radius:10px; background:#1e293b; color:white;">
        <input name="password" type="password" placeholder="Password" required style="width:100%; padding:14px; margin-top:16px; box-sizing:border-box; border:0; border-radius:10px; background:#1e293b; color:white;">
        <button type="submit" style="width:100%; margin-top:22px;">Login</button>
    </form>
    <p>Need an account? <a href="/signup">Create one</a></p>
</div>
"""
    return page_shell("Login", body)


@app.post("/login", include_in_schema=False)
def login(email: str = Form(...), password: str = Form(...)):
    try:
        email = normalize_email(email)
        result = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if not result.user:
            raise Exception("Login failed: no user returned by Supabase.")
        ensure_api_key_for_user(email=result.user.email, user_id=result.user.id)
        return login_redirect(result.user.email)
    except Exception as e:
        return HTMLResponse(page_shell("Login failed", f"<h1>Login failed</h1><pre>{escape(e)}</pre><p><a href='/login'>Try again</a></p>"), status_code=401)


@app.get("/logout", include_in_schema=False)
def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request):
    email = require_portal_user(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    row = get_api_key_row_by_email(email)
    if not row:
        row = ensure_api_key_for_user(email=email)

    # Keep billing cancellation state fresh even if a Stripe webhook was missed.
    # Never allow Stripe/Supabase sync issues to break the dashboard page.
    try:
        sync_subscription_from_stripe(email, row)
        row = get_api_key_row_by_email(email) or row
    except Exception:
        pass

    requests_today = row.get("requests_today", 0) or 0
    daily_limit = row.get("daily_limit", 100) or 100
    usage_pct = min(int((requests_today / daily_limit) * 100), 100)
    plan = row.get("plan", "free")
    subscription_status = row.get("subscription_status", "free")
    display_plan = plan if str(subscription_status).lower() == "active" else "free"

    cancel_at_period_end = bool(row.get("cancel_at_period_end"))
    current_period_end = row.get("current_period_end")
    status_label = str(subscription_status)
    if str(subscription_status).lower() == "active" and cancel_at_period_end:
        if current_period_end:
            status_label = f"active — cancels on {str(current_period_end)[:10]}"
        else:
            status_label = "active — cancels at period end"

    api_key = row.get("api_key", "")
    explorer_url = "/explorer"

    body = f"""
<div style="text-align:center; position:relative; margin-bottom:30px;">
    <a href="/logout" style="position:absolute; right:0; top:8px;">Logout</a>
    <h1>Customer Dashboard</h1>
    <p class="label">Welcome back, {escape(email)}</p>
</div>

<div class="card" style="max-width:1060px; margin:0 auto 24px; text-align:center;">
    <div class="actions" style="margin-top:0; justify-content:center; gap:14px;">
        <a class="button" href="/docs">View API Docs</a>
        <a class="button secondary" href="/api-examples">API Examples</a>
        <a class="button secondary" href="{escape(explorer_url)}" target="_blank">Open Dataset Explorer</a>
        <button class="button secondary" onclick="copyApiKey()">Copy API Key</button>
        <form method="post" action="/dashboard/api-key/regenerate"><button class="button danger" type="submit">Regenerate API Key</button></form>
        <a class="button secondary" href="/pricing">View Plans & Billing</a>
        <form method="post" action="/billing/portal"><button class="button secondary" type="submit">Manage Billing</button></form>
        <form method="post" action="/billing/sync"><button class="button secondary" type="submit">Sync Billing</button></form>
    </div>
</div>

<div class="card" style="max-width:850px; margin:0 auto 24px; text-align:center;">
    <div class="label">Your API Key</div>
    <div class="api-key" id="apiKey" style="text-align:center; margin-left:auto; margin-right:auto;">{escape(api_key)}</div>
</div>

<div class="grid" style="max-width:1060px; margin:0 auto;">
    <div class="card" style="text-align:center;"><div class="label">Current Plan</div><div class="value">{escape(display_plan).upper()}</div><p class="label">Status: {escape(status_label)}</p></div>
    <div class="card" style="text-align:center;"><div class="label">Requests Today</div><div class="value">{requests_today:,} / {daily_limit:,}</div><div class="progress"><div class="bar" style="width:{usage_pct}%"></div></div><p class="label">{usage_pct}% used</p></div>
    <div class="card" style="text-align:center;"><div class="label">Daily Limit</div><div class="value">{daily_limit:,}</div><p class="label">Upgrade for higher limits.</p></div>
</div>
<script>
function copyApiKey() {{
    navigator.clipboard.writeText(document.getElementById('apiKey').innerText);
    alert('API key copied');
}}
</script>
"""
    return page_shell("Dashboard", body)


@app.post("/dashboard/api-key/regenerate", include_in_schema=False)
def dashboard_regenerate_api_key(request: Request):
    email = require_portal_user(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    supabase.table("api_keys").update({"api_key": make_api_key()}).eq("email", email).execute()
    return RedirectResponse(url="/dashboard", status_code=303)

@app.get("/terms", response_class=HTMLResponse, include_in_schema=False)
def terms_page():
    body = """
<h1>Terms of Service</h1>

<p>Last updated: July 2026</p>

<div class="card">
    <h2>1. Service</h2>
    <p>
        Prediction Market Dataset provides access to historical and live prediction
        market data through a customer dashboard, dataset explorer, and REST API.
    </p>

    <h2>2. Dataset Use</h2>
    <p>
        The service is intended for research, analysis, development, and data access.
        We do not provide financial advice, trading advice, betting advice, or investment recommendations.
    </p>

    <h2>3. Accounts and API Keys</h2>
    <p>
        Customers are responsible for keeping their account credentials and API keys secure.
        API keys must not be shared publicly or used in abusive, fraudulent, or unlawful ways.
    </p>

    <h2>4. Subscriptions and Billing</h2>
    <p>
        Paid plans are billed through Stripe. Subscription access, API limits, and dataset
        features depend on the plan selected. Customers can manage billing through the customer dashboard.
    </p>

    <h2>5. Availability</h2>
    <p>
        We aim to provide reliable access to the dataset, but we do not guarantee uninterrupted
        availability, completeness, or error-free operation.
    </p>

    <h2>6. Data Accuracy</h2>
    <p>
        Prediction market data may come from third-party platforms. We work to normalize and maintain
        the dataset, but customers should independently verify data before relying on it.
    </p>

    <h2>7. Prohibited Use</h2>
    <p>
        Customers may not overload the API, reverse engineer the service, resell access without permission,
        or use the service for unlawful purposes.
    </p>

    <h2>8. Termination</h2>
    <p>
        We may suspend or terminate access for abuse, non-payment, or breach of these terms.
    </p>

    <h2>9. Contact</h2>
    <p>
        Questions about these terms can be sent through the contact page.
    </p>
</div>

<p><a href="/">← Back home</a></p>
"""
    return page_shell("Terms of Service", body)


@app.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy_page():
    body = """
<h1>Privacy Policy</h1>

<p>Last updated: July 2026</p>

<div class="card">
    <h2>1. Information We Collect</h2>
    <p>
        We collect account information such as email address, login details, subscription status,
        API usage, and billing identifiers needed to operate the service.
    </p>

    <h2>2. Payment Information</h2>
    <p>
        Payments are processed by Stripe. We do not store full card numbers on our servers.
        Stripe may process payment method, invoice, and billing information.
    </p>

    <h2>3. API Usage Data</h2>
    <p>
        We may record API request counts, endpoint usage, timestamps, and account-level usage
        statistics to enforce plan limits and improve the service.
    </p>

    <h2>4. How We Use Information</h2>
    <p>
        We use information to provide accounts, manage subscriptions, secure API access,
        operate the dataset platform, and support customers.
    </p>

    <h2>5. Data Sharing</h2>
    <p>
        We do not sell customer data. We may share necessary information with service providers
        such as Stripe, Supabase, Render, and infrastructure providers used to operate the platform.
    </p>

    <h2>6. Security</h2>
    <p>
        We use reasonable technical and organizational measures to protect account and service data.
        Customers should keep API keys and passwords secure.
    </p>

    <h2>7. Data Retention</h2>
    <p>
        We retain account, billing, and usage records as needed to operate the service,
        comply with obligations, and resolve disputes.
    </p>

    <h2>8. Contact</h2>
    <p>
        Privacy questions can be sent through the contact page.
    </p>
</div>

<p><a href="/">← Back home</a></p>
"""
    return page_shell("Privacy Policy", body)


@app.get("/contact", response_class=HTMLResponse, include_in_schema=False)
def contact_page():
    body = """
<h1>Contact</h1>

<div class="card">
    <p>
        For support, billing questions, enterprise access, dataset questions, or API issues,
        contact the Prediction Market Dataset team.
    </p>

    <p>
        <strong>Email:</strong>
        <a href="mailto:jjb9gvh6wq@privaterelay.appleid.com">
            jjb9gvh6wq@privaterelay.appleid.com
        </a>
    </p>

    <h2>Useful details to include</h2>
    <ul>
        <li>Your account email</li>
        <li>Your plan</li>
        <li>The API endpoint or dashboard page involved</li>
        <li>Any error message you received</li>
    </ul>
</div>

<p><a href="/">← Back home</a></p>
"""
    return page_shell("Contact", body)

@app.api_route("/billing/portal", methods=["GET", "POST"], include_in_schema=False)
async def create_billing_portal(request: Request):
    """
    Open Stripe Customer Portal for the currently logged-in user.

    This route intentionally does NOT require an email form field anymore.
    The dashboard uses a session cookie, so Manage Billing should work from:
        <form method="post" action="/billing/portal">...</form>

    It also supports /billing/portal?email=... as a fallback for debugging.
    """
    email = require_portal_user(request)

    # Fallbacks only. The session cookie is the normal source of truth.
    if not email:
        if request.method == "POST":
            try:
                form = await request.form()
                email = normalize_email(form.get("email"))
            except Exception:
                email = ""
        else:
            email = normalize_email(request.query_params.get("email"))

    if not email:
        return HTMLResponse(
            page_shell(
                "Missing email",
                """
                <h1>Missing account session</h1>
                <p>Please log in again, then click <strong>Manage Billing</strong> from your dashboard.</p>
                <p><a class="button" href="/login">Go to Login</a></p>
                """,
            ),
            status_code=400,
        )

    row = get_api_key_row_by_email(email)

    if not row:
        return HTMLResponse(
            page_shell(
                "Account not found",
                """
                <h1>Account not found</h1>
                <p>Please log in again so we can refresh your customer account.</p>
                <p><a class="button" href="/login">Go to Login</a></p>
                """,
            ),
            status_code=404,
        )

    stripe_customer_id = row.get("stripe_customer_id")

    # If Supabase does not have the customer ID yet, try to find it in Stripe by email.
    # This fixes older paid accounts created before stripe_customer_id was reliably stored.
    if not stripe_customer_id:
        try:
            customers = stripe.Customer.list(email=email, limit=1)

            if customers.data:
                stripe_customer_id = customers.data[0].id
                (
                    supabase.table("api_keys")
                    .update({"stripe_customer_id": stripe_customer_id})
                    .eq("email", email)
                    .execute()
                )

        except Exception as e:
            return HTMLResponse(
                page_shell(
                    "Billing portal failed",
                    f"<h1>Could not find Stripe customer</h1><pre>{escape(e)}</pre><p><a href='/dashboard'>Back to dashboard</a></p>",
                ),
                status_code=500,
            )

    # If there is still no Stripe customer, the user has not subscribed yet.
    if not stripe_customer_id:
        return RedirectResponse(url="/pricing", status_code=303)

    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=f"{APP_BASE_URL}/dashboard",
        )

        return RedirectResponse(portal_session.url, status_code=303)

    except Exception as e:
        return HTMLResponse(
            page_shell(
                "Billing portal failed",
                f"<h1>Billing portal failed</h1><pre>{escape(e)}</pre><p><a href='/dashboard'>Back to dashboard</a></p>",
            ),
            status_code=500,
        )



@app.post("/billing/sync", include_in_schema=False)
def billing_sync_now(request: Request):
    """Force-refresh local billing fields from Stripe for the logged-in user."""
    email = require_portal_user(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    row = get_api_key_row_by_email(email)
    if row:
        try:
            sync_subscription_from_stripe(email, row)
        except Exception:
            pass

    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/billing/debug", response_class=HTMLResponse, include_in_schema=False)
def billing_debug(request: Request):
    """Small logged-in debug page showing what Stripe returns for this customer."""
    email = require_portal_user(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    row = get_api_key_row_by_email(email)
    if not row:
        return page_shell("Billing Debug", "<h1>No account row found</h1><p><a href='/dashboard'>Back</a></p>")

    stripe_customer_id = row.get("stripe_customer_id")
    if not stripe_customer_id:
        return page_shell("Billing Debug", "<h1>No Stripe customer ID found</h1><p><a href='/dashboard'>Back</a></p>")

    try:
        subscriptions = stripe.Subscription.list(customer=stripe_customer_id, status="all", limit=20)
        data = [stripe_object_to_dict(item) for item in (getattr(subscriptions, "data", None) or [])]
        rows = []
        for sub in data:
            rows.append(f"""
            <tr>
                <td>{escape(sub.get('id'))}</td>
                <td>{escape(sub.get('status'))}</td>
                <td>{escape(sub.get('cancel_at_period_end'))}</td>
                <td>{escape(sub.get('cancel_at'))}</td>
                <td>{escape(sub.get('current_period_end'))}</td>
                <td>{escape(stripe_timestamp_to_iso(sub.get('current_period_end') or sub.get('cancel_at')))}</td>
            </tr>
            """)
        table = "".join(rows) or "<tr><td colspan='6'>No subscriptions returned</td></tr>"
        body = f"""
        <h1>Billing Debug</h1>
        <p>Account: {escape(email)}</p>
        <p>Stripe customer: {escape(stripe_customer_id)}</p>
        <form method='post' action='/billing/sync'><button type='submit'>Sync Billing Now</button></form>
        <div class='card' style='margin-top:20px; overflow:auto;'>
        <table style='width:100%; border-collapse:collapse; color:white;'>
            <tr><th>ID</th><th>Status</th><th>cancel_at_period_end</th><th>cancel_at</th><th>current_period_end</th><th>decoded end</th></tr>
            {table}
        </table>
        </div>
        <p><a href='/dashboard'>Back to dashboard</a></p>
        """
        return page_shell("Billing Debug", body)
    except Exception as e:
        return page_shell("Billing Debug", f"<h1>Stripe debug failed</h1><pre>{escape(e)}</pre><p><a href='/dashboard'>Back</a></p>")



@app.get("/api-examples", response_class=HTMLResponse, include_in_schema=False)
def api_examples_page(request: Request):
    email = require_portal_user(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    row = get_api_key_row_by_email(email)
    if not row:
        row = ensure_api_key_for_user(email=email)

    api_key = row.get("api_key", "YOUR_API_KEY")
    masked_key = api_key[:14] + "..." + api_key[-6:] if len(api_key) > 24 else api_key
    base_url = APP_BASE_URL.rstrip("/")

    curl_search = f"""curl -X GET "{base_url}/v1/search?q=bitcoin&limit=5" \\
  -H "Authorization: Bearer {api_key}"""

    curl_latest = f"""curl -X GET "{base_url}/v1/latest?platform=polymarket&limit=10" \\
  -H "Authorization: Bearer {api_key}"""

    python_example = f"""import requests

API_KEY = "{api_key}"
BASE_URL = "{base_url}"

headers = {{
    "Authorization": f"Bearer {{API_KEY}}"
}}

params = {{
    "q": "bitcoin",
    "limit": 10,
}}

response = requests.get(
    f"{{BASE_URL}}/v1/search",
    headers=headers,
    params=params,
)
response.raise_for_status()

markets = response.json()

for market in markets:
    print(
        market.get("platform"),
        market.get("title"),
        market.get("yes_price"),
    )"""

    javascript_example = f"""const API_KEY = "{api_key}";
const BASE_URL = "{base_url}";

async function searchMarkets() {{
  const url = `${{BASE_URL}}/v1/search?q=bitcoin&limit=10`;

  const response = await fetch(url, {{
    headers: {{
      Authorization: `Bearer ${{API_KEY}}`,
    }},
  }});

  if (!response.ok) {{
    throw new Error(`API error: ${{response.status}}`);
  }}

  const markets = await response.json();
  console.log(markets);
}}

searchMarkets();"""

    r_example = f"""library(httr2)
library(jsonlite)

api_key <- "{api_key}"
base_url <- "{base_url}"

req <- request(paste0(base_url, "/v1/search")) |>
  req_url_query(q = "bitcoin", limit = 10) |>
  req_headers(Authorization = paste("Bearer", api_key))

resp <- req_perform(req)
data <- resp_body_json(resp)
print(data)"""

    response_preview = """[
  {
    "platform": "polymarket",
    "market_id": "example_market_id",
    "title": "Example prediction market title",
    "yes_price": 0.42,
    "no_price": 0.58,
    "volume": 125000.0,
    "liquidity": 34000.0,
    "snapshot_time": "2026-07-10T10:00:00Z"
  }
]"""

    def example_block(block_id: str, title: str, code: str) -> str:
        return f"""
<div class="card" style="margin-top:24px;">
    <div style="display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:12px;">
        <h2 style="margin:0;">{escape(title)}</h2>
        <button class="button secondary" type="button" onclick="copyText('{block_id}')">Copy</button>
    </div>
    <code id="{block_id}" style="white-space:pre-wrap; line-height:1.55;">{escape(code)}</code>
</div>"""

    body = f"""
<a href="/dashboard">← Back to Dashboard</a>
<div style="text-align:center; margin-bottom:38px;">
    <h1>API Examples</h1>
    <p>Use your Prediction Market Dataset API key to query live and historical cross-platform prediction market data.</p>
    <div class="actions" style="justify-content:center; margin-top:22px;">
        <a class="button" href="/docs">Open Swagger Docs</a>
        <a class="button secondary" href="/dashboard">Account Dashboard</a>
    </div>
</div>

<div class="card" style="margin-bottom:24px; text-align:center;">
    <div class="label">Your API Key</div>
    <div class="api-key" id="apiKey" style="max-width:720px; margin:15px auto 0;">{escape(masked_key)}</div>
    <p>Use this key in the <strong>Authorization</strong> header as a Bearer token.</p>
    <button class="button secondary" type="button" onclick="copyApiKey()">Copy Full API Key</button>
</div>

<div class="grid" style="margin-bottom:28px;">
    <div class="card"><h2>Search</h2><p>Find markets by keyword across platforms.</p><code>GET /v1/search?q=bitcoin</code></div>
    <div class="card"><h2>Latest</h2><p>Fetch the latest market snapshot rows.</p><code>GET /v1/latest</code></div>
    <div class="card"><h2>Movers</h2><p>Find markets with the largest recent price changes.</p><code>GET /v1/movers</code></div>
</div>

{example_block('curlSearchExample', 'cURL: search markets', curl_search)}
{example_block('curlLatestExample', 'cURL: latest Polymarket rows', curl_latest)}
{example_block('pythonExample', 'Python', python_example)}
{example_block('javascriptExample', 'JavaScript / Node.js', javascript_example)}
{example_block('rExample', 'R', r_example)}

<div class="card" style="margin-top:24px;">
    <div style="display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:12px;">
        <h2 style="margin:0;">Example response</h2>
        <button class="button secondary" type="button" onclick="copyText('responsePreview')">Copy</button>
    </div>
    <p>This is the shape of a typical market row. Actual fields vary slightly by platform and endpoint.</p>
    <code id="responsePreview" style="white-space:pre-wrap; line-height:1.55;">{escape(response_preview)}</code>
</div>

<div class="card" style="margin-top:32px;">
    <h2>Useful endpoints</h2>
    <ul>
        <li><strong>/v1/account</strong> — check plan, subscription status, and remaining request quota.</li>
        <li><strong>/v1/stats</strong> — dataset size, platforms, markets, and latest snapshot time.</li>
        <li><strong>/v1/platforms</strong> — platform-level coverage and liquidity summary.</li>
        <li><strong>/v1/search?q=...</strong> — keyword search over latest markets.</li>
        <li><strong>/v1/latest</strong> — latest dataset rows, optionally filtered by platform.</li>
        <li><strong>/v1/market/{{market_id}}</strong> — historical snapshots for a specific market.</li>
        <li><strong>/v1/movers</strong> — recent market movers based on price changes.</li>
    </ul>
    <p>Full interactive docs are available at <a href="/docs">/docs</a>.</p>
</div>

<script>
const FULL_API_KEY = "{escape(api_key)}";

function copyApiKey() {{
    navigator.clipboard.writeText(FULL_API_KEY);
    alert("Full API key copied");
}}

function copyText(id) {{
    const text = document.getElementById(id).innerText;
    navigator.clipboard.writeText(text);
    alert("Copied");
}}
</script>
"""
    return page_shell("API Examples", body)

@app.get("/pricing", response_class=HTMLResponse, include_in_schema=False)
def pricing_page(request: Request):
    email = require_portal_user(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    body = """
<a href="/dashboard">← Back to Dashboard</a>
<div style="text-align:center; margin-bottom:46px;">
    <h1>Plans & Billing</h1>
    <p>Choose the right level of access for the Prediction Market Dataset platform.</p>
</div>
<div class="grid">
    <div class="card">
        <h2>Developer</h2>
        <div class="price">£19/mo</div>
        <p>For individual developers, testing, prototypes, and small research projects.</p>
        <ul>
            <li>Prediction market REST API</li>
            <li>Dataset Explorer access</li>
            <li>Market search</li>
            <li>Latest snapshots</li>
            <li>Historical market detail</li>
            <li>1,000 API requests/day</li>
        </ul>
        <form method="post" action="/billing/checkout/developer"><button type="submit" style="width:100%;">Subscribe to Developer</button></form>
    </div>
    <div class="card" style="border-color:#38bdf8;">
        <h2>Professional</h2>
        <div class="price">£49/mo</div>
        <p>For serious users, researchers, data teams, and production applications.</p>
        <ul>
            <li>Everything in Developer</li>
            <li>Higher request limits</li>
            <li>Priority data access</li>
            <li>Advanced dataset exploration</li>
            <li>CSV/JSON export workflows</li>
            <li>10,000 API requests/day</li>
        </ul>
        <form method="post" action="/billing/checkout/professional"><button type="submit" style="width:100%;">Subscribe to Professional</button></form>
    </div>
    <div class="card">
        <h2>Enterprise</h2>
        <div class="price">Custom</div>
        <p>For companies, funds, universities, and teams needing custom access.</p>
        <ul>
            <li>Custom API limits</li>
            <li>Bulk dataset exports</li>
            <li>Custom data delivery</li>
            <li>Team access</li>
            <li>Priority support</li>
            <li>Commercial licensing</li>
        </ul>
        <a class="button secondary" style="width:100%; text-align:center; box-sizing:border-box;" href="mailto:jjb9gvh6wq@privaterelay.appleid.com">Contact Sales</a>
    </div>
</div>
<div class="card" style="margin-top:35px;">
    <h2>What you get</h2>
    <p>Prediction Market Dataset is a unified dataset platform for historical and live prediction market data across Polymarket, Kalshi, Manifold, and PredictIt.</p>
    <p>Your subscription gives you access to API keys, the developer dashboard, dataset explorer, searchable market data, historical snapshots, and export-ready data workflows.</p>
</div>
"""
    return page_shell("Plans & Billing", body)


def create_checkout_session(email: str, plan: str):
    plan = plan.lower()
    if plan not in PLAN_CONFIG:
        return HTMLResponse(page_shell("Unknown plan", "<h1>Unknown plan</h1>"), status_code=400)

    if not STRIPE_SECRET_KEY:
        return HTMLResponse(
            page_shell(
                "Stripe not configured",
                "<h1>Stripe secret key is not configured</h1><p>Add STRIPE_SECRET_KEY to the API Render service environment variables.</p>",
            ),
            status_code=500,
        )

    config = PLAN_CONFIG[plan]

    try:
        checkout_session = stripe.checkout.Session.create(
            mode="subscription",
            customer_email=email,
            client_reference_id=email,
            line_items=[{
                "price_data": {
                    "currency": "gbp",
                    "unit_amount": config["amount_pence"],
                    "recurring": {"interval": "month"},
                    "product_data": {
                        "name": config["name"],
                        "description": config["description"],
                    },
                },
                "quantity": 1,
            }],
            success_url=f"{APP_BASE_URL}/billing/success?plan={plan}",
            cancel_url=f"{APP_BASE_URL}/pricing",
            metadata={"email": email, "plan": plan},
            subscription_data={"metadata": {"email": email, "plan": plan}},
        )
        return RedirectResponse(checkout_session.url, status_code=303)
    except Exception as e:
        return HTMLResponse(
            page_shell(
                "Stripe checkout failed",
                f"<h1>Stripe checkout failed</h1><pre>{escape(e)}</pre><p><a href='/pricing'>Back to pricing</a></p>",
            ),
            status_code=500,
        )


@app.post("/billing/checkout/developer", include_in_schema=False)
def create_developer_checkout(request: Request):
    email = require_portal_user(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    return create_checkout_session(email=email, plan="developer")


@app.post("/billing/checkout/professional", include_in_schema=False)
def create_professional_checkout(request: Request):
    email = require_portal_user(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)
    return create_checkout_session(email=email, plan="professional")


@app.get("/billing/success", include_in_schema=False)
def billing_success(request: Request, plan: str):
    email = require_portal_user(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    plan = plan.lower()
    if plan not in PLAN_CONFIG:
        plan = "developer"

    # The webhook is the source of truth, but this keeps the dashboard responsive
    # immediately after checkout returns.
    update_subscription_by_email(
        email=email,
        plan=plan,
        subscription_status="active",
    )

    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Stripe webhook secret is not configured")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Stripe payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    event_type = event.get("type")
    data_object = stripe_object_to_dict(event.get("data", {}).get("object", {}))

    if event_type == "checkout.session.completed":
        metadata = data_object.get("metadata") or {}
        email = (
            metadata.get("email")
            or data_object.get("customer_email")
            or (data_object.get("customer_details") or {}).get("email")
        )
        plan = metadata.get("plan", "developer")
        stripe_customer_id = data_object.get("customer")

        update_subscription_by_email(
            email=email,
            plan=plan,
            subscription_status="active",
            stripe_customer_id=stripe_customer_id,
        )

    elif event_type == "customer.subscription.updated":
        metadata = data_object.get("metadata") or {}
        email = normalize_email(metadata.get("email"))
        plan = metadata.get("plan") or infer_plan_from_stripe_subscription(data_object)
        status = str(data_object.get("status") or "active").lower()
        stripe_customer_id = data_object.get("customer")

        cancel_at_period_end = bool(data_object.get("cancel_at_period_end") or data_object.get("cancel_at"))
        current_period_end = stripe_timestamp_to_iso(data_object.get("current_period_end") or data_object.get("cancel_at"))

        if not email and stripe_customer_id:
            try:
                existing = (
                    supabase.table("api_keys")
                    .select("email")
                    .eq("stripe_customer_id", stripe_customer_id)
                    .limit(1)
                    .execute()
                )
                if existing.data:
                    email = normalize_email(existing.data[0].get("email"))
            except Exception:
                email = ""

        if status in {"active", "trialing"} and email:
            update_subscription_by_email(
                email=email,
                plan=plan,
                subscription_status="active",
                stripe_customer_id=stripe_customer_id,
                cancel_at_period_end=cancel_at_period_end,
                current_period_end=current_period_end,
            )
        elif status in {"past_due", "unpaid"}:
            update_subscription_by_customer(stripe_customer_id, "past_due")
            safe_update_api_keys(
                {
                    "cancel_at_period_end": cancel_at_period_end,
                    "current_period_end": current_period_end,
                },
                "stripe_customer_id",
                stripe_customer_id,
            )
        elif status in {"canceled", "incomplete_expired"}:
            update_subscription_by_customer(stripe_customer_id, "canceled")
            safe_update_api_keys(
                {
                    "cancel_at_period_end": False,
                    "current_period_end": None,
                },
                "stripe_customer_id",
                stripe_customer_id,
            )

    elif event_type == "customer.subscription.deleted":
        stripe_customer_id = data_object.get("customer")
        update_subscription_by_customer(stripe_customer_id, "canceled")
        safe_update_api_keys(
            {"cancel_at_period_end": False, "current_period_end": None},
            "stripe_customer_id",
            stripe_customer_id,
        )

    elif event_type == "invoice.payment_failed":
        update_subscription_by_customer(data_object.get("customer"), "past_due")

    return {"received": True}
