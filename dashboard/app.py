import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, Optional

import duckdb
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

# Ultra-stable Streamlit explorer for Render
# - No Plotly
# - No autorefresh
# - Small query limits
# - Heavy matcher only runs when clicked
# - Conservative DuckDB read settings

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "data/warehouse.duckdb")
ACCOUNT_PORTAL_URL = os.getenv(
    "ACCOUNT_PORTAL_URL",
    "https://prediction-market-dataset-api.onrender.com",
)
API_BASE_URL = os.getenv(
    "API_BASE_URL",
    "https://prediction-market-dataset-api.onrender.com",
)

st.set_page_config(page_title="Prediction Market Dataset Explorer", layout="wide")


def get_query_param(name: str, default: Optional[str] = None) -> Optional[str]:
    value = st.query_params.get(name, default)
    if isinstance(value, list):
        return value[0] if value else default
    return value


def open_db() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(DB_PATH, read_only=True)
    try:
        conn.execute("PRAGMA threads=1")
    except Exception:
        pass
    return conn


def sql_df(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    params: Optional[Iterable[Any]] = None,
) -> pd.DataFrame:
    if params is None:
        return conn.execute(query).df()
    return conn.execute(query, list(params)).df()


def show_df(df: pd.DataFrame, height: int = 420) -> None:
    st.dataframe(df, use_container_width=True, hide_index=True, height=height)


def safe_sql_text(value: Any) -> str:
    return str(value or "").replace("'", "''")


def safe_str(value: Any) -> str:
    return str(value or "").strip()


