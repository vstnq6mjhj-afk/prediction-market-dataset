import re
import duckdb
import pandas as pd
import streamlit as st
import plotly.express as px
from difflib import SequenceMatcher
from streamlit_autorefresh import st_autorefresh


def similarity(a, b):
    return SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()


def normalize_title(title):
    if not title:
        return ""

    title = str(title).lower()
    title = re.sub(r"[^a-z0-9\s]", " ", title)

    stopwords = {
        "will", "yes", "the", "a", "an", "in", "on", "for", "of", "to",
        "with", "and", "or", "who", "what", "when", "win", "wins" "who",
        "will", "wins", "win", "winner", "market", "nomination", "presidential",
        "democratic", "democratic", "republican", "election", "2024", "2025", "2026",
        "2027", "2028",

    }

    words = []
    for w in title.split():
        if w not in stopwords and len(w) > 2:
            words.append(w)

    return " ".join(words[:7])


st_autorefresh(interval=60_000, key="dashboard_refresh")

DB_PATH = "data/warehouse.duckdb"

st.set_page_config(
    page_title="Prediction Market Dataset",
    layout="wide",
)

st.title("Prediction Market Dataset")
st.caption("Live cross-platform prediction market data warehouse")

conn = duckdb.connect(DB_PATH, read_only=True)

page = st.sidebar.radio(
    "Navigation",
    ["Dashboard", "Markets", "Platforms", "Movers", "Opportunities", "Health", "Market Detail"],
)

platforms_df = conn.execute("""
    SELECT DISTINCT platform
    FROM market_snapshots
    WHERE platform IS NOT NULL
    ORDER BY platform
""").df()

platforms = ["All"] + platforms_df["platform"].tolist()

selected_platform = st.sidebar.selectbox("Platform", platforms)
search = st.sidebar.text_input("Search Markets", key="sidebar_market_search")

filters = []

if selected_platform != "All":
    filters.append(f"platform = '{selected_platform}'")

if search:
    safe_search = search.lower().replace("'", "''")
    filters.append(f"LOWER(title) LIKE '%{safe_search}%'")

where_clause = ""
if filters:
    where_clause = "WHERE " + " AND ".join(filters)


def latest_extra_filter():
    if filters:
        return "AND " + " AND ".join(filters)
    return ""


def sql_df(query):
    return conn.execute(query).df()


def show_df(df):
    st.dataframe(df, width="stretch", hide_index=True)


