
import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, Optional

import duckdb
import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

# ============================================================
# Prediction Market Dataset Explorer
# No-side-tabs tool-menu version
#
# Changes:
# - Removed sidebar tool navigation buttons.
# - Sidebar only shows account info + filters for the active tool.
# - Tool Menu is the main navigation.
# - Market Matcher has no "Run Market Matcher" button.
# - Market Matcher uses a capped, safer workload and renders a previous-style
#   matched market table with platform icons and match strength.
# ============================================================

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


# ============================================================
# Shared CSS
# ============================================================

st.markdown(
    """
    <style>
    .tool-card {
        border: 1px solid rgba(148, 163, 184, 0.35);
        border-radius: 18px;
        padding: 22px;
        min-height: 170px;
        background: rgba(15, 23, 42, 0.42);
        margin-bottom: 16px;
    }
    .tool-card h3 {
        margin-top: 0;
    }
    .matcher-row {
        border: 1px solid rgba(148, 163, 184, 0.28);
        border-radius: 16px;
        padding: 16px;
        margin-bottom: 12px;
        background: rgba(15, 23, 42, 0.46);
    }
    .platform-pill {
        display: inline-block;
        padding: 3px 9px;
        border-radius: 999px;
        background: rgba(56, 189, 248, 0.15);
        border: 1px solid rgba(56, 189, 248, 0.35);
        font-size: 0.82rem;
        margin-right: 8px;
    }
    .score-bar {
        width: 100%;
        height: 9px;
        border-radius: 999px;
        background: rgba(148, 163, 184, 0.22);
        overflow: hidden;
        margin-top: 8px;
    }
    .score-fill {
        height: 100%;
        border-radius: 999px;
        background: linear-gradient(90deg, #22c55e, #06b6d4);
    }
    .small-muted {
        color: rgba(226, 232, 240, 0.72);
        font-size: 0.88rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# Helpers
# ============================================================

def get_query_param(name: str, default: Optional[str] = None) -> Optional[str]:
    value = st.query_params.get(name, default)
    if isinstance(value, list):
        return value[0] if value else default
    return value


def set_tool(tool_name: str) -> None:
    st.query_params["tool"] = tool_name
    st.rerun()


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
    return " ".join(words[:14])


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


def platform_logo(value: Any) -> str:
    key = safe_str(value).lower()
    logos = {
        "polymarket": "🟣",
        "predictit": "🔵",
        "kalshi": "🟠",
        "manifold": "🟢",
    }
    return logos.get(key, "⚪")


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


def account_gate() -> tuple[Optional[Dict[str, Any]], str, bool, bool]:
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

    return account_status, current_plan, is_active_subscription, is_professional


def render_sidebar_base(account_status: Optional[Dict[str, Any]], current_plan: str) -> None:
    st.sidebar.subheader("Account")
    st.sidebar.link_button("Open Account Dashboard", f"{ACCOUNT_PORTAL_URL}/dashboard")
    st.sidebar.link_button("Plans & Billing", f"{ACCOUNT_PORTAL_URL}/pricing")
    st.sidebar.link_button("API Docs", f"{ACCOUNT_PORTAL_URL}/docs")

    st.sidebar.caption("Signup, login, billing, API keys, and usage are managed in the main customer portal.")

    if account_status:
        st.sidebar.success(account_status.get("email", "Account connected"))
        st.sidebar.write(f"Plan: **{current_plan.upper()}**")
        st.sidebar.write(f"Status: **{account_status.get('subscription_status', 'free')}**")
    else:
        st.sidebar.warning("No valid API key connected.")

    stored_api_key = st.session_state.get("pmd_api_key", "")
    entered_key = st.sidebar.text_input(
        "API key",
        value=stored_api_key,
        type="password",
        help="Open this from the customer dashboard or paste your API key here.",
    )
    if entered_key and entered_key != stored_api_key:
        st.session_state["pmd_api_key"] = entered_key.strip()
        st.rerun()

    if st.sidebar.button("Clear API key"):
        st.session_state.pop("pmd_api_key", None)
        st.rerun()

    st.sidebar.divider()


def build_filters(
    conn: duckdb.DuckDBPyConnection,
    exclude_kalshi: bool = False,
    show_platform_filter: bool = True,
    show_search_filter: bool = True,
) -> tuple[str, str, str, str]:
    platform_filter_sql = "AND LOWER(platform) <> 'kalshi'" if exclude_kalshi else ""

    selected_platform = "All"
    search = ""

    if show_platform_filter:
        try:
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
        selected_platform = st.sidebar.selectbox("Platform", platforms)

    if show_search_filter:
        search = st.sidebar.text_input("Search Markets")

    filters = []
    if selected_platform != "All":
        filters.append(f"platform = '{safe_sql_text(selected_platform)}'")
    if search:
        filters.append(f"LOWER(title) LIKE '%{safe_sql_text(search.lower())}%'")
    if exclude_kalshi:
        filters.append("LOWER(platform) <> 'kalshi'")

    where_clause = "WHERE " + " AND ".join(filters) if filters else ""
    latest_filter = "AND " + " AND ".join(filters) if filters else ""

    return selected_platform, search, where_clause, latest_filter


def back_to_menu() -> None:
    if st.button("← Back to Tool Menu"):
        set_tool("menu")


# ============================================================
# Tool Menu
# ============================================================

def render_tool_menu(is_professional: bool) -> None:
    st.title("Prediction Market Dataset Explorer")
    st.caption("Choose a dataset tool. Nothing heavy loads until you select a tool.")

    st.write(
        "This explorer is a gateway into the prediction market dataset. "
        "Select the workflow you want to use below."
    )

    tools = [
        ("Dataset Overview", "overview", "Live warehouse totals, latest snapshot, recent collection growth, and platform coverage."),
        ("Markets", "markets", "Browse the latest top-volume markets across supported platforms."),
        ("Platforms", "platforms", "Compare platform-level rows, unique markets, volume, liquidity, and freshness."),
        ("Movers", "movers", "Inspect markets with the largest recent YES-price, volume, and liquidity changes."),
        ("Market Matcher", "matcher", "Professional tool for comparing likely equivalent markets across platforms."),
        ("Market Detail", "market-detail", "Select a specific market and inspect historical observations and price history."),
        ("Dataset Health", "health", "Check coverage, first/latest snapshots, and platform-level data quality summary."),
        ("API Reference", "api", "View useful REST endpoints and links back to the main API docs and examples."),
    ]

    cols = st.columns(2)
    for i, (title, tool, body) in enumerate(tools):
        with cols[i % 2]:
            with st.container(border=True):
                st.subheader(title)
                st.write(body)
                if tool == "matcher" and not is_professional:
                    st.caption("Requires Professional plan.")
                    st.link_button("Upgrade to Professional", f"{ACCOUNT_PORTAL_URL}/pricing")
                else:
                    if st.button(f"Open {title}", key=f"open_{tool}", use_container_width=True):
                        set_tool(tool)


# ============================================================
# Pages
# ============================================================

def render_overview() -> None:
    st.title("Dataset Overview")
    st.caption("Lightweight live summary of the warehouse.")
    back_to_menu()

    conn = open_db()
    try:
        _, _, where_clause, _ = build_filters(conn)

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
            LIMIT 80
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
    finally:
        conn.close()


def render_markets() -> None:
    st.title("Markets")
    st.caption("Latest top-volume market rows.")
    back_to_menu()

    conn = open_db()
    try:
        _, _, _, latest_filter = build_filters(conn)

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
            LIMIT 150
            """,
        )
        show_df(top_volume)
        st.download_button(
            "Download current table CSV",
            top_volume.to_csv(index=False),
            file_name="prediction_market_latest_markets.csv",
            mime="text/csv",
        )
    finally:
        conn.close()


