import duckdb
import pandas as pd
import streamlit as st
import plotly.express as px
import streamlit as st
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
    ["Dashboard", "Markets", "Platforms", "Movers", "Market Detail"],
)

platforms_df = conn.execute("""
SELECT DISTINCT platform
FROM market_snapshots
WHERE platform IS NOT NULL
ORDER BY platform
""").df()

platforms = ["All"] + platforms_df["platform"].tolist()

selected_platform = st.sidebar.selectbox("Platform", platforms)
search = st.sidebar.text_input("Search Markets")

filters = []

if selected_platform != "All":
    filters.append(f"platform = '{selected_platform}'")

if search:
    safe_search = search.lower().replace("'", "''")
    filters.append(f"LOWER(title) LIKE '%{safe_search}%'")

where_clause = ""
if filters:
    where_clause = "WHERE " + " AND ".join(filters)


def sql_df(query):
    return conn.execute(query).df()


def latest_extra_filter():
    if filters:
        return "AND " + " AND ".join(filters)
    return ""


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

    st.dataframe(platform_stats, use_container_width=True)

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
        st.plotly_chart(fig, use_container_width=True)


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

    st.dataframe(top_volume, use_container_width=True)

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

    st.dataframe(latest_snapshot, use_container_width=True)


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

    st.dataframe(platform_stats, use_container_width=True)

    if not platform_stats.empty:
        fig_rows = px.bar(
            platform_stats,
            x="platform",
            y="rows",
            title="Rows by Platform",
        )
        st.plotly_chart(fig_rows, use_container_width=True)

        fig_unique = px.bar(
            platform_stats,
            x="platform",
            y="unique_markets",
            title="Unique Markets by Platform",
        )
        st.plotly_chart(fig_unique, use_container_width=True)


elif page == "Movers":
    st.subheader("Biggest Price Movers")

    movers = sql_df(f"""
        SELECT
            platform,
            market_id,
            title,
            MIN(yes_price) AS low_price,
            MAX(yes_price) AS high_price,
            MAX(yes_price) - MIN(yes_price) AS price_move,
            COUNT(*) AS observations
        FROM market_snapshots
        {where_clause}
        GROUP BY platform, market_id, title
        HAVING COUNT(*) >= 3
        ORDER BY price_move DESC NULLS LAST
        LIMIT 100
    """)

    st.dataframe(movers, use_container_width=True)

    if not movers.empty:
        fig = px.bar(
            movers.head(25),
            x="price_move",
            y="title",
            orientation="h",
            title="Top Price Movers",
        )
        fig.update_layout(height=700)
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Most Active Markets")

    active = sql_df(f"""
        SELECT
            platform,
            market_id,
            title,
            COUNT(*) AS snapshots,
            MAX(volume) - MIN(volume) AS volume_change,
            MAX(yes_price) - MIN(yes_price) AS price_change
        FROM market_snapshots
        {where_clause}
        GROUP BY platform, market_id, title
        HAVING COUNT(*) >= 3
        ORDER BY snapshots DESC, price_change DESC NULLS LAST
        LIMIT 100
    """)

    st.dataframe(active, use_container_width=True)


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

        selected_label = st.selectbox("Select a market", markets["label"].tolist())

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

        st.dataframe(history, use_container_width=True)

        if not history.empty:
            history["snapshot_time"] = pd.to_datetime(history["snapshot_time"])

            latest_yes = history["yes_price"].dropna().iloc[-1] if not history["yes_price"].dropna().empty else "N/A"
            latest_volume = history["volume"].dropna().iloc[-1] if not history["volume"].dropna().empty else "N/A"

            c1, c2, c3 = st.columns(3)
            c1.metric("Latest YES", latest_yes)
            c2.metric("Latest Volume", latest_volume)
            c3.metric("Observations", len(history))

            st.subheader("YES Price History")
            fig_price = px.line(
                history,
                x="snapshot_time",
                y="yes_price",
                markers=True,
            )
            st.plotly_chart(fig_price, use_container_width=True)

            st.subheader("Volume History")
            fig_volume = px.bar(
                history,
                x="snapshot_time",
                y="volume",
            )
            st.plotly_chart(fig_volume, use_container_width=True)

            st.subheader("Liquidity History")
            fig_liquidity = px.area(
                history,
                x="snapshot_time",
                y="liquidity",
            )
            st.plotly_chart(fig_liquidity, use_container_width=True)

            csv = history.to_csv(index=False)

            st.download_button(
                "Download Market CSV",
                csv,
                file_name=f"{selected_market_id}.csv",
                mime="text/csv",
            )

conn.close()