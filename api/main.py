import os
from typing import Optional

import duckdb
import math
import pandas as pd
import secrets
import stripe
from fastapi import Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import Depends, FastAPI, HTTPException, Query

from api.auth import verify_api_key
from api.usage import log_api_request
from api.supabase_client import supabase
from api.keygen import generate_api_key

DB_PATH = os.getenv("DB_PATH", "/var/data/warehouse.duckdb")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_DEVELOPER_PRICE_ID = os.getenv("STRIPE_DEVELOPER_PRICE_ID")
STRIPE_PROFESSIONAL_PRICE_ID = os.getenv("STRIPE_PROFESSIONAL_PRICE_ID")
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://prediction-market-dataset-api.onrender.com")

stripe.api_key = STRIPE_SECRET_KEY

app = FastAPI(
    title="Prediction Market Dataset API",
    version="1.0.0",
    description="Cross-platform prediction market data API covering Polymarket, Kalshi, Manifold, and PredictIt. Includes market search, latest snapshots, historical market data, movers, categories, platforms, and dataset stats.",
)

def make_api_key():
    return "pmd_live_" + secrets.token_urlsafe(32)

def query_db(sql: str, params=None):
    conn = duckdb.connect(DB_PATH, read_only=True)
    try:
        if params is None:
            df = conn.execute(sql).fetchdf()
        else:
            df = conn.execute(sql, params).fetchdf()

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


def ensure_api_key_for_user(email: str, user_id: str):
    """Return an api_keys row for this user, creating it if missing."""
    email = email.strip().lower()

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
        if not row.get("user_id"):
            updates["user_id"] = user_id
        if not row.get("api_key"):
            updates["api_key"] = make_api_key()
        if not row.get("plan"):
            updates["plan"] = "developer"
        if row.get("daily_limit") is None:
            updates["daily_limit"] = 100
        if row.get("requests_today") is None:
            updates["requests_today"] = 0
        if not row.get("subscription_status"):
            updates["subscription_status"] = "free"
        if row.get("active") is None:
            updates["active"] = True

        if updates:
            supabase.table("api_keys").update(updates).eq("email", email).execute()
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
        "plan": "developer",
        "active": True,
        "daily_limit": 100,
        "requests_today": 0,
        "subscription_status": "free",
    }).execute()

    return inserted.data[0] if inserted.data else {
        "user_id": user_id,
        "email": email,
        "api_key": api_key,
        "plan": "developer",
        "active": True,
        "daily_limit": 100,
        "requests_today": 0,
        "subscription_status": "free",
    }


@app.get("/v1/health")
def health(account=Depends(verify_api_key)):
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
def latest(
    account=Depends(verify_api_key),
    platform: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
):
    where = ""
    params = [limit]

    if platform:
        where = "AND platform = ?"
        params = [platform, limit]

    rows = query_db(f"""
        WITH latest AS (
            SELECT *
            FROM market_snapshots
            WHERE snapshot_time = (
                SELECT MAX(snapshot_time)
                FROM market_snapshots
            )
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
def platforms(account=Depends(verify_api_key)):
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
def markets(
    account=Depends(verify_api_key),
    q: Optional[str] = None,
    platform: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
):
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
        {where}
        ORDER BY snapshot_time DESC
        LIMIT ?
    """, params)

    log_api_request(account["api_key"], "/v1/markets", 200, len(rows))
    return rows


@app.get("/v1/market/{market_id}")
def market_detail(
    market_id: str,
    account=Depends(verify_api_key),
):
    rows = query_db("""
        SELECT *
        FROM market_snapshots
        WHERE market_id = ?
        ORDER BY snapshot_time
    """, [market_id])

    if not rows:
        raise HTTPException(status_code=404, detail="Market not found")

    log_api_request(account["api_key"], f"/v1/market/{market_id}", 200, len(rows))
    return rows


