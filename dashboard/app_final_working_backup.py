import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Set

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st
import stripe
from dotenv import load_dotenv
from streamlit_autorefresh import st_autorefresh
from supabase import create_client


# =========================
# Configuration
# =========================

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "data/warehouse.duckdb")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8501")

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PRO_PRICE_ID = os.getenv("STRIPE_PRO_PRICE_ID")
STRIPE_API_PRICE_ID = os.getenv("STRIPE_API_PRICE_ID")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

st.set_page_config(page_title="Prediction Market Dataset", layout="wide")
st_autorefresh(interval=60_000, key="dashboard_refresh")


# =========================
# Utility functions
# =========================


def normalize_email(email: Optional[str]) -> str:
    return str(email or "").strip().lower()


def get_query_param(name: str, default: Optional[str] = None) -> Optional[str]:
    value = st.query_params.get(name, default)
    if isinstance(value, list):
        return value[0] if value else default
    return value


def safe_str(value: Any) -> str:
    return str(value or "").strip()


def show_df(df: pd.DataFrame, **kwargs: Any) -> None:
    st.dataframe(df, use_container_width=True, hide_index=True, **kwargs)


def format_percent(value: Any, decimals: int = 2) -> str:
    try:
        return f"{float(value) * 100:.{decimals}f}%"
    except Exception:
        return ""


def format_percent_from_points(value: Any, decimals: int = 2) -> str:
    try:
        return f"{float(value):.{decimals}f}%"
    except Exception:
        return ""


def platform_label(platform: Any) -> str:
    labels = {
        "polymarket": "🟣 Polymarket",
        "predictit": "🔵 PredictIt",
        "kalshi": "🟠 Kalshi",
        "manifold": "🟢 Manifold",
    }
    key = safe_str(platform).lower()
    return labels.get(key, safe_str(platform).title())


def open_db() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB_PATH, read_only=True)


def sql_df(conn: duckdb.DuckDBPyConnection, query: str, params: Optional[Iterable[Any]] = None) -> pd.DataFrame:
    if params is None:
        return conn.execute(query).df()
    return conn.execute(query, list(params)).df()


# =========================
# Subscription/auth functions
# =========================


def get_subscription_status(email: Optional[str]) -> str:
    email = normalize_email(email)
    if not email:
        return "free"

    try:
        result = (
            supabase.table("user_subscriptions")
            .select("status")
            .eq("email", email)
            .execute()
        )
        if result.data:
            status = str(result.data[0].get("status", "free")).lower()
            if status in {"pro", "api", "free"}:
                return status
            if status == "active":
                return "pro"
    except Exception:
        pass

    return "free"


def subscription_flags(status: str) -> Dict[str, bool]:
    status = str(status or "free").lower()
    return {
        "is_free": status == "free",
        "is_pro": status in {"pro", "api"},
        "is_api": status == "api",
    }


def create_checkout_session(email: str, plan: str) -> Optional[str]:
    email = normalize_email(email)
    plan = plan.lower()

    if plan == "api":
        price_id = STRIPE_API_PRICE_ID
    else:
        price_id = STRIPE_PRO_PRICE_ID
        plan = "pro"

    if not stripe.api_key:
        st.error("Missing STRIPE_SECRET_KEY in .env")
        return None

    if not price_id:
        st.error(f"Missing Stripe price ID for {plan.upper()} in .env")
        return None

    try:
        checkout_session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=email,
            success_url=f"{APP_BASE_URL}/?payment=success&email={email}&plan={plan}",
            cancel_url=APP_BASE_URL,
        )
        return checkout_session.url
    except Exception as exc:
        st.error(f"Stripe checkout failed: {exc}")
        return None


def activate_subscription_from_query_params() -> None:
    if get_query_param("payment") != "success":
        return

    paid_email = normalize_email(get_query_param("email"))
    paid_plan = str(get_query_param("plan", "pro") or "pro").lower()

    if paid_plan not in {"pro", "api"}:
        paid_plan = "pro"

    if not paid_email:
        st.error("Payment succeeded, but no email was returned.")
        return

    try:
        supabase.table("user_subscriptions").upsert(
            {"email": paid_email, "status": paid_plan},
            on_conflict="email",
        ).execute()
        st.session_state.user_email = paid_email
        st.success(f"Subscription activated: {paid_plan.upper()}")
        st.query_params.clear()
        st.rerun()
    except Exception as exc:
        st.error(f"Could not activate subscription: {exc}")


