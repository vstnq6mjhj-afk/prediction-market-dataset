import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, Optional

import duckdb
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from dotenv import load_dotenv
from streamlit_autorefresh import st_autorefresh

# =========================
# Configuration
# =========================

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
st_autorefresh(interval=60 * 1000, limit=None, key="live_dashboard_refresh")

# =========================
# Helpers
# =========================


def get_query_param(name: str, default: Optional[str] = None) -> Optional[str]:
    value = st.query_params.get(name, default)
    if isinstance(value, list):
        return value[0] if value else default
    return value


def open_db() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB_PATH, read_only=True)


def sql_df(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    params: Optional[Iterable[Any]] = None,
) -> pd.DataFrame:
    if params is None:
        return conn.execute(query).df()
    return conn.execute(query, list(params)).df()


def show_df(df: pd.DataFrame) -> None:
    st.dataframe(df, use_container_width=True, hide_index=True)


def safe_sql_text(value: str) -> str:
    return str(value or "").replace("'", "''")


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


# =========================
# Account / access gate
# =========================

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
    st.write(
        "Use the main customer portal to choose a plan, then open the Dataset Explorer from your customer dashboard."
    )
    st.link_button("Go to Plans & Billing", f"{ACCOUNT_PORTAL_URL}/pricing")
    st.stop()

if page == "Market Matcher" and not is_professional:
    st.warning("Market Matcher is included in the Professional plan.")
    st.write("Developer subscribers still have access to the core dataset explorer, API docs, market search, movers, and market detail pages.")
    st.link_button("Upgrade to Professional", f"{ACCOUNT_PORTAL_URL}/pricing")
    st.stop()

# =========================
# Dataset filters
# =========================

conn = open_db()

try:
    platform_filter_sql = ""
    if page == "Market Detail":
        # Kalshi currently appears in the dataset but does not have useful Market Detail history.
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
    platforms = ["All"] + platforms_df["platform"].dropna().tolist()
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

# =========================
# Pages
# =========================