def render_platforms() -> None:
    st.title("Platforms")
    st.caption("Platform-level dataset coverage.")
    back_to_menu()

    conn = open_db()
    try:
        _, _, where_clause, _ = build_filters(conn, show_search_filter=False)

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
            st.subheader("Rows By Platform")
            st.bar_chart(platform_stats[["platform", "rows"]].set_index("platform"), use_container_width=True)
            st.subheader("Unique Markets By Platform")
            st.bar_chart(platform_stats[["platform", "unique_markets"]].set_index("platform"), use_container_width=True)
    finally:
        conn.close()


def render_movers() -> None:
    st.title("Movers")
    st.caption("Largest moves from recent snapshots.")
    back_to_menu()

    conn = open_db()
    try:
        _, _, _, latest_filter = build_filters(conn)

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
            LIMIT 60
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
    finally:
        conn.close()


def render_matcher() -> None:
    st.title("Market Matcher")
    st.caption("Compare likely equivalent live prediction markets across all supported platform pairs.")
    back_to_menu()

    conn = open_db()
    try:
        st.sidebar.subheader("Matcher Filters")
        min_match_score = st.sidebar.slider("Minimum match score", 0.10, 1.00, 0.35, 0.05)
        min_shared_terms = st.sidebar.slider("Minimum shared keywords", 1, 5, 2, 1)
        min_spread = st.sidebar.slider("Minimum price difference", 0.00, 0.50, 0.00, 0.01)
        max_per_platform = st.sidebar.slider("Markets per platform", 40, 250, 120, 20)
        keyword_focus = st.sidebar.text_input("Optional keyword focus", placeholder="bitcoin, trump, fed, world cup...")

        matcher_filter = ""
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

        if latest.empty or latest["platform"].nunique() < 2:
            st.info("Not enough cross-platform latest data to run the matcher with the current filters.")
            return

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
            selected_pair_keys = {
                reverse_badges.get(left_label, left_label.lower()),
                reverse_badges.get(right_label, right_label.lower()),
            }

        platform_counts = latest.groupby("platform").size().reset_index(name="Markets loaded")
        platform_counts["Platform"] = platform_counts["platform"].apply(platform_badge)
        platform_counts = platform_counts[["Platform", "Markets loaded"]]
        st.caption("Markets loaded into matcher")
        show_df(platform_counts, height=180)

        rows = latest.to_dict("records")
        prepared = [{**row, "tokens": token_set(row.get("title"))} for row in rows]

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

                if len(overlap_terms) < min_shared_terms:
                    continue

                # This is the previous matcher scoring style, but with a minimum shared-keyword
                # guard to remove the weakest false matches.
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
                "No matches found. Try lowering Minimum match score, reducing shared keywords, selecting All platform pairs, or using a focused keyword."
            )
            return

        matches_df = matches_df.sort_values(
            ["match_score", "price_difference"], ascending=False
        ).head(200)

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
        show_df(pair_summary[["Pair", "Matches", "Avg Match", "Max Difference"]], height=220)

        display = matches_df.copy()
        display["Platform A"] = display["platform_a"].apply(platform_badge)
        display["Platform B"] = display["platform_b"].apply(platform_badge)
        display["Market A"] = display["title_a"]
        display["Market B"] = display["title_b"]
        display["YES A"] = display["price_a"].map(lambda x: f"{x:.2%}")
        display["YES B"] = display["price_b"].map(lambda x: f"{x:.2%}")
        display["Difference"] = display["price_difference"].map(lambda x: f"{x:.2%}")
        display["Match Score"] = display["match_score"].map(lambda x: f"{x:.0%}")
        display["Title Similarity"] = display["title_similarity"].map(lambda x: f"{x:.0%}")
        display["Token Overlap"] = display["token_overlap"].map(lambda x: f"{x:.0%}")
        display["Shared Terms"] = display["shared_terms"]
        display["URL A"] = display["url_a"]
        display["URL B"] = display["url_b"]

        display_cols = [
            "Platform A", "Market A", "YES A",
            "Platform B", "Market B", "YES B",
            "Difference", "Match Score", "Shared Terms",
            "Title Similarity", "Token Overlap", "URL A", "URL B",
        ]

        st.subheader("Matched Markets")
        show_df(display[display_cols], height=520)

        st.subheader("Match Cards")
        for _, row in matches_df.head(20).iterrows():
            score_pct = int(round(float(row["match_score"]) * 100))
            difference_pct = f'{float(row["price_difference"]):.2%}'
            yes_a = f'{float(row["price_a"]):.2%}'
            yes_b = f'{float(row["price_b"]):.2%}'
            html = f"""
            <div class="matcher-row">
                <div>
                    <span class="platform-pill">{platform_logo(row["platform_a"])} {platform_badge(row["platform_a"])}</span>
                    <span class="platform-pill">{platform_logo(row["platform_b"])} {platform_badge(row["platform_b"])}</span>
                </div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:12px;">
                    <div>
                        <div class="small-muted">Market A · YES {yes_a}</div>
                        <strong>{row["title_a"]}</strong>
                    </div>
                    <div>
                        <div class="small-muted">Market B · YES {yes_b}</div>
                        <strong>{row["title_b"]}</strong>
                    </div>
                </div>
                <div style="margin-top:12px;">
                    <strong>Match Score: {score_pct}%</strong>
                    <span class="small-muted"> · Difference: {difference_pct} · Shared: {row["shared_terms"]}</span>
                    <div class="score-bar"><div class="score-fill" style="width:{score_pct}%"></div></div>
                </div>
            </div>
            """
            st.markdown(html, unsafe_allow_html=True)

        st.download_button(
            "Download matcher results CSV",
            matches_df.to_csv(index=False),
            file_name="prediction_market_matcher_results.csv",
            mime="text/csv",
        )

    finally:
        conn.close()