def normalize_title(title: Any) -> str:
    text = str(title or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    stopwords = {
        "will", "yes", "no", "the", "a", "an", "in", "on", "for", "of", "to",
        "with", "and", "or", "who", "what", "when", "win", "wins", "winner",
        "market", "markets", "prediction", "predict", "contract", "event", "by",
        "2024", "2025", "2026", "2027", "2028", "2029", "2030",
    }
    words = [w for w in text.split() if w not in stopwords and len(w) > 2]
    return " ".join(words[:12])


def similarity(a: Any, b: Any) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def token_set(title: Any) -> set:
    return {w for w in normalize_title(title).split() if len(w) >= 3}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def platform_badge(value: Any) -> str:
    key = safe_str(value).lower()
    badges = {
        "polymarket": "🟣 Polymarket",
        "predictit": "🔵 PredictIt",
        "kalshi": "🟠 Kalshi",
        "manifold": "🟢 Manifold",
    }
    return badges.get(key, safe_str(value).title())


def validate_api_key(api_key: str) -> Optional[Dict[str, Any]]:
    api_key = str(api_key or "").strip()
    if not api_key:
        return None
    try:
        response = requests.get(
            f"{API_BASE_URL}/v1/account",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if response.status_code != 200:
            return None
        return response.json()
    except Exception:
        return None


query_api_key = get_query_param("api_key")
if query_api_key:
    st.session_state["pmd_api_key"] = query_api_key

stored_api_key = st.session_state.get("pmd_api_key", "")
account_status = validate_api_key(stored_api_key) if stored_api_key else None

is_active_subscription = bool(
    account_status
    and str(account_status.get("subscription_status", "free")).lower() == "active"
)
current_plan = str(account_status.get("plan", "free")).lower() if account_status else "free"
is_professional = is_active_subscription and current_plan == "professional"

st.title("Prediction Market Dataset Explorer")
st.caption("Live cross-platform prediction market data warehouse")

with st.sidebar:
    st.subheader("Account")
    st.link_button("Open Account Dashboard", f"{ACCOUNT_PORTAL_URL}/dashboard")
    st.link_button("Plans & Billing", f"{ACCOUNT_PORTAL_URL}/pricing")
    st.link_button("API Docs", f"{ACCOUNT_PORTAL_URL}/docs")
    st.caption("Signup, login, subscriptions, billing, API keys, and usage are managed in the main customer portal.")

    if account_status:
        st.success(account_status.get("email", "Account connected"))
        st.write(f"Plan: **{current_plan.upper()}**")
        st.write(f"Status: **{account_status.get('subscription_status', 'free')}**")
    else:
        st.warning("No valid API key connected.")

    entered_key = st.text_input(
        "API key",
        value=stored_api_key,
        type="password",
        help="Open this from the customer dashboard or paste your API key here.",
    )
    if entered_key and entered_key != stored_api_key:
        st.session_state["pmd_api_key"] = entered_key.strip()
        st.rerun()

    if st.button("Clear API key"):
        st.session_state.pop("pmd_api_key", None)
        st.rerun()

    st.divider()
    page = st.radio(
        "Navigation",
        [
            "Dashboard",
            "Markets",
            "Platforms",
            "Movers",
            "Market Matcher",
            "Market Detail",
            "Health",
            "API",
        ],
    )

if not is_active_subscription:
    st.warning("A paid subscription is required to access the Dataset Explorer.")
    st.write("Use the main customer portal to choose a plan, then open the Dataset Explorer from your customer dashboard.")
    st.link_button("Go to Plans & Billing", f"{ACCOUNT_PORTAL_URL}/pricing")
    st.stop()

if page == "Market Matcher" and not is_professional:
    st.warning("Market Matcher is included in the Professional plan.")
    st.write("Developer subscribers still have access to the core dataset explorer, API docs, market search, movers, and market detail pages.")
    st.link_button("Upgrade to Professional", f"{ACCOUNT_PORTAL_URL}/pricing")
    st.stop()

conn = open_db()

try:
    platform_filter_sql = ""
    if page == "Market Detail":
        platform_filter_sql = "AND LOWER(platform) <> 'kalshi'"

    platforms_df = sql_df(
        conn,
        f"""
        SELECT DISTINCT platform
        FROM market_snapshots
        WHERE platform IS NOT NULL
        {platform_filter_sql}
        ORDER BY platform
        """,
    )
    platforms = ["All"] + platforms_df["platform"].dropna().astype(str).tolist()
except Exception:
    platforms = ["All"]

with st.sidebar:
    selected_platform = st.selectbox("Platform", platforms)
    search = st.text_input("Search Markets")

filters = []
if selected_platform != "All":
    filters.append(f"platform = '{safe_sql_text(selected_platform)}'")
if search:
    filters.append(f"LOWER(title) LIKE '%{safe_sql_text(search.lower())}%'")

where_clause = "WHERE " + " AND ".join(filters) if filters else ""
latest_filter = "AND " + " AND ".join(filters) if filters else ""

try:
    if page == "Dashboard":
        stats = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total_rows,
                COUNT(DISTINCT market_id) AS unique_markets,
                COUNT(DISTINCT snapshot_time) AS snapshots,
                MAX(snapshot_time) AS latest_snapshot
            FROM market_snapshots
            {where_clause}
            """
        ).fetchone()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Rows", f"{stats[0]:,}")
        c2.metric("Unique Markets", f"{stats[1]:,}")
        c3.metric("Snapshots", f"{stats[2]:,}")
        c4.metric("Latest Snapshot", str(stats[3])[:22])

        st.divider()
        st.subheader("Recent Snapshot Growth")
        growth = sql_df(
            conn,
            f"""
            SELECT snapshot_time, COUNT(*) AS rows
            FROM market_snapshots
            {where_clause}
            GROUP BY snapshot_time
            ORDER BY snapshot_time DESC
            LIMIT 120
            """,
        )
        if not growth.empty:
            growth["snapshot_time"] = pd.to_datetime(growth["snapshot_time"])
            growth = growth.sort_values("snapshot_time").set_index("snapshot_time")
            st.line_chart(growth["rows"], use_container_width=True)

        st.subheader("Platform Coverage")
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
        show_df(platform_stats, height=260)

        if not platform_stats.empty:
            st.subheader("Rows By Platform")
            st.bar_chart(platform_stats[["platform", "rows"]].set_index("platform"), use_container_width=True)

    elif page == "Markets":
        st.subheader("Latest Top Volume Markets")
        top_volume = sql_df(
            conn,
            f"""
            WITH latest AS (
                SELECT *
                FROM market_snapshots
                WHERE snapshot_time = (SELECT MAX(snapshot_time) FROM market_snapshots)
            )
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
            FROM latest
            WHERE 1=1
            {latest_filter}
            ORDER BY volume DESC NULLS LAST
            LIMIT 200
            """,
        )
        show_df(top_volume)
        st.download_button(
            "Download current table CSV",
            top_volume.to_csv(index=False),
            file_name="prediction_market_latest_markets.csv",
            mime="text/csv",
        )

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
        show_df(platform_stats, height=300)
        if not platform_stats.empty:
            st.bar_chart(platform_stats[["platform", "rows"]].set_index("platform"), use_container_width=True)
            st.bar_chart(platform_stats[["platform", "unique_markets"]].set_index("platform"), use_container_width=True)

    elif page == "Movers":
        st.subheader("Market Movers")
        st.caption("Largest moves from the most recent 12 hours of snapshots.")
        movers = sql_df(
            conn,
            f"""
            WITH latest_time AS (
                SELECT MAX(snapshot_time) AS max_time FROM market_snapshots
            ),
            recent AS (
                SELECT *
                FROM market_snapshots
                WHERE snapshot_time >= (SELECT max_time FROM latest_time) - INTERVAL '12 hours'
                  AND yes_price IS NOT NULL
                  {latest_filter}
            ),
            market_changes AS (
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
                FROM recent
                GROUP BY platform, market_id, title
                HAVING COUNT(*) >= 2
            )
            SELECT *
            FROM market_changes
            ORDER BY ABS(price_change) DESC NULLS LAST
            LIMIT 75
            """,
        )
        if movers.empty:
            st.info("No movers found yet. Let the scheduler collect more snapshots.")
        else:
            show_df(movers)
            st.download_button(
                "Download movers CSV",
                movers.to_csv(index=False),
                file_name="prediction_market_movers.csv",
                mime="text/csv",
            )

    elif page == "Market Matcher":
        st.subheader("Market Matcher")
        st.caption("Compare likely equivalent live prediction markets across supported platform pairs.")

        min_match_score = st.slider("Minimum match score", 0.10, 1.00, 0.30, 0.05)
        min_spread = st.slider("Minimum price difference", 0.00, 0.50, 0.00, 0.01)
        max_per_platform = st.slider("Markets per platform", 50, 250, 100, 25)
        keyword_focus = st.text_input("Optional keyword focus", placeholder="bitcoin, trump, fed, world cup...")

        if not st.button("Run Market Matcher", type="primary"):
            st.info("Set filters, then click Run Market Matcher. This keeps the explorer stable.")
        else:
            matcher_filter = latest_filter
            if keyword_focus:
                matcher_filter += f" AND LOWER(title) LIKE '%{safe_sql_text(keyword_focus.lower())}%'"

            latest = sql_df(
                conn,
                f"""
                WITH latest AS (
                    SELECT *
                    FROM market_snapshots
                    WHERE snapshot_time = (SELECT MAX(snapshot_time) FROM market_snapshots)
                ),
                ranked AS (
                    SELECT
                        platform,
                        market_id,
                        title,
                        yes_price,
                        no_price,
                        volume,
                        liquidity,
                        raw_url,
                        ROW_NUMBER() OVER (
                            PARTITION BY platform
                            ORDER BY COALESCE(volume, 0) DESC, COALESCE(liquidity, 0) DESC
                        ) AS rn
                    FROM latest
                    WHERE title IS NOT NULL
                      AND yes_price IS NOT NULL
                      {matcher_filter}
                )
                SELECT *
                FROM ranked
                WHERE rn <= ?
                """,
                [max_per_platform],
            )

            if latest.empty or latest["platform"].nunique() < 2:
                st.info("Not enough cross-platform latest data to run the matcher with the current filters.")
            else:
                rows = latest.to_dict("records")
                prepared = [{**row, "tokens": token_set(row.get("title"))} for row in rows]

                matches = []
                for i, a in enumerate(prepared):
                    for b in prepared[i + 1:]:
                        platform_a = str(a.get("platform")).lower()
                        platform_b = str(b.get("platform")).lower()
                        if platform_a == platform_b:
                            continue

                        title_score = similarity(a.get("title"), b.get("title"))
                        token_score = jaccard(a.get("tokens", set()), b.get("tokens", set()))
                        overlap_terms = sorted(a.get("tokens", set()) & b.get("tokens", set()))

                        match_score = (0.60 * token_score) + (0.40 * title_score)
                        if len(overlap_terms) >= 2:
                            match_score += 0.10
                        if len(overlap_terms) >= 3:
                            match_score += 0.10
                        match_score = min(match_score, 1.0)

                        if match_score < min_match_score:
                            continue

                        try:
                            price_a = float(a.get("yes_price"))
                            price_b = float(b.get("yes_price"))
                        except Exception:
                            continue

                        spread = abs(price_a - price_b)
                        if spread < min_spread:
                            continue

                        matches.append({
                            "Platform A": platform_badge(platform_a),
                            "Market A": a.get("title"),
                            "YES A": f"{price_a:.2%}",
                            "Platform B": platform_badge(platform_b),
                            "Market B": b.get("title"),
                            "YES B": f"{price_b:.2%}",
                            "Difference": f"{spread:.2%}",
                            "Match Score": f"{match_score:.0%}",
                            "Shared Terms": ", ".join(overlap_terms[:10]),
                            "URL A": a.get("raw_url"),
                            "URL B": b.get("raw_url"),
                        })

                matches_df = pd.DataFrame(matches)
                if matches_df.empty:
                    st.info("No matches found. Try lowering the match score, lowering price difference, or using a focused keyword.")
                else:
                    show_df(matches_df.head(150), height=600)
                    st.download_button(
                        "Download matcher results CSV",
                        matches_df.to_csv(index=False),
                        file_name="prediction_market_matcher_results.csv",
                        mime="text/csv",
                    )

    elif page == "Market Detail":
        st.subheader("Market Detail")
        st.caption("Kalshi is excluded from this selector until useful historical detail data is available.")

        market_detail_filter = latest_filter + " AND LOWER(platform) <> 'kalshi'"
        markets = sql_df(
            conn,
            f"""
            WITH latest AS (
                SELECT *
                FROM market_snapshots
                WHERE snapshot_time = (SELECT MAX(snapshot_time) FROM market_snapshots)
            )
            SELECT
                platform,
                market_id,
                MAX(title) AS title,
                MAX(snapshot_time) AS latest_snapshot
            FROM latest
            WHERE market_id IS NOT NULL
            {market_detail_filter}
            GROUP BY platform, market_id
            ORDER BY latest_snapshot DESC
            LIMIT 500
            """,
        )

        if markets.empty:
            st.info("No markets found.")
        else:
            markets["label"] = (
                markets["platform"].fillna("").astype(str)
                + " | "
                + markets["title"].fillna("").astype(str).str[:100]
                + " | "
                + markets["market_id"].fillna("").astype(str)
            )
            selected_label = st.selectbox("Select a market", markets["label"].tolist())
            selected_row = markets.loc[markets["label"] == selected_label].iloc[0]
            selected_platform_detail = selected_row["platform"]
            selected_market_id = selected_row["market_id"]

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
                WHERE platform = ?
                  AND market_id = ?
                ORDER BY snapshot_time DESC
                LIMIT 300
                """,
                [selected_platform_detail, selected_market_id],
            )
            history = history.sort_values("snapshot_time")
            show_df(history.tail(50))

            if not history.empty:
                history["snapshot_time"] = pd.to_datetime(history["snapshot_time"])
                latest_row = history.iloc[-1]

                c1, c2, c3 = st.columns(3)
                c1.metric("Latest YES", latest_row.get("yes_price"))
                c2.metric("Latest Volume", latest_row.get("volume"))
                c3.metric("Observations Loaded", len(history))

                price_df = history.dropna(subset=["yes_price"])[["snapshot_time", "yes_price"]].set_index("snapshot_time")
                if not price_df.empty:
                    st.subheader("YES Price History")
                    st.line_chart(price_df, use_container_width=True)

                volume_df = history.dropna(subset=["volume"])[["snapshot_time", "volume"]].set_index("snapshot_time")
                if not volume_df.empty:
                    st.subheader("Volume History")
                    st.bar_chart(volume_df, use_container_width=True)

                st.download_button(
                    "Download Market CSV",
                    history.to_csv(index=False),
                    file_name=f"{selected_platform_detail}_{selected_market_id}.csv",
                    mime="text/csv",
                )

    elif page == "Health":
        st.subheader("Dataset Health")
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
        show_df(health)
        if not health.empty:
            st.bar_chart(health[["platform", "total_rows"]].set_index("platform"), use_container_width=True)

    elif page == "API":
        st.subheader("Prediction Market Dataset API")
        st.caption("API access, billing, API key management, and usage are handled in the main customer portal.")

        st.link_button("Open Account Dashboard", f"{ACCOUNT_PORTAL_URL}/dashboard")
        st.link_button("Open API Docs", f"{ACCOUNT_PORTAL_URL}/docs")
        st.link_button("API Examples", f"{ACCOUNT_PORTAL_URL}/api-examples")

        st.divider()
        st.subheader("Example Request")
        example = 'curl -H "Authorization: Bearer YOUR_API_KEY" \\\n  "' + API_BASE_URL + '/v1/search?q=bitcoin"'
        st.code(example, language="bash")

        endpoints = pd.DataFrame([
            {"Endpoint": "/v1/health", "Description": "Dataset health and latest snapshot metadata"},
            {"Endpoint": "/v1/stats", "Description": "Snapshot, market, and platform counts"},
            {"Endpoint": "/v1/latest", "Description": "Latest market rows"},
            {"Endpoint": "/v1/search?q=bitcoin", "Description": "Search latest markets by keyword"},
            {"Endpoint": "/v1/markets", "Description": "Browse market records"},
            {"Endpoint": "/v1/market/{market_id}", "Description": "Historical observations for one market"},
            {"Endpoint": "/v1/platforms", "Description": "Platform-level dataset coverage"},
            {"Endpoint": "/v1/movers", "Description": "Largest recent market moves"},
            {"Endpoint": "/v1/categories", "Description": "Category-level market counts"},
            {"Endpoint": "/v1/account", "Description": "Authenticated account and usage status"},
        ])
        show_df(endpoints, height=360)

except Exception as exc:
    st.error(f"Dashboard error: {exc}")

finally:
    try:
        conn.close()
    except Exception:
        pass