try:
    if page == "Dashboard":
        total_rows = conn.execute(f"SELECT COUNT(*) FROM market_snapshots {where_clause}").fetchone()[0]
        unique_markets = conn.execute(f"SELECT COUNT(DISTINCT market_id) FROM market_snapshots {where_clause}").fetchone()[0]
        snapshots = conn.execute(f"SELECT COUNT(DISTINCT snapshot_time) FROM market_snapshots {where_clause}").fetchone()[0]
        latest_snapshot = conn.execute(f"SELECT MAX(snapshot_time) FROM market_snapshots {where_clause}").fetchone()[0]

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
            fig_unique = px.bar(platform_stats, x="platform", y="unique_markets", title="Unique Markets By Platform")
            st.plotly_chart(fig_unique, use_container_width=True)

        st.subheader("Recent Snapshot Growth")
        growth = sql_df(
            conn,
            f"""
            SELECT snapshot_time, COUNT(*) AS rows
            FROM market_snapshots
            {where_clause}
            GROUP BY snapshot_time
            ORDER BY snapshot_time DESC
            LIMIT 300
            """,
        )
        if not growth.empty:
            growth["snapshot_time"] = pd.to_datetime(growth["snapshot_time"])
            growth = growth.sort_values("snapshot_time")
            fig = px.line(growth, x="snapshot_time", y="rows", markers=True, title="Rows Collected Over Recent Snapshots")
            st.plotly_chart(fig, use_container_width=True)

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
            LIMIT 300
            """,
        )
        show_df(top_volume)
        st.download_button(
            "Download latest markets CSV",
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
        show_df(platform_stats)
        if not platform_stats.empty:
            fig_rows = px.bar(platform_stats, x="platform", y="rows", title="Rows By Platform")
            st.plotly_chart(fig_rows, use_container_width=True)
            fig_unique = px.bar(platform_stats, x="platform", y="unique_markets", title="Unique Markets By Platform")
            st.plotly_chart(fig_unique, use_container_width=True)

    elif page == "Movers":
        st.subheader("Market Movers")
        st.caption("Largest moves from the most recent 2 days of snapshots.")
        movers = sql_df(
            conn,
            f"""
            WITH latest_time AS (
                SELECT MAX(snapshot_time) AS max_time FROM market_snapshots
            ),
            recent AS (
                SELECT *
                FROM market_snapshots
                WHERE snapshot_time >= (SELECT max_time FROM latest_time) - INTERVAL '2 days'
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
            LIMIT 100
            """,
        )
        display_cols = [
            "platform", "market_id", "title", "snapshots", "low_price", "high_price",
            "first_price", "last_price", "price_change", "volume_change", "liquidity_change",
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

    elif page == "Market Matcher":
        st.subheader("Market Matcher")
        st.caption(
            "Compare likely equivalent live prediction markets across all supported platform pairs."
        )

        st.info(
            "Use the platform-pair filter to compare Polymarket ↔ Kalshi, PredictIt ↔ Manifold, Kalshi ↔ Manifold, or any other available pair."
        )

        min_match_score = st.slider("Minimum match score", 0.10, 1.00, 0.30, 0.05)
        min_spread = st.slider("Minimum price difference", 0.00, 0.50, 0.00, 0.01)
        max_per_platform = st.slider("Markets per platform", 100, 800, 350, 50)
        keyword_focus = st.text_input("Optional keyword focus", placeholder="bitcoin, trump, fed, world cup...")

        matcher_filter = latest_filter
        if keyword_focus:
            safe_keyword = safe_sql_text(keyword_focus.lower())
            matcher_filter += f" AND LOWER(title) LIKE '%{safe_keyword}%'"

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

        def platform_badge(value: Any) -> str:
            key = safe_str(value).lower()
            badges = {
                "polymarket": "🟣 Polymarket",
                "predictit": "🔵 PredictIt",
                "kalshi": "🟠 Kalshi",
                "manifold": "🟢 Manifold",
            }
            return badges.get(key, safe_str(value).title())

        if latest.empty or latest["platform"].nunique() < 2:
            st.info("Not enough cross-platform latest data to run the matcher with the current filters.")
        else:
            platforms_loaded = sorted([str(p).lower() for p in latest["platform"].dropna().unique()])
            pair_options = ["All platform pairs"]
            for i, left in enumerate(platforms_loaded):
                for right in platforms_loaded[i + 1:]:
                    pair_options.append(f"{platform_badge(left)} ↔ {platform_badge(right)}")

            selected_pair = st.selectbox("Platform pair", pair_options)
            selected_pair_keys = None
            if selected_pair != "All platform pairs":
                left_label, right_label = [x.strip() for x in selected_pair.split("↔")]
                reverse_badges = {platform_badge(p): p for p in platforms_loaded}
                selected_pair_keys = {reverse_badges.get(left_label, left_label.lower()), reverse_badges.get(right_label, right_label.lower())}

            platform_counts = latest.groupby("platform").size().reset_index(name="Markets loaded")
            platform_counts["Platform"] = platform_counts["platform"].apply(platform_badge)
            platform_counts = platform_counts[["Platform", "Markets loaded"]]
            st.caption("Markets loaded into matcher")
            show_df(platform_counts)

            def token_set(title: Any) -> set:
                norm = normalize_title(title)
                return {w for w in norm.split() if len(w) >= 3}

            def jaccard(a: set, b: set) -> float:
                if not a or not b:
                    return 0.0
                return len(a & b) / len(a | b)

            rows = latest.to_dict("records")
            prepared = []
            for row in rows:
                prepared.append({**row, "tokens": token_set(row.get("title"))})

            matches = []
            for i, a in enumerate(prepared):
                for b in prepared[i + 1:]:
                    platform_a = str(a.get("platform")).lower()
                    platform_b = str(b.get("platform")).lower()

                    if platform_a == platform_b:
                        continue

                    if selected_pair_keys and {platform_a, platform_b} != selected_pair_keys:
                        continue

                    title_a = a.get("title")
                    title_b = b.get("title")
                    tokens_a = a.get("tokens", set())
                    tokens_b = b.get("tokens", set())

                    title_score = similarity(title_a, title_b)
                    token_score = jaccard(tokens_a, tokens_b)
                    overlap_terms = sorted(tokens_a & tokens_b)

                    # Weighted score. Token overlap matters more than raw full-title similarity
                    # because different platforms often phrase equivalent markets differently.
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
                        "platform_a": platform_a,
                        "title_a": title_a,
                        "price_a": price_a,
                        "platform_b": platform_b,
                        "title_b": title_b,
                        "price_b": price_b,
                        "price_difference": spread,
                        "match_score": match_score,
                        "title_similarity": title_score,
                        "token_overlap": token_score,
                        "shared_terms": ", ".join(overlap_terms[:12]),
                        "volume_a": a.get("volume"),
                        "volume_b": b.get("volume"),
                        "liquidity_a": a.get("liquidity"),
                        "liquidity_b": b.get("liquidity"),
                        "market_id_a": a.get("market_id"),
                        "market_id_b": b.get("market_id"),
                        "url_a": a.get("raw_url"),
                        "url_b": b.get("raw_url"),
                    })

            matches_df = pd.DataFrame(matches)
            if matches_df.empty:
                st.info(
                    "No matches found. Try lowering Minimum match score to 0.10, setting price difference to 0.00, selecting All platform pairs, or using a focused keyword."
                )
            else:
                matches_df = matches_df.sort_values(
                    ["match_score", "price_difference"], ascending=False
                ).head(250)

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Potential Matches", f"{len(matches_df):,}")
                c2.metric("Platform Pairs", f"{matches_df[['platform_a', 'platform_b']].drop_duplicates().shape[0]:,}")
                c3.metric("Largest Difference", f"{matches_df['price_difference'].max() * 100:.2f}%")
                c4.metric("Avg Match Score", f"{matches_df['match_score'].mean() * 100:.0f}%")

                pair_summary = matches_df.copy()
                pair_summary["Pair"] = pair_summary.apply(
                    lambda r: " ↔ ".join(sorted([platform_badge(r["platform_a"]), platform_badge(r["platform_b"])])),
                    axis=1,
                )
                pair_summary = (
                    pair_summary.groupby("Pair")
                    .agg(
                        Matches=("Pair", "count"),
                        Avg_Match=("match_score", "mean"),
                        Max_Difference=("price_difference", "max"),
                    )
                    .reset_index()
                    .sort_values("Matches", ascending=False)
                )
                pair_summary["Avg Match"] = pair_summary["Avg_Match"].map(lambda x: f"{x:.0%}")
                pair_summary["Max Difference"] = pair_summary["Max_Difference"].map(lambda x: f"{x:.2%}")
                st.subheader("Platform Pair Coverage")
                show_df(pair_summary[["Pair", "Matches", "Avg Match", "Max Difference"]])

                display = matches_df.copy()
                display["Platform A"] = display["platform_a"].apply(platform_badge)
                display["Platform B"] = display["platform_b"].apply(platform_badge)
                display["Market A"] = display["title_a"]
                display["Market B"] = display["title_b"]
                display["YES A"] = display["price_a"]
                display["YES B"] = display["price_b"]
                display["Difference"] = display["price_difference"]
                display["Match Score"] = display["match_score"]
                display["Title Similarity"] = display["title_similarity"]
                display["Token Overlap"] = display["token_overlap"]
                display["Shared Terms"] = display["shared_terms"]
                display["URL A"] = display["url_a"]
                display["URL B"] = display["url_b"]

                display_cols = [
                    "Platform A", "Market A", "YES A",
                    "Platform B", "Market B", "YES B",
                    "Difference", "Match Score", "Shared Terms",
                    "Title Similarity", "Token Overlap", "URL A", "URL B",
                ]
                styled_display = display[display_cols].style.format({
                    "YES A": "{:.2%}",
                    "YES B": "{:.2%}",
                    "Difference": "{:.2%}",
                    "Match Score": "{:.0%}",
                    "Title Similarity": "{:.0%}",
                    "Token Overlap": "{:.0%}",
                }).background_gradient(
                    subset=["Match Score"], cmap="RdYlGn", vmin=0, vmax=1
                ).background_gradient(
                    subset=["Difference"], cmap="Greens", vmin=0, vmax=max(float(display["Difference"].max()), 0.01)
                ).background_gradient(
                    subset=["Title Similarity", "Token Overlap"], cmap="RdYlGn", vmin=0, vmax=1
                )

                st.subheader("Matched Markets")
                st.dataframe(styled_display, use_container_width=True, hide_index=True)

                st.download_button(
                    "Download matcher results CSV",
                    matches_df.to_csv(index=False),
                    file_name="prediction_market_matcher_results.csv",
                    mime="text/csv",
                )

                chart_df = matches_df.head(30).copy()
                chart_df["pair"] = chart_df.apply(
                    lambda r: f"{platform_badge(r['platform_a'])} ↔ {platform_badge(r['platform_b'])}",
                    axis=1,
                )
                chart_df["price_difference_pct"] = chart_df["price_difference"] * 100
                fig = px.bar(
                    chart_df,
                    x="price_difference_pct",
                    y="pair",
                    orientation="h",
                    title="Largest Matched Market Price Differences",
                    color="match_score",
                    color_continuous_scale="RdYlGn",
                    labels={"price_difference_pct": "Price Difference (%)", "match_score": "Match Score"},
                )
                st.plotly_chart(fig, use_container_width=True)

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
            LIMIT 1000
            """,
        )

        if markets.empty:
            st.info("No markets found.")
        else:
            markets["label"] = (
                markets["platform"].fillna("").astype(str)
                + " | "
                + markets["title"].fillna("").astype(str).str[:120]
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
                SELECT *
                FROM (
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
                    LIMIT 1000
                )
                ORDER BY snapshot_time
                """,
                [selected_platform_detail, selected_market_id],
            )

            show_df(history.tail(25))

            if not history.empty:
                history["snapshot_time"] = pd.to_datetime(history["snapshot_time"])
                latest_row = history.sort_values("snapshot_time").iloc[-1]

                c1, c2, c3 = st.columns(3)
                c1.metric("Latest YES", latest_row.get("yes_price"))
                c2.metric("Latest Volume", latest_row.get("volume"))
                c3.metric("Observations Loaded", len(history))

                st.subheader("YES Price History")
                price_df = history.dropna(subset=["yes_price"])
                if not price_df.empty:
                    fig_price = px.line(price_df, x="snapshot_time", y="yes_price", markers=True, title="YES Price History")
                    st.plotly_chart(fig_price, use_container_width=True)

                st.subheader("Volume History")
                volume_df = history.dropna(subset=["volume"])
                if not volume_df.empty:
                    fig_volume = px.bar(volume_df, x="snapshot_time", y="volume", title="Volume History")
                    st.plotly_chart(fig_volume, use_container_width=True)

                st.subheader("Liquidity History")
                liquidity_df = history.dropna(subset=["liquidity"])
                if not liquidity_df.empty:
                    fig_liquidity = px.area(liquidity_df, x="snapshot_time", y="liquidity", title="Liquidity History")
                    st.plotly_chart(fig_liquidity, use_container_width=True)

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
            col1, col2 = st.columns(2)
            with col1:
                fig_rows = px.bar(health, x="platform", y="total_rows", title="Rows By Platform")
                st.plotly_chart(fig_rows, use_container_width=True)
            with col2:
                fig_markets = px.bar(health, x="platform", y="total_markets", title="Markets By Platform")
                st.plotly_chart(fig_markets, use_container_width=True)

    elif page == "API":
        st.subheader("Prediction Market Dataset API")
        st.caption("API access, billing, API key management, and usage are handled in the main customer portal.")

        st.link_button("Open Account Dashboard", f"{ACCOUNT_PORTAL_URL}/dashboard")
        st.link_button("Open API Docs", f"{ACCOUNT_PORTAL_URL}/docs")

        st.divider()
        st.subheader("Example Request")
        st.code(
            f'''curl -H "Authorization: Bearer YOUR_API_KEY" \\
  "{API_BASE_URL}/v1/search?q=bitcoin"''',
            language="bash",
        )

        st.subheader("Core Endpoints")
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
        show_df(endpoints)

except Exception as exc:
    st.error(f"Dashboard error: {exc}")

finally:
    try:
        conn.close()
    except Exception:
        pass
