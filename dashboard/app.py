import duckdb
import pandas as pd
import streamlit as st
import plotly.express as px
from streamlit_autorefresh import st_autorefresh

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
    ["Dashboard", "Markets", "Platforms", "Movers", "Opportunities", "Market Detail"],
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

    st.subheader("Snapshot Growth")

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
            title="Rows by Platform",
        )
        st.plotly_chart(fig_rows, width="stretch")

        fig_unique = px.bar(
            platform_stats,
            x="platform",
            y="unique_markets",
            title="Unique Markets by Platform",
        )
        st.plotly_chart(fig_unique, width="stretch")

elif page == "Opportunities":
    st.subheader("Opportunity Scanner")

    opportunities = sql_df(f"""
        WITH latest AS (
            SELECT *
            FROM market_snapshots
            WHERE snapshot_time = (
                SELECT MAX(snapshot_time)
                FROM market_snapshots
            )
        ),
        matched AS (
            SELECT
                LOWER(title) AS normalized_title,
                title,
                platform,
                market_id,
                yes_price,
                no_price,
                volume,
                liquidity,
                raw_url
            FROM latest
            WHERE yes_price IS NOT NULL
        ),
        spreads AS (
            SELECT
                a.normalized_title,
                a.title AS title_a,
                b.title AS title_b,
                a.platform AS platform_a,
                b.platform AS platform_b,
                a.market_id AS market_id_a,
                b.market_id AS market_id_b,
                a.yes_price AS price_a,
                b.yes_price AS price_b,
                ABS(a.yes_price - b.yes_price) AS spread,
                a.volume AS volume_a,
                b.volume AS volume_b,
                a.liquidity AS liquidity_a,
                b.liquidity AS liquidity_b,
                a.raw_url AS url_a,
                b.raw_url AS url_b
            FROM matched a
            JOIN matched b
              ON a.normalized_title = b.normalized_title
             AND a.platform < b.platform
        )
        SELECT *
        FROM spreads
        WHERE spread > 0
        ORDER BY spread DESC
        LIMIT 100
    """)

    if opportunities.empty:
        st.info("No cross-platform opportunities found yet. This needs matching titles across platforms.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Opportunities", len(opportunities))
        c2.metric("Largest Spread", round(opportunities["spread"].max(), 4))
        c3.metric("Avg Spread", round(opportunities["spread"].mean(), 4))

        st.dataframe(opportunities, width="stretch")

        fig = px.bar(
            opportunities.head(25),
            x="spread",
            y="title_a",
            orientation="h",
            title="Top Cross-Platform Spreads",
        )
        st.plotly_chart(fig, width="stretch")

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
        LIMIT 5000
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