def render_market_detail() -> None:
    st.title("Market Detail")
    st.caption("Kalshi is excluded from this selector until useful historical detail data is available.")
    back_to_menu()

    conn = open_db()
    try:
        _, _, _, latest_filter = build_filters(conn, exclude_kalshi=True)

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
            {latest_filter}
            GROUP BY platform, market_id
            ORDER BY latest_snapshot DESC
            LIMIT 300
            """,
        )

        if markets.empty:
            st.info("No markets found.")
            return

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

        if not st.button("Load Market Detail", type="primary"):
            st.info("Select a market, then click Load Market Detail.")
            return

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
            LIMIT 250
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
    finally:
        conn.close()


def render_health() -> None:
    st.title("Dataset Health")
    back_to_menu()

    conn = open_db()
    try:
        _, _, where_clause, _ = build_filters(conn, show_search_filter=False)

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
    finally:
        conn.close()


def render_api_reference() -> None:
    st.title("API Reference")
    st.caption("API access, billing, API key management, and usage are handled in the main customer portal.")
    back_to_menu()

    st.link_button("Open Account Dashboard", f"{ACCOUNT_PORTAL_URL}/dashboard")
    st.link_button("Open API Docs", f"{ACCOUNT_PORTAL_URL}/docs")
    st.link_button("API Examples", f"{ACCOUNT_PORTAL_URL}/api-examples")

    st.divider()
    st.subheader("Example Request")
    st.code(
        'curl -H "Authorization: Bearer YOUR_API_KEY" '
        f'"{API_BASE_URL}/v1/search?q=bitcoin"',
        language="bash",
    )

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


# ============================================================
# Main
# ============================================================

account_status, current_plan, is_active_subscription, is_professional = account_gate()
render_sidebar_base(account_status, current_plan)

if not is_active_subscription:
    st.title("Prediction Market Dataset Explorer")
    st.warning("A paid subscription is required to access the Dataset Explorer.")
    st.write("Use the main customer portal to choose a plan, then open the Dataset Explorer from your customer dashboard.")
    st.link_button("Go to Plans & Billing", f"{ACCOUNT_PORTAL_URL}/pricing")
    st.stop()

tool = get_query_param("tool", "menu")

try:
    if tool in ("menu", "", None):
        render_tool_menu(is_professional)
    elif tool == "overview":
        render_overview()
    elif tool == "markets":
        render_markets()
    elif tool == "platforms":
        render_platforms()
    elif tool == "movers":
        render_movers()
    elif tool == "matcher":
        if not is_professional:
            st.warning("Market Matcher is included in the Professional plan.")
            st.link_button("Upgrade to Professional", f"{ACCOUNT_PORTAL_URL}/pricing")
        else:
            render_matcher()
    elif tool == "market-detail":
        render_market_detail()
    elif tool == "health":
        render_health()
    elif tool == "api":
        render_api_reference()
    else:
        st.error("Unknown dataset tool.")
        if st.button("Back to Tool Menu"):
            set_tool("menu")

except Exception as exc:
    st.error(f"Dataset Explorer error: {exc}")