if page == "Dashboard":
    total_rows = conn.execute(f"""
        SELECT COUNT(*)
        FROM market_snapshots
        {where_clause}
    """).fetchone()[0]

    unique_markets = conn.execute(f"""
        SELECT COUNT(DISTINCT market_id)
        FROM market_snapshots
        {where_clause}
    """).fetchone()[0]

    snapshots = conn.execute(f"""
        SELECT COUNT(DISTINCT snapshot_time)
        FROM market_snapshots
        {where_clause}
    """).fetchone()[0]

    latest_snapshot = conn.execute(f"""
        SELECT MAX(snapshot_time)
        FROM market_snapshots
        {where_clause}
    """).fetchone()[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Rows", f"{total_rows:,}")
    c2.metric("Unique Markets", f"{unique_markets:,}")
    c3.metric("Snapshots", f"{snapshots:,}")
    c4.metric("Latest Snapshot", str(latest_snapshot)[:22])

    st.divider()

    st.subheader("Platform Coverage")

    platform_stats = sql_df(f"""
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
    """)

    show_df(platform_stats)

    growth = sql_df(f"""
        SELECT
            snapshot_time,
            COUNT(*) AS rows
        FROM market_snapshots
        {where_clause}
        GROUP BY snapshot_time
        ORDER BY snapshot_time
    """)

    if not growth.empty:
        growth["snapshot_time"] = pd.to_datetime(growth["snapshot_time"])
        fig = px.line(
            growth,
            x="snapshot_time",
            y="rows",
            markers=True,
            title="Rows Collected Over Time",
        )
        st.plotly_chart(fig, width="stretch")

    if not platform_stats.empty:
        fig_rows = px.bar(
            platform_stats,
            x="platform",
            y="rows",
            title="Rows By Platform",
        )
        st.plotly_chart(fig_rows, width="stretch")

        fig_unique = px.bar(
            platform_stats,
            x="platform",
            y="unique_markets",
            title="Unique Markets By Platform",
        )
        st.plotly_chart(fig_unique, width="stretch")

    st.subheader("Snapshot Growth")


elif page == "Markets":
    st.subheader("Latest Top Volume Markets")

    top_volume = sql_df(f"""
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
    """)

    show_df(top_volume)

    st.subheader("Latest Snapshot Explorer")

    latest_snapshot = sql_df(f"""
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
    """)

    show_df(latest_snapshot)


elif page == "Platforms":
    st.subheader("Platform Comparison")

    platform_stats = sql_df(f"""
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
    """)

    show_df(platform_stats)

    if not platform_stats.empty:
        fig_rows = px.bar(
            platform_stats,
            x="platform",
            y="rows",
            title="Rows By Platform",
        )
        st.plotly_chart(fig_rows, width="stretch")

        fig_unique = px.bar(
            platform_stats,
            x="platform",
            y="unique_markets",
            title="Unique Markets By Platform",
        )
        st.plotly_chart(fig_unique, width="stretch")


elif page == "Opportunities":
    st.subheader("Opportunity Scanner")

    latest = sql_df("""
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
    """)

    if latest.empty:
        st.info(
            "No opportunity data found yet. Let the scheduler collect more snapshots."
        )

    else:

        rows = latest.to_dict("records")
        matches = []

        for i, a in enumerate(rows):
            for b in rows[i + 1:]:

                if a["platform"] == b["platform"]:
                    continue

                title_a = str(a.get("title") or "")
                title_b = str(b.get("title") or "")

                norm_a = normalize_title(title_a)
                norm_b = normalize_title(title_b)

                if not norm_a or not norm_b:
                    continue

                title_a_lower = title_a.lower()
                title_b_lower = title_b.lower()

                dem_a = "democratic" in title_a_lower
                dem_b = "democratic" in title_b_lower
                rep_a = "republican" in title_a_lower
                rep_b = "republican" in title_b_lower

                if dem_a != dem_b:
                    continue

                if rep_a != rep_b:
                    continue

                score = similarity(norm_a, norm_b)

                if score < 0.90:
                    continue

                price_a = a.get("yes_price")
                price_b = b.get("yes_price")

                if price_a is None or price_b is None:
                    continue

                spread = abs(float(price_a) - float(price_b))

                if spread < 0.01:
                    continue

                matches.append(
                    {
                        "title_a": title_a,
                        "title_b": title_b,
                        "platform_a": a.get("platform"),
                        "platform_b": b.get("platform"),
                        "market_id_a": a.get("market_id"),
                        "market_id_b": b.get("market_id"),
                        "price_a": round(float(price_a), 4),
                        "price_b": round(float(price_b), 4),
                        "spread": round(float(spread), 4),
                        "match_score": round(float(score), 3),
                        "volume_a": a.get("volume"),
                        "volume_b": b.get("volume"),
                        "liquidity_a": a.get("liquidity"),
                        "liquidity_b": b.get("liquidity"),
                        "url_a": a.get("raw_url"),
                        "url_b": b.get("raw_url"),
                    }
                )

        opportunities = pd.DataFrame(matches)

        st.sidebar.subheader("Opportunity Filters")

        min_spread = st.sidebar.slider(
            "Minimum Spread",
            0.00,
            0.20,
            0.01,
            0.005,
        )

        min_liquidity = st.sidebar.number_input(
            "Minimum Liquidity",
            value=0.0,
        )
        keyword_filter = st.sidebar.text_input(
            "Opportunity Keyword"
        )
        platform_pair = st.sidebar.selectbox(
            "Platform Pair",
            [
                "All",
                "polymarket ↔ predictit",
            ],
        )

        st.sidebar.caption(
            "Currently showing cross-platform matches from the latest snapshot. Additional platform pairs will appear automatically as matching markets are detected."
        )

        if keyword_filter and not opportunities.empty:
            keyword_filter = keyword_filter.lower()

            opportunities = opportunities[
                opportunities["title_a"].str.lower().str.contains(keyword_filter, na=False)
                | opportunities["title_b"].str.lower().str.contains(keyword_filter, na=False)
            ]
        if not opportunities.empty:

            opportunities = opportunities[
                opportunities["spread"] >= min_spread
            ]

            opportunities = opportunities[
                (
                    opportunities["liquidity_a"].fillna(0)
                    + opportunities["liquidity_b"].fillna(0)
                )
                >= min_liquidity
            ]
        if platform_pair != "All" and not opportunities.empty:
            left, right = [x.strip() for x in platform_pair.split("↔")]

            opportunities = opportunities[
                (
                    (opportunities["platform_a"] == left)
                    & (opportunities["platform_b"] == right)
                )
                | (
                    (opportunities["platform_a"] == right)
                    & (opportunities["platform_b"] == left)
                )
            ]
        if opportunities.empty:

            st.info(
                "No cross-platform opportunities found yet."
            )

            candidates = latest.sort_values(
                ["volume", "liquidity"],
                ascending=False,
                na_position="last",
            ).head(50)

            st.subheader("Best Single-Platform Candidates")

            show_df(
                candidates[
                    [
                        "platform",
                        "market_id",
                        "title",
                        "yes_price",
                        "volume",
                        "liquidity",
                        "raw_url",
                    ]
                ]
            )

        else:

            sort_method = st.selectbox(
                "Rank Opportunities By",
                [
                    "Spread",
                    "Liquidity",
                    "Match Score",
                ],
            )

            if sort_method == "Spread":

                opportunities = opportunities.sort_values(
                    "spread",
                    ascending=False,
                )

            elif sort_method == "Liquidity":

                opportunities["total_liquidity"] = (
                    opportunities["liquidity_a"].fillna(0)
                    + opportunities["liquidity_b"].fillna(0)
                )

                opportunities = opportunities.sort_values(
                    "total_liquidity",
                    ascending=False,
                )

            else:

                opportunities = opportunities.sort_values(
                    "match_score",
                    ascending=False,
                )

            opportunities = opportunities.head(100)

            c1, c2, c3 = st.columns(3)

            c1.metric(
                "Candidates",
                len(opportunities),
            )

            c2.metric(
                "Largest Spread",
                round(opportunities["spread"].max(), 4),
            )

            c3.metric(
                "Average Spread",
                round(opportunities["spread"].mean(), 4),
            )
            opportunities["market_a_link"] = opportunities["url_a"].apply(
                lambda x: x if str(x).startswith("http") else f"https://polymarket.com/market/{x}"
            )

            opportunities["market_b_link"] = opportunities["url_b"].apply(
                lambda x: x if str(x).startswith("http") else f"https://www.predictit.org/markets/detail/{x}"
            )
            display_df = opportunities[
            [
                "title_a",
                "title_b",
                "platform_a",
                "platform_b",
                "price_a",
                "price_b",
                "spread",
                "match_score",
                "volume_a",
                "volume_b",
                "liquidity_a",
                "liquidity_b",
                "market_a_link",
                "market_b_link",
            ]
        ]

            st.dataframe(
                display_df,
                use_container_width=True,
                column_config={
                    "market_a_link": st.column_config.LinkColumn("Market A"),
                    "market_b_link": st.column_config.LinkColumn("Market B"),
                },
            )
         

            st.subheader("Top Opportunity Candidates")

            chart_df = opportunities.head(25).copy()

            fig = px.bar(
                chart_df,
                x="spread",
                y="title_a",
                orientation="h",
                title="Top Cross-Platform Spreads",
            )

            st.plotly_chart(
                fig,
                width="stretch",
            )


elif page == "Movers":
    st.subheader("Market Movers")

    movers = sql_df(f"""
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
                LAST(yes_price ORDER BY snapshot_time)
                    - FIRST(yes_price ORDER BY snapshot_time) AS price_change,
                LAST(volume ORDER BY snapshot_time)
                    - FIRST(volume ORDER BY snapshot_time) AS volume_change,
                LAST(liquidity ORDER BY snapshot_time)
                    - FIRST(liquidity ORDER BY snapshot_time) AS liquidity_change
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
    """)

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
        gainers = movers.sort_values("price_change", ascending=False).head(25)
        show_df(gainers[display_cols])

        st.subheader("Top Losers")
        losers = movers.sort_values("price_change", ascending=True).head(25)
        show_df(losers[display_cols])

        st.subheader("Volume Movers")
        volume_movers = movers.sort_values("volume_change", ascending=False).head(25)
        show_df(volume_movers[display_cols])

        st.subheader("Liquidity Movers")
        liquidity_movers = movers.sort_values("liquidity_change", ascending=False).head(25)
        show_df(liquidity_movers[display_cols])


elif page == "Health":
    st.subheader("Market Health")

    health = sql_df(f"""
        SELECT
            platform,
            COUNT(*) AS total_rows,
            COUNT(DISTINCT market_id) AS total_markets,
            AVG(volume) AS avg_volume,
            AVG(liquidity) AS avg_liquidity,
            MIN(snapshot_time) AS first_snapshot,
            MAX(snapshot_time) AS latest_snapshot
        FROM market_snapshots
        GROUP BY platform
        ORDER BY total_rows DESC
    """)

    show_df(health)

    if not health.empty:
        fig_rows = px.bar(
            health,
            x="platform",
            y="total_rows",
            title="Rows By Platform",
        )
        st.plotly_chart(fig_rows, width="stretch")

        fig_markets = px.bar(
            health,
            x="platform",
            y="total_markets",
            title="Markets By Platform",
        )
        st.plotly_chart(fig_markets, width="stretch")


elif page == "Market Detail":
    st.subheader("Market Detail")

    market_where = where_clause if where_clause else "WHERE market_id IS NOT NULL"

    if where_clause:
        market_where += " AND market_id IS NOT NULL"

    markets = sql_df(f"""
        SELECT DISTINCT
            market_id,
            title,
            platform
        FROM market_snapshots
        {market_where}
        ORDER BY title
        LIMIT 3000
    """)

    if markets.empty:
        st.info("No markets found.")
    else:
        markets["label"] = (
            markets["title"].fillna("").str[:120]
            + " | "
            + markets["market_id"].fillna("")
        )

        selected_label = st.selectbox(
            "Select a market",
            markets["label"].tolist(),
            key="market_detail_selected_market",
        )

        selected_market_id = markets.loc[
            markets["label"] == selected_label,
            "market_id",
        ].iloc[0]

        history = conn.execute("""
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
        """, [selected_market_id]).df()

        show_df(history.tail(25))

        if not history.empty:
            history["snapshot_time"] = pd.to_datetime(history["snapshot_time"])
            latest = history.sort_values("snapshot_time").iloc[-1]

            c1, c2, c3 = st.columns(3)
            c1.metric("Latest YES", latest.get("yes_price"))
            c2.metric("Latest Volume", latest.get("volume"))
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
                st.plotly_chart(fig_price, width="stretch")
            else:
                st.info("No YES price history available.")

            st.subheader("Volume History")
            volume_df = history.dropna(subset=["volume"])

            if not volume_df.empty:
                fig_volume = px.bar(
                    volume_df,
                    x="snapshot_time",
                    y="volume",
                    title="Volume History",
                )
                st.plotly_chart(fig_volume, width="stretch")
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
                st.plotly_chart(fig_liquidity, width="stretch")
            else:
                st.info("No liquidity history available.")

            csv = history.to_csv(index=False)

            st.download_button(
                "Download Market CSV",
                csv,
                file_name=f"{selected_market_id}.csv",
                mime="text/csv",
            )

conn.close()