@app.get("/v1/movers")
def movers(
    account=Depends(verify_api_key),
    limit: int = Query(100, ge=1, le=500),
):
    rows = query_db("""
        WITH latest AS (
            SELECT MAX(snapshot_time) AS max_time
            FROM market_snapshots
        ),
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
        "plan": account.get("plan", account.get("tier", "developer")),
        "requests_today": account["requests_today"],
        "daily_limit": account["daily_limit"],
        "remaining": account["remaining"],
        "api_key": account["api_key"][:8] + "..."
    }

@app.post("/v1/api-key/regenerate")
def regenerate_api_key(account=Depends(verify_api_key)):
    new_key = generate_api_key()

    supabase.table("api_keys").update(
        {"api_key": new_key}
    ).eq(
        "api_key", account["api_key"]
    ).execute()

    return {
        "api_key": new_key,
        "message": "API key regenerated successfully"
    }

@app.get("/v1/search")
def search(
    q: str,
    account=Depends(verify_api_key),
    limit: int = Query(50, ge=1, le=200),
):
    rows = query_db("""
        SELECT *
        FROM market_snapshots
        WHERE LOWER(title) LIKE LOWER(?)
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY platform, market_id
            ORDER BY snapshot_time DESC
        ) = 1
        ORDER BY snapshot_time DESC
        LIMIT ?
    """, [f"%{q}%", limit])

    log_api_request(account["api_key"], "/v1/search", 200, len(rows))
    return rows

@app.get("/v1/categories")
def categories(account=Depends(verify_api_key)):
    rows = query_db("""
        SELECT
            LOWER(COALESCE(NULLIF(TRIM(category), ''), 'unknown')) AS category,
            COUNT(*) AS markets
        FROM (
            SELECT DISTINCT
                platform,
                market_id,
                LOWER(COALESCE(NULLIF(TRIM(category), ''), 'unknown')) AS category
            FROM market_snapshots
        )
        GROUP BY category
        ORDER BY markets DESC
    """)

    log_api_request(account["api_key"], "/v1/categories", 200, len(rows))
    return rows

@app.get("/v1/stats")
def stats(account=Depends(verify_api_key)):
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

@app.get("/signup", response_class=HTMLResponse, include_in_schema=False)
def signup_page():
    return """
<!DOCTYPE html>
<html>
<head>
<title>Sign Up</title>

<style>

body{
    margin:0;
    background:#0b1020;
    font-family:Arial,sans-serif;
    color:white;
    display:flex;
    justify-content:center;
    align-items:center;
    height:100vh;
}

.card{

    width:420px;
    background:#111827;
    padding:40px;
    border-radius:18px;
    border:1px solid #1f2937;
    box-shadow:0 20px 60px rgba(0,0,0,.45);

}

h1{

    text-align:center;
    margin-bottom:10px;

}

p{

    color:#94a3b8;
    text-align:center;

}

input{

    width:100%;
    padding:14px;
    margin-top:16px;
    border:none;
    border-radius:10px;
    background:#1e293b;
    color:white;
    font-size:16px;
    box-sizing:border-box;

}

button{

    width:100%;
    padding:14px;
    margin-top:22px;
    border:none;
    border-radius:10px;
    background:#22c55e;
    color:white;
    font-size:16px;
    cursor:pointer;

}

button:hover{

    background:#16a34a;

}

a{

    color:#38bdf8;
    text-decoration:none;

}

.footer{

    text-align:center;
    margin-top:25px;

}

</style>

</head>

<body>

<div class="card">

<h1>Create your account</h1>

<p>Start using the Prediction Market Dataset API</p>

<form method="post" action="/signup">

<input
type="email"
name="email"
placeholder="Email"
required>

<input
type="password"
name="password"
placeholder="Password"
required>

<button>Create Account</button>

</form>

<div class="footer">

Already have an account?

<a href="/login">Login</a>

</div>

</div>

</body>

</html>
"""

@app.post("/signup", include_in_schema=False)
def signup(email: str = Form(...), password: str = Form(...)):
    try:
        email = email.strip().lower()

        auth_result = supabase.auth.sign_up({
            "email": email,
            "password": password,
        })

        if not auth_result.user:
            raise Exception("Supabase did not return a user for this signup.")

        ensure_api_key_for_user(email=email, user_id=auth_result.user.id)

        return RedirectResponse(url=f"/dashboard?email={email}", status_code=303)

    except Exception as e:
        return HTMLResponse(
            f"<h1>Signup failed</h1><pre>{str(e)}</pre><p><a href='/signup'>Try again</a></p>",
            status_code=500,
        )

@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page():
    return """
    <html>
    <body style="font-family:Arial;background:#0b1020;color:white;display:flex;justify-content:center;align-items:center;height:100vh;">
        <form method="post" action="/login" style="background:#111827;padding:40px;border-radius:12px;width:400px;">
            <h1>Login</h1>

            <input
                name="email"
                type="email"
                placeholder="Email"
                required
                style="width:100%;padding:12px;margin:10px 0;"
            >

            <input
                name="password"
                type="password"
                placeholder="Password"
                required
                style="width:100%;padding:12px;margin:10px 0;"
            >

            <button
                type="submit"
                style="width:100%;padding:12px;background:#22c55e;color:white;border:none;border-radius:8px;">
                Login
            </button>
        </form>
    </body>
    </html>
    """


@app.post("/login", include_in_schema=False)
def login(email: str = Form(...), password: str = Form(...)):
    try:
        email = email.strip().lower()

        result = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password,
        })

        if not result.user:
            raise Exception("Login failed: no user returned by Supabase.")

        ensure_api_key_for_user(email=result.user.email, user_id=result.user.id)

        return RedirectResponse(
            url=f"/dashboard?email={result.user.email}",
            status_code=303,
        )

    except Exception as e:
        return HTMLResponse(
            f"<h2>Login failed</h2><pre>{e}</pre><p><a href='/login'>Try again</a></p>",
            status_code=401,
        )

@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard(email: str):
    result = supabase.table("api_keys").select("*").eq("email", email).limit(1).execute()
    row = result.data[0] if result.data else None

    if not row:
        return """
        <h1>No API key found</h1>
        <p>This account exists in Auth but has no api_keys row yet.</p>
        <p>Please log out and log in again from <a href="/login">/login</a>. The login route will create the missing API key automatically.</p>
        """

    requests_today = row.get("requests_today", 0) or 0
    daily_limit = row.get("daily_limit", 100) or 100
    usage_pct = min(int((requests_today / daily_limit) * 100), 100)

    return f"""