def render_auth_sidebar(status: str, is_pro: bool, is_api: bool) -> None:
    st.sidebar.subheader("Account")

    if st.session_state.get("user_email"):
        user_email = normalize_email(st.session_state.user_email)
        st.sidebar.success(user_email)

        if is_api:
            st.sidebar.success("Subscription: API Pro")
        elif is_pro:
            st.sidebar.success("Subscription: Pro")
        else:
            st.sidebar.info("Subscription: Free")

        if not is_pro:
            if st.sidebar.button("Upgrade to Pro"):
                url = create_checkout_session(user_email, "pro")
                if url:
                    st.sidebar.link_button("Open Stripe Checkout", url)

        if not is_api:
            if st.sidebar.button("Upgrade to API Pro"):
                url = create_checkout_session(user_email, "api")
                if url:
                    st.sidebar.link_button("Open Stripe Checkout", url)

        if st.sidebar.button("Log out"):
            try:
                supabase.auth.sign_out()
            except Exception:
                pass
            st.session_state.clear()
            st.query_params.clear()
            st.rerun()

    else:
        auth_mode = st.sidebar.radio("Account", ["Log in", "Sign up"], horizontal=True)
        email = st.sidebar.text_input("Email")
        password = st.sidebar.text_input("Password", type="password")

        if auth_mode == "Sign up":
            if st.sidebar.button("Create account"):
                try:
                    supabase.auth.sign_up(
                        {"email": normalize_email(email), "password": password}
                    )
                    st.sidebar.success("Account created. You can now log in.")
                except Exception as exc:
                    st.sidebar.error(f"Signup failed: {exc}")

        if auth_mode == "Log in":
            if st.sidebar.button("Log in"):
                try:
                    result = supabase.auth.sign_in_with_password(
                        {"email": normalize_email(email), "password": password}
                    )
                    st.session_state.user_email = normalize_email(email)
                    st.session_state.session = result.session
                    st.rerun()
                except Exception as exc:
                    st.sidebar.error(f"Login failed: {exc}")


# =========================
# Matching helpers
# =========================


def extract_year(title: Any) -> Optional[str]:
    match = re.search(r"\b(20[2-9][0-9])\b", str(title))
    return match.group(1) if match else None


def extract_party(title: Any) -> Optional[str]:
    title = str(title).lower()
    if "democratic" in title or "democrat" in title:
        return "democratic"
    if "republican" in title or "gop" in title:
        return "republican"
    return None


def extract_office(title: Any) -> Optional[str]:
    title = str(title).lower()
    if "vice president" in title or "vice presidential" in title:
        return "vice_president"
    if "president" in title or "presidential" in title:
        return "president"
    return None


def extract_event_type(title: Any) -> str:
    title = str(title).lower()
    if "nomination" in title or "nominee" in title:
        return "nomination"
    if "president" in title or "election" in title:
        return "election"
    if "world cup" in title or "fifa" in title:
        return "world_cup"
    if "bitcoin" in title or "btc" in title:
        return "bitcoin"
    if "ethereum" in title or "eth" in title:
        return "ethereum"
    if "fed" in title or "interest rate" in title or "rate cut" in title:
        return "fed_rates"
    return "other"


def extract_candidate(title: Any) -> Optional[str]:
    title = str(title).lower()
    candidates = [
        "gavin newsom",
        "kamala harris",
        "pete buttigieg",
        "andy beshear",
        "jon ossoff",
        "mark kelly",
        "rahm emanuel",
        "wes moore",
        "gretchen whitmer",
        "josh shapiro",
        "donald trump",
        "joe biden",
        "ron desantis",
        "j d vance",
        "jd vance",
        "ro khanna",
        "cory booker",
        "alexandria ocasio-cortez",
        "aoc",
        "jb pritzker",
        "j.b. pritzker",
    ]
    for candidate in candidates:
        if candidate in title:
            return candidate.replace("j d", "jd").replace("j.b.", "jb")
    return None


def canonical_key(title: Any) -> Dict[str, Optional[str]]:
    return {
        "year": extract_year(title),
        "party": extract_party(title),
        "event_type": extract_event_type(title),
        "candidate": extract_candidate(title),
        "office": extract_office(title),
    }


def normalize_title(title: Any) -> str:
    if not title:
        return ""

    title = str(title).lower()
    title = re.sub(r"[^a-z0-9\s]", " ", title)

    stopwords = {
        "will", "yes", "no", "the", "a", "an", "in", "on", "for", "of", "to",
        "with", "and", "or", "who", "what", "when", "win", "wins", "winner",
        "market", "markets", "nomination", "nominee", "presidential", "president",
        "democratic", "democrat", "republican", "election", "2024", "2025", "2026",
        "2027", "2028", "2029", "2030",
    }

    words = [word for word in title.split() if word not in stopwords and len(word) > 2]
    return " ".join(words[:8])


def similarity(a: Any, b: Any) -> float:
    return SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()


