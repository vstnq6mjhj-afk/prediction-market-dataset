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

from api.auth import verify_api_key
from api.keygen import generate_api_key
from api.supabase_client import supabase
from api.usage import log_api_request

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
        "Cross-platform prediction market data API covering Polymarket, Kalshi, "
        "Manifold, and PredictIt. Includes market search, latest snapshots, "
        "historical market data, movers, categories, platforms, and dataset stats."
    ),
)

# =========================
# Shared helpers
# =========================


def make_api_key() -> str:
    return "pmd_live_" + secrets.token_urlsafe(32)


def query_db(sql: str, params=None):
    conn = duckdb.connect(DB_PATH, read_only=True)
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


def update_subscription_by_email(
    email: str,
    plan: str,
    subscription_status: str,
    stripe_customer_id: Optional[str] = None,
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
    }

    if stripe_customer_id:
        updates["stripe_customer_id"] = stripe_customer_id

    supabase.table("api_keys").update(updates).eq("email", email).execute()


def update_subscription_by_customer(stripe_customer_id: str, subscription_status: str):
    stripe_customer_id = str(stripe_customer_id or "").strip()
    if not stripe_customer_id:
        return

    status = str(subscription_status or "free").lower()
    updates = {"subscription_status": status}

    if status != "active":
        updates["daily_limit"] = 100

    supabase.table("api_keys").update(updates).eq("stripe_customer_id", stripe_customer_id).execute()

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


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root():
    body = """
<section style="text-align:center; padding:60px 0;">
    <h1>Prediction Market Dataset API</h1>
    <p>Unified historical and live prediction market data from Polymarket, Kalshi, Manifold, and PredictIt.</p>
    <div class="actions" style="justify-content:center;">
        <a class="button" href="/signup">Create Account</a>
        <a class="button secondary" href="/login">Login</a>
        <a class="button secondary" href="/docs">View API Docs</a>
    </div>
</section>
<section class="grid">
    <div class="card"><h2>4.7M+</h2><p>Market snapshots collected</p></div>
    <div class="card"><h2>470K+</h2><p>Unique prediction markets</p></div>
    <div class="card"><h2>4</h2><p>Supported platforms</p></div>
</section>
<h2>Built for developers, researchers, and data teams</h2>
<section class="grid">
    <div class="card"><h3>Unified API</h3><p>Query multiple prediction market platforms through one normalized API.</p></div>
    <div class="card"><h3>Historical Data</h3><p>Access market history, snapshots, prices, volume, and liquidity.</p></div>
    <div class="card"><h3>Dataset Explorer</h3><p>Search, filter, inspect, and export prediction market data.</p></div>
</section>
<h2>Example Request</h2>
<code>GET /v1/search?q=bitcoin<br>Authorization: Bearer YOUR_API_KEY</code>
<footer>Prediction Market Dataset API · Live cross-platform market data</footer>
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

    requests_today = row.get("requests_today", 0) or 0
    daily_limit = row.get("daily_limit", 100) or 100
    usage_pct = min(int((requests_today / daily_limit) * 100), 100)
    plan = row.get("plan", "free")
    subscription_status = row.get("subscription_status", "free")
    display_plan = plan if str(subscription_status).lower() == "active" else "free"
    api_key = row.get("api_key", "")
    explorer_url = DATASET_EXPLORER_URL + "?" + urlencode({"api_key": api_key})

    body = f"""
<div class="header">
    <div>
        <h1>Customer Dashboard</h1>
        <p class="label">Welcome back, {escape(email)}</p>
    </div>
    <a href="/logout">Logout</a>
</div>
<div class="grid">
    <div class="card"><div class="label">Current Plan</div><div class="value">{escape(display_plan).upper()}</div><p class="label">Status: {escape(subscription_status)}</p></div>
    <div class="card"><div class="label">Requests Today</div><div class="value">{requests_today:,} / {daily_limit:,}</div><div class="progress"><div class="bar" style="width:{usage_pct}%"></div></div><p class="label">{usage_pct}% used</p></div>
    <div class="card"><div class="label">Daily Limit</div><div class="value">{daily_limit:,}</div><p class="label">Upgrade for higher limits.</p></div>
</div>
<div class="card" style="margin-top:25px;">
    <div class="label">Your API Key</div>
    <div class="api-key" id="apiKey">{escape(api_key)}</div>
</div>
<div class="actions">
    <a class="button" href="/docs">View API Docs</a>
    <a class="button secondary" href="{escape(explorer_url)}" target="_blank">Open Dataset Explorer</a>
    <button class="button secondary" onclick="copyApiKey()">Copy API Key</button>
    <form method="post" action="/dashboard/api-key/regenerate"><button class="button danger" type="submit">Regenerate API Key</button></form>
    <a class="button secondary" href="/pricing">View Plans & Billing</a>
    <form method="post" action="/billing/portal"><button class="button secondary" type="submit">Manage Billing</button></form>
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



@app.post("/billing/portal", include_in_schema=False)
def create_billing_portal(request: Request):
    email = require_portal_user(request)
    if not email:
        return RedirectResponse(url="/login", status_code=303)

    row = get_api_key_row_by_email(email)
    if not row:
        return RedirectResponse(url="/pricing", status_code=303)

    stripe_customer_id = row.get("stripe_customer_id")
    if not stripe_customer_id:
        return RedirectResponse(url="/pricing", status_code=303)

    if not STRIPE_SECRET_KEY:
        return HTMLResponse(
            page_shell(
                "Stripe not configured",
                "<h1>Stripe secret key is not configured</h1><p>Add STRIPE_SECRET_KEY to Render.</p>",
            ),
            status_code=500,
        )

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
    data_object = event.get("data", {}).get("object", {})

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
        email = metadata.get("email")
        plan = metadata.get("plan", "developer")
        status = data_object.get("status", "active")
        stripe_customer_id = data_object.get("customer")

        if status in {"active", "trialing"} and email:
            update_subscription_by_email(
                email=email,
                plan=plan,
                subscription_status="active",
                stripe_customer_id=stripe_customer_id,
            )
        elif status in {"past_due", "unpaid"}:
            update_subscription_by_customer(stripe_customer_id, "past_due")
        elif status in {"canceled", "incomplete_expired"}:
            update_subscription_by_customer(stripe_customer_id, "canceled")

    elif event_type == "customer.subscription.deleted":
        update_subscription_by_customer(data_object.get("customer"), "canceled")

    elif event_type == "invoice.payment_failed":
        update_subscription_by_customer(data_object.get("customer"), "past_due")

    return {"received": True}