<!DOCTYPE html>
<html>
<head>
<title>Dashboard</title>
<style>
body {{
    margin:0;
    background:#0b1020;
    font-family:Arial,sans-serif;
    color:white;
}}
.container {{
    max-width:1100px;
    margin:auto;
    padding:50px 24px;
}}
.header {{
    display:flex;
    justify-content:space-between;
    align-items:center;
    margin-bottom:35px;
}}
h1 {{
    font-size:42px;
    margin:0;
}}
a {{
    color:#38bdf8;
    text-decoration:none;
}}
.grid {{
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:20px;
}}
.card {{
    background:#111827;
    border:1px solid #1f2937;
    border-radius:18px;
    padding:26px;
    box-shadow:0 20px 60px rgba(0,0,0,.35);
}}
.label {{
    color:#94a3b8;
    font-size:14px;
}}
.value {{
    font-size:30px;
    font-weight:bold;
    margin-top:10px;
}}
.api-key {{
    background:#020617;
    border:1px solid #1f2937;
    padding:18px;
    border-radius:12px;
    word-break:break-all;
    color:#22c55e;
    margin-top:15px;
}}
.progress {{
    height:14px;
    background:#1e293b;
    border-radius:999px;
    overflow:hidden;
    margin-top:18px;
}}
.bar {{
    height:100%;
    width:{usage_pct}%;
    background:#22c55e;
}}
.actions {{
    margin-top:35px;
    display:flex;
    gap:15px;
}}
.button {{
    padding:14px 20px;
    border-radius:10px;
    background:#22c55e;
    color:white;
    font-weight:bold;
}}
.secondary {{
    background:#1e293b;
}}
@media(max-width:800px) {{
    .grid {{ grid-template-columns:1fr; }}
    .header {{ flex-direction:column; align-items:flex-start; gap:15px; }}
}}
</style>
</head>
<body>
<div class="container">

<div class="header">
    <div>
        <h1>Dashboard</h1>
        <p class="label">Welcome back, {row["email"]}</p>
    </div>
    <a href="/">← Home</a>
</div>

<div class="grid">
    <div class="card">
        <div class="label">Current Plan</div>
        <div class="value">{row.get("plan", "developer").upper()}</div>
        <p class="label">Status: {row.get("subscription_status", "free")}</p>
    </div>

    <div class="card">
        <div class="label">Requests Today</div>
        <div class="value">{requests_today:,} / {daily_limit:,}</div>
        <div class="progress"><div class="bar"></div></div>
        <p class="label">{usage_pct}% used</p>
    </div>

    <div class="card">
        <div class="label">Daily Limit</div>
        <div class="value">{daily_limit:,}</div>
        <p class="label">Upgrade for higher limits</p>
    </div>
</div>

<div class="card" style="margin-top:25px;">
    <div class="label">Your API Key</div>
    <div class="api-key">{row["api_key"]}</div>
</div>

<div class="actions">
    <a class="button" href="/docs">View API Docs</a>

    <a class="button secondary" href="https://prediction-market-dataset.onrender.com" target="_blank">
        Open Dataset Explorer
    </a>

    <button class="button secondary" onclick="copyApiKey()">
        Copy API Key
    </button>

    <form method="post" action="/dashboard/api-key/regenerate" style="margin:0;">
        <input type="hidden" name="email" value="{row["email"]}">
        <button class="button danger" type="submit">
            Regenerate API Key
        </button>
    </form>

    <form method="post" action="/billing/checkout/developer" style="margin:0;">
    <input type="hidden" name="email" value="{row["email"]}">
    <button class="button secondary" type="submit">
        Upgrade to Developer
    </button>
</form>

<form method="post" action="/billing/checkout/professional" style="margin:0;">
    <input type="hidden" name="email" value="{row["email"]}">
    <button class="button secondary" type="submit">
        Upgrade to Professional
    </button>