def extract_entities(title: Any) -> Set[str]:
    text = safe_str(title)
    lower_text = text.lower()

    ignore_words = {
        "will", "yes", "no", "who", "what", "when", "where", "which",
        "the", "a", "an", "in", "on", "by", "to", "of", "for", "and",
        "or", "vs", "fifa", "world", "cup", "election", "presidential",
        "president", "nomination", "nominee", "market", "markets",
    }

    entities: Set[str] = set()

    matches = re.findall(
        r"\b(?:[A-Z][a-z]+|[A-Z]{2,}|\d+[A-Za-z]+)(?:\s+(?:[A-Z][a-z]+|[A-Z]{2,}|\d+[A-Za-z]+))*\b",
        text,
    )

    for match in matches:
        cleaned = match.strip().lower()
        parts = [p for p in cleaned.split() if p not in ignore_words and len(p) > 1]
        if parts:
            entities.add(" ".join(parts))

    aliases = {
        "cr7", "messi", "btc", "bitcoin", "eth", "ethereum", "trump", "biden",
        "kamala", "harris", "newsom", "desantis", "vance", "aoc",
    }
    for alias in aliases:
        if alias in lower_text:
            entities.add(alias)

    priority_names = [
        "kamala harris",
        "gavin newsom",
        "gretchen whitmer",
        "josh shapiro",
        "jon ossoff",
        "mark kelly",
        "rahm emanuel",
        "jb pritzker",
        "j.b. pritzker",
        "wes moore",
        "andy beshear",
        "ro khanna",
        "pete buttigieg",
        "cory booker",
    ]
    for name in priority_names:
        if name in lower_text:
            return {name.replace("j.b.", "jb")}

    return entities


def pretty_name(value: Any) -> str:
    names = [n.strip().title() for n in str(value or "").split(",") if n.strip()]
    return " / ".join(names)


# =========================
# App setup and global state
# =========================

if "user_email" not in st.session_state:
    st.session_state.user_email = None

activate_subscription_from_query_params()

current_email = normalize_email(st.session_state.get("user_email"))
subscription_status = get_subscription_status(current_email)
flags = subscription_flags(subscription_status)
is_pro = flags["is_pro"]
is_api = flags["is_api"]

st.title("Prediction Market Dataset")
st.caption("Live cross-platform prediction market data warehouse")

render_auth_sidebar(subscription_status, is_pro, is_api)

page = st.sidebar.radio(
    "Navigation",
    [
        "Dashboard",
        "Markets",
        "Platforms",
        "Movers",
        "Market Comparison",
        "Health",
        "Market Detail",
        "API",
    ]
)

conn = open_db()

try:
    platforms_df = sql_df(
        conn,
        """
        SELECT DISTINCT platform
        FROM market_snapshots
        WHERE platform IS NOT NULL
        ORDER BY platform
        """,
    )
    platforms = ["All"] + platforms_df["platform"].dropna().tolist()
except Exception:
    platforms = ["All"]

selected_platform = st.sidebar.selectbox("Platform", platforms)
search = st.sidebar.text_input("Search Markets", key="sidebar_market_search")

filters: List[str] = []

if selected_platform != "All":
    safe_platform = str(selected_platform).replace("'", "''")
    filters.append(f"platform = '{safe_platform}'")

if search:
    safe_search = search.lower().replace("'", "''")
    filters.append(f"LOWER(title) LIKE '%{safe_search}%'")

where_clause = "WHERE " + " AND ".join(filters) if filters else ""


def latest_extra_filter() -> str:
    if filters:
        return "AND " + " AND ".join(filters)
    return ""


# =========================
# Pages
# =========================

try:
    if page == "Dashboard":
        total_rows = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM market_snapshots
            {where_clause}
            """
        ).fetchone()[0]

        unique_markets = conn.execute(
            f"""
            SELECT COUNT(DISTINCT market_id)
            FROM market_snapshots
            {where_clause}
            """
        ).fetchone()[0]

        snapshots = conn.execute(
            f"""
            SELECT COUNT(DISTINCT snapshot_time)
            FROM market_snapshots
            {where_clause}
            """
        ).fetchone()[0]

        latest_snapshot = conn.execute(
            f"""
            SELECT MAX(snapshot_time)
            FROM market_snapshots
            {where_clause}
            """
        ).fetchone()[0]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Rows", f"{total_rows:,}")
        c2.metric("Unique Markets", f"{unique_markets:,}")
        c3.metric("Snapshots", f"{snapshots:,}")
        c4.metric("Latest Snapshot", str(latest_snapshot)[:22])

        st.divider()
        st.subheader("Platform Coverage")

        platform_stats = sql_df(
            conn,
            f"""
            SELECT
                platform,
                COUNT(*) AS rows,
                COUNT(DISTINCT market_id) AS unique_markets,
                AVG(volume) AS avg_volume,
                AVG(liquidity) AS avg_liquidity
            FROM market_snapshots
            {where_clause}
            GROUP BY platform
            ORDER BY rows DESC
            """,
        )

        show_df(platform_stats)

        growth = sql_df(
            conn,
            f"""
            SELECT
                snapshot_time,
                COUNT(*) AS rows
            FROM market_snapshots
            {where_clause}
            GROUP BY snapshot_time
            ORDER BY snapshot_time
            """,
        )

        if not growth.empty:
            growth["snapshot_time"] = pd.to_datetime(growth["snapshot_time"])
            fig = px.line(
                growth,
                x="snapshot_time",
                y="rows",
                markers=True,
                title="Rows Collected Over Time",
            )
            st.plotly_chart(fig, use_container_width=True)

        if not platform_stats.empty:
            fig_rows = px.bar(platform_stats, x="platform", y="rows", title="Rows By Platform")
            st.plotly_chart(fig_rows, use_container_width=True)

            fig_unique = px.bar(
                platform_stats,
                x="platform",
                y="unique_markets",
                title="Unique Markets By Platform",
            )
            st.plotly_chart(fig_unique, use_container_width=True)

        st.subheader("Snapshot Growth")

    elif page == "Markets":
        st.subheader("Latest Top Volume Markets")

        top_volume = sql_df(
            conn,
            f"""
            WITH latest AS (
                SELECT *
                FROM market_snapshots
                WHERE snapshot_time = (
                    SELECT MAX(snapshot_time)
                    FROM market_snapshots
                )
            )
            SELECT
                platform,
                market_id,
                title,
                yes_price,
                no_price,
                volume,
                liquidity,
                snapshot_time
            FROM latest
            WHERE 1=1
            {latest_extra_filter()}
            ORDER BY volume DESC NULLS LAST
            LIMIT 100
            """,
        )
        show_df(top_volume)

        st.subheader("Latest Snapshot Explorer")
        latest_snapshot_df = sql_df(
            conn,
            f"""
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
            {latest_extra_filter()}
            ORDER BY platform, volume DESC NULLS LAST
            LIMIT 300
            """,
        )
        show_df(latest_snapshot_df)

    elif page == "Platforms":
        st.subheader("Platform Comparison")

        platform_stats = sql_df(
            conn,
            f"""
            SELECT
                platform,
                COUNT(*) AS rows,
                COUNT(DISTINCT market_id) AS unique_markets,
                AVG(volume) AS avg_volume,
                AVG(liquidity) AS avg_liquidity,
                MIN(snapshot_time) AS first_snapshot,
                MAX(snapshot_time) AS latest_snapshot
            FROM market_snapshots
            {where_clause}
            GROUP BY platform
            ORDER BY rows DESC
            """,
        )
        show_df(platform_stats)

        if not platform_stats.empty:
            fig_rows = px.bar(platform_stats, x="platform", y="rows", title="Rows By Platform")
            st.plotly_chart(fig_rows, use_container_width=True)

            fig_unique = px.bar(
                platform_stats,
                x="platform",
                y="unique_markets",
                title="Unique Markets By Platform",
            )
            st.plotly_chart(fig_unique, use_container_width=True)

    elif page == "Market Comparison":
        if not current_email:
            st.warning("Please log in to view the Opportunity Scanner.")
            st.stop()

        if not is_pro:
            st.warning("Upgrade to Pro to view the Opportunity Scanner.")
            if st.button("Upgrade to Pro"):
                url = create_checkout_session(current_email, "pro")
                if url:
                    st.link_button("Open Stripe Checkout", url)
            st.stop()

        st.subheader("Cross-Platform Market Comparison")
        st.caption(
            "Compare equivalent prediction markets across platforms and identify pricing differences."
        )

        latest = sql_df(
            conn,
            """
            WITH latest AS (
                SELECT *
                FROM market_snapshots
                WHERE snapshot_time = (
                    SELECT MAX(snapshot_time)
                    FROM market_snapshots
                )
            )
            SELECT
                platform,
                market_id,
                title,
                yes_price,
                no_price,
                volume,
                liquidity,
                raw_url
            FROM latest
            WHERE yes_price IS NOT NULL
              AND title IS NOT NULL
            """,
        )

        if latest.empty:
            st.info("No opportunity data found yet. Let the scheduler collect more snapshots.")
            st.stop()

        st.sidebar.subheader("Comparison Filters")
        min_match_score = st.sidebar.slider("Minimum Similarity", 0.70, 1.00, 0.92, 0.01)
        min_spread = st.sidebar.slider("Minimum Price Difference", 0.00, 0.20, 0.01, 0.005)
        min_liquidity = st.sidebar.number_input("Minimum Liquidity", value=0.0)
        keyword_filter = st.sidebar.text_input("Market Keyword")
        platform_pair = st.sidebar.selectbox(
            "Platform Pair",
            [
                "All",
                "polymarket ↔ predictit",
                "polymarket ↔ manifold",
                "polymarket ↔ kalshi",
                "predictit ↔ manifold",
                "predictit ↔ kalshi",
                "kalshi ↔ manifold",
            ],
        )

        platform_counts = (
            latest.groupby("platform")
            .agg(rows=("market_id", "count"), markets=("market_id", "nunique"), with_price=("yes_price", "count"))
            .reset_index()
        )
        st.subheader("Latest Snapshot Platform Counts")
        show_df(platform_counts)

        rows = latest.to_dict("records")
        matches: List[Dict[str, Any]] = []
        seen_pairs = set()

        for i, a in enumerate(rows):
            for b in rows[i + 1 :]:
                platform_a = safe_str(a.get("platform")).lower()
                platform_b = safe_str(b.get("platform")).lower()

                if platform_a == platform_b:
                    continue

                title_a = safe_str(a.get("title"))
                title_b = safe_str(b.get("title"))

                if not title_a or not title_b:
                    continue

                key_a = canonical_key(title_a)
                key_b = canonical_key(title_b)

                for field in ["year", "party", "event_type", "candidate", "office"]:
                    if key_a[field] and key_b[field] and key_a[field] != key_b[field]:
                        break
                else:
                    entities_a = extract_entities(title_a)
                    entities_b = extract_entities(title_b)

                    if entities_a and entities_b and entities_a.isdisjoint(entities_b):
                        continue

                    norm_a = normalize_title(title_a)
                    norm_b = normalize_title(title_b)

                    if not norm_a or not norm_b:
                        continue

                    score = similarity(norm_a, norm_b)
                    if score < min_match_score:
                        continue

                    price_a = a.get("yes_price")
                    price_b = b.get("yes_price")
                    if price_a is None or price_b is None:
                        continue

                    spread = abs(float(price_a) - float(price_b))
                    if spread < min_spread:
                        continue

                    liquidity_a = a.get("liquidity") or 0
                    liquidity_b = b.get("liquidity") or 0
                    if float(liquidity_a) + float(liquidity_b) < min_liquidity:
                        continue

                    pair_key = tuple(
                        sorted(
                            [
                                f"{platform_a}:{a.get('market_id')}",
                                f"{platform_b}:{b.get('market_id')}",
                            ]
                        )
                    )
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    matches.append(
                        {
                            "title_a": title_a,
                            "title_b": title_b,
                            "platform_a": platform_a,
                            "platform_b": platform_b,
                            "market_id_a": a.get("market_id"),
                            "market_id_b": b.get("market_id"),
                            "price_a": round(float(price_a), 4),
                            "price_b": round(float(price_b), 4),
                            "spread": round(float(spread), 4),
                            "match_score": round(float(score), 3),
                            "topic": key_a["event_type"] if key_a["event_type"] != "other" else key_b["event_type"],
                            "entities_a": ", ".join(sorted(entities_a)),
                            "entities_b": ", ".join(sorted(entities_b)),
                            "volume_a": a.get("volume"),
                            "volume_b": b.get("volume"),
                            "liquidity_a": liquidity_a,
                            "liquidity_b": liquidity_b,
                            "url_a": a.get("raw_url"),
                            "url_b": b.get("raw_url"),
                        }
                    )

        opportunities = pd.DataFrame(matches)

        if not opportunities.empty:
            opportunities["opportunity"] = opportunities["entities_a"].apply(pretty_name)
            opportunities.loc[opportunities["opportunity"] == "", "opportunity"] = opportunities["title_a"]

        if keyword_filter and not opportunities.empty:
            keyword = keyword_filter.lower()
            opportunities = opportunities[
                opportunities["title_a"].str.lower().str.contains(keyword, na=False)
                | opportunities["title_b"].str.lower().str.contains(keyword, na=False)
                | opportunities["opportunity"].str.lower().str.contains(keyword, na=False)
            ]

        if platform_pair != "All" and not opportunities.empty:
            left, right = [x.strip() for x in platform_pair.split("↔")]
            opportunities = opportunities[
                ((opportunities["platform_a"] == left) & (opportunities["platform_b"] == right))
                | ((opportunities["platform_a"] == right) & (opportunities["platform_b"] == left))
            ]

        if opportunities.empty:
            st.info("No cross-platform opportunities found with the current filters.")
        else:
            opportunities["edge_pct"] = opportunities["spread"] * 100
            opportunities["opportunity_score"] = (
                opportunities["spread"] * 100 + opportunities["match_score"] * 10
            ).round(2)

            sort_method = st.selectbox(
                "Sort Comparisons By",
                ["Comparison Score", "Edge", "Liquidity", "Match Score"],
            )

            if sort_method == "Comparison Score":
                opportunities = opportunities.sort_values("opportunity_score", ascending=False)
            elif sort_method == "Edge":
                opportunities = opportunities.sort_values("spread", ascending=False)
            elif sort_method == "Liquidity":
                opportunities["total_liquidity"] = opportunities["liquidity_a"].fillna(0) + opportunities["liquidity_b"].fillna(0)
                opportunities = opportunities.sort_values("total_liquidity", ascending=False)
            else:
                opportunities = opportunities.sort_values("match_score", ascending=False)

            opportunities["buy_platform"] = opportunities.apply(
                lambda r: r["platform_a"] if r["price_a"] < r["price_b"] else r["platform_b"],
                axis=1,
            )
            opportunities["buy_price"] = opportunities.apply(lambda r: min(r["price_a"], r["price_b"]), axis=1)
            opportunities["sell_platform"] = opportunities.apply(
                lambda r: r["platform_b"] if r["price_a"] < r["price_b"] else r["platform_a"],
                axis=1,
            )
            opportunities["sell_price"] = opportunities.apply(lambda r: max(r["price_a"], r["price_b"]), axis=1)
            opportunities = opportunities.head(100)

            c1, c2, c3 = st.columns(3)
            c1.metric("Markets Compared", len(opportunities))
            c2.metric("Largest Price Difference", f"{opportunities['edge_pct'].max():.2f}%")
            c3.metric("Average Price Difference", f"{opportunities['edge_pct'].mean():.2f}%")

            opportunities["market_a_link"] = opportunities["url_a"].apply(
                lambda x: x if str(x).startswith("http") else f"https://polymarket.com/market/{x}"
            )
            opportunities["market_b_link"] = opportunities["url_b"].apply(
                lambda x: x if str(x).startswith("http") else f"https://www.predictit.org/markets/detail/{x}"
            )

            display_df = opportunities[
                [
                    "opportunity",
                    "buy_platform",
                    "buy_price",
                    "sell_platform",
                    "sell_price",
                    "edge_pct",
                    "match_score",
                    "opportunity_score",
                    "market_a_link",
                    "market_b_link",
                ]
            ].copy()
            display_df_numeric = display_df.copy()

            display_df["buy_platform"] = display_df["buy_platform"].apply(platform_label)
            display_df["sell_platform"] = display_df["sell_platform"].apply(platform_label)

            display_df = display_df.rename(
                columns={
                    "opportunity": "Market",
                    "buy_platform": "Buy On",
                    "buy_price": "Buy Price",
                    "sell_platform": "Sell On",
                    "sell_price": "Sell Price",
                    "edge_pct": "Edge %",
                    "match_score": "Match",
                    "opportunity_score": "Comparison Score",
                    "market_a_link": "Market A",
                    "market_b_link": "Market B",
                }
            )

            display_df["Buy Price"] = display_df["Buy Price"].apply(lambda x: f"{x:.2%}")
            display_df["Sell Price"] = display_df["Sell Price"].apply(lambda x: f"{x:.2%}")
            display_df["Match"] = display_df["Match"].apply(lambda x: f"{x * 100:.0f}%")
            display_df["Edge %"] = display_df["Edge %"].apply(lambda x: format_percent_from_points(x, 2))
            display_df["Comparison Score"] = (
                display_df["Comparison Score"].apply(lambda x: f"{x:.2f}")
            )

            styled = (
                display_df.style
                .background_gradient(subset=["Edge %"], cmap="Greens", gmap=display_df_numeric["edge_pct"])
                .background_gradient(subset=["Comparison Score"], cmap="Greens", gmap=display_df_numeric["opportunity_score"])
            )
            st.dataframe(styled, use_container_width=True)

            st.subheader("Largest Cross-Platform Price Differences")
            chart_df = opportunities.head(25).copy()
            chart_df["price_difference_pct"] = chart_df["spread"] * 100

            fig = px.bar(
                chart_df,
                x="price_difference_pct",
                y="opportunity",
                orientation="h",
                title="Top Cross-Platform Pricing Differences",
                labels={
                    "price_difference_pct": "Price Difference (%)",
                    "opportunity": "Market",
                },
            )
            st.plotly_chart(fig, use_container_width=True)

    elif page == "Movers":
        st.subheader("Market Movers")

        movers = sql_df(
            conn,
            f"""
            WITH market_changes AS (
                SELECT
                    platform,
                    market_id,
                    title,
                    COUNT(*) AS snapshots,
                    MIN(yes_price) AS low_price,
                    MAX(yes_price) AS high_price,
                    FIRST(yes_price ORDER BY snapshot_time) AS first_price,
                    LAST(yes_price ORDER BY snapshot_time) AS last_price,
                    LAST(yes_price ORDER BY snapshot_time) - FIRST(yes_price ORDER BY snapshot_time) AS price_change,
                    LAST(volume ORDER BY snapshot_time) - FIRST(volume ORDER BY snapshot_time) AS volume_change,
                    LAST(liquidity ORDER BY snapshot_time) - FIRST(liquidity ORDER BY snapshot_time) AS liquidity_change
                FROM market_snapshots
                WHERE yes_price IS NOT NULL
                {latest_extra_filter()}
                GROUP BY platform, market_id, title
                HAVING COUNT(*) >= 3
            )
            SELECT *
            FROM market_changes
            ORDER BY ABS(price_change) DESC NULLS LAST
            LIMIT 100
            """,
        )

        display_cols = [
            "platform",
            "market_id",
            "title",
            "snapshots",
            "low_price",
            "high_price",
            "first_price",
            "last_price",
            "price_change",
            "volume_change",
            "liquidity_change",
        ]

        if movers.empty:
            st.info("No movers found yet. Let the scheduler collect more snapshots.")
        else:
            show_df(movers[display_cols])
            st.subheader("Top Gainers")
            show_df(movers.sort_values("price_change", ascending=False).head(25)[display_cols])
            st.subheader("Top Losers")
            show_df(movers.sort_values("price_change", ascending=True).head(25)[display_cols])
            st.subheader("Volume Movers")
            show_df(movers.sort_values("volume_change", ascending=False).head(25)[display_cols])
            st.subheader("Liquidity Movers")
            show_df(movers.sort_values("liquidity_change", ascending=False).head(25)[display_cols])

    elif page == "Health":
        st.subheader("Market Health")

        health = sql_df(
            conn,
            f"""
            SELECT
                platform,
                COUNT(*) AS total_rows,
                COUNT(DISTINCT market_id) AS total_markets,
                AVG(volume) AS avg_volume,
                AVG(liquidity) AS avg_liquidity,
                MIN(snapshot_time) AS first_snapshot,
                MAX(snapshot_time) AS latest_snapshot
            FROM market_snapshots
            {where_clause}
            GROUP BY platform
            ORDER BY total_rows DESC
            """,
        )

        if health.empty:
            st.info("No market health data found.")
        else:
            display_health = health.rename(
                columns={
                    "platform": "Platform",
                    "total_rows": "Snapshots",
                    "total_markets": "Markets",
                    "avg_volume": "Avg Volume",
                    "avg_liquidity": "Avg Liquidity",
                    "first_snapshot": "First Snapshot",
                    "latest_snapshot": "Latest Snapshot",
                }
            )
            st.dataframe(display_health, use_container_width=True, hide_index=True)

            col1, col2 = st.columns(2)
            with col1:
                fig_rows = px.bar(health, x="platform", y="total_rows", title="Rows By Platform")
                st.plotly_chart(fig_rows, use_container_width=True)
            with col2:
                fig_markets = px.bar(health, x="platform", y="total_markets", title="Markets By Platform")
                st.plotly_chart(fig_markets, use_container_width=True)

    elif page == "Market Detail":
        st.subheader("Market Detail")

        market_where = where_clause if where_clause else "WHERE market_id IS NOT NULL"
        if where_clause:
            market_where += " AND market_id IS NOT NULL"

        markets = sql_df(
            conn,
            f"""
            SELECT DISTINCT
                market_id,
                title,
                platform
            FROM market_snapshots
            {market_where}
            ORDER BY title
            LIMIT 3000
            """,
        )

        if markets.empty:
            st.info("No markets found.")
        else:
            markets["label"] = markets["title"].fillna("").str[:120] + " | " + markets["market_id"].fillna("")
            selected_label = st.selectbox(
                "Select a market",
                markets["label"].tolist(),
                key="market_detail_selected_market",
            )

            selected_market_id = markets.loc[markets["label"] == selected_label, "market_id"].iloc[0]
            history = sql_df(
                conn,
                """
                SELECT
                    snapshot_time,
                    platform,
                    market_id,
                    title,
                    yes_price,
                    no_price,
                    volume,
                    liquidity,
                    status,
                    raw_url
                FROM market_snapshots
                WHERE market_id = ?
                ORDER BY snapshot_time
                """,
                [selected_market_id],
            )

            show_df(history.tail(25))

            if not history.empty:
                history["snapshot_time"] = pd.to_datetime(history["snapshot_time"])
                latest_row = history.sort_values("snapshot_time").iloc[-1]

                c1, c2, c3 = st.columns(3)
                c1.metric("Latest YES", latest_row.get("yes_price"))
                c2.metric("Latest Volume", latest_row.get("volume"))
                c3.metric("Observations", len(history))

                st.subheader("YES Price History")
                price_df = history.dropna(subset=["yes_price"])
                if not price_df.empty:
                    fig_price = px.line(
                        price_df,
                        x="snapshot_time",
                        y="yes_price",
                        markers=True,
                        title="YES Price History",
                    )
                    st.plotly_chart(fig_price, use_container_width=True)
                else:
                    st.info("No YES price history available.")

                st.subheader("Volume History")
                volume_df = history.dropna(subset=["volume"])
                if not volume_df.empty:
                    fig_volume = px.bar(volume_df, x="snapshot_time", y="volume", title="Volume History")
                    st.plotly_chart(fig_volume, use_container_width=True)
                else:
                    st.info("No volume history available.")

                st.subheader("Liquidity History")
                liquidity_df = history.dropna(subset=["liquidity"])
                if not liquidity_df.empty:
                    fig_liquidity = px.area(
                        liquidity_df,
                        x="snapshot_time",
                        y="liquidity",
                        title="Liquidity History",
                    )
                    st.plotly_chart(fig_liquidity, use_container_width=True)
                else:
                    st.info("No liquidity history available.")

                st.download_button(
                    "Download Market CSV",
                    history.to_csv(index=False),
                    file_name=f"{selected_market_id}.csv",
                    mime="text/csv",
                )

    elif page == "API":
        st.subheader("Prediction Market Dataset API")
        st.caption("Developer access to cross-platform prediction market data.")

        if not current_email:
            st.warning("Please log in to view API Pro.")
            st.stop()

        if not is_api:
            st.warning("API access requires API Pro.")
            st.info(
                "Upgrade to API Pro from the sidebar to unlock API exports and API documentation."
            )
            st.stop()

        st.success("✅ API Pro Active")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Status", "Online")
        col2.metric("Version", "v1")
        col3.metric("Daily Limit", "5,000")
        col4.metric("Format", "JSON")

        st.divider()

        st.subheader("Your API Key")
        api_key = f"pmd_live_{current_email.replace('@', '_').replace('.', '_')[:16]}"

        st.text_input(
            "API Key",
            value=api_key,
            disabled=True,
        )
        st.caption("Use this key in the Authorization header.")

        st.code(
            """Authorization: Bearer YOUR_API_KEY