</form>
</div>

<script>
function copyApiKey() {{
    navigator.clipboard.writeText("{row["api_key"]}");
    alert("API key copied");
}}
</script>

</div>
</body>
</html>
"""

@app.post("/dashboard/api-key/regenerate", include_in_schema=False)
def dashboard_regenerate_api_key(email: str = Form(...)):
    new_key = make_api_key()

    result = (
        supabase.table("api_keys")
        .update({"api_key": new_key})
        .eq("email", email)
        .execute()
    )

    return RedirectResponse(url=f"/dashboard?email={email}", status_code=303)

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Prediction Market Dataset API</title>
        <style>
            body {
                margin: 0;
                font-family: Arial, sans-serif;
                background: #0b1020;
                color: #f8fafc;
            }
            .container {
                max-width: 1100px;
                margin: auto;
                padding: 60px 24px;
            }
            .hero {
                text-align: center;
                padding: 70px 0;
            }
            h1 {
                font-size: 56px;
                margin-bottom: 16px;
            }
            p {
                color: #cbd5e1;
                font-size: 18px;
                line-height: 1.6;
            }
            .buttons a {
                display: inline-block;
                margin: 12px;
                padding: 14px 22px;
                border-radius: 8px;
                text-decoration: none;
                font-weight: bold;
            }
            .primary {
                background: #38bdf8;
                color: #020617;
            }
            .secondary {
                border: 1px solid #475569;
                color: #f8fafc;
            }
            .danger {
                background:#dc2626;
            }

            .danger:hover {
                background:#b91c1c;
            }

            .actions form {
                display:inline;
            }
            .stats, .features, .pricing {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 20px;
                margin-top: 40px;
            }
            .card {
                background: #111827;
                border: 1px solid #1e293b;
                padding: 24px;
                border-radius: 14px;
            }
            .price {
                font-size: 34px;
                font-weight: bold;
                color: #38bdf8;
            }
            code {
                display: block;
                background: #020617;
                border: 1px solid #1e293b;
                padding: 20px;
                border-radius: 12px;
                overflow-x: auto;
                color: #a7f3d0;
            }
            footer {
                margin-top: 70px;
                text-align: center;
                color: #64748b;
            }
            @media (max-width: 800px) {
                h1 { font-size: 38px; }
                .stats, .features, .pricing {
                    grid-template-columns: 1fr;
                }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <section class="hero">
                <h1>Prediction Market Dataset API</h1>
                <p>
                    Unified historical and live prediction market data from
                    Polymarket, Kalshi, Manifold, and PredictIt.
                </p>
                <div class="buttons">
                    <a class="primary" href="/docs">View API Docs</a>
                    <a class="secondary" href="#pricing">View Pricing</a>
                </div>
            </section>

            <section class="stats">
                <div class="card">
                    <h2>4.7M+</h2>
                    <p>Market snapshots collected</p>
                </div>
                <div class="card">
                    <h2>470K+</h2>
                    <p>Unique prediction markets</p>
                </div>
                <div class="card">
                    <h2>4</h2>
                    <p>Supported platforms</p>
                </div>
            </section>

            <h2>Built for developers, researchers, and data teams</h2>
            <section class="features">
                <div class="card">
                    <h3>Unified API</h3>
                    <p>Query multiple prediction market platforms through one normalized API.</p>
                </div>
                <div class="card">
                    <h3>Historical Data</h3>
                    <p>Access market history, snapshots, prices, volume, and liquidity.</p>
                </div>
                <div class="card">
                    <h3>Search & Discovery</h3>
                    <p>Search markets by keyword and browse categories, movers, and platforms.</p>
                </div>
            </section>

            <h2>Example Request</h2>
            <code>
GET /v1/search?q=bitcoin<br>
Authorization: Bearer YOUR_API_KEY
            </code>

            <h2 id="pricing">Pricing</h2>
            <section class="pricing">
                <div class="card">
                    <h3>Developer</h3>
                    <div class="price">£19/mo</div>
                    <p>For testing, prototypes, and individual developers.</p>
                    <p>Monthly, 3-month, 6-month, and annual billing available.</p>
                </div>
                <div class="card">
                    <h3>Professional</h3>
                    <div class="price">£49/mo</div>
                    <p>For production applications, research teams, and serious users.</p>
                    <p>Higher limits and priority feature access.</p>
                </div>
                <div class="card">
                    <h3>Enterprise</h3>
                    <div class="price">Custom</div>
                    <p>For companies, funds, universities, and data teams.</p>
                    <p>Custom limits, exports, and support.</p>
                </div>
            </section>

            <footer>
                Prediction Market Dataset API · Live cross-platform market data
            </footer>
        </div>
    </body>
    </html>
    """