Accept: application/json""",
            language="text",
        )

        st.subheader("Available Endpoints")
        endpoints = pd.DataFrame(
            [
                ["GET /v1/latest", "Latest market snapshots"],
                ["GET /v1/platforms", "Platform statistics"],
                ["GET /v1/markets", "Search markets"],
                ["GET /v1/comparisons", "Cross-platform comparisons"],
                ["GET /v1/market/{market_id}", "Market history"],
                ["GET /v1/movers", "Biggest movers"],
                ["GET /v1/health", "Dataset health"],
            ],
            columns=["Endpoint", "Description"],
        )
        st.dataframe(endpoints, use_container_width=True, hide_index=True)

        st.subheader("Example Request")
        st.code(
            """curl -H "Authorization: Bearer YOUR_API_KEY" \\
https://api.predictionmarketdataset.com/v1/latest""",
            language="bash",
        )

        st.subheader("Example JSON Response")
        st.json(
            {
                "platform": "polymarket",
                "market_id": "0x123456...",
                "title": "Will Bitcoin exceed $150k in 2026?",
                "yes_price": 0.63,
                "no_price": 0.37,
                "volume": 154322.82,
                "liquidity": 428000.00,
                "snapshot_time": "2026-06-27T10:15:00Z",
            }
        )

        st.subheader("Python Example")
        st.code(
            """import requests

headers = {
    "Authorization": "Bearer YOUR_API_KEY"
}

response = requests.get(
    "https://api.predictionmarketdataset.com/v1/latest",
    headers=headers
)

print(response.json())
""",
            language="python",
        )

        st.subheader("JavaScript Example")
        st.code(
            """fetch(
    "https://api.predictionmarketdataset.com/v1/latest",
    {
        headers: {
            Authorization: "Bearer YOUR_API_KEY"
        }
    }
)
.then(r => r.json())
.then(console.log);
""",
            language="javascript",
        )

        st.subheader("Download Latest Dataset")
        export_df = sql_df(
            conn,
            """
            SELECT *
            FROM market_snapshots
            ORDER BY snapshot_time DESC
            LIMIT 10000
            """,
        )

        st.download_button(
            "Download Latest 10,000 Rows CSV",
            data=export_df.to_csv(index=False),
            file_name="prediction_market_api_export.csv",
            mime="text/csv",
        )

finally:
    conn.close()
